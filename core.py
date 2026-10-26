"""Lógica de negocio y base de datos del sistema de servicio técnico (versión web)."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
COMERCIOS_DIR = DATA_DIR / "comercios"
CUENTAS_DB = DATA_DIR / "cuentas.db"

ESTADOS = ["Nuevo ingreso", "Pendiente", "Listo para retirar", "Equipo retirado", "Scrap", "Equipo comprado"]
ESTADOS_FINALES = ("Scrap", "Equipo comprado")
E_NUEVO, E_PENDIENTE, E_LISTO, E_RETIRADO, E_SCRAP, E_COMPRADO = ESTADOS
# (fondo, texto) de cada estado, tema claro / oscuro
COLORES = {
    "Nuevo ingreso": (("#3b82f6", "#ffffff"), ("#1d4ed8", "#ffffff")),
    "Pendiente": (("#f97316", "#ffffff"), ("#c2410c", "#ffffff")),
    "Listo para retirar": (("#bbf7d0", "#166534"), ("#14532d", "#86efac")),
    "Equipo retirado": (("#9ca3af", "#111827"), ("#4b5563", "#f3f4f6")),
    "Scrap": (("#dc2626", "#ffffff"), ("#991b1b", "#ffffff")),
    "Equipo comprado": (("#9ca3af", "#111827"), ("#4b5563", "#f3f4f6")),
}
TIPOS_EQUIPO = ['NOTEBOOK', 'PC DE ESCRITORIO', 'ALL IN ONE', 'IMPRESORA', 'CELULAR', 'TABLET', 'MONITOR', 'CONSOLA', 'GPS', 'OTRO']
IVA_OPCIONES = ["Monotributo", "Responsable Inscripto", "Exento", "Consumidor Final"]
CODIGO_POR_DEFECTO = "0000"
MARCA_NOMBRE = "Mi Servicio Técnico"
MARCA_SUBTITULO = "Sistema de servicio técnico"
COLOR_POR_DEFECTO = "#2f5bea"


# ---------------------------------------------------------------- utilidades
ZONA = os.environ.get("APP_TZ", "America/Argentina/Buenos_Aires")


def ahora_dt():
    """Hora local del comercio (el servidor suele estar en UTC)."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(ZONA)).replace(tzinfo=None)


def ahora():
    return ahora_dt().strftime("%Y-%m-%d %H:%M:%S")


def parse_fecha(txt):
    """Lee fechas guardadas con o sin segundos (la agenda de escritorio guarda 'AAAA-MM-DD HH:MM')."""
    txt = (txt or "").strip()
    for largo, formato in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(txt[:largo], formato)
        except ValueError:
            continue
    return None


def fmt_fecha(txt, con_hora=False):
    if not txt:
        return ""
    try:
        d = parse_fecha(txt)
        return d.strftime("%d/%m/%Y %H:%M" if con_hora else "%d/%m/%Y") if d else txt
    except ValueError:
        return txt


def num_ar(valor):
    """1500 -> '1.500'; 1500.5 -> '1.500,50' (miles con punto, centavos con coma solo si los hay)."""
    if valor is None or valor == "":
        return ""
    v = round(float(valor), 2)
    texto = f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_monto(valor):
    if valor is None or valor == "":
        return ""
    return "$ " + num_ar(valor)


def nombre_cliente(c):
    """Razón social si la tiene; si no, «Apellido, Nombre» (consumidor final)."""
    rs = (c["razon_social"] or "").strip()
    return rs or f"{c['apellido']}, {c['nombre']}"


def parse_monto(texto):
    """Acepta 1500, 1500.50, 1.500,50 o $ 1.500. Vacío devuelve None."""
    t = str(texto or "").replace("$", "").replace(" ", "").strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    valor = float(t)
    if valor < 0:
        raise ValueError("negativo")
    return valor


def calcular_saldo(presupuesto, sena):
    if presupuesto is None:
        return None
    return presupuesto - (sena or 0)


def saldo_a_abonar(presupuesto, sena, devuelto=None, rechazado=False):
    if devuelto or rechazado:
        return 0
    return calcular_saldo(presupuesto, sena)


def texto_equipo(tipo, marca, modelo):
    return " ".join(p.strip() for p in (tipo, marca, modelo) if p and p.strip())


def telefono_whatsapp(telefono):
    """Número en formato internacional para WhatsApp (Argentina: 549 + área + número), o None."""
    d = re.sub(r"\D", "", telefono or "")
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("549") and len(d) == 13:
        return d
    if d.startswith("54") and len(d) == 12:
        return "549" + d[2:]
    d = d.lstrip("0")
    if len(d) > 10:
        for i in (2, 3, 4):
            if d[i:i + 2] == "15" and len(d) - 2 == 10:
                d = d[:i] + d[i + 2:]
                break
    return "549" + d if len(d) == 10 else None


def es_retroceso(viejo, nuevo):
    return viejo in ESTADOS and nuevo in ESTADOS and ESTADOS.index(nuevo) < ESTADOS.index(viejo)


def requiere_codigo(viejo, nuevo):
    """Volver atrás, o salir de Scrap / Equipo comprado, requiere el código de validación."""
    if viejo == nuevo:
        return False
    return viejo in ESTADOS_FINALES or es_retroceso(viejo, nuevo)


# ---------------------------------------------------------------- textos de historial
TEXTO_RESPUESTA = {"aceptado": "Presupuesto aceptado por el cliente",
                   "rechazado": "Presupuesto NO aceptado por el cliente"}


def texto_evento_estado(viejo, nuevo, a_abonar=None, devuelto=False):
    if nuevo == E_LISTO:
        if devuelto:
            return "Trabajo no realizado, listo para retirar el equipo. Sin saldo a abonar (dinero devuelto)"
        txt = "Trabajo finalizado: listo para retirar"
        if a_abonar is not None:
            txt += f". Total a abonar: {fmt_monto(a_abonar)}"
        return txt
    if nuevo == E_RETIRADO:
        txt = "Equipo retirado por el cliente"
        if devuelto:
            txt += ". Trabajo no realizado, dinero devuelto"
        elif a_abonar:
            txt += f". Saldo abonado al retirar: {fmt_monto(a_abonar)}"
        return txt
    if nuevo == E_SCRAP:
        return "Equipo pasado a Scrap"
    return f"Estado: {viejo} → {nuevo}"


def texto_evento_presupuesto(monto, detalle, nuevo):
    if monto is None and not detalle:
        return "Presupuesto quitado"
    partes = []
    if monto is not None:
        partes.append(f"{'Presupuesto cargado' if nuevo else 'Presupuesto'}: {fmt_monto(monto)}")
    else:
        partes.append("Detalle del presupuesto")
    if detalle:
        partes.append("Detalle: " + " ".join(detalle.split()))
    return ". ".join(partes)


def texto_evento_devolucion(monto, motivo):
    if not monto:
        return "Devolución de dinero quitada"
    return f"Dinero devuelto al cliente: {fmt_monto(monto)}. Motivo: {motivo}"


def texto_evento_compra(monto, condiciones):
    return (f"Equipo comprado al cliente por {fmt_monto(monto)}. "
            f"Condiciones: {' '.join((condiciones or '').split())}")


# ---------------------------------------------------------------- cuentas (todas las empresas)
class Con(sqlite3.Connection):
    """Como `with con:` (commit/rollback) pero además cierra la conexión al salir."""
    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def sin_acentos(v):
    import unicodedata
    t = unicodedata.normalize("NFD", str(v).lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def conectar_cuentas():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CUENTAS_DB, timeout=15, factory=Con)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


# Secciones que se pueden habilitar o deshabilitar por cuenta desde el panel de administración.
# Para agregar una sección nueva: sumarla acá (clave: (nombre, descripción)), proteger sus rutas con
# @modulo_requerido("clave") y mostrar su enlace con {% if 'clave' in modulos %}.
MODULOS = {
    "clientes": ("Clientes", "Agenda de clientes y ficha completa de cada uno."),
    "ordenes": ("Órdenes de reparación", "Ingreso de equipos, presupuestos, estados, avisos por WhatsApp e historial. Requiere Clientes."),
    "recordatorios": ("Recordatorios", "Avisos con fecha y hora, con ventana y sonido."),
    "respaldo": ("Respaldo de datos", "Descargar copia de la base y cargar una base (en Configuración)."),
}
# Qué tienen habilitado las cuentas que todavía no se configuraron (las secciones nuevas arrancan apagadas).
MODULOS_INICIALES = ["clientes", "ordenes", "recordatorios", "respaldo"]
# Secciones planificadas: se muestran como «Próximamente» en el panel. Cuando estén listas se pasan a MODULOS.
MODULOS_FUTUROS = {
    "stock": ("Control de stock", "Repuestos y productos: existencias, ingresos y egresos."),
    "ventas": ("Ventas", "Registro de ventas y comprobantes."),
    "contabilidad": ("Contabilidad", "Ingresos, egresos y resultados."),
}
DEPENDENCIAS = {"ordenes": ["clientes"]}      # una sección que necesita a otra


def modulos_de(cuenta):
    """Conjunto de secciones habilitadas para una cuenta (fila de la tabla cuentas)."""
    import json
    if cuenta["es_admin"]:
        return set(MODULOS)
    crudo = cuenta["modulos"]
    try:
        lista = json.loads(crudo) if crudo else MODULOS_INICIALES
    except ValueError:
        lista = MODULOS_INICIALES
    return {m for m in lista if m in MODULOS}


def guardar_modulos(cuenta_id, claves):
    import json
    validas = [m for m in MODULOS if m in claves]
    for m in list(validas):                      # ordenes necesita clientes, etc.
        for req in DEPENDENCIAS.get(m, []):
            if req not in validas:
                validas.append(req)
    validas = [m for m in MODULOS if m in validas]
    with conectar_cuentas() as con:
        con.execute("UPDATE cuentas SET modulos=? WHERE id=?", (json.dumps(validas), cuenta_id))


def crear_cuentas():
    with conectar_cuentas() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cuentas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                comercio TEXT NOT NULL,
                clave_hash TEXT NOT NULL,
                activa INTEGER NOT NULL DEFAULT 1,
                es_admin INTEGER NOT NULL DEFAULT 0,
                creada TEXT,
                ultimo_ingreso TEXT,
                vence TEXT,
                notas TEXT,
                modulos TEXT
            )
        """)
        if "modulos" not in [r[1] for r in con.execute("PRAGMA table_info(cuentas)")]:
            con.execute("ALTER TABLE cuentas ADD COLUMN modulos TEXT")
        if con.execute("PRAGMA user_version").fetchone()[0] < 1:
            # Clientes y Órdenes eran parte fija del sistema: las cuentas con lista guardada las conservan.
            import json
            for cid, crudo in con.execute("SELECT id, modulos FROM cuentas WHERE modulos IS NOT NULL").fetchall():
                try:
                    lista = json.loads(crudo)
                except ValueError:
                    lista = []
                nueva = ["clientes", "ordenes"] + [m for m in lista if m not in ("clientes", "ordenes")]
                con.execute("UPDATE cuentas SET modulos=? WHERE id=?", (json.dumps(nueva), cid))
            con.execute("PRAGMA user_version=1")


def generar_clave(largo=10):
    alfabeto = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(largo))


def crear_cuenta(email, comercio, clave=None, es_admin=False):
    """Crea la cuenta y su base de datos. Devuelve (id, clave en texto) — la clave se muestra una sola vez."""
    from werkzeug.security import generate_password_hash
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("El mail no es válido.")
    clave = clave or generar_clave()
    with conectar_cuentas() as con:
        try:
            cur = con.execute(
                "INSERT INTO cuentas (email, comercio, clave_hash, es_admin, creada) VALUES (?,?,?,?,?)",
                (email, comercio.strip() or email, generate_password_hash(clave), int(es_admin), ahora()))
        except sqlite3.IntegrityError:
            raise ValueError("Ya existe una cuenta con ese mail.")
        cuenta_id = cur.lastrowid
    crear_base(cuenta_id)
    guardar_config(cuenta_id, "marca_nombre", comercio.strip() or MARCA_NOMBRE)
    return cuenta_id, clave


def cambiar_clave(cuenta_id, nueva):
    from werkzeug.security import generate_password_hash
    with conectar_cuentas() as con:
        con.execute("UPDATE cuentas SET clave_hash=? WHERE id=?", (generate_password_hash(nueva), cuenta_id))


def verificar_login(email, clave):
    from werkzeug.security import check_password_hash
    with conectar_cuentas() as con:
        fila = con.execute("SELECT * FROM cuentas WHERE email=?", (email.strip().lower(),)).fetchone()
        ok = bool(fila) and check_password_hash(fila["clave_hash"], clave)
        if not fila:      # mismo tiempo de respuesta exista o no la cuenta
            check_password_hash("pbkdf2:sha256:600000$x$" + "0" * 64, clave)
        if ok:
            con.execute("UPDATE cuentas SET ultimo_ingreso=? WHERE id=?", (ahora(), fila["id"]))
    return fila if ok else None


# ---------------------------------------------------------------- base de cada comercio
def ruta_base(cuenta_id):
    COMERCIOS_DIR.mkdir(parents=True, exist_ok=True)
    return COMERCIOS_DIR / f"{int(cuenta_id)}.db"


def conectar(cuenta_id):
    con = sqlite3.connect(ruta_base(cuenta_id), timeout=15, factory=Con)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.create_function("lower_u", 1, lambda v: sin_acentos(v) if v is not None else "")
    return con


def crear_base(cuenta_id):
    with conectar(cuenta_id) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL, apellido TEXT NOT NULL, telefono TEXT, dni TEXT,
                ciudad TEXT, domicilio TEXT, razon_social TEXT,
                condicion_iva TEXT NOT NULL DEFAULT 'Consumidor Final', cuit TEXT, mail TEXT
            );
            CREATE TABLE IF NOT EXISTS equipos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL, tipo TEXT, marca TEXT, modelo TEXT, serie TEXT,
                descripcion TEXT, creado TEXT, en_garantia INTEGER NOT NULL DEFAULT 0, clave_acceso TEXT
            );
            CREATE TABLE IF NOT EXISTS ordenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL, equipo_id INTEGER,
                fecha_ingreso TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'Nuevo ingreso',
                falla TEXT, accesorios TEXT, diagnostico TEXT,
                presupuesto_monto REAL, presupuesto_detalle TEXT, fecha_presupuesto TEXT,
                fecha_aceptacion TEXT, trasladado_a TEXT, actualizado TEXT, fecha_archivada TEXT,
                espera_repuesto INTEGER NOT NULL DEFAULT 0, sena_monto REAL,
                compra_monto REAL, compra_condiciones TEXT, fecha_compra TEXT,
                presupuesto_respuesta TEXT, fecha_finalizado TEXT, fecha_retiro TEXT,
                reembolso_monto REAL, reembolso_motivo TEXT, fecha_reembolso TEXT
            );
            CREATE TABLE IF NOT EXISTS historial_ordenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, orden_id INTEGER NOT NULL,
                fecha TEXT NOT NULL, estado TEXT, nota TEXT
            );
            CREATE TABLE IF NOT EXISTS recordatorios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_hora TEXT NOT NULL, descripcion TEXT NOT NULL,
                hecho INTEGER NOT NULL DEFAULT 0, avisado INTEGER NOT NULL DEFAULT 0, creado TEXT
            );
            CREATE TABLE IF NOT EXISTS presupuesto_opciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT, orden_id INTEGER NOT NULL, n INTEGER NOT NULL,
                monto REAL NOT NULL, detalle TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_opc_orden ON presupuesto_opciones(orden_id);
            CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT);
            CREATE INDEX IF NOT EXISTS ix_equipos_cliente ON equipos(cliente_id);
            CREATE INDEX IF NOT EXISTS ix_ordenes_cliente ON ordenes(cliente_id);
            CREATE INDEX IF NOT EXISTS ix_ordenes_estado ON ordenes(estado);
            CREATE INDEX IF NOT EXISTS ix_hist_orden ON historial_ordenes(orden_id);
        """)


def leer_config(cuenta_id, clave, defecto=None):
    with conectar(cuenta_id) as con:
        fila = con.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
    return fila["valor"] if fila and fila["valor"] is not None else defecto


def guardar_config(cuenta_id, clave, valor):
    with conectar(cuenta_id) as con:
        if valor is None:
            con.execute("DELETE FROM config WHERE clave=?", (clave,))
        else:
            con.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES (?, ?)", (clave, str(valor)))


def marca(cuenta_id):
    return {
        "nombre": leer_config(cuenta_id, "marca_nombre", MARCA_NOMBRE),
        "subtitulo": leer_config(cuenta_id, "marca_subtitulo", MARCA_SUBTITULO),
        "color": leer_config(cuenta_id, "marca_color", COLOR_POR_DEFECTO),
        "tiene_logo": bool(leer_config(cuenta_id, "marca_logo")),
        "logo_ancho": int(leer_config(cuenta_id, "marca_logo_ancho", 84)),
        "logo_alto": int(leer_config(cuenta_id, "marca_logo_alto", 84)),
    }


# ---------------------------------------------------------------- código de validación (por comercio)
def _hash_codigo(codigo, sal):
    return hashlib.pbkdf2_hmac("sha256", codigo.encode(), sal.encode(), 100_000).hex()


def codigo_es_valido(cuenta_id, codigo):
    guardado = leer_config(cuenta_id, "codigo_hash")
    if not guardado:
        return hmac.compare_digest(str(codigo or ""), CODIGO_POR_DEFECTO)
    sal, _, h = guardado.partition("$")
    return hmac.compare_digest(_hash_codigo(str(codigo or ""), sal), h)


def guardar_codigo(cuenta_id, nuevo):
    sal = secrets.token_hex(8)
    guardar_config(cuenta_id, "codigo_hash", f"{sal}${_hash_codigo(nuevo, sal)}")


def codigo_por_defecto(cuenta_id):
    return not leer_config(cuenta_id, "codigo_hash")


def registrar_evento(con, orden_id, estado, nota):
    con.execute("INSERT INTO historial_ordenes (orden_id, fecha, estado, nota) VALUES (?,?,?,?)",
                (orden_id, ahora(), estado, nota))


# ---------------------------------------------------------------- respaldo / carga de base
TABLAS_DATOS = ("clientes", "equipos", "ordenes", "historial_ordenes", "presupuesto_opciones")


def exportar_base(cuenta_id, destino):
    """Copia consistente de la base del comercio (API de backup de SQLite)."""
    origen = conectar(cuenta_id)
    try:
        dst = sqlite3.connect(destino)
        with dst:
            origen.backup(dst)
        dst.close()
    finally:
        origen.close()


def importar_base(cuenta_id, ruta):
    """Reemplaza los datos del comercio con los de otra base SQLite (respaldo web o agenda de escritorio).
    Solo se copian las columnas que existen en ambas. Devuelve {tabla: filas}. Lanza ValueError si no sirve."""
    try:
        src = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
        tablas = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        raise ValueError("El archivo no es una base de datos válida.")
    if "clientes" not in tablas:
        src.close()
        raise ValueError("El archivo no contiene la tabla de clientes: no parece una base de este sistema.")
    # copia de seguridad de lo que había
    previa = ruta_base(cuenta_id).with_name(f"{cuenta_id}.previa.db")
    exportar_base(cuenta_id, previa)
    dst = conectar(cuenta_id)
    resumen = {}
    try:
        with dst:
            for tabla in reversed(TABLAS_DATOS):
                dst.execute(f"DELETE FROM {tabla}")
            for tabla in TABLAS_DATOS:
                if tabla not in tablas:
                    continue
                cols_d = [r[1] for r in dst.execute(f"PRAGMA table_info({tabla})")]
                cols = [r[1] for r in src.execute(f"PRAGMA table_info({tabla})") if r[1] in cols_d]
                filas = src.execute(f"SELECT {','.join(cols)} FROM {tabla}").fetchall()
                dst.executemany(f"INSERT INTO {tabla} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", filas)
                resumen[tabla] = len(filas)
            if "config" in tablas:
                for clave, valor in src.execute("SELECT clave, valor FROM config WHERE clave LIKE 'marca_%'"):
                    dst.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES (?, ?)", (clave, valor))
    finally:
        src.close()
    return resumen


# ---------------------------------------------------------------- recordatorios
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def clasificar_recordatorio(fecha_hora, hecho=0):
    """(estado, texto corto): vencido, hoy, manana, proximo o hecho."""
    from datetime import timedelta
    try:
        dt = datetime.strptime(fecha_hora, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "proximo", fecha_hora or ""
    largo = f"{DIAS[dt.weekday()].capitalize()} {dt.strftime('%d/%m/%Y')}"
    if hecho:
        return "hecho", f"{largo}  {dt.strftime('%H:%M')}"
    n = ahora_dt()
    if dt < n:
        return "vencido", f"VENCIDO · {largo}  {dt.strftime('%H:%M')}"
    if dt.date() == n.date():
        return "hoy", "HOY · " + dt.strftime("%H:%M")
    if dt.date() == (n + timedelta(days=1)).date():
        return "manana", "Mañana · " + dt.strftime("%H:%M")
    return "proximo", f"{largo}  ·  {dt.strftime('%H:%M')}"


# ---------------------------------------------------------------- respaldo automático cada 30 días
DIAS_RESPALDO = 30
NOMBRE_RESPALDO = "RspSERv"


def zip_respaldo(cuenta_id):
    """Devuelve (bytes del .zip, nombre de archivo) con una copia consistente de la base del comercio."""
    import io, tempfile, zipfile
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "base.db")
        exportar_base(cuenta_id, base)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(base, "base.db")
    return buf.getvalue(), f"{NOMBRE_RESPALDO}_{ahora_dt().strftime('%Y-%m-%d')}.zip"


def respaldo_pendiente(cuenta_id):
    """True si pasaron 30 días desde el último respaldo (o su aviso). La primera vez solo arranca el conteo."""
    hoy = ahora_dt().date()
    ultimo = leer_config(cuenta_id, "respaldo_ultimo")
    if not ultimo:
        guardar_config(cuenta_id, "respaldo_ultimo", hoy.isoformat())
        return False
    try:
        return (hoy - datetime.strptime(ultimo, "%Y-%m-%d").date()).days >= DIAS_RESPALDO
    except ValueError:
        guardar_config(cuenta_id, "respaldo_ultimo", hoy.isoformat())
        return False


def guardar_copia_servidor(cuenta_id, conservar=3):
    """Copia automática en el servidor (se conservan las últimas 3)."""
    datos, nombre = zip_respaldo(cuenta_id)
    carpeta = DATA_DIR / "respaldos" / str(cuenta_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / nombre).write_bytes(datos)
    for viejo in sorted(carpeta.glob(f"{NOMBRE_RESPALDO}_*.zip"))[:-conservar]:
        viejo.unlink()
    return nombre


# ---------------------------------------------------------------- comprobante de ingreso (WhatsApp)
DIAS_LARGO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def texto_ingreso(empresa, oid, fecha_ingreso, nombre_pila, tipo, marca, modelo, serie, accesorios, falla, observaciones):
    """Ticket resumido del ingreso del equipo, en tipografía de máquina de escribir (monoespaciada en WhatsApp)."""
    dt = parse_fecha(fecha_ingreso)
    if dt:
        dia, fecha, hora = DIAS_LARGO[dt.weekday()].capitalize(), dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M") + " hs"
    else:
        dia, fecha, hora = "-", fecha_ingreso or "-", "-"
    limpio = lambda t: " ".join((t or "").split())
    fila = lambda etiqueta, valor: f"{etiqueta:<13}{valor}"
    raya = "=" * 30
    ticket = [limpio(empresa).upper(), raya,
              fila("Orden:", f"N° {int(oid):05d}"), fila("Día:", dia), fila("Fecha:", fecha), fila("Hora:", hora),
              "-" * 30,
              fila("Tipo:", limpio(tipo) or "-"), fila("Marca:", limpio(marca) or "-"), fila("Modelo:", limpio(modelo) or "-"),
              fila("N° de serie:", limpio(serie) or "-"), fila("Accesorios:", limpio(accesorios) or "Ninguno"),
              fila("Falla:", limpio(falla) or "-")]
    if limpio(observaciones):
        ticket.append(fila("Observ.:", limpio(observaciones)))
    ticket.append(raya)
    return (f"Hola, {nombre_pila}. Te enviamos el comprobante de ingreso de tu equipo.\n\n"
            "```\n" + "\n".join(ticket) + "\n```\n\nGracias por confiar en nosotros.")


# ---------------------------------------------------------------- listas y modo de texto configurables
MARCAS_DEFECTO = ['ACER', 'APPLE', 'ASUS', 'BGH', 'BROTHER', 'CANON', 'DELL', 'EPSON', 'GARMIN', 'HP', 'HUAWEI', 'LENOVO', 'LG', 'MOTOROLA', 'NOBLEX', 'PANTUM', 'POSITIVO', 'SAMSUNG', 'SONY', 'TOSHIBA', 'VAIO', 'XEROX', 'XIAOMI']
MODOS_TEXTO = ("mayusculas", "minusculas", "manual")


def limpiar_lista(texto, maximo=300):
    """Texto (una entrada por línea o separadas por coma) -> lista sin vacíos ni repetidos."""
    vistos, salida = set(), []
    for item in re.split(r"[\n,;]+", texto or ""):
        item = " ".join(item.split())[:40]
        if item and item.lower() not in vistos:
            vistos.add(item.lower())
            salida.append(item)
    return salida[:maximo]


def ajustes(cuenta_id):
    """Tipos de equipo, marcas y modo de texto configurados por el comercio (con valores por defecto)."""
    import json
    with conectar(cuenta_id) as con:
        filas = dict(con.execute("SELECT clave, valor FROM config WHERE clave IN ('tipos_equipo','marcas_equipo','modo_texto')").fetchall())
    def lista(clave, defecto):
        try:
            v = json.loads(filas.get(clave) or "")
            return v if isinstance(v, list) and v else list(defecto)
        except ValueError:
            return list(defecto)
    modo = filas.get("modo_texto")
    return {"tipos": lista("tipos_equipo", TIPOS_EQUIPO), "marcas": lista("marcas_equipo", MARCAS_DEFECTO),
            "modo_texto": modo if modo in MODOS_TEXTO else "mayusculas"}


def texto_opciones(extra, desde=2):
    """Texto para el historial: 'Opción 2: $ 25.000 (detalle); Opción 3: ...'."""
    partes = []
    for i, (monto, detalle) in enumerate(extra, desde):
        det = " ".join((detalle or "").split())
        partes.append(f"Opción {i}: {fmt_monto(monto)}" + (f" ({det})" if det else ""))
    return "; ".join(partes)
