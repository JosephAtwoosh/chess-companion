// Chess Companion front end. All rules, games and coaching live on the server; this file only draws the
// board, sends moves and questions to api.php, and speaks the coach's answers.
import { Chessground } from './vendor/chessground.min.js';
import { Chess } from './vendor/chess.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const S = { token: localStorage.getItem('cc.token'), player: null, game: null, lastSpeech: '', busy: false, sending: false,
            sound: localStorage.getItem('cc.sound') !== 'off', stars: localStorage.getItem('cc.stars') !== 'off' };
let cg = null;

// ------------------------------------------------------------------ server
async function api(op, args = {}) {
  let r;
  try {
    r = await fetch('api.php', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CC-Token': S.token || '' },
                                 body: JSON.stringify({ op, ...args }) });
  } catch (e) { throw new Error('No connection. Check the internet and try again.'); }
  let j;
  try { j = await r.json(); } catch (e) { throw new Error('The chess computer is not answering.'); }
  if (!j.ok) {
    if (j.error === 'auth') { localStorage.removeItem('cc.token'); S.token = null; show('login'); }
    throw new Error(j.error || 'Something went wrong.');
  }
  return j;
}

function show(name) {
  $$('.screen').forEach((s) => (s.hidden = s.id !== 's-' + name));
}
let toastTimer = 0;
function toast(msg) {   // errors (e.g. the chess computer not answering) must be impossible to miss
  setStatus(msg, true);
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 7000);
}
function setStatus(msg, warn = false) { const s = $('#status'); s.textContent = msg; s.style.color = warn ? 'var(--accent)' : ''; }

// ------------------------------------------------------------------ speech
let voice = null;
function pickVoice() {
  const vs = speechSynthesis.getVoices();
  voice = vs.find((v) => v.lang === 'en-GB' && /female|libby|sonia|serena|kate|google uk english female/i.test(v.name))
       || vs.find((v) => v.lang === 'en-GB') || vs.find((v) => v.lang.startsWith('en')) || null;
}
if ('speechSynthesis' in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

function speak(text, caption = true) {
  S.lastSpeech = text;
  $('#caption').textContent = caption ? text : '';
  if (!S.sound || !('speechSynthesis' in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  if (voice) u.voice = voice;
  u.lang = 'en-GB';
  u.rate = S.player && S.player.age_group === 'child' ? 0.92 : 1.0;
  speechSynthesis.speak(u);
}

let actx = null;
function click(capture) {   // a small wooden "tock" for moves
  if (!S.sound) return;
  try {
    actx = actx || new AudioContext();
    const o = actx.createOscillator(), g = actx.createGain();
    o.frequency.value = capture ? 220 : 330; o.type = 'triangle';
    g.gain.setValueAtTime(0.25, actx.currentTime); g.gain.exponentialRampToValueAtTime(0.001, actx.currentTime + 0.12);
    o.connect(g).connect(actx.destination); o.start(); o.stop(actx.currentTime + 0.13);
  } catch (e) {}
}

// ------------------------------------------------------------------ board
function dests(chess) {
  const d = new Map();
  for (const m of chess.moves({ verbose: true })) {
    if (!d.has(m.from)) d.set(m.from, []);
    d.get(m.from).push(m.to);
  }
  return d;
}

function drawGame(g, animate = true) {
  S.game = g;
  const chess = new Chess(g.fen);
  const mine = g.color;
  const last = g.last ? [g.last.slice(0, 2), g.last.slice(2, 4)] : undefined;
  const cfg = {
    fen: g.fen, orientation: mine, turnColor: g.turn, lastMove: last, check: g.check ? g.turn : false,
    animation: { enabled: animate, duration: 220 },
    movable: { free: false, color: g.your_turn ? mine : undefined, dests: g.your_turn ? dests(chess) : new Map(), showDests: true,
               events: { after: onUserMove } },
    premovable: { enabled: false }, draggable: { enabled: true, showGhost: true },
    highlight: { lastMove: true, check: true }, drawable: { enabled: true, visible: true, autoShapes: [] },
    coordinates: true,
  };
  if (!cg) { cg = Chessground($("#board"), cfg); window._cg = cg; } else cg.set(cfg);
  cg.setAutoShapes([]);
  $('#g-level').textContent = 'Opponent ' + g.level;
  $('#b-undo').disabled = S.sending || g.status !== 'playing' || !g.moves.length;
  if (g.status === 'playing') {
    setStatus(g.your_turn ? (g.check ? 'Check! Your move.' : 'Your move') : 'Computer is thinking…');
    $('#banner').hidden = true;
  } else {
    setStatus('');
    showResult(g);
  }
}

// 1-5 stars for the player's last move, so they can take it back and try again
function showMoveRating(g) {
  const el = $('#moverate');
  const r = g && g.last_rating;
  $('#b-undo').classList.toggle('suggest', !!(S.stars && r && g.status === 'playing' && r.stars <= 2));
  if (!S.stars || !r || g.status !== 'playing') { el.innerHTML = ''; el.className = 'moverate'; return ''; }
  el.className = 'moverate' + (r.stars <= 2 ? ' low' : '');
  el.innerHTML = `<span class="st" aria-label="${r.stars} out of 5 stars">${'★'.repeat(r.stars)}<i>${'★'.repeat(5 - r.stars)}</i></span> ${esc(r.san)}: ${esc(r.label)}`;
  return r.label;
}

function showResult(g) {
  const b = $('#banner');
  const head = g.score === 1 ? 'You won! 🎉' : g.score === 0.5 ? 'A draw 🤝' : (g.termination === 'resigned' ? 'Game resigned' : 'The computer won');
  const how = { checkmate: 'by checkmate', stalemate: 'stalemate', resigned: '', 'insufficient material': 'not enough pieces to mate',
               'threefold repetition': 'repetition', 'fifty moves': '50-move rule', 'move limit': 'move limit' }[g.termination] || g.termination || '';
  const r = g.review && g.review.rating;
  const stars = r ? `<div class="stars" aria-label="${r.stars} out of 5 stars">${'★'.repeat(r.stars)}<span>${'★'.repeat(5 - r.stars)}</span></div>` : '';
  const report = r ? `<p class="report">${esc(r.text.replace(r.headline, '').trim())}</p>` : '';
  const more = g.rating_change ? `Next opponent: ${g.rating_change.after}.` : '';
  b.innerHTML = `<b>${head}</b>${stars}<div class="small">${how}</div>${report}<div class="small">${more}</div><button class="primary" id="b-again">New game</button>`;
  b.hidden = false;
  $('#b-again').onclick = () => openNew();
}

// While a move or an undo is on its way to the server, Undo is greyed out and extra taps are ignored.
// Without this, a double tap or a tap during "Computer is thinking…" sent two requests; the answers
// could arrive in the wrong order, the board then showed a position the server no longer had, every
// next move was refused and the game looked frozen.
function sending(on) {
  S.sending = on;
  $('#b-undo').disabled = on || !S.game || S.game.status !== 'playing' || !S.game.moves.length;
}

// After an error, fetch the real position from the server rather than redrawing what we had,
// so the board can never get stuck out of step with the server.
async function resync() {
  if (!S.game) return;
  try { const { game } = await api('game_open', { gid: S.game.id }); drawGame(game, false); showMoveRating(null); }
  catch (e) { drawGame(S.game, false); }
}

async function onUserMove(orig, dest) {
  const chess = new Chess(S.game.fen);
  const piece = chess.get(orig);
  let promo = '';
  if (piece && piece.type === 'p' && (dest[1] === '8' || dest[1] === '1')) {
    promo = await askPromotion(piece.color);
  }
  const uci = orig + dest + promo;
  cg.set({ movable: { color: undefined } });
  sending(true);
  click(!!chess.get(dest));
  setStatus('Computer is thinking…');
  cg.setAutoShapes([]);
  try {
    const { game, feedback } = await api('move', { gid: S.game.id, uci });
    sending(false);
    if (game.moves.length > S.game.moves.length + 1) setTimeout(() => click(false), 50);
    drawGame(game);
    showMoveRating(game);   // stars are shown, never spoken (too distracting)
    const ended = game.status === 'finished' && game.review && game.review.rating ? game.review.rating.text : '';
    // spoken only: the verdict on a move played after a hint, and the end-of-game report card
    const say = [feedback ? feedback.text : '', ended].filter(Boolean).join(' ');
    if (say) speak(say);
    else if (game.last_rating && game.last_rating.stars <= 2 && S.stars) $('#caption').textContent = 'Press Undo to try a different move.';
    else $('#caption').textContent = '';
  } catch (e) {
    sending(false);
    toast(e.message);
    resync();
  }
}

function askPromotion(color) {
  return new Promise((res) => {
    const p = $('#promo');
    const sym = color === 'w' ? { q: '♕', r: '♖', b: '♗', n: '♘' } : { q: '♛', r: '♜', b: '♝', n: '♞' };
    p.innerHTML = Object.entries(sym).map(([k, s]) => `<button data-p="${k}" aria-label="${k}">${s}</button>`).join('');
    p.hidden = false;
    p.onclick = (e) => { const b = e.target.closest('button'); if (!b) return; p.hidden = true; res(b.dataset.p); };
  });
}

// ------------------------------------------------------------------ coach
async function ask(kind, btn) {
  if (!S.game || S.busy) return;
  if (S.game.status !== 'playing') { speak('This game is finished. Start a new game and I will help you again.'); return; }
  S.busy = true; btn.classList.add('busy');
  if ('speechSynthesis' in window) speechSynthesis.cancel();
  const prev = $('#status').textContent;
  setStatus('Coach is thinking…');
  try {
    const r = await api('coach', { gid: S.game.id, kind });
    cg.setAutoShapes((r.shapes || []).map((s) => ({ orig: s.orig, dest: s.dest, brush: s.brush })));
    speak(r.text);
    setStatus(prev);
  } catch (e) { toast(e.message); }
  finally { S.busy = false; btn.classList.remove('busy'); }
}

// ------------------------------------------------------------------ screens
async function boot() {
  try {
    const st = await api('status');
    if (!st.setup) { if (st.local) show('setup'); else { $('#loading-msg').textContent = 'Not set up yet. Open Chess Companion on the server itself first (or set CC_FAMILY_PASSWORD and CC_ADMIN_PASSWORD).'; } return; }
    if (!st.authed) { show('login'); return; }
    await players();
  } catch (e) { $('#loading-msg').textContent = e.message; setTimeout(boot, 8000); }
}

async function players() {
  const { profiles } = await api('profiles');
  const list = $('#player-list');
  list.innerHTML = '';
  for (const p of profiles) {
    const b = document.createElement('button');
    b.className = 'player';
    b.innerHTML = `<span></span><small>${p.games} game${p.games === 1 ? '' : 's'}</small>`;
    b.firstChild.textContent = p.name;
    b.onclick = () => choosePlayer(p);
    list.append(b);
  }
  $('.new-player').open = profiles.length === 0;
  show('players');
  const last = +localStorage.getItem('cc.player');
  if (last && S.autoResume !== false) { const p = profiles.find((x) => x.id === last); S.autoResume = false; if (p) choosePlayer(p); }
}

async function choosePlayer(p) {
  S.player = p;
  localStorage.setItem('cc.player', p.id);
  $('#g-name').textContent = p.name;
  show('game');
  $('#caption').textContent = '';
  const { game } = await api('game_current', { pid: p.id });
  if (game) { drawGame(game, false); showMoveRating(game); setStatus(game.your_turn ? 'Welcome back! Your move.' : 'Computer is thinking…'); }
  else { drawGame({ id: 0, fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', color: 'white', moves: [], turn: 'white',
                    status: 'none', level: p.level, your_turn: false }, false);
         $('#banner').hidden = true; setStatus(''); openNew(); }
}

function openNew() {
  const playing = S.game && S.game.status === 'playing';
  const mine = playing ? Math.floor((S.game.moves.length + (S.game.color === 'white' ? 1 : 0)) / 2) : 0;
  $('#new-warn').hidden = !(playing && mine >= 4);
  $('#d-new').showModal();
}

async function newGame(color) {
  $('#d-new').close();
  setStatus('Setting up the board…');
  try { const { game } = await api('game_new', { pid: S.player.id, color }); drawGame(game); showMoveRating(null); $('#caption').textContent = ''; }
  catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ history / pgn
function download(name, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/x-chess-pgn' }));
  a.download = name; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
async function exportPgn(gid) {
  try {
    const { pgn } = await api('pgn', { gid });
    if (navigator.clipboard) navigator.clipboard.writeText(pgn).catch(() => {});
    download(`chess-companion-${gid}.pgn`, pgn);
  } catch (e) { toast(e.message); }
}

function spark(hist) {
  if (hist.length < 2) return '';
  const lv = hist.map((h) => h.level), lo = Math.min(...lv), hi = Math.max(...lv), span = Math.max(50, hi - lo);
  const pts = lv.map((v, i) => `${(i / (lv.length - 1)) * 300},${55 - ((v - lo) / span) * 50}`).join(' ');
  return `<svg class="spark" viewBox="0 0 300 60" preserveAspectRatio="none" aria-label="Opponent strength over time">
    <polyline points="${pts}" fill="none" stroke="#f0b429" stroke-width="3"/></svg>
    <p class="small">Opponent strength needed to keep your win rate (from ${lo} to ${hi}). Going up means you are improving.</p>`;
}

async function history() {
  const { games, stats } = await api('history', { pid: S.player.id });
  const pct = (x) => (x == null ? '–' : Math.round(x * 100) + '%');
  $('#h-stats').innerHTML = `<div class="stats">
      <div>Games<b>${stats.games}</b></div><div>Wins (last 20)<b>${pct(stats.win_rate_last20)}</b></div>
      <div>Your target<b>${pct(stats.target)}</b></div><div>Opponent now<b>${stats.opponent_level_now}</b></div></div>
      ${spark(stats.level_history)}
      ${stats.habits.length ? '<p class="small">Things to work on: ' + stats.habits.map((h) => h.habit).join('; ') + '.</p>' : ''}`;
  const list = $('#h-list');
  list.innerHTML = '';
  for (const g of games) {
    const row = document.createElement('div');
    row.className = 'hrow';
    const res = g.status === 'playing' ? ['…', 'd'] : g.score === 1 ? ['W', 'w'] : g.score === 0 ? ['L', 'l'] : ['½', 'd'];
    const when = new Date(g.started * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
    row.innerHTML = `<span class="res ${res[1]}">${res[0]}</span><span>${when} · ${g.color} · ${Math.ceil(g.moves / 2)} move${Math.ceil(g.moves / 2) === 1 ? "" : "s"}<br><small class="small">opponent ${g.level}${g.termination ? ' · ' + g.termination : ''}</small></span><button>PGN</button>`;
    row.querySelector('button').onclick = () => exportPgn(g.id);
    list.append(row);
  }
  $('#d-history').showModal();
}

// ------------------------------------------------------------------ admin
async function adminOpen(pw) {
  const body = $('#admin-body');
  try {
    const r = await api('admin', { admin_password: pw, action: 'overview' });
    S.adminPw = pw;
    $('#f-admin').hidden = true;
    const all = r.ai.subscription_profiles === 'all';
    const sub = new Set(all ? [] : r.ai.subscription_profiles);
    body.innerHTML = `<h3>AI coaching</h3>
      <p class="small">Ticked players get the coach's words from AI (${esc(r.ai.providers.join(', ') || 'none set up')}).
       With a personal subscription, tick only the account holder. Everyone else gets spoken coaching straight from the chess engine.
       ChatGPT sign-in: ${r.ai.siwc_signed_in ? 'connected' : 'not connected'}.</p>
      ${r.profiles.map((p) => `<label class="admin-row"><span>${esc(p.name)}</span><input type="checkbox" data-sub="${p.id}" ${all || sub.has(p.id) ? 'checked' : ''}></label>`).join('')}
      <h3>Devices</h3>
      ${r.devices.map((d) => `<div class="admin-row"><span>${esc(d.name || 'device')} <small class="small">${d.revoked ? '(removed)' : 'seen ' + new Date(d.last_seen * 1000).toLocaleDateString('en-GB')}</small></span>${d.revoked ? '' : `<button data-rev="${d.id}">Remove</button>`}</div>`).join('')}
      <h3>Usage today</h3><p class="small">${r.usage_24h.map((u) => `${u.provider}: ${u.n}`).join(', ') || 'none'}</p>
      <div class="stack"><button id="a-backup">Back up now</button></div>`;
    body.querySelectorAll('[data-sub]').forEach((c) => (c.onchange = async () => {
      const ids = [...body.querySelectorAll('[data-sub]:checked')].map((x) => +x.dataset.sub);
      await api('admin', { admin_password: S.adminPw, action: 'ai_set', subscription_profiles: ids });
    }));
    body.querySelectorAll('[data-rev]').forEach((b) => (b.onclick = async () => {
      await api('admin', { admin_password: S.adminPw, action: 'revoke', device_id: +b.dataset.rev }); adminOpen(S.adminPw);
    }));
    $('#a-backup').onclick = async () => { const x = await api('admin', { admin_password: S.adminPw, action: 'backup' }); $('#a-backup').textContent = 'Saved ' + x.file; };
  } catch (e) { body.innerHTML = `<p class="warn">${esc(e.message)}</p>`; }
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

// ------------------------------------------------------------------ wiring
$('#f-setup').onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { const r = await api('setup', { family: f.get('family'), admin: f.get('admin'), device: 'server' });
        S.token = r.token; localStorage.setItem('cc.token', r.token); players(); }
  catch (err) { alert(err.message); }
};
$('#f-login').onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { const r = await api('login', { password: f.get('password'), device: f.get('device') || navigator.platform || 'device' });
        S.token = r.token; localStorage.setItem('cc.token', r.token); players(); }
  catch (err) { alert(err.message); }
};
$('#f-player').onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { const { profile } = await api('profile_create', { name: f.get('name'), age_group: f.get('age_group') }); e.target.reset(); choosePlayer(profile); }
  catch (err) { alert(err.message); }
};
$$('.coach button').forEach((b) => (b.onclick = () => ask(b.dataset.k, b)));
$('#b-repeat').onclick = () => { if (S.lastSpeech) speak(S.lastSpeech); };
$('#b-new').onclick = openNew;
$('#b-undo').onclick = async () => {
  if (S.sending || !S.game) return;        // a second tap (or a tap while the computer thinks) is ignored
  sending(true);
  try { const { game } = await api('undo', { gid: S.game.id }); sending(false); drawGame(game); showMoveRating(null); $('#caption').textContent = '';
        if (S.stars) setStatus('Taken back. Try another move!'); }
  catch (e) { sending(false); toast(e.message); resync(); }
};
function soundIcon() { $('#b-sound').textContent = S.sound ? '🔊' : '🔇'; }
function starsLabel() { $('#m-stars').textContent = 'Move stars: ' + (S.stars ? 'on' : 'off'); }
$('#b-sound').onclick = () => { S.sound = !S.sound; localStorage.setItem('cc.sound', S.sound ? 'on' : 'off'); soundIcon();
  if (!S.sound) speechSynthesis.cancel(); };
soundIcon(); starsLabel();
$('#b-menu').onclick = () => $('#d-menu').showModal();
$$('#d-new [data-c]').forEach((b) => (b.onclick = () => (b.dataset.c === 'cancel' ? $('#d-new').close() : newGame(b.dataset.c))));
$$('dialog [data-close]').forEach((b) => (b.onclick = () => b.closest('dialog').close()));
$$('#d-menu [data-m]').forEach((b) => (b.onclick = async () => {
  $('#d-menu').close();
  const m = b.dataset.m;
  if (m === 'new') openNew();
  if (m === 'stars') {
    S.stars = !S.stars; localStorage.setItem('cc.stars', S.stars ? 'on' : 'off'); starsLabel(); showMoveRating(S.game);
  }
  if (m === 'challenge') {
    const v = Math.round((S.player.target || 0.65) * 100);
    $('#ch-range').value = v; $('#ch-val').textContent = v; $('#d-challenge').showModal();
  }
  if (m === 'history') history().catch((e) => toast(e.message));
  if (m === 'pgn' && S.game && S.game.id) exportPgn(S.game.id);
  if (m === 'resign' && S.game && S.game.status === 'playing' && confirm('Resign this game? It counts as a loss.')) {
    try { const { game } = await api('resign', { gid: S.game.id }); drawGame(game);
          if (game.review && game.review.rating) speak(game.review.rating.text); } catch (e) { toast(e.message); }
  }
  if (m === 'players') { S.autoResume = false; players(); }
  if (m === 'admin') { $('#f-admin').hidden = false; $('#admin-body').innerHTML = ''; $('#d-admin').showModal(); }
}));
$('#ch-range').oninput = (e) => ($('#ch-val').textContent = e.target.value);
$('#ch-save').onclick = async () => {
  try { const { profile } = await api('profile_settings', { pid: S.player.id, target: +$('#ch-range').value / 100 });
        S.player = profile; $('#d-challenge').close();
        if (S.game && S.game.moves.length <= 2 && S.game.status === 'playing') { const { game } = await api('game_current', { pid: profile.id }); drawGame(game, false); } }
  catch (e) { toast(e.message); }
};
$('#f-admin').onsubmit = (e) => { e.preventDefault(); adminOpen(new FormData(e.target).get('pw')); e.target.reset(); };

if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
boot();
