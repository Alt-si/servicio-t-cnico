// Control de stock: formularios de producto, movimientos y listas (todo editable por el comercio)
const optHtml = (lista, sel) => lista.map(x => `<option value="${x.id}" ${String(x.id) === String(sel) ? 'selected' : ''}>${esc(x.nombre)}${x.abrev ? ' (' + esc(x.abrev) + ')' : ''}</option>`).join('');

async function productoForm(id) {
  const f = await api('/api/stock/formulario' + (id ? '?id=' + id : ''));
  if (!f.ok) return aviso(f.error);
  const p = f.producto || {}, L = f.etiquetas;
  const num = v => (v === null || v === undefined || v === '') ? '' : Number(v).toLocaleString('es-AR', {maximumFractionDigits: 3});
  const c = modal(`<h2>${id ? 'Editar producto' : 'Nuevo producto'}</h2><div id="p-err" class="msg error" style="display:none"></div>
    <div class="grid">
      <div><label>Código (opcional)</label><input id="p-codigo" value="${esc(p.codigo || '')}"></div>
      <div><label>Producto *</label><input id="p-nombre" class="mayus" value="${esc(p.nombre || '')}"></div>
      <div><label>${esc(L.categoria)} <a href="#" id="p-mas-cat">+ nuevo</a></label><select id="p-cat"><option value="">—</option>${optHtml(f.categorias, p.categoria_id)}</select></div>
      <div><label>${esc(L.marca)} <a href="#" id="p-mas-mar">+ nueva</a></label><select id="p-mar"><option value="">—</option>${optHtml(f.marcas, p.marca_id)}</select></div>
      <div><label>Unidad de medida * <a href="/stock/listas">editar unidades</a></label><select id="p-uni">${optHtml(f.unidades, p.unidad_id)}</select></div>
      <div><label>Ubicación</label><input id="p-ubi" class="mayus" value="${esc(p.ubicacion || '')}" placeholder="Estante, depósito…"></div>
      <div><label>Costo ($)</label><input id="p-costo" class="moneda" inputmode="decimal" value="${num(p.costo)}"></div>
      <div><label>Precio de venta ($)</label><input id="p-precio" class="moneda" inputmode="decimal" value="${num(p.precio)}"></div>
      <div><label>Stock mínimo (aviso)</label><input id="p-min" inputmode="decimal" value="${num(p.stock_minimo || '')}"></div>
      ${id ? '' : '<div><label>Stock inicial</label><input id="p-ini" inputmode="decimal"></div>'}
    </div>
    <label>Descripción</label><textarea id="p-desc" class="mayus">${esc(p.descripcion || '')}</textarea>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="p-ok">Guardar</button><button class="btn" id="p-no">Cancelar</button></div>`);
  const nuevo = async (tabla, selId) => {
    const r = await listaPedir(tabla); if (!r) return;
    $(selId, c).insertAdjacentHTML('beforeend', `<option value="${r.id}">${esc(r.nombre)}</option>`); $(selId, c).value = r.id;
  };
  $('#p-mas-cat', c).onclick = e => { e.preventDefault(); nuevo('categorias', '#p-cat'); };
  $('#p-mas-mar', c).onclick = e => { e.preventDefault(); nuevo('marcas', '#p-mar'); };
  $('#p-no', c).onclick = cerrar;
  $('#p-ok', c).onclick = async () => {
    const d = {id, nombre: $('#p-nombre', c).value, codigo: $('#p-codigo', c).value, categoria_id: $('#p-cat', c).value, marca_id: $('#p-mar', c).value,
      unidad_id: $('#p-uni', c).value, ubicacion: $('#p-ubi', c).value, costo: $('#p-costo', c).value, precio: $('#p-precio', c).value,
      stock_minimo: $('#p-min', c).value, descripcion: $('#p-desc', c).value, stock_inicial: $('#p-ini', c)?.value || ''};
    const j = await api('/api/stock/producto', d);
    if (j.ok) { location = id ? location.href : '/stock/producto/' + j.id; } else { const e = $('#p-err', c); e.textContent = j.error; e.style.display = 'block'; }
  };
}

// Alta rápida de un rubro o una marca desde el formulario del producto
function listaPedir(tabla) {
  return new Promise(res => {
    const c = modal(`<h2>${tabla === 'categorias' ? 'Nuevo rubro' : 'Nueva marca'}</h2><div id="l-err" class="msg error" style="display:none"></div>
      <input id="l-nombre" class="mayus" autocomplete="off"><div class="row" style="margin-top:12px"><button class="btn pri" id="l-ok">Agregar</button><button class="btn" id="l-no">Cancelar</button></div>`);
    setTimeout(() => $('#l-nombre', c).focus(), 50);
    $('#l-no', c).onclick = () => { cerrar(); res(null); };
    $('#l-ok', c).onclick = async () => {
      const j = await api('/api/stock/lista/' + tabla, {nombre: $('#l-nombre', c).value});
      if (j.ok) { cerrar(); res(j); } else { const e = $('#l-err', c); e.textContent = j.error; e.style.display = 'block'; }
    };
  });
}

const TIT_MOV = {ingreso: 'Ingreso de stock', egreso: 'Egreso de stock', ajuste: 'Ajuste de inventario (stock real contado)'};
async function movimientoForm(pid, tipo, recargar) {
  const motivos = ['Compra a proveedor', 'Venta', 'Devolución de cliente', 'Devolución a proveedor', 'Uso interno', 'Rotura / merma', 'Pérdida', 'Inventario (conteo)', 'Corrección'];
  const c = modal(`<h2>${TIT_MOV[tipo]}</h2><div id="m-err" class="msg error" style="display:none"></div>
    <label>${tipo === 'ajuste' ? 'Cantidad real contada' : 'Cantidad'}</label><input id="m-cant" inputmode="decimal" autocomplete="off">
    <label>Motivo</label><input id="m-motivo" list="m-motivos" autocomplete="off"><datalist id="m-motivos">${motivos.map(m => `<option value="${esc(m)}">`).join('')}</datalist>
    <label>Nota (opcional)</label><input id="m-nota" class="mayus">
    <div class="row" style="margin-top:12px"><button class="btn pri" id="m-ok">Confirmar</button><button class="btn" id="m-no">Cancelar</button></div>`);
  setTimeout(() => $('#m-cant', c).focus(), 50);
  $('#m-no', c).onclick = cerrar;
  $('#m-ok', c).onclick = async () => {
    const j = await api('/api/stock/movimiento', {producto_id: pid, tipo, cantidad: $('#m-cant', c).value, motivo: $('#m-motivo', c).value, nota: $('#m-nota', c).value});
    if (j.ok) location.reload(); else { const e = $('#m-err', c); e.textContent = j.error; e.style.display = 'block'; }
  };
}

// Pantalla de rubros, marcas y unidades
function listaForm(tabla, r) {
  r = r || {};
  const u = tabla === 'unidades';
  const c = modal(`<h2>${r.id ? 'Editar' : 'Agregar'}</h2><div id="l-err" class="msg error" style="display:none"></div>
    <label>Nombre</label><input id="l-nombre" class="mayus" value="${esc(r.nombre || '')}" autocomplete="off">
    ${u ? `<label>Abreviatura (ej.: kg, l, u)</label><input id="l-abrev" value="${esc(r.abrev || '')}" maxlength="8">
           <label>Decimales de la cantidad (0 a 3)</label><input id="l-dec" type="number" min="0" max="3" value="${r.decimales ?? 0}">` : ''}
    <div class="row" style="margin-top:12px"><button class="btn pri" id="l-ok">Guardar</button><button class="btn" id="l-no">Cancelar</button></div>`);
  $('#l-no', c).onclick = cerrar;
  $('#l-ok', c).onclick = async () => {
    const d = {id: r.id, nombre: $('#l-nombre', c).value}; if (u) { d.abrev = $('#l-abrev', c).value; d.decimales = $('#l-dec', c).value; }
    const j = await api('/api/stock/lista/' + tabla, d);
    if (j.ok) location.reload(); else { const e = $('#l-err', c); e.textContent = j.error; e.style.display = 'block'; }
  };
}
async function listaEliminar(tabla, id) {
  if (!await confirmar('¿Eliminar?')) return;
  const j = await api(`/api/stock/lista/${tabla}/${id}/eliminar`, {});
  if (j.ok) location.reload(); else aviso(j.error);
}
$('#lbl-ok')?.addEventListener('click', async () => {
  const j = await api('/api/stock/etiquetas', {categoria: $('#lbl-categoria').value, marca: $('#lbl-marca').value});
  if (j.ok) location.reload(); else aviso(j.error);
});
