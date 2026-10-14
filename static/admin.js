// O'qituvchi sahifalari uchun umumiy yordamchilar: API, parol, xabarlar
const TOKEN_KEY = 'kahoot_admin_token';
const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
const setToken = t => { try { localStorage.setItem(TOKEN_KEY, t); } catch {} };

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': getToken(), ...(opts.headers || {}) },
    body: opts.body && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
  });
  if (r.status === 401) { await askPassword(); return api(path, opts); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'Server xatosi');
  return data;
}

// Parol so'rash oynasi (ADMIN_PASSWORD o'rnatilgan bo'lsa)
function askPassword() {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'modal-bg';
    wrap.innerHTML = `
      <form class="modal" style="max-width:340px">
        <h2>🔒 O‘qituvchi paneli</h2>
        <input class="input" type="password" placeholder="Parol" autofocus>
        <div class="err"></div>
        <button class="btn primary">Kirish</button>
      </form>`;
    document.body.appendChild(wrap);
    const form = wrap.querySelector('form'), inp = wrap.querySelector('input');
    inp.focus();
    form.onsubmit = async e => {
      e.preventDefault();
      const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: inp.value }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { wrap.querySelector('.err').textContent = d.detail || 'Xato'; inp.select(); return; }
      setToken(d.token); wrap.remove(); resolve();
    };
  });
}

async function ensureAuth() {
  const s = await fetch('/api/auth', { headers: { 'X-Admin-Token': getToken() } }).then(r => r.json());
  if (s.required && !s.ok) await askPassword();
}

function toast(msg, bad = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (bad ? ' bad' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.classList.add('out'), 2200);
  setTimeout(() => t.remove(), 2600);
}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const SHAPES = [
  '<svg viewBox="0 0 32 32"><path d="M16 3 30 28H2z"/></svg>',
  '<svg viewBox="0 0 32 32"><path d="M16 1 31 16 16 31 1 16z"/></svg>',
  '<svg viewBox="0 0 32 32"><circle cx="16" cy="16" r="14"/></svg>',
  '<svg viewBox="0 0 32 32"><rect x="3" y="3" width="26" height="26"/></svg>',
];

/* Matndan import: chatdagi formatni tushunadi
   ━━━ 1. SELECT ━━━         -> mavzu
   3. Savol matni? (20s)     -> savol (+ vaqt)
   SELECT ...                -> savolga kod (javoblardan oldingi qatorlar)
   A) javob ✅                -> javob, ✅ yoki * = to'g'ri                       */
function parseQuizText(text) {
  const lines = text.replace(/\r/g, '').split('\n');
  const out = []; let topic = '', cur = null;
  const flush = () => { if (cur && cur.q && cur.answers.length >= 2) out.push(cur); cur = null; };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (/^MAVZU\s*:/i.test(line)) continue;
    const head = line.match(/^[━─=\-#*]{2,}\s*(?:\d+\.\s*)?(.*?)\s*[━─=\-#*]*$/);
    if (head) { flush(); topic = head[1].trim(); continue; }
    const ans = line.match(/^([A-Da-d])[\)\.]\s*(.+)$/);
    if (ans && cur) {
      let a = ans[2]; let ok = false;
      if (/✅|\(\s*to‘?'?g‘?'?ri\s*\)|\*\s*$/i.test(a)) ok = true;
      a = a.replace(/✅/g, '').replace(/\*\s*$/, '').replace(/\(\s*to‘?'?g‘?'?ri\s*\)/i, '').trim();
      if (ok) cur.correct = cur.answers.length;
      cur.answers.push(a);
      continue;
    }
    const q = line.match(/^(\d+)[\.\)]\s*(.+)$/);
    if (q) {
      flush();
      let text = q[2], time = 20;
      const tm = text.match(/\((\d+)\s*s(?:ek|oniya)?\)\s*$/i);
      if (tm) { time = +tm[1]; text = text.slice(0, tm.index).trim(); }
      cur = { topic, q: text, code: '', answers: [], correct: -1, time, code_answers: false, explain: '' };
      continue;
    }
    if (cur && !cur.answers.length) cur.code += (cur.code ? '\n' : '') + raw.replace(/\s+$/, '');
  }
  flush();
  // Savol ichiga yozilgan kodni ajratish: "Natija? SELECT ... ;" -> kod maydoniga
  for (const it of out) {
    const m = it.q.match(/^(.*?[?:])\s+((?:SELECT|CASE|ORDER BY|INSERT|UPDATE|DELETE|CREATE|WITH)\b.*)$/i);
    if (m && !it.code) { it.q = m[1].trim(); it.code = m[2].trim(); }
    const looksCode = it.answers.filter(a => /\b(SELECT|ORDER BY|LIMIT|OFFSET|FROM|CASE|WHERE)\b/.test(a)).length;
    if (looksCode >= 2) it.code_answers = true;
    if (![5, 10, 20, 30, 45, 60, 90, 120].includes(it.time)) it.time = 20;
  }
  return out;
}
