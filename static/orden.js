const val = id => $('#' + id).value;
const chk = id => $('#' + id).checked;
const fmt = n => { n = Math.round(Number(n) * 100) / 100; const d = Number.isInteger(n) ? 0 : 2; return '$ ' + n.toLocaleString('es-AR', {minimumFractionDigits: d, maximumFractionDigits: d}); };
function num(t) {
  t = String(t || '').replace(/[$\s]/g, '');
  if (!t) return null;
  if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.'); else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, '');
  const n = Number(t); return isNaN(n) ? null : n;
}
function mostrarError(t) { const e = $('#err'); e.textContent = t; e.style.display = t ? 'block' : 'none'; if (t) scrollTo(0, 0); }

function saldo() {
  const m = num(val('monto')), s = num(val('sena')) || 0;
  let t = '—';
  if (ORDEN.resp === 'rechazado' || ORDEN.dev) t = fmt(0);
  else if (m !== null) t = fmt(Math.max(m - s, 0));
  $('#saldo').textContent = t;
}
['monto', 'sena'].forEach(i => $('#' + i).addEventListener('input', saldo));
saldo();

function datos() {
  const d = {};
  ['tipo', 'marca', 'modelo', 'serie', 'clave_acceso', 'descripcion', 'falla', 'accesorios', 'monto', 'sena', 'detalle'].forEach(k => d[k] = val(k));
  d.en_garantia = chk('en_garantia'); d.espera_repuesto = chk('espera_repuesto');
  if ($('#estado')) d.estado = val('estado');
  return d;
}

// ----- bloqueos según respuesta del presupuesto
if (ORDEN.id) {
  if (ORDEN.resp) { $('#monto').disabled = true; $('#detalle').disabled = true; }
  if (ORDEN.resp === 'rechazado') $('#sena').disabled = true;
  if (['Equipo retirado', 'Scrap', 'Equipo comprado'].includes(ORDEN.estado)) $('#espera_repuesto').disabled = true;
}

function textoPresupuesto(id, d) {
  const m = num(d.monto);
  const equipo = [d.tipo, d.marca, d.modelo].filter(x => x && x.trim()).join(' ');
  return `Hola, ${ORDEN.nombre} en ${ORDEN.empresa} hemos actualizado la orden ${String(id).padStart(5, '0')} (${equipo}) ` +
         `Presupuesto: ${(d.detalle || '').trim()} ${m !== null ? fmt(m) : ''}. Esperamos su respuesta.`.replace(/\s+/g, ' ');
}

$('#guardar').onclick = async () => {
  mostrarError('');
  const d = datos();
  let j;
  if (!ORDEN.id) {
    if (!ORDEN.cliente_id) return mostrarError('Elegí el cliente.');
    d.cliente_id = ORDEN.cliente_id; d.equipo_id = $('#prev-equipo')?.value || null;
    j = await api('/api/orden', d);
    if (!j.ok) return mostrarError(j.error);
    if (j.ofrecer_presupuesto) {
      const cl = await buscarCliente(ORDEN.cliente_id);
      const wa = cl ? cl.wa : '';
      ORDEN.nombre = cl ? cl.nombre : '';
      await ofrecerWhatsApp('¿Enviar el presupuesto por WhatsApp?', wa, textoPresupuesto(j.id, d));
    }
    location = '/orden/' + j.id; return;
  }
  j = await apiCodigo('/api/orden/' + ORDEN.id, d);
  if (j.cancelado) return;
  if (!j.ok) return mostrarError(j.error);
  location.reload();
};

async function buscarCliente(id) {
  // nombre de pila y número de WhatsApp para una orden recién creada
  const r = await api('/api/cliente/' + id + '/contacto');
  return r.ok ? r : null;
}

// ----- sugeridos de cliente (orden nueva sin cliente)
const bc = $('#busca-cliente');
if (bc) bc.addEventListener('input', async () => {
  const j = await api('/api/clientes/buscar?q=' + encodeURIComponent(bc.value));
  $('#sugeridos').innerHTML = (j.clientes || []).map(c => `<div><a href="/orden/nueva?cliente=${c.id}">${esc(c.mostrar)}</a> <span class="mut">${esc(c.telefono || '')}</span></div>`).join('');
});
// ----- equipos anteriores
const pe = $('#prev-equipo');
if (pe) api('/api/cliente/' + ORDEN.cliente_id + '/equipos').then(j => {
  const eqs = j.equipos || [];
  eqs.forEach(e => pe.insertAdjacentHTML('beforeend', `<option value="${e.id}">${esc([e.tipo, e.marca, e.modelo].filter(Boolean).join(' '))}</option>`));
  pe.onchange = () => {
    const e = eqs.find(x => String(x.id) === pe.value) || {tipo: 'Notebook', marca: '', modelo: '', serie: '', clave_acceso: '', descripcion: '', en_garantia: 0};
    ['tipo', 'marca', 'modelo', 'serie', 'clave_acceso', 'descripcion'].forEach(k => $('#' + k).value = e[k] || '');
    $('#en_garantia').checked = !!e.en_garantia;
  };
});

// ----- acciones de la orden
async function accion(a, extra) {
  const j = await apiCodigo(`/api/orden/${ORDEN.id}/accion`, {accion: a, ...extra});
  if (j.cancelado) return null;
  if (!j.ok) { mostrarError(j.error); return null; }
  return j;
}
function formulario(titulo, campos, boton) {
  return new Promise(res => {
    const c = modal(`<h2>${esc(titulo)}</h2>${campos.map(f => `<label>${esc(f.t)}</label>${f.area ? `<textarea id="m-${f.k}"></textarea>` : `<input id="m-${f.k}" class="${f.num ? 'moneda' : ''}" inputmode="${f.num ? 'decimal' : 'text'}">`}`).join('')}
      <div class="row" style="margin-top:12px"><button class="btn pri" id="m-ok">${esc(boton)}</button><button class="btn" id="m-no">Cancelar</button></div>`);
    $('#m-no', c).onclick = () => { cerrar(); res(null); };
    $('#m-ok', c).onclick = () => { const o = {}; campos.forEach(f => o[f.k] = $('#m-' + f.k, c).value); cerrar(); res(o); };
  });
}
function botones() {
  const cont = $('#acciones'); if (!cont) return;
  const est = ORDEN.estado, final = ['Scrap', 'Equipo comprado'].includes(est), ret = est === 'Equipo retirado';
  const b = [];
  const add = (t, cls, fn) => b.push([t, cls, fn]);
  if (!ORDEN.resp && !final && !ret) {
    add('El cliente aceptó el presupuesto', 'ok', async () => { if (await accion('aceptar')) location.reload(); });
    add('El cliente rechazó el presupuesto', 'bad', async () => { if (await accion('rechazar')) location.reload(); });
  }
  if (ORDEN.resp) add('Deshacer respuesta del cliente', '', async () => { if (await accion('deshacer')) location.reload(); });
  if (!final && !ret && est !== 'Listo para retirar') add('Marcar listo para retirar', 'ok', async () => { if (await accion('listo')) location.reload(); });
  if (est === 'Listo para retirar') add('Retirar', 'ok', async () => {
    const texto = `Hola, ${ORDEN.nombre}. ${ORDEN.empresa} le informa que su equipo (${ORDEN.equipo_txt}) está listo para ser retirado. Gracias.`;
    await ofrecerWhatsApp('¿Avisar al cliente por WhatsApp?', ORDEN.wa, texto);
    if (await accion('retirar')) location = '/ordenes';
  });
  if (!final) add(ORDEN.dev ? 'Modificar devolución' : 'Registrar devolución', '', async () => {
    const f = await formulario('Devolución de dinero', [{k: 'dev_monto', t: 'Monto a devolver', num: 1}, {k: 'dev_motivo', t: 'Motivo', area: 1}], 'Registrar');
    if (f && await accion('devolver', f)) location.reload();
  });
  if (!final) add('Comprar el equipo', '', async () => {
    const f = await formulario('Compra del equipo', [{k: 'compra_monto', t: 'Monto pagado', num: 1}, {k: 'compra_condiciones', t: 'Condiciones de la compra', area: 1}], 'Confirmar compra');
    if (f && await accion('comprar', f)) location.reload();
  });
  if (est !== 'Scrap' && !ret) add('Pasar a Scrap', 'bad', async () => {
    if (await confirmar('¿Pasar la orden a Scrap?') && await accion('scrap')) location.reload();
  });
  cont.innerHTML = '';
  b.forEach(([t, cls, fn]) => { const e = document.createElement('button'); e.className = 'btn ' + cls; e.textContent = t; e.onclick = fn; cont.appendChild(e); });
}
async function cargarHistorial() {
  const l = $('#hist'); if (!l) return;
  const j = await api(`/api/orden/${ORDEN.id}/historial`);
  $('#hist-tit').textContent = `Historial (${(j.items || []).length})`;
  l.innerHTML = (j.items || []).map(i => `<li><small>${esc(i.fecha)} · ${esc(i.estado)}</small><br>${esc(i.nota)}
    <button class="btn" style="padding:1px 8px;float:right" data-h="${i.id}" title="Borrar línea">×</button></li>`).join('') || '<li class="mut">Sin movimientos.</li>';
  l.querySelectorAll('[data-h]').forEach(bt => bt.onclick = async () => {
    const r = await apiCodigo(`/api/historial/${bt.dataset.h}/borrar`, {});
    if (r.ok) cargarHistorial(); else if (!r.cancelado) mostrarError(r.error);
  });
}
if (ORDEN.id) { botones(); cargarHistorial(); }

// recordar si el historial quedó desplegado
try { const d = $('#hist-det'); if (d) { d.open = localStorage.getItem('hist_abierto') === '1'; d.addEventListener('toggle', () => localStorage.setItem('hist_abierto', d.open ? '1' : '0')); } } catch (e) {}
