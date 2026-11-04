"""Sección Cuenta corriente: lo que los clientes te deben (a cobrar) y lo que vos le debés a proveedores (a pagar)."""
import csv
import datetime
import io
import re

from flask import Response, g, jsonify, render_template, request

import core
import ventas
from stock import cant_o_error, fmt_cant

TIPOS = {"cliente": ("Clientes", "A cobrar", "Cobrado", "Cobrado", "Cliente"), "proveedor": ("Proveedores", "A pagar", "Pagado", "Pagado", "Proveedor")}


def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS cc_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, persona TEXT NOT NULL, emision TEXT NOT NULL, vence TEXT NOT NULL,
            numero TEXT, total REAL NOT NULL, obs TEXT, creado TEXT);
        CREATE TABLE IF NOT EXISTS cc_pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, fecha TEXT NOT NULL, monto REAL NOT NULL, nota TEXT);
        CREATE TABLE IF NOT EXISTS cc_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, producto_id INTEGER, nombre TEXT NOT NULL,
            cantidad REAL NOT NULL, precio REAL NOT NULL, abrev TEXT);
        CREATE TABLE IF NOT EXISTS cc_entregas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, persona TEXT NOT NULL, fecha TEXT NOT NULL,
            monto REAL NOT NULL, restante REAL NOT NULL, nota TEXT, creado TEXT);
        CREATE INDEX IF NOT EXISTS ix_cc_items_doc ON cc_items(doc_id);
        CREATE INDEX IF NOT EXISTS ix_cc_docs_tipo ON cc_docs(tipo);
        CREATE INDEX IF NOT EXISTS ix_cc_pagos_doc ON cc_pagos(doc_id);
    """)
    _migrar(con)


def _migrar(con):
    if "entrega_id" not in [r[1] for r in con.execute("PRAGMA table_info(cc_pagos)")]:
        con.execute("ALTER TABLE cc_pagos ADD COLUMN entrega_id INTEGER")      # pago que salió de una entrega de dinero a cuenta
    if "sin_factura" not in [r[1] for r in con.execute("PRAGMA table_info(cc_docs)")]:
        con.execute("ALTER TABLE cc_docs ADD COLUMN sin_factura INTEGER NOT NULL DEFAULT 0")      # se cobra sin factura (puede pedirla después)


SQL_DOCS = ("SELECT d.*, IFNULL((SELECT SUM(monto) FROM cc_pagos WHERE doc_id=d.id), 0) AS pagado FROM cc_docs d")


def _estado(d, hoy):
    if not (d["total"] or 0) > 0 or not (d["numero"] or d["sin_factura"]):
        return "sinfacturar", 0         # todavía sin número de factura ni importe
    saldo = round(d["total"] - d["pagado"], 2)
    if saldo <= 0.004:
        return "pagado", 0
    if d["vence"] < hoy:
        return "vencido", (datetime.date.fromisoformat(hoy) - datetime.date.fromisoformat(d["vence"])).days
    return "pendiente", 0


def _fecha_ok(txt):
    txt = (txt or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", txt):
        return None
    try:
        datetime.date.fromisoformat(txt)
    except ValueError:
        return None
    return txt


def _quitar_cobros(con, donde, par):
    """Si el pago ya había entrado al Historial de cobros, sale también (y su cheque)."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='cobros'").fetchone():
        return
    ids = [r[0] for r in con.execute(f"SELECT id FROM cobros WHERE {donde}", par)]
    for i in ids:
        con.execute("DELETE FROM cheques WHERE cobro_id=?", (i,))
        con.execute("DELETE FROM cobros WHERE id=?", (i,))


def aplicar_creditos(con, tipo, persona):
    """Las entregas de dinero con saldo a favor se descuentan de las facturas con deuda de esa persona, de la más vieja a la más nueva.
    Devuelve cuánto se aplicó."""
    aplicado = 0.0
    entregas = con.execute("SELECT * FROM cc_entregas WHERE tipo=? AND lower(persona)=lower(?) AND restante>0.004 ORDER BY fecha, id", (tipo, persona)).fetchall()
    for e in entregas:
        resto = e["restante"]
        docs = con.execute(SQL_DOCS + " WHERE d.tipo=? AND lower(d.persona)=lower(?) AND d.total>0 AND (IFNULL(d.numero,'')<>'' OR d.sin_factura=1) ORDER BY d.emision, d.id", (tipo, persona)).fetchall()
        for d in docs:
            if resto <= 0.004:
                break
            saldo = round(d["total"] - d["pagado"], 2)
            if saldo <= 0.004:
                continue
            m = round(min(saldo, resto), 2)
            con.execute("INSERT INTO cc_pagos (doc_id, fecha, monto, nota, entrega_id) VALUES (?,?,?,?,?)", (d["id"], e["fecha"], m, "Entrega de dinero", e["id"]))
            resto = round(resto - m, 2)
            aplicado += m
        con.execute("UPDATE cc_entregas SET restante=? WHERE id=?", (max(resto, 0), e["id"]))
    return aplicado


def _liberar_entregas(con, filtro, par):
    """Los pagos que salieron de una entrega vuelven a ser saldo a favor de esa entrega."""
    for p in con.execute(f"SELECT id, monto, entrega_id FROM cc_pagos WHERE entrega_id IS NOT NULL AND {filtro}", par).fetchall():
        con.execute("UPDATE cc_entregas SET restante=restante+? WHERE id=?", (p["monto"], p["entrega_id"]))


def registrar(app, login_requerido, modulo_requerido, db, U, error):

    def enriquecer(filas, hoy):
        out = []
        for r in filas:
            d = dict(r)
            d["estado"], d["atraso"] = _estado(d, hoy)
            d["saldo"] = 0 if d["estado"] == "sinfacturar" else max(round(d["total"] - d["pagado"], 2), 0)
            out.append(d)
        return out

    def listar(con, tipo, estado, q):
        hoy = core.ahora()[:10]
        sql, par = SQL_DOCS + " WHERE d.tipo=?", [tipo]
        for w in (q or "").split()[:4]:
            sql += " AND (d.persona LIKE ? OR IFNULL(d.numero,'') LIKE ? OR IFNULL(d.obs,'') LIKE ?)"
            par += [f"%{w}%"] * 3
        sql += " ORDER BY d.vence, d.id"
        filas = enriquecer(con.execute(sql, par).fetchall(), hoy)
        if estado in ("pendiente", "vencido", "pagado", "sinfacturar"):
            filas = [f for f in filas if f["estado"] == estado]
        elif estado == "deuda":
            filas = [f for f in filas if f["estado"] != "pagado"]
        return filas

    def sin_codigo(d, motivo):
        if core.codigo_es_valido(g.cuenta["id"], d.get("codigo")):
            return None
        return jsonify(ok=False, necesita_codigo=True, error="Código de validación incorrecto." if d.get("codigo") else motivo), 403

    @app.route("/cuenta-corriente")
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def cuenta_corriente():
        tipo = request.args.get("t") if request.args.get("t") in TIPOS else "cliente"
        estado = request.args.get("e") if request.args.get("e") in ("deuda", "pendiente", "vencido", "pagado", "todos") else "deuda"
        q = (request.args.get("q") or "").strip()
        vista = request.args.get("v") if request.args.get("v") in ("personas", "entregas") else "docs"
        with db() as con:
            todos = enriquecer(con.execute(SQL_DOCS + " WHERE d.tipo=?", (tipo,)).fetchall(), core.ahora()[:10])
            filas = listar(con, tipo, estado, q)
            nombres = sorted({r[0] for r in con.execute("SELECT persona FROM cc_docs WHERE tipo=?", (tipo,))} |
                             ({core.nombre_natural(c) for c in con.execute("SELECT * FROM clientes")} if tipo == "cliente" else set()))
        resumen = dict(cantidad=len(todos), sinfacturar=sum(1 for f in todos if f["estado"] == "sinfacturar"), total=sum(f["total"] for f in todos if f["estado"] != "sinfacturar"), pagado=sum(f["pagado"] for f in todos if f["estado"] != "sinfacturar"),
                       pendiente=sum(f["saldo"] for f in todos if f["estado"] == "pendiente"), vencido=sum(f["saldo"] for f in todos if f["estado"] == "vencido"))
        personas = {}
        for f in filas if q else todos:
            p = personas.setdefault(f["persona"].strip().lower(), dict(nombre=f["persona"], docs=0, saldo=0, vencido=0))
            p["docs"] += 1
            p["saldo"] += f["saldo"]
            if f["estado"] == "vencido":
                p["vencido"] += f["saldo"]
        with db() as con:
            entregas = [dict(r) for r in con.execute("SELECT * FROM cc_entregas WHERE tipo=? ORDER BY fecha DESC, id DESC", (tipo,))]
        for e in entregas:
            if not q:
                p = personas.setdefault(e["persona"].strip().lower(), dict(nombre=e["persona"], docs=0, saldo=0, vencido=0))
            else:
                p = personas.get(e["persona"].strip().lower())
            if p is not None:
                p["a_favor"] = round(p.get("a_favor", 0) + e["restante"], 2)
        personas = sorted([p for p in personas.values() if p["saldo"] > 0 or p.get("a_favor", 0) > 0], key=lambda p: -p["saldo"])
        return render_template("cuentacorriente.html", tipo=tipo, T=TIPOS[tipo], estado=estado, q=q, vista=vista, filas=filas, resumen=resumen,
                               personas=personas, entregas=entregas, con_ventas="ventas" in g.modulos, nombres=nombres, con_stock="stock" in g.modulos, hoy=core.ahora()[:10], fmt_fecha=core.fmt_fecha)

    @app.route("/cuenta-corriente/exportar.csv")
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def cuenta_corriente_csv():
        tipo = request.args.get("t") if request.args.get("t") in TIPOS else "cliente"
        with db() as con:
            filas = listar(con, tipo, "todos", "")
        sal = io.StringIO()
        w = csv.writer(sal, delimiter=";")
        w.writerow(["Estado", "Emisión", "Vencimiento", TIPOS[tipo][4], "N° de factura", "Total", "Pagado", "Saldo", "Atraso (días)", "Observaciones"])
        for f in filas:
            w.writerow([{"pagado": TIPOS[tipo][2], "vencido": "Vencido", "pendiente": TIPOS[tipo][1], "sinfacturar": "Sin facturar"}[f["estado"]], f["emision"], f["vence"], f["persona"], f["numero"] or ("SIN FACTURA" if f["sin_factura"] else ""),
                        str(f["total"]).replace(".", ","), str(f["pagado"]).replace(".", ","), str(f["saldo"]).replace(".", ","), f["atraso"], f["obs"] or ""])
        return Response("﻿" + sal.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=cuenta_corriente_{tipo}.csv"})

    def items_de(con, did):
        return [dict(r) for r in con.execute("SELECT * FROM cc_items WHERE doc_id=? ORDER BY id", (did,))]

    @app.route("/api/cc/doc/<int:did>")
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_doc(did):
        with db() as con:
            r = con.execute(SQL_DOCS + " WHERE d.id=?", (did,)).fetchone()
            if not r:
                return error("No existe el registro.", 404)
            pagos = [dict(p) for p in con.execute("SELECT * FROM cc_pagos WHERE doc_id=? ORDER BY fecha, id", (did,))]
            items = items_de(con, did)
            formas = []
            if "ventas" in g.modulos and r["tipo"] == "cliente":      # los cobros de clientes también van al Historial de cobros
                formas = [dict(id=f["id"], nombre=f["nombre"], cheque=bool(f["pide_cheque"])) for f in con.execute("SELECT id, nombre, pide_cheque FROM stock_formas_pago ORDER BY id")]
        d = enriquecer([r], core.ahora()[:10])[0]
        return jsonify(ok=True, doc=d, pagos=pagos, items=items, formas=formas)

    @app.route("/api/cc/productos")
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_productos():
        if "stock" not in g.modulos:
            return jsonify(ok=True, items=[])
        sql = "SELECT p.*, u.abrev, u.decimales FROM stock_productos p JOIN stock_unidades u ON u.id=p.unidad_id WHERE p.activo=1"
        par = []
        for w in (request.args.get("q") or "").split()[:4]:
            sql += " AND (p.nombre LIKE ? OR IFNULL(p.codigo,'') LIKE ? OR IFNULL(p.codigo_barras,'') LIKE ?)"
            par += [f"%{w}%"] * 3
        with db() as con:
            items = [dict(id=r["id"], nombre=r["nombre"], codigo=r["codigo"], precio=r["precio"], stock=r["stock_actual"], abrev=r["abrev"], decimales=r["decimales"])
                     for r in con.execute(sql + " ORDER BY p.nombre COLLATE NOCASE LIMIT 30", par)]
        return jsonify(ok=True, items=items)

    def aplicar_items(con, did, items, tabla_prev):
        """Deja los productos del registro como en `items` (devuelve al stock los anteriores y descuenta los nuevos). Devuelve un error o None."""
        ahora = core.ahora()
        prev = {}
        for r in tabla_prev:
            if r["producto_id"]:
                prev[r["producto_id"]] = prev.get(r["producto_id"], 0) + r["cantidad"]
        nuevos, pedido = [], {}
        for it in items:
            p = con.execute("SELECT p.*, u.abrev, u.decimales FROM stock_productos p JOIN stock_unidades u ON u.id=p.unidad_id WHERE p.id=? AND p.activo=1", (it.get("producto_id"),)).fetchone()
            if not p:
                return "Uno de los productos ya no existe en Stock."
            try:
                cant = cant_o_error(it.get("cantidad"), p["decimales"])
                precio = core.parse_monto(it.get("precio")) if it.get("precio") not in (None, "") else p["precio"]
            except ValueError:
                return f"Revisá la cantidad o el precio de {p['nombre']}."
            if cant <= 0 or precio is None or precio < 0:
                return f"Revisá la cantidad o el precio de {p['nombre']}."
            pedido[p["id"]] = pedido.get(p["id"], 0) + cant
            nuevos.append((p, cant, precio))
        for pid_, cant in pedido.items():
            p = con.execute("SELECT nombre, stock_actual FROM stock_productos WHERE id=?", (pid_,)).fetchone()
            if cant > round(p["stock_actual"] + prev.get(pid_, 0), 6) + 1e-9:
                return f"Stock insuficiente de {p['nombre']}: hay {fmt_cant(p['stock_actual'] + prev.get(pid_, 0))}."
        for pid_, cant in prev.items():        # se devuelve lo anterior...
            p = con.execute("SELECT stock_actual FROM stock_productos WHERE id=?", (pid_,)).fetchone()
            if p:
                nuevo = round(p["stock_actual"] + cant, 6)
                con.execute("UPDATE stock_productos SET stock_actual=?, actualizado=? WHERE id=?", (nuevo, ahora, pid_))
                con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo) VALUES (?,?,?,?,?,?)",
                            (pid_, ahora, "ingreso", cant, nuevo, "Cuenta corriente: producto quitado"))
        con.execute("DELETE FROM cc_items WHERE doc_id=?", (did,))
        for p, cant, precio in nuevos:         # ...y se descuenta lo nuevo
            act = con.execute("SELECT stock_actual FROM stock_productos WHERE id=?", (p["id"],)).fetchone()["stock_actual"]
            nuevo = round(act - cant, 6)
            con.execute("UPDATE stock_productos SET stock_actual=?, actualizado=? WHERE id=?", (nuevo, ahora, p["id"]))
            con.execute("INSERT INTO stock_movimientos (producto_id, fecha, tipo, cantidad, stock_resultante, motivo) VALUES (?,?,?,?,?,?)",
                        (p["id"], ahora, "egreso", -cant, nuevo, "Cuenta corriente: %s" % con.execute("SELECT persona FROM cc_docs WHERE id=?", (did,)).fetchone()["persona"]))
            con.execute("INSERT INTO cc_items (doc_id, producto_id, nombre, cantidad, precio, abrev) VALUES (?,?,?,?,?,?)", (did, p["id"], p["nombre"], cant, precio, p["abrev"]))
        return None

    @app.route("/api/cc/doc", methods=["POST"])
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_doc_guardar():
        d = request.get_json(force=True, silent=True) or {}
        tipo = d.get("tipo")
        persona = " ".join(U(d.get("persona")).split())
        emision = _fecha_ok(d.get("emision"))
        sin_factura = 1 if d.get("sin_factura") else 0
        numero = None if sin_factura else (" ".join(str(d.get("numero") or "").split())[:40] or None)
        obs = U(d.get("obs"))[:500] or None
        if tipo not in TIPOS:
            return error("Tipo inválido.")
        if not persona:
            return error(f"Escribí el nombre del {TIPOS[tipo][4].lower()}.")
        if not emision:
            return error("Completá la fecha.")
        total, vence = 0.0, ""
        if numero or sin_factura:      # con número de factura (o tildado «sin factura»), el importe y el vencimiento son obligatorios
            try:
                total = core.parse_monto(d.get("total"))
            except ValueError:
                total = None
            if not total or total <= 0:
                return error("Con número de factura (o «sin factura»), el importe total es obligatorio.")
            vence = _fecha_ok(d.get("vence"))
            if not vence:
                return error("Completá el vencimiento.")
            if vence < emision:
                return error("El vencimiento no puede ser anterior a la emisión.")
        elif not obs and not d.get("items"):
            return error("Anotá en observaciones qué retiró o qué servicio se le hizo, o cargá el número de factura.")
        items = d.get("items") if "stock" in g.modulos and isinstance(d.get("items"), list) else None
        with db() as con:
            if d.get("id"):
                r = con.execute(SQL_DOCS + " WHERE d.id=?", (d.get("id"),)).fetchone()
                if not r:
                    return error("No existe el registro.", 404)
                if (numero or sin_factura) and total + 0.004 < r["pagado"]:
                    return error("El total no puede ser menor a lo ya pagado.")
                if not numero and not sin_factura and r["pagado"] > 0:
                    return error("Ya tiene pagos registrados: no se puede quitar el número de factura.")
                did = r["id"]
                con.execute("UPDATE cc_docs SET persona=?, emision=?, vence=?, numero=?, total=?, obs=?, sin_factura=? WHERE id=?", (persona, emision, vence, numero, total, obs, sin_factura, did))
                if items is not None:
                    e = aplicar_items(con, did, items, items_de(con, did))
                    if e:
                        con.rollback()
                        return error(e)
                ap = aplicar_creditos(con, tipo, persona) if (numero or sin_factura) else 0
                return jsonify(ok=True, id=did, aplicado=ap)
            m = 0
            if (numero or sin_factura) and d.get("pagado_inicial") not in (None, ""):
                try:
                    m = core.parse_monto(d.get("pagado_inicial")) or 0
                except ValueError:
                    return error("El importe ya cobrado/pagado no es válido.")
                if m > total + 0.004:
                    return error("Lo ya cobrado/pagado no puede superar el total.")
            did = con.execute("INSERT INTO cc_docs (tipo, persona, emision, vence, numero, total, obs, creado, sin_factura) VALUES (?,?,?,?,?,?,?,?,?)",
                              (tipo, persona, emision, vence, numero, total, obs, core.ahora(), sin_factura)).lastrowid
            if items:
                e = aplicar_items(con, did, items, [])
                if e:
                    con.rollback()
                    return error(e)
            if m > 0:
                con.execute("INSERT INTO cc_pagos (doc_id, fecha, monto, nota) VALUES (?,?,?,?)", (did, emision, m, "Entrega inicial"))
            ap = aplicar_creditos(con, tipo, persona) if (numero or sin_factura) else 0
        return jsonify(ok=True, id=did, aplicado=ap)

    def saldo_persona(con, tipo, persona):
        docs = enriquecer(con.execute(SQL_DOCS + " WHERE d.tipo=? AND lower(d.persona)=lower(?)", (tipo, persona)).fetchall(), core.ahora()[:10])
        a_favor = con.execute("SELECT IFNULL(SUM(restante),0) FROM cc_entregas WHERE tipo=? AND lower(persona)=lower(?)", (tipo, persona)).fetchone()[0]
        return round(sum(d["saldo"] for d in docs), 2), round(a_favor, 2), sum(1 for d in docs if d["estado"] == "sinfacturar")

    @app.route("/api/cc/persona")
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_persona():
        tipo = request.args.get("tipo")
        nombre = " ".join(U(request.args.get("nombre")).split())
        if tipo not in TIPOS:
            return error("Tipo inválido.")
        with db() as con:
            saldo, a_favor, sin_fact = saldo_persona(con, tipo, nombre)
            formas = []
            if "ventas" in g.modulos and tipo == "cliente":
                formas = [dict(id=f["id"], nombre=f["nombre"], cheque=bool(f["pide_cheque"])) for f in con.execute("SELECT id, nombre, pide_cheque FROM stock_formas_pago ORDER BY id")]
        return jsonify(ok=True, saldo=saldo, a_favor=a_favor, sin_facturar=sin_fact, formas=formas)

    @app.route("/api/cc/entrega", methods=["POST"])
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_entrega():
        d = request.get_json(force=True, silent=True) or {}
        tipo = d.get("tipo")
        persona = " ".join(U(d.get("persona")).split())
        fecha = _fecha_ok(d.get("fecha")) or core.ahora()[:10]
        if tipo not in TIPOS:
            return error("Tipo inválido.")
        if not persona:
            return error(f"Escribí el nombre del {TIPOS[tipo][4].lower()}.")
        try:
            m = core.parse_monto(d.get("monto"))
        except ValueError:
            m = None
        if not m or m <= 0:
            return error("Escribí el importe de la entrega.")
        with db() as con:
            forma = cheque = None
            if "ventas" in g.modulos and tipo == "cliente":
                forma = con.execute("SELECT * FROM stock_formas_pago WHERE id=?", (d.get("forma_id"),)).fetchone()
                if not forma:
                    return error("Elegí la forma de pago.")
                if forma["pide_cheque"]:
                    cheque, msg = ventas.validar_cheque(d.get("cheque"))
                    if msg:
                        return error(msg)
            eid = con.execute("INSERT INTO cc_entregas (tipo, persona, fecha, monto, restante, nota, creado) VALUES (?,?,?,?,?,?,?)",
                              (tipo, persona, fecha, m, m, U(d.get("nota"))[:200] or None, core.ahora())).lastrowid
            if forma:      # la plata entró: va al Historial de cobros
                cobro_id = con.execute("INSERT INTO cobros (fecha, orden_id, monto, forma_id, forma, nota, cc_entrega_id) VALUES (?,?,?,?,?,?,?)",
                                       (fecha + core.ahora()[10:], None, m, forma["id"], forma["nombre"], persona, eid)).lastrowid
                if cheque:
                    ventas.guardar_cheque(con, cheque, forma["nombre"], m, "cuenta corriente", cobro_id=cobro_id)
            aplicado = aplicar_creditos(con, tipo, persona)
            saldo, a_favor, sin_fact = saldo_persona(con, tipo, persona)
        return jsonify(ok=True, aplicado=round(aplicado, 2), diferencia=saldo, a_favor=a_favor, sin_facturar=sin_fact)

    @app.route("/api/cc/entrega/<int:eid>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_entrega_eliminar(eid):
        d = request.get_json(force=True, silent=True) or {}
        r = sin_codigo(d, "Eliminar una entrega requiere el código de validación.")
        if r:
            return r
        with db() as con:
            e = con.execute("SELECT * FROM cc_entregas WHERE id=?", (eid,)).fetchone()
            if not e:
                return error("No existe la entrega.", 404)
            con.execute("DELETE FROM cc_pagos WHERE entrega_id=?", (eid,))
            _quitar_cobros(con, "cc_entrega_id=?", (eid,))
            con.execute("DELETE FROM cc_entregas WHERE id=?", (eid,))
            aplicar_creditos(con, e["tipo"], e["persona"])
        return jsonify(ok=True)

    @app.route("/api/cc/doc/<int:did>/pago", methods=["POST"])
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_pago(did):
        d = request.get_json(force=True, silent=True) or {}
        fecha = _fecha_ok(d.get("fecha")) or core.ahora()[:10]
        try:
            m = core.parse_monto(d.get("monto"))
        except ValueError:
            m = None
        if not m or m <= 0:
            return error("Escribí el importe.")
        with db() as con:
            r = con.execute(SQL_DOCS + " WHERE d.id=?", (did,)).fetchone()
            if not r:
                return error("No existe el registro.", 404)
            if not (r["numero"] or r["sin_factura"]) or not (r["total"] or 0) > 0:
                return error("Primero cargá el número de factura (o tildá «sin factura») y el importe.")
            saldo = round(r["total"] - r["pagado"], 2)
            if m > saldo + 0.004:
                return error(f"El importe supera el saldo pendiente ({core.fmt_monto(saldo)}).")
            forma = cheque = None
            if "ventas" in g.modulos and r["tipo"] == "cliente":
                forma = con.execute("SELECT * FROM stock_formas_pago WHERE id=?", (d.get("forma_id"),)).fetchone()
                if not forma:
                    return error("Elegí la forma de pago.")
                if forma["pide_cheque"]:
                    cheque, msg = ventas.validar_cheque(d.get("cheque"))
                    if msg:
                        return error(msg)
            pid = con.execute("INSERT INTO cc_pagos (doc_id, fecha, monto, nota) VALUES (?,?,?,?)", (did, fecha, m, U(d.get("nota"))[:200] or None)).lastrowid
            if forma:      # recién ahora, que el cobro se realizó, entra al Historial de cobros
                cobro_id = con.execute("INSERT INTO cobros (fecha, orden_id, monto, forma_id, forma, nota, cc_pago_id) VALUES (?,?,?,?,?,?,?)",
                                       (fecha + core.ahora()[10:], None, m, forma["id"], forma["nombre"], r["persona"], pid)).lastrowid
                if cheque:
                    ventas.guardar_cheque(con, cheque, forma["nombre"], m, "cuenta corriente", cobro_id=cobro_id)
        return jsonify(ok=True)

    @app.route("/api/cc/pago/<int:pid>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_pago_eliminar(pid):
        d = request.get_json(force=True, silent=True) or {}
        r = sin_codigo(d, "Eliminar un pago requiere el código de validación.")
        if r:
            return r
        with db() as con:
            _quitar_cobros(con, "cc_pago_id=?", (pid,))
            _liberar_entregas(con, "id=?", (pid,))
            con.execute("DELETE FROM cc_pagos WHERE id=?", (pid,))
        return jsonify(ok=True)

    @app.route("/api/cc/doc/<int:did>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_doc_eliminar(did):
        d = request.get_json(force=True, silent=True) or {}
        r = sin_codigo(d, "Eliminar el registro requiere el código de validación.")
        if r:
            return r
        with db() as con:
            _quitar_cobros(con, "cc_pago_id IN (SELECT id FROM cc_pagos WHERE doc_id=?)", (did,))
            if "stock" in g.modulos:
                aplicar_items(con, did, [], items_de(con, did))
            _liberar_entregas(con, "doc_id=?", (did,))
            doc = con.execute("SELECT tipo, persona FROM cc_docs WHERE id=?", (did,)).fetchone()
            con.execute("DELETE FROM cc_pagos WHERE doc_id=?", (did,))
            con.execute("DELETE FROM cc_docs WHERE id=?", (did,))
            if doc:
                aplicar_creditos(con, doc["tipo"], doc["persona"])
        return jsonify(ok=True)
