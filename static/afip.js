// Configuración de cuentas de AFIP
function afipCuenta(c) {
  c = c || {};
  const modal_ = modal(`<h2>${c.id ? 'Editar cuenta de AFIP' : 'Nueva cuenta de AFIP'}</h2><div id="a-err" class="msg error" style="display:none"></div>
    <div class="grid">
      <div><label>Nombre de la cuenta *</label><input id="a-nombre" value="${esc(c.nombre || '')}" placeholder="Ej.: Mi monotributo"></div>
      <div><label>Razón social / nombre *</label><input id="a-razon" value="${esc(c.razon_social || '')}"></div>
      <div><label>Nombre de fantasía (sale en la factura)</label><input id="a-fant" value="${esc(c.nombre_fantasia || '')}" placeholder="Ej.: Alt Soluciones Informáticas"></div>
      <div><label>CUIT *</label><input id="a-cuit" inputmode="numeric" value="${esc(c.cuit || '')}" placeholder="20123456789"></div>
      <div><label>Condición frente al IVA *</label><select id="a-iva">${['Monotributo', 'Responsable Inscripto', 'Exento'].map(x => `<option ${x === (c.condicion_iva || 'Monotributo') ? 'selected' : ''}>${x}</option>`).join('')}</select></div>
      <div><label>Punto de venta *</label><input id="a-pv" inputmode="numeric" value="${c.punto_venta || 1}"></div>
      <div><label>Ambiente</label><select id="a-amb"><option value="homologacion" ${c.ambiente !== 'produccion' ? 'selected' : ''}>Pruebas (sin validez fiscal)</option><option value="produccion" ${c.ambiente === 'produccion' ? 'selected' : ''}>Producción (facturas reales)</option></select></div>
      <div><label>Domicilio comercial</label><input id="a-dom" value="${esc(c.domicilio || '')}"></div>
      <div><label>Ingresos Brutos</label><input id="a-iibb" value="${esc(c.ingresos_brutos || '')}"></div>
      <div><label>Inicio de actividades</label><input id="a-inicio" value="${esc(c.inicio_actividades || '')}" placeholder="DD/MM/AAAA"></div>
    </div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="a-ok">Guardar</button><button class="btn" id="a-no">Cancelar</button></div>`);
  const q = s => modal_.querySelector(s);
  q('#a-no').onclick = cerrar;
  q('#a-ok').onclick = async () => {
    const j = await api('/api/afip/cuenta', {id: c.id, nombre: q('#a-nombre').value, razon_social: q('#a-razon').value, nombre_fantasia: q('#a-fant').value, cuit: q('#a-cuit').value, condicion_iva: q('#a-iva').value,
      punto_venta: q('#a-pv').value, ambiente: q('#a-amb').value, domicilio: q('#a-dom').value, ingresos_brutos: q('#a-iibb').value, inicio_actividades: q('#a-inicio').value});
    if (j.ok) { if (c.id) return location.reload(); const n = q('#a-nombre').value; cerrar(); afipCert({id: j.id, nombre: n, tiene_clave: false, csr: ''}); }
    else { const e = q('#a-err'); e.textContent = j.error; e.style.display = 'block'; }
  };
}
async function afipBorrar(id, nombre) {
  if (!await confirmar(`¿Borrar la cuenta «${nombre}»? Las facturas ya emitidas se conservan.`)) return;
  const j = await api(`/api/afip/cuenta/${id}/eliminar`, {}); if (j.ok) location.reload(); else aviso(j.error);
}
async function afipProbar(id) {
  aviso('Conectando con AFIP…');
  const j = await api(`/api/afip/cuenta/${id}/probar`, {});
  aviso(j.ok ? j.mensaje : j.error, j.ok ? 'Conexión correcta' : 'No se pudo conectar');
}
// Certificado: 1) pedido (CSR) que se sube a AFIP, 2) certificado (.crt) que devuelve AFIP
function afipCert(c) {
  const m = modal(`<h2>Certificado de ${esc(c.nombre)}</h2><div id="c-err" class="msg error" style="display:none"></div>
    <p><b>Paso 1.</b> Generá el pedido de certificado. El sistema guarda la clave privada cifrada en el servidor; nunca sale de acá.</p>
    <p><button class="btn pri" id="c-gen">${c.tiene_clave ? 'Generar uno nuevo' : 'Generar pedido de certificado'}</button></p>
    <div id="c-csr-caja" style="display:${c.csr ? 'block' : 'none'}"><label>Pedido (CSR): copialo o descargalo y subilo a AFIP</label><textarea id="c-csr" readonly rows="5" style="font-family:monospace;font-size:11px">${esc(c.csr || '')}</textarea>
      <div class="row"><button class="btn" id="c-copiar">Copiar</button><button class="btn" id="c-bajar">Descargar .csr</button></div>
      <p class="mut" style="font-size:13px">En AFIP (con clave fiscal): <b>Administración de Certificados Digitales</b> → agregar alias → pegá el pedido → descargá el .crt. Después, en <b>Administrador de Relaciones de Clave Fiscal</b>, asociá a ese certificado el servicio <b>Facturación Electrónica (wsfe)</b>. Y en <b>ABM de Puntos de Venta</b>, creá un punto de venta tipo <b>RECE para aplicativo y web services</b>.</p></div>
    <p><b>Paso 2.</b> Cargá el certificado (.crt) que te dio AFIP:</p>
    <input type="file" id="c-archivo" accept=".crt,.pem,.cer,.txt"><textarea id="c-cert" rows="4" placeholder="-----BEGIN CERTIFICATE-----" style="font-family:monospace;font-size:11px;margin-top:6px"></textarea>
    <details style="margin-top:6px"><summary class="mut">Ya tengo mi propia clave privada (opcional)</summary><textarea id="c-clave" rows="4" placeholder="-----BEGIN PRIVATE KEY-----" style="font-family:monospace;font-size:11px"></textarea></details>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="c-ok">Guardar certificado</button><button class="btn" id="c-no">Cerrar</button></div>`);
  const q = s => m.querySelector(s), err = t => { const e = q('#c-err'); e.textContent = t; e.style.display = t ? 'block' : 'none'; };
  q('#c-no').onclick = () => { cerrar(); location.reload(); };
  q('#c-gen').onclick = async () => {
    if (c.tiene_clave && !await confirmar('Se reemplaza la clave y el certificado actuales: vas a tener que cargar un certificado nuevo. ¿Continuar?')) return;
    const j = await api(`/api/afip/cuenta/${c.id}/csr`, {}); if (!j.ok) return err(j.error);
    c.tiene_clave = true; q('#c-csr').value = j.csr; q('#c-csr-caja').style.display = 'block'; err('');
  };
  q('#c-copiar').onclick = () => { q('#c-csr').select(); document.execCommand('copy'); };
  q('#c-bajar').onclick = () => { const a = document.createElement('a'); a.href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(q('#c-csr').value); a.download = 'pedido_certificado.csr'; a.click(); };
  q('#c-archivo').onchange = e => { const f = e.target.files[0]; if (f) f.text().then(t => { q('#c-cert').value = t; }); };
  q('#c-ok').onclick = async () => {
    const j = await api(`/api/afip/cuenta/${c.id}/certificado`, {certificado: q('#c-cert').value, clave: q('#c-clave').value});
    if (!j.ok) return err(j.error);
    cerrar(); await aviso(`Certificado guardado. Vence el ${j.vence}. Probá la conexión para verificar que AFIP lo acepta.`); location.reload();
  };
}

// Envío de mails (SMTP)
const PROV_MAIL = {'gmail.com': 'gmail', 'googlemail.com': 'gmail', 'hotmail.com': 'ms', 'hotmail.com.ar': 'ms', 'outlook.com': 'ms', 'outlook.com.ar': 'ms', 'live.com': 'ms', 'live.com.ar': 'ms', 'msn.com': 'ms', 'yahoo.com': 'yahoo', 'yahoo.com.ar': 'yahoo', 'icloud.com': 'icloud', 'me.com': 'icloud'};
const AYUDA_MAIL = {
  gmail: 'Gmail: usá una <b>contraseña de aplicación</b>, no la común. Se crea en tu cuenta de Google → Seguridad → Verificación en dos pasos → Contraseñas de aplicaciones.',
  ms: 'Hotmail/Outlook: si tenés verificación en dos pasos, usá una <b>contraseña de aplicación</b> (cuenta Microsoft → Seguridad).',
  yahoo: 'Yahoo: usá una <b>contraseña de aplicación</b> (cuenta Yahoo → Seguridad de la cuenta).',
  icloud: 'iCloud: usá una <b>contraseña específica de app</b> (appleid.apple.com).'};
function mailConfig() {
  const c0 = JSON.parse(document.getElementById('mail-datos').textContent);
  const m = modal(`<h2>Envío de mails</h2><div id="ml-err" class="msg error" style="display:none"></div>
    <div class="grid">
      <div><label>Mail desde el que se envía *</label><input id="ml-rem" type="email" value="${esc(c0.mail_remitente)}" placeholder="tu@gmail.com"></div>
      <div><label>Contraseña ${c0.tiene_clave ? '(dejá vacío para no cambiarla)' : ''}</label><input id="ml-clave" type="password" autocomplete="new-password"></div>
      <div><label>Nombre que ve el cliente</label><input id="ml-nombre" value="${esc(c0.mail_nombre)}" placeholder="Mi Comercio"></div>
    </div>
    <p class="mut" style="font-size:13px" id="ml-ayuda"></p>
    <div id="ml-srv" style="display:none"><div class="grid">
      <div><label>Servidor (SMTP) *</label><input id="ml-host" value="${esc(c0.mail_host)}" placeholder="smtp.tudominio.com"></div>
      <div><label>Puerto *</label><input id="ml-port" inputmode="numeric" value="${esc(c0.mail_port || '465')}"></div>
      <div><label>Seguridad</label><select id="ml-seg"><option value="ssl" ${c0.mail_seg === 'ssl' ? 'selected' : ''}>SSL (puerto 465)</option><option value="starttls" ${c0.mail_seg === 'starttls' ? 'selected' : ''}>STARTTLS (puerto 587)</option><option value="ninguna" ${c0.mail_seg === 'ninguna' ? 'selected' : ''}>Ninguna</option></select></div>
      <div><label>Usuario</label><input id="ml-user" value="${esc(c0.mail_user)}" autocomplete="off" placeholder="si es distinto del mail"></div>
    </div></div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="ml-ok">Guardar</button><button class="btn" id="ml-no">Cancelar</button></div>`);
  const q = s => m.querySelector(s);
  const prov = () => PROV_MAIL[(q('#ml-rem').value.split('@')[1] || '').trim().toLowerCase()];
  const actualizar = () => {
    const p = prov(), tiene = q('#ml-rem').value.includes('@');
    q('#ml-srv').style.display = (p || !tiene) ? 'none' : 'block';
    q('#ml-ayuda').innerHTML = p ? AYUDA_MAIL[p] + ' El servidor se configura solo.' : (tiene ? 'Es un correo propio: completá los datos del servidor que te dio tu proveedor.' : '');
  };
  q('#ml-rem').oninput = actualizar; actualizar();
  q('#ml-no').onclick = cerrar;
  q('#ml-ok').onclick = async () => {
    const auto = !!prov();
    const j = await api('/api/mail/config', {host: auto ? '' : q('#ml-host').value, puerto: q('#ml-port').value, seguridad: q('#ml-seg').value, usuario: auto ? '' : q('#ml-user').value,
      clave: q('#ml-clave').value, remitente: q('#ml-rem').value, nombre: q('#ml-nombre').value});
    if (j.ok) location.reload(); else { const e = q('#ml-err'); e.textContent = j.error; e.style.display = 'block'; }
  };
}
function mailProbar() {
  const m = modal(`<h2>Mail de prueba</h2><div id="mp-err" class="msg error" style="display:none"></div><label>Enviar la prueba a</label><input id="mp-dest" type="email" placeholder="tu@correo.com">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="mp-ok">Enviar</button><button class="btn" id="mp-no">Cancelar</button></div>`);
  m.querySelector('#mp-no').onclick = cerrar;
  m.querySelector('#mp-ok').onclick = async () => {
    const b = m.querySelector('#mp-ok'); b.disabled = true; b.textContent = 'Enviando…';
    const j = await api('/api/mail/probar', {destino: m.querySelector('#mp-dest').value});
    if (j.ok) { cerrar(); aviso('Mail de prueba enviado. Revisá la bandeja de entrada (y el correo no deseado).'); }
    else { b.disabled = false; b.textContent = 'Enviar'; const e = m.querySelector('#mp-err'); e.textContent = j.error; e.style.display = 'block'; }
  };
}
