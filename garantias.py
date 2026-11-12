"""Sección Garantías: productos y servicios asociados a clientes, con número de serie y tiempo de garantía."""
import calendar
import datetime

from flask import g, jsonify, render_template, request

import core

UNIDADES = {"dias": "días", "meses": "meses", "anios": "años"}


def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS garantias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER, cliente TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'producto',
            descripcion TEXT NOT NULL, serie TEXT,
            inicio TEXT NOT NULL, cantidad INTEGER NOT NULL DEFAULT 0, unidad TEXT NOT NULL DEFAULT 'meses',
            vence TEXT, nota TEXT, creado TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_garantias_cliente ON garantias(cliente);
    """)


def sumar(inicio, cantidad, unidad):
    """Fecha de vencimiento: inicio + cantidad de días, meses o años (el día se ajusta al fin de mes si hace falta)."""
    d = datetime.date.fromisoformat(inicio)
    if unidad == "dias":
        return (d + datetime.timedelta(days=cantidad)).isoformat()
    meses = cantidad * (12 if unidad == "anios" else 1)
    m = d.month - 1 + meses
    a, m = d.year + m // 12, m % 12 + 1
    return datetime.date(a, m, min(d.day, calendar.monthrange(a, m)[1])).isoformat()


def estado(vence, hoy):
    """(clave, días que faltan). Sin vencimiento = sin garantía."""
    if not vence:
        return "sin", None
    faltan = (datetime.date.fromisoformat(vence) - datetime.date.fromisoformat(hoy)).days
    if faltan < 0:
        return "vencida", faltan
    return ("porvencer" if faltan <= 30 else "vigente"), faltan


def registrar(app, login_requerido, modulo_requerido, db, U, error):

    def nombres_clientes(con):
        return sorted({core.nombre_natural(c) for c in con.execute("SELECT * FROM clientes")} | {r[0] for r in con.execute("SELECT DISTINCT cliente FROM garantias")}) if "clientes" in g.modulos \
            else sorted({r[0] for r in con.execute("SELECT DISTINCT cliente FROM garantias")})

    @app.route("/garantias")
    @login_requerido
    @modulo_requerido("garantias")
    def garantias():
        e = request.args.get("e") or "vigentes"
        q = " ".join((request.args.get("q") or "").split())
        hoy = core.ahora()[:10]
        sql, par = "SELECT * FROM garantias WHERE 1=1", []
        for w in q.split()[:4]:
            sql += " AND (cliente LIKE ? OR descripcion LIKE ? OR IFNULL(serie,'') LIKE ? OR IFNULL(nota,'') LIKE ?)"
            par += [f"%{w}%"] * 4
        sql += " ORDER BY cliente, vence"
        with db() as con:
            filas = []
            for r in con.execute(sql, par):
                d = dict(r)
                d["estado"], d["faltan"] = estado(d["vence"], hoy)
                filas.append(d)
            nombres = nombres_clientes(con)
            productos = [r[0] for r in con.execute("SELECT nombre FROM stock_productos WHERE activo=1 ORDER BY nombre")] if "stock" in g.modulos else []
        resumen = {k: sum(1 for f in filas if f["estado"] == k) for k in ("vigente", "porvencer", "vencida", "sin")}
        if e == "vigentes":
            filas = [f for f in filas if f["estado"] in ("vigente", "porvencer")]
        elif e == "porvencer":
            filas = [f for f in filas if f["estado"] == "porvencer"]
        elif e == "vencidas":
            filas = [f for f in filas if f["estado"] == "vencida"]
        grupos = []      # un bloque por cliente con todos sus productos y servicios
        for f in filas:
            if not grupos or grupos[-1]["cliente"] != f["cliente"]:
                grupos.append({"cliente": f["cliente"], "filas": []})
            grupos[-1]["filas"].append(f)
        return render_template("garantias.html", filas=filas, grupos=grupos, resumen=resumen, e=e, q=q, nombres=nombres, productos=productos, hoy=hoy,
                               UNIDADES=UNIDADES, fmt_fecha=core.fmt_fecha)

    def leer_item(it, inicio_comun):
        """Valida un producto/servicio: devuelve (fila, error)."""
        tipo = "servicio" if (it or {}).get("tipo") == "servicio" else "producto"
        desc = " ".join(U((it or {}).get("descripcion")).split())[:200]
        serie = None if tipo == "servicio" else (" ".join(U((it or {}).get("serie")).split())[:80] or None)
        if not desc:
            return None, "Escribí el producto o el servicio en todas las líneas."
        unidad = it.get("unidad") if it.get("unidad") in UNIDADES else "meses"
        try:
            cantidad = int(str(it.get("cantidad") or "0").strip() or 0)
        except ValueError:
            return None, "El tiempo de garantía tiene que ser un número."
        if cantidad < 0 or cantidad > 600:
            return None, "El tiempo de garantía no es válido."
        return (tipo, desc, serie, inicio_comun, cantidad, unidad, sumar(inicio_comun, cantidad, unidad) if cantidad else None), None

    @app.route("/api/garantias", methods=["POST"])
    @login_requerido
    @modulo_requerido("garantias")
    def api_garantias_guardar():
        d = request.get_json(force=True, silent=True) or {}
        gid = d.get("id")
        cliente = " ".join(U(d.get("cliente")).split())[:120]
        nota = U(d.get("nota"))[:300] or None
        if not cliente:
            return error("Escribí o elegí el cliente.")
        try:
            inicio = datetime.date.fromisoformat(str(d.get("inicio") or "")).isoformat()
        except ValueError:
            return error("Poné la fecha desde la que corre la garantía.")
        items = d.get("items") if isinstance(d.get("items"), list) and d.get("items") else [d]
        if gid:
            items = items[:1]
        filas = []
        for it in items[:40]:
            f, msg = leer_item(it if isinstance(it, dict) else {}, inicio)
            if msg:
                return error(msg)
            filas.append(f)
        with db() as con:
            cid = None
            if "clientes" in g.modulos:      # si el nombre es de un cliente cargado, queda asociado a su ficha
                for c in con.execute("SELECT * FROM clientes"):
                    if core.nombre_natural(c).upper() == cliente:
                        cid = c["id"]
                        break
            if gid:
                if not con.execute("SELECT 1 FROM garantias WHERE id=?", (gid,)).fetchone():
                    return error("No existe la garantía.", 404)
                t, desc, serie, ini, cant, uni, vence = filas[0]
                con.execute("UPDATE garantias SET cliente_id=?, cliente=?, tipo=?, descripcion=?, serie=?, inicio=?, cantidad=?, unidad=?, vence=?, nota=? WHERE id=?",
                            (cid, cliente, t, desc, serie, ini, cant, uni, vence, nota, gid))
            else:
                for t, desc, serie, ini, cant, uni, vence in filas:
                    gid = con.execute("INSERT INTO garantias (cliente_id, cliente, tipo, descripcion, serie, inicio, cantidad, unidad, vence, nota, creado) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                      (cid, cliente, t, desc, serie, ini, cant, uni, vence, nota, core.ahora())).lastrowid
        return jsonify(ok=True, id=gid, cantidad=len(filas))

    @app.route("/api/garantias/<int:gid>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("garantias")
    def api_garantias_eliminar(gid):
        d = request.get_json(force=True, silent=True) or {}
        if not core.codigo_es_valido(g.cuenta["id"], d.get("codigo")):
            return jsonify(ok=False, necesita_codigo=True, error="Código de validación incorrecto." if d.get("codigo") else "Eliminar una garantía requiere el código de validación."), 403
        with db() as con:
            con.execute("DELETE FROM garantias WHERE id=?", (gid,))
        return jsonify(ok=True)
