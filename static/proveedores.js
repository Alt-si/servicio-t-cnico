// Proveedores de stock: alta, edición y baja
function provForm(p) {
  p = p || {};
  const m = modal(`<h2>${p.id ? 'Editar proveedor' : 'Nuevo proveedor'}</h2><div id="pv-err" class="msg error" style="display:none"></div>
    <label>Nombre o razón social *</label><input id="pv-nombre" class="mayus" autocomplete="off" value="${esc(p.nombre || '')}">
    <div class="grid"><div><label>Persona de contacto</label><input id="pv-contacto" value="${esc(p.contacto || '')}"></div>
    <div><label>Teléfono / WhatsApp</label><input id="pv-telefono" inputmode="tel" value="${esc(p.telefono || '')}"></div>
    <div><label>Email</label><input id="pv-email" type="email" value="${esc(p.email || '')}"></div>
    <div><label>CUIT</label><input id="pv-cuit" inputmode="numeric" value="${esc(p.cuit || '')}"></div></div>
    <label>Dirección</label><input id="pv-direccion" value="${esc(p.direccion || '')}">
    <label>Notas</label><input id="pv-nota" value="${esc(p.nota || '')}" placeholder="Días de entrega, condiciones de pago…">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="pv-ok">Guardar</button><button class="btn" id="pv-no">Cancelar</button></div>`);
  m.querySelector('#pv-no').onclick = cerrar;
  m.querySelector('#pv-ok').onclick = async () => {
    const v = k => m.querySelector('#pv-' + k).value;
    const j = await api('/api/stock/lista/proveedores', {id: p.id, nombre: v('nombre'), contacto: v('contacto'), telefono: v('telefono'), email: v('email'), cuit: v('cuit'), direccion: v('direccion'), nota: v('nota')});
    if (!j.ok) { const e = m.querySelector('#pv-err'); e.textContent = j.error || 'No se pudo guardar.'; e.style.display = 'block'; return; }
    location.reload();
  };
  setTimeout(() => m.querySelector('#pv-nombre').focus(), 50);
}
async function provEliminar(id, nombre) {
  if (!await confirmar('¿Eliminar al proveedor ' + nombre + '?')) return;
  const j = await api('/api/stock/lista/proveedores/' + id + '/eliminar', {});
  if (!j.ok) return aviso(j.error || 'No se pudo eliminar.');
  location.reload();
}
