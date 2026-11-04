"""Sección Ventas: caja simple (código de barras / código / buscador), descuenta stock y registra la forma de pago."""
from flask import abort, g, jsonify, render_template, request

import core
from stock import cant_o_error, fmt_cant


def crear_tablas(con):
    existia = con.execute("SELECT 1 FROM sqlite_master WHERE name='ventas'").fetchone()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, total REAL NOT NULL, forma_id INTEGER, forma TEXT,
            recibido REAL, vuelto REAL, nota TEXT, anulada INTEGER NOT NULL DEFAULT 0, anulada_fecha TEXT);
        CREATE TABLE IF NOT EXISTS venta_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, venta_id INTEGER NOT NULL, producto_id INTEGER, codigo TEXT, nombre TEXT NOT NULL,
            abrev TEXT, decimales INTEGER NOT NULL DEFAULT 0, cantidad REAL NOT NULL, precio_unit REAL NOT NULL,
            descuento_pct REAL, subtotal REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS cobros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, orden_id INTEGER, monto REAL NOT NULL, forma_id INTEGER, forma TEXT, nota TEXT);
        CREATE TABLE IF NOT EXISTS cheques (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, origen TEXT, orden_id INTEGER, venta_id INTEGER, forma TEXT, monto REAL NOT NULL,
            emision TEXT, cobro TEXT, numero TEXT, banco TEXT, entrega TEXT, cobrado INTEGER NOT NULL DEFAULT 0, cobrado_fecha TEXT);
        CREATE INDEX IF NOT EXISTS ix_cobros_fecha ON cobros(fecha);
        CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas(fecha);
        CREATE INDEX IF NOT EXISTS ix_venta_items_venta ON venta_items(venta_id);
    """)
    if "cobro_id" not in [r[1] for r in con.execute("PRAGMA table_info(cheques)")]:
        con.execute("ALTER TABLE cheques ADD COLUMN cobro_id INTEGER")
    if not existia:        # la primera vez, se suma «Billetera virtual» a las formas de pago
        if not con.execute("SELECT 1 FROM stock_formas_pago WHERE lower(nombre) LIKE 'billetera%'").fetchone():
            con.execute("INSERT INTO stock_formas_pago (nombre, aplica_descuento) VALUES ('Billetera virtual', 0)")


def sembrar_cheques(cid):
    """Una sola vez por comercio con Ventas: suma las formas de pago «Cheque» y «E-cheq» (piden los datos del cheque)."""
    if core.leer_config(cid, "cheques_sembrados"):
        return
    with core.conectar(cid) as con:
        if not con.execute("SELECT 1 FROM stock_formas_pago WHERE lower(nombre) LIKE '%cheq%'").fetchone():
            for n in ("Cheque", "E-Cheq"):
                con.execute("INSERT INTO stock_formas_pago (nombre, aplica_descuento, pide_cheque) VALUES (?,0,1)", (n,))
    core.guardar_config(cid, "cheques_sembrados", "1")


def validar_cheque(d):
    """(datos limpios, mensaje de error). Los cinco datos son obligatorios."""
    import re
    d = d or {}
    limpio = {"emision": (d.get("emision") or "").strip()[:10], "cobro": (d.get("cobro") or "").strip()[:10], "numero": " ".join(str(d.get("numero") or "").split())[:40],
              "banco": " ".join(str(d.get("banco") or "").split()).upper()[:60], "entrega": " ".join(str(d.get("entrega") or "").split()).upper()[:80]}
    for k in ("emision", "cobro"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", limpio[k]):
            return None, "Completá la fecha de emisión y la fecha de cobro del cheque."
    if limpio["cobro"] < limpio["emision"]:
        return None, "La fecha de cobro no puede ser anterior a la de emisión."
    if not limpio["numero"] or not limpio["banco"] or not limpio["entrega"]:
        return None, "Completá el número, el banco y quién entrega el cheque."
    return limpio, None


def guardar_cheque(con, datos, forma, monto, origen, orden_id=None, venta_id=None, cobro_id=None):
    con.execute("INSERT INTO cheques (fecha, origen, orden_id, venta_id, forma, monto, emision, cobro, numero, banco, entrega, cobro_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (core.ahora(), origen, orden_id, venta_id, forma, monto, datos["emision"], datos["cobro"], datos["numero"], datos["banco"], datos["entrega"], cobro_id))


def _iva(cid):
    try:
        defecto = float(core.leer_config(cid, "stock_iva_defecto", "21") or 21)
    except ValueError:
        defecto = 21.0
    return core.leer_config(cid, "stock_iva_activo", "0") == "1", defecto


def _lista_final(p, iva_activo, iva_def):
    if p["precio"] is None:
        return None
    if not iva_activo:
        return round(p["precio"], 2)
    return round(p["precio"] * (1 + (iva_def if p["iva"] is None else p["iva"]) / 100), 2)


def registrar(app, login_requerido, modulo_requerido, db, error):
    SQL = ("SELECT p.*, u.abrev, u.decimales, IFNULL(p.descuento, IFNULL(m.descuento, c.descuento)) AS desc_pct FROM stock_productos p "
           "JOIN stock_unidades u ON u.id=p.unidad_id LEFT JOIN stock_categorias c ON c.id=p.categoria_id "
           "LEFT JOIN stock_marcas m ON m.id=p.marca_id")

    def item(p, ia, idef):
        return {"id": p["id"], "nombre": p["nombre"], "codigo": p["codigo"] or p["codigo_barras"] or "", "abrev": p["abrev"],
                "decimales": p["decimales"], "stock": p["stock_actual"], "precio": _lista_final(p, ia, idef), "desc": p["desc_pct"] or 0}

    @app.route("/ventas")
    @login_requerido
    @modulo_requerido("ventas")
    def ventas():
        with db() as con:
            formas = [dict(r) for r in con.execute("SELECT * FROM stock_formas_pago ORDER BY id")]
        return render_template("ventas/nueva.html", formas=formas)

    @app.route("/api/ventas/buscar")
    @login_requerido
    @modulo_requerido("ventas")
    def api_ventas_buscar():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify(ok=True, exacto=None, resultados=[])
        ia, idef = _iva(g.cuenta["id"])
        with db() as con:
            p = con.execute(SQL + " WHERE p.activo=1 AND (lower(p.codigo)=lower(?) OR lower(p.codigo_barras)=lower(?)) LIMIT 1", (q, q)).fetchone()
            if p:
                return jsonify(ok=True, exacto=item(p, ia, idef), resultados=[])
            like = f"%{core.sin_acentos(q)}%"
            filas = con.execute(SQL + " WHERE p.activo=1 AND (lower_u(p.nombre) LIKE ? OR lower_u(p.codigo) LIKE ? OR lower_u(p.codigo_barras) LIKE ? "
                                "OR lower_u(c.nombre) LIKE ? OR lower_u(m.nombre) LIKE ?) ORDER BY p.nombre COLLATE NOCASE LIMIT 25", (like,) * 5).fetchall()
        return jsonify(ok=True, exacto=None, resultados=[item(r, ia, idef) for r in filas])

    @app.route("/api/ventas/confirmar", methods=["POST"])
    @login_requerido
    @modulo_requerido("ventas")
    def api_ventas_confirmar():
        d = request.get_json(force=True, silent=True) or {}
        items = d.get("items") or []
        if not items:
            return error("Agregá al menos un producto.")
        ia, idef = _iva(g.cuenta["id"])
        with db() as con:
            forma = con.execute("SELECT * FROM stock_formas_pago WHERE id=?", (d.get("forma_id"),)).fetchone()
            if not forma:
                return error("Elegí la forma de pago.")
            lineas, total, visto = [], 0.0, {}
            for it in items:
                p = con.execute(SQL + " WHERE p.id=?", (it.get("id"),)).fetchone()
                if not p or not p["activo"]:
                    return error("Hay un producto que ya no está disponible.")
                try:
                    cant = cant_o_error(it.get("cantidad"), p["decimales"])
                except ValueError:
                    return error(f"Revisá la cantidad de {p['nombre']}.")
                if not cant or cant <= 0:
                    return error(f"Revisá la cantidad de {p['nombre']}.")
                visto[p["id"]] = visto.get(p["id"], 0) + cant
                if visto[p["id"]] > p["stock_actual"] + 1e-9:
                    return error(f"Stock insuficiente de {p['nombre']}: hay {fmt_cant(p['stock_actual'], p['decimales'])} {p['abrev']}.")
                base = _lista_final(p, ia, idef)
                desc = p["desc_pct"] if (forma["aplica_descuento"] and p["desc_pct"]) else 0
                if it.get("manual"):
                    try:
                        unit = core.parse_monto(it.get("precio"))
                    except ValueError:
                        unit = None
                    desc = 0
                else:
                    unit = None if base is None else round(base * (1 - desc / 100), 2)
                if unit is None:
                    return error(f"{p['nombre']} no tiene precio. Cargalo en Stock o escribí el precio en la venta.")
                sub = round(cant * unit, 2)
                total += sub
                lineas.append((p, cant, unit, desc or None, sub))
            total = round(total, 2)
            recibido = vuelto = None
            if d.get("recibido") not in (None, ""):
                try:
                    recibido = core.parse_monto(d.get("recibido"))
                except ValueError:
                    return error("Revisá el monto recibido.")
                if recibido is not None and recibido < total - 0.001:
                    return error("El monto recibido es menor al total.")
                vuelto = round(recibido - total, 2) if recibido is not None else None
            cheque = None
            if forma["pide_cheque"]:
                cheque, msg = validar_cheque(d.get("cheque"))
                if msg:
                    return error(msg)
            ahora = core.ahora()
            vid = con.execute("INSERT INTO ventas (fecha, total, forma_id, forma, recibido, vuelto, nota) VALUES (?,?,?,?,?,?,?)",
                              (ahora, total, forma["id"], forma["nombre"], recibido, vuelto, (d.get("nota") or "").strip() or None)).lastrowid
            for p, cant, unit, desc, sub in lineas:
                con.execute("INSERT INTO venta_items (venta_id, producto_id, codigo, nombre, abrev, decimales, cantidad, precio_unit, descuento_pct, subtotal) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)", (vid, p["id"], p["codigo"] or p["codigo_barras"], p["nombre"], p["abrev"], p["decimales"], cant, unit, desc, sub))
                nuevo = round(con.execute("SELECT stock_actual FROM stock_productos WHERE id=?", (p["id"],)).fetchone()[0] - cant, 6)
                con.execute("UPDATE stock_productos SET stock_actual=?, actualizado=? WHERE id=?", (nuevo, ahora, p["id"]))
                con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo) VALUES (?,?,?,?,?,?)",
                            (p["id"], ahora, "egreso", -cant, nuevo, f"Venta N° {vid}"))
            if cheque:
                guardar_cheque(con, cheque, forma["nombre"], total, "venta", venta_id=vid)
        return jsonify(ok=True, id=vid, total=total, vuelto=vuelto)

    @app.route("/ventas/historial")
    @login_requerido
    @modulo_requerido("ventas")
    def ventas_historial():
        hoy = core.ahora()[:10]
        desde = (request.args.get("desde") or hoy)[:10]
        hasta = (request.args.get("hasta") or desde)[:10]
        with db() as con:
            filas = con.execute("SELECT v.*, (SELECT COUNT(*) FROM venta_items i WHERE i.venta_id=v.id) AS n FROM ventas v "
                                "WHERE substr(v.fecha,1,10) BETWEEN ? AND ? ORDER BY v.id DESC LIMIT 500", (desde, hasta)).fetchall()
        por_forma, total = {}, 0.0
        for v in filas:
            if not v["anulada"]:
                por_forma[v["forma"] or "—"] = por_forma.get(v["forma"] or "—", 0) + v["total"]
                total += v["total"]
        return render_template("ventas/historial.html", filas=filas, desde=desde, hasta=hasta, por_forma=sorted(por_forma.items()),
                               total=total, cantidad=sum(1 for v in filas if not v["anulada"]), fmt_fecha=core.fmt_fecha)

    # ---------------- corregir cobros de órdenes (solo con el código de validación)
    def _sin_codigo(d, motivo):
        if core.codigo_es_valido(g.cuenta["id"], d.get("codigo")):
            return None
        return jsonify(ok=False, necesita_codigo=True, error="Código de validación incorrecto." if d.get("codigo") else motivo), 403

    @app.route("/api/cobros/<int:cid_>")
    @login_requerido
    @modulo_requerido("ventas")
    def api_cobro(cid_):
        with db() as con:
            c = con.execute("SELECT * FROM cobros WHERE id=?", (cid_,)).fetchone()
            if not c:
                return error("No existe el cobro.", 404)
            ch = con.execute("SELECT * FROM cheques WHERE cobro_id=?", (cid_,)).fetchone()
            formas = [dict(id=r["id"], nombre=r["nombre"], cheque=bool(r["pide_cheque"])) for r in con.execute("SELECT id, nombre, pide_cheque FROM stock_formas_pago ORDER BY id")]
        return jsonify(ok=True, cobro=dict(c), cheque=dict(ch) if ch else None, formas=formas)

    @app.route("/api/cobros/<int:cid_>/editar", methods=["POST"])
    @login_requerido
    @modulo_requerido("ventas")
    def api_cobro_editar(cid_):
        d = request.get_json(force=True, silent=True) or {}
        r = _sin_codigo(d, "Modificar un cobro requiere el código de validación.")
        if r:
            return r
        try:
            monto = core.parse_monto(d.get("monto"))
        except ValueError:
            monto = None
        if not monto or monto <= 0:
            return error("Escribí el importe cobrado.")
        with db() as con:
            c = con.execute("SELECT * FROM cobros WHERE id=?", (cid_,)).fetchone()
            if not c:
                return error("No existe el cobro.", 404)
            forma = con.execute("SELECT * FROM stock_formas_pago WHERE id=?", (d.get("forma_id"),)).fetchone()
            if not forma:
                return error("Elegí la forma de pago.")
            cheque = None
            if forma["pide_cheque"]:
                cheque, msg = validar_cheque(d.get("cheque"))
                if msg:
                    return error(msg)
            con.execute("UPDATE cobros SET monto=?, forma_id=?, forma=? WHERE id=?", (monto, forma["id"], forma["nombre"], cid_))
            con.execute("DELETE FROM cheques WHERE cobro_id=?", (cid_,))
            if cheque:
                guardar_cheque(con, cheque, forma["nombre"], monto, "orden", orden_id=c["orden_id"], cobro_id=cid_)
            if c["orden_id"]:
                o = con.execute("SELECT estado FROM ordenes WHERE id=?", (c["orden_id"],)).fetchone()
                if o:
                    core.registrar_evento(con, c["orden_id"], o["estado"], f"Cobro corregido: {core.fmt_monto(c['monto'])} ({c['forma']}) → {core.fmt_monto(monto)} ({forma['nombre']})")
        return jsonify(ok=True)

    @app.route("/api/cobros/<int:cid_>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("ventas")
    def api_cobro_eliminar(cid_):
        d = request.get_json(force=True, silent=True) or {}
        r = _sin_codigo(d, "Eliminar un cobro requiere el código de validación.")
        if r:
            return r
        with db() as con:
            c = con.execute("SELECT * FROM cobros WHERE id=?", (cid_,)).fetchone()
            if not c:
                return error("No existe el cobro.", 404)
            con.execute("DELETE FROM cheques WHERE cobro_id=?", (cid_,))
            con.execute("DELETE FROM cobros WHERE id=?", (cid_,))
            if c["orden_id"]:
                o = con.execute("SELECT estado FROM ordenes WHERE id=?", (c["orden_id"],)).fetchone()
                if o:
                    core.registrar_evento(con, c["orden_id"], o["estado"], f"Cobro eliminado: {core.fmt_monto(c['monto'])} ({c['forma']})")
        return jsonify(ok=True)

    @app.route("/ventas/cheques")
    @login_requerido
    @modulo_requerido("ventas")
    def ventas_cheques():
        """Cheques y e-cheqs recibidos, por fecha de cobro."""
        estado = request.args.get("e") if request.args.get("e") in ("cartera", "cobrados", "todos") else "cartera"
        where = {"cartera": "WHERE c.cobrado=0", "cobrados": "WHERE c.cobrado=1", "todos": ""}[estado]
        with db() as con:
            filas = con.execute(f"SELECT c.*, (SELECT cl.nombre || ' ' || cl.apellido FROM ordenes o JOIN clientes cl ON cl.id=o.cliente_id WHERE o.id=c.orden_id) AS cliente "
                                f"FROM cheques c {where} ORDER BY c.cobro, c.id LIMIT 500").fetchall()
            en_cartera = con.execute("SELECT COUNT(*), COALESCE(SUM(monto),0) FROM cheques WHERE cobrado=0").fetchone()
        return render_template("ventas/cheques.html", filas=filas, estado=estado, n_cartera=en_cartera[0], total_cartera=en_cartera[1], hoy=core.ahora()[:10], fmt_fecha=core.fmt_fecha)

    @app.route("/api/cheques/<int:cid_>/cobrado", methods=["POST"])
    @login_requerido
    @modulo_requerido("ventas")
    def api_cheque_cobrado(cid_):
        d = request.get_json(force=True, silent=True) or {}
        with db() as con:
            if not con.execute("SELECT 1 FROM cheques WHERE id=?", (cid_,)).fetchone():
                return error("No existe el cheque.", 404)
            if d.get("cobrado"):
                con.execute("UPDATE cheques SET cobrado=1, cobrado_fecha=? WHERE id=?", (core.ahora(), cid_))
            else:
                con.execute("UPDATE cheques SET cobrado=0, cobrado_fecha=NULL WHERE id=?", (cid_,))
        return jsonify(ok=True)

    @app.route("/ventas/estadisticas")
    @login_requerido
    @modulo_requerido("ventas")
    def ventas_estadisticas():
        """A dónde fue el dinero: ventas de mostrador y cobros de órdenes, por forma de pago, en el día, la semana o el mes."""
        from datetime import date, timedelta
        modo = request.args.get("p") if request.args.get("p") in ("dia", "semana", "mes") else "dia"
        try:
            ref = date.fromisoformat((request.args.get("f") or core.ahora()[:10])[:10])
        except ValueError:
            ref = date.fromisoformat(core.ahora()[:10])
        if modo == "dia":
            d1 = d2 = ref
            ant, sig = ref - timedelta(days=1), ref + timedelta(days=1)
        elif modo == "semana":
            d1 = ref - timedelta(days=ref.weekday())
            d2 = d1 + timedelta(days=6)
            ant, sig = d1 - timedelta(days=7), d1 + timedelta(days=7)
        else:
            d1 = ref.replace(day=1)
            sig = (d1 + timedelta(days=32)).replace(day=1)
            d2 = sig - timedelta(days=1)
            ant = (d1 - timedelta(days=1)).replace(day=1)
        a, b = d1.isoformat(), d2.isoformat()
        with db() as con:
            vts = con.execute("SELECT substr(fecha,1,10) AS dia, COALESCE(forma,'—') AS forma, SUM(total) AS t, COUNT(*) AS n FROM ventas "
                              "WHERE anulada=0 AND substr(fecha,1,10) BETWEEN ? AND ? GROUP BY dia, forma", (a, b)).fetchall()
            cbs = con.execute("SELECT substr(fecha,1,10) AS dia, COALESCE(forma,'—') AS forma, SUM(monto) AS t, COUNT(*) AS n FROM cobros "
                              "WHERE substr(fecha,1,10) BETWEEN ? AND ? GROUP BY dia, forma", (a, b)).fetchall()
            movs = []
            if modo == "dia":
                movs = [dict(hora=r["fecha"][11:16], origen="Venta N° %d" % r["id"], forma=r["forma"] or "—", monto=r["total"]) for r in
                        con.execute("SELECT id, fecha, forma, total FROM ventas WHERE anulada=0 AND substr(fecha,1,10)=? ORDER BY id", (a,))]
                movs += [dict(hora=r["fecha"][11:16], origen="Orden N° %04d · %s" % (r["orden_id"] or 0, r["cliente"] or ""), forma=r["forma"] or "—", monto=r["monto"], orden=r["orden_id"], cobro=r["id"]) for r in
                         con.execute("SELECT c.id, c.fecha, c.orden_id, c.forma, c.monto, (SELECT cl.nombre || ' ' || cl.apellido FROM ordenes o JOIN clientes cl ON cl.id=o.cliente_id "
                                     "WHERE o.id=c.orden_id) AS cliente FROM cobros c WHERE substr(c.fecha,1,10)=? ORDER BY c.id", (a,))]
                movs.sort(key=lambda m: m["hora"])
        formas, dias = {}, {}
        tot_v = tot_c = 0.0
        for r in vts:
            f = formas.setdefault(r["forma"], [0.0, 0.0]); f[0] += r["t"]; tot_v += r["t"]
            dd = dias.setdefault(r["dia"], [0.0, 0.0]); dd[0] += r["t"]
        for r in cbs:
            f = formas.setdefault(r["forma"], [0.0, 0.0]); f[1] += r["t"]; tot_c += r["t"]
            dd = dias.setdefault(r["dia"], [0.0, 0.0]); dd[1] += r["t"]
        total = tot_v + tot_c
        filas = sorted(([k, v[0], v[1], v[0] + v[1], (100 * (v[0] + v[1]) / total) if total else 0] for k, v in formas.items()), key=lambda x: -x[3])
        lista_dias = []
        d = d1
        while d <= d2 and modo != "dia":
            v = dias.get(d.isoformat(), [0.0, 0.0])
            lista_dias.append(dict(fecha=d.isoformat(), txt=core.fmt_fecha(d.isoformat()), dia=core.DIAS[d.weekday()].capitalize(), v=v[0], c=v[1], t=v[0] + v[1]))
            d += timedelta(days=1)
        if modo == "dia":
            titulo = core.fmt_fecha(a)
        elif modo == "semana":
            titulo = f"{core.fmt_fecha(a)} al {core.fmt_fecha(b)}"
        else:
            titulo = f"{['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'][d1.month - 1]} {d1.year}"
        return render_template("ventas/estadisticas.html", modo=modo, titulo=titulo, ref=ref.isoformat(), ant=ant.isoformat(), sig=sig.isoformat(), filas=filas,
                               total=total, tot_v=tot_v, tot_c=tot_c, dias=lista_dias, movs=movs, hoy=core.ahora()[:10])

    @app.route("/api/ventas/<int:vid>")
    @login_requerido
    @modulo_requerido("ventas")
    def api_venta(vid):
        with db() as con:
            v = con.execute("SELECT * FROM ventas WHERE id=?", (vid,)).fetchone()
            if not v:
                abort(404)
            its = [dict(r, cant_txt=fmt_cant(r["cantidad"], r["decimales"])) for r in
                   con.execute("SELECT * FROM venta_items WHERE venta_id=? ORDER BY id", (vid,))]
        return jsonify(ok=True, venta=dict(v), items=its, fecha_txt=core.fmt_fecha(v["fecha"], True))

    @app.route("/api/ventas/<int:vid>/anular", methods=["POST"])
    @login_requerido
    @modulo_requerido("ventas")
    def api_venta_anular(vid):
        with db() as con:
            v = con.execute("SELECT * FROM ventas WHERE id=?", (vid,)).fetchone()
            if not v:
                return error("No existe la venta.", 404)
            if v["anulada"]:
                return error("La venta ya estaba anulada.")
            ahora = core.ahora()
            for i in con.execute("SELECT * FROM venta_items WHERE venta_id=?", (vid,)).fetchall():
                p = con.execute("SELECT stock_actual FROM stock_productos WHERE id=?", (i["producto_id"],)).fetchone()
                if not p:
                    continue          # el producto se eliminó: no hay stock para devolver
                nuevo = round(p[0] + i["cantidad"], 6)
                con.execute("UPDATE stock_productos SET stock_actual=?, actualizado=? WHERE id=?", (nuevo, ahora, i["producto_id"]))
                con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo) VALUES (?,?,?,?,?,?)",
                            (i["producto_id"], ahora, "ingreso", i["cantidad"], nuevo, f"Anulación venta N° {vid}"))
            con.execute("UPDATE ventas SET anulada=1, anulada_fecha=? WHERE id=?", (ahora, vid))
        return jsonify(ok=True)
