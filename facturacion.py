"""Sección Facturación: facturas electrónicas con AFIP/ARCA (WSAA + WSFEv1) y presupuestos «no válidos como factura»."""
import base64
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

from flask import abort, current_app, g, jsonify, redirect, render_template, request, url_for

import core

# ------------------------------------------------------------------ constantes AFIP
URLS = {
    "homologacion": {"wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms", "wsfe": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx"},
    "produccion": {"wsaa": "https://wsaa.afip.gov.ar/ws/services/LoginCms", "wsfe": "https://servicios1.afip.gov.ar/wsfev1/service.asmx"},
}
TIPOS_CBTE = {1: "Factura A", 6: "Factura B", 11: "Factura C"}
LETRA = {1: "A", 6: "B", 11: "C"}
ALICUOTAS = {0: 3, 2.5: 9, 5: 8, 10.5: 4, 21: 5, 27: 6}          # % -> id AFIP
CONDICION_RECEPTOR = {"Responsable Inscripto": 1, "Exento": 4, "Consumidor Final": 5, "Monotributo": 6}
CONDICIONES_EMISOR = ["Responsable Inscripto", "Monotributo", "Exento"]
CONCEPTOS = {1: "Productos", 2: "Servicios", 3: "Productos y servicios"}


class AfipError(Exception):
    pass


# ------------------------------------------------------------------ cifrado de la clave privada (en reposo)
def _fernet():
    from cryptography.fernet import Fernet
    secreto = current_app.secret_key
    secreto = secreto.encode() if isinstance(secreto, str) else secreto
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"afip|" + secreto).digest()))


def cifrar(texto):
    return _fernet().encrypt(texto.encode()).decode()


def descifrar(texto):
    return _fernet().decrypt(texto.encode()).decode()


def solo_digitos(t):
    return re.sub(r"\D", "", t or "")


def cuit_valido(cuit):
    c = solo_digitos(cuit)
    if len(c) != 11:
        return False
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    resto = 11 - sum(int(a) * b for a, b in zip(c[:10], pesos)) % 11
    dv = {11: 0, 10: 9}.get(resto, resto)
    return dv == int(c[10])


# ------------------------------------------------------------------ claves y certificados
def generar_clave_y_csr(cuit, nombre):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alias = re.sub(r"[^A-Za-z0-9]", "", nombre)[:20] or "sistema"
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, (nombre or alias)[:60]),
        x509.NameAttribute(NameOID.COMMON_NAME, alias),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {solo_digitos(cuit)}"),
    ])
    csr = x509.CertificateSigningRequestBuilder().subject_name(sujeto).sign(clave, hashes.SHA256())
    clave_pem = clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return clave_pem, csr.public_bytes(serialization.Encoding.PEM).decode()


def verificar_certificado(cert_pem, clave_pem):
    """Devuelve (vencimiento, titular) si el certificado es válido y corresponde a la clave; si no, AfipError."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
    except Exception:
        raise AfipError("El certificado no es válido. Pegá el contenido completo del archivo .crt (empieza con BEGIN CERTIFICATE).")
    try:
        clave = serialization.load_pem_private_key(clave_pem.encode(), password=None)
    except Exception:
        raise AfipError("La clave privada no es válida.")
    pub = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if pub(cert.public_key()) != pub(clave.public_key()):
        raise AfipError("Ese certificado no corresponde a la clave privada de esta cuenta. Usá el certificado que AFIP generó a partir del pedido (CSR) de esta misma cuenta.")
    return cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after, cert.subject.rfc4514_string()


def _firmar_tra(cert_pem, clave_pem, servicio="wsfe"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7
    from zoneinfo import ZoneInfo
    ahora = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).replace(microsecond=0)
    fmt = lambda d: d.isoformat()
    tra = (f'<?xml version="1.0" encoding="UTF-8"?><loginTicketRequest version="1.0"><header>'
           f'<uniqueId>{int(ahora.timestamp())}</uniqueId><generationTime>{fmt(ahora - timedelta(minutes=10))}</generationTime>'
           f'<expirationTime>{fmt(ahora + timedelta(minutes=10))}</expirationTime></header><service>{servicio}</service></loginTicketRequest>')
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    clave = serialization.load_pem_private_key(clave_pem.encode(), password=None)
    cms = pkcs7.PKCS7SignatureBuilder().set_data(tra.encode()).add_signer(cert, clave, hashes.SHA256()).sign(
        serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    return base64.b64encode(cms).decode()


# ------------------------------------------------------------------ SOAP
def _post(url, cuerpo, accion=""):
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")        # los servidores de AFIP usan parámetros DH antiguos
    except ssl.SSLError:
        pass
    req = urllib.request.Request(url, data=cuerpo.encode("utf-8"), method="POST",
                                 headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{accion}"'})
    try:
        with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:                 # AFIP devuelve los errores SOAP con HTTP 500
        return e.read().decode("utf-8", "replace")
    except Exception as e:
        raise AfipError(f"No se pudo conectar con AFIP ({e}). Probá de nuevo en unos minutos.")


def _limpio(xml_texto):
    """Parsea XML ignorando los espacios de nombres."""
    xml_texto = re.sub(r"""\sxmlns(:\w+)?=("[^"]*"|'[^']*')""", "", xml_texto)
    xml_texto = re.sub(r"<(/?)\w+:", r"<\1", xml_texto)
    try:
        return ET.fromstring(xml_texto)
    except ET.ParseError:
        raise AfipError("AFIP devolvió una respuesta que no se pudo interpretar.")


def _texto(nodo, ruta, defecto=""):
    n = nodo.find(ruta)
    return (n.text or "").strip() if n is not None and n.text else defecto


def obtener_ticket(con, cuenta):
    """Token y sign de WSAA (se reutilizan hasta que vencen)."""
    fila = con.execute("SELECT * FROM afip_tokens WHERE cuenta_id=?", (cuenta["id"],)).fetchone()
    ahora = core.ahora_dt().replace(tzinfo=None)
    if fila and fila["vence"] and core.parse_fecha(fila["vence"]) and core.parse_fecha(fila["vence"]) > ahora + timedelta(minutes=5):
        return fila["token"], fila["sign"]
    if not cuenta["certificado"] or not cuenta["clave_cifrada"]:
        raise AfipError("La cuenta no tiene certificado cargado. Completalo en Configuración → Facturación.")
    cms = _firmar_tra(cuenta["certificado"], descifrar(cuenta["clave_cifrada"]))
    sobre = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
             'xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov"><soapenv:Header/><soapenv:Body><wsaa:loginCms>'
             f'<wsaa:in0>{cms}</wsaa:in0></wsaa:loginCms></soapenv:Body></soapenv:Envelope>')
    resp = _post(URLS[cuenta["ambiente"]]["wsaa"], sobre)
    raiz = _limpio(resp)
    falla = raiz.find(".//faultstring")
    if falla is not None:
        msg = (falla.text or "").strip()
        if "alreadyAuthenticated" in msg:
            raise AfipError("AFIP ya tiene un ticket vigente para esta cuenta. Esperá unos minutos y volvé a intentar.")
        raise AfipError(f"AFIP rechazó el ingreso: {msg}")
    interno = raiz.find(".//loginCmsReturn")
    if interno is None or not interno.text:
        raise AfipError("AFIP no devolvió el ticket de acceso.")
    t = _limpio(interno.text)
    token, sign, vence = _texto(t, ".//token"), _texto(t, ".//sign"), _texto(t, ".//expirationTime")
    if not token or not sign:
        raise AfipError("AFIP no devolvió el ticket de acceso.")
    con.execute("INSERT OR REPLACE INTO afip_tokens (cuenta_id, token, sign, vence) VALUES (?,?,?,?)",
                (cuenta["id"], token, sign, vence[:19].replace("T", " ")))
    return token, sign


def _wsfe(con, cuenta, metodo, cuerpo_xml):
    token, sign = obtener_ticket(con, cuenta)
    auth = f"<ar:Auth><ar:Token>{escape(token)}</ar:Token><ar:Sign>{escape(sign)}</ar:Sign><ar:Cuit>{solo_digitos(cuenta['cuit'])}</ar:Cuit></ar:Auth>"
    sobre = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
             'xmlns:ar="http://ar.gov.afip.dif.FEV1/"><soapenv:Header/><soapenv:Body>'
             f'<ar:{metodo}>{auth}{cuerpo_xml}</ar:{metodo}></soapenv:Body></soapenv:Envelope>')
    resp = _post(URLS[cuenta["ambiente"]]["wsfe"], sobre, f"http://ar.gov.afip.dif.FEV1/{metodo}")
    raiz = _limpio(resp)
    falla = raiz.find(".//faultstring")
    if falla is not None:
        raise AfipError(f"AFIP respondió con un error: {(falla.text or '').strip()}")
    errores = [f"{_texto(e, 'Code')}: {_texto(e, 'Msg')}" for e in raiz.findall(".//Errors/Err")]
    return raiz, errores


def ultimo_autorizado(con, cuenta, tipo):
    raiz, errores = _wsfe(con, cuenta, "FECompUltimoAutorizado", f"<ar:PtoVta>{int(cuenta['punto_venta'])}</ar:PtoVta><ar:CbteTipo>{tipo}</ar:CbteTipo>")
    if errores:
        raise AfipError("AFIP: " + "; ".join(errores))
    return int(_texto(raiz, ".//CbteNro", "0") or 0)


# ------------------------------------------------------------------ cálculo del comprobante
def r2(x):
    return round(float(x) + 1e-9, 2)


def tipo_comprobante(cuenta_cond, receptor_cond):
    if cuenta_cond == "Responsable Inscripto":
        return 1 if receptor_cond == "Responsable Inscripto" else 6
    return 11


def calcular(items, cuenta_cond):
    """Items con precio final (IVA incluido para Responsable Inscripto). Devuelve (items, neto, iva, total, alicuotas)."""
    out, por_alic = [], {}
    for it in items:
        cant = float(it["cantidad"]); precio = float(it["precio"])
        total = r2(cant * precio)
        alic = float(it.get("iva", 21)) if cuenta_cond == "Responsable Inscripto" else 0.0
        if alic not in ALICUOTAS:
            raise AfipError(f"Alícuota de IVA no válida: {alic}%.")
        out.append({"descripcion": it["descripcion"], "cantidad": cant, "precio": precio, "iva": alic, "total": total})
        por_alic[alic] = por_alic.get(alic, 0) + total
    neto = iva = 0.0
    alicuotas = []
    for alic, tot in sorted(por_alic.items()):
        base = r2(tot / (1 + alic / 100))
        imp = r2(tot - base)
        neto += base; iva += imp
        alicuotas.append({"id": ALICUOTAS[alic], "alic": alic, "base": base, "importe": imp})
    total = r2(sum(i["total"] for i in out))
    return out, r2(neto), r2(iva), total, alicuotas


def solicitar_cae(con, cuenta, datos):
    """Pide el CAE a AFIP. datos: tipo, concepto, doc_tipo, doc_nro, cond_receptor_id, fecha(date), neto, iva, total, alicuotas, servicio(desde,hasta,vto)."""
    tipo, pv = datos["tipo"], int(cuenta["punto_venta"])
    numero = ultimo_autorizado(con, cuenta, tipo) + 1
    f = lambda d: d.strftime("%Y%m%d")
    serv = ""
    if datos["concepto"] in (2, 3):
        d, h, v = datos["serv"]
        serv = f"<ar:FchServDesde>{f(d)}</ar:FchServDesde><ar:FchServHasta>{f(h)}</ar:FchServHasta><ar:FchVtoPago>{f(v)}</ar:FchVtoPago>"
    iva_xml = ""
    if datos["alicuotas"] and tipo != 11:
        iva_xml = "<ar:Iva>" + "".join(f"<ar:AlicIva><ar:Id>{a['id']}</ar:Id><ar:BaseImp>{a['base']:.2f}</ar:BaseImp><ar:Importe>{a['importe']:.2f}</ar:Importe></ar:AlicIva>"
                                        for a in datos["alicuotas"]) + "</ar:Iva>"
    cuerpo = (f"<ar:FeCAEReq><ar:FeCabReq><ar:CantReg>1</ar:CantReg><ar:PtoVta>{pv}</ar:PtoVta><ar:CbteTipo>{tipo}</ar:CbteTipo></ar:FeCabReq>"
              f"<ar:FeDetReq><ar:FECAEDetRequest><ar:Concepto>{datos['concepto']}</ar:Concepto><ar:DocTipo>{datos['doc_tipo']}</ar:DocTipo>"
              f"<ar:DocNro>{datos['doc_nro']}</ar:DocNro><ar:CbteDesde>{numero}</ar:CbteDesde><ar:CbteHasta>{numero}</ar:CbteHasta>"
              f"<ar:CbteFch>{f(datos['fecha'])}</ar:CbteFch><ar:ImpTotal>{datos['total']:.2f}</ar:ImpTotal><ar:ImpTotConc>0.00</ar:ImpTotConc>"
              f"<ar:ImpNeto>{datos['neto']:.2f}</ar:ImpNeto><ar:ImpOpEx>0.00</ar:ImpOpEx><ar:ImpTrib>0.00</ar:ImpTrib><ar:ImpIVA>{datos['iva']:.2f}</ar:ImpIVA>"
              f"{serv}<ar:MonId>PES</ar:MonId><ar:MonCotiz>1</ar:MonCotiz><ar:CondicionIVAReceptorId>{datos['cond_receptor_id']}</ar:CondicionIVAReceptorId>"
              f"{iva_xml}</ar:FECAEDetRequest></ar:FeDetReq></ar:FeCAEReq>")
    raiz, errores = _wsfe(con, cuenta, "FECAESolicitar", cuerpo)
    det = raiz.find(".//FECAEDetResponse")
    obs = [f"{_texto(o, 'Code')}: {_texto(o, 'Msg')}" for o in raiz.findall(".//FECAEDetResponse/Observaciones/Obs")]
    if det is None or _texto(det, "Resultado") != "A":
        raise AfipError("AFIP no autorizó el comprobante. " + "; ".join(errores + obs) if (errores or obs) else "AFIP no autorizó el comprobante.")
    cae, vto = _texto(det, "CAE"), _texto(det, "CAEFchVto")
    if not cae:
        raise AfipError("AFIP no devolvió el CAE.")
    return {"numero": numero, "cae": cae, "cae_vto": f"{vto[:4]}-{vto[4:6]}-{vto[6:8]}", "observaciones": "; ".join(obs)}


def url_qr(f, cuenta_cuit):
    """Código QR de AFIP (RG 4892) como URL."""
    datos = {"ver": 1, "fecha": f["fecha"][:10], "cuit": int(solo_digitos(cuenta_cuit)), "ptoVta": f["pto_vta"], "tipoCmp": f["tipo_cbte"],
             "nroCmp": f["numero"], "importe": f["total"], "moneda": "PES", "ctz": 1, "tipoDocRec": f["receptor_doc_tipo"],
             "nroDocRec": int(solo_digitos(f["receptor_doc"]) or 0), "tipoCodAut": "E", "codAut": int(f["cae"])}
    return "https://www.afip.gob.ar/fe/qr/?p=" + base64.urlsafe_b64encode(json.dumps(datos, separators=(",", ":")).encode()).decode().rstrip("=")


def qr_svg(url):
    try:
        import io
        import segno
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="svg", scale=4, border=1, xmldecl=False, svgns=True)
        return buf.getvalue().decode()
    except Exception:
        return ""


# ------------------------------------------------------------------ base de datos
def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS afip_cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, cuit TEXT NOT NULL, razon_social TEXT NOT NULL,
            condicion_iva TEXT NOT NULL DEFAULT 'Monotributo', punto_venta INTEGER NOT NULL DEFAULT 1,
            ambiente TEXT NOT NULL DEFAULT 'homologacion', domicilio TEXT, ingresos_brutos TEXT, inicio_actividades TEXT,
            clave_cifrada TEXT, certificado TEXT, csr TEXT, cert_vence TEXT);
        CREATE TABLE IF NOT EXISTS afip_tokens (cuenta_id INTEGER PRIMARY KEY, token TEXT, sign TEXT, vence TEXT);
        CREATE TABLE IF NOT EXISTS facturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, cuenta_id INTEGER, cuenta_nombre TEXT, emisor_json TEXT, cliente_id INTEGER,
            receptor_nombre TEXT, receptor_doc_tipo INTEGER, receptor_doc TEXT, receptor_condicion TEXT, receptor_domicilio TEXT,
            tipo_cbte INTEGER NOT NULL, pto_vta INTEGER NOT NULL, numero INTEGER NOT NULL, concepto INTEGER NOT NULL DEFAULT 1,
            items TEXT NOT NULL, neto REAL NOT NULL, iva REAL NOT NULL, total REAL NOT NULL, cae TEXT, cae_vto TEXT,
            observaciones TEXT, ambiente TEXT, forma_pago TEXT);
        CREATE INDEX IF NOT EXISTS ix_facturas_cliente ON facturas(cliente_id);
        CREATE TABLE IF NOT EXISTS presupuestos_doc (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, cliente_id INTEGER, cliente_nombre TEXT,
            items TEXT NOT NULL, total REAL NOT NULL, nota TEXT, validez TEXT);
    """)


def registrar(app, login_requerido, modulo_requerido, db, U, error):
    def cuentas(con):
        return con.execute("SELECT * FROM afip_cuentas ORDER BY nombre COLLATE NOCASE").fetchall()

    def publico(c):
        """Datos de la cuenta que se pueden mostrar (nunca la clave privada)."""
        return {"id": c["id"], "nombre": c["nombre"], "cuit": c["cuit"], "razon_social": c["razon_social"], "condicion_iva": c["condicion_iva"],
                "punto_venta": c["punto_venta"], "ambiente": c["ambiente"], "domicilio": c["domicilio"] or "", "ingresos_brutos": c["ingresos_brutos"] or "",
                "inicio_actividades": c["inicio_actividades"] or "", "tiene_clave": bool(c["clave_cifrada"]), "tiene_cert": bool(c["certificado"]),
                "csr": c["csr"] or "", "cert_vence": c["cert_vence"] or ""}

    app.jinja_env.globals["afip_cuentas"] = lambda: [publico(c) for c in cuentas(db())]

    def doc_del_cliente(c):
        cuit = solo_digitos(c["cuit"])
        if len(cuit) == 11:
            return 80, cuit
        dni = solo_digitos(c["dni"])
        if dni:
            return 96, dni
        return 99, "0"

    # ---------------- configuración de cuentas AFIP
    @app.route("/api/afip/cuenta", methods=["POST"])
    @login_requerido
    @modulo_requerido("facturacion")
    def api_afip_cuenta():
        d = request.get_json(force=True, silent=True) or {}
        nombre = " ".join(U(d.get("nombre")).split())
        razon = " ".join(U(d.get("razon_social")).split())
        cuit = solo_digitos(d.get("cuit"))
        cond = d.get("condicion_iva")
        amb = d.get("ambiente")
        try:
            pv = int(d.get("punto_venta"))
        except (TypeError, ValueError):
            pv = 0
        if not nombre or not razon:
            return error("Completá el nombre de la cuenta y la razón social.")
        if not cuit_valido(cuit):
            return error("El CUIT no es válido (11 dígitos, con su dígito verificador).")
        if cond not in CONDICIONES_EMISOR:
            return error("Elegí la condición frente al IVA.")
        if amb not in URLS:
            return error("Elegí el ambiente.")
        if not 1 <= pv <= 99999:
            return error("El punto de venta tiene que ser un número entre 1 y 99999.")
        campos = (nombre, cuit, razon, cond, pv, amb, (d.get("domicilio") or "").strip(), (d.get("ingresos_brutos") or "").strip(),
                  (d.get("inicio_actividades") or "").strip())
        with db() as con:
            cid = d.get("id")
            if cid:
                if not con.execute("SELECT 1 FROM afip_cuentas WHERE id=?", (cid,)).fetchone():
                    return error("No existe la cuenta.", 404)
                con.execute("UPDATE afip_cuentas SET nombre=?, cuit=?, razon_social=?, condicion_iva=?, punto_venta=?, ambiente=?, domicilio=?, "
                            "ingresos_brutos=?, inicio_actividades=? WHERE id=?", campos + (cid,))
                con.execute("DELETE FROM afip_tokens WHERE cuenta_id=?", (cid,))
            else:
                cid = con.execute("INSERT INTO afip_cuentas (nombre, cuit, razon_social, condicion_iva, punto_venta, ambiente, domicilio, ingresos_brutos, "
                                  "inicio_actividades) VALUES (?,?,?,?,?,?,?,?,?)", campos).lastrowid
        return jsonify(ok=True, id=cid)

    @app.route("/api/afip/cuenta/<int:cid>/eliminar", methods=["POST"])
    @login_requerido
    @modulo_requerido("facturacion")
    def api_afip_cuenta_eliminar(cid):
        with db() as con:
            con.execute("DELETE FROM afip_cuentas WHERE id=?", (cid,))
            con.execute("DELETE FROM afip_tokens WHERE cuenta_id=?", (cid,))      # las facturas ya emitidas se conservan
        return jsonify(ok=True)

    @app.route("/api/afip/cuenta/<int:cid>/csr", methods=["POST"])
    @login_requerido
    @modulo_requerido("facturacion")
    def api_afip_csr(cid):
        """Genera la clave privada (queda guardada cifrada en el servidor) y el pedido de certificado (CSR) para subir a AFIP."""
        with db() as con:
            c = con.execute("SELECT * FROM afip_cuentas WHERE id=?", (cid,)).fetchone()
            if not c:
                return error("No existe la cuenta.", 404)
            clave, csr = generar_clave_y_csr(c["cuit"], c["razon_social"])
            con.execute("UPDATE afip_cuentas SET clave_cifrada=?, csr=?, certificado=NULL, cert_vence=NULL WHERE id=?", (cifrar(clave), csr, cid))
            con.execute("DELETE FROM afip_tokens WHERE cuenta_id=?", (cid,))
        return jsonify(ok=True, csr=csr)

    @app.route("/api/afip/cuenta/<int:cid>/certificado", methods=["POST"])
    @login_requerido
    @modulo_requerido("facturacion")
    def api_afip_certificado(cid):
        """Guarda el certificado (.crt) que entregó AFIP. Opcionalmente también una clave privada propia (si no se usó el pedido del sistema)."""
        d = request.get_json(force=True, silent=True) or {}
        cert = (d.get("certificado") or "").strip()
        clave_nueva = (d.get("clave") or "").strip()
        with db() as con:
            c = con.execute("SELECT * FROM afip_cuentas WHERE id=?", (cid,)).fetchone()
            if not c:
                return error("No existe la cuenta.", 404)
            clave = clave_nueva or (descifrar(c["clave_cifrada"]) if c["clave_cifrada"] else "")
            if not clave:
                return error("Primero generá el pedido de certificado, o cargá también tu clave privada.")
            try:
                vence, _ = verificar_certificado(cert, clave)
            except AfipError as e:
                return error(str(e))
            con.execute("UPDATE afip_cuentas SET certificado=?, clave_cifrada=?, cert_vence=? WHERE id=?",
                        (cert, cifrar(clave) if clave_nueva else c["clave_cifrada"], vence.strftime("%Y-%m-%d"), cid))
            con.execute("DELETE FROM afip_tokens WHERE cuenta_id=?", (cid,))
        return jsonify(ok=True, vence=vence.strftime("%d/%m/%Y"))

    @app.route("/api/afip/cuenta/<int:cid>/probar", methods=["POST"])
    @login_requerido
    @modulo_requerido("facturacion")
    def api_afip_probar(cid):
        with db() as con:
            c = con.execute("SELECT * FROM afip_cuentas WHERE id=?", (cid,)).fetchone()
            if not c:
                return error("No existe la cuenta.", 404)
            try:
                tipo = tipo_comprobante(c["condicion_iva"], "Consumidor Final")
                ultimo = ultimo_autorizado(con, c, tipo)
            except AfipError as e:
                return error(str(e))
        return jsonify(ok=True, mensaje=f"Conexión correcta con AFIP ({'pruebas' if c['ambiente'] == 'homologacion' else 'producción'}). "
                                        f"Último {TIPOS_CBTE[tipo]} autorizado en el punto de venta {c['punto_venta']}: {ultimo}.")

    # ---------------- listado
    @app.route("/facturacion")
    @login_requerido
    @modulo_requerido("facturacion")
    def facturacion():
        with db() as con:
            facs = con.execute("SELECT * FROM facturas ORDER BY id DESC LIMIT 300").fetchall()
            pres = con.execute("SELECT * FROM presupuestos_doc ORDER BY id DESC LIMIT 300").fetchall()
            hay = con.execute("SELECT COUNT(*) FROM afip_cuentas").fetchone()[0]
        return render_template("facturacion/lista.html", facs=facs, pres=pres, hay_cuentas=hay, TIPOS=TIPOS_CBTE, fmt_fecha=core.fmt_fecha)

    # ---------------- nueva factura
    @app.route("/facturacion/nueva")
    @login_requerido
    @modulo_requerido("facturacion")
    def factura_nueva():
        cliente_id = request.args.get("cliente", type=int)
        with db() as con:
            cliente = con.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,)).fetchone() if cliente_id else None
            if not cliente:
                return redirect(url_for("clientes"))
            todas = cuentas(con)
            cuenta_id = request.args.get("cuenta", type=int)
            cuenta = next((c for c in todas if c["id"] == cuenta_id), None)
            if not cuenta and len(todas) == 1:
                cuenta = todas[0]
        if not todas:
            return render_template("facturacion/elegir_cuenta.html", cliente=cliente, cuentas=[], ps=None)
        if not cuenta:
            return render_template("facturacion/elegir_cuenta.html", cliente=cliente, cuentas=[publico(c) for c in todas])
        tipo = tipo_comprobante(cuenta["condicion_iva"], cliente["condicion_iva"])
        doc_tipo, doc_nro = doc_del_cliente(cliente)
        return render_template("facturacion/nueva.html", cliente=cliente, cuenta=publico(cuenta), tipo=tipo, TIPOS=TIPOS_CBTE, CONCEPTOS=CONCEPTOS,
                               doc_tipo=doc_tipo, doc_nro=doc_nro, hoy=core.ahora()[:10], ri=cuenta["condicion_iva"] == "Responsable Inscripto",
                               cuentas_total=len(todas), ALICUOTAS=sorted(ALICUOTAS))

    @app.route("/api/facturacion/emitir", methods=["POST"])
    @login_requerido
    @modulo_requerido("facturacion")
    def api_facturar():
        d = request.get_json(force=True, silent=True) or {}
        items = []
        for it in d.get("items") or []:
            desc = " ".join(U(it.get("descripcion")).split())
            try:
                cant, precio = core.parse_monto(it.get("cantidad")), core.parse_monto(it.get("precio"))
            except ValueError:
                cant = precio = None
            if not desc or not cant or cant <= 0 or precio is None or precio <= 0:
                return error("Revisá los renglones: cada uno necesita descripción, cantidad y precio mayores a cero.")
            items.append({"descripcion": desc, "cantidad": cant, "precio": precio, "iva": core.parse_monto(it.get("iva")) if it.get("iva") not in (None, "") else 21})
        if not items:
            return error("Agregá al menos un renglón.")
        concepto = int(d.get("concepto") or 1)
        if concepto not in CONCEPTOS:
            return error("Concepto inválido.")
        try:
            fecha = datetime.strptime((d.get("fecha") or core.ahora()[:10])[:10], "%Y-%m-%d").date()
            serv = None
            if concepto in (2, 3):
                serv = tuple(datetime.strptime((d.get(k) or d.get("fecha") or core.ahora()[:10])[:10], "%Y-%m-%d").date() for k in ("serv_desde", "serv_hasta", "vto_pago"))
        except ValueError:
            return error("Revisá las fechas.")
        if abs((fecha - core.ahora_dt().date()).days) > 10:
            return error("AFIP solo admite fechas de comprobante hasta 10 días antes o después de hoy.")
        with db() as con:
            c = con.execute("SELECT * FROM afip_cuentas WHERE id=?", (d.get("cuenta_id"),)).fetchone()
            cli = con.execute("SELECT * FROM clientes WHERE id=?", (d.get("cliente_id"),)).fetchone()
            if not c or not cli:
                return error("Falta la cuenta de AFIP o el cliente.")
            tipo = tipo_comprobante(c["condicion_iva"], cli["condicion_iva"])
            doc_tipo, doc_nro = doc_del_cliente(cli)
            if tipo == 1 and doc_tipo != 80:
                return error("Para una Factura A el cliente necesita CUIT. Cargalo en su ficha.")
            try:
                lineas, neto, iva, total, alic = calcular(items, c["condicion_iva"])
                if doc_tipo == 99 and total >= 417288:
                    return error("Para este importe AFIP exige identificar al cliente (CUIT o DNI). Cargalo en su ficha.")
                res = solicitar_cae(con, c, {"tipo": tipo, "concepto": concepto, "doc_tipo": doc_tipo, "doc_nro": doc_nro, "fecha": fecha,
                                              "cond_receptor_id": CONDICION_RECEPTOR.get(cli["condicion_iva"], 5), "neto": neto, "iva": iva,
                                              "total": total, "alicuotas": alic, "serv": serv})
            except AfipError as e:
                return error(str(e))
            except (KeyError, ValueError):
                return error("Revisá los datos del comprobante.")
            emisor = {"razon_social": c["razon_social"], "cuit": c["cuit"], "condicion_iva": c["condicion_iva"], "domicilio": c["domicilio"],
                      "ingresos_brutos": c["ingresos_brutos"], "inicio_actividades": c["inicio_actividades"]}
            fid = con.execute(
                "INSERT INTO facturas (fecha, cuenta_id, cuenta_nombre, emisor_json, cliente_id, receptor_nombre, receptor_doc_tipo, receptor_doc, receptor_condicion, "
                "receptor_domicilio, tipo_cbte, pto_vta, numero, concepto, items, neto, iva, total, cae, cae_vto, observaciones, ambiente, forma_pago) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fecha.strftime("%Y-%m-%d") + " " + core.ahora()[11:19], c["id"], c["nombre"], json.dumps(emisor), cli["id"], core.nombre_cliente(cli),
                 doc_tipo, doc_nro, cli["condicion_iva"], (cli["domicilio"] or ""), tipo, int(c["punto_venta"]), res["numero"], concepto,
                 json.dumps(lineas), neto, iva, total, res["cae"], res["cae_vto"], res["observaciones"], c["ambiente"], (d.get("forma_pago") or "").strip())).lastrowid
        return jsonify(ok=True, id=fid)

    @app.route("/facturacion/comprobante/<int:fid>")
    @login_requerido
    @modulo_requerido("facturacion")
    def factura_ver(fid):
        with db() as con:
            f = con.execute("SELECT * FROM facturas WHERE id=?", (fid,)).fetchone()
            if not f:
                abort(404)
        emisor = json.loads(f["emisor_json"] or "{}")
        qr = url_qr(f, emisor.get("cuit", "0"))
        return render_template("facturacion/comprobante.html", f=f, emisor=emisor, items=json.loads(f["items"]), TIPOS=TIPOS_CBTE, LETRA=LETRA,
                               qr_url=qr, qr=qr_svg(qr), fmt_fecha=core.fmt_fecha, CONCEPTOS=CONCEPTOS)

    # ---------------- presupuesto no válido como factura
    @app.route("/facturacion/presupuesto")
    @login_requerido
    @modulo_requerido("facturacion")
    def presupuesto_nuevo():
        cliente = None
        with db() as con:
            if request.args.get("cliente", type=int):
                cliente = con.execute("SELECT * FROM clientes WHERE id=?", (request.args.get("cliente", type=int),)).fetchone()
        if not cliente:
            return redirect(url_for("clientes"))
        return render_template("facturacion/presupuesto.html", cliente=cliente)

    @app.route("/api/facturacion/presupuesto", methods=["POST"])
    @login_requerido
    @modulo_requerido("facturacion")
    def api_presupuesto_doc():
        d = request.get_json(force=True, silent=True) or {}
        items, total = [], 0.0
        for it in d.get("items") or []:
            desc = " ".join(U(it.get("descripcion")).split())
            try:
                cant, precio = core.parse_monto(it.get("cantidad")), core.parse_monto(it.get("precio"))
            except ValueError:
                cant = precio = None
            if not desc or not cant or cant <= 0 or precio is None:
                return error("Revisá los renglones: cada uno necesita descripción, cantidad y precio.")
            sub = r2(cant * precio)
            total += sub
            items.append({"descripcion": desc, "cantidad": cant, "precio": precio, "total": sub})
        if not items:
            return error("Agregá al menos un renglón.")
        with db() as con:
            cli = con.execute("SELECT * FROM clientes WHERE id=?", (d.get("cliente_id"),)).fetchone()
            if not cli:
                return error("Falta el cliente.")
            pid = con.execute("INSERT INTO presupuestos_doc (fecha, cliente_id, cliente_nombre, items, total, nota, validez) VALUES (?,?,?,?,?,?,?)",
                              (core.ahora(), cli["id"], core.nombre_cliente(cli), json.dumps(items), r2(total), U(d.get("nota")) or None,
                               (d.get("validez") or "").strip() or None)).lastrowid
        return jsonify(ok=True, id=pid)

    @app.route("/facturacion/presupuesto/<int:pid>")
    @login_requerido
    @modulo_requerido("facturacion")
    def presupuesto_ver(pid):
        with db() as con:
            p = con.execute("SELECT * FROM presupuestos_doc WHERE id=?", (pid,)).fetchone()
            if not p:
                abort(404)
            cli = con.execute("SELECT * FROM clientes WHERE id=?", (p["cliente_id"],)).fetchone()
        items = json.loads(p["items"])
        marca = core.marca(g.cuenta["id"])
        nombre_pila = ((cli["nombre"] if cli else "") or "").strip().title()
        lineas = [f"Hola, {nombre_pila}, en {marca['nombre']} le enviamos el presupuesto N° {p['id']:04d}"]
        for i in items:
            cant = f"{i['cantidad']:g}"
            lineas.append(f"{cant} x {i['descripcion']}: {core.fmt_monto(i['total'])}")
        lineas.append(f"TOTAL: {core.fmt_monto(p['total'])}")
        if p["validez"]:
            lineas.append(f"Válido hasta: {p['validez']}")
        lineas.append("Documento no válido como factura.")
        return render_template("facturacion/presupuesto_ver.html", p=p, items=items, cli=cli, marca=marca, fmt_fecha=core.fmt_fecha,
                               wa=core.telefono_whatsapp(cli["telefono"]) if cli else "", texto="\n".join(lineas))
