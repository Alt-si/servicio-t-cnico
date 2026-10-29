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
  renglon(); $f('#f-mas').onclick = () => renglon();
  $f('#f-concepto')?.addEventListener('change', () => { $f('#f-serv').style.display = $f('#f-concepto').value === '1' ? 'none' : ''; });
  $f('#f-emitir')?.addEventListener('click', async () => {
    mostrarErr('');
    const b = $f('#f-emitir'); b.disabled = true; b.textContent = 'Pidiendo el CAE a AFIP…';
    const j = await api('/api/facturacion/emitir', {cuenta_id: FACT.cuenta_id, cliente_id: FACT.cliente_id, concepto: $f('#f-concepto').value, fecha: $f('#f-fecha').value,
      serv_desde: $f('#f-desde').value, serv_hasta: $f('#f-hasta').value, vto_pago: $f('#f-vto').value, forma_pago: $f('#f-pago').value, items: leerRenglones()});
    if (j.ok) { location = '/facturacion/comprobante/' + j.id; return; }
    b.disabled = false; b.textContent = 'Reintentar'; mostrarErr(j.error);
  });
  $f('#p-guardar')?.addEventListener('click', async () => {
    mostrarErr('');
    const j = await api('/api/facturacion/presupuesto', {cliente_id: FACT.cliente_id, items: leerRenglones(), validez: $f('#p-validez').value, nota: $f('#p-nota').value});
    if (j.ok) location = '/facturacion/presupuesto/' + j.id; else mostrarErr(j.error);
  });
}
