// Faltantes de stock: lista manual y copiar al portapapeles
async function falForm(f) {
  let sel = null, nuevoOn = !!(f && !f.producto_id), t = null;      // sel = producto elegido de Stock
  if (f && f.producto_id) sel = {id: f.producto_id, nombre: f.nombre, proveedor_id: f.proveedor_id};
  const m = modal(`<h2>${f ? 'Editar faltante' : 'Agregar faltante'}</h2><div id="fa-err" class="msg error" style="display:none"></div>
    <label>Producto *</label>
    <div id="fa-elegido" class="mut" style="display:none;margin:2px 0 6px"></div>
    <div id="fa-buscar"><input id="fa-q" autocomplete="off" placeholder="Buscar por nombre o código…"><div id="fa-l" style="max-height:170px;overflow:auto;margin:4px 0"></div>
      <a href="#" id="fa-mas">＋ ¿No está cargado? Agregarlo como producto nuevo</a></div>
    <div id="fa-nuevo" style="display:none;margin-top:6px"><label>Nombre del producto nuevo *</label><input id="fa-n" class="mayus" autocomplete="off" value="${esc(f && !f.producto_id ? f.nombre : '')}" placeholder="Escribí el nombre">
      <a href="#" id="fa-volver">← Buscar en Stock</a></div>
    ${window.PROV_ON ? `<label>Proveedor</label><select id="fa-p"><option value="">—</option>${(window.PROVS || []).map(v => `<option value="${v.id}" ${f && f.proveedor_id == v.id ? 'selected' : ''}>${esc(v.nombre)}</option>`).join('')}</select>` : ''}
    <div class="grid"><div><label>Cantidad *</label><input id="fa-c" inputmode="decimal" value="${f ? String(f.cantidad).replace('.', ',') : '1'}"></div>
    <div><label>Nota</label><input id="fa-o" value="${esc(f && f.nota || '')}" placeholder="Urgencia, detalle…"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="fa-ok">Guardar</button><button class="btn" id="fa-no">Cancelar</button></div>`);
  const q = s => m.querySelector(s);
  const vista = () => {
    q('#fa-buscar').style.display = !sel && !nuevoOn ? 'block' : 'none';
    q('#fa-nuevo').style.display = nuevoOn && !sel ? 'block' : 'none';
    const e = q('#fa-elegido');
    e.style.display = sel ? 'block' : 'none';
    e.innerHTML = sel ? `<b style="color:var(--texto,inherit)">${esc(sel.nombre)}</b> <a href="#" id="fa-cambiar">cambiar</a>` : '';
    if (sel) q('#fa-cambiar').onclick = ev => { ev.preventDefault(); sel = null; vista(); q('#fa-q').focus(); buscar(); };
  };
  const buscar = async () => {
    const j = await api('/api/stock/faltantes/buscar?q=' + encodeURIComponent(q('#fa-q').value));
    const l = q('#fa-l'); l.innerHTML = '';
    const items = (j.items || []).slice(0, 30);
    items.forEach(p => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'btn'; b.style.cssText = 'display:block;width:100%;text-align:left;margin-bottom:3px';
      b.textContent = `${p.nombre}${p.codigo ? ' · ' + p.codigo : ''} — stock ${p.stock} ${p.abrev}`;
      b.onclick = () => { sel = p; nuevoOn = false; if (p.proveedor_id && q('#fa-p')) q('#fa-p').value = p.proveedor_id; vista(); q('#fa-c').focus(); q('#fa-c').select(); };
      l.appendChild(b);
    });
    if (!items.length) l.innerHTML = '<p class="mut" style="margin:4px 0">No hay productos con ese nombre o código.</p>';
  };
  q('#fa-q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(buscar, 200); });
  q('#fa-mas').onclick = ev => { ev.preventDefault(); nuevoOn = true; q('#fa-n').value = q('#fa-n').value || q('#fa-q').value.toUpperCase(); vista(); q('#fa-n').focus(); };
  q('#fa-volver').onclick = ev => { ev.preventDefault(); nuevoOn = false; vista(); q('#fa-q').focus(); };
  q('#fa-no').onclick = cerrar;
  q('#fa-ok').onclick = async () => {
    const err = x => { const e = q('#fa-err'); e.textContent = x; e.style.display = 'block'; };
    if (!sel && !nuevoOn) return err('Buscá y elegí un producto, o agregalo como nuevo.');
    const nombre = sel ? sel.nombre : q('#fa-n').value;
    const j = await api('/api/stock/faltantes', {id: f ? f.id : null, producto_id: sel ? sel.id : null, proveedor_id: q('#fa-p') ? q('#fa-p').value : null,
      nombre, cantidad: q('#fa-c').value, nota: q('#fa-o').value});
    if (!j.ok) return err(j.error || 'No se pudo guardar.');
    location.reload();
  };
  vista();
  if (!sel && !nuevoOn) { buscar(); setTimeout(() => q('#fa-q').focus(), 50); }
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
