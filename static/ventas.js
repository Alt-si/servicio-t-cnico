// Ventas: caja simple (lector de código de barras, buscador, formas de pago)
const $v = s => document.querySelector(s);
const money = v => '$ ' + Number(v || 0).toLocaleString('es-AR', {maximumFractionDigits: 2});
const numAr = s => { s = String(s ?? '').trim(); if (!s) return null; const v = parseFloat(s.includes(',') ? s.replace(/\./g, '').replace(',', '.') : s.replace(/\./g, '')); return isNaN(v) ? null : v; };
let carrito = [], forma = null;

function precioItem(it) {
  if (it.manual) return it.precioManual;
  if (it.precio === null) return null;
  const d = (forma && forma.desc && it.desc) ? it.desc : 0;
  return Math.round(it.precio * (1 - d / 100) * 100) / 100;
}
function totales() {
  let t = 0, sinPrecio = false, ahorro = 0;
  carrito.forEach(it => { const p = precioItem(it); if (p === null) { sinPrecio = true; return; } t += Math.round(p * it.cant * 100) / 100;
    if (!it.manual && it.precio !== null) ahorro += (it.precio - p) * it.cant; });
  return {t: Math.round(t * 100) / 100, sinPrecio, ahorro: Math.round(ahorro * 100) / 100};
}
function dibujar() {
  const tb = $v('#v-items');
  if (!carrito.length) tb.innerHTML = '<tr><td colspan="5" class="mut">Todavía no agregaste productos.</td></tr>';
  else tb.innerHTML = carrito.map((it, i) => {
    const p = precioItem(it), sub = p === null ? null : p * it.cant, falta = it.cant > it.stock + 1e-9;
    return `<tr><td><b>${esc(it.nombre)}</b><div class="mut" style="font-size:12px">${esc(it.codigo)} · stock ${it.stock.toLocaleString('es-AR')} ${esc(it.abrev)}${falta ? ' <b style="color:#b91c1c">· supera el stock</b>' : ''}</div></td>
      <td><input data-i="${i}" data-k="cant" value="${String(it.cant).replace('.', ',')}" inputmode="decimal" style="width:80px"> ${esc(it.abrev)}</td>
      <td style="text-align:right"><input data-i="${i}" data-k="precio" value="${p === null ? '' : p.toLocaleString('es-AR', {maximumFractionDigits: 2})}" placeholder="sin precio" inputmode="decimal" style="width:110px;text-align:right${p === null ? ';border-color:#dc2626' : ''}"></td>
      <td style="text-align:right"><b>${sub === null ? '—' : money(sub)}</b></td><td><button class="btn" data-quitar="${i}" title="Quitar">✕</button></td></tr>`; }).join('');
  const {t, sinPrecio, ahorro} = totales();
  $v('#v-total').textContent = money(t);
  $v('#v-desc').textContent = ahorro > 0 ? `Incluye descuento por ${forma.nombre.toLowerCase()}: −${money(ahorro)}` : '';
  $v('#v-ok').disabled = !carrito.length || !forma || sinPrecio;
  vuelto();
}
function vuelto() {
  const r = numAr($v('#v-recibido').value), t = totales().t;
  $v('#v-vuelto').textContent = r === null ? '$ 0' : money(Math.max(r - t, 0));
}
function agregar(p) {
  const ya = carrito.find(x => x.id === p.id);
  if (ya) ya.cant = Math.round((ya.cant + 1) * 1000) / 1000;
  else carrito.push({...p, cant: 1, manual: false, precioManual: null});
  $v('#v-res').style.display = 'none'; $v('#v-scan').value = ''; $v('#v-scan').focus(); dibujar();
}
let temporizador = null;
async function buscar(enter) {
  const q = $v('#v-scan').value.trim(), caja = $v('#v-res');
  if (!q) { caja.style.display = 'none'; return; }
  const j = await api('/api/ventas/buscar?q=' + encodeURIComponent(q));
  if (!j.ok) return;
  if (j.exacto && enter) return agregar(j.exacto);
  const lista = j.exacto ? [j.exacto] : j.resultados;
  if (enter && lista.length === 1) return agregar(lista[0]);
  caja.innerHTML = lista.length ? lista.map((p, i) => `<div class="v-op" data-i="${i}"><b>${esc(p.nombre)}</b> <span class="mut">${esc(p.codigo)} · stock ${p.stock.toLocaleString('es-AR')} ${esc(p.abrev)}</span><span style="float:right">${p.precio === null ? 'sin precio' : money(p.precio)}</span></div>`).join('')
    : '<div class="v-op mut">No se encontró ningún producto.</div>';
  caja.style.display = 'block';
  caja.querySelectorAll('[data-i]').forEach(e => e.onclick = () => agregar(lista[+e.dataset.i]));
}
if ($v('#v-scan')) {
  $v('#v-scan').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(temporizador); buscar(true); } });
  $v('#v-scan').addEventListener('input', () => { clearTimeout(temporizador); temporizador = setTimeout(() => buscar(false), 250); });
  document.addEventListener('click', e => { if (!e.target.closest('#v-res') && e.target.id !== 'v-scan') $v('#v-res').style.display = 'none'; });
  $v('#v-items').addEventListener('change', e => {
    const i = e.target.dataset.i; if (i === undefined) return; const it = carrito[+i];
    if (e.target.dataset.k === 'cant') { const n = numAr(e.target.value); if (n && n > 0) it.cant = n; }
    else { const n = numAr(e.target.value); if (n === null) { it.manual = false; it.precioManual = null; } else { it.manual = true; it.precioManual = n; } }
    dibujar();
  });
  $v('#v-items').addEventListener('click', e => { const q = e.target.closest('[data-quitar]'); if (q) { carrito.splice(+q.dataset.quitar, 1); dibujar(); } });
  document.querySelectorAll('.v-forma').forEach(b => b.onclick = () => {
    forma = {id: +b.dataset.id, nombre: b.dataset.nombre, desc: b.dataset.desc === '1'};
    document.querySelectorAll('.v-forma').forEach(x => x.classList.toggle('pri', x === b));
    $v('#v-efectivo').style.display = forma.nombre.toLowerCase().startsWith('efectivo') ? 'block' : 'none';
    dibujar();
  });
  $v('#v-recibido').addEventListener('input', vuelto);
  $v('#v-limpiar').onclick = () => { carrito = []; dibujar(); $v('#v-scan').focus(); };
  $v('#v-ok').onclick = async () => {
    const err = $v('#v-err'); err.style.display = 'none';
    const j = await api('/api/ventas/confirmar', {forma_id: forma.id, recibido: $v('#v-efectivo').style.display === 'block' ? $v('#v-recibido').value : '',
      items: carrito.map(it => ({id: it.id, cantidad: String(it.cant).replace('.', ','), manual: it.manual, precio: it.manual ? String(it.precioManual).replace('.', ',') : ''}))});
    if (!j.ok) { err.textContent = j.error; err.style.display = 'block'; return; }
    carrito = []; $v('#v-recibido').value = ''; dibujar();
    aviso(`Venta N° ${j.id} registrada por ${money(j.total)}.` + (j.vuelto ? ` Vuelto: ${money(j.vuelto)}.` : ''));
    $v('#v-scan').focus();
  };
  dibujar();
}

// Historial: detalle y anulación
async function ventaDetalle(id) {
  const j = await api('/api/ventas/' + id);
  if (!j.ok) return aviso(j.error);
  const v = j.venta;
  const c = modal(`<h2>Venta N° ${v.id}${v.anulada ? ' (ANULADA)' : ''}</h2><div class="mut">${esc(j.fecha_txt)} · ${esc(v.forma || '')}</div>
    <table style="margin-top:8px"><tbody>${j.items.map(i => `<tr><td>${esc(i.nombre)}${i.descuento_pct ? ` <span class="mut">(−${i.descuento_pct}%)</span>` : ''}</td><td>${esc(i.cant_txt)} ${esc(i.abrev || '')}</td><td style="text-align:right">${money(i.precio_unit)}</td><td style="text-align:right"><b>${money(i.subtotal)}</b></td></tr>`).join('')}</tbody></table>
    <p style="text-align:right;font-size:18px"><b>Total ${money(v.total)}</b>${v.recibido ? `<br><span class="mut" style="font-size:13px">Recibido ${money(v.recibido)} · Vuelto ${money(v.vuelto)}</span>` : ''}</p>
    <div class="row">${v.anulada ? '' : '<button class="btn bad" id="vd-anular">Anular venta</button>'}<button class="btn der" id="vd-cerrar">Cerrar</button></div>`);
  c.querySelector('#vd-cerrar').onclick = cerrar;
  const a = c.querySelector('#vd-anular');
  if (a) a.onclick = async () => {
    if (!await confirmar('¿Anular la venta? Los productos vuelven al stock.')) return;
    const r = await api(`/api/ventas/${id}/anular`, {}); if (r.ok) location.reload(); else aviso(r.error);
  };
}
