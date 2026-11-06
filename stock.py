"""Sección «Control de stock»: inventario genérico para cualquier comercio o rubro.

Todo es editable por el comercio: rubros (categorías), marcas, unidades de medida (unidad, kg, litro, metro...)
y los nombres de las etiquetas. Es código nuevo e independiente: no modifica las demás secciones.
"""
import csv
import io

from flask import Response, abort, g, jsonify, redirect, render_template, request, url_for

import re

import core

TABLAS_STOCK = ("stock_categorias", "stock_marcas", "stock_unidades", "stock_productos", "stock_movimientos", "stock_proveedores")

# (nombre, abreviatura, decimales permitidos en la cantidad)
UNIDADES_INICIALES = [("Unidad", "u", 0), ("Par", "par", 0), ("Caja", "caja", 0), ("Pack", "pack", 0), ("Docena", "doc", 0),
                      ("Kilogramo", "kg", 3), ("Gramo", "g", 0), ("Litro", "l", 3), ("Mililitro", "ml", 0),
                      ("Metro", "m", 2), ("Metro cuadrado", "m²", 2)]
MOTIVOS = ["Compra a proveedor", "Venta", "Devolución de cliente", "Devolución a proveedor", "Uso interno",
           "Rotura / merma", "Pérdida", "Inventario (conteo)", "Corrección"]


def crear_tablas(con):
    """Crea las tablas de stock (si faltan) y carga las unidades iniciales la primera vez."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS stock_categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stock_marcas (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stock_unidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, abrev TEXT NOT NULL, decimales INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS stock_productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT, nombre TEXT NOT NULL, descripcion TEXT,
            categoria_id INTEGER, marca_id INTEGER, unidad_id INTEGER NOT NULL,
            costo REAL, precio REAL, stock_minimo REAL NOT NULL DEFAULT 0, stock_actual REAL NOT NULL DEFAULT 0,
            ubicacion TEXT, activo INTEGER NOT NULL DEFAULT 1, creado TEXT, actualizado TEXT);
        CREATE TABLE IF NOT EXISTS stock_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER NOT NULL, fecha TEXT NOT NULL,
            tipo TEXT NOT NULL, cantidad REAL NOT NULL, stock_resultante REAL NOT NULL, motivo TEXT, nota TEXT);
        CREATE INDEX IF NOT EXISTS ix_stock_prod_nombre ON stock_productos(nombre);
        CREATE INDEX IF NOT EXISTS ix_stock_mov_prod ON stock_movimientos(producto_id);
    """)
    def agregar(tabla, col, tipo):
        if col not in [r[1] for r in con.execute(f"PRAGMA table_info({tabla})")]:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    for tabla in ("stock_categorias", "stock_marcas"):          # reglas de precio por rubro / marca (% , vacío = sin regla)
        for col in ("margen_lista", "margen_contado", "descuento"):
            agregar(tabla, col, "REAL")
    agregar("stock_productos", "precio_contado", "REAL")
    agregar("stock_productos", "codigo_barras", "TEXT")
    agregar("stock_productos", "iva", "REAL")                   # vacío = usa el IVA general de la cuenta
    agregar("stock_productos", "precio_auto", "INTEGER NOT NULL DEFAULT 0")
    agregar("stock_productos", "descuento", "REAL")             # % de descuento propio del producto (vacío = el del rubro / marca)
    con.execute("CREATE TABLE IF NOT EXISTS stock_proveedores (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, telefono TEXT)")
    agregar("stock_productos", "proveedor_id", "INTEGER")
    con.execute("CREATE TABLE IF NOT EXISTS stock_barras (id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER NOT NULL, codigo TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_barras_prod ON stock_barras(producto_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_barras_cod ON stock_barras(codigo)")
    con.execute("INSERT INTO stock_barras (producto_id, codigo) SELECT id, codigo_barras FROM stock_productos WHERE IFNULL(codigo_barras,'')<>'' "
                "AND NOT EXISTS (SELECT 1 FROM stock_barras b WHERE b.producto_id=stock_productos.id)")      # el código único anterior pasa a la lista
    con.execute("""CREATE TABLE IF NOT EXISTS orden_repuestos (id INTEGER PRIMARY KEY AUTOINCREMENT, orden_id INTEGER NOT NULL, producto_id INTEGER,
                   nombre TEXT NOT NULL, cantidad REAL NOT NULL, precio REAL NOT NULL, abrev TEXT, fecha TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_orep_orden ON orden_repuestos(orden_id)")
    con.execute("""CREATE TABLE IF NOT EXISTS stock_faltantes (id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER, nombre TEXT NOT NULL,
                   cantidad REAL NOT NULL DEFAULT 1, nota TEXT, creado TEXT)""")
    agregar("stock_faltantes", "proveedor_id", "INTEGER")
    con.execute("CREATE TABLE IF NOT EXISTS stock_formas_pago (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, aplica_descuento INTEGER NOT NULL DEFAULT 0)")
    agregar("stock_formas_pago", "pide_cheque", "INTEGER NOT NULL DEFAULT 0")      # cheque / e-cheq: al cobrar se piden sus datos
    if con.execute("SELECT COUNT(*) FROM stock_formas_pago").fetchone()[0] == 0:
        con.executemany("INSERT INTO stock_formas_pago (nombre, aplica_descuento) VALUES (?,?)",
                        [("Efectivo", 1), ("Transferencia", 1), ("Tarjeta de débito", 0), ("Tarjeta de crédito", 0)])
    if con.execute("SELECT COUNT(*) FROM stock_unidades").fetchone()[0] == 0:
        con.executemany("INSERT INTO stock_unidades (nombre, abrev, decimales) VALUES (?,?,?)", UNIDADES_INICIALES)


# ------------------------------------------------------------------ formatos
def fmt_cant(valor, decimales=3):
    """1500 -> '1.500'; 2.5 -> '2,5' (hasta `decimales`, sin ceros de más)."""
    if valor is None:
        return ""
    v = round(float(valor), max(decimales, 0))
    texto = f"{v:,.{max(decimales, 0)}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if "," in texto:
        texto = texto.rstrip("0").rstrip(",")
    return texto


def cant_o_error(texto, decimales):
    """Cantidad escrita por el usuario -> float redondeado a los decimales de la unidad."""
    v = core.parse_monto(texto)           # acepta 1.500,5 / 1500.5; no admite negativos
    if v is None:
        raise ValueError("falta")
    return round(v, decimales)


def estado_stock(actual, minimo):
    if actual <= 0:
        return "sin"
    if minimo and actual <= minimo:
        return "bajo"
    return "ok"


# ------------------------------------------------------------------ rutas
def registrar(app, login_requerido, modulo_requerido, db, U, error):
    """Agrega las pantallas y la API de stock a la aplicación."""

    def etiquetas(cid):
        return {"categoria": core.leer_config(cid, "stock_lbl_categoria", "Rubro"),
                "marca": core.leer_config(cid, "stock_lbl_marca", "Marca")}

    def iva_cfg(cid):
        try:
            defecto = float(core.leer_config(cid, "stock_iva_defecto", "21") or 21)
        except ValueError:
            defecto = 21.0
        return {"activo": core.leer_config(cid, "stock_iva_activo", "0") == "1", "defecto": defecto}

    def pct(v):
        """Porcentaje opcional: vacío -> None."""
        if v is None or str(v).strip() == "":
            return None
        n = core.parse_monto(v)
        if n is None or n < 0 or n > 1000:
            raise ValueError
        return n

    def calcular(costo, cat, mar):
        """Precios (sin IVA) según las reglas de la marca (si tiene) o del rubro. El descuento no se aplica acá: depende de la forma de pago."""
        def regla(campo):
            if mar is not None and mar[campo] is not None:
                return mar[campo]
            return cat[campo] if cat is not None else None
        if costo is None:
            return None, None
        out = []
        for campo in ("margen_lista", "margen_contado"):
            m = regla(campo)
            out.append(None if m is None else round(costo * (1 + m / 100), 2))
        return out[0], out[1]

    def recalcular(con, where="", params=()):
        """Recalcula los productos con precio automático."""
        cats = {r["id"]: r for r in con.execute("SELECT * FROM stock_categorias")}
        mars = {r["id"]: r for r in con.execute("SELECT * FROM stock_marcas")}
        for p in con.execute("SELECT id, costo, categoria_id, marca_id FROM stock_productos WHERE precio_auto=1" + where, params).fetchall():
            lista, contado = calcular(p["costo"], cats.get(p["categoria_id"]), mars.get(p["marca_id"]))
            con.execute("UPDATE stock_productos SET precio=?, precio_contado=? WHERE id=?", (lista, contado, p["id"]))

    def pfin(valor, iva, cfg):
        """Precio final al público: suma el IVA si la cuenta lo usa."""
        if valor is None:
            return None
        if not cfg["activo"]:
            return valor
        return round(valor * (1 + (cfg["defecto"] if iva is None else iva) / 100), 2)

    def migrar(cid):
        """Una sola vez: los precios automáticos ya no llevan el descuento incorporado."""
        if core.leer_config(cid, "stock_calc_v2") != "1":
            with db() as con:
                recalcular(con)
            core.guardar_config(cid, "stock_calc_v2", "1")

    def formas(con):
        return con.execute("SELECT * FROM stock_formas_pago ORDER BY id").fetchall()

    def solo(nombre):
        return " ".join((nombre or "").split())

    def prov_activo():
        return core.leer_config(g.cuenta["id"], "stock_proveedores", "0") == "1"

    app.jinja_env.globals["prov_activo"] = prov_activo

    SQL_PROD = ("SELECT p.*, c.nombre AS categoria, IFNULL(p.descuento, IFNULL(m.descuento, c.descuento)) AS desc_pct, m.nombre AS marca, v.nombre AS proveedor, u.nombre AS unidad, u.abrev, u.decimales "
                "FROM stock_productos p LEFT JOIN stock_categorias c ON c.id=p.categoria_id "
                "LEFT JOIN stock_marcas m ON m.id=p.marca_id LEFT JOIN stock_proveedores v ON v.id=p.proveedor_id JOIN stock_unidades u ON u.id=p.unidad_id")

    # ---------------- listado
    # ---------------- faltantes: productos por debajo del stock mínimo (automático, configurable) + lista manual
    def faltantes_auto_activo():
        return core.leer_config(g.cuenta["id"], "stock_faltantes_auto", "1") == "1"

    def faltantes_automaticos(con):
        if not faltantes_auto_activo():
            return []
        return [dict(id=r["id"], nombre=r["nombre"], codigo=r["codigo"], proveedor=r["proveedor"], actual=r["stock_actual"], minimo=r["stock_minimo"], abrev=r["abrev"], decimales=r["decimales"],
                     faltan=round(r["stock_minimo"] - r["stock_actual"], 6))
                for r in con.execute(SQL_PROD + " WHERE p.activo=1 AND p.stock_minimo>0 AND p.stock_actual<=p.stock_minimo ORDER BY " + ("IFNULL(v.nombre,'zzz') COLLATE NOCASE, " if prov_activo() else "") + "(p.stock_minimo-p.stock_actual) DESC, p.nombre COLLATE NOCASE")]

    @app.route("/stock/faltantes")
    @login_requerido
    @modulo_requerido("stock")
    def stock_faltantes():
        with db() as con:
            auto = faltantes_automaticos(con)
            manual = [dict(r) for r in con.execute("SELECT f.*, u.abrev, u.decimales, v.nombre AS proveedor FROM stock_faltantes f LEFT JOIN stock_productos p ON p.id=f.producto_id "
                                                   "LEFT JOIN stock_unidades u ON u.id=p.unidad_id LEFT JOIN stock_proveedores v ON v.id=f.proveedor_id "
                                                   "ORDER BY " + ("IFNULL(v.nombre,'zzz') COLLATE NOCASE, " if prov_activo() else "") + "f.id DESC")]
            provs = [dict(r) for r in con.execute("SELECT id, nombre FROM stock_proveedores ORDER BY nombre COLLATE NOCASE")] if prov_activo() else []
        return render_template("stock/faltantes.html", provs=provs, auto=auto, manual=manual, auto_activo=faltantes_auto_activo(), fmt_cant=fmt_cant)

    @app.route("/api/stock/faltantes/buscar")
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_faltantes_buscar():
        sql, par = SQL_PROD + " WHERE p.activo=1", []
        for w in (request.args.get("q") or "").split()[:4]:
            sql += " AND (p.nombre LIKE ? OR IFNULL(p.codigo,'') LIKE ?)"
            par += [f"%{w}%"] * 2
        with db() as con:
            items = [dict(id=r["id"], nombre=r["nombre"], codigo=r["codigo"], stock=r["stock_actual"], abrev=r["abrev"], proveedor_id=r["proveedor_id"],
                          barras=[b[0] for b in con.execute("SELECT codigo FROM stock_barras WHERE producto_id=?", (r["id"],))]) for r in con.execute(sql + " ORDER BY p.nombre COLLATE NOCASE LIMIT " + ("20" if par else "1000"), par).fetchall()]
        return jsonify(ok=True, items=items)

    @app.route("/api/stock/faltantes", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_faltante_guardar():
        d = request.get_json(force=True, silent=True) or {}
        nombre = U(d.get("nombre"))
        try:
            cant = float(str(d.get("cantidad") or "").replace(".", "").replace(",", ".")) if d.get("cantidad") not in (None, "") else 1.0
        except ValueError:
            cant = 0
        if not nombre:
            return error("Escribí el producto que falta.")
        if cant <= 0:
            return error("La cantidad tiene que ser mayor a cero.")
        with db() as con:
            prov = d.get("proveedor_id") or None
            if prov and not con.execute("SELECT 1 FROM stock_proveedores WHERE id=?", (prov,)).fetchone():
                prov = None
            if d.get("id"):
                if not con.execute("SELECT 1 FROM stock_faltantes WHERE id=?", (d.get("id"),)).fetchone():
                    return error("No existe ese faltante.", 404)
                pid_e = d.get("producto_id") if d.get("producto_id") and con.execute("SELECT 1 FROM stock_productos WHERE id=?", (d.get("producto_id"),)).fetchone() else None
                con.execute("UPDATE stock_faltantes SET producto_id=?, nombre=?, cantidad=?, nota=?, proveedor_id=? WHERE id=?", (pid_e, nombre, cant, U(d.get("nota"))[:200] or None, prov, d.get("id")))
            else:
                pid = d.get("producto_id") if d.get("producto_id") and con.execute("SELECT 1 FROM stock_productos WHERE id=?", (d.get("producto_id"),)).fetchone() else None
                con.execute("INSERT INTO stock_faltantes (producto_id, nombre, cantidad, nota, creado, proveedor_id) VALUES (?,?,?,?,?,?)", (pid, nombre, cant, U(d.get("nota"))[:200] or None, core.ahora(), prov))
        return jsonify(ok=True)

    @app.route("/api/stock/faltantes/<int:fid>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_faltante_eliminar(fid):
        with db() as con:
            con.execute("DELETE FROM stock_faltantes WHERE id=?", (fid,))
        return jsonify(ok=True)

    # ---------------- repuestos y accesorios usados en una orden (descuentan stock y suman al total a cobrar)
    def _orden_abierta(con, oid):
        o = con.execute("SELECT id, estado FROM ordenes WHERE id=?", (oid,)).fetchone()
        if not o:
            return None, error("No existe la orden.", 404)
        if o["estado"] in ("Equipo retirado", "Scrap", "Equipo comprado"):
            return None, error("La orden ya está cerrada: no se pueden cambiar sus repuestos.")
        return o, None

    @app.route("/api/orden/<int:oid>/repuestos/buscar")
    @login_requerido
    @modulo_requerido("stock")
    def api_orden_rep_buscar(oid):
        q = (request.args.get("q") or "").strip()
        sql = SQL_PROD + " WHERE p.activo=1"
        par = []
        for w in q.split()[:4]:
            sql += " AND (p.nombre LIKE ? OR IFNULL(p.codigo,'') LIKE ? OR p.id IN (SELECT producto_id FROM stock_barras WHERE codigo LIKE ?))"
            par += [f"%{w}%"] * 3
        sql += " ORDER BY p.nombre COLLATE NOCASE LIMIT 30"
        with db() as con:
            items = [dict(id=r["id"], nombre=r["nombre"], codigo=r["codigo"], precio=r["precio"], stock=r["stock_actual"], abrev=r["abrev"], decimales=r["decimales"])
                     for r in con.execute(sql, par)]
        return jsonify(ok=True, items=items)

    @app.route("/api/orden/<int:oid>/repuestos", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_orden_rep_agregar(oid):
        d = request.get_json(force=True, silent=True) or {}
        with db() as con:
            o, err = _orden_abierta(con, oid)
            if err:
                return err
            p = con.execute(SQL_PROD + " WHERE p.id=? AND p.activo=1", (d.get("producto_id"),)).fetchone()
            if not p:
                return error("Elegí un repuesto o accesorio cargado en Stock.")
            try:
                cant = cant_o_error(d.get("cantidad"), p["decimales"])
            except ValueError:
                return error("Escribí una cantidad válida.")
            if cant <= 0:
                return error("La cantidad tiene que ser mayor a cero.")
            try:
                precio = core.parse_monto(d.get("precio")) if d.get("precio") not in (None, "") else p["precio"]
            except ValueError:
                return error("El precio no es válido.")
            if precio is None or precio < 0:
                return error("Este producto no tiene precio: escribí el precio unitario.")
            actual = round(p["stock_actual"], 6)
            if cant > actual + 1e-9:
                return error(f"Stock insuficiente: hay {fmt_cant(actual, p['decimales'])} {p['abrev']}.")
            nuevo = round(actual - cant, 6)
            ahora = core.ahora()
            con.execute("UPDATE stock_productos SET stock_actual=?, actualizado=? WHERE id=?", (nuevo, ahora, p["id"]))
            con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo, nota) VALUES (?,?,?,?,?,?,?)",
                        (p["id"], ahora, "egreso", -cant, nuevo, "Usado en orden N° %04d" % oid, None))
            con.execute("INSERT INTO orden_repuestos (orden_id, producto_id, nombre, cantidad, precio, abrev, fecha) VALUES (?,?,?,?,?,?,?)",
                        (oid, p["id"], p["nombre"], cant, precio, p["abrev"], ahora))
            core.registrar_evento(con, oid, o["estado"], f"Repuesto agregado: {fmt_cant(cant, p['decimales'])} {p['abrev']} {p['nombre']} · {core.fmt_monto(round(cant * precio, 2))}")
        return jsonify(ok=True)

    @app.route("/api/orden/<int:oid>/repuestos/<int:rid>/quitar", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_orden_rep_quitar(oid, rid):
        with db() as con:
            o, err = _orden_abierta(con, oid)
            if err:
                return err
            r = con.execute("SELECT * FROM orden_repuestos WHERE id=? AND orden_id=?", (rid, oid)).fetchone()
            if not r:
                return error("No existe ese repuesto en la orden.", 404)
            p = con.execute("SELECT id, stock_actual FROM stock_productos WHERE id=?", (r["producto_id"],)).fetchone()
            ahora = core.ahora()
            if p:       # el producto vuelve al stock
                nuevo = round(p["stock_actual"] + r["cantidad"], 6)
                con.execute("UPDATE stock_productos SET stock_actual=?, actualizado=? WHERE id=?", (nuevo, ahora, p["id"]))
                con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo, nota) VALUES (?,?,?,?,?,?,?)",
                            (p["id"], ahora, "ingreso", r["cantidad"], nuevo, "Quitado de la orden N° %04d" % oid, None))
            con.execute("DELETE FROM orden_repuestos WHERE id=?", (rid,))
            core.registrar_evento(con, oid, o["estado"], f"Repuesto quitado: {r['nombre']}")
        return jsonify(ok=True)

    @app.route("/stock")
    @login_requerido
    @modulo_requerido("stock")
    def stock():
        migrar(g.cuenta["id"])
        q = core.sin_acentos((request.args.get("q") or "").strip())
        cat, mar = request.args.get("cat", type=int), request.args.get("mar", type=int)
        prov = request.args.get("prov", type=int) if prov_activo() else None
        orden = request.args.get("orden") if prov_activo() and request.args.get("orden") == "proveedor" else "nombre"
        ver = request.args.get("ver", "activos")
        where, params = [], []
        if q:
            where.append("(" + " OR ".join(f"lower_u({c}) LIKE ?" for c in
                         ("p.nombre", "p.codigo", "p.descripcion", "p.ubicacion", "c.nombre", "m.nombre")) +
                         " OR p.id IN (SELECT producto_id FROM stock_barras WHERE lower_u(codigo) LIKE ?))")
            params += [f"%{q}%"] * 7
        if cat:
            where.append("p.categoria_id=?"); params.append(cat)
        if mar:
            where.append("p.marca_id=?"); params.append(mar)
        if prov:
            where.append("p.proveedor_id=?"); params.append(prov)
        if ver == "bajo":
            where.append("p.activo=1 AND p.stock_actual>0 AND p.stock_minimo>0 AND p.stock_actual<=p.stock_minimo")
        elif ver == "sin":
            where.append("p.activo=1 AND p.stock_actual<=0")
        elif ver == "inactivos":
            where.append("p.activo=0")
        elif ver == "todos":
            pass
        else:
            where.append("p.activo=1")
        sql = SQL_PROD + (" WHERE " + " AND ".join(where) if where else "") + (" ORDER BY IFNULL(v.nombre,'zzz') COLLATE NOCASE, p.nombre COLLATE NOCASE LIMIT 500" if orden == "proveedor" else " ORDER BY p.nombre COLLATE NOCASE LIMIT 500")
        with db() as con:
            filas = con.execute(sql, params).fetchall()
            cats = con.execute("SELECT * FROM stock_categorias ORDER BY nombre COLLATE NOCASE").fetchall()
            mars = con.execute("SELECT * FROM stock_marcas ORDER BY nombre COLLATE NOCASE").fetchall()
            provs = con.execute("SELECT * FROM stock_proveedores ORDER BY nombre COLLATE NOCASE").fetchall() if prov_activo() else []
            res = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN stock_actual<=0 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN stock_actual>0 AND stock_minimo>0 AND stock_actual<=stock_minimo THEN 1 ELSE 0 END), "
                "SUM(stock_actual*IFNULL(costo,0)), SUM(stock_actual*IFNULL(precio,0)) FROM stock_productos WHERE activo=1").fetchone()
            n_falt = len(faltantes_automaticos(con)) + con.execute("SELECT COUNT(*) FROM stock_faltantes").fetchone()[0]
        return render_template("stock/lista.html", n_falt=n_falt, provs=provs, prov=prov, orden=orden, filas=filas, cats=cats, mars=mars, q=request.args.get("q", ""), cat=cat, mar=mar,
                               ver=ver, res=res, lbl=etiquetas(g.cuenta["id"]), fmt_cant=fmt_cant, estado_stock=estado_stock,
                               iva=iva_cfg(g.cuenta["id"]), pfin=lambda v, i: pfin(v, i, iva_cfg(g.cuenta["id"])))

    # ---------------- ficha
    @app.route("/stock/producto/<int:pid>")
    @login_requerido
    @modulo_requerido("stock")
    def stock_producto(pid):
        migrar(g.cuenta["id"])
        with db() as con:
            p = con.execute(SQL_PROD + " WHERE p.id=?", (pid,)).fetchone()
            if not p:
                abort(404)
            movs = con.execute("SELECT * FROM stock_movimientos WHERE producto_id=? ORDER BY id DESC LIMIT 300", (pid,)).fetchall()
            cats = con.execute("SELECT * FROM stock_categorias ORDER BY nombre COLLATE NOCASE").fetchall()
            mars = con.execute("SELECT * FROM stock_marcas ORDER BY nombre COLLATE NOCASE").fetchall()
            unis = con.execute("SELECT * FROM stock_unidades ORDER BY nombre COLLATE NOCASE").fetchall()
            fpagos = formas(con)
            barras = [r[0] for r in con.execute("SELECT codigo FROM stock_barras WHERE producto_id=? ORDER BY id", (pid,))]
        return render_template("stock/producto.html", barras=barras, fpagos=fpagos, p=p, movs=movs, cats=cats, mars=mars, unis=unis, MOTIVOS=MOTIVOS,
                               iva=iva_cfg(g.cuenta["id"]), pfin=lambda v, i: pfin(v, i, iva_cfg(g.cuenta["id"])),
                               lbl=etiquetas(g.cuenta["id"]), fmt_cant=fmt_cant, estado_stock=estado_stock, fmt_fecha=core.fmt_fecha)

    @app.route("/api/stock/formulario")
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_formulario():
        """Datos para armar el formulario de producto (listas y, si se pide, el producto)."""
        pid = request.args.get("id", type=int)
        with db() as con:
            out = {"ok": True,
                   "categorias": [dict(r) for r in con.execute("SELECT * FROM stock_categorias ORDER BY nombre COLLATE NOCASE")],
                   "marcas": [dict(r) for r in con.execute("SELECT * FROM stock_marcas ORDER BY nombre COLLATE NOCASE")],
                   "unidades": [dict(r) for r in con.execute("SELECT id, nombre, abrev, decimales FROM stock_unidades ORDER BY nombre COLLATE NOCASE")],
                   "usa_proveedores": prov_activo(),
                   "proveedores": [dict(r) for r in con.execute("SELECT id, nombre FROM stock_proveedores ORDER BY nombre COLLATE NOCASE")] if prov_activo() else [],
                   "etiquetas": etiquetas(g.cuenta["id"]), "iva": iva_cfg(g.cuenta["id"])}
            if pid:
                p = con.execute("SELECT * FROM stock_productos WHERE id=?", (pid,)).fetchone()
                out["producto"] = dict(p) if p else None
                if p:
                    out["producto"]["barras"] = [r[0] for r in con.execute("SELECT codigo FROM stock_barras WHERE producto_id=? ORDER BY id", (pid,))]
        return jsonify(out)

    # ---------------- alta / edición de producto
    @app.route("/api/stock/producto", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_producto():
        d = request.get_json(force=True, silent=True) or {}
        pid = d.get("id")
        nombre = U(solo(d.get("nombre")))
        if not nombre:
            return error("Escribí el nombre del producto.")
        with db() as con:
            uni = con.execute("SELECT * FROM stock_unidades WHERE id=?", (d.get("unidad_id"),)).fetchone()
            if not uni:
                return error("Elegí la unidad de medida.")
            try:
                costo, precio = core.parse_monto(d.get("costo")), core.parse_monto(d.get("precio"))
                contado = core.parse_monto(d.get("precio_contado"))
                iva = pct(d.get("iva"))
                descuento = pct(d.get("descuento"))
                minimo = cant_o_error(d.get("stock_minimo") or "0", uni["decimales"])
                inicial = cant_o_error(d.get("stock_inicial") or "0", uni["decimales"]) if not pid else 0
            except ValueError:
                return error("Revisá los números (costo, precios, IVA, stock mínimo y stock inicial).")
            codigo = solo(d.get("codigo"))
            if codigo and con.execute("SELECT 1 FROM stock_productos WHERE lower(codigo)=lower(?) AND id<>?", (codigo, pid or 0)).fetchone():
                return error("Ya existe otro producto con ese código.")
            if codigo and con.execute("SELECT 1 FROM stock_barras WHERE lower(codigo)=lower(?) AND producto_id<>?", (codigo, pid or 0)).fetchone():
                return error("Ya existe otro producto con ese código.")
            crudo = d.get("codigos_barras") if isinstance(d.get("codigos_barras"), list) else re.split(r"[\n,;]+", str(d.get("codigo_barras") or ""))
            barras_lista = []
            for b in crudo:
                b = solo(b)
                if b and b.lower() not in [x.lower() for x in barras_lista]:
                    barras_lista.append(b)
            for b in barras_lista:
                if con.execute("SELECT 1 FROM stock_productos WHERE id<>? AND lower(codigo)=lower(?)", (pid or 0, b)).fetchone() or \
                   con.execute("SELECT 1 FROM stock_barras WHERE producto_id<>? AND lower(codigo)=lower(?)", (pid or 0, b)).fetchone():
                    return error(f"El código de barras {b} ya lo usa otro producto.")
            cat = d.get("categoria_id") or None
            mar = d.get("marca_id") or None
            ahora = core.ahora()
            auto = 1 if d.get("precio_auto") else 0
            if auto:
                c_row = con.execute("SELECT * FROM stock_categorias WHERE id=?", (cat,)).fetchone() if cat else None
                m_row = con.execute("SELECT * FROM stock_marcas WHERE id=?", (mar,)).fetchone() if mar else None
                precio, contado = calcular(costo, c_row, m_row)
            prov = d.get("proveedor_id") or None
            if prov and not con.execute("SELECT 1 FROM stock_proveedores WHERE id=?", (prov,)).fetchone():
                prov = None
            valores = [codigo or None, nombre, U(d.get("descripcion")) or None, cat, mar, uni["id"], costo, precio, minimo,
                       U(d.get("ubicacion")) or None, contado, iva, auto, descuento]
            if pid:
                p = con.execute("SELECT * FROM stock_productos WHERE id=?", (pid,)).fetchone()
                if not p:
                    return error("No existe el producto.", 404)
                con.execute("UPDATE stock_productos SET codigo=?, nombre=?, descripcion=?, categoria_id=?, marca_id=?, unidad_id=?, costo=?, "
                            "precio=?, stock_minimo=?, ubicacion=?, precio_contado=?, iva=?, precio_auto=?, descuento=?, proveedor_id=?, activo=?, actualizado=? WHERE id=?",
                            valores + [prov if "proveedor_id" in d else p["proveedor_id"], 0 if d.get("activo") is False else 1, ahora, pid])
            else:
                pid = con.execute("INSERT INTO stock_productos (codigo, nombre, descripcion, categoria_id, marca_id, unidad_id, costo, precio, "
                                  "stock_minimo, ubicacion, precio_contado, iva, precio_auto, descuento, proveedor_id, stock_actual, activo, creado, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                                  valores + [prov, inicial, ahora, ahora]).lastrowid
                if inicial:
                    con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo) VALUES (?,?,?,?,?,?)",
                                (pid, ahora, "ingreso", inicial, inicial, "Stock inicial"))
            if "codigo_barras" in d or "codigos_barras" in d:
                con.execute("DELETE FROM stock_barras WHERE producto_id=?", (pid,))
                con.executemany("INSERT INTO stock_barras (producto_id, codigo) VALUES (?,?)", [(pid, b) for b in barras_lista])
                con.execute("UPDATE stock_productos SET codigo_barras=? WHERE id=?", (barras_lista[0] if barras_lista else None, pid))
        return jsonify(ok=True, id=pid)

    @app.route("/api/stock/producto/<int:pid>/<accion>", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_producto_accion(pid, accion):
        with db() as con:
            p = con.execute("SELECT * FROM stock_productos WHERE id=?", (pid,)).fetchone()
            if not p:
                return error("No existe el producto.", 404)
            if accion == "eliminar":
                d = request.get_json(force=True, silent=True) or {}
                if not core.codigo_es_valido(g.cuenta["id"], d.get("codigo")):
                    return jsonify(ok=False, necesita_codigo=True, error="Código de validación incorrecto." if d.get("codigo") else "Eliminar un producto requiere el código de validación."), 403
                con.execute("DELETE FROM stock_movimientos WHERE producto_id=?", (pid,))
                con.execute("DELETE FROM stock_productos WHERE id=?", (pid,))
            elif accion in ("desactivar", "activar"):
                con.execute("UPDATE stock_productos SET activo=? WHERE id=?", (1 if accion == "activar" else 0, pid))
            else:
                return error("Acción desconocida.", 404)
        return jsonify(ok=True)

    # ---------------- movimientos
    @app.route("/api/stock/movimiento", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_movimiento():
        d = request.get_json(force=True, silent=True) or {}
        tipo = d.get("tipo")
        if tipo not in ("ingreso", "egreso", "ajuste"):
            return error("Tipo de movimiento inválido.")
        with db() as con:
            p = con.execute(SQL_PROD + " WHERE p.id=?", (d.get("producto_id"),)).fetchone()
            if not p:
                return error("No existe el producto.", 404)
            try:
                cant = cant_o_error(d.get("cantidad"), p["decimales"])
            except ValueError:
                return error("Escribí una cantidad válida.")
            actual = round(p["stock_actual"], 6)
            if tipo == "ingreso":
                if cant <= 0:
                    return error("La cantidad tiene que ser mayor a cero.")
                nuevo, delta = round(actual + cant, 6), cant
            elif tipo == "egreso":
                if cant <= 0:
                    return error("La cantidad tiene que ser mayor a cero.")
                if cant > actual + 1e-9:
                    return error(f"Stock insuficiente: hay {fmt_cant(actual, p['decimales'])} {p['abrev']}.")
                nuevo, delta = round(actual - cant, 6), -cant
            else:                                   # ajuste: la cantidad es el stock real contado
                nuevo, delta = cant, round(cant - actual, 6)
            motivo = solo(d.get("motivo")) or {"ingreso": "Ingreso", "egreso": "Egreso", "ajuste": "Ajuste de inventario"}[tipo]
            ahora = core.ahora()
            con.execute("UPDATE stock_productos SET stock_actual=?, actualizado=? WHERE id=?", (nuevo, ahora, p["id"]))
            con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo, nota) VALUES (?,?,?,?,?,?,?)",
                        (p["id"], ahora, tipo, delta, nuevo, motivo, U(d.get("nota")) or None))
        return jsonify(ok=True, stock=nuevo)

    # ---------------- listas editables (rubros, marcas, unidades)
    @app.route("/stock/listas")
    @login_requerido
    @modulo_requerido("stock")
    def stock_listas():
        with db() as con:
            def con_uso(tabla, col):
                return con.execute(f"SELECT t.*, (SELECT COUNT(*) FROM stock_productos p WHERE p.{col}=t.id) AS usos "
                                   f"FROM {tabla} t ORDER BY t.nombre COLLATE NOCASE").fetchall()
            cats, mars, unis = con_uso("stock_categorias", "categoria_id"), con_uso("stock_marcas", "marca_id"), con_uso("stock_unidades", "unidad_id")
            fpagos = formas(con)
            provs = con_uso("stock_proveedores", "proveedor_id") if prov_activo() else []
        return render_template("stock/listas.html", provs=provs, fpagos=fpagos, cats=cats, mars=mars, unis=unis, lbl=etiquetas(g.cuenta["id"]), iva=iva_cfg(g.cuenta["id"]))

    TABLAS = {"categorias": ("stock_categorias", "categoria_id"), "marcas": ("stock_marcas", "marca_id"), "unidades": ("stock_unidades", "unidad_id"),
              "formas_pago": ("stock_formas_pago", None), "proveedores": ("stock_proveedores", "proveedor_id")}

    @app.route("/api/stock/lista/<tabla>", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_lista(tabla):
        if tabla not in TABLAS:
            return error("Lista desconocida.", 404)
        nombre_tabla, _ = TABLAS[tabla]
        d = request.get_json(force=True, silent=True) or {}
        nombre = U(solo(d.get("nombre")))
        if not nombre:
            return error("Escribí el nombre.")
        with db() as con:
            rid = d.get("id")
            repetido = con.execute(f"SELECT 1 FROM {nombre_tabla} WHERE lower(nombre)=lower(?) AND id<>?", (nombre, rid or 0)).fetchone()
            if repetido:
                return error("Ya existe uno con ese nombre.")
            if tabla == "unidades":
                abrev = solo(d.get("abrev"))[:8]
                if not abrev:
                    return error("Escribí la abreviatura (por ejemplo kg, l, u).")
                try:
                    dec = min(max(int(d.get("decimales", 0)), 0), 3)
                except (TypeError, ValueError):
                    dec = 0
                if rid:
                    con.execute("UPDATE stock_unidades SET nombre=?, abrev=?, decimales=? WHERE id=?", (nombre, abrev, dec, rid))
                else:
                    rid = con.execute("INSERT INTO stock_unidades (nombre, abrev, decimales) VALUES (?,?,?)", (nombre, abrev, dec)).lastrowid
            elif tabla == "proveedores":
                tel = solo(d.get("telefono"))[:40] or None
                if rid:
                    con.execute("UPDATE stock_proveedores SET nombre=?, telefono=? WHERE id=?", (nombre, tel, rid))
                else:
                    rid = con.execute("INSERT INTO stock_proveedores (nombre, telefono) VALUES (?,?)", (nombre, tel)).lastrowid
            elif tabla == "formas_pago":
                aplica = 1 if d.get("aplica_descuento") else 0
                if rid:
                    con.execute("UPDATE stock_formas_pago SET nombre=?, aplica_descuento=? WHERE id=?", (nombre, aplica, rid))
                    if "pide_cheque" in d:
                        con.execute("UPDATE stock_formas_pago SET pide_cheque=? WHERE id=?", (1 if d.get("pide_cheque") else 0, rid))
                else:
                    rid = con.execute("INSERT INTO stock_formas_pago (nombre, aplica_descuento, pide_cheque) VALUES (?,?,?)",
                                      (nombre, aplica, 1 if d.get("pide_cheque") else 0)).lastrowid
            else:
                try:
                    reglas = [pct(d.get(k)) if k in d else None for k in ("margen_lista", "margen_contado", "descuento")]
                except ValueError:
                    return error("Los porcentajes tienen que ser números entre 0 y 1000.")
                if rid:
                    if "margen_lista" in d:
                        con.execute(f"UPDATE {nombre_tabla} SET nombre=?, margen_lista=?, margen_contado=?, descuento=? WHERE id=?", [nombre] + reglas + [rid])
                    else:
                        con.execute(f"UPDATE {nombre_tabla} SET nombre=? WHERE id=?", (nombre, rid))
                else:
                    rid = con.execute(f"INSERT INTO {nombre_tabla} (nombre, margen_lista, margen_contado, descuento) VALUES (?,?,?,?)",
                                      [nombre] + reglas).lastrowid
                recalcular(con)
        return jsonify(ok=True, id=rid, nombre=nombre)

    @app.route("/api/stock/lista/<tabla>/<int:rid>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_lista_eliminar(tabla, rid):
        if tabla not in TABLAS:
            return error("Lista desconocida.", 404)
        nombre_tabla, col = TABLAS[tabla]
        with db() as con:
            usos = con.execute(f"SELECT COUNT(*) FROM stock_productos WHERE {col}=?", (rid,)).fetchone()[0] if col else 0
            if usos:
                return error(f"Está en uso en {usos} producto(s). Cambialo en esos productos antes de eliminarlo.")
            con.execute(f"DELETE FROM {nombre_tabla} WHERE id=?", (rid,))
        return jsonify(ok=True)

    @app.route("/api/stock/iva", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_iva():
        d = request.get_json(force=True, silent=True) or {}
        try:
            n = pct(d.get("defecto"))
        except ValueError:
            return error("El IVA tiene que ser un número.")
        core.guardar_config(g.cuenta["id"], "stock_iva_activo", "1" if d.get("activo") else "0")
        core.guardar_config(g.cuenta["id"], "stock_iva_defecto", str(21 if n is None else n))
        return jsonify(ok=True)

    @app.route("/api/stock/etiquetas", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_etiquetas():
        d = request.get_json(force=True, silent=True) or {}
        cid = g.cuenta["id"]
        for clave, defecto in (("categoria", "Rubro"), ("marca", "Marca")):
            valor = solo(d.get(clave))[:25] or defecto
            core.guardar_config(cid, f"stock_lbl_{clave}", valor)
        return jsonify(ok=True)

    # ---------------- exportar
    @app.route("/stock/exportar.csv")
    @login_requerido
    @modulo_requerido("stock")
    def stock_exportar():
        lbl = etiquetas(g.cuenta["id"])
        with db() as con:
            filas = con.execute(SQL_PROD + " ORDER BY p.nombre COLLATE NOCASE").fetchall()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Código", "Producto", lbl["categoria"], lbl["marca"]] + (["Proveedor"] if prov_activo() else []) + ["Unidad", "Stock", "Stock mínimo", "Costo", "Precio lista", "Precio contado", "IVA %", "Precio lista final", "Precio contado final", "Descuento %", "Lista con descuento (final)", "Ubicación", "Activo"])
        cfg = iva_cfg(g.cuenta["id"])
        for r in filas:
            w.writerow([r["codigo"] or "", r["nombre"], r["categoria"] or "", r["marca"] or ""] + ([r["proveedor"] or ""] if prov_activo() else []) + [r["abrev"], fmt_cant(r["stock_actual"], r["decimales"]),
                        fmt_cant(r["stock_minimo"], r["decimales"]), core.num_ar(r["costo"]), core.num_ar(r["precio"]), core.num_ar(r["precio_contado"]),
                        core.num_ar(r["iva"] if r["iva"] is not None else cfg["defecto"]) if cfg["activo"] else "",
                        core.num_ar(pfin(r["precio"], r["iva"], cfg)), core.num_ar(pfin(r["precio_contado"], r["iva"], cfg)), core.num_ar(r["desc_pct"]) if r["desc_pct"] else "",
                        core.num_ar(pfin(r["precio"] * (1 - r["desc_pct"] / 100), r["iva"], cfg)) if r["desc_pct"] and r["precio"] is not None else "", r["ubicacion"] or "",
                        "Sí" if r["activo"] else "No"])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="stock.csv"'})
