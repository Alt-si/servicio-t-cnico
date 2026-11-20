// Facturación: renglones del comprobante / presupuesto
const $f = s => document.querySelector(s);
const nAr = s => { s = String(s ?? '').trim(); if (!s) return 0; const v = parseFloat(s.includes(',') ? s.replace(/\./g, '').replace(',', '.') : s.replace(/\./g, '')); return isNaN(v) ? 0 : v; };
const dinero = v => '$ ' + Number(v || 0).toLocaleString('es-AR', {maximumFractionDigits: 2});

function renglon(d) {
  d = d || {};
  const tr = document.createElement('tr');
  tr.innerHTML = `<td><input class="r-desc mayus" value="${esc(d.descripcion || '')}"></td><td><input class="r-cant" inputmode="decimal" value="${d.cantidad || 1}"></td>
    <td><input class="r-precio moneda" inputmode="decimal" value="${d.precio || ''}"></td>
    ${FACT.ri ? `<td><select class="r-iva">${FACT.alicuotas.map(a => `<option ${a === 21 ? 'selected' : ''}>${String(a).replace('.', ',')}</option>`).join('')}</select></td>` : ''}
    <td class="r-sub" style="text-align:right"><b>$ 0</b></td><td><button type="button" class="btn r-quitar" title="Quitar">✕</button></td>`;
  tr.querySelector('.r-quitar').onclick = () => { tr.remove(); totales(); };
  tr.addEventListener('input', totales);
  $f('#f-items').appendChild(tr); totales();
  return tr;
}
function totales() {
  let t = 0;
  document.querySelectorAll('#f-items tr').forEach(tr => { const s = nAr(tr.querySelector(".r-cant").value) * nAr(tr.querySelector('.r-precio').value); t += s; tr.querySelector('.r-sub').innerHTML = `<b>${dinero(s)}</b>`; });
  $f('#f-total').textContent = dinero(t);
}
function leerRenglones() {
  return [...document.querySelectorAll('#f-items tr')].map(tr => ({descripcion: tr.querySelector('.r-desc').value, cantidad: tr.querySelector('.r-cant').value,
    precio: tr.querySelector('.r-precio').value, iva: tr.querySelector('.r-iva')?.value.replace(',', '.') || ''}));
}
function mostrarErr(t) { const e = $f('#err'); e.textContent = t; e.style.display = t ? 'block' : 'none'; if (t) scrollTo(0, 0); }

if ($f('#f-items')) {
  if (FACT.edit && FACT.edit.items.length) FACT.edit.items.forEach(i => renglon(i)); else renglon();
  $f('#f-mas').onclick = () => renglon();
  $f('#f-concepto')?.addEventListener('change', () => { $f('#f-serv').style.display = $f('#f-concepto').value === '1' ? 'none' : ''; });
  $f('#f-emitir')?.addEventListener('click', async () => {
    mostrarErr('');
    const b = $f('#f-emitir'); b.disabled = true; b.textContent = 'Pidiendo el CAE a AFIP…';
    const j = await api('/api/facturacion/emitir', {cuenta_id: FACT.cuenta_id, cliente_id: FACT.cliente_id, concepto: $f('#f-concepto').value, fecha: $f('#f-fecha').value,
      serv_desde: $f('#f-desde').value, serv_hasta: $f('#f-hasta').value, vto_pago: $f('#f-vto').value, forma_pago: $f('#f-pago').value, items: leerRenglones()});
    if (j.ok) { location = '/facturacion/comprobante/' + j.id + '?nuevo=1'; return; }
    b.disabled = false; b.textContent = 'Reintentar'; mostrarErr(j.error);
  });
  $f('#p-validez')?.addEventListener('click', e => { try { e.target.showPicker(); } catch (x) {} });
  $f('#p-modo')?.addEventListener('change', () => {
    const t = $f('#p-modo').value === 'total';
    $f('#p-tabla').classList.toggle('modo-total', t);
    $f('#tot-calc').style.display = t ? 'none' : ''; $f('#tot-fijo').style.display = t ? '' : 'none'; $f('#p-precios-l').style.display = t ? 'none' : '';
  });
  $f('#p-guardar')?.addEventListener('click', async () => {
    mostrarErr('');
    if (!FACT.cliente_id) return mostrarErr('Elegí el cliente o creá uno nuevo.');
    const unico = $f('#p-modo').value === 'total';
    const j = await api('/api/facturacion/presupuesto', {id: FACT.edit?.id, cliente_id: FACT.cliente_id, items: leerRenglones(), total_unico: unico ? $f('#p-total').value : '', aviso: $f('#p-aviso').value, validez: $f('#p-validez').value, nota: $f('#p-nota').value, forma_pago: $f('#p-forma').value, mostrar_precios: $f('#p-precios').checked});
    if (j.ok) location = '/facturacion/presupuesto/' + j.id + (FACT.edit ? '' : '?nuevo=1'); else mostrarErr(j.error);
  });
}

// Edición de un presupuesto: carga los datos guardados
if (FACT.edit) {
  const e = FACT.edit;
  $f('#p-forma').value = e.forma_pago; $f('#p-precios').checked = e.mostrar_precios; $f('#p-validez').value = /^\d{4}-\d{2}-\d{2}$/.test(e.validez) ? e.validez : ''; $f('#p-nota').value = e.nota;
  if (e.modo === 'total') { $f('#p-modo').value = 'total'; $f('#p-total').value = String(e.total).replace('.', ','); $f('#p-modo').dispatchEvent(new Event('change')); }
}


// Presupuesto sin cliente elegido: buscar uno cargado o crearlo en el momento
if (FACT.elegir) {
  const elegir = (id, nombre) => {
    FACT.cliente_id = id; $f('#pc-nombre').textContent = nombre;
    $f('#pc-sel').style.display = 'flex'; $f('#pc-buscar').style.display = 'none'; $f('#pc-res').innerHTML = '';
    document.querySelector('h1').textContent = 'Presupuesto para ' + nombre;
  };
  window.alCrearCliente = elegir;
  let t = null;
  $f('#pc-q').addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const q = $f('#pc-q').value.trim(); if (q.length < 2) { $f('#pc-res').innerHTML = ''; return; }
      const j = await api('/api/clientes/buscar?q=' + encodeURIComponent(q));
      $f('#pc-res').innerHTML = j.ok && j.clientes.length ? j.clientes.map(c => `<a href="#" class="btn" style="display:block;text-align:left;margin:0 0 4px" data-id="${c.id}" data-n="${esc(c.mostrar)}">${esc(c.mostrar)}${c.telefono ? ' · ' + esc(c.telefono) : ''}</a>`).join('')
        : '<span class="mut">No hay clientes con ese dato. Podés crearlo con «+ Crear cliente».</span>';
    }, 250);
  });
  $f('#pc-res').addEventListener('click', e => { const a = e.target.closest('a[data-id]'); if (!a) return; e.preventDefault(); elegir(+a.dataset.id, a.dataset.n); });
  $f('#pc-cambiar').onclick = e => { e.preventDefault(); FACT.cliente_id = null; $f('#pc-sel').style.display = 'none'; $f('#pc-buscar').style.display = 'flex'; $f('#pc-q').value = ''; $f('#pc-q').focus(); };
  $f('#pc-nuevo').onclick = () => clienteForm();
}


// Presupuesto y factura: al escribir un renglón se sugieren los productos cargados en Stock (con su precio); si no está, se escribe libre
if (FACT.stock && $f('#f-items')) {
  const panel = document.createElement('div');
  panel.style.cssText = 'display:none;position:fixed;z-index:70;background:var(--card);border:1px solid var(--bd);border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.28);padding:4px;max-height:260px;overflow:auto';
  document.body.appendChild(panel);
  let t = null, fila = null;
  const ocultar = () => { panel.style.display = 'none'; };
  document.addEventListener('click', e => { if (!panel.contains(e.target) && !e.target.classList.contains('r-desc')) ocultar(); });
  addEventListener('scroll', ocultar, true);
  $f('#f-items').addEventListener('input', e => {
    if (!e.target.classList.contains('r-desc')) return;
    const inp = e.target; fila = inp.closest('tr'); clearTimeout(t);
    t = setTimeout(async () => {
      const q = inp.value.trim(); if (q.length < 2) return ocultar();
      const j = await api('/api/facturacion/productos?q=' + encodeURIComponent(q));
      if (!j.ok || !j.items.length || inp.value.trim() !== q) return ocultar();
      const r = inp.getBoundingClientRect();
      panel.style.left = r.left + 'px'; panel.style.top = (r.bottom + 2) + 'px'; panel.style.width = Math.max(r.width, 320) + 'px';
      panel.innerHTML = '<div class="mut" style="font-size:12px;padding:2px 6px">En stock — tocá uno para cargarlo, o seguí escribiendo</div>' + j.items.map(p => `<a href="#" class="btn" style="display:block;text-align:left;margin:2px 0;white-space:normal" data-p='${esc(JSON.stringify(p))}'>${esc(p.nombre)}${p.codigo ? ' · ' + esc(p.codigo) : ''}<br><small class="mut">${p.precio ? dinero(p.precio) : 'sin precio'} · hay ${p.stock} ${esc(p.abrev || '')}</small></a>`).join('');
      panel.style.display = 'block';
    }, 250);
  });
  panel.addEventListener('click', e => {
    const a = e.target.closest('a[data-p]'); if (!a || !fila) return; e.preventDefault();
    const p = JSON.parse(a.dataset.p);
    fila.querySelector('.r-desc').value = p.nombre;
    if (p.precio) fila.querySelector('.r-precio').value = String(p.precio).replace('.', ',');
    totales(); ocultar(); fila.querySelector('.r-cant').focus(); fila.querySelector('.r-cant').select();
  });
}
