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
import stock
import ventas
import facturacion
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
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30, MAX_CONTENT_LENGTH=64 * 1024 * 1024)
core.crear_cuentas()

_bases_listas = set()


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
app.jinja_env.filters["nombre_cliente"] = core.nombre_cliente
app.jinja_env.filters["wa_numero"] = core.telefono_whatsapp
app.jinja_env.filters["monto_input"] = core.num_ar
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
        ahora = time.time()
        inact = session.get("inact", 60)                  # minutos sin usar el sistema antes de pedir la clave (0 = nunca)
        if session.get("v") != 2 or (inact and ahora - session.get("ultimo", ahora) > inact * 60):
            era_v2 = session.get("v") == 2
            session.clear()
            if era_v2:
                session["expiro"] = 1
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="Sesión vencida"), 401
        elif ahora - session.get("ultimo", 0) > 30 and request.endpoint != "static":
            session["ultimo"] = ahora
    if session.get("cuenta_id"):
        with core.conectar_cuentas() as con:
            g.cuenta = con.execute("SELECT * FROM cuentas WHERE id=?", (session["cuenta_id"],)).fetchone()
        if not g.cuenta or not g.cuenta["activa"]:
            session.clear()
            g.cuenta = None
        elif g.cuenta["id"] not in _bases_listas:
            core.crear_base(g.cuenta["id"])      # agrega tablas nuevas a comercios existentes
            with core.conectar(g.cuenta["id"]) as _c:
                stock.crear_tablas(_c)
                ventas.crear_tablas(_c)
                facturacion.crear_tablas(_c)
            if core.ajustes(g.cuenta["id"])["modo_texto"] == "mayusculas":      # tipos y marcas ya cargados, en mayúsculas
                with core.conectar(g.cuenta["id"]) as _c:
                    _c.execute("UPDATE equipos SET tipo=UPPER(tipo), marca=UPPER(marca) WHERE tipo<>UPPER(tipo) OR marca<>UPPER(marca)")
            _bases_listas.add(g.cuenta["id"])
    g.modulos = core.modulos_de(g.cuenta) if g.cuenta else set()


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


def modulo_requerido(clave):
    def deco(f):
        @wraps(f)
        def interior(*a, **k):
            if clave not in g.get("modulos", set()):
                if request.path.startswith("/api/"):
                    return jsonify(ok=False, error="Esta sección no está habilitada en tu cuenta."), 403
                abort(403)
            return f(*a, **k)
        return interior
    return deco


def admin_requerido(f):
    @wraps(f)
    @login_requerido
    def interior(*a, **k):
        if not g.cuenta["es_admin"]:
            abort(403)
        return f(*a, **k)
    return interior


def inicio_url():
    """Primera pantalla disponible según las secciones habilitadas."""
    for modulo, ruta in (("ordenes", "ordenes"), ("ventas", "ventas"), ("clientes", "clientes"), ("stock", "stock"), ("recordatorios", "recordatorios")):
        if modulo in g.get("modulos", set()):
            return url_for(ruta)
    return url_for("configuracion")


def db():
    return core.conectar(g.cuenta["id"])


@app.context_processor
def contexto():
    if not g.get("cuenta"):
        return {}
    cuentas = {}
    if "ordenes" in g.modulos:
        with db() as con:
            cuentas = dict(con.execute("SELECT estado, COUNT(*) FROM ordenes GROUP BY estado").fetchall())
    vistas = [("Todas las órdenes", "", sum(cuentas.values())), ("Ingresos nuevos", "Nuevo ingreso", cuentas.get("Nuevo ingreso", 0)),
              ("Pendientes", "Pendiente", cuentas.get("Pendiente", 0)), ("Listos para retirar", "Listo para retirar", cuentas.get("Listo para retirar", 0)),
              ("Equipos retirados", "Equipo retirado", cuentas.get("Equipo retirado", 0)), ("Scrap", "Scrap", cuentas.get("Scrap", 0)),
              ("Equipos comprados", "Equipo comprado", cuentas.get("Equipo comprado", 0))]
    return {"marca": core.marca(g.cuenta["id"]), "cuenta": g.cuenta, "ESTADOS": ESTADOS, "COLORES": core.COLORES, "vistas": vistas, "modulos": g.modulos,
            "tipos_equipo": ajustes_cuenta()["tipos"], "marcas_equipo": ajustes_cuenta()["marcas"], "modo_texto": ajustes_cuenta()["modo_texto"]}


def ajustes_cuenta():
    if "_ajustes" not in g:
        g._ajustes = core.ajustes(g.cuenta["id"])
    return g._ajustes


def U(valor):
    """Texto ingresado por el usuario, según el modo elegido en Configuración (mayúsculas, minúsculas o tal cual)."""
    t = (valor or "").strip()
    modo = ajustes_cuenta()["modo_texto"]
    return t.upper() if modo == "mayusculas" else t.lower() if modo == "minusculas" else t


def error(msg, codigo=400, **extra):
    return jsonify(ok=False, error=msg, **extra), codigo


# --------------------------------------------------------------------------- acceso
@app.route("/login", methods=["GET", "POST"])
def login():
    if g.cuenta:
        return redirect(inicio_url())
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
                session["v"] = 2
                session["ultimo"] = time.time()
                session["inact"] = minutos_inactividad(fila["id"])
                session.permanent = session["inact"] == 0      # si nunca se cierra por inactividad, también recuerda el navegador
                g.modulos = core.modulos_de(fila)
                csrf_token()
                dest = request.args.get("siguiente", "")
                return redirect(dest if dest.startswith("/") and not dest.startswith("//") else inicio_url())
            _intentos.setdefault(clave_ip, []).append(time.time())
            msg = "Mail o clave incorrectos." if not fila else "La cuenta está desactivada. Escribinos."
    if not msg and session.pop("expiro", None):
        msg = "Por seguridad, la sesión se cerró por inactividad. Ingresá de nuevo."
    csrf_token()
    return render_template("login.html", msg=msg)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def inicio():
    return redirect(inicio_url() if g.cuenta else url_for("login"))


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
@modulo_requerido("clientes")
def clientes():
    q = (request.args.get("q") or "").strip()
    with db() as con:
        sql = "SELECT *, CASE WHEN TRIM(IFNULL(razon_social,''))<>'' THEN razon_social ELSE apellido||', '||nombre END AS mostrar FROM clientes"
        params = []
        if q:
            like = f"%{core.sin_acentos(q)}%"
            campos = ("nombre", "apellido", "telefono", "dni", "ciudad", "domicilio", "razon_social", "condicion_iva", "cuit", "mail")  # incluye la condición de IVA
            sql += " WHERE " + " OR ".join(f"lower_u({c}) LIKE ?" for c in campos)
            sql += (" OR id IN (SELECT cliente_id FROM equipos WHERE lower_u(tipo||' '||marca||' '||modelo||' '||ifnull(serie,'')) LIKE ?)"
                    " OR id IN (SELECT cliente_id FROM ordenes WHERE CAST(id AS TEXT) LIKE ? OR lower_u(falla) LIKE ?)")
            params = [like] * len(campos) + [like, f"%{q.lstrip('#').lstrip('0') or q}%", like]
        filas = con.execute(sql + " ORDER BY mostrar COLLATE NOCASE", params).fetchall()
    return render_template("clientes.html", filas=filas, q=q, IVA=core.IVA_OPCIONES)


@app.route("/cliente/<int:cid>")
@login_requerido
@modulo_requerido("clientes")
def cliente_ver(cid):
    with db() as con:
        c = con.execute("SELECT * FROM clientes WHERE id=?", (cid,)).fetchone()
        if not c:
            abort(404)
        equipos = con.execute("SELECT * FROM equipos WHERE cliente_id=? ORDER BY id DESC", (cid,)).fetchall()
        ords = con.execute(
            "SELECT o.id, o.fecha_ingreso, o.estado, o.falla, o.presupuesto_respuesta, o.fecha_retiro, o.imposible_reparar, o.imposible_motivo, e.tipo, e.marca, e.modelo "
            "FROM ordenes o LEFT JOIN equipos e ON e.id=o.equipo_id WHERE o.cliente_id=? ORDER BY o.id DESC", (cid,)).fetchall()
    return render_template("cliente.html", c=c, equipos=equipos, ords=ords, IVA=core.IVA_OPCIONES,
                           fmt_fecha=fmt_fecha, texto_equipo=texto_equipo)


@app.route("/api/cliente", methods=["POST"])
@login_requerido
@modulo_requerido("clientes")
def api_cliente_guardar():
    d = request.get_json(force=True, silent=True) or {}
    cid = d.get("id")
    with db() as con:
        msg = validar_cliente(d, con, cid)
        if msg:
            return error(msg)
        valores = (U(d.get("nombre")), U(d.get("apellido")), (d.get("telefono") or "").strip(),
                   (d.get("dni") or "").strip(), U(d.get("ciudad")), U(d.get("domicilio")),
                   U(d.get("razon_social")), (d.get("condicion_iva") or "").strip() or "Consumidor Final",
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
@modulo_requerido("clientes")
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
@modulo_requerido("ordenes")
def api_cliente_ordenes(cid):
    with db() as con:
        c = con.execute("SELECT nombre, apellido, razon_social FROM clientes WHERE id=?", (cid,)).fetchone()
        if not c:
            return error("No existe el cliente", 404)
        filas = con.execute(
            "SELECT o.id, o.fecha_ingreso, o.estado, o.falla, o.presupuesto_respuesta, e.tipo, e.marca, e.modelo "
            "FROM ordenes o LEFT JOIN equipos e ON e.id=o.equipo_id WHERE o.cliente_id=? ORDER BY o.id DESC", (cid,)).fetchall()
    return jsonify(ok=True, cliente=core.nombre_cliente(c), ordenes=[
        dict(id=f["id"], fecha=fmt_fecha(f["fecha_ingreso"]), estado=f["estado"], falla=f["falla"] or "",
             equipo=texto_equipo(f["tipo"], f["marca"], f["modelo"]), rechazado=f["presupuesto_respuesta"] == "rechazado")
        for f in filas])


@app.route("/api/cliente/<int:cid>/contacto")
@login_requerido
@modulo_requerido("clientes")
def api_cliente_contacto(cid):
    with db() as con:
        c = con.execute("SELECT nombre, telefono FROM clientes WHERE id=?", (cid,)).fetchone()
    if not c:
        return error("No existe el cliente", 404)
    return jsonify(ok=True, nombre=(c["nombre"] or "").strip().title(), wa=telefono_whatsapp(c["telefono"]))


@app.route("/api/cliente/<int:cid>/equipos")
@login_requerido
@modulo_requerido("ordenes")
def api_cliente_equipos(cid):
    with db() as con:
        filas = con.execute("SELECT * FROM equipos WHERE cliente_id=? ORDER BY id DESC", (cid,)).fetchall()
    return jsonify(ok=True, equipos=[dict(f) for f in filas])


# --------------------------------------------------------------------------- órdenes: listado
SQL_ORDEN = {"nuevas": "o.fecha_ingreso DESC, o.id DESC", "viejas": "o.fecha_ingreso ASC, o.id ASC",
             "cliente": "CASE WHEN TRIM(IFNULL(c.razon_social,''))<>'' THEN c.razon_social ELSE c.apellido||', '||c.nombre END COLLATE NOCASE, o.id DESC",
             "estado": "o.estado, o.fecha_ingreso DESC"}


@app.route("/ordenes")
@login_requerido
@modulo_requerido("ordenes")
def ordenes():
    estado = request.args.get("estado", "")
    q = (request.args.get("q") or "").strip()
    orden = request.args.get("orden", "nuevas")
    where, params = [], []
    if estado in ESTADOS:
        where.append("o.estado=?")
        params.append(estado)
    for palabra in core.sin_acentos(q).replace(",", " ").split():          # cada palabra tiene que aparecer en algún dato de la orden
        num = palabra.lstrip("#")
        campos = ("c.nombre", "c.apellido", "c.razon_social", "c.telefono", "e.tipo", "e.marca", "e.modelo", "e.serie",
                  "e.descripcion", "o.falla", "o.accesorios", "o.estado", "o.presupuesto_detalle", "o.tecnico_presupuesto",
                  "o.tecnico_reparacion", "o.imposible_motivo")
        cond = " OR ".join(f"lower_u({c}) LIKE ?" for c in campos)
        params += [f"%{palabra}%"] * len(campos)
        if num.isdigit():                                                  # número de orden: #11, 0011 o 00011
            cond += " OR o.id = ? OR CAST(o.id AS TEXT) LIKE ?"
            params += [int(num), f"{num.lstrip('0') or '0'}%"]
        where.append(f"({cond})")
    sql = ("SELECT o.*, c.nombre, c.apellido, c.razon_social, c.telefono, e.tipo, e.marca, e.modelo, e.en_garantia "
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
@modulo_requerido("ordenes")
def orden_nueva():
    with db() as con:
        cliente = con.execute("SELECT * FROM clientes WHERE id=?", (request.args.get("cliente", type=int),)).fetchone()
    return render_template("orden.html", o=None, cliente=cliente, equipo=None, TIPOS=core.TIPOS_EQUIPO,
                           necesita_cliente=cliente is None)


@app.route("/orden/<int:oid>")
@login_requerido
@modulo_requerido("ordenes")
def orden_ver(oid):
    with db() as con:
        o = cargar_orden(con, oid)
        cliente = con.execute("SELECT * FROM clientes WHERE id=?", (o["cliente_id"],)).fetchone()
        equipo = con.execute("SELECT * FROM equipos WHERE id=?", (o["equipo_id"],)).fetchone() if o["equipo_id"] else None
        extras = [dict(monto=m, detalle=t) for m, t in opciones_de(con, oid)]
    equipo_txt = texto_equipo(equipo["tipo"], equipo["marca"], equipo["modelo"]) if equipo else "equipo"
    return render_template("orden.html", o=o, cliente=cliente, equipo=equipo, TIPOS=core.TIPOS_EQUIPO, necesita_cliente=False,
                           wa_numero=telefono_whatsapp(cliente["telefono"]), equipo_txt=equipo_txt,
                           nombre_pila=(cliente["nombre"] or "").strip().title(), extras=extras)


@app.route("/api/clientes/buscar")
@login_requerido
@modulo_requerido("clientes")
def api_clientes_buscar():
    q = core.sin_acentos((request.args.get("q") or "").strip())
    with db() as con:
        filas = con.execute(
            "SELECT id, nombre, apellido, razon_social, telefono, condicion_iva FROM clientes WHERE lower_u(nombre||' '||apellido||' '||IFNULL(razon_social,'')) LIKE ? OR lower_u(telefono) LIKE ? "
            "ORDER BY apellido COLLATE NOCASE LIMIT 15", (f"%{q}%", f"%{q}%")).fetchall()
    return jsonify(ok=True, clientes=[dict(f, mostrar=core.nombre_cliente(f)) for f in filas])


def importes(d):
    try:
        return parse_monto(d.get("monto")), parse_monto(d.get("sena")), None
    except ValueError:
        return None, None, "Revisá el presupuesto y la seña (solo números)."


def leer_opciones(d):
    """Opciones adicionales de presupuesto del pedido: ([(monto, detalle)], mensaje de error)."""
    extra = []
    for it in (d.get("opciones") or [])[:5]:
        try:
            m = parse_monto(it.get("monto"))
        except ValueError:
            return None, "Revisá el monto de las opciones de presupuesto (solo números)."
        det = U(it.get("detalle"))
        if m is None and not det:
            continue
        if m is None:
            return None, "Cada opción de presupuesto necesita un monto."
        extra.append((m, det))
    return extra, None


def opciones_de(con, oid):
    return [(r["monto"], r["detalle"] or "") for r in
            con.execute("SELECT monto, detalle FROM presupuesto_opciones WHERE orden_id=? ORDER BY n", (oid,))]


def guardar_opciones(con, oid, extra):
    con.execute("DELETE FROM presupuesto_opciones WHERE orden_id=?", (oid,))
    for n, (m, det) in enumerate(extra, 2):
        con.execute("INSERT INTO presupuesto_opciones (orden_id, n, monto, detalle) VALUES (?,?,?,?)", (oid, n, m, det))


def pide_codigo(d, motivo):
    """None si el código es correcto; si no, la respuesta de error que hace que el navegador lo pida."""
    if core.codigo_es_valido(g.cuenta["id"], d.get("codigo")):
        return None
    return error("Código de validación incorrecto." if d.get("codigo") else motivo, 403, necesita_codigo=True)


def lista_tecnicos():
    with db() as con:
        return [r["nombre"] for r in con.execute("SELECT nombre FROM tecnicos ORDER BY nombre COLLATE NOCASE")]


def usa_tecnicos():
    return core.leer_config(g.cuenta["id"], "usar_tecnicos", "1") != "0"


app.jinja_env.globals["lista_tecnicos"] = lista_tecnicos
app.jinja_env.globals["usa_tecnicos"] = usa_tecnicos


def tecnicos_filas():
    with db() as con:
        return [dict(r, usos=con.execute("SELECT COUNT(*) FROM ordenes WHERE lower(tecnico_presupuesto)=lower(?) OR lower(tecnico_reparacion)=lower(?)",
                                         (r["nombre"], r["nombre"])).fetchone()[0])
                for r in con.execute("SELECT * FROM tecnicos ORDER BY nombre COLLATE NOCASE")]


app.jinja_env.globals["tecnicos_filas"] = tecnicos_filas


@app.route("/api/tecnicos/<int:tid>", methods=["POST"])
@login_requerido
@modulo_requerido("ordenes")
def api_tecnico_editar(tid):
    d = request.get_json(force=True, silent=True) or {}
    nuevo = " ".join(U(d.get("nombre")).split())
    if not nuevo:
        return error("Escribí el nombre del técnico.")
    with db() as con:
        t = con.execute("SELECT * FROM tecnicos WHERE id=?", (tid,)).fetchone()
        if not t:
            return error("No existe el técnico.", 404)
        if con.execute("SELECT 1 FROM tecnicos WHERE lower(nombre)=lower(?) AND id<>?", (nuevo, tid)).fetchone():
            return error("Ya existe un técnico con ese nombre.")
        con.execute("UPDATE tecnicos SET nombre=? WHERE id=?", (nuevo, tid))
        # las órdenes que lo tenían asignado pasan a mostrar el nombre corregido
        con.execute("UPDATE ordenes SET tecnico_presupuesto=? WHERE lower(tecnico_presupuesto)=lower(?)", (nuevo, t["nombre"]))
        con.execute("UPDATE ordenes SET tecnico_reparacion=? WHERE lower(tecnico_reparacion)=lower(?)", (nuevo, t["nombre"]))
    return jsonify(ok=True)


@app.route("/api/tecnicos/<int:tid>/eliminar", methods=["POST"])
@login_requerido
@modulo_requerido("ordenes")
def api_tecnico_eliminar(tid):
    with db() as con:
        con.execute("DELETE FROM tecnicos WHERE id=?", (tid,))      # las órdenes ya cargadas conservan el nombre en su historial
    return jsonify(ok=True)


@app.route("/configuracion/tecnicos", methods=["POST"])
@login_requerido
@modulo_requerido("ordenes")
def configuracion_tecnicos():
    core.guardar_config(g.cuenta["id"], "usar_tecnicos", "1" if request.form.get("usar_tecnicos") else "0")
    flash("Configuración de técnicos guardada.", "ok")
    return redirect(url_for("configuracion"))


def minutos_inactividad(cid):
    """Minutos sin actividad antes de cerrar la sesión (0 = nunca). Por defecto, 1 hora."""
    try:
        m = int(core.leer_config(cid, "sesion_minutos", "60"))
    except ValueError:
        m = 60
    return max(0, m)


@app.route("/configuracion/sesion", methods=["POST"])
@login_requerido
def configuracion_sesion():
    try:
        m = int(request.form.get("minutos", "60"))
    except ValueError:
        m = 60
    m = m if m in (0, 15, 30, 60, 120, 240, 480) else 60
    core.guardar_config(g.cuenta["id"], "sesion_minutos", m)
    session["inact"] = m
    session["ultimo"] = time.time()
    session.permanent = m == 0
    flash("Tiempo de sesión guardado.", "ok")
    return redirect(url_for("configuracion"))


def asegurar_tecnico(con, nombre):
    """Devuelve el nombre normalizado (o None) y lo agrega a la lista de técnicos si no estaba."""
    nombre = " ".join(U(nombre).split()) if nombre else ""
    if not nombre:
        return None
    if not con.execute("SELECT 1 FROM tecnicos WHERE lower(nombre)=lower(?)", (nombre,)).fetchone():
        con.execute("INSERT INTO tecnicos (nombre) VALUES (?)", (nombre,))
    return nombre


@app.route("/api/tecnicos", methods=["GET", "POST"])
@login_requerido
@modulo_requerido("ordenes")
def api_tecnicos():
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        with db() as con:
            nombre = asegurar_tecnico(con, d.get("nombre"))
        if not nombre:
            return error("Escribí el nombre del técnico.")
        return jsonify(ok=True, nombre=nombre, tecnicos=lista_tecnicos())
    return jsonify(ok=True, tecnicos=lista_tecnicos())


@app.route("/api/orden", methods=["POST"])
@login_requerido
@modulo_requerido("ordenes")
def api_orden_crear():
    d = request.get_json(force=True, silent=True) or {}
    monto, sena, msg = importes(d)
    if msg:
        return error(msg)
    extra, msg = leer_opciones(d)
    if msg:
        return error(msg)
    if extra and monto is None:
        return error("Cargá el precio de la opción 1 antes de agregar otras opciones.")
    falla = U(d.get("falla"))
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
        campos = [U(d.get("tipo")), U(d.get("marca")), U(d.get("modelo")), U(d.get("serie")), U(d.get("descripcion")), (d.get("clave_acceso") or "").strip()]
        if equipo_id:
            if not con.execute("SELECT 1 FROM equipos WHERE id=? AND cliente_id=?", (equipo_id, cliente["id"])).fetchone():
                return error("El equipo no es de ese cliente.")
            con.execute("UPDATE equipos SET tipo=?, marca=?, modelo=?, serie=?, descripcion=?, clave_acceso=?, en_garantia=? WHERE id=?",
                        campos + [int(bool(d.get("en_garantia"))), equipo_id])
        else:
            equipo_id = con.execute(
                "INSERT INTO equipos (cliente_id, tipo, marca, modelo, serie, descripcion, clave_acceso, en_garantia, creado) "
                "VALUES (?,?,?,?,?,?,?,?,?)", [cliente["id"]] + campos + [int(bool(d.get("en_garantia"))), ahora]).lastrowid
        detalle = U(d.get("detalle"))
        tiene_pres = bool((monto and monto > 0) or detalle or extra)
        estado = E_PENDIENTE if tiene_pres else E_NUEVO
        tec_p = asegurar_tecnico(con, d.get("tecnico_presupuesto")) if usa_tecnicos() else None
        if usa_tecnicos() and tiene_pres and not tec_p:
            return error("Elegí el técnico que presupuesta.")
        oid = con.execute(
            "INSERT INTO ordenes (cliente_id, equipo_id, fecha_ingreso, estado, falla, accesorios, presupuesto_monto, "
            "presupuesto_detalle, fecha_presupuesto, sena_monto, actualizado, espera_repuesto, tecnico_presupuesto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cliente["id"], equipo_id, ahora, estado, falla, U(d.get("accesorios")), monto, detalle,
             ahora if monto is not None else None, sena, ahora, int(bool(d.get("espera_repuesto"))), tec_p)).lastrowid
        if tec_p:
            core.registrar_evento(con, oid, estado, f"Técnico que presupuesta: {tec_p}")
        core.registrar_evento(con, oid, estado, "Ingreso del equipo al taller")
        if monto is not None or detalle:
            core.registrar_evento(con, oid, estado, core.texto_evento_presupuesto(monto, detalle, True))
        if extra:
            guardar_opciones(con, oid, extra)
            core.registrar_evento(con, oid, estado, "Opciones adicionales de presupuesto - " + core.texto_opciones(extra))
        if sena:
            core.registrar_evento(con, oid, estado, f"Seña recibida: {fmt_monto(sena)}")
        if d.get("espera_repuesto"):
            core.registrar_evento(con, oid, estado, "Esperando repuesto")
    return jsonify(ok=True, id=oid, estado=estado, ofrecer_presupuesto=bool(tiene_pres))


@app.route("/api/orden/<int:oid>", methods=["POST"])
@login_requerido
@modulo_requerido("ordenes")
def api_orden_guardar(oid):
    d = request.get_json(force=True, silent=True) or {}
    with db() as con:
        o = cargar_orden(con, oid)
        resp = o["presupuesto_respuesta"]
        monto, sena, msg = importes(d)
        if msg:
            return error(msg)
        detalle = U(d.get("detalle"))
        extra, msg = leer_opciones(d)
        if msg:
            return error(msg)
        previas = opciones_de(con, oid)
        if extra and monto is None:
            return error("Cargá el precio de la opción 1 antes de agregar otras opciones.")
        if resp and [tuple(x) for x in extra] != previas:
            return error("El presupuesto ya fue respondido: no se pueden modificar las opciones.")
        # Con el presupuesto respondido se bloquea el precio; con «no aceptado» también la seña.
        if resp:
            if (monto, detalle) != (o["presupuesto_monto"], (o["presupuesto_detalle"] or "")):
                return error("El presupuesto ya fue respondido: no se puede modificar el precio ni el detalle.")
        if resp == "rechazado" and sena != o["sena_monto"]:
            return error("Con el presupuesto no aceptado no se puede modificar la seña.")
        falla = U(d.get("falla"))
        if not falla or not (d.get("tipo") or "").strip():
            return error("Completá el tipo de equipo y la falla.")
        estado = d.get("estado") or o["estado"]
        if estado == "Imposible reparar":          # no es un estado propio: la orden sigue en Pendiente con la marca y su descripción
            estado = E_PENDIENTE if o["estado"] in (E_NUEVO, E_PENDIENTE) else o["estado"]
            d["imposible"] = True
        if estado not in ESTADOS:
            return error("Estado inválido.")
        imp = int(bool(d.get("imposible"))) if "imposible" in d else o["imposible_reparar"]
        imp_motivo = U(d.get("imposible_motivo")) if "imposible_motivo" in d else (o["imposible_motivo"] or "")
        if imp and not imp_motivo:
            return error("Escribí la descripción de por qué es imposible reparar el equipo.")
        if imp and estado == E_NUEVO:
            estado = E_PENDIENTE
        if not imp:
            imp_motivo = ""
        if estado == E_COMPRADO and estado != o["estado"]:
            return error("Para pasar a «Equipo comprado» usá el botón Comprar.")
        if core.requiere_codigo(o["estado"], estado):
            r = pide_codigo(d, f"Cambiar el estado de «{o['estado']}» a «{estado}» requiere el código de validación.")
            if r:
                return r
        if estado == E_NUEVO and o["estado"] == E_NUEVO and ((monto and monto > 0) or detalle or extra):
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
        con_tec = usa_tecnicos()
        tec_p = asegurar_tecnico(con, d.get("tecnico_presupuesto")) if (con_tec and "tecnico_presupuesto" in d) else o["tecnico_presupuesto"]
        tec_r = asegurar_tecnico(con, d.get("tecnico_reparacion")) if (con_tec and "tecnico_reparacion" in d) else o["tecnico_reparacion"]
        if con_tec and ((monto and monto > 0) or detalle or extra) and not tec_p:
            return error("Elegí el técnico que presupuesta.")
        if con_tec and estado == E_LISTO and o["estado"] != E_LISTO and not tec_r:
            return error("Elegí el técnico que reparó el equipo.")
        con.execute("UPDATE ordenes SET estado=?, falla=?, accesorios=?, presupuesto_monto=?, presupuesto_detalle=?, sena_monto=?, "
                    "espera_repuesto=?, fecha_presupuesto=?, fecha_finalizado=?, fecha_retiro=?, fecha_archivada=?, actualizado=?, "
                    "tecnico_presupuesto=?, tecnico_reparacion=?, imposible_reparar=?, imposible_motivo=? WHERE id=?",
                    (estado, falla, U(d.get("accesorios")), monto, detalle, sena, repuesto, f_pres, f_fin, f_ret,
                     f_arch, ahora, tec_p, tec_r, imp, imp_motivo or None, oid))
        if imp and (not o["imposible_reparar"] or imp_motivo != (o["imposible_motivo"] or "")):
            core.registrar_evento(con, oid, estado, f"Imposible reparar: {imp_motivo}")
        elif not imp and o["imposible_reparar"]:
            core.registrar_evento(con, oid, estado, "Se quitó la marca de imposible reparar")
        if tec_p != o["tecnico_presupuesto"]:
            core.registrar_evento(con, oid, estado, f"Técnico que presupuesta: {tec_p}" if tec_p else "Técnico que presupuesta quitado")
        if tec_r != o["tecnico_reparacion"]:
            core.registrar_evento(con, oid, estado, f"Técnico que reparó: {tec_r}" if tec_r else "Técnico que reparó quitado")
        if o["equipo_id"]:
            con.execute("UPDATE equipos SET tipo=?, marca=?, modelo=?, serie=?, descripcion=?, clave_acceso=?, en_garantia=? WHERE id=?",
                        [U(d.get("tipo")), U(d.get("marca")), U(d.get("modelo")), U(d.get("serie")), U(d.get("descripcion")), (d.get("clave_acceso") or "").strip()]
                        + [int(bool(d.get("en_garantia"))), o["equipo_id"]])
        if estado != o["estado"]:
            core.registrar_evento(con, oid, estado, core.texto_evento_estado(o["estado"], estado, a_abonar, bool(dev)))
        if (monto, detalle) != (o["presupuesto_monto"], (o["presupuesto_detalle"] or "")):
            core.registrar_evento(con, oid, estado, core.texto_evento_presupuesto(monto, detalle, False))
        if extra != previas:
            guardar_opciones(con, oid, extra)
            core.registrar_evento(con, oid, estado, ("Opciones adicionales de presupuesto - " + core.texto_opciones(extra)) if extra
                                  else "Opciones adicionales de presupuesto quitadas")
        if repuesto != o["espera_repuesto"]:
            core.registrar_evento(con, oid, estado, "Esperando repuesto" if repuesto else "Repuesto recibido")
        if sena != o["sena_monto"]:
            core.registrar_evento(con, oid, estado, f"Seña recibida: {fmt_monto(sena)}" if sena else "Seña quitada")
        if falla != (o["falla"] or "") or U(d.get("accesorios")) != (o["accesorios"] or ""):
            core.registrar_evento(con, oid, estado, "Datos de ingreso modificados")
    return jsonify(ok=True, id=oid, estado=estado,
                   ofrecer_presupuesto=bool(o["estado"] == E_NUEVO and estado == E_PENDIENTE and not imp),
                   ofrecer_imposible=bool(imp and (not o["imposible_reparar"] or imp_motivo != (o["imposible_motivo"] or ""))),
                   imposible_motivo=imp_motivo)


@app.route("/api/orden/<int:oid>/accion", methods=["POST"])
@login_requerido
@modulo_requerido("ordenes")
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
            extras = opciones_de(con, oid)
            elegida = None
            if accion == "aceptar" and extras:
                todas = [(monto, o["presupuesto_detalle"] or "")] + extras
                try:
                    aceptadas = sorted({int(x) for x in (d.get("aceptadas") or [])})
                except (TypeError, ValueError):
                    aceptadas = []
                if not aceptadas or any(not 1 <= x <= len(todas) for x in aceptadas):
                    return error("Marcá al menos una opción que el cliente acepta (si no acepta ninguna, usá «El cliente rechazó el presupuesto»).")
                sel = [todas[x - 1] for x in aceptadas]
                total = sum(m for m, _ in sel)
                detalle_final = " + ".join(" ".join(t.split()) for _, t in sel if t.strip())
                if aceptadas != [1]:
                    con.execute("UPDATE ordenes SET presupuesto_monto=?, presupuesto_detalle=? WHERE id=?", (total, detalle_final, oid))
                texto = lambda m, t: fmt_monto(m) + (f" ({' '.join(t.split())})" if t.strip() else "")
                si = "; ".join(f"Opción {x}: {texto(*todas[x - 1])}" for x in aceptadas)
                no = "; ".join(f"Opción {x}: {texto(*todas[x - 1])}" for x in range(1, len(todas) + 1) if x not in aceptadas)
                con.execute("DELETE FROM presupuesto_opciones WHERE orden_id=?", (oid,))
                core.registrar_evento(con, oid, estado, "El cliente aceptó - " + si + (f". Total aceptado: {fmt_monto(total)}" if len(aceptadas) > 1 else "")
                                      + (f". No aceptó - {no}" if no else ""))
            nuevo_estado = E_PENDIENTE if estado == E_NUEVO else estado
            cambiar(nuevo_estado, ", presupuesto_respuesta=?, fecha_aceptacion=COALESCE(fecha_aceptacion, ?)" if nueva == "aceptado"
                    else ", presupuesto_respuesta=?", (nueva, ahora) if nueva == "aceptado" else (nueva,))
            core.registrar_evento(con, oid, nuevo_estado, core.TEXTO_RESPUESTA[nueva])
            if nuevo_estado != estado:
                core.registrar_evento(con, oid, nuevo_estado, core.texto_evento_estado(estado, nuevo_estado))
        elif accion in ("imposible", "quitar_imposible"):
            if estado in ESTADOS_FINALES or estado == E_RETIRADO:
                return error("La orden ya no está en curso.")
            if accion == "quitar_imposible":
                con.execute("UPDATE ordenes SET imposible_reparar=0, imposible_motivo=NULL, actualizado=? WHERE id=?", (ahora, oid))
                core.registrar_evento(con, oid, estado, "Se quitó la marca de imposible reparar")
            else:
                motivo = U(d.get("motivo"))
                if not motivo:
                    return error("Escribí la descripción de por qué es imposible reparar el equipo.")
                nuevo_estado = E_PENDIENTE if estado == E_NUEVO else estado
                con.execute("UPDATE ordenes SET imposible_reparar=1, imposible_motivo=?, estado=?, actualizado=? WHERE id=?", (motivo, nuevo_estado, ahora, oid))
                core.registrar_evento(con, oid, nuevo_estado, f"Imposible reparar: {motivo}")
                if nuevo_estado != estado:
                    core.registrar_evento(con, oid, nuevo_estado, core.texto_evento_estado(estado, nuevo_estado))
                return jsonify(ok=True, motivo=motivo)
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
            tec = (asegurar_tecnico(con, d.get("tecnico")) or o["tecnico_reparacion"]) if usa_tecnicos() else o["tecnico_reparacion"]
            if usa_tecnicos() and not tec:
                return error("Elegí el técnico que reparó el equipo.")
            if tec and tec != o["tecnico_reparacion"]:
                con.execute("UPDATE ordenes SET tecnico_reparacion=? WHERE id=?", (tec, oid))
                core.registrar_evento(con, oid, E_LISTO, f"Técnico que reparó: {tec}")
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
            cond = U(d.get("compra_condiciones"))
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
            motivo = U(d.get("dev_motivo"))
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


@app.route("/api/orden/<int:oid>/ticket")
@login_requerido
@modulo_requerido("ordenes")
def api_orden_ticket(oid):
    """Texto del comprobante de ingreso y número de WhatsApp del cliente."""
    with db() as con:
        o = cargar_orden(con, oid)
        c = con.execute("SELECT nombre, telefono FROM clientes WHERE id=?", (o["cliente_id"],)).fetchone()
        e = con.execute("SELECT * FROM equipos WHERE id=?", (o["equipo_id"],)).fetchone() if o["equipo_id"] else None
    e = e or {"tipo": "", "marca": "", "modelo": "", "serie": "", "descripcion": ""}
    nombre = (c["nombre"] or "").strip().title()
    texto = core.texto_ingreso(core.marca(g.cuenta["id"])["nombre"], oid, o["fecha_ingreso"], nombre, e["tipo"], e["marca"], e["modelo"],
                               e["serie"], o["accesorios"], o["falla"], e["descripcion"])
    return jsonify(ok=True, texto=texto, wa=telefono_whatsapp(c["telefono"]), nombre=nombre)


@app.route("/api/orden/<int:oid>/historial")
@login_requerido
@modulo_requerido("ordenes")
def api_orden_historial(oid):
    with db() as con:
        cargar_orden(con, oid)
        filas = con.execute("SELECT id, fecha, estado, nota FROM historial_ordenes WHERE orden_id=? ORDER BY fecha DESC, id DESC", (oid,)).fetchall()
    return jsonify(ok=True, items=[dict(id=f["id"], fecha=fmt_fecha(f["fecha"], True), estado=f["estado"] or "", nota=f["nota"] or "") for f in filas])


@app.route("/api/historial/<int:hid>/borrar", methods=["POST"])
@login_requerido
@modulo_requerido("ordenes")
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
                           rec_email=core.leer_config(cid, "rec_email", ""),
                           ult_resp=core.leer_config(cid, "respaldo_ultimo", ""), ult_resp_accion=core.leer_config(cid, "respaldo_ultimo_accion", ""))


@app.route("/configuracion/respaldo")
@login_requerido
@modulo_requerido("respaldo")
def respaldo():
    datos, nombre = core.zip_respaldo(g.cuenta["id"])
    return Response(datos, mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.route("/api/respaldo/estado")
@login_requerido
@modulo_requerido("respaldo")
def api_respaldo_estado():
    """Si toca el respaldo mensual, hace la copia en el servidor y avisa para ofrecer la descarga."""
    cid = g.cuenta["id"]
    if not core.respaldo_pendiente(cid):
        return jsonify(ok=True, pendiente=False)
    if not core.leer_config(cid, "respaldo_copia"):
        core.guardar_config(cid, "respaldo_copia", core.guardar_copia_servidor(cid))
    return jsonify(ok=True, pendiente=True, nombre=core.leer_config(cid, "respaldo_copia"))


@app.route("/api/respaldo/confirmar", methods=["POST"])
@login_requerido
@modulo_requerido("respaldo")
def api_respaldo_confirmar():
    """El usuario eligió guardar o no el archivo: en ambos casos arranca un nuevo ciclo de 30 días."""
    d = request.get_json(force=True, silent=True) or {}
    accion = "guardado" if d.get("accion") == "guardado" else "omitido"
    cid = g.cuenta["id"]
    core.guardar_config(cid, "respaldo_ultimo", core.ahora_dt().date().isoformat())
    core.guardar_config(cid, "respaldo_ultimo_accion", accion)
    core.guardar_config(cid, "respaldo_copia", None)
    return jsonify(ok=True)


@app.route("/configuracion/cargar", methods=["POST"])
@login_requerido
@modulo_requerido("respaldo")
def cargar_base():
    import tempfile, zipfile
    if not core.codigo_es_valido(g.cuenta["id"], request.form.get("codigo")):
        flash("Código de validación incorrecto: no se cargó nada.", "error")
        return redirect(url_for("configuracion"))
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        flash("Elegí el archivo a cargar.", "error")
        return redirect(url_for("configuracion"))
    nombre = archivo.filename.lower()
    if nombre.endswith(".rar"):
        flash("Los archivos .rar no se pueden abrir acá. Comprimilo como .zip o subí directamente el archivo .db.", "error")
        return redirect(url_for("configuracion"))
    with tempfile.TemporaryDirectory() as tmp:
        ruta = os.path.join(tmp, "entrada.db")
        try:
            if nombre.endswith(".zip"):
                with zipfile.ZipFile(archivo.stream) as z:
                    dbs = [n for n in z.namelist() if n.lower().endswith(".db") and not n.endswith("/")]
                    if not dbs:
                        raise ValueError("El .zip no contiene ningún archivo .db.")
                    with z.open(dbs[0]) as o, open(ruta, "wb") as f:
                        f.write(o.read(60 * 1024 * 1024))
            else:
                archivo.save(ruta)
            resumen = core.importar_base(g.cuenta["id"], ruta)
        except zipfile.BadZipFile:
            flash("El .zip está dañado.", "error")
            return redirect(url_for("configuracion"))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("configuracion"))
    flash("Base cargada: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in resumen.items()) + ".", "ok")
    return redirect(url_for("configuracion"))


# --------------------------------------------------------------------------- recordatorios
def _fecha_hora(d):
    from datetime import datetime as _dt
    try:
        return _dt.strptime(f"{d.get('fecha')} {d.get('hora')}", "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


@app.route("/recordatorios")
@login_requerido
@modulo_requerido("recordatorios")
def recordatorios():
    filtro = request.args.get("ver", "pendientes")
    sql = "SELECT * FROM recordatorios"
    sql += {"pendientes": " WHERE hecho=0 ORDER BY fecha_hora ASC", "hechos": " WHERE hecho=1 ORDER BY fecha_hora DESC"}.get(
        filtro, " ORDER BY hecho ASC, fecha_hora ASC")
    with db() as con:
        filas = con.execute(sql).fetchall()
    items = []
    for f in filas:
        estado, texto = core.clasificar_recordatorio(f["fecha_hora"], f["hecho"])
        items.append(dict(id=f["id"], fecha_hora=f["fecha_hora"], descripcion=f["descripcion"], estado=estado, texto=texto))
    base = core.ahora_dt()
    return render_template("recordatorios.html", items=items, filtro=filtro,
                           hoy=base.strftime("%Y-%m-%d"), hora_defecto=(base.replace(minute=0, second=0) + __import__("datetime").timedelta(hours=1)).strftime("%H:%M"))


@app.route("/api/recordatorio", methods=["POST"])
@login_requerido
@modulo_requerido("recordatorios")
def api_recordatorio_guardar():
    d = request.get_json(force=True, silent=True) or {}
    desc = U(d.get("descripcion"))
    fh = _fecha_hora(d)
    if not desc:
        return error("Escribí la descripción del recordatorio.")
    if not fh:
        return error("Fecha u hora inválida.")
    with db() as con:
        if d.get("id"):
            con.execute("UPDATE recordatorios SET fecha_hora=?, descripcion=?, avisado=0 WHERE id=?", (fh, desc, d["id"]))
        else:
            con.execute("INSERT INTO recordatorios (fecha_hora, descripcion, creado) VALUES (?,?,?)", (fh, desc, core.ahora()))
    return jsonify(ok=True)


@app.route("/api/recordatorio/<int:rid>/<accion>", methods=["POST"])
@login_requerido
@modulo_requerido("recordatorios")
def api_recordatorio_accion(rid, accion):
    from datetime import timedelta
    with db() as con:
        if accion == "alternar":
            con.execute("UPDATE recordatorios SET hecho = 1 - hecho WHERE id=?", (rid,))
        elif accion == "hecho":
            con.execute("UPDATE recordatorios SET hecho=1 WHERE id=?", (rid,))
        elif accion == "posponer":
            nuevo = (core.ahora_dt() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
            con.execute("UPDATE recordatorios SET fecha_hora=?, avisado=0 WHERE id=?", (nuevo, rid))
        elif accion == "eliminar":
            con.execute("DELETE FROM recordatorios WHERE id=?", (rid,))
        else:
            return error("Acción desconocida.", 404)
    return jsonify(ok=True)


@app.route("/api/recordatorios/avisos")
@login_requerido
@modulo_requerido("recordatorios")
def api_recordatorios_avisos():
    """Recordatorios cuya hora llegó y no se avisaron; se marcan como avisados. Además, cuántos hay vencidos/de hoy."""
    ahora_txt = core.ahora_dt().strftime("%Y-%m-%d %H:%M")
    with db() as con:
        vencidos = con.execute("SELECT id, fecha_hora, descripcion FROM recordatorios WHERE hecho=0 AND avisado=0 AND fecha_hora<=? "
                               "ORDER BY fecha_hora LIMIT 3", (ahora_txt,)).fetchall()
        for f in vencidos:
            con.execute("UPDATE recordatorios SET avisado=1 WHERE id=?", (f["id"],))
        pendientes = con.execute("SELECT COUNT(*) FROM recordatorios WHERE hecho=0 AND fecha_hora<=?",
                                 (ahora_txt[:10] + " 23:59",)).fetchone()[0]
    return jsonify(ok=True, pendientes=pendientes, avisos=[
        dict(id=f["id"], cuando=core.clasificar_recordatorio(f["fecha_hora"])[1].replace("VENCIDO · ", ""), descripcion=f["descripcion"])
        for f in vencidos])


@app.route("/configuracion/listas", methods=["POST"])
@login_requerido
def configuracion_listas():
    import json
    cid = g.cuenta["id"]
    accion = request.form.get("accion", "guardar")
    if accion == "restablecer":
        for clave in ("tipos_equipo", "marcas_equipo"):
            core.guardar_config(cid, clave, None)
        flash("Tipos de equipo y marcas restablecidos a los valores por defecto.", "ok")
        return redirect(url_for("configuracion"))
    tipos = core.limpiar_lista(request.form.get("tipos"))
    marcas = core.limpiar_lista(request.form.get("marcas"))
    if not tipos:
        flash("Tiene que haber al menos un tipo de equipo.", "error")
        return redirect(url_for("configuracion"))
    core.guardar_config(cid, "tipos_equipo", json.dumps(tipos))
    core.guardar_config(cid, "marcas_equipo", json.dumps(marcas))
    modo = request.form.get("modo_texto")
    core.guardar_config(cid, "modo_texto", modo if modo in core.MODOS_TEXTO else "mayusculas")
    flash("Listas y modo de escritura guardados.", "ok")
    return redirect(url_for("configuracion"))


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
    secciones = {f["id"]: ", ".join(core.MODULOS[m][0] for m in core.MODULOS if m in core.modulos_de(f)) for f in filas}
    return render_template("admin.html", filas=filas, stats=stats, nueva=nueva, fmt_fecha=fmt_fecha, MODULOS=core.MODULOS, secciones=secciones)


@app.route("/admin/crear", methods=["POST"])
@admin_requerido
def admin_crear():
    try:
        cid, clave = core.crear_cuenta(request.form.get("email", ""), request.form.get("comercio", ""))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin"))
    if request.form.get("elegir_secciones"):
        core.guardar_modulos(cid, request.form.getlist("modulo"))
    session["cuenta_nueva"] = {"email": request.form.get("email", "").strip().lower(), "clave": clave}
    return redirect(url_for("admin"))


@app.route("/admin/<int:cid>/secciones", methods=["GET", "POST"])
@admin_requerido
def admin_secciones(cid):
    with core.conectar_cuentas() as con:
        f = con.execute("SELECT * FROM cuentas WHERE id=?", (cid,)).fetchone()
    if not f:
        abort(404)
    if request.method == "POST":
        core.guardar_modulos(cid, request.form.getlist("modulo"))
        flash(f"Secciones de «{f['comercio']}» actualizadas.", "ok")
        return redirect(url_for("admin"))
    return render_template("admin_secciones.html", f=f, activos=core.modulos_de(f), MODULOS=core.MODULOS, FUTUROS=core.MODULOS_FUTUROS)


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


stock.registrar(app, login_requerido, modulo_requerido, db, U, error)
ventas.registrar(app, login_requerido, modulo_requerido, db, error)
facturacion.registrar(app, login_requerido, modulo_requerido, db, U, error)


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
