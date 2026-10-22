// Utilidades comunes
const CSRF = document.querySelector('meta[name=csrf]')?.content || '';
const $ = (s, r = document) => r.querySelector(s);
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

document.getElementById('tema')?.addEventListener('click', () => {
  const d = document.documentElement;
  const n = d.dataset.theme === 'dark' ? 'light' : 'dark';
  d.dataset.theme = n;
  try { localStorage.setItem('tema', n); } catch (e) {}
});

async function api(url, datos, metodo) {
  const r = await fetch(url, {method: metodo || (datos === undefined ? 'GET' : 'POST'),
    headers: {'Content-Type': 'application/json', 'X-CSRF': CSRF},
    body: datos === undefined ? undefined : JSON.stringify(datos)});
  let j; try { j = await r.json(); } catch (e) { j = {ok: false, error: 'Respuesta inválida del servidor'}; }
  if (r.status === 401) { location = '/login'; }
  return j;
}

function modal(html) {
  const m = $('#modal'); $('#modal-caja').innerHTML = html; m.classList.add('abierto'); return $('#modal-caja');
}
function cerrar() { $('#modal').classList.remove('abierto'); }
$('#modal')?.addEventListener('mousedown', e => { if (e.target.id === 'modal') cerrar(); });

// Pide el código de validación y reintenta
function pedirCodigo(motivo) {
  return new Promise(res => {
    const c = modal(`<h2>Código de validación</h2><p>${esc(motivo || '')}</p>
      <input type="password" id="cod" autocomplete="off" autofocus>
      <div class="row" style="margin-top:12px"><button class="btn pri" id="cod-ok">Aceptar</button><button class="btn" id="cod-no">Cancelar</button></div>`);
    const ok = () => { const v = $('#cod', c).value; cerrar(); res(v); };
    $('#cod-ok', c).onclick = ok; $('#cod-no', c).onclick = () => { cerrar(); res(null); };
    $('#cod', c).onkeydown = e => { if (e.key === 'Enter') ok(); };
    setTimeout(() => $('#cod', c).focus(), 50);
  });
}
// api que maneja necesita_codigo
async function apiCodigo(url, datos) {
  let j = await api(url, datos);
  while (j.necesita_codigo) {
    const cod = await pedirCodigo(j.error);
    if (cod === null) return {ok: false, cancelado: true};
    j = await api(url, {...datos, codigo: cod});
  }
  return j;
}
function aviso(texto, titulo) {
  return new Promise(res => {
    const c = modal(`<h2>${esc(titulo || 'Aviso')}</h2><p>${esc(texto)}</p><button class="btn pri" id="av-ok">Aceptar</button>`);
    $('#av-ok', c).onclick = () => { cerrar(); res(); };
  });
}
function confirmar(texto) {
  return new Promise(res => {
    const c = modal(`<p>${esc(texto)}</p><div class="row"><button class="btn pri" id="c-si">Sí</button><button class="btn" id="c-no">No</button></div>`);
    $('#c-si', c).onclick = () => { cerrar(); res(true); }; $('#c-no', c).onclick = () => { cerrar(); res(false); };
  });
}
// Ofrece enviar un WhatsApp; resuelve cuando se cierra la ventana
function ofrecerWhatsApp(titulo, numero, texto) {
  return new Promise(res => {
    const c = modal(`<h2>${esc(titulo)}</h2><pre>${esc(texto)}</pre>
      ${numero ? '' : '<p class="err">El teléfono del cliente no parece un celular válido; se abrirá WhatsApp para elegir el contacto.</p>'}
      <div class="row"><a class="btn ok" id="wa-ir" target="_blank" rel="noopener"
        href="https://wa.me/${numero || ''}?text=${encodeURIComponent(texto)}">Enviar por WhatsApp</a>
      <button class="btn" id="wa-no">No enviar</button></div>`);
    $('#wa-ir', c).onclick = () => setTimeout(() => { cerrar(); res(true); }, 300);
    $('#wa-no', c).onclick = () => { cerrar(); res(false); };
  });
}
