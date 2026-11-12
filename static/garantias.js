// Garantías: producto o servicio de un cliente, con número de serie y tiempo de garantía
function garBloque(g, n, ultimo) {
  return `<div class="gar-b" style="border:1px solid var(--bd);border-radius:8px;padding:8px 10px;margin:8px 0">
    <div class="row" style="justify-content:space-between"><b class="gar-n">${g ? '' : 'Producto o servicio ' + n}</b><a href="#" class="gar-q" style="font-size:13px;${n > 1 ? '' : 'display:none'}">Quitar</a></div>
    <label>Tipo *</label>
    <select class="gar-tipo"><option value="producto" ${!g || g.tipo === 'producto' ? 'selected' : ''}>Producto</option><option value="servicio" ${g && g.tipo === 'servicio' ? 'selected' : ''}>Servicio</option></select>
    <label class="gar-l-desc">Producto *</label>
    <input class="gar-desc" list="gar-productos" autocomplete="off" value="${esc(g ? g.descripcion : '')}">
    <div class="gar-serie-box"><label>N° de serie</label><input class="gar-serie" autocomplete="off" value="${esc(g && g.serie ? g.serie : '')}"></div>
    <div class="row" style="gap:10px;align-items:flex-end">
      <div style="width:110px"><label>Tiempo</label><input class="gar-cant" inputmode="numeric" value="${g ? g.cantidad : ''}" placeholder="Ej: 6"></div>
      <div style="width:110px"><label>&nbsp;</label><select class="gar-uni"><option value="dias" ${g && g.unidad === 'dias' ? 'selected' : ''}>días</option><option value="meses" ${!g || g.unidad === 'meses' ? 'selected' : ''}>meses</option><option value="anios" ${g && g.unidad === 'anios' ? 'selected' : ''}>años</option></select></div>
      <div class="gar-vence mut" style="flex:1;font-size:13px;padding-bottom:8px"></div>
    </div></div>`;
}
function garForm(g) {
  g = g || null;
  const m = modal(`<h2>${g ? 'Editar garantía' : 'Nueva garantía'}</h2><div id="gar-err" class="msg error" style="display:none"></div>
    <label>Cliente * <span class="mut" style="font-weight:400">(elegí uno cargado o escribí el nombre)</span></label>
    <input id="gar-cli" list="gar-clientes" autocomplete="off" value="${esc(g ? g.cliente : '')}" placeholder="Empezá a escribir el nombre…">
    <label>Garantía desde *</label><input id="gar-ini" type="date" value="${g ? g.inicio : GAR_HOY}">
    <div id="gar-items">${garBloque(g, 1)}</div>
    ${g ? '' : '<button type="button" class="btn" id="gar-mas" style="margin-top:4px">＋ Agregar otro producto o servicio</button>'}
    <label>Nota (opcional)</label><input id="gar-nota" value="${esc(g && g.nota ? g.nota : '')}">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="gar-ok">Guardar</button><button class="btn" id="gar-no">Cancelar</button></div>`);
  const q = s => m.querySelector(s), bloques = () => [...m.querySelectorAll('.gar-b')];
  const ver = () => {
    const ini = q('#gar-ini').value;
    bloques().forEach((b, i) => {
      const srv = b.querySelector('.gar-tipo').value === 'servicio';
      b.querySelector('.gar-l-desc').textContent = srv ? 'Servicio realizado *' : 'Producto *';
      b.querySelector('.gar-serie-box').style.display = srv ? 'none' : '';
      b.querySelector('.gar-desc').setAttribute('list', srv ? '' : 'gar-productos');
      if (!g) { b.querySelector('.gar-n').textContent = 'Producto o servicio ' + (i + 1); b.querySelector('.gar-q').style.display = bloques().length > 1 ? '' : 'none'; }
      const n = parseInt(b.querySelector('.gar-cant').value, 10), v = b.querySelector('.gar-vence');
      if (n > 0 && ini) {
        const d = new Date(ini + 'T12:00:00'), u = b.querySelector('.gar-uni').value, dia = d.getDate();
        if (u === 'dias') d.setDate(d.getDate() + n);
        else { d.setDate(1); d.setMonth(d.getMonth() + n * (u === 'anios' ? 12 : 1)); d.setDate(Math.min(dia, new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate())); }
        v.textContent = 'Vence el ' + d.toLocaleDateString('es-AR');
      } else v.textContent = 'Sin garantía';
    });
  };
  m.addEventListener('input', ver); m.addEventListener('change', ver);
  m.addEventListener('click', e => {
    const x = e.target.closest('.gar-q'); if (!x) return;
    e.preventDefault(); x.closest('.gar-b').remove(); ver();
  });
  if (q('#gar-mas')) q('#gar-mas').onclick = () => {
    q('#gar-items').insertAdjacentHTML('beforeend', garBloque(null, bloques().length + 1));
    ver(); const b = bloques().pop(); b.querySelector('.gar-desc').focus();
  };
  ver();
  q('#gar-no').onclick = cerrar;
  q('#gar-ok').onclick = async () => {
    const items = bloques().map(b => ({tipo: b.querySelector('.gar-tipo').value, descripcion: b.querySelector('.gar-desc').value, serie: b.querySelector('.gar-serie').value,
      cantidad: b.querySelector('.gar-cant').value, unidad: b.querySelector('.gar-uni').value}));
    const j = await api('/api/garantias', {id: g ? g.id : null, cliente: q('#gar-cli').value, inicio: q('#gar-ini').value, nota: q('#gar-nota').value, items});
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
