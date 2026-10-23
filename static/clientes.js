function clienteForm(c) {
  c = c || {};
  const ivas = IVA.map(i => `<option ${((c.condicion_iva || '') === i) ? 'selected' : ''}>${esc(i)}</option>`).join('');
  const MAY = ['nombre', 'apellido', 'ciudad', 'domicilio', 'razon_social'];
  const campo = (k, t, req) => `<div><label>${t}${req ? ' *' : ''}</label><input id="f-${k}" class="${MAY.includes(k) ? 'mayus' : ''}" value="${esc(c[k] || '')}"></div>`;
  const box = modal(`<h2>${c.id ? 'Editar cliente' : 'Nuevo cliente'}</h2><div id="f-err" class="msg error" style="display:none"></div>
    <div class="grid">${campo('nombre', 'Nombre', 1)}${campo('apellido', 'Apellido', 1)}${campo('telefono', 'Teléfono', 1)}
    ${campo('dni', 'DNI')}${campo('ciudad', 'Ciudad')}${campo('domicilio', 'Domicilio')}${campo('razon_social', 'Razón social')}
    ${campo('cuit', 'CUIT (11 números)')}<div><label>Condición de IVA (obligatoria si hay CUIT)</label><select id="f-condicion_iva"><option value="">—</option>${ivas}</select></div>
    ${campo('mail', 'Mail')}</div>
    <div class="row" style="margin-top:12px"><button class="btn pri" id="f-ok">Guardar</button><button class="btn" id="f-no">Cancelar</button>
    ${c.id ? '<button class="btn bad der" id="f-del">Eliminar</button>' : ''}</div>`);
  $('#f-no', box).onclick = cerrar;
  $('#f-ok', box).onclick = async () => {
    const d = {id: c.id};
    ['nombre','apellido','telefono','dni','ciudad','domicilio','razon_social','cuit','condicion_iva','mail'].forEach(k => d[k] = $('#f-' + k, box).value);
    const j = await api('/api/cliente', d);
    if (j.ok) location.reload(); else { const e = $('#f-err', box); e.textContent = j.error; e.style.display = 'block'; }
  };
  $('#f-del', box)?.addEventListener('click', async () => {
    if (!await confirmar('¿Eliminar este cliente?')) return;
    const j = await api(`/api/cliente/${c.id}/eliminar`, {});
    if (j.ok) location.reload(); else await aviso(j.error);
  });
}
async function historial(id) {
  const j = await api(`/api/cliente/${id}/ordenes`);
  if (!j.ok) return aviso(j.error);
  const filas = j.ordenes.map(o => `<li><a href="/orden/${o.id}">#${String(o.id).padStart(5, '0')}</a> · ${esc(o.fecha)} · ${esc(o.equipo)}<br>
    <small>${esc(o.estado)}${o.rechazado ? ' (no aceptado)' : ''} — ${esc(o.falla)}</small></li>`).join('') || '<li class="mut">Sin órdenes.</li>';
  const c = modal(`<h2>${esc(j.cliente)}</h2><ul class="hist">${filas}</ul><p><button class="btn" id="h-ok">Cerrar</button></p>`);
  $('#h-ok', c).onclick = cerrar;
}
