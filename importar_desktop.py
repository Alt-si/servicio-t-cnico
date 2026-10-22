"""Importa clientes, equipos, órdenes e historial de la agenda de escritorio (clientes.db) a una cuenta web.
Uso: python importar_desktop.py ruta/clientes.db mail_de_la_cuenta
La cuenta debe existir y estar vacía (se conservan los mismos números de orden)."""
import sqlite3, sys
import core

if len(sys.argv) != 3:
    sys.exit(__doc__)
origen, email = sys.argv[1], sys.argv[2].strip().lower()
with core.conectar_cuentas() as c:
    f = c.execute("SELECT id FROM cuentas WHERE email=?", (email,)).fetchone()
if not f:
    sys.exit("No existe una cuenta con ese mail. Creala primero desde el panel de Cuentas.")
src = sqlite3.connect(origen)
src.row_factory = sqlite3.Row
dst = core.conectar(f["id"])
if dst.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]:
    sys.exit("La cuenta ya tiene clientes; para no duplicar, el importador solo funciona con cuentas vacías.")
for tabla in ("clientes", "equipos", "ordenes", "historial_ordenes"):
    try:
        cols_o = [r[1] for r in src.execute(f"PRAGMA table_info({tabla})")]
    except sqlite3.Error:
        continue
    if not cols_o:
        continue
    cols_d = [r[1] for r in dst.execute(f"PRAGMA table_info({tabla})")]
    cols = [c for c in cols_o if c in cols_d]
    filas = src.execute(f"SELECT {','.join(cols)} FROM {tabla}").fetchall()
    dst.executemany(f"INSERT INTO {tabla} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [tuple(r) for r in filas])
    print(f"{tabla}: {len(filas)} filas")
dst.commit()
dst.close()
