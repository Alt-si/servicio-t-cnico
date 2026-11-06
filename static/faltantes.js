// Faltantes de stock: lista manual y copiar al portapapeles
async function falForm(f) {
  const j0 = await api('/api/stock/faltantes/buscar?q=');
  const prods = j0.items || [];
  const enStock = f && f.producto_id && prods.some(p => p.id === f.producto_id);
  const m = modal(`<h2>${f ? 'Editar faltante' : 'Agregar faltante'}</h2><div id="fa-err" class="msg error" style="display:none"></div>
    <label>Producto *</label><select id="fa-s"><option value="">— Elegí un producto de Stock —</option>
      ${prods.map(p => `<option value="${p.id}" ${enStock && f.producto_id === p.id ? 'selected' : ''}>${esc(p.nombre)}${p.codigo ? ' · ' + esc(p.codigo) : ''} (stock ${p.stock} ${esc(p.abrev)})</option>`).join('')}
      <option value="nuevo" ${f && !enStock ? 'selected' : ''}>＋ Agregar nuevo (el producto no está en Stock)</option></select>
    <div id="fa-nuevo" style="display:${f && !enStock ? 'block' : 'none'}"><label>Nombre del producto nuevo *</label><input id="fa-n" class="mayus" autocomplete="off" value="${esc(f && !enStock ? f.nombre : '')}" placeholder="Escribí el nombre"></div>
    ${window.PROV_ON ? `<label>Proveedor</label><select id="fa-p"><option value="">—</option>${(window.PROVS || []).map(v => `<option value="${v.id}" ${f && f.proveedor_id == v.id ? 'selected' : ''}>${esc(v.nombre)}</option>`).join('')}</select>` : ''}
    <div class="grid"><div><label>Cantidad *</label><input id="fa-c" inputmode="decimal" value="${f ? String(f.cantidad).replace('.', ',') : '1'}"></div>
    <div><label>Nota</label><input id="fa-o" value="${esc(f && f.nota || '')}" placeholder="Urgencia, detalle…"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="fa-ok">Guardar</button><button class="btn" id="fa-no">Cancelar</button></div>`);
  const sel = m.querySelector('#fa-s');
  sel.onchange = () => {
    const nuevo = sel.value === 'nuevo';
    m.querySelector('#fa-nuevo').style.display = nuevo ? 'block' : 'none';
    const p = prods.find(x => String(x.id) === sel.value);
    if (p && p.proveedor_id && m.querySelector('#fa-p')) m.querySelector('#fa-p').value = p.proveedor_id;     // por defecto, el proveedor ya cargado en el producto
    setTimeout(() => (nuevo ? m.querySelector('#fa-n') : m.querySelector('#fa-c')).focus(), 20);
  };
  m.querySelector('#fa-no').onclick = cerrar;
  m.querySelector('#fa-ok').onclick = async () => {
    const err = x => { const e = m.querySelector('#fa-err'); e.textContent = x; e.style.display = 'block'; };
    const p = prods.find(x => String(x.id) === sel.value);
    if (!sel.value) return err('Elegí un producto o «Agregar nuevo».');
    const nombre = p ? p.nombre : m.querySelector('#fa-n').value;
    const j = await api('/api/stock/faltantes', {id: f ? f.id : null, producto_id: p ? p.id : null, proveedor_id: m.querySelector('#fa-p') ? m.querySelector('#fa-p').value : null,
      nombre, cantidad: m.querySelector('#fa-c').value, nota: m.querySelector('#fa-o').value});
    if (!j.ok) return err(j.error || 'No se pudo guardar.');
    location.reload();
  };
  setTimeout(() => sel.focus(), 50);
}
async function falQuitar(id) {
  if (!await confirmar('¿Sacar este faltante de la lista?')) return;
  const j = await api('/api/stock/faltantes/' + id + '/eliminar', {});
  if (!j.ok) return aviso(j.error);
  location.reload();
}
async function falCopiar() {
  const txt = [...document.querySelectorAll('tr[data-copiar]')].map(r => '• ' + r.dataset.copiar).join('\n');
  if (!txt) return aviso('No hay faltantes para copiar.');
  try { await navigator.clipboard.writeText('Faltantes:\n' + txt); await aviso('Lista copiada. Podés pegarla en WhatsApp o donde quieras.'); }
  catch (e) { aviso(txt); }
}
