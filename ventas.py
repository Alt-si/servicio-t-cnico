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
        CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas(fecha);
        CREATE INDEX IF NOT EXISTS ix_venta_items_venta ON venta_items(venta_id);
    """)
    if not existia:        # la primera vez, se suma «Billetera virtual» a las formas de pago
        if not con.execute("SELECT 1 FROM stock_formas_pago WHERE lower(nombre) LIKE 'billetera%'").fetchone():
            con.execute("INSERT INTO stock_formas_pago (nombre, aplica_descuento) VALUES ('Billetera virtual', 0)")


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
    SQL = ("SELECT p.*, u.abrev, u.decimales, IFNULL(m.descuento, c.descuento) AS desc_pct FROM stock_productos p "
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
