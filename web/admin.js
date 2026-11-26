// Chess Companion admin page: devices, banned addresses, recent sign-ins.
// Everything shown here comes from visitors (device names, browser strings), so it is only ever set as text.
'use strict';
const $ = (s) => document.querySelector(s);
let adminPw = '';   // kept in memory only

async function api(action, args = {}) {
  const r = await fetch('api.php', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ op: 'local_admin', action, admin_password: adminPw, ...args }) });
  let j;
  try { j = await r.json(); } catch (e) { throw new Error('The chess computer is not answering.'); }
  if (!j.ok) throw new Error(j.error || 'Something went wrong.');
  return j;
}

let msgTimer = 0;
function say(text, err = false) {
  const m = $('#msg'); m.textContent = text; m.className = err ? 'err' : ''; m.hidden = false;
  clearTimeout(msgTimer); msgTimer = setTimeout(() => (m.hidden = true), 5000);
}

function el(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; }
function when(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000), mins = (Date.now() - d) / 60000;
  if (mins < 2) return 'just now';
  if (mins < 60) return `${Math.round(mins)} min ago`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
}
function kind(ua) {   // a short, friendly description of a browser string
  if (!ua) return '—';
  const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? (/Mobile/.test(ua) ? 'Android phone' : 'Android tablet')
    : /Windows/.test(ua) ? 'Windows' : /Mac OS X|Macintosh/.test(ua) ? 'Mac' : /CrOS/.test(ua) ? 'Chromebook' : /Linux/.test(ua) ? 'Linux' : '';
  const br = /Edg\//.test(ua) ? 'Edge' : /SamsungBrowser/.test(ua) ? 'Samsung Internet' : /Firefox|FxiOS/.test(ua) ? 'Firefox'
    : /Chrome|CriOS/.test(ua) ? 'Chrome' : /Safari/.test(ua) ? 'Safari' : '';
  return [br, os].filter(Boolean).join(' on ') || ua.slice(0, 40);
}
function btn(text, cls, fn) {
  const b = el('button', text, cls);
  b.onclick = async () => { b.disabled = true; try { await fn(); await load(); } catch (e) { say(e.message, true); } b.disabled = false; };
  return b;
}
function row(cells, acts) {
  const tr = el('tr');
  for (const c of cells) { const td = el('td'); if (c instanceof Node) td.append(c); else td.textContent = c ?? ''; tr.append(td); }
  const td = el('td', null, 'acts'); acts.forEach((a) => a && td.append(a)); tr.append(td);
  return tr;
}
function cellWithTitle(text, title, cls) { const s = el('span', text, cls); if (title) s.title = title; return s; }

async function load() {
  const d = await api('overview');
  $('#pw').hidden = true; $('#body').hidden = false;
  $('#you').textContent = `Signed in as admin from ${d.you}`;

  const dev = $('#devices'); dev.replaceChildren();
  for (const x of d.devices) {
    const st = x.banned ? 'banned' : x.revoked ? 'removed' : 'active';
    const statusCell = el('span'); statusCell.append(el('span', st, 'tag ' + st));
    if (x.ip_banned) statusCell.append(' ', el('span', 'address banned', 'tag banned'));
    const name = x.name || 'device';
    dev.append(row([name, cellWithTitle(kind(x.ua), x.ua), cellWithTitle(x.last_ip || '—', '', 'mono'),
                    cellWithTitle(when(x.created), '', 'hide-sm'), when(x.last_seen), statusCell], x.banned ? [
      btn('Unban', '', async () => { await api('unban_device', { device_id: x.id }); say(`${name} unbanned. It needs the family password again.`); }),
    ] : [
      btn('Ban', 'ban', async () => {
        if (!confirm(`Ban "${name}"? It is signed out at once and can't sign in again.`)) throw new Error('Cancelled.');
        await api('ban_device', { device_id: x.id }); say(`${name} banned.`); }),
      x.last_ip && !x.ip_banned ? btn('Ban + address', 'ban', async () => {
        if (!confirm(`Ban "${name}" and every device on ${x.last_ip}?`)) throw new Error('Cancelled.');
        await api('ban_device', { device_id: x.id, with_address: true }); say(`${name} and ${x.last_ip} banned.`); }) : null,
      !x.revoked ? btn('Sign out', '', async () => { await api('remove_device', { device_id: x.id }); say(`${name} signed out. It can sign in again with the family password.`); }) : null,
    ]));
  }
  if (!d.devices.length) dev.append(row([el('span', 'No devices yet.', 'empty')], []));

  const bans = $('#bans'); bans.replaceChildren();
  for (const b of d.bans) {
    bans.append(row([cellWithTitle(b.ip, '', 'mono'), b.note, when(b.created)],
      [btn('Unban', '', async () => { await api('unban_ip', { ban_id: b.id }); say(`${b.ip} unbanned.`); })]));
  }
  if (!d.bans.length) bans.append(row([el('span', 'No banned addresses.', 'empty')], []));

  const log = $('#log'); log.replaceChildren();
  const words = { signed_in: 'signed in', wrong_password: 'wrong password', blocked: 'blocked' };
  for (const r of d.log) {
    const res = el('span'); res.append(el('span', words[r.event] || r.event, 'tag ' + r.event));
    log.append(row([when(r.ts), cellWithTitle(r.ip, '', 'mono'), cellWithTitle(kind(r.ua), r.ua), r.detail || r.device || '', res],
      [r.ip_banned ? el('span', 'address banned', 'small') : btn('Ban address', 'ban', async () => {
        if (!confirm(`Ban ${r.ip}? Every device on that address will be refused.`)) throw new Error('Cancelled.');
        await api('ban_ip', { ip: r.ip, note: r.detail ? `sign-in as "${r.detail}"` : '' }); say(`${r.ip} banned.`); })]));
  }
  if (!d.log.length) log.append(row([el('span', 'Nothing yet. Sign-ins are recorded from now on.', 'empty')], []));
}

$('#f-ban').onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try { const r = await api('ban_ip', { ip: f.ip.value, note: f.note.value }); f.reset(); say(`${r.ip} banned.`); await load(); }
  catch (err) { say(err.message, true); }
};
$('#pw').onsubmit = async (e) => {
  e.preventDefault();
  adminPw = e.target.pw.value;
  try { await load(); } catch (err) { say(err.message, true); }
};
$('#refresh').onclick = () => load().catch((e) => say(e.message, true));

load().catch((e) => {
  if (/admin password|Locked/.test(e.message)) { $('#pw').hidden = false; if (adminPw) say(e.message, true); adminPw = ''; }
  else say(e.message, true);
});
