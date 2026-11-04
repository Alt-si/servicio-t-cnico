// Repuestos y accesorios de una orden (usa los productos cargados en Stock)
async function repAgregar() {
  const m = modal(`<h2>Agregar repuesto o accesorio</h2><div id="rp-err" class="msg error" style="display:none"></div>
    <label>Buscar en Stock (nombre o código)</label><input id="rp-q" autocomplete="off" placeholder="Escribí para buscar">
    <div id="rp-lista" style="max-height:200px;overflow:auto;margin:6px 0"></div>
    <div id="rp-sel" style="display:none"><p style="margin:4px 0"><b id="rp-nom"></b> <span class="mut" id="rp-stk"></span></p>
      <div class="grid"><div><label>Cantidad</label><input id="rp-cant" inputmode="decimal" value="1"></div>
      <div><label>Precio unitario ($)</label><input id="rp-precio" class="moneda" inputmode="decimal"></div></div></div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="rp-ok">Agregar a la orden</button><button class="btn" id="rp-no">Cancelar</button></div>`);
  let sel = null, t = null;
  const err = x => { const e = m.querySelector('#rp-err'); e.textContent = x; e.style.display = 'block'; };
  const buscar = async () => {
    const j = await api(`/api/orden/${ORDEN.id}/repuestos/buscar?q=` + encodeURIComponent(m.querySelector('#rp-q').value));
    const l = m.querySelector('#rp-lista'); l.innerHTML = '';
    (j.items || []).forEach(p => {
      const b = document.createElement('button'); b.className = 'btn'; b.style.cssText = 'display:block;width:100%;text-align:left;margin-bottom:4px';
      b.textContent = `${p.nombre}${p.codigo ? ' · ' + p.codigo : ''} — stock ${p.stock} ${p.abrev}` + (p.precio != null ? ` — $ ${num_ar(p.precio)}` : '');
      b.onclick = () => { sel = p; m.querySelector('#rp-sel').style.display = 'block'; m.querySelector('#rp-nom').textContent = p.nombre;
        m.querySelector('#rp-stk').textContent = `(stock: ${p.stock} ${p.abrev})`; m.querySelector('#rp-precio').value = p.precio != null ? num_ar(p.precio) : ''; l.innerHTML = ''; };
      l.appendChild(b);
    });
    if (!(j.items || []).length) l.innerHTML = '<p class="mut">No hay productos con ese nombre en Stock.</p>';
  };
  m.querySelector('#rp-q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(buscar, 250); });
  m.querySelector('#rp-no').onclick = cerrar;
  m.querySelector('#rp-ok').onclick = async () => {
    if (!sel) return err('Elegí un producto de la lista.');
    const j = await api(`/api/orden/${ORDEN.id}/repuestos`, {producto_id: sel.id, cantidad: m.querySelector('#rp-cant').value, precio: m.querySelector('#rp-precio').value});
    if (!j.ok) return err(j.error || 'No se pudo agregar.');
    location.reload();
  };
  buscar(); setTimeout(() => m.querySelector('#rp-q').focus(), 50);
}
async function repQuitar(id) {
  if (!await confirmar('¿Quitar este repuesto de la orden? Vuelve al stock.')) return;
  const j = await api(`/api/orden/${ORDEN.id}/repuestos/${id}/quitar`, {});
  if (!j.ok) return aviso(j.error || 'No se pudo quitar.');
  location.reload();
}
