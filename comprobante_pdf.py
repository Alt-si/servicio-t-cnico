"""PDF de facturas, notas de crédito y presupuestos (con reportlab; el QR también lo dibuja reportlab)."""
import base64
import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

NORMAL = ParagraphStyle("n", fontName="Helvetica", fontSize=9.5, leading=12)
CHICO = ParagraphStyle("c", parent=NORMAL, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
DER = ParagraphStyle("d", parent=NORMAL, alignment=2)
CENTRO = ParagraphStyle("ce", parent=NORMAL, alignment=1)
NEGRITA = ParagraphStyle("b", parent=NORMAL, fontName="Helvetica-Bold")
GRANDE = ParagraphStyle("g", parent=NORMAL, fontName="Helvetica-Bold", fontSize=15, leading=18)
LETRA_ST = ParagraphStyle("l", parent=NORMAL, fontName="Helvetica-Bold", fontSize=30, leading=32, alignment=1)
TOTAL = ParagraphStyle("t", parent=NORMAL, fontSize=13, leading=16, alignment=2)
ANCHO = A4[0] - 30 * mm


def _p(texto, estilo=NORMAL):
    return Paragraph(escape(str(texto)).replace("\n", "<br/>"), estilo)


def _pb(texto, estilo=NORMAL):
    """Párrafo con marcado simple (<b>) ya armado por quien llama; el texto variable va escapado antes."""
    return Paragraph(texto, estilo)


def _monto(v):
    s = f"{float(v):,.2f}"
    return "$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _cant(v):
    return (f"{float(v):g}").replace(".", ",")


def _fecha(s):
    s = (s or "")[:10]
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if len(s) == 10 else s


def _logo(b64, alto=22 * mm, ancho=55 * mm):
    if not b64:
        return None
    try:
        from PIL import Image as PI
        datos = base64.b64decode(b64)
        w, h = PI.open(io.BytesIO(datos)).size
        k = min(alto / h, ancho / w)
        return Image(io.BytesIO(datos), width=w * k, height=h * k, hAlign="LEFT")
    except Exception:
        return None


def _qr(url, lado=28 * mm):
    w = QrCodeWidget(url)
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0, lado / (y1 - y0), 0, 0])
    d.add(w)
    return d


def _construir(story, titulo):
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                      title=titulo, author="").build(story)
    return buf.getvalue()


def pdf_factura(f, emisor, items, tipos, letras, asoc=None, anuladora=None, qr_url="", logo_b64=""):
    f = dict(f)
    c_a = f["tipo_cbte"] in (1, 3)
    story = []
    if f["ambiente"] == "homologacion":
        story.append(Table([[_p("COMPROBANTE DE PRUEBA — SIN VALIDEZ FISCAL", NEGRITA)]], colWidths=[ANCHO],
                           style=[("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef3c7")), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story.append(Spacer(1, 4))
    if anuladora:
        story.append(Table([[_p(f"ANULADA con {tipos[anuladora['tipo_cbte']]} N° {anuladora['pto_vta']:04d}-{anuladora['numero']:08d}", NEGRITA)]], colWidths=[ANCHO],
                           style=[("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fee2e2")), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story.append(Spacer(1, 4))

    izq = []
    logo = _logo(logo_b64)
    if logo:
        izq += [logo, Spacer(1, 3)]
    if emisor.get("nombre_fantasia"):
        izq.append(_p(emisor["nombre_fantasia"], GRANDE))
        izq.append(_p(emisor.get("razon_social", ""), NEGRITA))
    else:
        izq.append(_p(emisor.get("razon_social", ""), GRANDE))
    izq.append(_p(emisor.get("condicion_iva", "")))
    if emisor.get("domicilio"):
        izq.append(_p(emisor["domicilio"]))
    der = [_p(tipos[f["tipo_cbte"]].upper(), GRANDE), _pb(f"<b>N° {f['pto_vta']:04d}-{f['numero']:08d}</b>"), _p(f"Fecha: {_fecha(f['fecha'])}"),
           _p(f"CUIT: {emisor.get('cuit', '')}")]
    if emisor.get("ingresos_brutos"):
        der.append(_p(f"Ing. Brutos: {emisor['ingresos_brutos']}"))
    if emisor.get("inicio_actividades"):
        der.append(_p(f"Inicio de actividades: {emisor['inicio_actividades']}"))
    letra = Table([[_p(letras[f["tipo_cbte"]], LETRA_ST)], [_p(f"COD. {f['tipo_cbte']:02d}", CHICO)]], colWidths=[18 * mm],
                  style=[("BOX", (0, 0), (-1, -1), 1, colors.black), ("ALIGN", (0, 0), (-1, -1), "CENTER")])
    cab = Table([[izq, letra, der]], colWidths=[ANCHO * 0.42, 22 * mm, ANCHO - ANCHO * 0.42 - 22 * mm],
                style=[("BOX", (0, 0), (-1, -1), 1, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP")])
    story += [cab]

    doc_t = {80: "CUIT", 96: "DNI"}.get(f["receptor_doc_tipo"], "Documento")
    rec = [_pb(f"<b>{escape(f['receptor_nombre'] or '')}</b>"),
           _p(f"{doc_t}: {f['receptor_doc'] if f['receptor_doc_tipo'] != 99 else 'Sin identificar'} · {f['receptor_condicion'] or ''}")]
    if f.get("receptor_domicilio"):
        rec.append(_p(f["receptor_domicilio"]))
    if f.get("forma_pago"):
        rec.append(_p(f"Forma de pago: {f['forma_pago']}"))
    if asoc:
        rec.append(_pb(f"<b>Comprobante asociado:</b> {escape(tipos[asoc['tipo_cbte']])} N° {asoc['pto_vta']:04d}-{asoc['numero']:08d}"))
    story.append(Table([[rec]], colWidths=[ANCHO], style=[("BOX", (0, 0), (-1, -1), 1, colors.black)]))
    story.append(Spacer(1, 8))

    cab_t = ["Descripción", "Cant.", "Precio unit."] + (["IVA"] if c_a else []) + ["Subtotal"]
    filas = [[_p(h, NEGRITA if i == 0 else ParagraphStyle("h", parent=NEGRITA, alignment=2)) for i, h in enumerate(cab_t)]]
    for i in items:
        k = 1 + (i.get("iva", 0) / 100) if c_a else 1
        fila = [_p(i["descripcion"]), _p(_cant(i["cantidad"]), DER), _p(_monto(i["precio"] / k), DER)]
        if c_a:
            fila.append(_p(f"{i.get('iva', 0):g}%".replace(".", ","), DER))
        fila.append(_p(_monto(i["total"] / k), DER))
        filas.append(fila)
    resto = 20 * mm + 28 * mm + 28 * mm + (16 * mm if c_a else 0)
    anchos = [ANCHO - resto] + [20 * mm, 28 * mm] + ([16 * mm] if c_a else []) + [28 * mm]
    story.append(Table(filas, colWidths=anchos, repeatRows=1, style=[
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black), ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(Spacer(1, 8))
    if c_a:
        story.append(_p(f"Importe neto: {_monto(f['neto'])}", DER))
        story.append(_p(f"IVA: {_monto(f['iva'])}", DER))
    story.append(_pb(f"TOTAL: <b>{_monto(f['total'])}</b>", TOTAL))
    story.append(Spacer(1, 10))

    cae = [_pb(f"<b>CAE N°:</b> {escape(str(f['cae'] or ''))}"), _pb(f"<b>Vencimiento del CAE:</b> {_fecha(f['cae_vto'])}")]
    if f.get("observaciones"):
        cae.append(_p(f"Obs. AFIP: {f['observaciones']}", CHICO))
    cae.append(_p("Comprobante autorizado", CHICO))
    story.append(Table([[_qr(qr_url) if qr_url else "", cae]], colWidths=[34 * mm, ANCHO - 34 * mm],
                       style=[("LINEABOVE", (0, 0), (-1, 0), 1, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 8)]))
    return _construir(story, f"{tipos[f['tipo_cbte']]} {f['pto_vta']:04d}-{f['numero']:08d}")


def _monto_entero(v):
    v = round(float(v), 2)
    s = f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    return "$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def pdf_presupuesto(p, items, pd, logo_b64=""):
    """Presupuesto con el formato del comercio: logo y fecha arriba, título con número, detalle, total con forma de pago, aviso y pie."""
    p = dict(p)
    story = []
    logo = _logo(logo_b64, alto=24 * mm)
    cab = [[logo or "", [_p(pd["fecha_larga"], DER), _p(f"N° {p['id']:04d}", ParagraphStyle("nr", parent=DER, fontName="Helvetica-Bold"))]]]
    story.append(Table(cab, colWidths=[ANCHO * 0.5, ANCHO * 0.5], style=[("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(Spacer(1, 14))
    story.append(_p("PRESUPUESTO", ParagraphStyle("pg", parent=GRANDE, fontSize=17, alignment=1, leading=20)))
    story.append(_p("DOCUMENTO NO VÁLIDO COMO FACTURA", ParagraphStyle("nv", parent=NEGRITA, fontSize=8.5, alignment=1)))
    story.append(Spacer(1, 16))
    story.append(_pb(f"Cliente: <b>{escape(p['cliente_nombre'] or '')}</b>"))
    story.append(Spacer(1, 10))
    linea = ParagraphStyle("li", parent=NORMAL, fontSize=10.5, leading=17)
    filas = []
    for i in items:
        txt = (f"{_cant(i['cantidad'])} x " if float(i["cantidad"]) != 1 else "") + i["descripcion"]
        filas.append([_p(txt, linea)] + ([_p(_monto_entero(i["total"]), ParagraphStyle("lr", parent=linea, alignment=2))] if pd["mostrar_precios"] else []))
    story.append(Table(filas, colWidths=[ANCHO - 35 * mm, 35 * mm] if pd["mostrar_precios"] else [ANCHO], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.append(Spacer(1, 14))
    total = f"TOTAL: {_monto_entero(p['total'])}.-" + (f" ({pd['forma_pago']})" if pd["forma_pago"] else "")
    story.append(_p(total, ParagraphStyle("tt", parent=NORMAL, fontName="Helvetica-BoldOblique", fontSize=12.5, leading=16)))
    if pd.get("validez"):
        story.append(Spacer(1, 4))
        story.append(_p(f"Válido hasta: {pd['validez']}"))
    if p.get("nota"):
        story.append(Spacer(1, 4))
        story.append(_p(p["nota"]))

    def pie(canvas, doc):
        if pd["aviso"]:
            canvas.saveState()
            av = Paragraph(escape(pd["aviso"].upper()), ParagraphStyle("av", parent=NORMAL, fontSize=9.5, leading=15))
            av.wrap(ANCHO - 10 * mm, 40 * mm)
            av.drawOn(canvas, 20 * mm, 22 * mm)
            canvas.restoreState()
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=40 * mm,
                      title=f"Presupuesto {p['id']:04d}", author="").build(story, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
