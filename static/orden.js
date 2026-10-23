const val = id => $('#' + id).value;
const chk = id => $('#' + id).checked;
const fmt = n => { n = Math.round(Number(n) * 100) / 100; const d = Number.isInteger(n) ? 0 : 2; return '$ ' + n.toLocaleString('es-AR', {minimumFractionDigits: d, maximumFractionDigits: d}); };
function num(t) {
  t = String(t || '').replace(/[$\s]/g, '');
  if (!t) return null;
  if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.'); else t = t.replace(/\./g, '');      // sin coma, los puntos son separadores de miles
  const n = Number(t); return isNaN(n) ? null : n;
}
function mostrarError(t) { const e = $('#err'); e.textContent = t; e.style.display = t ? 'block' : 'none'; if (t) scrollTo(0, 0); }

function saldo() {
  const m = num(val('monto')), s = num(val('sena')) || 0;
  let t = '—';
  if (ORDEN.resp === 'rechazado' || ORDEN.dev) t = fmt(0);
  else if (!ORDEN.resp && document.querySelector('.opcion-extra')) t = 'Según la opción elegida';
  else if (m !== null) t = fmt(Math.max(m - s, 0));
  $('#saldo').textContent = t;
}
['monto', 'sena'].forEach(i => $('#' + i).addEventListener('input', e => { aplicarMoneda(e.target); saldo(); }));
saldo();

function datos() {
  const d = {};
  ['tipo', 'marca', 'modelo', 'serie', 'clave_acceso', 'descripcion', 'falla', 'accesorios', 'monto', 'sena', 'detalle'].forEach(k => d[k] = val(k));
  d.opciones = [...document.querySelectorAll('.opcion-extra')].map(b => ({monto: $('.ex-monto', b).value, detalle: $('.ex-detalle', b).value}));
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

// opciones = [{monto: número|null, detalle}]; con una sola opción el texto es el de siempre
function textoPresupuestoOpciones(id, equipo, opciones) {
  const una = o => `${(o.detalle || '').trim()} ${o.monto !== null ? fmt(o.monto) : ''}`.trim();
  const cuerpo = opciones.length === 1 ? una(opciones[0]) : opciones.map((o, i) => `Opción ${i + 1}: ${una(o)}.`).join(' ');
  return `Hola, ${ORDEN.nombre} en ${ORDEN.empresa} hemos actualizado la orden ${String(id).padStart(5, '0')} (${equipo}) ` +
         `Presupuesto: ${cuerpo}${opciones.length === 1 ? '.' : ''} Esperamos su respuesta.`.replace(/\s+/g, ' ');
}
function textoPresupuesto(id, d) {
  const equipo = [d.tipo, d.marca, d.modelo].filter(x => x && x.trim()).join(' ');
  const ops = [{monto: num(d.monto), detalle: d.detalle}].concat((d.opciones || []).map(o => ({monto: num(o.monto), detalle: o.detalle})));
  return textoPresupuestoOpciones(id, equipo, ops);
}

// ----- varias opciones de presupuesto
const bloqueado = !!ORDEN.resp;
function nuevaOpcion(monto, detalle) {
  const n = document.querySelectorAll('.opcion-extra').length + 2;
  const b = document.createElement('div'); b.className = 'opcion-extra';
  b.innerHTML = `<div class="row"><b class="ex-tit">Opción ${n}</b>${bloqueado ? '' : '<button type="button" class="btn ex-quitar der" title="Quitar esta opción">✕</button>'}</div>
    <div class="grid"><div><label>Presupuesto ($)</label><input class="ex-monto moneda" inputmode="decimal"></div></div>
    <label>Detalle</label><textarea class="ex-detalle mayus"></textarea>`;
  $('.ex-monto', b).value = monto || ''; $('.ex-detalle', b).value = detalle || '';
  if (bloqueado) { $('.ex-monto', b).disabled = true; $('.ex-detalle', b).disabled = true; }
  else $('.ex-quitar', b).onclick = () => { b.remove(); renumerar(); };
  $('#opciones-extra').appendChild(b); renumerar();
  return b;
}
function renumerar() {
  const bs = [...document.querySelectorAll('.opcion-extra')];
  bs.forEach((b, i) => $('.ex-tit', b).textContent = `Opción ${i + 2}`);
  $('#lbl-detalle').textContent = bs.length ? 'Detalle de la opción 1' : 'Detalle del presupuesto';
  $('#mas-opcion').style.display = (bloqueado || bs.length >= 4) ? 'none' : '';
  saldo();
}
$('#mas-opcion').onclick = () => nuevaOpcion('', '').querySelector('.ex-detalle').focus();
(ORDEN.extra || []).forEach(o => nuevaOpcion(num_ar(o.monto), o.detalle));
function num_ar(v) { v = Math.round(Number(v) * 100) / 100; return v.toLocaleString('es-AR', {minimumFractionDigits: Number.isInteger(v) ? 0 : 2, maximumFractionDigits: 2}); }

function textoListo(equipo) {
  return `Hola, ${ORDEN.nombre}. ${ORDEN.empresa} le informa que su equipo (${equipo}) está listo para ser retirado. Gracias.`;
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
    const tk = await api(`/api/orden/${j.id}/ticket`);
    if (tk.ok) {
      ORDEN.nombre = tk.nombre;
      await ofrecerWhatsApp('¿Enviar el comprobante de ingreso por WhatsApp?', tk.wa, tk.texto);
    }
    if (j.ofrecer_presupuesto && tk.ok) await ofrecerWhatsApp('¿Enviar el presupuesto por WhatsApp?', tk.wa, textoPresupuesto(j.id, d));
    location = '/ordenes'; return;
  }
  j = await apiCodigo('/api/orden/' + ORDEN.id, d);
  if (j.cancelado) return;
  if (!j.ok) return mostrarError(j.error);
  if (j.ofrecer_presupuesto) await ofrecerWhatsApp('La orden pasó a Pendiente: ¿enviar el presupuesto por WhatsApp?', ORDEN.wa, textoPresupuesto(ORDEN.id, d));
  if (j.estado === 'Listo para retirar' && ORDEN.estado !== 'Listo para retirar')
    await ofrecerWhatsApp('La orden está lista: ¿avisar al cliente por WhatsApp?', ORDEN.wa, textoListo([d.tipo, d.marca, d.modelo].filter(x => x && x.trim()).join(' ')));
  location = '/ordenes';
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
  $('#sugeridos').innerHTML = (j.clientes || []).map(c => `<div><a href="/orden/nueva?cliente=${c.id}">${esc(c.mostrar)}</a> <span class="mut">${esc(c.telefono || '')} · ${esc(c.condicion_iva || '')}</span></div>`).join('');
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
    const c = modal(`<h2>${esc(titulo)}</h2>${campos.map(f => `<label>${esc(f.t)}</label>${f.area ? `<textarea id="m-${f.k}" class="mayus"></textarea>` : `<input id="m-${f.k}" class="${f.num ? 'moneda' : ''}" inputmode="${f.num ? 'decimal' : 'text'}">`}`).join('')}
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
  add('Enviar comprobante de ingreso', 'wa', async () => {
    const tk = await api(`/api/orden/${ORDEN.id}/ticket`);
    if (tk.ok) await ofrecerWhatsApp('Comprobante de ingreso por WhatsApp', tk.wa, tk.texto);
  });
  if (ORDEN.monto !== null && ORDEN.monto > 0 && !final && !ret) add('Enviar presupuesto', 'wa', async () => {
    const texto = textoPresupuestoOpciones(ORDEN.id, ORDEN.equipo_txt,
      [{monto: ORDEN.monto, detalle: ORDEN.detalle}].concat((ORDEN.extra || []).map(o => ({monto: o.monto, detalle: o.detalle}))));
    await ofrecerWhatsApp('Enviar presupuesto por WhatsApp', ORDEN.wa, texto);
  });
  if (!ORDEN.resp && !final && !ret) {
    add('El cliente aceptó el presupuesto', 'ok', async () => {
      let opcion;
      if ((ORDEN.extra || []).length) {
        const todas = [{monto: ORDEN.monto, detalle: ORDEN.detalle}].concat(ORDEN.extra);
        opcion = await new Promise(res => {
          const c = modal(`<h2>¿Qué opción aceptó el cliente?</h2>${todas.map((o, i) => `<label class="modulo"><input type="radio" name="op" value="${i + 1}" ${i === 0 ? 'checked' : ''}><span><b>Opción ${i + 1}: ${esc(fmt(o.monto))}</b><br><span class="mut">${esc(o.detalle || '')}</span></span></label>`).join('')}
            <div class="row" style="margin-top:12px"><button class="btn pri" id="op-ok">Aceptar</button><button class="btn" id="op-no">Cancelar</button></div>`);
          $('#op-ok', c).onclick = () => { const v = c.querySelector('input[name=op]:checked').value; cerrar(); res(Number(v)); };
          $('#op-no', c).onclick = () => { cerrar(); res(null); };
        });
        if (!opcion) return;
      }
      if (await accion('aceptar', {opcion})) location.reload();
    });
    add('El cliente rechazó el presupuesto', 'bad', async () => { if (await accion('rechazar')) location.reload(); });
  }
  if (ORDEN.resp) add('Deshacer respuesta del cliente', '', async () => { if (await accion('deshacer')) location.reload(); });
  if (!final && !ret && est !== 'Listo para retirar') add('Marcar listo para retirar', 'ok', async () => {
    if (!await accion('listo')) return;
    await ofrecerWhatsApp('La orden está lista: ¿avisar al cliente por WhatsApp?', ORDEN.wa, textoListo(ORDEN.equipo_txt));
    location.reload();
  });
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
