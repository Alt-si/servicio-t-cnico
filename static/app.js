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

// ----- Recordatorios: aviso cuando llega la hora (consulta cada 20 s)
function sonido() {
  try {
    const a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator(), g = a.createGain();
    o.connect(g); g.connect(a.destination); o.frequency.value = 880; g.gain.value = 0.15; o.start();
    setTimeout(() => { o.stop(); a.close(); }, 600);
  } catch (e) {}
}
const colaAvisos = []; let mostrando = false;
function siguienteAviso() {
  if (mostrando || !colaAvisos.length) return;
  mostrando = true;
  const r = colaAvisos.shift();
  const c = modal(`<div style="background:#f97316;color:#fff;margin:-18px -18px 12px;padding:12px 18px;border-radius:10px 10px 0 0">
      <b style="font-size:16px">RECORDATORIO</b><br>${esc(r.cuando)}</div>
    <p style="font-size:17px;white-space:pre-wrap">${esc(r.descripcion)}</p>
    <div class="row"><button class="btn pri" id="r-hecho">Hecho</button><button class="btn" id="r-pos">Posponer 10 min</button><button class="btn" id="r-cer">Cerrar</button></div>`);
  sonido();
  const fin = async (acc) => { if (acc) await api(`/api/recordatorio/${r.id}/${acc}`, {}); cerrar(); mostrando = false; revisarRecordatorios(); siguienteAviso(); };
  $('#r-hecho', c).onclick = () => fin('hecho'); $('#r-pos', c).onclick = () => fin('posponer'); $('#r-cer', c).onclick = () => fin(null);
  try { if (window.Notification && Notification.permission === 'granted') new Notification('Recordatorio', {body: r.descripcion}); } catch (e) {}
}
async function revisarRecordatorios() {
  if (!window.HAY_SESION) return;
  const j = await api('/api/recordatorios/avisos');
  if (!j.ok) return;
  const n = $('#nav-rec'); if (n) n.textContent = j.pendientes ? `Recordatorios (${j.pendientes})` : 'Recordatorios';
  (j.avisos || []).forEach(a => colaAvisos.push(a)); siguienteAviso();
}
if (window.HAY_SESION) {
  try { if (window.Notification && Notification.permission === 'default') Notification.requestPermission(); } catch (e) {}
  revisarRecordatorios(); setInterval(revisarRecordatorios, 20000);
}

// cerrar el submenú de Órdenes al hacer clic afuera
document.addEventListener('click', e => { const d = document.querySelector('.menu-ord'); if (d && !d.contains(e.target)) d.open = false; });

// ----- Montos: miles con punto (1.000 / 1.000.000) y centavos con coma, mientras se escribe
function formatearMonto(txt) {
  txt = txt.replace(/[^\d,]/g, '');
  const i = txt.indexOf(',');
  let ent = i < 0 ? txt : txt.slice(0, i), dec = i < 0 ? null : txt.slice(i + 1).replace(/,/g, '').slice(0, 2);
  ent = ent.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return dec === null ? ent : ent + ',' + dec;
}
function aplicarMoneda(el) {
  const antes = el.value, pos = el.selectionStart ?? antes.length;
  const sig = antes.slice(0, pos).replace(/[^\d,]/g, '').length;      // dígitos/coma a la izquierda del cursor
  const nuevo = formatearMonto(antes);
  if (nuevo === antes) return;
  el.value = nuevo;
  let n = 0, p = 0;
  while (p < nuevo.length && n < sig) { if (/[\d,]/.test(nuevo[p])) n++; p++; }
  try { el.setSelectionRange(p, p); } catch (e) {}
}
document.addEventListener('input', e => { if (e.target.classList?.contains('moneda')) aplicarMoneda(e.target); });
document.addEventListener('keydown', e => {      // el punto del teclado numérico cuenta como coma decimal
  const el = e.target;
  if (el.classList?.contains('moneda') && e.key === '.' && !el.value.includes(',')) {
    e.preventDefault(); el.setRangeText(',', el.selectionStart, el.selectionEnd, 'end'); aplicarMoneda(el);
  } else if (el.classList?.contains('moneda') && e.key === '.') e.preventDefault();
});

// ----- Texto de datos siempre en MAYÚSCULAS (campos con clase "mayus")
document.addEventListener('input', e => {
  const el = e.target;
  if (!el.classList?.contains('mayus') || e.isComposing) return;
  const a = el.selectionStart, b = el.selectionEnd, up = el.value.toUpperCase();
  if (up !== el.value) { el.value = up; try { el.setSelectionRange(a, b); } catch (x) {} }
});
