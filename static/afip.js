// Configuración de cuentas de AFIP
function afipCuenta(c) {
  c = c || {};
  const modal_ = modal(`<h2>${c.id ? 'Editar cuenta de AFIP' : 'Nueva cuenta de AFIP'}</h2><div id="a-err" class="msg error" style="display:none"></div>
    <div class="grid">
      <div><label>Nombre de la cuenta *</label><input id="a-nombre" value="${esc(c.nombre || '')}" placeholder="Ej.: Mi monotributo"></div>
      <div><label>Razón social / nombre *</label><input id="a-razon" value="${esc(c.razon_social || '')}"></div>
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
    const j = await api('/api/afip/cuenta', {id: c.id, nombre: q('#a-nombre').value, razon_social: q('#a-razon').value, cuit: q('#a-cuit').value, condicion_iva: q('#a-iva').value,
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
