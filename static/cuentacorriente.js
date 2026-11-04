// Cuenta corriente: ventas / compras a cuenta, pagos parciales y vencimientos
const ccNum = v => Number(v).toLocaleString('es-AR', {minimumFractionDigits: Number.isInteger(Number(v)) ? 0 : 2, maximumFractionDigits: 2});
const ccSumar = (iso, dias) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + dias); return d.toISOString().slice(0, 10); };
const ccEtiq = t => t === 'cliente' ? {persona: 'Cliente', pagado: 'cobrado', accion: 'Cobrar'} : {persona: 'Proveedor', pagado: 'pagado', accion: 'Pagar'};

// Estado del formulario: se conserva al ir al buscador de productos y volver
function ccFormulario(tipo, d, est) {
  const L = ccEtiq(tipo), nuevo = !d;
  est = est || {persona: d ? d.persona : '', emision: d ? d.emision : CC_HOY, obs: d ? d.obs || '' : '', numero: d ? d.numero || '' : '', sinf: d ? !!d.sin_factura : false,
    total: d && d.total > 0 ? ccNum(d.total) : '', vence: d && d.vence ? d.vence : '', ini: '', items: (d && d.items) ? d.items.map(i => ({...i})) : []};
  const sumItems = () => est.items.reduce((s, i) => s + i.cantidad * i.precio, 0);
  const m = modal(`<h2>${nuevo ? 'Nueva cuenta corriente' : 'Editar'} · ${L.persona}</h2><div id="cc-err" class="msg error" style="display:none"></div>
    <label>${L.persona} *</label><input id="cc-per" list="cc-nombres" autocomplete="off" value="${esc(est.persona)}">
    <label>Fecha *</label><input id="cc-em" type="date" value="${est.emision}">
    <label>${tipo === 'cliente' ? 'Qué retiró / servicio que se le realizó' : 'Qué se compró'} *</label><textarea id="cc-obs" rows="3">${esc(est.obs)}</textarea>
    ${CC_STOCK ? `<div style="margin:6px 0"><b>Productos del stock</b> <button type="button" class="btn" id="cc-addp" style="padding:2px 10px">+ Agregar producto</button>
      ${est.items.length ? `<table class="tabla" style="width:100%;margin-top:6px">${est.items.map((i, n) => `<tr><td>${esc(i.nombre)}</td><td>${ccNum(i.cantidad)} ${esc(i.abrev || '')}</td><td style="text-align:right">$ ${ccNum(i.cantidad * i.precio)}</td><td><button type="button" class="btn" style="padding:1px 8px" data-q="${n}">×</button></td></tr>`).join('')}
      <tr><td colspan="2" style="text-align:right">Total productos</td><td style="text-align:right"><b>$ ${ccNum(sumItems())}</b></td><td></td></tr></table>` : '<div class="mut" style="font-size:13px">Descuentan del stock al guardar.</div>'}</div>` : ''}
    <hr><label>N° de factura <span class="mut">(o tildá «Sin factura»: se carga igual el importe y el vencimiento, y después se puede poner la factura si el cliente la pide)</span></label><div class="row"><input id="cc-num" autocomplete="off" value="${esc(est.numero)}" style="flex:1" ${est.sinf ? 'disabled' : ''}>
      <label style="display:flex;align-items:center;gap:6px;margin:0;white-space:nowrap"><input type="checkbox" id="cc-sf" ${est.sinf ? 'checked' : ''}> Sin factura</label></div>
    <div id="cc-fact" style="display:none">
      <div class="grid"><div><label>Importe total ($) *</label><input id="cc-tot" class="moneda" inputmode="decimal" value="${esc(est.total)}"></div>
      <div><label>Vencimiento *</label><input id="cc-ve" type="date" value="${est.vence}"></div></div>
      <div class="row" style="margin:4px 0"><span class="mut">Plazo:</span>${[7, 15, 30, 60].map(n => `<button type="button" class="btn" data-p="${n}" style="padding:2px 8px">${n} días</button>`).join('')}</div>
      ${nuevo ? `<label>Ya ${L.pagado} (opcional)</label><input id="cc-ini" class="moneda" inputmode="decimal" value="${esc(est.ini)}" placeholder="0">` : ''}
    </div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="cc-ok">Guardar</button><button class="btn" id="cc-no">Cancelar</button></div>`);
  const q = s => m.querySelector(s);
  const leer = () => { est.persona = q('#cc-per').value; est.emision = q('#cc-em').value; est.obs = q('#cc-obs').value; est.numero = q('#cc-sf').checked ? '' : q('#cc-num').value; est.sinf = q('#cc-sf').checked;
    est.total = q('#cc-tot').value; est.vence = q('#cc-ve').value; if (q('#cc-ini')) est.ini = q('#cc-ini').value; };
  const mostrar = () => {
    const con = q('#cc-sf').checked || q('#cc-num').value.trim() !== '';
    q('#cc-fact').style.display = con ? 'block' : 'none';
    if (con) {
      if (!q('#cc-tot').value && sumItems() > 0) { q('#cc-tot').value = ccNum(sumItems()); }
      if (!q('#cc-ve').value) q('#cc-ve').value = ccSumar(q('#cc-em').value || CC_HOY, 7);
    }
  };
  q('#cc-num').addEventListener('input', mostrar);
  q('#cc-sf').addEventListener('change', () => { const n = q('#cc-num'); n.disabled = q('#cc-sf').checked; if (n.disabled) n.value = ''; mostrar(); });
  mostrar();
  ['cc-em', 'cc-ve'].forEach(i => q('#' + i).addEventListener('click', ev => { try { ev.target.showPicker(); } catch (x) {} }));
  m.querySelectorAll('[data-p]').forEach(b => b.onclick = () => { q('#cc-ve').value = ccSumar(q('#cc-em').value || CC_HOY, +b.dataset.p); });
  m.querySelectorAll('[data-q]').forEach(b => b.onclick = () => { leer(); est.items.splice(+b.dataset.q, 1); cerrar(); ccFormulario(tipo, d, est); });
  if (q('#cc-addp')) q('#cc-addp').onclick = async () => { leer(); cerrar(); const it = await ccElegirProducto(); if (it) est.items.push(it); ccFormulario(tipo, d, est); };
  q('#cc-no').onclick = cerrar;
  q('#cc-ok').onclick = async () => {
    leer();
    const conFactura = est.sinf || est.numero.trim() !== '';
    const j = await api('/api/cc/doc', {id: d ? d.id : null, tipo, persona: est.persona, emision: est.emision, vence: conFactura ? est.vence : '', numero: est.numero, sin_factura: est.sinf,
      total: conFactura ? est.total : '', obs: est.obs, pagado_inicial: nuevo && conFactura ? est.ini : null, items: CC_STOCK ? est.items : undefined});
    if (!j.ok) { const e = q('#cc-err'); e.textContent = j.error || 'No se pudo guardar.'; e.style.display = 'block'; return; }
    if (j.aplicado > 0) await aviso(`Se descontaron $ ${ccNum(j.aplicado)} de las entregas que había a favor.`);
    location.reload();
  };
  setTimeout(() => q(est.persona ? '#cc-obs' : '#cc-per').focus(), 50);
}
function ccNuevo(tipo, persona) {
  ccFormulario(tipo, null);
  if (persona) { const i = document.querySelector('#cc-per'); i.value = persona; const o = document.querySelector('#cc-obs'); if (o) o.focus(); }
}
async function ccFactura(id, tipo) { const j = await api('/api/cc/doc/' + id); if (j.ok) ccFormulario(tipo, {...j.doc, items: j.items}); else aviso(j.error); }

// Buscador de productos del stock; resuelve con {producto_id, nombre, cantidad, precio, abrev} o null
function ccElegirProducto() {
  return new Promise(res => {
    const m = modal(`<h2>Agregar producto del stock</h2><div id="pp-err" class="msg error" style="display:none"></div>
      <input id="pp-q" autocomplete="off" placeholder="Buscar por nombre o código"><div id="pp-l" style="max-height:200px;overflow:auto;margin:6px 0"></div>
      <div id="pp-s" style="display:none"><p style="margin:4px 0"><b id="pp-n"></b> <span class="mut" id="pp-st"></span></p>
      <div class="grid"><div><label>Cantidad</label><input id="pp-c" inputmode="decimal" value="1"></div><div><label>Precio unitario ($)</label><input id="pp-p" class="moneda" inputmode="decimal"></div></div></div>
      <div class="row" style="margin-top:12px"><button class="btn pri" id="pp-ok">Agregar</button><button class="btn" id="pp-no">Cancelar</button></div>`);
    let sel = null, t = null;
    const buscar = async () => {
      const j = await api('/api/cc/productos?q=' + encodeURIComponent(m.querySelector('#pp-q').value));
      const l = m.querySelector('#pp-l'); l.innerHTML = '';
      (j.items || []).forEach(p => {
        const b = document.createElement('button'); b.className = 'btn'; b.style.cssText = 'display:block;width:100%;text-align:left;margin-bottom:4px';
        b.textContent = `${p.nombre}${p.codigo ? ' · ' + p.codigo : ''} — stock ${p.stock} ${p.abrev}` + (p.precio != null ? ` — $ ${ccNum(p.precio)}` : '');
        b.onclick = () => { sel = p; m.querySelector('#pp-s').style.display = 'block'; m.querySelector('#pp-n').textContent = p.nombre; m.querySelector('#pp-st').textContent = `(stock: ${p.stock} ${p.abrev})`;
          m.querySelector('#pp-p').value = p.precio != null ? ccNum(p.precio) : ''; l.innerHTML = ''; };
        l.appendChild(b);
      });
      if (!(j.items || []).length) l.innerHTML = '<p class="mut">No hay productos con ese nombre en Stock.</p>';
    };
    m.querySelector('#pp-q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(buscar, 250); });
    m.querySelector('#pp-no').onclick = () => { cerrar(); res(null); };
    m.querySelector('#pp-ok').onclick = () => {
      const err = x => { const e = m.querySelector('#pp-err'); e.textContent = x; e.style.display = 'block'; };
      if (!sel) return err('Elegí un producto de la lista.');
      const cant = parseFloat(String(m.querySelector('#pp-c').value).replace(',', '.')), pr = parseFloat(String(m.querySelector('#pp-p').value).replace(/\./g, '').replace(',', '.'));
      if (!(cant > 0)) return err('Escribí una cantidad válida.');
      if (!(pr >= 0)) return err('Escribí el precio unitario.');
      if (cant > sel.stock) return err(`Stock insuficiente: hay ${sel.stock} ${sel.abrev}.`);
      cerrar(); res({producto_id: sel.id, nombre: sel.nombre, cantidad: cant, precio: pr, abrev: sel.abrev});
    };
    buscar(); setTimeout(() => m.querySelector('#pp-q').focus(), 50);
  });
}

async function ccPago(id, tipo) {
  const j = await api('/api/cc/doc/' + id);
  if (!j.ok) return aviso(j.error);
  const L = ccEtiq(tipo), d = j.doc;
  const m = modal(`<h2>${L.accion} · ${esc(d.persona)}</h2><div id="cp-err" class="msg error" style="display:none"></div>
    <p class="mut">Total $ ${ccNum(d.total)} · ${L.pagado} $ ${ccNum(d.pagado)} · <b>Saldo $ ${ccNum(d.saldo)}</b></p>
    <div class="grid"><div><label>Importe ($) *</label><input id="cp-m" class="moneda" inputmode="decimal" value="${ccNum(d.saldo)}"></div>
    <div><label>Fecha</label><input id="cp-f" type="date" value="${CC_HOY}"></div></div>
    ${(j.formas || []).length ? `<label>Forma de pago *</label><select id="cp-forma">${j.formas.map(f => `<option value="${f.id}">${esc(f.nombre)}</option>`).join('')}</select>` : ''}
    <label>Nota (opcional)</label><input id="cp-n" placeholder="Ej: número de operación…">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="cp-ok">Registrar</button><button class="btn" id="cp-no">Cancelar</button></div>`);
  m.querySelector('#cp-f').addEventListener('click', e => { try { e.target.showPicker(); } catch (x) {} });
  m.querySelector('#cp-no').onclick = cerrar;
  m.querySelector('#cp-ok').onclick = async () => {
    const monto = m.querySelector('#cp-m').value, fsel = m.querySelector('#cp-forma');
    const datos = {monto, fecha: m.querySelector('#cp-f').value, nota: m.querySelector('#cp-n').value};
    if (fsel) {
      datos.forma_id = fsel.value;
      const f = j.formas.find(x => x.id == fsel.value);
      if (f && f.cheque) {
        cerrar();
        datos.cheque = await pedirCheque(monto, d.persona);
        if (!datos.cheque) return;
      }
    }
    const r = await api('/api/cc/doc/' + id + '/pago', datos);
    if (!r.ok && !m.isConnected) return aviso(r.error || 'No se pudo registrar.');
    if (!r.ok) { const e = m.querySelector('#cp-err'); e.textContent = r.error || 'No se pudo registrar.'; e.style.display = 'block'; return; }
    location.reload();
  };
  setTimeout(() => { const i = m.querySelector('#cp-m'); i.focus(); i.select(); }, 50);
}

async function ccVer(id, tipo) {
  const j = await api('/api/cc/doc/' + id);
  if (!j.ok) return aviso(j.error);
  const L = ccEtiq(tipo), d = j.doc;
  const fe = s => s.split('-').reverse().join('/');
  const m = modal(`<h2>${esc(d.persona)}</h2>
    <p class="mut" style="line-height:1.6">Fecha ${fe(d.emision)}${d.vence ? ' · Vence ' + fe(d.vence) : ''}${d.numero ? ' · Factura ' + esc(d.numero) : d.sin_factura ? ' · SIN FACTURA' : ' · Sin facturar'}<br>Total $ ${ccNum(d.total)} · ${L.pagado} $ ${ccNum(d.pagado)} · <b>Saldo $ ${ccNum(d.saldo)}</b>${d.obs ? '<br>' + esc(d.obs) : ''}</p>
    ${j.items.length ? `<p class="mut" style="margin:0">Productos: ${j.items.map(i => esc(i.nombre) + ' x' + ccNum(i.cantidad)).join(', ')}</p>` : ''}
    <table class="tabla" style="width:100%"><tr><th>Fecha</th><th>Nota</th><th style="text-align:right">Importe</th><th></th></tr>
    ${j.pagos.map(p => `<tr><td>${fe(p.fecha)}</td><td>${esc(p.nota || '')}</td><td style="text-align:right">$ ${ccNum(p.monto)}</td><td><button class="btn" style="padding:1px 8px" data-pp="${p.id}">×</button></td></tr>`).join('') || '<tr><td colspan="4" class="mut">Sin pagos registrados.</td></tr>'}</table>
    <div class="row" style="margin-top:12px"><button class="btn" id="cv-ed">Editar</button><button class="btn bad" id="cv-del">Eliminar</button><button class="btn" id="cv-no">Cerrar</button></div>`);
  m.querySelector('#cv-no').onclick = cerrar;
  m.querySelector('#cv-ed').onclick = () => { cerrar(); ccFormulario(tipo, {...d, items: j.items}); };
  m.querySelector('#cv-del').onclick = async () => {
    cerrar();
    if (!await confirmar('¿Eliminar este registro y todos sus pagos?')) return;
    const r = await apiCodigo('/api/cc/doc/' + id + '/eliminar', {});
    if (r.cancelado) return;
    if (!r.ok) return aviso(r.error);
    location.reload();
  };
  m.querySelectorAll('[data-pp]').forEach(b => b.onclick = async () => {
    cerrar();
    const r = await apiCodigo('/api/cc/pago/' + b.dataset.pp + '/eliminar', {});
    if (r.cancelado) return;
    if (!r.ok) return aviso(r.error);
    location.reload();
  });
}

// Entrega de dinero a cuenta: se descuenta de las facturas más viejas y queda la diferencia a abonar
async function ccEntrega(tipo, nombre) {
  const L = ccEtiq(tipo), noun = tipo === 'cliente' ? 'Entrega de dinero' : 'Pago a cuenta';
  const m = modal(`<h2>${noun}</h2><div id="en-err" class="msg error" style="display:none"></div>
    <label>${L.persona} *</label><input id="en-per" list="cc-nombres" autocomplete="off" value="${esc(nombre || '')}">
    <div id="en-info" class="mut" style="margin:4px 0;min-height:20px"></div>
    <div class="grid"><div><label>Importe ($) *</label><input id="en-m" class="moneda" inputmode="decimal"></div>
    <div><label>Fecha</label><input id="en-f" type="date" value="${CC_HOY}"></div></div>
    <div id="en-fp"></div>
    <label>Nota (opcional)</label><input id="en-n" placeholder="Ej: seña por los próximos retiros">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="en-ok">Registrar</button><button class="btn" id="en-no">Cancelar</button></div>`);
  const q = s => m.querySelector(s);
  let formas = [];
  const info = async () => {
    const n = q('#en-per').value.trim(); if (!n) { q('#en-info').textContent = ''; return; }
    const j = await api('/api/cc/persona?tipo=' + tipo + '&nombre=' + encodeURIComponent(n));
    if (!j.ok) return;
    q('#en-info').innerHTML = `Saldo actual: <b>$ ${ccNum(j.saldo)}</b>${j.a_favor ? ` · Entrega a favor: <b>$ ${ccNum(j.a_favor)}</b>` : ''}${j.sin_facturar ? ` · ${j.sin_facturar} retiro(s) sin facturar` : ''}`;
    formas = j.formas || [];
    q('#en-fp').innerHTML = formas.length ? `<label>Forma de pago *</label><select id="en-forma">${formas.map(f => `<option value="${f.id}">${esc(f.nombre)}</option>`).join('')}</select>` : '';
  };
  q('#en-per').addEventListener('change', info); q('#en-f').addEventListener('click', e => { try { e.target.showPicker(); } catch (x) {} });
  q('#en-no').onclick = cerrar;
  q('#en-ok').onclick = async () => {
    const err = x => { const e = q('#en-err'); e.textContent = x; e.style.display = 'block'; };
    await info();
    const datos = {tipo, persona: q('#en-per').value, monto: q('#en-m').value, fecha: q('#en-f').value, nota: q('#en-n').value};
    if (!datos.persona.trim()) return err(`Escribí el nombre del ${L.persona.toLowerCase()}.`);
    if (!datos.monto.trim()) return err('Escribí el importe.');
    const fs = q('#en-forma');
    if (fs) {
      datos.forma_id = fs.value;
      const f = formas.find(x => x.id == fs.value);
      if (f && f.cheque) { cerrar(); datos.cheque = await pedirCheque(datos.monto, datos.persona); if (!datos.cheque) return; }
    }
    const r = await api('/api/cc/entrega', datos);
    if (!r.ok) { if (!m.isConnected) return aviso(r.error || 'No se pudo registrar.'); return err(r.error || 'No se pudo registrar.'); }
    cerrar();
    await aviso(`Entrega registrada.${r.aplicado ? ` Se descontaron $ ${ccNum(r.aplicado)} de lo adeudado.` : ''} ${r.diferencia > 0 ? `Diferencia a ${tipo === 'cliente' ? 'abonar' : 'pagar'}: $ ${ccNum(r.diferencia)}.` : 'No queda deuda con facturas.'}${r.a_favor ? ` Queda $ ${ccNum(r.a_favor)} a favor para los próximos retiros.` : ''}`, noun);
    location.reload();
  };
  if (nombre) info();
  setTimeout(() => q(nombre ? '#en-m' : '#en-per').focus(), 50);
}
async function ccEntregaEliminar(id) {
  if (!await confirmar('¿Eliminar esta entrega? Se quita también de los pagos y del Historial de cobros.')) return;
  const r = await apiCodigo('/api/cc/entrega/' + id + '/eliminar', {});
  if (r.cancelado) return;
  if (!r.ok) return aviso(r.error);
  location.reload();
}
