// Cuenta corriente: ventas / compras a cuenta, pagos parciales y vencimientos
const ccNum = v => Number(v).toLocaleString('es-AR', {minimumFractionDigits: Number.isInteger(Number(v)) ? 0 : 2, maximumFractionDigits: 2});
const ccSumar = (iso, dias) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + dias); return d.toISOString().slice(0, 10); };
const ccEtiq = t => t === 'cliente' ? {persona: 'Cliente', pagado: 'cobrado', accion: 'Cobrar'} : {persona: 'Proveedor', pagado: 'pagado', accion: 'Pagar'};

function ccFormulario(tipo, d) {
  const L = ccEtiq(tipo), nuevo = !d;
  const m = modal(`<h2>${nuevo ? 'Nueva cuenta corriente' : 'Editar'} · ${L.persona}</h2><div id="cc-err" class="msg error" style="display:none"></div>
    <label>${L.persona} *</label><input id="cc-per" list="cc-nombres" autocomplete="off" value="${esc(d ? d.persona : '')}">
    <div class="grid"><div><label>Fecha de emisión *</label><input id="cc-em" type="date" value="${d ? d.emision : CC_HOY}"></div>
    <div><label>Vencimiento *</label><input id="cc-ve" type="date" value="${d ? d.vence : ccSumar(CC_HOY, 7)}"></div></div>
    <div class="row" style="margin:4px 0"><span class="mut">Plazo:</span>${[7, 15, 30, 60].map(n => `<button type="button" class="btn" data-p="${n}" style="padding:2px 8px">${n} días</button>`).join('')}</div>
    <div class="grid"><div><label>N° de comprobante</label><input id="cc-num" autocomplete="off" value="${esc(d ? d.numero || '' : '')}"></div>
    <div><label>Importe total ($) *</label><input id="cc-tot" class="moneda" inputmode="decimal" value="${d ? ccNum(d.total) : ''}"></div></div>
    ${nuevo ? `<label>Ya ${L.pagado} (opcional)</label><input id="cc-ini" class="moneda" inputmode="decimal" placeholder="0">` : ''}
    <label>Observaciones</label><input id="cc-obs" value="${esc(d ? d.obs || '' : '')}">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="cc-ok">Guardar</button><button class="btn" id="cc-no">Cancelar</button></div>`);
  ['cc-em', 've', 'cc-ve'].forEach(i => { const e = m.querySelector('#' + i); if (e) e.addEventListener('click', ev => { try { ev.target.showPicker(); } catch (x) {} }); });
  m.querySelectorAll('[data-p]').forEach(b => b.onclick = () => { m.querySelector('#cc-ve').value = ccSumar(m.querySelector('#cc-em').value || CC_HOY, +b.dataset.p); });
  m.querySelector('#cc-no').onclick = cerrar;
  m.querySelector('#cc-ok').onclick = async () => {
    const j = await api('/api/cc/doc', {id: d ? d.id : null, tipo, persona: m.querySelector('#cc-per').value, emision: m.querySelector('#cc-em').value,
      vence: m.querySelector('#cc-ve').value, numero: m.querySelector('#cc-num').value, total: m.querySelector('#cc-tot').value,
      obs: m.querySelector('#cc-obs').value, pagado_inicial: nuevo ? m.querySelector('#cc-ini').value : null});
    if (!j.ok) { const e = m.querySelector('#cc-err'); e.textContent = j.error || 'No se pudo guardar.'; e.style.display = 'block'; return; }
    location.reload();
  };
  setTimeout(() => m.querySelector('#cc-per').focus(), 50);
}
function ccNuevo(tipo) { ccFormulario(tipo, null); }

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
    <p class="mut" style="line-height:1.6">Emisión ${fe(d.emision)} · Vence ${fe(d.vence)}${d.numero ? ' · Comp. ' + esc(d.numero) : ''}<br>Total $ ${ccNum(d.total)} · ${L.pagado} $ ${ccNum(d.pagado)} · <b>Saldo $ ${ccNum(d.saldo)}</b>${d.obs ? '<br>' + esc(d.obs) : ''}</p>
    <table class="tabla" style="width:100%"><tr><th>Fecha</th><th>Nota</th><th style="text-align:right">Importe</th><th></th></tr>
    ${j.pagos.map(p => `<tr><td>${fe(p.fecha)}</td><td>${esc(p.nota || '')}</td><td style="text-align:right">$ ${ccNum(p.monto)}</td><td><button class="btn" style="padding:1px 8px" data-pp="${p.id}">×</button></td></tr>`).join('') || '<tr><td colspan="4" class="mut">Sin pagos registrados.</td></tr>'}</table>
    <div class="row" style="margin-top:12px"><button class="btn" id="cv-ed">Editar</button><button class="btn bad" id="cv-del">Eliminar</button><button class="btn" id="cv-no">Cerrar</button></div>`);
  m.querySelector('#cv-no').onclick = cerrar;
  m.querySelector('#cv-ed').onclick = () => { cerrar(); ccFormulario(tipo, d); };
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
