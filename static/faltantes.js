// Faltantes de stock: lista manual y copiar al portapapeles
async function falForm(f) {
  const todos = ((await api('/api/stock/faltantes/buscar?q=')).items) || [];
  let sel = null, nuevoOn = !!(f && !f.producto_id);      // sel = producto elegido de Stock
  if (f && f.producto_id) sel = todos.find(p => p.id === f.producto_id) || {id: f.producto_id, nombre: f.nombre, proveedor_id: f.proveedor_id};
  const m = modal(`<h2>${f ? 'Editar faltante' : 'Agregar faltante'}</h2><div id="fa-err" class="msg error" style="display:none"></div>
    <div id="fa-prod"><label>Producto *</label>
      <div style="position:relative"><input id="fa-q" autocomplete="off" placeholder="Elegí o buscá por nombre o código ▾" value="${esc(sel ? sel.nombre : '')}">
        <div id="fa-l" style="display:none;position:absolute;left:0;right:0;top:100%;z-index:20;max-height:176px;overflow:auto;background:var(--card);color:var(--tx);border:1px solid var(--bd);border-radius:8px;box-shadow:0 6px 18px #0003"></div></div>
      <a href="#" id="fa-mas" style="display:inline-block;margin-top:4px">＋ ¿No está en Stock? Agregarlo como producto nuevo</a></div>
    <div id="fa-nuevo" style="display:none"><label>Nombre del producto nuevo *</label><input id="fa-n" class="mayus" autocomplete="off" value="${esc(f && !f.producto_id ? f.nombre : '')}" placeholder="Escribí el nombre">
      <a href="#" id="fa-volver">← Elegir un producto de Stock</a></div>
    ${window.PROV_ON ? `<label>Proveedor</label><select id="fa-p"><option value="">—</option>${(window.PROVS || []).map(v => `<option value="${v.id}" ${f && f.proveedor_id == v.id ? 'selected' : ''}>${esc(v.nombre)}</option>`).join('')}</select>` : ''}
    <div class="grid"><div><label>Cantidad *</label><input id="fa-c" inputmode="decimal" value="${f ? String(f.cantidad).replace('.', ',') : '1'}"></div>
    <div><label>Nota</label><input id="fa-o" value="${esc(f && f.nota || '')}" placeholder="Urgencia, detalle…"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="fa-ok">Guardar</button><button class="btn" id="fa-no">Cancelar</button></div>`);
  const q = s => m.querySelector(s), lista = q('#fa-l');
  const norm = x => (x || '').toString().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  const vista = () => { q('#fa-prod').style.display = nuevoOn ? 'none' : 'block'; q('#fa-nuevo').style.display = nuevoOn ? 'block' : 'none'; };
  // distancia de edición (tolera errores de tipeo: «pantala» ~ «pantalla»)
  const dist = (a, b) => { const d = Array.from({length: b.length + 1}, (_, i) => i); for (let i = 1; i <= a.length; i++) { let prev = d[0]; d[0] = i;
    for (let j = 1; j <= b.length; j++) { const t = d[j]; d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1)); prev = t; } } return d[b.length]; };
  const puntaje = (p, ws) => {      // 0 = no coincide; más alto = más parecido
    const hay = norm([p.nombre, p.codigo, ...(p.barras || [])].join(' ')), pal = hay.split(/[^a-z0-9]+/).filter(Boolean);
    let total = 0;
    for (const w of ws) {
      if (hay.includes(w)) { total += pal.some(x => x.startsWith(w)) ? 3 : 2; continue; }
      const tol = w.length > 6 ? 2 : w.length > 3 ? 1 : 0;
      if (tol && pal.some(x => Math.abs(x.length - w.length) <= tol && dist(x, w) <= tol)) { total += 1; continue; }
      return 0;
    }
    return total;
  };
  const mostrar = (filtrar) => {
    const ws = filtrar ? norm(q('#fa-q').value).split(/\s+/).filter(Boolean) : [];
    const items = (ws.length ? todos.map(p => [puntaje(p, ws), p]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).map(x => x[1]) : todos).slice(0, 40);
    lista.innerHTML = '';
    items.forEach(p => {
      const d = document.createElement('div'); d.style.cssText = 'padding:5px 10px;cursor:pointer;border-bottom:1px solid #9992;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:14px';
      d.innerHTML = `<b>${esc(p.nombre)}</b>${p.codigo ? ` <span class="mut">· ${esc(p.codigo)}</span>` : ''} <span class="mut" style="font-size:12px">(stock ${p.stock} ${esc(p.abrev)})</span>`;
      d.onmouseenter = () => d.style.background = '#9992'; d.onmouseleave = () => d.style.background = '';
      d.onmousedown = ev => { ev.preventDefault(); sel = p; q('#fa-q').value = p.nombre; lista.style.display = 'none';
        if (p.proveedor_id && q('#fa-p')) q('#fa-p').value = p.proveedor_id; q('#fa-c').focus(); q('#fa-c').select(); };
      lista.appendChild(d);
    });
    if (!items.length) lista.innerHTML = '<div class="mut" style="padding:8px 10px">No hay productos con ese nombre o código. </div>';
    const mas = document.createElement('div'); mas.id = 'fa-mas2'; mas.style.cssText = 'padding:8px 10px;cursor:pointer;color:var(--brand);font-weight:600';
    mas.textContent = '＋ No está en Stock: agregarlo como producto nuevo'; mas.onmousedown = ev => { ev.preventDefault(); lista.style.display = 'none'; irNuevo(); };
    lista.appendChild(mas);
    lista.style.display = 'block';
  };
  q('#fa-q').addEventListener('click', () => mostrar(!!q('#fa-q').value && !sel));
  q('#fa-q').addEventListener('input', () => { sel = null; mostrar(true); });
  q('#fa-q').addEventListener('blur', () => setTimeout(() => { lista.style.display = 'none'; }, 150));
  const irNuevo = () => { nuevoOn = true; q('#fa-n').value = q('#fa-n').value || (sel ? '' : q('#fa-q').value.toUpperCase()); sel = null; vista(); q('#fa-n').focus(); };
  q('#fa-mas').onclick = ev => { ev.preventDefault(); irNuevo(); };
  q('#fa-volver').onclick = ev => { ev.preventDefault(); nuevoOn = false; vista(); q('#fa-q').focus(); };
  q('#fa-no').onclick = cerrar;
  q('#fa-ok').onclick = async () => {
    const err = x => { const e = q('#fa-err'); e.textContent = x; e.style.display = 'block'; };
    if (!nuevoOn && !sel) return err('Elegí un producto de la lista, o agregalo como nuevo.');
    const nombre = nuevoOn ? q('#fa-n').value : sel.nombre;
    const j = await api('/api/stock/faltantes', {id: f ? f.id : null, producto_id: nuevoOn ? null : sel.id, proveedor_id: q('#fa-p') ? q('#fa-p').value : null,
      nombre, cantidad: q('#fa-c').value, nota: q('#fa-o').value});
    if (!j.ok) return err(j.error || 'No se pudo guardar.');
    location.reload();
  };
  vista();
  if (nuevoOn) setTimeout(() => q('#fa-n').focus(), 50);      // el menú queda cerrado hasta que lo toques o escribas
}
async function falQuitar(id) {
  if (!await confirmar('¿Sacar este faltante de la lista?')) return;
  const j = await api('/api/stock/faltantes/' + id + '/eliminar', {});
  if (!j.ok) return aviso(j.error);
  location.reload();
}
async function falCopiar(btn) {
  // Con el botón de un proveedor copia solo su grupo; sin botón, todo agrupado por proveedor.
  const cuerpos = btn ? [btn.closest('tbody')] : [...document.querySelectorAll('tbody')];
  const partes = cuerpos.map(tb => {
    const filas = [...tb.querySelectorAll('tr[data-copiar]')].map(r => '• ' + r.dataset.copiar);
    if (!filas.length) return '';
    const g = tb.querySelector('tr.grupo b');
    return (g ? g.textContent + ':\n' : '') + filas.join('\n');
  }).filter(Boolean);
  if (!partes.length) return aviso('No hay faltantes para copiar.');
  const txt = (btn ? '' : 'Faltantes:\n\n') + partes.join('\n\n');
  try { await navigator.clipboard.writeText(txt); await aviso('Lista copiada. Podés pegarla en WhatsApp o donde quieras.'); }
  catch (e) { aviso(txt); }
}
