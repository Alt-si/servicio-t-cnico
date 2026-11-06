// Faltantes de stock: lista manual y copiar al portapapeles
function falForm(f) {
  const m = modal(`<h2>${f ? 'Editar faltante' : 'Agregar faltante'}</h2><div id="fa-err" class="msg error" style="display:none"></div>
    ${f ? '' : '<label>Buscar en Stock (opcional)</label><input id="fa-q" autocomplete="off" placeholder="Escribí para elegir un producto cargado"><div id="fa-l" style="max-height:140px;overflow:auto;margin:4px 0"></div>'}
    <label>Producto *</label><input id="fa-n" class="mayus" autocomplete="off" value="${esc(f ? f.nombre : '')}" placeholder="Nombre del producto que falta">
    ${window.PROV_ON ? `<label>Proveedor</label><select id="fa-p"><option value="">—</option>${(window.PROVS || []).map(v => `<option value="${v.id}" ${f && f.proveedor_id == v.id ? 'selected' : ''}>${esc(v.nombre)}</option>`).join('')}</select>` : ''}
    <div class="grid"><div><label>Cantidad *</label><input id="fa-c" inputmode="decimal" value="${f ? String(f.cantidad).replace('.', ',') : '1'}"></div>
    <div><label>Nota</label><input id="fa-o" value="${esc(f && f.nota || '')}" placeholder="Proveedor, urgencia…"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="fa-ok">Guardar</button><button class="btn" id="fa-no">Cancelar</button></div>`);
  let pid = null, t = null;
  const q = m.querySelector('#fa-q');
  if (q) q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(async () => {
    const l = m.querySelector('#fa-l'); l.innerHTML = ''; if (!q.value.trim()) return;
    const j = await api('/api/stock/faltantes/buscar?q=' + encodeURIComponent(q.value));
    (j.items || []).forEach(p => { const b = document.createElement('button'); b.type = 'button'; b.className = 'btn'; b.style.cssText = 'display:block;width:100%;text-align:left;margin-bottom:3px';
      b.textContent = `${p.nombre} — stock ${p.stock} ${p.abrev}`; b.onclick = () => { pid = p.id; m.querySelector('#fa-n').value = p.nombre; if (p.proveedor_id && m.querySelector('#fa-p')) m.querySelector('#fa-p').value = p.proveedor_id; l.innerHTML = ''; m.querySelector('#fa-c').focus(); }; l.appendChild(b); });
  }, 250); });
  m.querySelector('#fa-no').onclick = cerrar;
  m.querySelector('#fa-ok').onclick = async () => {
    const j = await api('/api/stock/faltantes', {id: f ? f.id : null, producto_id: pid, proveedor_id: m.querySelector('#fa-p') ? m.querySelector('#fa-p').value : null, nombre: m.querySelector('#fa-n').value, cantidad: m.querySelector('#fa-c').value, nota: m.querySelector('#fa-o').value});
    if (!j.ok) { const e = m.querySelector('#fa-err'); e.textContent = j.error || 'No se pudo guardar.'; e.style.display = 'block'; return; }
    location.reload();
  };
  setTimeout(() => (q || m.querySelector('#fa-n')).focus(), 50);
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
