// Garantías: producto o servicio de un cliente, con número de serie y tiempo de garantía
function garForm(g) {
  g = g || null;
  const m = modal(`<h2>${g ? 'Editar garantía' : 'Nueva garantía'}</h2><div id="gar-err" class="msg error" style="display:none"></div>
    <label>Cliente * <span class="mut" style="font-weight:400">(elegí uno cargado o escribí el nombre)</span></label>
    <input id="gar-cli" list="gar-clientes" autocomplete="off" value="${esc(g ? g.cliente : '')}" placeholder="Empezá a escribir el nombre…">
    <label>Tipo *</label>
    <select id="gar-tipo"><option value="producto" ${g && g.tipo === 'producto' || !g ? 'selected' : ''}>Producto</option><option value="servicio" ${g && g.tipo === 'servicio' ? 'selected' : ''}>Servicio</option></select>
    <label id="gar-l-desc">Producto *</label>
    <input id="gar-desc" list="gar-productos" autocomplete="off" value="${esc(g ? g.descripcion : '')}">
    <div id="gar-serie-box"><label>N° de serie</label><input id="gar-serie" autocomplete="off" value="${esc(g && g.serie ? g.serie : '')}"></div>
    <div class="row" style="gap:10px;align-items:flex-end">
      <div style="flex:1"><label>Garantía desde *</label><input id="gar-ini" type="date" value="${g ? g.inicio : GAR_HOY}"></div>
      <div style="width:90px"><label>Tiempo</label><input id="gar-cant" inputmode="numeric" value="${g ? g.cantidad : ''}" placeholder="Ej: 6"></div>
      <div style="width:110px"><label>&nbsp;</label><select id="gar-uni"><option value="dias" ${g && g.unidad === 'dias' ? 'selected' : ''}>días</option><option value="meses" ${!g || g.unidad === 'meses' ? 'selected' : ''}>meses</option><option value="anios" ${g && g.unidad === 'anios' ? 'selected' : ''}>años</option></select></div>
    </div>
    <div id="gar-vence" class="mut" style="margin:4px 0;font-size:13px"></div>
    <label>Nota (opcional)</label><input id="gar-nota" value="${esc(g && g.nota ? g.nota : '')}">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="gar-ok">Guardar</button><button class="btn" id="gar-no">Cancelar</button></div>`);
  const q = s => m.querySelector(s);
  const ver = () => {
    const srv = q('#gar-tipo').value === 'servicio';
    q('#gar-l-desc').textContent = srv ? 'Servicio realizado *' : 'Producto *';
    q('#gar-serie-box').style.display = srv ? 'none' : '';
    q('#gar-desc').setAttribute('list', srv ? '' : 'gar-productos');
    const n = parseInt(q('#gar-cant').value, 10), ini = q('#gar-ini').value;
    if (n > 0 && ini) {
      const d = new Date(ini + 'T12:00:00'), u = q('#gar-uni').value, dia = d.getDate();
      if (u === 'dias') d.setDate(d.getDate() + n);
      else { d.setDate(1); d.setMonth(d.getMonth() + n * (u === 'anios' ? 12 : 1)); d.setDate(Math.min(dia, new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate())); }
      q('#gar-vence').textContent = 'Vence el ' + d.toLocaleDateString('es-AR');
    } else q('#gar-vence').textContent = 'Sin tiempo de garantía.';
  };
  ['#gar-tipo', '#gar-cant', '#gar-ini', '#gar-uni'].forEach(s => { q(s).addEventListener('input', ver); q(s).addEventListener('change', ver); });
  ver();
  q('#gar-no').onclick = cerrar;
  q('#gar-ok').onclick = async () => {
    const j = await api('/api/garantias', {id: g ? g.id : null, cliente: q('#gar-cli').value, tipo: q('#gar-tipo').value, descripcion: q('#gar-desc').value,
      serie: q('#gar-serie').value, inicio: q('#gar-ini').value, cantidad: q('#gar-cant').value, unidad: q('#gar-uni').value, nota: q('#gar-nota').value});
    if (!j.ok) { const e = q('#gar-err'); e.textContent = j.error; e.style.display = 'block'; return; }
    location.reload();
  };
  setTimeout(() => q('#gar-cli').focus(), 50);
}
function garEliminar(id, nombre) {
  confirmar(`¿Eliminar la garantía de ${nombre}?`).then(async ok => {
    if (!ok) return;
    const j = await apiCodigo('/api/garantias/' + id + '/eliminar', {});
    if (j.cancelado) return;
    if (!j.ok) return aviso(j.error);
    location.reload();
  });
}
