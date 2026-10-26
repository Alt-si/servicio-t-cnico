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
    con.execute("CREATE TABLE IF NOT EXISTS stock_formas_pago (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, aplica_descuento INTEGER NOT NULL DEFAULT 0)")
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

    SQL_PROD = ("SELECT p.*, c.nombre AS categoria, IFNULL(m.descuento, c.descuento) AS desc_pct, m.nombre AS marca, u.nombre AS unidad, u.abrev, u.decimales "
                "FROM stock_productos p LEFT JOIN stock_categorias c ON c.id=p.categoria_id "
                "LEFT JOIN stock_marcas m ON m.id=p.marca_id JOIN stock_unidades u ON u.id=p.unidad_id")

    # ---------------- listado
    @app.route("/stock")
    @login_requerido
    @modulo_requerido("stock")
    def stock():
        migrar(g.cuenta["id"])
        q = core.sin_acentos((request.args.get("q") or "").strip())
        cat, mar = request.args.get("cat", type=int), request.args.get("mar", type=int)
        ver = request.args.get("ver", "activos")
        where, params = [], []
        if q:
            where.append("(" + " OR ".join(f"lower_u({c}) LIKE ?" for c in
                         ("p.nombre", "p.codigo", "p.codigo_barras", "p.descripcion", "p.ubicacion", "c.nombre", "m.nombre")) + ")")
            params += [f"%{q}%"] * 7
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
        return render_template("stock/producto.html", fpagos=fpagos, p=p, movs=movs, cats=cats, mars=mars, unis=unis, MOTIVOS=MOTIVOS,
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
                   "etiquetas": etiquetas(g.cuenta["id"]), "iva": iva_cfg(g.cuenta["id"])}
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
                contado = core.parse_monto(d.get("precio_contado"))
                iva = pct(d.get("iva"))
                minimo = cant_o_error(d.get("stock_minimo") or "0", uni["decimales"])
                inicial = cant_o_error(d.get("stock_inicial") or "0", uni["decimales"]) if not pid else 0
            except ValueError:
                return error("Revisá los números (costo, precios, IVA, stock mínimo y stock inicial).")
            codigo = solo(d.get("codigo"))
            if codigo and con.execute("SELECT 1 FROM stock_productos WHERE (lower(codigo)=lower(?) OR lower(codigo_barras)=lower(?)) AND id<>?", (codigo, codigo, pid or 0)).fetchone():
                return error("Ya existe otro producto con ese código.")
            barras = solo(d.get("codigo_barras"))
            if barras and con.execute("SELECT 1 FROM stock_productos WHERE id<>? AND (lower(codigo)=lower(?) OR lower(codigo_barras)=lower(?))",
                                      (pid or 0, barras, barras)).fetchone():
                return error("Ese código de barras ya lo usa otro producto.")
            cat = d.get("categoria_id") or None
            mar = d.get("marca_id") or None
            ahora = core.ahora()
            auto = 1 if d.get("precio_auto") else 0
            if auto:
                c_row = con.execute("SELECT * FROM stock_categorias WHERE id=?", (cat,)).fetchone() if cat else None
                m_row = con.execute("SELECT * FROM stock_marcas WHERE id=?", (mar,)).fetchone() if mar else None
                precio, contado = calcular(costo, c_row, m_row)
            valores = [codigo or None, nombre, U(d.get("descripcion")) or None, cat, mar, uni["id"], costo, precio, minimo,
                       U(d.get("ubicacion")) or None, contado, iva, auto]
            if pid:
                p = con.execute("SELECT * FROM stock_productos WHERE id=?", (pid,)).fetchone()
                if not p:
                    return error("No existe el producto.", 404)
                con.execute("UPDATE stock_productos SET codigo=?, nombre=?, descripcion=?, categoria_id=?, marca_id=?, unidad_id=?, costo=?, "
                            "precio=?, stock_minimo=?, ubicacion=?, precio_contado=?, iva=?, precio_auto=?, activo=?, actualizado=? WHERE id=?",
                            valores + [0 if d.get("activo") is False else 1, ahora, pid])
            else:
                pid = con.execute("INSERT INTO stock_productos (codigo, nombre, descripcion, categoria_id, marca_id, unidad_id, costo, precio, "
                                  "stock_minimo, ubicacion, precio_contado, iva, precio_auto, stock_actual, activo, creado, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                                  valores + [inicial, ahora, ahora]).lastrowid
                if inicial:
                    con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo) VALUES (?,?,?,?,?,?)",
                                (pid, ahora, "ingreso", inicial, inicial, "Stock inicial"))
            con.execute("UPDATE stock_productos SET codigo_barras=? WHERE id=?", (barras or None, pid))
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
            fpagos = formas(con)
        return render_template("stock/listas.html", fpagos=fpagos, cats=cats, mars=mars, unis=unis, lbl=etiquetas(g.cuenta["id"]), iva=iva_cfg(g.cuenta["id"]))

    TABLAS = {"categorias": ("stock_categorias", "categoria_id"), "marcas": ("stock_marcas", "marca_id"), "unidades": ("stock_unidades", "unidad_id"),
              "formas_pago": ("stock_formas_pago", None)}

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
            elif tabla == "formas_pago":
                aplica = 1 if d.get("aplica_descuento") else 0
                if rid:
                    con.execute("UPDATE stock_formas_pago SET nombre=?, aplica_descuento=? WHERE id=?", (nombre, aplica, rid))
                else:
                    rid = con.execute("INSERT INTO stock_formas_pago (nombre, aplica_descuento) VALUES (?,?)", (nombre, aplica)).lastrowid
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
        w.writerow(["Código", "Producto", lbl["categoria"], lbl["marca"], "Unidad", "Stock", "Stock mínimo", "Costo", "Precio lista", "Precio contado", "IVA %", "Precio lista final", "Precio contado final", "Descuento %", "Lista con descuento (final)", "Ubicación", "Activo"])
        cfg = iva_cfg(g.cuenta["id"])
        for r in filas:
            w.writerow([r["codigo"] or "", r["nombre"], r["categoria"] or "", r["marca"] or "", r["abrev"], fmt_cant(r["stock_actual"], r["decimales"]),
                        fmt_cant(r["stock_minimo"], r["decimales"]), core.num_ar(r["costo"]), core.num_ar(r["precio"]), core.num_ar(r["precio_contado"]),
                        core.num_ar(r["iva"] if r["iva"] is not None else cfg["defecto"]) if cfg["activo"] else "",
                        core.num_ar(pfin(r["precio"], r["iva"], cfg)), core.num_ar(pfin(r["precio_contado"], r["iva"], cfg)), core.num_ar(r["desc_pct"]) if r["desc_pct"] else "",
                        core.num_ar(pfin(r["precio"] * (1 - r["desc_pct"] / 100), r["iva"], cfg)) if r["desc_pct"] and r["precio"] is not None else "", r["ubicacion"] or "",
                        "Sí" if r["activo"] else "No"])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="stock.csv"'})
