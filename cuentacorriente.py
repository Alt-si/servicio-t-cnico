"""Sección Cuenta corriente: lo que los clientes te deben (a cobrar) y lo que vos le debés a proveedores (a pagar)."""
import csv
import datetime
import io
import re

from flask import Response, g, jsonify, render_template, request

import core

TIPOS = {"cliente": ("Clientes", "A cobrar", "Cobrado", "Cobrado", "Cliente"), "proveedor": ("Proveedores", "A pagar", "Pagado", "Pagado", "Proveedor")}


def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS cc_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, persona TEXT NOT NULL, emision TEXT NOT NULL, vence TEXT NOT NULL,
            numero TEXT, total REAL NOT NULL, obs TEXT, creado TEXT);
        CREATE TABLE IF NOT EXISTS cc_pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, fecha TEXT NOT NULL, monto REAL NOT NULL, nota TEXT);
        CREATE INDEX IF NOT EXISTS ix_cc_docs_tipo ON cc_docs(tipo);
        CREATE INDEX IF NOT EXISTS ix_cc_pagos_doc ON cc_pagos(doc_id);
    """)


SQL_DOCS = ("SELECT d.*, IFNULL((SELECT SUM(monto) FROM cc_pagos WHERE doc_id=d.id), 0) AS pagado FROM cc_docs d")


def _estado(d, hoy):
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


def registrar(app, login_requerido, modulo_requerido, db, U, error):

    def enriquecer(filas, hoy):
        out = []
        for r in filas:
            d = dict(r)
            d["saldo"] = max(round(d["total"] - d["pagado"], 2), 0)
            d["estado"], d["atraso"] = _estado(d, hoy)
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
        if estado in ("pendiente", "vencido", "pagado"):
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
        vista = request.args.get("v") if request.args.get("v") == "personas" else "docs"
        with db() as con:
            todos = enriquecer(con.execute(SQL_DOCS + " WHERE d.tipo=?", (tipo,)).fetchall(), core.ahora()[:10])
            filas = listar(con, tipo, estado, q)
            nombres = sorted({r[0] for r in con.execute("SELECT persona FROM cc_docs WHERE tipo=?", (tipo,))} |
                             ({core.nombre_natural(c) for c in con.execute("SELECT * FROM clientes")} if tipo == "cliente" else set()))
        resumen = dict(cantidad=len(todos), total=sum(f["total"] for f in todos), pagado=sum(f["pagado"] for f in todos),
                       pendiente=sum(f["saldo"] for f in todos if f["estado"] == "pendiente"), vencido=sum(f["saldo"] for f in todos if f["estado"] == "vencido"))
        personas = {}
        for f in filas if q else todos:
            p = personas.setdefault(f["persona"].strip().lower(), dict(nombre=f["persona"], docs=0, saldo=0, vencido=0))
            p["docs"] += 1
            p["saldo"] += f["saldo"]
            if f["estado"] == "vencido":
                p["vencido"] += f["saldo"]
        personas = sorted([p for p in personas.values() if p["saldo"] > 0], key=lambda p: -p["saldo"])
        return render_template("cuentacorriente.html", tipo=tipo, T=TIPOS[tipo], estado=estado, q=q, vista=vista, filas=filas, resumen=resumen,
                               personas=personas, nombres=nombres, hoy=core.ahora()[:10], fmt_fecha=core.fmt_fecha)

    @app.route("/cuenta-corriente/exportar.csv")
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def cuenta_corriente_csv():
        tipo = request.args.get("t") if request.args.get("t") in TIPOS else "cliente"
        with db() as con:
            filas = listar(con, tipo, "todos", "")
        sal = io.StringIO()
        w = csv.writer(sal, delimiter=";")
        w.writerow(["Estado", "Emisión", "Vencimiento", TIPOS[tipo][4], "N° comprobante", "Total", "Pagado", "Saldo", "Atraso (días)", "Observaciones"])
        for f in filas:
            w.writerow([{"pagado": TIPOS[tipo][2], "vencido": "Vencido", "pendiente": TIPOS[tipo][1]}[f["estado"]], f["emision"], f["vence"], f["persona"], f["numero"] or "",
                        str(f["total"]).replace(".", ","), str(f["pagado"]).replace(".", ","), str(f["saldo"]).replace(".", ","), f["atraso"], f["obs"] or ""])
        return Response("﻿" + sal.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=cuenta_corriente_{tipo}.csv"})

    @app.route("/api/cc/doc/<int:did>")
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_doc(did):
        with db() as con:
            r = con.execute(SQL_DOCS + " WHERE d.id=?", (did,)).fetchone()
            if not r:
                return error("No existe el registro.", 404)
            pagos = [dict(p) for p in con.execute("SELECT * FROM cc_pagos WHERE doc_id=? ORDER BY fecha, id", (did,))]
        d = enriquecer([r], core.ahora()[:10])[0]
        return jsonify(ok=True, doc=d, pagos=pagos)

    @app.route("/api/cc/doc", methods=["POST"])
    @login_requerido
    @modulo_requerido("cuentacorriente")
    def api_cc_doc_guardar():
        d = request.get_json(force=True, silent=True) or {}
        tipo = d.get("tipo")
        persona = " ".join(U(d.get("persona")).split())
        emision, vence = _fecha_ok(d.get("emision")), _fecha_ok(d.get("vence"))
        if tipo not in TIPOS:
            return error("Tipo inválido.")
        if not persona:
            return error(f"Escribí el nombre del {TIPOS[tipo][4].lower()}.")
        if not emision or not vence:
            return error("Completá la fecha de emisión y el vencimiento.")
        if vence < emision:
            return error("El vencimiento no puede ser anterior a la emisión.")
        try:
            total = core.parse_monto(d.get("total"))
        except ValueError:
            total = None
        if not total or total <= 0:
            return error("Escribí el importe total.")
        numero, obs = " ".join(str(d.get("numero") or "").split())[:40] or None, U(d.get("obs"))[:500] or None
        with db() as con:
            if d.get("id"):
                r = con.execute(SQL_DOCS + " WHERE d.id=?", (d.get("id"),)).fetchone()
                if not r:
                    return error("No existe el registro.", 404)
                if total + 0.004 < r["pagado"]:
                    return error("El total no puede ser menor a lo ya pagado.")
                con.execute("UPDATE cc_docs SET persona=?, emision=?, vence=?, numero=?, total=?, obs=? WHERE id=?", (persona, emision, vence, numero, total, obs, r["id"]))
                return jsonify(ok=True, id=r["id"])
            m = 0
            if d.get("pagado_inicial") not in (None, ""):
                try:
                    m = core.parse_monto(d.get("pagado_inicial")) or 0
                except ValueError:
                    return error("El importe ya cobrado/pagado no es válido.")
                if m > total + 0.004:
                    return error("Lo ya cobrado/pagado no puede superar el total.")
            did = con.execute("INSERT INTO cc_docs (tipo, persona, emision, vence, numero, total, obs, creado) VALUES (?,?,?,?,?,?,?,?)",
                              (tipo, persona, emision, vence, numero, total, obs, core.ahora())).lastrowid
            if m > 0:
                con.execute("INSERT INTO cc_pagos (doc_id, fecha, monto, nota) VALUES (?,?,?,?)", (did, emision, m, "Entrega inicial"))
        return jsonify(ok=True, id=did)

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
            saldo = round(r["total"] - r["pagado"], 2)
            if m > saldo + 0.004:
                return error(f"El importe supera el saldo pendiente ({core.fmt_monto(saldo)}).")
            con.execute("INSERT INTO cc_pagos (doc_id, fecha, monto, nota) VALUES (?,?,?,?)", (did, fecha, m, U(d.get("nota"))[:200] or None))
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
            con.execute("DELETE FROM cc_pagos WHERE doc_id=?", (did,))
            con.execute("DELETE FROM cc_docs WHERE id=?", (did,))
        return jsonify(ok=True)
