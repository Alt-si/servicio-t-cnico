"""Sección «Control de stock»: inventario genérico para cualquier comercio o rubro.

Todo es editable por el comercio: rubros (categorías), marcas, unidades de medida (unidad, kg, litro, metro...)
y los nombres de las etiquetas. Es código nuevo e independiente: no modifica las demás secciones.
"""
import csv
import io

from flask import Response, abort, g, jsonify, redirect, render_template, request, url_for

import core

TABLAS_STOCK = ("stock_categorias", "stock_marcas", "stock_unidades", "stock_productos", "stock_movimientos")

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

    def solo(nombre):
        return " ".join((nombre or "").split())

    SQL_PROD = ("SELECT p.*, c.nombre AS categoria, m.nombre AS marca, u.nombre AS unidad, u.abrev, u.decimales "
                "FROM stock_productos p LEFT JOIN stock_categorias c ON c.id=p.categoria_id "
                "LEFT JOIN stock_marcas m ON m.id=p.marca_id JOIN stock_unidades u ON u.id=p.unidad_id")

    # ---------------- listado
    @app.route("/stock")
    @login_requerido
    @modulo_requerido("stock")
    def stock():
        q = core.sin_acentos((request.args.get("q") or "").strip())
        cat, mar = request.args.get("cat", type=int), request.args.get("mar", type=int)
        ver = request.args.get("ver", "activos")
        where, params = [], []
        if q:
            where.append("(" + " OR ".join(f"lower_u({c}) LIKE ?" for c in
                         ("p.nombre", "p.codigo", "p.descripcion", "p.ubicacion", "c.nombre", "m.nombre")) + ")")
            params += [f"%{q}%"] * 6
        if cat:
            where.append("p.categoria_id=?"); params.append(cat)
        if mar:
            where.append("p.marca_id=?"); params.append(mar)
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
        sql = SQL_PROD + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY p.nombre COLLATE NOCASE LIMIT 500"
        with db() as con:
            filas = con.execute(sql, params).fetchall()
            cats = con.execute("SELECT * FROM stock_categorias ORDER BY nombre COLLATE NOCASE").fetchall()
            mars = con.execute("SELECT * FROM stock_marcas ORDER BY nombre COLLATE NOCASE").fetchall()
            res = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN stock_actual<=0 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN stock_actual>0 AND stock_minimo>0 AND stock_actual<=stock_minimo THEN 1 ELSE 0 END), "
                "SUM(stock_actual*IFNULL(costo,0)), SUM(stock_actual*IFNULL(precio,0)) FROM stock_productos WHERE activo=1").fetchone()
        return render_template("stock/lista.html", filas=filas, cats=cats, mars=mars, q=request.args.get("q", ""), cat=cat, mar=mar,
                               ver=ver, res=res, lbl=etiquetas(g.cuenta["id"]), fmt_cant=fmt_cant, estado_stock=estado_stock)

    # ---------------- ficha
    @app.route("/stock/producto/<int:pid>")
    @login_requerido
    @modulo_requerido("stock")
    def stock_producto(pid):
        with db() as con:
            p = con.execute(SQL_PROD + " WHERE p.id=?", (pid,)).fetchone()
            if not p:
                abort(404)
            movs = con.execute("SELECT * FROM stock_movimientos WHERE producto_id=? ORDER BY id DESC LIMIT 300", (pid,)).fetchall()
            cats = con.execute("SELECT * FROM stock_categorias ORDER BY nombre COLLATE NOCASE").fetchall()
            mars = con.execute("SELECT * FROM stock_marcas ORDER BY nombre COLLATE NOCASE").fetchall()
            unis = con.execute("SELECT * FROM stock_unidades ORDER BY nombre COLLATE NOCASE").fetchall()
        return render_template("stock/producto.html", p=p, movs=movs, cats=cats, mars=mars, unis=unis, MOTIVOS=MOTIVOS,
                               lbl=etiquetas(g.cuenta["id"]), fmt_cant=fmt_cant, estado_stock=estado_stock, fmt_fecha=core.fmt_fecha)

    @app.route("/api/stock/formulario")
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_formulario():
        """Datos para armar el formulario de producto (listas y, si se pide, el producto)."""
        pid = request.args.get("id", type=int)
        with db() as con:
            out = {"ok": True,
                   "categorias": [dict(r) for r in con.execute("SELECT id, nombre FROM stock_categorias ORDER BY nombre COLLATE NOCASE")],
                   "marcas": [dict(r) for r in con.execute("SELECT id, nombre FROM stock_marcas ORDER BY nombre COLLATE NOCASE")],
                   "unidades": [dict(r) for r in con.execute("SELECT id, nombre, abrev, decimales FROM stock_unidades ORDER BY nombre COLLATE NOCASE")],
                   "etiquetas": etiquetas(g.cuenta["id"])}
            if pid:
                p = con.execute("SELECT * FROM stock_productos WHERE id=?", (pid,)).fetchone()
                out["producto"] = dict(p) if p else None
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
                minimo = cant_o_error(d.get("stock_minimo") or "0", uni["decimales"])
                inicial = cant_o_error(d.get("stock_inicial") or "0", uni["decimales"]) if not pid else 0
            except ValueError:
                return error("Revisá los números (costo, precio, stock mínimo y stock inicial).")
            codigo = solo(d.get("codigo"))
            if codigo and con.execute("SELECT 1 FROM stock_productos WHERE lower(codigo)=lower(?) AND id<>?", (codigo, pid or 0)).fetchone():
                return error("Ya existe otro producto con ese código.")
            cat = d.get("categoria_id") or None
            mar = d.get("marca_id") or None
            ahora = core.ahora()
            valores = [codigo or None, nombre, U(d.get("descripcion")) or None, cat, mar, uni["id"], costo, precio, minimo,
                       U(d.get("ubicacion")) or None]
            if pid:
                p = con.execute("SELECT * FROM stock_productos WHERE id=?", (pid,)).fetchone()
                if not p:
                    return error("No existe el producto.", 404)
                con.execute("UPDATE stock_productos SET codigo=?, nombre=?, descripcion=?, categoria_id=?, marca_id=?, unidad_id=?, costo=?, "
                            "precio=?, stock_minimo=?, ubicacion=?, activo=?, actualizado=? WHERE id=?",
                            valores + [0 if d.get("activo") is False else 1, ahora, pid])
            else:
                pid = con.execute("INSERT INTO stock_productos (codigo, nombre, descripcion, categoria_id, marca_id, unidad_id, costo, precio, "
                                  "stock_minimo, ubicacion, stock_actual, activo, creado, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                                  valores + [inicial, ahora, ahora]).lastrowid
                if inicial:
                    con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo) VALUES (?,?,?,?,?,?)",
                                (pid, ahora, "ingreso", inicial, inicial, "Stock inicial"))
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
                if con.execute("SELECT COUNT(*) FROM stock_movimientos WHERE producto_id=?", (pid,)).fetchone()[0]:
                    return error("Este producto tiene movimientos de stock, por eso no se puede eliminar. Podés desactivarlo.")
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
        return render_template("stock/listas.html", cats=cats, mars=mars, unis=unis, lbl=etiquetas(g.cuenta["id"]))

    TABLAS = {"categorias": ("stock_categorias", "categoria_id"), "marcas": ("stock_marcas", "marca_id"), "unidades": ("stock_unidades", "unidad_id")}

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
            elif rid:
                con.execute(f"UPDATE {nombre_tabla} SET nombre=? WHERE id=?", (nombre, rid))
            else:
                rid = con.execute(f"INSERT INTO {nombre_tabla} (nombre) VALUES (?)", (nombre,)).lastrowid
        return jsonify(ok=True, id=rid, nombre=nombre)

    @app.route("/api/stock/lista/<tabla>/<int:rid>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("stock")
    def api_stock_lista_eliminar(tabla, rid):
        if tabla not in TABLAS:
            return error("Lista desconocida.", 404)
        nombre_tabla, col = TABLAS[tabla]
        with db() as con:
            usos = con.execute(f"SELECT COUNT(*) FROM stock_productos WHERE {col}=?", (rid,)).fetchone()[0]
            if usos:
                return error(f"Está en uso en {usos} producto(s). Cambialo en esos productos antes de eliminarlo.")
            con.execute(f"DELETE FROM {nombre_tabla} WHERE id=?", (rid,))
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
        w.writerow(["Código", "Producto", lbl["categoria"], lbl["marca"], "Unidad", "Stock", "Stock mínimo", "Costo", "Precio", "Ubicación", "Activo"])
        for r in filas:
            w.writerow([r["codigo"] or "", r["nombre"], r["categoria"] or "", r["marca"] or "", r["abrev"], fmt_cant(r["stock_actual"], r["decimales"]),
                        fmt_cant(r["stock_minimo"], r["decimales"]), core.num_ar(r["costo"]), core.num_ar(r["precio"]), r["ubicacion"] or "",
                        "Sí" if r["activo"] else "No"])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="stock.csv"'})
