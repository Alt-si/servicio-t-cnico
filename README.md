# Sistema de servicio técnico (versión web)

Cada comercio entra con **mail + clave** desde cualquier equipo. Cada uno tiene su propia base de datos (nadie ve los datos de otro).

## Publicar en Render (recomendado para la etapa de prueba)
1. Subí esta carpeta a un repositorio de GitHub (privado).
2. En render.com: **New → Blueprint** → elegí el repositorio. Lee `render.yaml` solo (servicio web + disco persistente de 1 GB; requiere plan pago *Starter*).
3. Cuando termine el deploy, abrí **Shell** del servicio y creá tu cuenta de administrador:
   `python crear_admin.py tu@mail.com "Alt Soluciones Informáticas" TuClaveSegura1`
4. Entrá a la URL que te da Render, ingresá con ese mail y clave. En **Cuentas** creás una cuenta por cada comercio que compre: se genera una clave que ves una sola vez y le entregás al cliente.
5. Importar tus clientes actuales: subí `clientes.db` a `/var/data/` y ejecutá en el Shell:
   `python importar_desktop.py /var/data/clientes.db tu@mail.com`

Notas: Render reinicia el servicio unos segundos en cada deploy. Los datos viven en `/var/data` (disco persistente): hacé copias de `/var/data` periódicamente (snapshots del disco desde Render).

## Alternativa: VPS (más barato con muchos clientes)
Ubuntu + `pip install -r requirements.txt` + `gunicorn wsgi:app --workers 1 --threads 4` detrás de Nginx con HTTPS (Let's Encrypt), variable `DATA_DIR=/ruta/datos`.

## Probar en tu PC
`pip install -r requirements.txt` → `INSECURE_COOKIES=1 python app.py` → http://localhost:5000
(Primero: `python crear_admin.py mail clave`.)

## Seguridad incluida
Claves con hash, cookie de sesión HttpOnly/Secure, protección CSRF, límite de intentos de login, cuentas desactivables, código de validación por comercio (0000 por defecto).
