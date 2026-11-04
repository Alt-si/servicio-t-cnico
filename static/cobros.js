// Corregir o eliminar un cobro (siempre con código de validación)
async function cobroEditar(id) {
  const j = await api('/api/cobros/' + id);
  if (!j.ok) { aviso(j.error || 'No se pudo cargar el cobro.'); return; }
  const c = j.cobro, formas = j.formas;
  const m = modal(`<h2>Corregir cobro</h2><div id="cb-err" class="msg error" style="display:none"></div>
    <label>Importe cobrado</label><input id="cb-monto" class="moneda" inputmode="decimal" value="${esc(String(c.monto).replace('.', ','))}">
    <label>Forma de pago / cuenta</label><select id="cb-forma">${formas.map(f => `<option value="${f.id}" ${f.id == c.forma_id ? 'selected' : ''}>${esc(f.nombre)}</option>`).join('')}</select>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="cb-ok">Guardar</button><button class="btn" id="cb-no">Cancelar</button></div>`);
  m.querySelector('#cb-no').onclick = cerrar;
  m.querySelector('#cb-ok').onclick = async () => {
    const monto = m.querySelector('#cb-monto').value, fid = m.querySelector('#cb-forma').value;
    const f = formas.find(x => x.id == fid);
    let cheque = null;
    if (f && f.cheque) {
      cerrar();
      cheque = await pedirCheque(monto, j.cheque ? j.cheque.entrega : '', j.cheque || {});
      if (!cheque) return;
    } else cerrar();
    const r = await apiCodigo('/api/cobros/' + id + '/editar', {monto, forma_id: fid, cheque});
    if (r.cancelado) return;
    if (!r.ok) { await aviso(r.error || 'No se pudo guardar.'); return; }
    location.reload();
  };
}
async function cobroEliminar(id) {
  if (!await confirmar('¿Eliminar este cobro? Solo hacelo si se cargó por error.')) return;
  const r = await apiCodigo('/api/cobros/' + id + '/eliminar', {});
  if (r.cancelado) return;
  if (!r.ok) { await aviso(r.error || 'No se pudo eliminar.'); return; }
  location.reload();
}
