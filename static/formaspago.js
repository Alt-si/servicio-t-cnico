// Formas de pago y cuentas (Configuración): agregar, editar y eliminar
function fpForm(r) {
  r = r || {};
  const c = modal(`<h2>${r.id ? 'Editar forma de pago' : 'Agregar forma de pago o cuenta'}</h2><div id="fp-err" class="msg error" style="display:none"></div>
    <label>Nombre</label><input id="fp-nombre" class="mayus" value="${esc(r.nombre || '')}" autocomplete="off" placeholder="Ej.: TRANSFERENCIA MERCADO PAGO">
    <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="fp-ad" style="width:auto" ${r.aplica_descuento ? 'checked' : ''}> Aplica el descuento de precios (efectivo, transferencia…)</label>
    <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="fp-ch" style="width:auto" ${r.pide_cheque ? 'checked' : ''}> Es cheque o e-cheq (al cobrar pide fecha de emisión, fecha de cobro, N°, banco y quién lo entrega)</label>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="fp-ok">Guardar</button><button class="btn" id="fp-no">Cancelar</button></div>`);
  setTimeout(() => c.querySelector('#fp-nombre').focus(), 50);
  c.querySelector('#fp-no').onclick = cerrar;
  c.querySelector('#fp-ok').onclick = async () => {
    const j = await api('/api/stock/lista/formas_pago', {id: r.id, nombre: c.querySelector('#fp-nombre').value, aplica_descuento: c.querySelector('#fp-ad').checked, pide_cheque: c.querySelector('#fp-ch').checked});
    if (j.ok) { location.hash = 'formas-pago'; location.reload(); } else { const e = c.querySelector('#fp-err'); e.textContent = j.error; e.style.display = 'block'; }
  };
}
async function fpEliminar(id, nombre) {
  if (!await confirmar(`¿Eliminar «${nombre}»? Los cobros y ventas ya registrados con esa forma de pago se conservan.`)) return;
  const j = await api(`/api/stock/lista/formas_pago/${id}/eliminar`, {});
  if (j.ok) { location.hash = 'formas-pago'; location.reload(); } else aviso(j.error);
}
