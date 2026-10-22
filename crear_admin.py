"""Crea la cuenta administradora (la del dueño del sistema). Uso: python crear_admin.py mail@dominio.com "Nombre del negocio" [clave]"""
import sys
import core
core.crear_cuentas()
if len(sys.argv) < 3:
    sys.exit(__doc__)
cid, clave = core.crear_cuenta(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None, es_admin=True)
print(f"Cuenta admin creada. Mail: {sys.argv[1]}  Clave: {clave}")
