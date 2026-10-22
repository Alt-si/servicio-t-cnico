"""Sistema de servicio técnico — versión web (Flask). Una cuenta (mail + clave) por comercio, una base por cuenta."""
import base64
import io
import os
import re
import secrets
import time
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for, Response)

import core
from core import (ESTADOS, ESTADOS_FINALES, E_NUEVO, E_PENDIENTE, E_LISTO, E_RETIRADO, E_SCRAP, E_COMPRADO,
                  fmt_fecha, fmt_monto, parse_monto, saldo_a_abonar, texto_equipo, telefono_whatsapp)

app = Flask(__name__)
core.DATA_DIR.mkdir(parents=True, exist_ok=True)
_clave_secreta = core.DATA_DIR / "secret.key"
if not _clave_secreta.exists():
    _clave_secreta.write_text(secrets.token_hex(32))
app.secret_key = os.environ.get("SECRET_KEY") or _clave_secreta.read_text().strip()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("INSECURE_COOKIES") != "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14, MAX_CONTENT_LENGTH=4 * 1024 * 1024)
core.crear_cuentas()

# --------------------------------------------------------------------------- seguridad
_intentos = {}          # (ip, mail) -> [instantes de intentos fallidos]


def demasiados_intentos(clave):
    ahora = time.time()
    lista = [t for t in _intentos.get(clave, []) if ahora - t < 600]
    _intentos[clave] = lista
    return len(lista) >= 8


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.filters["tojson_cliente"] = lambda c: {k: c[k] for k in c.keys()}


@app.before_request
def antes():
    if request.method == "POST":
        token = request.headers.get("X-CSRF") or request.form.get("csrf")
        if not token or not secrets.compare_digest(token, session.get("csrf", "")):
            if request.path == "/login" and not session.get("csrf"):
                pass
            else:
                abort(400, "Sesión vencida: actualizá la página.")
    g.cuenta = None
    if session.get("cuenta_id"):
        with core.conectar_cuentas() as con:
            g.cuenta = con.execute("SELECT * FROM cuentas WHERE id=?", (session["cuenta_id"],)).fetchone()
        if not g.cuenta or not g.cuenta["activa"]:
            session.clear()
            g.cuenta = None


@app.after_request
def cabeceras(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def login_requerido(f):
    @wraps(f)
    def interior(*a, **k):
        if not g.cuenta:
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="Sesión vencida"), 401
            return redirect(url_for("login", siguiente=request.path))
        return f(*a, **k)
    return interior


def admin_requerido(f):
    @wraps(f)
    @login_requerido
    def interior(*a, **k):
        if not g.cuenta["es_admin"]:
            abort(403)
        return f(*a, **k)
    return interior


def db():
    return core.conectar(g.cuenta["id"])


@app.context_processor
def contexto():
    if not g.get("cuenta"):
        return {}
    return {"marca": core.marca(g.cuenta["id"]), "cuenta": g.cuenta, "ESTADOS": ESTADOS, "COLORES": core.COLORES}


def error(msg, codigo=400, **extra):
    return jsonify(ok=False, error=msg, **extra), codigo


# --------------------------------------------------------------------------- acceso
@app.route("/login", methods=["GET", "POST"])
def login():
    if g.cuenta:
        return redirect(url_for("ordenes"))
    msg = ""
    if request.method == "POST":
        email = request.form.get("email", "")
        clave_ip = (request.remote_addr, email.strip().lower())
        if demasiados_intentos(clave_ip):
            msg = "Demasiados intentos. Esperá unos minutos."
        else:
            fila = core.verificar_login(email, request.form.get("clave", ""))
            if fila and fila["activa"]:
                session.clear()
                session["cuenta_id"] = fila["id"]
                session.permanent = True
                csrf_token()
                dest = request.args.get("siguiente", "")
                return redirect(dest if dest.startswith("/") and not dest.startswith("//") else url_for("ordenes"))
            _intentos.setdefault(clave_ip, []).append(time.time())
            msg = "Mail o clave incorrectos." if not fila else "La cuenta está desactivada. Escribinos."
    csrf_token()
    return render_template("login.html", msg=msg)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def inicio():
    return redirect(url_for("ordenes"))


@app.route("/salud")
def salud():
    return "ok"


# --------------------------------------------------------------------------- clientes
def validar_cliente(d, con, cliente_id=None):
    nombre, apellido = (d.get("nombre") or "").strip(), (d.get("apellido") or "").strip()
    telefono, cuit, iva = (d.get("telefono") or "").strip(), (d.get("cuit") or "").strip(), (d.get("condicion_iva") or "").strip()
    if not nombre:
        return "Ingresá el nombre."
    if not apellido:
        return "Ingresá el apellido."
    if not telefono:
        return "Ingresá el teléfono."
    if cuit:
        if not re.fullmatch(r"\d{11}", cuit):
            return "El CUIT debe tener exactamente 11 números, sin guiones."
        if iva not in core.IVA_OPCIONES:
            return "Si cargás el CUIT, tenés que elegir la condición de IVA."
        otro = con.execute("SELECT 1 FROM clientes WHERE cuit=? AND id<>? LIMIT 1", (cuit, cliente_id or 0)).fetchone()
        if otro:
            return "Este CUIT ya forma parte de la base de datos."
    if iva and iva not in core.IVA_OPCIONES:
        return "Elegí una condición de IVA válida."
    return None


@app.route("/clientes")
@login_requerido
def clientes():
    q = (request.args.get("q") or "").strip()
    with db() as con:
        sql = "SELECT * FROM clientes"
        params = []
        if q:
            like = f"%{core.sin_acentos(q)}%"
            campos = ("nombre", "apellido", "telefono", "dni", "ciudad", "domicilio", "razon_social", "condicion_iva", "cuit", "mail")
            sql += " WHERE " + " OR ".join(f"lower_u({c}) LIKE ?" for c in campos)
            sql += (" OR id IN (SELECT cliente_id FROM equipos WHERE lower_u(tipo||' '||marca||' '||modelo||' '||ifnull(serie,'')) LIKE ?)"
                    " OR id IN (SELECT cliente_id FROM ordenes WHERE CAST(id AS TEXT) LIKE ? OR lower_u(falla) LIKE ?)")
            params = [like] * len(campos) + [like, f"%{q.lstrip('#').lstrip('0') or q}%", like]
        filas = con.execute(sql + " ORDER BY apellido COLLATE NOCASE, nombre COLLATE NOCASE", params).fetchall()
    return render_template("clientes.html", filas=filas, q=q, IVA=core.IVA_OPCIONES)


@app.route("/api/cliente", methods=["POST"])
@login_requerido
def api_cliente_guardar():
    d = request.get_json(force=True, silent=True) or {}
    cid = d.get("id")
    with db() as con:
        msg = validar_cliente(d, con, cid)
        if msg:
            return error(msg)
        valores = ((d.get("nombre") or "").strip(), (d.get("apellido") or "").strip(), (d.get("telefono") or "").strip(),
                   (d.get("dni") or "").strip(), (d.get("ciudad") or "").strip(), (d.get("domicilio") or "").strip(),
                   (d.get("razon_social") or "").strip(), (d.get("condicion_iva") or "").strip() or "Consumidor Final",
                   (d.get("cuit") or "").strip(), (d.get("mail") or "").strip())
        if cid:
            con.execute("UPDATE clientes SET nombre=?, apellido=?, telefono=?, dni=?, ciudad=?, domicilio=?, razon_social=?, "
                        "condicion_iva=?, cuit=?, mail=? WHERE id=?", valores + (cid,))
        else:
            cid = con.execute("INSERT INTO clientes (nombre, apellido, telefono, dni, ciudad, domicilio, razon_social, "
                              "condicion_iva, cuit, mail) VALUES (?,?,?,?,?,?,?,?,?,?)", valores).lastrowid
    return jsonify(ok=True, id=cid)


@app.route("/api/cliente/<int:cid>/eliminar", methods=["POST"])
@login_requerido
def api_cliente_eliminar(cid):
    with db() as con:
        n = con.execute("SELECT COUNT(*) FROM ordenes WHERE cliente_id=?", (cid,)).fetchone()[0]
        if n:
            return error(f"Este cliente tiene {n} orden(es) de reparación en su historial, por eso no se puede eliminar.")
        con.execute("DELETE FROM equipos WHERE cliente_id=?", (cid,))
        con.execute("DELETE FROM clientes WHERE id=?", (cid,))
    return jsonify(ok=True)


@app.route("/api/cliente/<int:cid>/ordenes")
@login_requerido
def api_cliente_ordenes(cid):
    with db() as con:
        c = con.execute("SELECT nombre, apellido FROM clientes WHERE id=?", (cid,)).fetchone()
        if not c:
            return error("No existe el cliente", 404)
        filas = con.execute(
            "SELECT o.id, o.fecha_ingreso, o.estado, o.falla, o.presupuesto_respuesta, e.tipo, e.marca, e.modelo "
            "FROM ordenes o LEFT JOIN equipos e ON e.id=o.equipo_id WHERE o.cliente_id=? ORDER BY o.id DESC", (cid,)).fetchall()
    return jsonify(ok=True, cliente=f"{c['apellido']}, {c['nombre']}", ordenes=[
        dict(id=f["id"], fecha=fmt_fecha(f["fecha_ingreso"]), estado=f["estado"], falla=f["falla"] or "",
             equipo=texto_equipo(f["tipo"], f["marca"], f["modelo"]), rechazado=f["presupuesto_respuesta"] == "rechazado")
        for f in filas])


@app.route("/api/cliente/<int:cid>/contacto")
@login_requerido
def api_cliente_contacto(cid):
    with db() as con:
        c = con.execute("SELECT nombre, telefono FROM clientes WHERE id=?", (cid,)).fetchone()
    if not c:
        return error("No existe el cliente", 404)
    return jsonify(ok=True, nombre=(c["nombre"] or "").strip().title(), wa=telefono_whatsapp(c["telefono"]))


@app.route("/api/cliente/<int:cid>/equipos")
@login_requerido
def api_cliente_equipos(cid):
    with db() as con:
        filas = con.execute("SELECT * FROM equipos WHERE cliente_id=? ORDER BY id DESC", (cid,)).fetchall()
    return jsonify(ok=True, equipos=[dict(f) for f in filas])


# --------------------------------------------------------------------------- órdenes: listado
SQL_ORDEN = {"nuevas": "o.fecha_ingreso DESC, o.id DESC", "viejas": "o.fecha_ingreso ASC, o.id ASC",
             "cliente": "c.apellido COLLATE NOCASE, c.nombre COLLATE NOCASE, o.id DESC",
             "estado": "o.estado, o.fecha_ingreso DESC"}


@app.route("/ordenes")
@login_requerido
def ordenes():
    estado = request.args.get("estado", "")
    q = (request.args.get("q") or "").strip()
    orden = request.args.get("orden", "nuevas")
    where, params = [], []
    if estado in ESTADOS:
        where.append("o.estado=?")
        params.append(estado)
    if q:
        like = f"%{core.sin_acentos(q).lstrip('#')}%"
        where.append("(" + " OR ".join(f"lower_u({c}) LIKE ?" for c in (
            "c.nombre", "c.apellido", "c.telefono", "e.tipo", "e.marca", "e.modelo", "e.serie", "o.falla", "o.estado")) +
            " OR CAST(o.id AS TEXT) LIKE ?)")
        params += [like] * 9
        params[-1] = f"%{q.lstrip('#').lstrip('0') or q}%"
    sql = ("SELECT o.*, c.nombre, c.apellido, c.telefono, e.tipo, e.marca, e.modelo, e.en_garantia "
           "FROM ordenes o JOIN clientes c ON c.id=o.cliente_id LEFT JOIN equipos e ON e.id=o.equipo_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + SQL_ORDEN.get(orden, SQL_ORDEN["nuevas"])
    with db() as con:
        filas = con.execute(sql, params).fetchall()
    return render_template("ordenes.html", filas=filas, estado=estado, q=q, orden=orden,
                           fmt_fecha=fmt_fecha, texto_equipo=texto_equipo)


# --------------------------------------------------------------------------- órdenes: ficha
def cargar_orden(con, oid):
    o = con.execute("SELECT * FROM ordenes WHERE id=?", (oid,)).fetchone()
    if not o:
        abort(404)
    return o


@app.route("/orden/nueva")
@login_requerido
def orden_nueva():
    with db() as con:
        cliente = con.execute("SELECT * FROM clientes WHERE id=?", (request.args.get("cliente", type=int),)).fetchone()
    return render_template("orden.html", o=None, cliente=cliente, equipo=None, TIPOS=core.TIPOS_EQUIPO,
                           necesita_cliente=cliente is None)


@app.route("/orden/<int:oid>")
@login_requerido
def orden_ver(oid):
    with db() as con:
        o = cargar_orden(con, oid)
        cliente = con.execute("SELECT * FROM clientes WHERE id=?", (o["cliente_id"],)).fetchone()
        equipo = con.execute("SELECT * FROM equipos WHERE id=?", (o["equipo_id"],)).fetchone() if o["equipo_id"] else None
    equipo_txt = texto_equipo(equipo["tipo"], equipo["marca"], equipo["modelo"]) if equipo else "equipo"
    return render_template("orden.html", o=o, cliente=cliente, equipo=equipo, TIPOS=core.TIPOS_EQUIPO, necesita_cliente=False,
                           wa_numero=telefono_whatsapp(cliente["telefono"]), equipo_txt=equipo_txt,
                           nombre_pila=(cliente["nombre"] or "").strip().title())


@app.route("/api/clientes/buscar")
@login_requerido
def api_clientes_buscar():
    q = core.sin_acentos((request.args.get("q") or "").strip())
    with db() as con:
        filas = con.execute(
            "SELECT id, nombre, apellido, telefono FROM clientes WHERE lower_u(nombre||' '||apellido) LIKE ? OR lower_u(telefono) LIKE ? "
            "ORDER BY apellido COLLATE NOCASE LIMIT 15", (f"%{q}%", f"%{q}%")).fetchall()
    return jsonify(ok=True, clientes=[dict(f) for f in filas])


def importes(d):
    try:
        return parse_monto(d.get("monto")), parse_monto(d.get("sena")), None
    except ValueError:
        return None, None, "Revisá el presupuesto y la seña (solo números)."


def pide_codigo(d, motivo):
    """None si el código es correcto; si no, la respuesta de error que hace que el navegador lo pida."""
    if core.codigo_es_valido(g.cuenta["id"], d.get("codigo")):
        return None
    return error("Código de validación incorrecto." if d.get("codigo") else motivo, 403, necesita_codigo=True)


@app.route("/api/orden", methods=["POST"])
@login_requerido
def api_orden_crear():
    d = request.get_json(force=True, silent=True) or {}
    monto, sena, msg = importes(d)
    if msg:
        return error(msg)
    falla = (d.get("falla") or "").strip()
    tipo = (d.get("tipo") or "").strip()
    if not falla:
        return error("Describí la falla reportada por el cliente.")
    if not tipo:
        return error("Elegí el tipo de equipo.")
    with db() as con:
        cliente = con.execute("SELECT id FROM clientes WHERE id=?", (d.get("cliente_id"),)).fetchone()
        if not cliente:
            return error("Elegí el cliente.")
        ahora = core.ahora()
        equipo_id = d.get("equipo_id") or None
        campos = [(d.get(k) or "").strip() for k in ("tipo", "marca", "modelo", "serie", "descripcion", "clave_acceso")]
        if equipo_id:
            if not con.execute("SELECT 1 FROM equipos WHERE id=? AND cliente_id=?", (equipo_id, cliente["id"])).fetchone():
                return error("El equipo no es de ese cliente.")
            con.execute("UPDATE equipos SET tipo=?, marca=?, modelo=?, serie=?, descripcion=?, clave_acceso=?, en_garantia=? WHERE id=?",
                        campos + [int(bool(d.get("en_garantia"))), equipo_id])
        else:
            equipo_id = con.execute(
                "INSERT INTO equipos (cliente_id, tipo, marca, modelo, serie, descripcion, clave_acceso, en_garantia, creado) "
                "VALUES (?,?,?,?,?,?,?,?,?)", [cliente["id"]] + campos + [int(bool(d.get("en_garantia"))), ahora]).lastrowid
        detalle = (d.get("detalle") or "").strip()
        estado = E_PENDIENTE if monto and monto > 0 else E_NUEVO
        oid = con.execute(
            "INSERT INTO ordenes (cliente_id, equipo_id, fecha_ingreso, estado, falla, accesorios, presupuesto_monto, "
            "presupuesto_detalle, fecha_presupuesto, sena_monto, actualizado, espera_repuesto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cliente["id"], equipo_id, ahora, estado, falla, (d.get("accesorios") or "").strip(), monto, detalle,
             ahora if monto is not None else None, sena, ahora, int(bool(d.get("espera_repuesto"))))).lastrowid
        core.registrar_evento(con, oid, estado, "Ingreso del equipo al taller")
        if monto is not None or detalle:
            core.registrar_evento(con, oid, estado, core.texto_evento_presupuesto(monto, detalle, True))
        if sena:
            core.registrar_evento(con, oid, estado, f"Seña recibida: {fmt_monto(sena)}")
        if d.get("espera_repuesto"):
            core.registrar_evento(con, oid, estado, "Esperando repuesto")
    return jsonify(ok=True, id=oid, estado=estado, ofrecer_presupuesto=bool(monto and monto > 0))


@app.route("/api/orden/<int:oid>", methods=["POST"])
@login_requerido
def api_orden_guardar(oid):
    d = request.get_json(force=True, silent=True) or {}
    with db() as con:
        o = cargar_orden(con, oid)
        resp = o["presupuesto_respuesta"]
        monto, sena, msg = importes(d)
        if msg:
            return error(msg)
        detalle = (d.get("detalle") or "").strip()
        # Con el presupuesto respondido se bloquea el precio; con «no aceptado» también la seña.
        if resp:
            if (monto, detalle) != (o["presupuesto_monto"], (o["presupuesto_detalle"] or "")):
                return error("El presupuesto ya fue respondido: no se puede modificar el precio ni el detalle.")
        if resp == "rechazado" and sena != o["sena_monto"]:
            return error("Con el presupuesto no aceptado no se puede modificar la seña.")
        falla = (d.get("falla") or "").strip()
        if not falla or not (d.get("tipo") or "").strip():
            return error("Completá el tipo de equipo y la falla.")
        estado = d.get("estado") or o["estado"]
        if estado not in ESTADOS:
            return error("Estado inválido.")
        if estado == E_COMPRADO and estado != o["estado"]:
            return error("Para pasar a «Equipo comprado» usá el botón Comprar.")
        if core.requiere_codigo(o["estado"], estado):
            r = pide_codigo(d, f"Cambiar el estado de «{o['estado']}» a «{estado}» requiere el código de validación.")
            if r:
                return r
        if estado == E_NUEVO and monto and monto > 0 and o["estado"] == E_NUEVO:
            estado = E_PENDIENTE
        ahora = core.ahora()
        repuesto = int(bool(d.get("espera_repuesto")))
        if estado in (E_LISTO, E_RETIRADO):
            repuesto = 0
        dev = o["reembolso_monto"]
        a_abonar = saldo_a_abonar(monto, sena, dev, resp == "rechazado")
        f_fin = o["fecha_finalizado"] or (ahora if estado in (E_LISTO, E_RETIRADO) else None)
        f_ret = ahora if estado == E_RETIRADO and o["estado"] != E_RETIRADO else (None if estado != E_RETIRADO else o["fecha_retiro"])
        f_arch = o["fecha_archivada"] or (ahora if estado == E_SCRAP else None)
        f_pres = o["fecha_presupuesto"] or (ahora if monto is not None else None)
        if estado == E_LISTO:
            f_ret = None
        con.execute("UPDATE ordenes SET estado=?, falla=?, accesorios=?, presupuesto_monto=?, presupuesto_detalle=?, sena_monto=?, "
                    "espera_repuesto=?, fecha_presupuesto=?, fecha_finalizado=?, fecha_retiro=?, fecha_archivada=?, actualizado=? WHERE id=?",
                    (estado, falla, (d.get("accesorios") or "").strip(), monto, detalle, sena, repuesto, f_pres, f_fin, f_ret,
                     f_arch, ahora, oid))
        if o["equipo_id"]:
            con.execute("UPDATE equipos SET tipo=?, marca=?, modelo=?, serie=?, descripcion=?, clave_acceso=?, en_garantia=? WHERE id=?",
                        [(d.get(k) or "").strip() for k in ("tipo", "marca", "modelo", "serie", "descripcion", "clave_acceso")]
                        + [int(bool(d.get("en_garantia"))), o["equipo_id"]])
        if estado != o["estado"]:
            core.registrar_evento(con, oid, estado, core.texto_evento_estado(o["estado"], estado, a_abonar, bool(dev)))
        if (monto, detalle) != (o["presupuesto_monto"], (o["presupuesto_detalle"] or "")):
            core.registrar_evento(con, oid, estado, core.texto_evento_presupuesto(monto, detalle, False))
        if repuesto != o["espera_repuesto"]:
            core.registrar_evento(con, oid, estado, "Esperando repuesto" if repuesto else "Repuesto recibido")
        if sena != o["sena_monto"]:
            core.registrar_evento(con, oid, estado, f"Seña recibida: {fmt_monto(sena)}" if sena else "Seña quitada")
        if falla != (o["falla"] or "") or (d.get("accesorios") or "").strip() != (o["accesorios"] or ""):
            core.registrar_evento(con, oid, estado, "Datos de ingreso modificados")
    return jsonify(ok=True, id=oid, estado=estado)


@app.route("/api/orden/<int:oid>/accion", methods=["POST"])
@login_requerido
def api_orden_accion(oid):
    d = request.get_json(force=True, silent=True) or {}
    accion = d.get("accion")
    with db() as con:
        o = cargar_orden(con, oid)
        estado, resp = o["estado"], o["presupuesto_respuesta"]
        ahora = core.ahora()
        monto, sena, dev = o["presupuesto_monto"], o["sena_monto"], o["reembolso_monto"]

        def cambiar(nuevo, extra="", args=()):
            con.execute(f"UPDATE ordenes SET estado=?, actualizado=?{extra} WHERE id=?", (nuevo, ahora) + tuple(args) + (oid,))

        if accion in ("aceptar", "rechazar"):
            if resp:
                return error("El cliente ya respondió el presupuesto.")
            if estado in ESTADOS_FINALES or estado == E_RETIRADO:
                return error("La orden ya no admite esa acción.")
            if accion == "aceptar" and monto is None:
                return error("Cargá el presupuesto antes de marcar que el cliente lo acepta.")
            nueva = "aceptado" if accion == "aceptar" else "rechazado"
            nuevo_estado = E_PENDIENTE if estado == E_NUEVO else estado
            cambiar(nuevo_estado, ", presupuesto_respuesta=?, fecha_aceptacion=COALESCE(fecha_aceptacion, ?)" if nueva == "aceptado"
                    else ", presupuesto_respuesta=?", (nueva, ahora) if nueva == "aceptado" else (nueva,))
            core.registrar_evento(con, oid, nuevo_estado, core.TEXTO_RESPUESTA[nueva])
            if nuevo_estado != estado:
                core.registrar_evento(con, oid, nuevo_estado, core.texto_evento_estado(estado, nuevo_estado))
        elif accion == "deshacer":
            if not resp:
                return error("No hay respuesta para deshacer.")
            r = pide_codigo(d, "Deshacer la respuesta del cliente requiere el código de validación.")
            if r:
                return r
            cambiar(E_PENDIENTE, ", presupuesto_respuesta=NULL, fecha_aceptacion=NULL")
            core.registrar_evento(con, oid, E_PENDIENTE, "Respuesta del cliente al presupuesto deshecha")
        elif accion == "listo":
            if estado in ESTADOS_FINALES or estado == E_RETIRADO:
                return error("La orden ya no está en curso.")
            cambiar(E_LISTO, ", espera_repuesto=0, fecha_finalizado=COALESCE(fecha_finalizado, ?), fecha_retiro=NULL", (ahora,))
            core.registrar_evento(con, oid, E_LISTO, core.texto_evento_estado(estado, E_LISTO, saldo_a_abonar(monto, sena, dev, resp == "rechazado"), bool(dev)))
        elif accion == "retirar":
            if estado != E_LISTO:
                return error("Solo se puede retirar una orden que está lista para retirar.")
            cambiar(E_RETIRADO, ", espera_repuesto=0, fecha_retiro=?", (ahora,))
            core.registrar_evento(con, oid, E_RETIRADO, core.texto_evento_estado(estado, E_RETIRADO, saldo_a_abonar(monto, sena, dev, resp == "rechazado"), bool(dev)))
        elif accion == "scrap":
            if core.requiere_codigo(estado, E_SCRAP):
                r = pide_codigo(d, "Pasar la orden a Scrap desde ese estado requiere el código de validación.")
                if r:
                    return r
            cambiar(E_SCRAP, ", fecha_archivada=COALESCE(fecha_archivada, ?)", (ahora,))
            core.registrar_evento(con, oid, E_SCRAP, core.texto_evento_estado(estado, E_SCRAP))
        elif accion == "comprar":
            try:
                cmonto = parse_monto(d.get("compra_monto"))
            except ValueError:
                return error("El monto de la compra no es válido.")
            cond = (d.get("compra_condiciones") or "").strip()
            if cmonto is None or not cond:
                return error("Completá el monto y las condiciones de la compra.")
            if core.requiere_codigo(estado, E_COMPRADO):
                r = pide_codigo(d, "Este cambio de estado requiere el código de validación.")
                if r:
                    return r
            cambiar(E_COMPRADO, ", compra_monto=?, compra_condiciones=?, fecha_compra=?", (cmonto, cond, ahora))
            core.registrar_evento(con, oid, E_COMPRADO, core.texto_evento_compra(cmonto, cond))
        elif accion == "devolver":
            if resp == "rechazado" and False:
                pass
            try:
                dmonto = parse_monto(d.get("dev_monto"))
            except ValueError:
                return error("El monto a devolver no es válido.")
            motivo = (d.get("dev_motivo") or "").strip()
            if dev and not core.codigo_es_valido(g.cuenta["id"], d.get("codigo")):
                return pide_codigo(d, "Modificar una devolución ya registrada requiere el código de validación.")
            if dmonto is None or dmonto <= 0 or not motivo:
                return error("Completá el monto a devolver y el motivo.")
            con.execute("UPDATE ordenes SET reembolso_monto=?, reembolso_motivo=?, fecha_reembolso=?, actualizado=? WHERE id=?",
                        (dmonto, motivo, ahora, ahora, oid))
            core.registrar_evento(con, oid, estado, core.texto_evento_devolucion(dmonto, motivo))
        else:
            return error("Acción desconocida.")
    return jsonify(ok=True)


@app.route("/api/orden/<int:oid>/historial")
@login_requerido
def api_orden_historial(oid):
    with db() as con:
        cargar_orden(con, oid)
        filas = con.execute("SELECT id, fecha, estado, nota FROM historial_ordenes WHERE orden_id=? ORDER BY fecha DESC, id DESC", (oid,)).fetchall()
    return jsonify(ok=True, items=[dict(id=f["id"], fecha=fmt_fecha(f["fecha"], True), estado=f["estado"] or "", nota=f["nota"] or "") for f in filas])


@app.route("/api/historial/<int:hid>/borrar", methods=["POST"])
@login_requerido
def api_historial_borrar(hid):
    d = request.get_json(force=True, silent=True) or {}
    r = pide_codigo(d, "Borrar una línea del historial requiere el código de validación.")
    if r:
        return r
    with db() as con:
        con.execute("DELETE FROM historial_ordenes WHERE id=?", (hid,))
    return jsonify(ok=True)


# --------------------------------------------------------------------------- configuración
def _logo_png(datos):
    """Normaliza el logo subido a PNG (máx. 600 px). Requiere Pillow."""
    from PIL import Image
    im = Image.open(io.BytesIO(datos)).convert("RGBA")
    im.thumbnail((600, 600))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@app.route("/configuracion", methods=["GET", "POST"])
@login_requerido
def configuracion():
    cid = g.cuenta["id"]
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        color = request.form.get("color", "").strip()
        if not nombre:
            flash("Escribí el nombre de la empresa.", "error")
            return redirect(url_for("configuracion"))
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = core.COLOR_POR_DEFECTO
        core.guardar_config(cid, "marca_nombre", nombre)
        core.guardar_config(cid, "marca_subtitulo", request.form.get("subtitulo", "").strip())
        core.guardar_config(cid, "marca_color", color)
        for clave, minimo, maximo in (("logo_ancho", 24, 300), ("logo_alto", 24, 200)):
            try:
                v = min(max(int(request.form.get(clave, "")), minimo), maximo)
            except ValueError:
                v = 84
            core.guardar_config(cid, f"marca_{clave}", v)
        if request.form.get("quitar_logo"):
            core.guardar_config(cid, "marca_logo", None)
        archivo = request.files.get("logo")
        if archivo and archivo.filename:
            try:
                core.guardar_config(cid, "marca_logo", _logo_png(archivo.read()))
            except Exception:
                flash("No se pudo usar esa imagen. Probá con un PNG o JPG.", "error")
                return redirect(url_for("configuracion"))
        flash("Configuración guardada.", "ok")
        return redirect(url_for("configuracion"))
    return render_template("configuracion.html", codigo_defecto=core.codigo_por_defecto(cid),
                           rec_email=core.leer_config(cid, "rec_email", ""))


@app.route("/logo")
@login_requerido
def logo():
    datos = core.leer_config(g.cuenta["id"], "marca_logo")
    if not datos:
        abort(404)
    return Response(base64.b64decode(datos), mimetype="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.route("/api/codigo/cambiar", methods=["POST"])
@login_requerido
def api_codigo_cambiar():
    d = request.get_json(force=True, silent=True) or {}
    cid = g.cuenta["id"]
    if not core.codigo_es_valido(cid, d.get("actual")):
        return error("El código actual no es correcto.")
    nuevo = (d.get("nuevo") or "").strip()
    if len(nuevo) < 4:
        return error("El código nuevo debe tener al menos 4 caracteres.")
    if nuevo != (d.get("repetir") or "").strip():
        return error("Los dos códigos nuevos no coinciden.")
    if not core.leer_config(cid, "rec_email"):
        mail = (d.get("mail") or "").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail):
            return error("Para cambiar el código tenés que registrar un mail de recuperación válido.")
        core.guardar_config(cid, "rec_email", mail)
    core.guardar_codigo(cid, nuevo)
    return jsonify(ok=True)


@app.route("/api/cuenta/clave", methods=["POST"])
@login_requerido
def api_cuenta_clave():
    from werkzeug.security import check_password_hash
    d = request.get_json(force=True, silent=True) or {}
    if not check_password_hash(g.cuenta["clave_hash"], d.get("actual") or ""):
        return error("La clave actual no es correcta.")
    nueva = d.get("nueva") or ""
    if len(nueva) < 8:
        return error("La clave nueva debe tener al menos 8 caracteres.")
    if nueva != (d.get("repetir") or ""):
        return error("Las dos claves nuevas no coinciden.")
    core.cambiar_clave(g.cuenta["id"], nueva)
    return jsonify(ok=True)


# --------------------------------------------------------------------------- administración (solo dueño del sistema)
@app.route("/admin")
@admin_requerido
def admin():
    with core.conectar_cuentas() as con:
        filas = con.execute("SELECT * FROM cuentas ORDER BY id").fetchall()
    stats = {}
    for f in filas:
        try:
            with core.conectar(f["id"]) as c:
                stats[f["id"]] = (c.execute("SELECT COUNT(*) FROM clientes").fetchone()[0],
                                  c.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0])
        except Exception:
            stats[f["id"]] = (0, 0)
    nueva = session.pop("cuenta_nueva", None)
    return render_template("admin.html", filas=filas, stats=stats, nueva=nueva, fmt_fecha=fmt_fecha)


@app.route("/admin/crear", methods=["POST"])
@admin_requerido
def admin_crear():
    try:
        cid, clave = core.crear_cuenta(request.form.get("email", ""), request.form.get("comercio", ""))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin"))
    session["cuenta_nueva"] = {"email": request.form.get("email", "").strip().lower(), "clave": clave}
    return redirect(url_for("admin"))


@app.route("/admin/<int:cid>/<accion>", methods=["POST"])
@admin_requerido
def admin_accion(cid, accion):
    if accion == "clave":
        email = None
        with core.conectar_cuentas() as con:
            f = con.execute("SELECT email FROM cuentas WHERE id=?", (cid,)).fetchone()
        if not f:
            abort(404)
        clave = core.generar_clave()
        core.cambiar_clave(cid, clave)
        session["cuenta_nueva"] = {"email": f["email"], "clave": clave}
    elif accion in ("activar", "desactivar"):
        if cid == g.cuenta["id"]:
            flash("No podés desactivar tu propia cuenta.", "error")
        else:
            with core.conectar_cuentas() as con:
                con.execute("UPDATE cuentas SET activa=? WHERE id=?", (1 if accion == "activar" else 0, cid))
    elif accion == "codigo":
        core.guardar_config(cid, "codigo_hash", None)
        flash("El código de validación volvió a 0000.", "ok")
    else:
        abort(404)
    return redirect(url_for("admin"))


@app.errorhandler(400)
def e400(e):
    if request.path.startswith("/api/"):
        return error(getattr(e, "description", "Pedido inválido"))
    return render_template("error.html", titulo="Pedido inválido", texto=getattr(e, "description", "")), 400


@app.errorhandler(403)
def e403(e):
    return render_template("error.html", titulo="Sin permiso", texto="No tenés acceso a esta página."), 403


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", titulo="No encontrado", texto="La página no existe."), 404


@app.errorhandler(500)
def e500(e):
    if request.path.startswith("/api/"):
        return error("Ocurrió un error en el servidor.", 500)
    return render_template("error.html", titulo="Error", texto="Ocurrió un error. Probá de nuevo."), 500


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", 5000)))
