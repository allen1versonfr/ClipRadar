'use strict';
/* VodClipper UI. Sections: utilities, app shell, home, workspace (review),
   player, timeline, format, exports + TikTok, settings, boot. */

/* =============================================================== utilities */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = s => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`; };
const fmtBytes = b => b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b >= 1e6 ? Math.round(b / 1e6) + ' MB' : b > 0 ? Math.max(1, Math.round(b / 1e3)) + ' KB' : '0 MB';
const plural = (n, w, p) => `${n} ${n === 1 ? w : (p || w + 's')}`;
const ago = t => { const s = Date.now() / 1000 - t; return s < 90 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
const icon = (id, cls = '') => `<svg class="ic ${cls}"><use href="#i-${id}"/></svg>`;
const store = {
  get(k, d) { try { const v = localStorage.getItem('vc_' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('vc_' + k, JSON.stringify(v)); } catch {} },
};
async function api(path, body) {
  let r;
  try { r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}); }
  catch { throw new Error('Lost connection to VodClipper. Restart it if this keeps happening.'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
  return j;
}
function toast(msg, kind = '', ms = 4500, action = null) {
  const el = document.createElement('div');
  el.className = 'toast ' + kind; el.textContent = msg;
  if (action) {
    const b = document.createElement('button'); b.className = 'act'; b.textContent = action.label;
    b.onclick = () => { el.remove(); action.fn(); }; el.appendChild(b);
  }
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), ms);
  while ($('#toasts').children.length > 3) $('#toasts').firstChild.remove();
}
function ask({ title, body, ok = 'OK', danger = false }) {
  const d = $('#dlg');
  $('#dlgTitle').textContent = title; $('#dlgBody').textContent = body;
  $('#dlgYes').textContent = ok; $('#dlgYes').classList.toggle('danger', danger);
  return new Promise(res => {
    const done = v => { d.close(); $('#dlgYes').onclick = $('#dlgNo').onclick = d.oncancel = null; res(v); };
    $('#dlgYes').onclick = () => done(true);
    $('#dlgNo').onclick = () => done(false);
    d.oncancel = e => { e.preventDefault(); done(false); };
    d.showModal(); $('#dlgNo').focus();
  });
}
async function copyText(t, what = 'Caption') {
  try { await navigator.clipboard.writeText(t); }
  catch { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
  toast(`${what} copied.`, '', 2200);
}
const openUrl = url => api('/api/open_url', { url }).catch(e => toast(e.message, 'err'));
document.addEventListener('click', e => { const a = e.target.closest('a[data-url]'); if (a) { e.preventDefault(); openUrl(a.dataset.url); } });

// Update a list without re-creating thumbnails that are already loaded (they'd flash while live polling)
function patchList(box, html, key) {
  if (box._html === html) return;
  box._html = html;
  const tpl = document.createElement('template'); tpl.innerHTML = html;
  const old = new Map([...box.children].filter(e => e.dataset && e.dataset[key]).map(e => [e.dataset[key], e]));
  box.replaceChildren(...[...tpl.content.children].map(n => {
    const o = old.get(n.dataset[key]);
    const oi = o && o.querySelector('img'), ni = n.querySelector('img');
    if (oi && ni && oi.getAttribute('src') === ni.getAttribute('src')) ni.replaceWith(oi);
    return n;
  }));
}

/* =============================================================== state */
let status = {};
let view = 'home';
let W = null;                 // open workspace: {kind:'vod'|'live', ...}
let cur = null;               // key of the clip on the phone
let mode = 'clip';            // clip | free
let filter = 'todo';
let queueItems = [];
let exSel = null;
let ttStatus = {};
let safetyCfg = { enabled: true, censor: 'off', censor_categories: [] };
let vodJob = null, ttJob = null, setupJob = null;
const DEFAULTS = { layout: 'split', cam: [0, 0, 0.25, 0.25], cam_height: 0.33, game_x: 0.5, game_zoom: 1.0,
  captions: true, caption_pos: 'auto', caption_color: '#ffe600', caption_size: 100, words_per_line: 3, uppercase: true,
  caption_style: 'pop', caption_font: 'classic', caption_anim: true, cap_x: 0.5, cap_y: 0.7,
  title_style: 'box', title_font: 'classic', title_size: 100, title_x: 0.5, title_y: 0.05,
  whisper_model: 'small', language: '', hw_encode: true, aspect: '9:16' };
const opts = Object.assign({}, DEFAULTS, store.get('opts', {}));
const SWATCHES = ['#ffe600', '#ffffff', '#4fd1ff', '#5ee6a8', '#ff6b8b'];
const PRIVACY = { PUBLIC_TO_EVERYONE: 'Everyone', MUTUAL_FOLLOW_FRIENDS: 'Friends', FOLLOWER_OF_CREATOR: 'Followers', SELF_ONLY: 'Only me' };
const VIBE = { funny: 'Funny', hype: 'Hype', shock: 'Surprise', fail: 'Fail', wholesome: 'Wholesome' };

/* =============================================================== shell */
function setView(v) {
  if (v === 'review' && !W) { toast('Open a VOD or start live clipping first.'); v = 'home'; }
  view = v;
  $$('.rail button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  ['home', 'review', 'exports', 'settings'].forEach(n => $('#view-' + n).hidden = n !== v);
  if (v !== 'review') { vod.pause(); }
  if (v !== 'exports') { $('#exVideo').pause(); }
  if (v === 'review') { sizePhone(); drawHeat(); dirty = true; }
  if (v === 'exports') { refreshQueue(); if (exSel) renderDetail(true); }
  if (v === 'settings') { loadTikTok(); loadStorage(); renderTools(); loadSafety(); renderHotkey(); loadTwitch(); loadLearning(); }
  if (v === 'home') loadRecent();
  updateCrumb();
}
$$('.rail button').forEach(b => b.onclick = () => setView(b.dataset.view));
function updateCrumb() {
  $('#crumb').innerHTML = W ? `<span>${esc(W.title)}</span>` : '';
}

async function loadStatus() {
  try { status = await api('/api/status'); } catch { return; }
  $('#sbVer').textContent = 'v' + status.version; $('#verText').textContent = status.version;
  $('#quitBtn').hidden = !!status.window;
  $('#sbTools').innerHTML = [['ffmpeg', 'FFmpeg'], ['ytdlp', 'yt-dlp'], ['whisper', 'Whisper']]
    .map(([k, l]) => `<span class="sb-tool" title="${status[k] ? l + ' ready' : l + ' missing'}"><span class="status-dot ${status[k] ? 'on' : 'warn'}"></span>${l}</span>`).join('');
  $('#capWarn').textContent = status.whisper ? '' : 'Captions need Whisper, which isn\'t available in this install.';
  $('#events').disabled = status.laughter === false;
  $('#eventsHint').textContent = status.laughter === false ? 'The laughter detector isn\'t installed in this copy.' : 'Adds about a minute per hour of video.';
  $('#deep').disabled = status.whisper === false;
  if (status.whisper === false) $('#deepHint').textContent = 'Needs Whisper, which isn\'t available in this install.';
  const missing = [];
  if (!status.ffmpeg) missing.push(['ffmpeg', 'FFmpeg']);
  if (!status.ytdlp) missing.push(['ytdlp', 'yt-dlp']);
  if (!status.twitchdownloader) missing.push(['twitchdownloader', 'TwitchDownloader']);
  toolsMissing = missing.map(m => m[0]);
  if (missing.length && status.can_download_tools) {
    $('#toolsText').innerHTML = `<b>${missing.map(m => m[1]).join(', ')} ${missing.length === 1 ? 'is' : 'are'} missing.</b> VodClipper needs ${missing.length === 1 ? 'it' : 'them'} to download and cut video (about ${!status.ffmpeg ? 130 : 40} MB).`;
    $('#getTools').hidden = false; $('#toolsBanner').hidden = false;
  } else if (!status.ffmpeg) {
    $('#toolsText').innerHTML = '<b>FFmpeg is missing.</b> Install FFmpeg and add it to your PATH, then restart VodClipper.';
    $('#getTools').hidden = true; $('#toolsBanner').hidden = false;
  } else $('#toolsBanner').hidden = true;
  if (status.live && (!W || (W.kind === 'live' && W.sid !== status.live))) {
    // live clipping is running (the page was reloaded, or 'keep watching' moved on to the next stream)
    const prev = W && W.kind === 'live' ? W : null;
    try {
      openLive(await api('/api/live/' + status.live), false);
      if (prev) toast(`Stream ended with ${plural(prev.clips.length, 'clip')}. They're in Exports. Waiting for the next stream.`, '', 8000);
    } catch {}
  }
  if (view === 'settings') renderTools();
  syncLiveCard(); updateLivePill();
}
let toolsMissing = [];
async function runSetup(want) {
  if (!want.length || setupJob) return;
  try {
    setupJob = (await api('/api/setup_tools', { want })).job;
    toast('Downloading tools…', '', 2500);
    const j = await waitJob(setupJob, () => updateStatusBar());
    if (j && j.status === 'done') toast('Tools are ready.');
    else if (j && j.error) toast(j.error, 'err', 8000);
  } catch (e) { toast(e.message, 'err'); }
  setupJob = null; jobsState.setup = null;
  loadStatus(); updateStatusBar();
}
$('#getTools').onclick = () => runSetup(toolsMissing);

const jobsState = {};
async function waitJob(id, onTick) {
  while (true) {
    let j;
    try { j = await api('/api/job/' + id); } catch (e) { return null; }
    jobsState[j.kind] = j;
    if (onTick) onTick(j);
    if (j.status !== 'running') { jobsState[j.kind] = null; return j; }
    await new Promise(r => setTimeout(r, 700));
  }
}

function updateStatusBar() {
  const parts = [];
  if (W && W.kind === 'live' && W.live) {
    const L = W.live;
    if (['recording', 'reconnecting', 'starting'].includes(L.status))
      parts.push(`<span class="task rec"><i></i>${L.status === 'recording' ? 'Recording ' + fmt(L.recorded) : L.status === 'starting' ? 'Connecting to stream' : 'Reconnecting'}</span>`);
    else if (L.status === 'waiting') parts.push(`<span class="task"><i></i>Waiting for ${esc(L.channel || 'the stream')} to go live</span>`);
    if (L.hands_free && LIVE_ON.includes(L.status)) parts.push('<span class="task"><i></i>Hands-free</span>');
  }
  const vj = jobsState.analyze; if (vj) parts.push(`<span class="task"><i></i>${esc(vj.step)} ${Math.round(vj.progress * 100)}%</span>`);
  const sj = jobsState.setup; if (sj) parts.push(`<span class="task"><i></i>Downloading tools ${Math.round(sj.progress * 100)}%</span>`);
  if (W && W.listening) {
    const done = W.clips.filter(c => c.listened).length;
    parts.push(`<span class="task"><i></i>Writing titles ${done}/${W.clips.length}</span>`);
  }
  const active = queueItems.filter(q => q.status === 'queued' || q.status === 'rendering');
  if (active.length) {
    const r = queueItems.find(q => q.status === 'rendering');
    parts.push(`<span class="task"><i></i>Rendering ${plural(active.length, 'clip')}${r ? ' · ' + Math.round(r.progress * 100) + '%' : ''}</span>`);
  }
  const tj = jobsState.tiktok; if (tj) parts.push(`<span class="task"><i></i>TikTok: ${esc(tj.step)}</span>`);
  $('#sbTasks').innerHTML = parts.length ? parts.join('') : '<span class="muted">Ready</span>';
  const todo = W ? W.clips.filter(c => c.state === 'todo' && !isGem(c)).length : 0;
  if (W && W.kind === 'vod' && W.deep && W.deep.state === 'running') parts.push(`<span class="task"><i></i>Deep scan ${Math.round((W.deep.progress || 0) * 100)}%</span>`);
  $('#badgeReview').hidden = !todo; $('#badgeReview').textContent = todo;
  $('#badgeExports').hidden = !active.length; $('#badgeExports').textContent = active.length;
  updateLivePill();
}
const LIVE_ON = ['recording', 'reconnecting', 'starting', 'waiting'];
function updateLivePill() {
  const mine = W && W.kind === 'live' && W.live && LIVE_ON.includes(W.live.status);
  $('#livePill').hidden = !(mine || status.live);
  $('#livePillText').textContent = mine ? (W.live.status === 'waiting' ? 'Waiting for stream' : `Live ${fmt(W.live.recorded)}`) : 'Live clipping';
}
$('#livePill').onclick = async () => {
  if (status.live && (!W || W.kind !== 'live' || W.sid !== status.live)) {
    try { openLive(await api('/api/live/' + status.live), false); } catch (e) { toast(e.message, 'err'); }
  } else setView('review');
};
$('#quitBtn').onclick = async () => {
  if (status.running && !await ask({ title: 'Quit VodClipper?', body: 'Something is still running (a download, render or live recording). It will be stopped. Renders that didn\'t finish start again next time you open VodClipper.', ok: 'Quit', danger: true })) return;
  await api('/api/quit', {}).catch(() => {});
  document.body.innerHTML = '<div style="height:100vh;display:grid;place-items:center;color:#a3a6ae;font:14px Segoe UI,system-ui">VodClipper has stopped. You can close this tab.</div>';
};
window.askQuit = async () => {
  if (await ask({ title: 'Quit VodClipper?', body: 'Something is still running (a download, render or live recording). It will be stopped. Renders that didn\'t finish start again next time you open VodClipper.', ok: 'Quit', danger: true }))
    await api('/api/quit', {}).catch(() => {});
};

/* =============================================================== home */
function bindNum(id, key, def) {
  const el = $(id); el.value = store.get(key, def);
  if (el.value === '') el.value = def;           // a value this version no longer offers
  el.addEventListener('change', () => { store.set(key, el.value); if (['#count', '#length', '#cw'].includes(id)) repickSoon(); });
}
// 3.1 picks how many clips and how long each one is by itself; earlier fixed choices start fresh
bindNum('#count', 'count2', '0'); bindNum('#length', 'length2', '0'); bindNum('#cw', 'cw', 60); bindNum('#delay', 'delay', 6);
bindNum('#liveLength', 'liveLength2', '0');
function setCount(n) {
  const sel = $('#count');
  if (![...sel.options].some(o => o.value === String(n))) sel.add(new Option(String(n), String(n)));
  sel.value = String(n); store.set('count2', sel.value);
}
['deep', 'events'].forEach(id => { $('#' + id).checked = store.get(id, id === 'events'); $('#' + id).onchange = e => store.set(id, e.target.checked); });
$('#deepModel').value = store.get('deepModel', 'auto'); $('#deepModel').onchange = e => store.set('deepModel', e.target.value);
let twStatus = {};
async function loadTwitch(refresh) {
  try { twStatus = await api('/api/twitch' + (refresh ? '?refresh=1' : '')); } catch { return; }
  renderTwitch(); syncLiveCard();
}
function adNote() {
  const t = twStatus;
  if (t.signed_in && t.turbo) return `Signed in to Twitch as <b>${esc(t.login)}</b> with Turbo: recordings come without ads.`;
  if (t.signed_in) return `Signed in to Twitch as <b>${esc(t.login)}</b>. Without Turbo or a sub, ad breaks are left out of the recording.`;
  return 'Ad breaks are left out of recordings. <a href="#" id="liveAdSignIn">Sign in to Twitch</a> with Turbo for an ad-free stream.';
}
$('#cw').addEventListener('input', () => { store.set('cw', $('#cw').value); repickSoon(); });
const liveCfg = { mode: store.get('liveMode', 'hands'), freq: store.get('liveFreq', 'normal') };
const FREQ = { fewer: 'Fewer clips', normal: 'Normal', more: 'More clips', lots: 'Lots of clips' };
function syncLiveCard() {
  $$('#liveMode button').forEach(b => b.classList.toggle('on', b.dataset.v === liveCfg.mode));
  $$('#liveFreq button').forEach(b => b.classList.toggle('on', b.dataset.v === liveCfg.freq));
  $('#liveModeHint').textContent = liveCfg.mode === 'hands'
    ? 'Every moment is titled, checked and rendered with your style on its own. Finished clips appear in Exports.'
    : 'Moments wait in Review. You keep the ones you want.';
  const hk = status.hotkey || {};
  $('#liveHotkeyNote').textContent = (hk.registered ? `Press ${hk.keys} anywhere to clip the last 30 seconds. ` : '')
    + 'Only the last 15 minutes of the stream are kept on disk.';
  $('#liveAdNote').innerHTML = adNote();
  const si = $('#liveAdSignIn'); if (si) si.onclick = e => { e.preventDefault(); setView('settings'); $('#setTwitch').scrollIntoView({ block: 'center' }); };
  const ttOn = !!(ttStatus && ttStatus.connected);
  $('#liveDraft').disabled = !ttOn || liveCfg.mode !== 'hands';
  if (!ttOn) $('#liveDraft').checked = false;
  $('#liveDraftHint').textContent = !ttOn ? 'Connect TikTok in Settings first. You finish each post in the TikTok app.'
    : liveCfg.mode !== 'hands' ? 'Available in hands-free mode.' : 'You finish each post in the TikTok app. TikTok allows a few pending drafts a day.';
}
$('#liveMode').onclick = e => { const v = e.target.closest('button')?.dataset.v; if (v) { liveCfg.mode = v; store.set('liveMode', v); syncLiveCard(); } };
$('#liveFreq').onclick = e => { const v = e.target.closest('button')?.dataset.v; if (v) { liveCfg.freq = v; store.set('liveFreq', v); syncLiveCard(); } };
['liveWait', 'liveKeep', 'liveDraft'].forEach(id => {
  $('#' + id).checked = store.get(id, id === 'liveWait');
  $('#' + id).onchange = e => store.set(id, e.target.checked);
});
api('/api/tiktok').then(t => { ttStatus = t; syncLiveCard(); }).catch(() => syncLiveCard());
$('#src').value = store.get('src', ''); $('#liveSrc').value = store.get('liveSrc', '');
$('#maxh').value = store.get('maxh', '1080'); $('#maxh').onchange = e => store.set('maxh', e.target.value);
$('#cookies').value = store.get('cookies', ''); $('#cookies').onchange = e => store.set('cookies', e.target.value);
async function pick(kind) { try { return (await api('/api/pick_file', { kind })).path || ''; } catch { return ''; } }
$('#browseSrc').onclick = async () => { const p = await pick('video'); if (p) { $('#src').value = p; $('#analyze').focus(); } };
$('#browseChat').onclick = async () => { const p = await pick('chat'); if (p) $('#chatFile').value = p; };
$('#src').addEventListener('keydown', e => { if (e.key === 'Enter') $('#analyze').click(); });
$('#liveSrc').addEventListener('keydown', e => { if (e.key === 'Enter') $('#goLive').click(); });

$('#analyze').onclick = async () => {
  const source = $('#src').value.trim().replace(/^"(.*)"$/, '$1');
  if (!source) { $('#src').focus(); return; }
  if (status.ffmpeg === false) { toast('Download the missing tools first.', 'err'); return; }
  store.set('src', source);
  $('#analyze').disabled = true; $('#vodJob').hidden = false; $('#vodErr').textContent = ''; $('#vodLog').textContent = '';
  try {
    const { job } = await api('/api/analyze', { source, chat_file: $('#chatFile').value, count: +$('#count').value,
      length: +$('#length').value, chat_weight: +$('#cw').value, chat_delay: +$('#delay').value,
      max_height: +$('#maxh').value, cookies: $('#cookies').value, events: $('#events').checked,
      deep_scan: $('#deep').checked && !$('#deep').disabled, deep_model: $('#deepModel').value, language: opts.language });
    vodJob = job;
    $('#vodCancel').hidden = false;
    $('#vodCancel').onclick = () => api('/api/cancel', { job }).catch(() => {});
    const j = await waitJob(job, j => {
      $('#vodStep').textContent = j.step; $('#vodPct').textContent = Math.round(j.progress * 100) + '%';
      $('#vodBar').style.width = Math.round(j.progress * 100) + '%';
      $('#vodLog').textContent = j.log.slice(-4).join('\n'); updateStatusBar();
    });
    $('#vodCancel').hidden = true;
    if (j && j.status === 'done') { $('#vodJob').hidden = true; openVod(j.result); loadStorage(); }
    else if (j && j.status === 'error') $('#vodErr').textContent = j.error;
    else if (j && j.status === 'cancelled') $('#vodJob').hidden = true;
  } catch (e) { $('#vodErr').textContent = e.message; }
  vodJob = null; $('#analyze').disabled = false; updateStatusBar();
};

$('#goLive').onclick = async () => {
  const source = $('#liveSrc').value.trim();
  if (!source) { $('#liveSrc').focus(); return; }
  if (status.ffmpeg === false) { toast('Download the missing tools first.', 'err'); return; }
  store.set('liveSrc', source);
  $('#liveErr').textContent = ''; $('#goLive').disabled = true;
  try {
    const v = await api('/api/live/start', { source, length: +$('#liveLength').value, frequency: liveCfg.freq, events: $('#events').checked,
      hands_free: liveCfg.mode === 'hands', wait_for_live: $('#liveWait').checked, keep_watching: $('#liveKeep').checked,
      auto_draft: liveCfg.mode === 'hands' && $('#liveDraft').checked,
      opts: JSON.parse(JSON.stringify(opts)), model: opts.whisper_model,
      language: opts.language, max_height: +$('#maxh').value, cookies: $('#cookies').value, chat_delay: +$('#delay').value });
    openLive(v, true);
  } catch (e) { $('#liveErr').textContent = e.message; }
  $('#goLive').disabled = false;
};

async function loadRecent() {
  let list = [];
  try { list = await api('/api/recent'); } catch {}
  $('#recentWrap').hidden = !list.length;
  const rv = r => r.review && (r.review.kept || r.review.skipped) ? ` <span class="muted">· ${r.review.kept} kept, ${r.review.todo} to review</span>` : '';
  $('#recentTable tbody').innerHTML = list.map(r => `<tr data-src="${esc(r.source)}"><td title="${esc(r.title)}">${esc(r.title)}${rv(r)}</td>
    <td title="${esc(r.source)}">${esc(r.source)}</td><td class="r tnum">${r.duration ? fmt(r.duration) : ''}</td><td class="r">${r.updated ? ago(r.updated) : ''}</td></tr>`).join('');
}
$('#recentTable').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-src]'); if (!tr) return;
  $('#src').value = tr.dataset.src; $('#analyze').click();
});

/* =============================================================== workspace */
const keyOf = c => `${c.pid}:${c.id}`;
const byKey = k => W && W.clips.find(c => keyOf(c) === k);
const curClip = () => byKey(cur);
const rankOf = c => W.clips.indexOf(c) + 1;
const absOf = (c, t) => t + (c.absOff || 0);
const isGem = c => c.kind === 'gem';
const visible = () => !W ? [] : filter === 'flagged' ? flaggedList()
  : W.clips.filter(c => filter === 'gems' ? isGem(c) : !isGem(c) && (filter === 'all' || c.state === filter));
const DEFAULT_TAGS = '#twitch #twitchclips #streamer #fyp';
function postFor(c) {
  if (c.userPost) return c.postText;
  const tags = (c.post || '').split('\n\n')[1] || DEFAULT_TAGS;
  return `${(c.title || '').trim() || 'Wait for it...'} ${c.emoji || ''}`.trim() + `\n\n${tags}`;
}
function prepClip(c) {
  c.state = c.state || 'todo';
  if (c.wordsEdited && c.words_edit) { c.wordsOrig = c.words || []; c.words = c.words_edit; delete c.words_edit; }
  c.kind = c.kind || 'hype';
  c.title = c.title || ''; c.titles = c.titles || []; c.words = c.words || [];
  if (c.absOff == null) c.absOff = c.abs_start != null ? c.abs_start - c.start : 0;
  return c;
}
function mergeClip(c, s) {
  c.listened = s.listened; if (!c.wordsEdited) c.words = s.words || []; c.titles = s.titles || []; c.vibe = s.vibe; c.emoji = s.emoji;
  if (s.orig && !c.userTrim) c.orig = s.orig;
  c.reason = s.reason; c.post = s.post; c.held = s.held || ''; if (s.safety) c.safety = s.safety; if (s.check) c.check = s.check;
  if (!c.userTitle && s.title) c.title = s.title;
  // once a clip has been listened to, its cuts move to sentence boundaries (unless you trimmed it yourself)
  if (!c.userTrim && c.state !== 'kept' && s.start != null && (s.start !== c.start || s.end !== c.end)) {
    c.start = s.start; c.end = s.end;
    if (s.abs_start != null) c.absOff = s.abs_start - s.start;
    return true;
  }
  return false;
}

function openVod(p) {
  W = { kind: 'vod', pid: p.pid, title: p.title, duration: p.duration, curve: p.curve, meta: p,
        clips: p.clips.map(prepClip).concat((p.gems || []).map(prepClip)), listening: false, deep: p.deep };
  if (p.restored && p.settings) {
    // opened before: the sliders go back to where you left them for this VOD
    setCount(p.settings.count || 0);
    $('#length').value = String(p.settings.length || 0); if ($('#length').value === '') $('#length').value = '0';
    store.set('length2', $('#length').value);
    $('#cw').value = p.settings.chat_weight; store.set('cw', $('#cw').value);
  }
  startWorkspace();
  listen();
  if (p.restored) {
    const r = p.review || {};
    toast(`Picked up where you left off: ${plural(r.kept || 0, 'clip')} kept, ${r.skipped || 0} skipped, ${r.todo || 0} to review.`, '', 5000);
  }
}
function openLive(v, fresh) {
  W = { kind: 'live', sid: v.id, title: v.title, duration: Math.max(1, v.recorded), curve: v.curve, live: v,
        clips: v.clips.map(c => Object.assign(prepClip(c), { isNew: false })), listening: v.listening };
  startWorkspace();
  if (fresh) toast(v.status === 'waiting' ? `Waiting for ${v.channel || 'the stream'} to go live. Recording starts by itself.`
    : v.hands_free ? 'Hands-free clipping started. Clips render by themselves and land in Exports.'
    : 'Live clipping started. Moments will appear here as they happen.', '', 6000);
}
function startWorkspace() {
  hist.undo = []; hist.redo = []; syncUndo(); hideExact(); ed.key = null; ed.data = null;
  cur = null; mode = 'clip'; filter = W.kind === 'live' && W.live.hands_free ? 'all' : 'todo'; syncFilterTabs();
  vod.removeAttribute('src'); vod.dataset.src = '';
  buildHead();
  setView('review');
  const first = W.clips.find(c => c.state === 'todo' && !isGem(c));
  if (first) select(keyOf(first), true); else { showInspector(); if (W.kind === 'vod') loadFrame(W.pid, W.duration / 2); }
  renderRows(); drawHeat(); updateCrumb(); refreshQueue();
}

/* --- review header (VOD facts, or live controls) --- */
function buildHead() {
  const h = $('#rvHead');
  if (W.kind === 'vod') {
    const p = W.meta, cs = p.chat_stats || {}, est = p.estimate || {};
    const hours = p.duration / 3600, len = hours >= 1 ? `${Math.round(hours * 10) / 10}-hour` : `${Math.max(1, Math.round(p.duration / 60))}-minute`;
    const chatFact = !p.has_chat ? 'No chat, ranked by audio'
      : cs.chatters ? `<b class="tnum">${cs.chatters.toLocaleString()}</b> chatters` : `<b class="tnum">${p.chat_count.toLocaleString()}</b> chat messages`;
    const chatTip = cs.ignored ? `${cs.ignored.toLocaleString()} bot, command and sub messages ignored` + (cs.raids ? `, ${plural(cs.raids, 'raid')} played down` : '') : '';
    const lr = p.learning || {};
    h.innerHTML = `<span class="t" title="${esc(W.title)}">${esc(W.title)}</span>
      <span class="facts"><span><b class="tnum">${fmt(p.duration)}</b> long</span>
      <span title="${esc(chatTip)}">${chatFact}</span>
      ${est.strong != null ? `<span class="est" title="Distinct moments that stand out clearly (${est.good} worth a look in total)">≈<b class="tnum">${est.strong}</b> strong ${est.strong === 1 ? 'moment' : 'moments'} in this ${len} stream</span>` : ''}
      ${lr.active ? `<span class="tuned" title="Ranking learned from ${lr.decisions} of your keeps and skips${lr.using === 'all your picks' ? ' across all streamers' : ''}">Tuned to your picks</span>` : ''}</span>
      <span class="acts"><span class="deep" id="deepBox"></span><button class="btn sm ghost" id="headNew">${icon('plus')}New project</button></span>`;
    $('#headNew').onclick = () => setView('home');
    renderDeep();
  } else {
    h.innerHTML = `<span class="live-tag" id="lvTag"><i></i><span id="lvState">Live</span></span>
      <span class="t" title="${esc(W.title)}">${esc(W.live.channel || W.title)}</span>
      <span class="ad-tag" id="lvAd" hidden><i></i>Ad break: left out</span>
      <span class="facts"><span>Recorded <b class="tnum" id="lvRec">0:00</b></span><span>Chat <b class="tnum" id="lvChat">0</b>/min</span><span><b class="tnum" id="lvMoments">0</b> <span id="lvMomentsWord">moments</span></span><span id="lvAdsWrap" hidden title="Ads Twitch put in the stream, kept out of the recording">Ads skipped <b class="tnum" id="lvAds">0:00</b></span></span>
      <span class="acts">
        <select class="input" id="lvSens" title="How many clips to make"><option value="fewer">Fewer clips</option><option value="normal">Normal</option><option value="more">More clips</option><option value="lots">Lots of clips</option></select>
        <label class="switch-label" title="Title, check and render every moment by itself">Hands-free<span class="switch"><input type="checkbox" id="lvAuto"><i></i></span></label>
        <button class="btn" id="lvClip" title="Clip the last 30 seconds (C)">${icon('scissors')}Clip last 30s</button>
        <button class="btn" id="lvStop">${icon('stop')}Stop</button>
      </span>`;
    $('#lvSens').value = W.live.frequency || 'normal';
    $('#lvAuto').checked = !!W.live.hands_free;
    $('#lvSens').onchange = e => liveSettings({ frequency: e.target.value });
    $('#lvAuto').onchange = e => {
      liveSettings({ hands_free: e.target.checked, opts: JSON.parse(JSON.stringify(opts)) });
      toast(e.target.checked ? 'Hands-free is on: new moments render by themselves.' : 'Hands-free is off: new moments wait for you in Review.', '', 3500);
    };
    $('#lvClip').onclick = clipNow;
    $('#lvStop').onclick = stopLive;
    updateLiveHead();
  }
}
function updateLiveHead() {
  if (!W || W.kind !== 'live' || !$('#lvState')) return;
  const L = W.live;
  const labels = { starting: 'Connecting', waiting: 'Waiting for stream', recording: 'Live', reconnecting: 'Reconnecting', ended: 'Stream ended', stopped: 'Stopped', error: 'Error' };
  $('#lvState').textContent = labels[L.status] || L.status;
  $('#lvTag').classList.toggle('off', !['recording', 'reconnecting'].includes(L.status));
  $('#lvRec').textContent = fmt(L.recorded);
  $('#lvChat').textContent = L.chat_connected ? L.chat_per_min : '–';
  $('#lvMoments').textContent = W.clips.length;
  $('#lvMomentsWord').textContent = W.clips.length === 1 ? 'moment' : 'moments';
  $('#lvAd').hidden = !(L.in_ad && L.status === 'recording');
  $('#lvAdsWrap').hidden = !L.ads_skipped;
  $('#lvAds').textContent = fmt(L.ads_skipped || 0);
  const active = LIVE_ON.includes(L.status);
  $('#lvClip').disabled = L.status !== 'recording';
  $('#lvStop').disabled = !active;
  $('#lvStop').hidden = !active;
  if (L.status === 'error' && L.error && !W.errShown) { W.errShown = true; toast(L.error, 'err', 9000); }
}
async function liveSettings(body) {
  try { W.live = Object.assign(W.live, await api(`/api/live/${W.sid}/settings`, body)); } catch (e) { toast(e.message, 'err'); }
}
async function clipNow() {
  if (!W || W.kind !== 'live') return;
  try { await api(`/api/live/${W.sid}/clip`, { seconds: 30 }); toast('Clipping the last 30 seconds. It appears in the list in a few seconds.', '', 3500); }
  catch (e) { toast(e.message, 'err'); }
}
async function stopLive() {
  if (!await ask({ title: 'Stop live clipping?', body: 'Recording stops. Moments already found stay here so you can keep reviewing them.', ok: 'Stop', danger: true })) return;
  try { await api(`/api/live/${W.sid}/stop`, {}); toast('Stopping…', '', 2000); } catch (e) { toast(e.message, 'err'); }
}
async function pollLive() {
  if (!W || W.kind !== 'live') return;
  let v;
  try { v = await api('/api/live/' + W.sid); } catch { return; }
  W.live = v; W.curve = v.curve; W.duration = Math.max(1, v.recorded); W.listening = v.listening;
  let added = 0, changed = false;
  for (const s of v.clips) {
    const c = byKey(keyOf(s));
    if (c) { const before = c.title + c.listened; if (mergeClip(c, s) || before !== c.title + c.listened) changed = true; }
    else { W.clips.push(Object.assign(prepClip(s), { isNew: true })); added++; }
  }
  if (added) {
    const last = W.clips[W.clips.length - 1];
    if (view !== 'review' || cur) toast(`New moment: ${last.title || 'clip ' + W.clips.length}`, '', 3500);
    if (view === 'review' && !cur && mode === 'clip') select(keyOf(last), true);
  }
  if (added || changed) { renderRows(); if (mode === 'clip') fillInspector(); dirty = true; }
  updateLiveHead(); drawHeat(); updateStatusBar();
}
setInterval(pollLive, 1500);

/* --- titles for VOD clips arrive in the background --- */
async function listen(order) {
  if (!W || W.kind !== 'vod') return;
  const ids = (order || [curClip(), ...visible()].filter(Boolean).map(c => c.id));
  try { await api('/api/listen', { pid: W.pid, order: ids, model: opts.whisper_model, language: opts.language }); W.listening = true; } catch {}
  updateListen();
}
async function pollClips() {
  if (!W || W.kind !== 'vod' || !(W.listening || (W.deep && W.deep.state === 'running'))) return;
  let r;
  try { r = await api('/api/clips/' + W.pid); } catch { return; }
  W.listening = r.listening;
  let changed = false;
  const wasRunning = W.deep && W.deep.state === 'running';
  W.deep = r.deep;
  for (const s of r.clips.concat(r.gems || [])) {
    const c = byKey(keyOf(s));
    if (!c) { if (s.kind === 'gem') { W.clips.push(Object.assign(prepClip(s), { isNew: true })); changed = true; } continue; }
    const before = c.title + c.listened; if (mergeClip(c, s) || before !== c.title + c.listened) changed = true;
  }
  updateListen(); renderDeep();
  if (wasRunning && W.deep.state === 'done') {
    const n = W.clips.filter(isGem).length;
    toast(n ? `Deep scan found ${plural(n, 'quiet gem')}. They're in the Gems tab.` : 'Deep scan finished. No quiet gems stood out.', '', 7000);
  }
  if (changed) { renderRows(); if (mode === 'clip') fillInspector(); dirty = true; drawHeat(); }
}
function renderDeep() {
  const box = $('#deepBox'); if (!box || !W || W.kind !== 'vod') return;
  const d = W.deep || {}, n = W.clips.filter(isGem).length;
  if (d.state === 'running') {
    box.innerHTML = `<span>${esc(d.step || 'Deep scan')}</span><span class="meter"><i style="width:${Math.round((d.progress || 0) * 100)}%"></i></span><button class="link" id="deepStop">Stop</button>`;
    $('#deepStop').onclick = () => api('/api/deepscan', { pid: W.pid, cancel: true }).then(v => { W.deep = v; renderDeep(); }).catch(e => toast(e.message, 'err'));
  } else if (d.state === 'done') {
    box.innerHTML = `<span>${n ? plural(n, 'quiet gem') : 'No quiet gems'}</span>`;
  } else if (d.can) {
    box.innerHTML = `<button class="btn sm" id="deepGo" title="Transcribe the whole stream to find quiet funny lines chat and volume missed">${d.state === 'error' ? 'Retry deep scan' : 'Deep scan'}</button>`;
    $('#deepGo').onclick = startDeep;
    if (d.state === 'error' && d.error) box.title = d.error;
  } else box.innerHTML = '';
}
async function startDeep() {
  if (!W || W.kind !== 'vod') return;
  try { W.deep = await api('/api/deepscan', { pid: W.pid, model: $('#deepModel').value, language: opts.language }); renderDeep(); updateStatusBar(); toast('Deep scan started. Quiet gems show up in the Gems tab as it goes.', '', 5000); }
  catch (e) { toast(e.message, 'err', 7000); }
}
setInterval(pollClips, 1200);
function updateListen() {
  const busy = W && W.listening && W.clips.some(c => !c.listened);
  $('#listenFoot').hidden = !busy;
  if (busy) $('#listenText').textContent = status.whisper
    ? `Listening and writing titles (${W.clips.filter(c => c.listened).length} of ${W.clips.length})` : 'Writing titles from chat';
  updateStatusBar();
}

/* --- clip list --- */
function syncFilterTabs() { $$('#inboxTabs button').forEach(b => b.classList.toggle('on', b.dataset.f === filter)); }
$('#inboxTabs').onclick = e => { const b = e.target.closest('button'); if (!b) return; filter = b.dataset.f; syncFilterTabs(); renderRows(); };
function renderRows() {
  if (!W) return;
  const hype = W.clips.filter(c => !isGem(c)), gemsN = W.clips.length - hype.length;
  const n = s => hype.filter(c => c.state === s).length;
  $('#nTodo').textContent = n('todo'); $('#nSkip').textContent = n('skipped'); $('#nAll').textContent = hype.length;
  $('#gemsTab').hidden = !(gemsN || (W.deep && W.deep.state === 'running')); $('#nGems').textContent = gemsN || '';
  if (filter === 'gems' && $('#gemsTab').hidden) { filter = 'todo'; syncFilterTabs(); }
  const flaggedN = W.clips.filter(isFlagged).length, openN = W.clips.filter(c => checkStatus(c) === 'needs_review').length;
  $('#flagTab').hidden = !flaggedN && filter !== 'flagged'; $('#nFlag').textContent = openN || (flaggedN ? '' : '');
  $('#flagBar').hidden = filter !== 'flagged';
  const list = visible();
  if (!list.length) {
    $('#cards')._html = '';
    $('#cards').innerHTML = `<div class="empty-note">${
 !W.clips.length ? (W.kind === 'live' ? (W.live && W.live.status === 'waiting' ? `Waiting for ${esc(W.live.channel || 'the stream')} to go live. Recording starts by itself; you can leave this running.` : W.live && W.live.warming_up ? 'Listening to the stream to learn what normal sounds like. Moments start showing up after a minute or two.' : 'No moments yet. They appear here as chat and the stream react. Use Clip last 30s to grab one yourself.')
                                           : 'Nothing stood out. Click anywhere on the timeline below to preview a moment and make a clip there.')
      : filter === 'todo' ? 'Nothing left to review.' : filter === 'skipped' ? 'No skipped clips.'
      : filter === 'flagged' ? (flagFilter === 'all' ? 'Nothing flagged.' : 'No flagged clips with this status.')
      : filter === 'gems' ? (W.deep && W.deep.state === 'running' ? 'Deep scan is listening to the whole stream. Quiet gems show up here as it finds them.' : 'No quiet gems.') : ''}</div>`;
  } else {
    patchList($('#cards'), list.map(c => {
      const q = queueFor(c);
      const st = c.state === 'kept' ? (q && q.status === 'error' ? `<span class="st err">${icon('alert')}Render failed</span>`
          : `<span class="st kept">${icon('check')}${q ? (q.status === 'done' ? 'Rendered' : q.status === 'rendering' ? `Rendering ${Math.round(q.progress * 100)}%` : 'In render queue') : 'Kept'}</span>`)
        : c.state === 'skipped' ? `<span class="st">Skipped</span>`
        : checkStatus(c) === 'excluded' ? `<span class="st excl">${icon('alert')}Excluded from export</span>`
        : c.held ? `<span class="st held">${icon('alert')}Held for review</span>` : '';
      const lvl = c.safety && c.safety.level, cst = checkStatus(c);
      const showRisk = cst === 'needs_review' && (safetyCfg.notify !== 'later' || filter === 'flagged');
      const risk = showRisk ? `<span class="risk ${lvl === 'high' || lvl === 'medium' ? lvl : 'low'}" title="Content check: ${esc(c.safety.flags.filter(f => f.status === 'open').map(f => f.label).join(', '))}">${icon('alert')}</span>` : '';
      const ck = ['reviewed', 'corrected', 'dismissed'].includes(cst) ? `<span class="ck ${cst}">${CHECK_LABEL[cst]}</span>` : '';
      const title = c.title ? esc(c.title) : 'Untitled';
      return `<button class="crow ${keyOf(c) === cur && mode === 'clip' ? 'cur' : ''}" data-k="${esc(keyOf(c))}">
        <span class="vt"><img src="${c.thumb}" alt=""></span>
        <span><span class="ct ${c.listened || c.userTitle ? '' : 'pending'}">${risk}${title}</span>
        <span class="cm">${W.kind === 'vod' ? `<span class="num">#${c.id}</span>` : ''}<span>${W.kind === 'live' ? fmt(absOf(c, c.start)) : fmt(c.start)}</span><span>${Math.round(c.end - c.start)}s</span>${c.vibe ? `<span>${VIBE[c.vibe] || ''}</span>` : ''}${ck}</span>${st}</span>
        ${c.isNew ? '<span class="new" title="New"></span>' : ''}</button>`;
    }).join(''), 'k');
  }
  updateStatusBar();
}
$('#cards').onclick = e => { const b = e.target.closest('.crow'); if (b) select(b.dataset.k, true); };
function scrollRowIntoView(k) {
  const box = $('#cards'), el = [...box.children].find(x => x.dataset.k === k); if (!el) return;
  const top = el.offsetTop - box.offsetTop, bottom = top + el.offsetHeight;
  if (top < box.scrollTop) box.scrollTop = top - 4;
  else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight + 4;
}

/* --- selecting --- */
function select(k, play = true) {
  const c = byKey(k); if (!c) return;
  if (cur !== k) { stopPreview(); hideExact(); const old = curClip(); if (old) old._prop = null; }
  cur = k; mode = 'clip'; c.isNew = false;
  loadSource(c.vod, c.start, play);
  loadFrame(c.pid, c.start + (c.end - c.start) * 0.6);
  showInspector(); fillInspector(); renderRows(); drawHeat(); dirty = true;
  scrollRowIntoView(k);
}
function step(dir) {
  if (!W) return;
  const list = visible();
  if (mode !== 'clip' || !cur) { if (list[0]) select(keyOf(list[0])); return; }
  const i = list.findIndex(c => keyOf(c) === cur);
  if (i < 0) { if (list[0]) select(keyOf(list[0])); return; }
  const n = list[i + dir]; if (n) select(keyOf(n));
}
function advance(fromKey) {
  const i = W.clips.findIndex(c => keyOf(c) === fromKey);
  const gem = W.clips[i] && isGem(W.clips[i]);
  const ok = c => c.state === 'todo' && isGem(c) === gem;
  const next = W.clips.slice(i + 1).find(ok) || W.clips.find(ok);
  if (next) select(keyOf(next));
  else { cur = null; vod.pause(); showInspector(); renderRows(); dirty = true; }
}
function showInspector() {
  const c = curClip();
  $('#clipForm').hidden = $('#clipActions').hidden = !(mode === 'clip' && c);
  $('#editor').hidden = !(mode === 'clip' && c); edDirty = true;
  $('#freePane').hidden = mode !== 'free';
  $('#donePane').hidden = !(mode === 'clip' && !c);
  if (mode === 'clip' && !c && W) {
    const kept = W.clips.filter(x => x.state === 'kept').length, skipped = W.clips.filter(x => x.state === 'skipped').length;
    $('#doneText').textContent = !W.clips.length
      ? (W.kind === 'live' ? 'Moments appear here as they happen.' : 'Click the timeline to preview any moment and make a clip from it.')
      : `${plural(kept, 'clip')} kept, ${skipped} skipped.` + (W.kind === 'live' && W.live && W.live.status === 'recording' ? ' New moments will appear as the stream goes on.' : '');
    $('#reviewSkipped').hidden = !skipped;
    $('#findMore').hidden = W.kind !== 'vod';
    $('#goExports').hidden = !kept;
  }
  $('#phoneEmpty').hidden = !(mode === 'clip' && !c);
  $('#phoneEmpty').textContent = W && !W.clips.length && W.kind === 'live' ? 'Waiting for the first moment…' : 'No clip selected';
}
function fillInspector() {
  const c = curClip(); if (!c || mode !== 'clip') return;
  if (!$('#pane-captions').hidden && !$('#capLines').contains(document.activeElement)) renderCapEditor();
  const gemList = W.clips.filter(isGem), hypeList = W.clips.filter(x => !isGem(x));
  $('#cRank').textContent = W.kind === 'live' ? `Moment ${c.moment || rankOf(c)}${c.manual ? ' (clipped by you)' : ''}`
    : isGem(c) ? `Quiet gem #${c.id} · ${gemList.indexOf(c) + 1} of ${gemList.length}` : `Clip #${c.id} · ${hypeList.indexOf(c) + 1} of ${hypeList.length}`;
  $('#cTime').textContent = `${fmtA(absOf(c, c.start))} to ${fmtA(absOf(c, c.end))} (${(c.end - c.start).toFixed(1)}s)`;
  edDirty = true;
  $('#cWhy').textContent = c.reason || '';
  $('#cVibe').textContent = c.vibe ? VIBE[c.vibe] || c.vibe : c.listened ? 'Neutral' : 'Listening…';
  if (document.activeElement !== $('#titleInput')) $('#titleInput').value = c.title;
  if (document.activeElement !== $('#postInput')) $('#postInput').value = postFor(c);
  $('#nextTitle').disabled = (c.titles || []).length < 2;
  syncTrimInfo(c);
  const kept = c.state === 'kept', q = queueFor(c), failed = kept && q && q.status === 'error';
  $('#keepBtn').textContent = failed ? 'Retry render' : kept ? (q && q.status === 'done' ? 'Rendered' : 'In render queue') : 'Keep and render';
  $('#keepBtn').disabled = kept && !failed;
  renderCheck(c);
  const note = $('#cNotice');
  if (failed) {
    note.className = 'notice err'; note.hidden = false;
    note.innerHTML = `<b>Render failed.</b> ${esc(q.error)}`;
  } else if (c.held && c.state !== 'kept') {
    note.className = 'notice warn'; note.hidden = false;
    note.innerHTML = `<b>${esc(c.held)}.</b> Hands-free didn't render this one. Watch it, fix the captions if needed, then keep or skip it.`;
  } else note.hidden = true;
  $('#skipBtn').textContent = kept ? 'Remove from queue' : c.state === 'skipped' ? 'Move back to review' : 'Skip';
  $('#skipBtn').disabled = kept && q && q.status === 'done';
  $$('#clipForm [data-t]').forEach(b => b.disabled = kept);
  $('#titleInput').disabled = $('#postInput').disabled = kept;
}
$('#titleInput').addEventListener('input', e => {
  const c = curClip(); if (!c) return;
  record(c, 'title change', () => { c.title = e.target.value; c.userTitle = true; }, 'title');
  if (!c.userPost) $('#postInput').value = postFor(c);
  dirty = true; renderRowsSoon(); rescanSoon(c);
});
$('#postInput').addEventListener('input', e => { const c = curClip(); if (!c) return; record(c, 'post caption change', () => { c.userPost = true; c.postText = e.target.value; }, 'post'); rescanSoon(c); });
function nextTitle() {
  const c = curClip(); if (!c || !(c.titles || []).length || c.state === 'kept') return;
  const i = c.titles.findIndex(t => t === c.title);
  record(c, 'new title', () => { c.title = c.titles[(i + 1) % c.titles.length]; c.userTitle = true; });
  fillInspector(); renderRows(); dirty = true; saveClip(c, true);
}
$('#nextTitle').onclick = nextTitle;
let rrTimer = 0; function renderRowsSoon() { clearTimeout(rrTimer); rrTimer = setTimeout(renderRows, 250); }
$('#copyPost').onclick = () => { const c = curClip(); if (c) copyText($('#postInput').value || postFor(c)); };

$('#clipForm').addEventListener('click', e => {
  const t = e.target.closest('[data-t]')?.dataset.t; if (!t) return;
  const c = curClip(); if (!c || c.state === 'kept') return;
  const [which, d] = t.split(','), now = vod.currentTime, f = 1 / fpsOf(c);
  const by = d === 'f' ? f : d === '-f' ? -f : +d;
  const frames = d === 'f' || d === '-f';
  const ok = which === 's' ? setBounds(c, d === 'now' ? now : c.start + by, c.end, d === 'now' ? 'set start' : 'move start', d === 'now' ? null : 'trim-s')
    : setBounds(c, c.start, d === 'now' ? now : c.end + by, d === 'now' ? 'set end' : 'move end', d === 'now' ? null : 'trim-e');
  if (!ok) return;
  if (frames) { vod.pause(); try { vod.currentTime = which === 'e' ? Math.max(c.start, c.end - f) : c.start; } catch {} }
  else { try { vod.currentTime = which === 'e' ? Math.max(c.start, c.end - 3) : c.start; } catch {} vod.play().catch(() => {}); }
});

/* --- keep / skip --- */
const queueFor = c => queueItems.find(q => q.pid === c.pid && q.clip_id === c.id);
/* --- content check review (3.3): statuses, flags and what you decide about them --- */
const CHECK_LABEL = { off: 'Content check is off', unchecked: 'Not checked', clean: 'Nothing flagged', needs_review: 'Needs review',
  reviewed: 'Reviewed', corrected: 'Corrected', dismissed: 'Dismissed', excluded: 'Excluded from export' };
const FIX_LABEL = { mute: 'Mute it', bleep: 'Bleep it', cut_end: 'End the clip before it', cut_start: 'Start the clip after it',
  mask_title: 'Hide it in the title', mask_post: 'Hide it in the post caption' };
const FIX_EXPLAIN = {
  mute: 'The sound goes silent for these words when the clip renders. Captions show them masked.',
  bleep: 'A bleep plays over these words when the clip renders. Captions show them masked.',
  cut_end: 'The clip ends just before these words. Your video itself isn\'t changed.',
  cut_start: 'The clip starts just after these words. Your video itself isn\'t changed.',
  mask_title: 'The flagged words in the title are replaced with stars.',
  mask_post: 'The flagged words in the post caption are replaced with stars.' };
const FLAG_STATE = { open: '', accepted: 'Left as is', dismissed: 'Dismissed' };
const STATUS_ORDER = { needs_review: 0, excluded: 1, reviewed: 2, corrected: 3, dismissed: 4 };
let flagFilter = 'all';
const checkOf = c => c.check || {};
function checkStatus(c) {
  if (checkOf(c).excluded) return 'excluded';
  if (!c.safety) return safetyCfg.enabled === false ? 'off' : 'unchecked';
  if (c.safety.enabled === false) return 'off';
  return c.safety.status || ((c.safety.flags || []).length ? 'needs_review' : 'clean');
}
const isFlagged = c => !!(checkOf(c).excluded || (checkOf(c).fixes || []).length || (c.safety && (c.safety.flags || []).length));
function flaggedList() {
  const lvl = c => ({ high: 3, medium: 2, low: 1 })[c.safety && c.safety.level] || 0;
  return W.clips.filter(c => isFlagged(c) && (flagFilter === 'all' || checkStatus(c) === flagFilter))
    .sort((a, b) => (STATUS_ORDER[checkStatus(a)] ?? 9) - (STATUS_ORDER[checkStatus(b)] ?? 9) || lvl(b) - lvl(a));
}
function fixSpans(c) {
  const s = (checkOf(c).fixes || []).filter(x => x.span).map(x => [x.span[0], x.span[1], x.kind]);
  if (pv && pv.c === c && pv.span) s.push([pv.span[0], pv.span[1], pv.kind]);
  return s;
}

function flagRow(c, f, kept) {
  const at = f.source === 'speech'
    ? `<button class="link at" data-seek="${f.start}" title="Play from here">${fmtT(Math.max(0, f.start))}</button>`
    : `<span class="muted">${f.source === 'title' ? 'in the title' : 'in the post caption'}</span>`;
  const st = f.status === 'corrected' ? `Fixed: ${(FIX_LABEL[f.fixed] || 'fixed').toLowerCase()}` : FLAG_STATE[f.status] || '';
  let acts = '';
  if (!kept) {
    if (f.status === 'open') acts = (f.fixes || []).map(k => `<button class="btn xs" data-propose="${k}" data-fp="${esc(f.fp)}">${FIX_LABEL[k]}</button>`).join('')
      + `<button class="btn xs ghost" data-act="dismiss" data-fp="${esc(f.fp)}" title="Not a problem, or a false alarm">Dismiss</button>`;
    else if (f.status === 'corrected') acts = `<button class="btn xs ghost" data-act="undo" data-fix="${esc(f.fix_id || '')}">Undo fix</button>`;
    else acts = `<button class="btn xs ghost" data-act="reopen" data-fp="${esc(f.fp)}">Undo</button>`;
  }
  const prop = c._prop && c._prop.fp === f.fp ? proposalBox(c) : '';
  return `<li class="cf ${f.status} sev${f.severity}"><div class="cf-h"><b>${esc(f.label)}</b><span class="cf-t">“${esc(f.text)}”</span>${at}${st ? `<span class="cf-s">${esc(st)}</span>` : ''}</div>`
    + (acts ? `<div class="cf-a">${acts}</div>` : '') + prop + '</li>';
}
function proposalBox(c) {
  const p = c._prop, v = p.view || {};
  let what = '';
  if (p.kind === 'cut_end' || p.kind === 'cut_start') what = `<p class="new">New length ${fmt(v.end - v.start)} (was ${fmt(c.end - c.start)}), ${fmt(absOf(c, v.start))} to ${fmt(absOf(c, v.end))}.</p>`;
  else if (p.kind === 'mask_title') what = `<p class="new">New title: “${esc(v.title || '')}”</p>`;
  else if (p.kind === 'mask_post') what = `<p class="new">New caption starts: “${esc((v.postText || '').slice(0, 90))}”</p>`;
  const canPlay = p.kind !== 'mask_post';
  return `<div class="cf-prop"><p>${esc(FIX_EXPLAIN[p.kind])}</p>${what}`
    + `<p class="fine">You can undo it. It lowers the risk but doesn't guarantee TikTok will allow the clip.</p>`
    + `<div class="row">${canPlay ? `<button class="btn xs" data-prev="1">${p.playing ? 'Stop preview' : 'Preview'}</button>` : ''}<span class="grow"></span>`
    + `<button class="btn xs ghost" data-cancel="1">Cancel</button><button class="btn xs primary" data-apply="1">${FIX_LABEL[p.kind]}</button></div></div>`;
}
function renderCheck(c) {
  const box = $('#checkBox'), st = checkStatus(c), sf = c.safety, ck = checkOf(c), kept = c.state === 'kept';
  const lvl = sf && sf.level;
  const tone = st === 'needs_review' ? (lvl === 'high' ? 'high' : lvl === 'medium' ? 'medium' : 'low')
    : st === 'clean' ? 'ok' : st === 'excluded' ? 'medium' : st === 'corrected' ? 'ok' : 'low';
  $('#cSafe').innerHTML = st === 'unchecked' && !c.listened && safetyCfg.auto !== false ? '<span class="muted">Checking…</span>'
    : `<span class="safe ${tone}">${icon(st === 'clean' || st === 'corrected' ? 'check' : 'alert')}${CHECK_LABEL[st]}${st === 'needs_review' ? ` · ${plural(sf.open || 0, 'flag')}` : ''}${ck.stale ? ' · changed since' : ''}</span>`;
  const quiet = st === 'off' || (st === 'clean' && !ck.stale && !(ck.fixes || []).length && !(ck.log || []).length)
    || (st === 'unchecked' && safetyCfg.auto !== false && !c.listened);
  if (quiet) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  if (safetyCfg.notify === 'later' && filter !== 'flagged' && st === 'needs_review' && !c._showFlags) {
    box.innerHTML = `<div class="cb-later">${plural(sf.open || 0, 'flag')} waiting in the Flagged tab. <button class="link" data-show="1">Review now</button></div>`;
    return;
  }
  const flags = (sf && sf.flags) || [];
  const fps = new Set(flags.map(f => f.fp));
  const extra = (ck.fixes || []).filter(fx => !fps.has(fx.fp));
  let h = `<div class="cb-head"><span>Content check</span><span class="muted">VodClipper's own check, not TikTok's decision</span></div>`;
  if (st === 'unchecked') h += `<div class="cb-row"><span>This clip hasn't been checked.</span><button class="btn xs" data-act="check">Check now</button></div>`;
  if (ck.stale) h += `<div class="cb-row">${icon('alert')}<span>The clip changed since it was checked.</span><button class="btn xs" data-act="check">Check again</button></div>`;
  if (st === 'excluded') h += `<div class="cb-row excl"><span>Excluded from export. It stays here and won't render until you include it again.</span></div>`;
  if (flags.length || extra.length) {
    h += `<ul class="cflags">${flags.map(f => flagRow(c, f, kept)).join('')}`
      + extra.map(fx => `<li class="cf corrected"><div class="cf-h"><span class="cf-t">${esc(fx.flag || 'Flag')}</span><span class="cf-s">Fixed: ${esc((FIX_LABEL[fx.kind] || '').toLowerCase())}</span></div>`
        + (kept ? '' : `<div class="cf-a"><button class="btn xs ghost" data-act="undo" data-fix="${esc(fx.id)}">Undo fix</button></div>`) + '</li>').join('') + '</ul>';
  }
  const a = [];
  if (!kept && sf && sf.open) a.push(`<button class="btn sm" data-act="leave" title="Keep the clip exactly as it is">Leave as is</button>`);
  if (!kept && flags.some(f => f.source === 'speech' && f.status !== 'corrected')) a.push(`<button class="btn sm" data-edit="1" title="Jump to the flagged words in the Captions tab">Review and edit</button>`);
  a.push(ck.excluded ? `<button class="btn sm" data-act="include">Include in export</button>`
    : `<button class="btn sm" data-act="exclude" title="Keep it out of rendering without deleting it">Exclude from export</button>`);
  a.push(`<button class="btn sm ghost danger-text" data-remove="1" title="Take it out of this project">Remove…</button>`);
  h += `<div class="cb-acts">${a.join('')}</div>`;
  const more = [];
  if (flags.some(f => f.status === 'dismissed' || f.status === 'accepted')) more.push(`<button class="link" data-act="recheck">Check again from scratch (brings back dismissed flags)</button>`);
  if ((ck.log || []).length) more.push(`<details class="cb-log"${c._logOpen ? ' open' : ''}><summary>History (${ck.log.length})</summary><ul>`
    + ck.log.slice().reverse().map(l => `<li><span class="muted">${ago(l.at)}</span> ${esc(l.text)}</li>`).join('') + '</ul></details>');
  if (more.length) h += `<div class="cb-more">${more.join('')}</div>`;
  if (kept) h += `<p class="fine">This clip is in the render queue. Remove it from the queue to change it.</p>`;
  box.innerHTML = h;
  const det = box.querySelector('details'); if (det) det.ontoggle = () => { c._logOpen = det.open; };
}
$('#checkBox').addEventListener('click', async e => {
  const c = curClip(); if (!c) return;
  const t = e.target.closest('button'); if (!t) return;
  const d = t.dataset;
  if (d.seek != null) { stopPreview(); try { vod.currentTime = clamp(c.start + +d.seek - 1, c.start, c.end - 0.1); } catch {} vod.play().catch(() => {}); return; }
  if (d.show) { c._showFlags = true; renderCheck(c); return; }
  if (d.edit) return reviewEdit(c);
  if (d.remove) return removeClip(c);
  if (d.propose) return propose(c, d.fp, d.propose);
  if (d.prev) return togglePreview(c);
  if (d.cancel) { stopPreview(); c._prop = null; renderCheck(c); return; }
  if (d.apply) { const p = c._prop; stopPreview(); c._prop = null; if (p) await checkAct(c, 'fix', { fp: p.fp, kind: p.kind }); return; }
  if (d.act === 'check') return checkAct(c, '');
  if (d.act === 'include') return checkAct(c, 'exclude', { on: false });
  if (d.act === 'exclude') { await checkAct(c, 'exclude', { on: true }); refreshQueue(); return; }
  if (d.act) return checkAct(c, d.act, { fp: d.fp, fix: d.fix });
});
async function checkAct(c, action, args = {}) {
  let r;
  try { r = await api('/api/check', Object.assign(clipSnapshot(c), { action, args })); }
  catch (e) { toast(e.message, 'err', 6000); return null; }
  if (r.clips && r.clips[0]) applyCheck(c, r.clips[0]);
  if (action === 'fix' && r.fix) toast(`${FIX_LABEL[r.fix.kind]}: done. Undo is next to the flag.`, '', 3500);
  return r;
}
function applyCheck(c, v) {
  c.safety = v.safety; c.check = v.check;
  if (v.start != null && (v.start !== c.start || v.end !== c.end)) { c.start = v.start; c.end = v.end; c.userTrim = !!v.userTrim; }
  if (v.title != null && v.title !== c.title) { c.title = v.title; c.userTitle = !!v.userTitle; }
  if (v.userPost && v.postText !== c.postText) { c.userPost = true; c.postText = v.postText; }
  if (v.state && v.state !== c.state) c.state = v.state;
  if (curClip() === c && mode === 'clip') fillInspector();
  renderRows(); drawHeat(); dirty = true;
}
async function propose(c, fp, kind) {
  stopPreview();
  let r;
  try { r = await api('/api/check', Object.assign(clipSnapshot(c), { action: 'fix', args: { fp, kind, preview: true } })); }
  catch (e) { toast(e.message, 'err'); return; }
  c._prop = { fp, kind, view: r.clips[0] || {}, fix: r.fix || {}, playing: false };
  renderCheck(c);
}
// previewing a fix: the player plays the part it changes, muted or bleeped where it would be
let pv = null, actx = null, osc = null;
function togglePreview(c) {
  const p = c._prop; if (!p) return;
  if (p.playing) { stopPreview(); renderCheck(c); return; }
  const v = p.view || {};
  let from = c.start, to = Math.min(c.end, c.start + 4), span = null, title = null;
  if (p.kind === 'mute' || p.kind === 'bleep') { span = p.fix.span; from = Math.max(c.start, span[0] - 2); to = Math.min(c.end, span[1] + 2); }
  else if (p.kind === 'cut_end') { from = Math.max(v.start, v.end - 4); to = v.end; }
  else if (p.kind === 'cut_start') { from = v.start; to = Math.min(v.end, v.start + 4); }
  else if (p.kind === 'mask_title') title = v.title;
  pv = { c, kind: p.kind, span, to, title, t0: performance.now(), started: false };
  p.playing = true; dirty = true;
  try { vod.currentTime = from; } catch {}
  vod.play().catch(() => {});
  renderCheck(c);
}
function stopPreview() {
  if (!pv) return;
  const c = pv.c; pv = null; beep(false); vod.muted = userMuted; vod.pause();
  if (c._prop) c._prop.playing = false;
  dirty = true;
}
function beep(on) {
  if (on && !osc) {
    try { actx = actx || new AudioContext(); osc = actx.createOscillator(); const g = actx.createGain(); g.gain.value = 0.06; osc.frequency.value = 1000; osc.connect(g).connect(actx.destination); osc.start(); }
    catch { osc = null; }
  } else if (!on && osc) { try { osc.stop(); } catch {} osc = null; }
}
function fixAudio(c) {
  if (!c || vod.paused) { if (osc) beep(false); if (vod.muted !== userMuted) vod.muted = userMuted; return; }
  const t = vod.currentTime, hit = fixSpans(c).find(s => t >= s[0] && t <= s[1]);
  const m = userMuted || !!hit;
  if (vod.muted !== m) vod.muted = m;
  beep(!!hit && hit[2] === 'bleep' && !userMuted);
}
function reviewEdit(c) {
  const flags = (c.safety && c.safety.flags) || [];
  const f = flags.find(x => x.source === 'speech' && x.status === 'open') || flags.find(x => x.source === 'speech');
  $('#inspTabs button[data-t="captions"]').click();
  if (f) seekClip(c.start + f.start);
  toast('Fix the words here, or trim the clip in the Clip tab. Your video isn\'t changed, and Undo all edits brings the captions back.', '', 6500);
}
async function removeClip(c) {
  const n = c.id, k = keyOf(c);
  if (!await ask({ title: `Remove clip #${n} from this project?`, ok: 'Remove', danger: true,
    body: 'It leaves the clip list for good, even when you change the sliders. Your stream recording and any file already rendered stay where they are. You can undo this right away.' })) return;
  try { await api('/api/clip_remove', { pid: c.pid, id: c.id }); } catch (e) { toast(e.message, 'err'); return; }
  stopPreview();
  const i = W.clips.indexOf(c);
  if (i >= 0) W.clips.splice(i, 1);
  if (cur === k) { cur = null; const nx = W.clips[i] || W.clips[i - 1]; if (nx) select(keyOf(nx), false); else showInspector(); }
  renderRows(); drawHeat(); refreshQueue();
  toast(`Clip #${n} removed.`, '', 8000, { label: 'Undo', fn: async () => {
    try {
      const r = await api('/api/clip_restore', { pid: c.pid, id: n });
      if (W && W.kind === 'vod' && r.clip && !byKey(k)) { W.clips.splice(Math.min(Math.max(i, 0), W.clips.length), 0, prepClip(r.clip)); select(k, false); }
      renderRows(); drawHeat();
    } catch (e) { toast(e.message, 'err'); }
  } });
}
async function checkGate(c) {
  // before rendering: excluded clips don't render, and unreviewed flags get a warning (Settings > Content check)
  const st = checkStatus(c);
  if (st === 'excluded') { toast('This clip is excluded from export. Include it again in the content check box first.', 'err', 5500); return false; }
  if (st !== 'needs_review' || safetyCfg.enabled === false) return true;
  const mode_ = safetyCfg.export_flagged || 'ask';
  if (mode_ === 'allow') return true;
  if (mode_ === 'block') { toast('Review its flags first: leave it as is, fix them or dismiss them.', 'err', 5500); c._showFlags = true; renderCheck(c); return false; }
  const open = c.safety.flags.filter(f => f.status === 'open');
  if (!await ask({ title: 'Render a clip with flags?', ok: 'Render anyway',
    body: `${open.map(f => `${f.label}: “${f.text}”`).join(' · ')}. Rendering it records that you saw this warning. VodClipper's check isn't TikTok's decision either way.` })) return false;
  return !!(await checkAct(c, 'ack'));
}
async function flagBatch(action, args, verb, what) {
  let list = visible();
  if (action !== 'exclude') list = list.filter(c => c.state !== 'kept' && c.safety && c.safety.open);
  else list = list.filter(c => !checkOf(c).excluded);
  if (!list.length) { toast('Nothing in this list needs that.', '', 2800); return; }
  if (!await ask({ title: `${verb} for ${plural(list.length, 'clip')}?`, ok: verb, body: what })) return;
  const byPid = {};
  list.forEach(c => (byPid[c.pid] = byPid[c.pid] || []).push(c));
  for (const [pid, cs] of Object.entries(byPid)) {
    let r;
    try { r = await api('/api/check', { pid, ids: cs.map(c => c.id), action, args }); } catch (e) { toast(e.message, 'err'); continue; }
    for (const v of r.clips || []) { const c = cs.find(x => x.id === v.id); if (c) { c.safety = v.safety; c.check = v.check; if (v.state) c.state = v.state; } }
  }
  if (action === 'exclude') refreshQueue();
  renderRows(); drawHeat(); const c = curClip(); if (c && mode === 'clip') fillInspector();
  toast(`Done for ${plural(list.length, 'clip')}.`, '', 2500);
}
$('#flagFilter').onchange = e => { flagFilter = e.target.value; renderRows(); };
$('#flagLeaveAll').onclick = () => flagBatch('leave', {}, 'Leave as is', 'The clips stay exactly as they are and their open flags are marked as reviewed. You can undo it on each clip.');
$('#flagDismissAll').onclick = () => flagBatch('dismiss', {}, 'Dismiss flags', 'Their open flags are dismissed (they come back if the words change or the rules do). Dismissing doesn\'t make a clip safe.');
$('#flagExcludeAll').onclick = () => flagBatch('exclude', { on: true }, 'Exclude from export', 'They stay in the project but won\'t render until you include them again. Unfinished renders of them are taken out of the queue.');
async function refreshChecks() {
  // settings changed: pick up the new check results for the open VOD
  if (!W || W.kind !== 'vod') return;
  let r; try { r = await api('/api/clips/' + W.pid); } catch { return; }
  for (const s of r.clips.concat(r.gems || [])) { const c = byKey(keyOf(s)); if (c) { c.safety = s.safety; c.check = s.check; } }
  renderRows(); const c = curClip(); if (c && mode === 'clip') fillInspector();
}
async function retryRender(id) {
  try { await api('/api/queue/retry', { id }); toast('Rendering again…', '', 2500); queueEpoch++; await refreshQueue(); }
  catch (e) { toast(e.message, 'err'); }
}
async function keep() {
  const c = curClip(); if (!c || mode !== 'clip') return;
  if (c.state === 'kept') {
    const q = queueFor(c);
    if (q && q.status === 'error') await retryRender(q.id);
    return;
  }
  if (!await checkGate(c)) return;
  const rank = W.kind === 'live' ? (c.moment || rankOf(c)) : c.id;
  const clip = { id: c.id, rank, start: c.start, end: c.end,
                 title: c.title.trim(), post: $('#postInput').value || postFor(c), thumb: c.thumb };
  if (c.wordsEdited) clip.words = c.words.filter(w => w.start >= c.start - 0.2 && w.start < c.end);
  queueEpoch++;
  try { const it = await api('/api/queue/add', { pid: c.pid, clip, opts: JSON.parse(JSON.stringify(opts)) }); queueItems.push(it); queueEpoch++; }
  catch (e) { toast(e.message, 'err'); return; }
  c.state = 'kept'; saveClip(c);
  feedback(c, 'keep');
  toast(`Rendering "${clip.title || 'clip ' + clip.rank}" in the background.`, '', 2500);
  updateStatusBar(); advance(keyOf(c));
}
async function skipOrUndo() {
  const c = curClip(); if (!c || mode !== 'clip') return;
  if (c.state === 'kept') {
    const q = queueFor(c);
    if (q && q.status === 'done') return;
    queueEpoch++;
    if (q) await api('/api/queue/remove', { id: q.id }).catch(() => {});
    c.state = 'todo'; saveClip(c); feedback(c, 'undo'); await refreshQueue(); fillInspector(); renderRows(); return;
  }
  if (c.state === 'skipped') { c.state = 'todo'; saveClip(c); feedback(c, 'undo'); fillInspector(); renderRows(); return; }
  c.state = 'skipped'; saveClip(c); feedback(c, 'skip'); advance(keyOf(c));
}
// what you keep and skip teaches the ranking (Settings > Clip ranking)
function feedback(c, action) { api('/api/feedback', { pid: c.pid, clip: c.id, action }).catch(() => {}); }
// everything you do to a clip is saved with the VOD, so it's all there when you open it again
function clipSnapshot(c) {
  const b = { pid: c.pid, id: c.id, state: c.state, start: c.start, end: c.end, title: c.title || '',
    userTrim: !!c.userTrim, userTitle: !!c.userTitle, userPost: !!c.userPost, postText: c.postText || '', wordsEdited: !!c.wordsEdited };
  if (c.wordsEdited) b.words_edit = c.words.map(w => ({ start: w.start, end: w.end, word: w.word, g: w.g, edited: !!w.edited }));
  return b;
}
function saveClip(c, soon) {
  if (!W || W.kind !== 'vod' || !c) return;
  clearTimeout(c._saveT);
  const go = () => api('/api/clip_state', clipSnapshot(c)).catch(() => {});
  if (soon) c._saveT = setTimeout(go, 600); else go();
}
$('#keepBtn').onclick = keep;
$('#skipBtn').onclick = skipOrUndo;
$('#prevBtn').onclick = () => step(-1);
$('#nextBtn').onclick = () => step(1);
$('#reviewSkipped').onclick = () => { filter = 'skipped'; syncFilterTabs(); renderRows(); const f = visible()[0]; if (f) select(keyOf(f)); };
$('#findMore').onclick = () => { setCount(Math.min(100, (+$('#count').value || W.clips.filter(c => !isGem(c)).length) + 5)); repick(); };
$('#goExports').onclick = () => setView('exports');
$('#inspTabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  $$('#inspTabs button').forEach(x => x.classList.toggle('on', x === b));
  ['clip', 'captions', 'text', 'layout'].forEach(t => { $('#pane-' + t).hidden = b.dataset.t !== t; });
  if (b.dataset.t === 'captions') renderCapEditor();
};

/* --- re-ranking (VOD only) --- */
let repickTimer = 0;
function repickSoon() { if (!W || W.kind !== 'vod') return; clearTimeout(repickTimer); repickTimer = setTimeout(repick, 350); }
async function repick() {
  if (!W || W.kind !== 'vod') return;
  try {
    const old = new Map(W.clips.map(c => [keyOf(c), c]));
    const p = await api('/api/repick', { pid: W.pid, count: +$('#count').value, length: +$('#length').value, chat_weight: +$('#cw').value });
    // clips that are still suggested keep everything you did to them
    W.clips = p.clips.map(s => { const o = old.get(keyOf(s)); if (o) { mergeClip(o, s); return o; } return prepClip(s); })
      .concat(W.clips.filter(isGem));
    W.curve = p.curve; W.meta.estimate = p.estimate; W.meta.learning = p.learning; buildHead();
    if (!curClip()) { const f = W.clips.find(c => c.state === 'todo' && !isGem(c)); if (f) select(keyOf(f), !vod.paused); else { cur = null; showInspector(); } }
    renderRows(); drawHeat(); fillInspector(); listen(); refreshQueue();
  } catch (e) { toast(e.message, 'err'); }
}

/* --- previewing any moment (VOD only) --- */
function preview(t) {
  if (W.kind !== 'vod') return;
  mode = 'free';
  loadSource(W.meta.vod, t, true);
  loadFrame(W.pid, t);
  showInspector(); renderRows(); drawHeat(); dirty = true;
}
$('#backBtn').onclick = () => { const c = curClip() || visible()[0] || W.clips[0]; if (c) select(keyOf(c)); else { mode = 'clip'; showInspector(); } };
$('#addHere').onclick = async () => {
  try {
    const c = prepClip(await api('/api/add_clip', { pid: W.pid, t: vod.currentTime, length: +$('#length').value }));
    c.kind = 'hype';
    c.manual = true; W.clips.push(c); filter = 'todo'; syncFilterTabs();
    select(keyOf(c)); listen([c.id]);
  } catch (e) { toast(e.message, 'err'); }
};

/* =============================================================== player */
const vod = $('#vod'), phone = $('#phone'), pg = phone.getContext('2d');
let dirty = true, vodOk = true, pendingSeek = null, pendingPlay = false;
function loadSource(url, t, play) {
  if (vod.dataset.src !== url) {
    vod.dataset.src = url; vodOk = true;
    pendingSeek = t; pendingPlay = play;
    vod.src = url; vod.load();
  } else {
    try { vod.currentTime = t; } catch {}
    if (play) vod.play().catch(() => {});
  }
  dirty = true;
}
vod.addEventListener('loadedmetadata', () => {
  if (pendingSeek != null) { try { vod.currentTime = pendingSeek; } catch {} pendingSeek = null; }
  if (pendingPlay) vod.play().catch(() => {});
  pendingPlay = false; dirty = true;
});
['play', 'pause', 'seeked', 'loadeddata'].forEach(ev => vod.addEventListener(ev, () => { dirty = true; updatePlayIcon(); }));
vod.addEventListener('error', () => { if (vod.dataset.src) { vodOk = false; dirty = true; } });
let userMuted = store.get('muted', false);
vod.muted = userMuted;
function updatePlayIcon() { $('#playIcon').setAttribute('href', vod.paused ? '#i-play' : '#i-pause'); }
function updateMute() { $('#muteIcon').setAttribute('href', userMuted ? '#i-mute' : '#i-vol'); }
updateMute();
function togglePlay() { if (vod.paused) vod.play().catch(() => {}); else vod.pause(); }
$('#playBtn').onclick = togglePlay;
phone.addEventListener('click', () => { if (suppressClick) { suppressClick = false; return; } togglePlay(); });
$('#muteBtn').onclick = () => { userMuted = !userMuted; vod.muted = userMuted; store.set('muted', userMuted); updateMute(); };
$('#scrub').addEventListener('pointerdown', e => {
  const c = curClip(); if (mode !== 'clip' || !c) return;
  const seek = ev => { const r = $('#scrub').getBoundingClientRect(); const f = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)); vod.currentTime = c.start + f * (c.end - c.start); dirty = true; };
  seek(e); $('#scrub').setPointerCapture(e.pointerId);
  $('#scrub').onpointermove = ev => { if (ev.buttons) seek(ev); };
});
let wheelAt = 0;
$('#phoneBox').addEventListener('wheel', e => {
  if (mode !== 'clip' || Math.abs(e.deltaY) < 30) return;
  e.preventDefault();
  const now = Date.now(); if (now - wheelAt < 450) return; wheelAt = now;
  step(e.deltaY > 0 ? 1 : -1);
}, { passive: false });

function sizePhone() {
  const box = $('#playerBox'); if (!box.clientWidth) return;
  let ow = 1080, oh = 1920; try { [ow, oh] = OUT(); } catch {}
  const r = ow / oh;
  const h = Math.max(120, Math.min(box.clientHeight - 32, (box.clientWidth - 32) / r));
  $('#phoneBox').style.height = h + 'px'; $('#phoneBox').style.width = (h * r) + 'px';
}
new ResizeObserver(sizePhone).observe($('#playerBox'));

// mouse clicks shouldn't leave focus on a button, or Enter/Space would press it again instead of the shortcut
document.addEventListener('pointerup', e => {
  const b = e.target.closest('button');
  if (b && view === 'review' && !$('#dlg').open) setTimeout(() => { if (document.activeElement === b) b.blur(); }, 0);
});
document.addEventListener('keydown', e => {
  if (view !== 'review' || !W || $('#dlg').open || e.target.closest('input,select,textarea')) return;
  const k = e.key;
  if (k === ' ') { e.preventDefault(); togglePlay(); }
  else if (k === 'ArrowDown' || k === 'j') { e.preventDefault(); step(1); }
  else if (k === 'ArrowUp' || k === 'k') { e.preventDefault(); step(-1); }
  else if (k === 'Enter' && !e.target.closest('button')) { e.preventDefault(); keep(); }
  else if (k === 'x' || k === 'Delete') skipOrUndo();
  else if (k === 'ArrowLeft') { vod.currentTime = Math.max(0, vod.currentTime - 2); }
  else if (k === 'ArrowRight') { vod.currentTime = vod.currentTime + 2; }
  else if (k === 't') nextTitle();
  else if (k === 'm') $('#muteBtn').click();
  else if (k === 'c' && W.kind === 'live') clipNow();
});

/* --- drawing (mirrors engine.build_filter and build_ass so the preview matches the render) --- */
const bgCanvas = document.createElement('canvas'); bgCanvas.width = 36; bgCanvas.height = 64;
function layoutDraw(g, src, sw, sh, Wd, Ht) {
  const z = Math.max(1, opts.game_zoom);
  const gameCrop = (ow, oh) => { const a = ow / oh; const cw = Math.min(sw, sh * a) / z, ch = cw / a;
    const cx = Math.min(Math.max(0, opts.game_x * sw - cw / 2), sw - cw); return [cx, (sh - ch) / 2, cw, ch]; };
  const lay = layoutOf();
  if (lay === 'split') {
    const th = Math.round(Ht * Math.min(.5, Math.max(.2, opts.cam_height)));
    const x = opts.cam[0] * sw, y = opts.cam[1] * sh, w = Math.max(4, opts.cam[2] * sw), h = Math.max(4, opts.cam[3] * sh);
    const s = Math.max(Wd / w, th / h), vw = Wd / s, vh = th / s;
    g.drawImage(src, x + (w - vw) / 2, y + (h - vh) / 2, vw, vh, 0, 0, Wd, th);
    const [gx, gy, gw, gh] = gameCrop(Wd, Ht - th); g.drawImage(src, gx, gy, gw, gh, 0, th, Wd, Ht - th);
    return th;
  }
  if (lay === 'full') { const [gx, gy, gw, gh] = gameCrop(Wd, Ht); g.drawImage(src, gx, gy, gw, gh, 0, 0, Wd, Ht); return Ht * .7; }
  const b = bgCanvas.getContext('2d'), bw = Math.min(sw, sh * Wd / Ht), bh = bw * Ht / Wd;
  bgCanvas.width = 36; bgCanvas.height = Math.max(8, Math.round(36 * Ht / Wd));
  b.drawImage(src, (sw - bw) / 2, (sh - bh) / 2, bw, bh, 0, 0, bgCanvas.width, bgCanvas.height);
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
  g.drawImage(bgCanvas, 0, 0, Wd, Ht); g.fillStyle = 'rgba(0,0,0,.18)'; g.fillRect(0, 0, Wd, Ht);
  const fh = Wd * sh / sw;
  if (z > 1) { const [gx, gy, gw, gh] = gameCrop(Wd, fh); g.drawImage(src, gx, gy, gw, gh, 0, (Ht - fh) / 2, Wd, fh); }
  else g.drawImage(src, 0, (Ht - fh) / 2, Wd, fh);
  return Math.min(Ht / 2 + fh / 2 + Ht * 170 / OUT()[1], Ht * .8);
}
const FONTS = {
  classic: { label: 'Classic', css: "'Arial Black', 'Segoe UI Black', 'Segoe UI', sans-serif", w: 900, scale: 1 },
  montserrat: { label: 'Montserrat', css: "'VC Montserrat'", w: 400, scale: 1 },
  poppins: { label: 'Poppins', css: "'VC Poppins'", w: 400, scale: 1 },
  inter: { label: 'Inter', css: "'VC Inter'", w: 400, scale: 1.05 },
  anton: { label: 'Anton', css: "'VC Anton'", w: 400, scale: 1.3 },
  bebas: { label: 'Bebas', css: "'VC Bebas'", w: 400, scale: 1.4 },
  bangers: { label: 'Bangers', css: "'VC Bangers'", w: 400, scale: 1.25 },
  luckiest: { label: 'Luckiest', css: "'VC Luckiest'", w: 400, scale: 1 },
};
const fontOf = k => FONTS[k] || FONTS.classic;
const fontCss = (k, px) => { const f = fontOf(k); return `${f.w} ${Math.round(px)}px ${f.css}`; };
// load the bundled faces so the first preview frame already uses them
Object.keys(FONTS).filter(k => k !== 'classic').forEach(k => document.fonts.load(fontCss(k, 40)).then(() => { dirty = true; }).catch(() => {}));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const hit = { title: null, cap: null };   // where the title and captions were drawn (canvas px), for dragging

function drawTitle(g, text, Wd, Ht) {
  const k = Wd / OUT()[0], st = opts.title_style;
  const fs = 64 * clamp(opts.title_size, 50, 160) / 100 * fontOf(opts.title_font).scale * k;
  const pad = Math.max(8, Math.floor(64 * clamp(opts.title_size, 50, 160) / 100 * fontOf(opts.title_font).scale / 4)) * k;
  g.font = fontCss(opts.title_font, fs); g.textAlign = 'center'; g.textBaseline = 'top';
  const words = text.trim().split(/\s+/), lines = []; let line = '';
  const x = clamp(opts.title_x, .3, .7) * Wd, top = clamp(opts.title_y, 0, .92) * Ht;
  const wrapW = 2 * (Math.min(x, Wd - x) - 90 * k);
  for (const w of words) { const t = line ? line + ' ' + w : w; if (g.measureText(t).width > wrapW && line) { lines.push(line); line = w; } else line = t; }
  if (line) lines.push(line);
  const lh = fs * 1.19, tw = Math.max(...lines.map(l => g.measureText(l).width));
  if (st === 'outline') {
    const ob = Math.max(4, Math.floor(fs / k / 10)) * k;
    g.lineJoin = 'round'; g.lineWidth = ob * 2; g.strokeStyle = '#000'; g.fillStyle = '#fff';
    lines.forEach((l, i) => { g.strokeText(l, x, top + i * lh); g.fillText(l, x, top + i * lh); });
    hit.title = { x: x - tw / 2 - ob, y: top - ob, w: tw + 2 * ob, h: lines.length * lh + ob };
    return;
  }
  const [fill, boxc] = st === 'dark' ? ['#fff', 'rgba(20,20,20,.9)'] : st === 'accent' ? ['#141414', opts.caption_color] : ['#141414', '#fff'];
  const bw = tw + 2 * pad, bh = lines.length * lh + 2 * pad - (lh - fs);
  g.fillStyle = boxc; g.fillRect(x - bw / 2, top, bw, bh);
  g.fillStyle = fill; lines.forEach((l, i) => g.fillText(l, x, top + pad + i * lh));
  hit.title = { x: x - bw / 2, y: top, w: bw, h: bh };
}
function chunksFor(c) {
  const ws = c.words.filter(w => w.start >= c.start - 0.2 && w.start < c.end);
  const chunks = []; let ch = [];
  for (const w of ws) {
    const prev = ch[ch.length - 1];
    // a line typed in the Captions tab stays together (g); otherwise break by length, pauses and sentence ends
    const brk = ch.length && ((w.g || prev.g) ? w.g !== prev.g
      : (ch.length >= Math.max(1, opts.words_per_line) || w.start - prev.end > 0.6 || /[.?!]$/.test(prev.word)));
    if (brk) { chunks.push(ch); ch = []; }
    ch.push(w);
  }
  if (ch.length) chunks.push(ch);
  return chunks.map((k, i) => {
    const last = k[k.length - 1];
    let end = i + 1 < chunks.length ? Math.min(Math.max(last.end, last.end + 0.3), chunks[i + 1][0].start) : last.end + 0.4;
    return { words: k, start: k[0].start, end: Math.min(end, c.end) };
  });
}
function captionAt(c, t) {
  for (const ch of chunksFor(c)) {
    if (t >= ch.start && t < ch.end) {
      let hi = 0; ch.words.forEach((w, j) => { if (w.start <= t) hi = j; });
      return { texts: ch.words.map(w => { const t = censorWord(c, w); return opts.uppercase ? t.toUpperCase() : t; }), hi, t0: ch.start, wt: ch.words[hi].start };
    }
  }
  return null;
}
const maskWord = w => w.replace(/^(\W*)(\w)(\w*)/, (m, a, b, r) => a + b + '*'.repeat(r.length));
function censorWord(c, w) {
  // preview what will be masked: fixes made in Review, and automatic censoring (when it's on)
  const abs = w.start + 0.01;
  if (fixSpans(c).some(s => abs >= s[0] - 0.02 && abs <= s[1] + 0.02)) return maskWord(w.word);
  if (!safetyCfg.enabled || !safetyCfg.censor || safetyCfg.censor === 'off' || !c.safety) return w.word;
  const cats = safetyCfg.censor_categories || [], t = w.start - c.start + 0.01;
  return c.safety.flags.some(f => f.source === 'speech' && cats.includes(f.cat) && f.status !== 'accepted' && f.status !== 'dismissed'
    && t >= f.start - 0.02 && t <= f.end + 0.02) ? maskWord(w.word) : w.word;
}
function capAnchor(capY, Wd, Ht) {
  const oh = OUT()[1];
  if (opts.caption_pos === 'custom') return [clamp(opts.cap_x, .3, .7) * Wd, clamp(opts.cap_y * Ht, Ht * 120 / oh, Ht - Ht * 120 / oh)];
  capY = { top: Ht * .22, middle: Ht * .5, bottom: Ht * .78 }[opts.caption_pos] ?? capY;
  return [Wd / 2, clamp(capY, Ht * 260 / oh, Ht - Ht * 220 / oh)];
}
function drawCaption(g, cap, capY, Wd, Ht, t) {
  const k = Wd / OUT()[0], st = opts.caption_style, hi = opts.caption_color;
  const [cx, cy] = capAnchor(capY, Wd, Ht);
  const chars = cap.texts.join(' ').length, fsBase = 86 * opts.caption_size / 100 * fontOf(opts.caption_font).scale;
  const fs1080 = chars <= 16 ? fsBase : Math.max(fsBase * .75, fsBase * 18 / chars), fs = fs1080 * k;
  g.font = fontCss(opts.caption_font, fs); g.textAlign = 'left'; g.textBaseline = 'middle';
  const sp = g.measureText(' ').width, widths = cap.texts.map(w => g.measureText(w).width);
  const maxW = 2 * (Math.min(cx, Wd - cx) - 70 * k), lines = [[]]; let lw = 0;
  cap.texts.forEach((w, i) => { if (lw && lw + sp + widths[i] > maxW) { lines.push([]); lw = 0; } lines[lines.length - 1].push(i); lw += (lw ? sp : 0) + widths[i]; });
  const lh = fs * 1.15, y0 = cy - (lines.length - 1) * lh / 2;
  const bord = Math.max(4, Math.floor(fs1080 / 12)) * k;
  // pop-in: the chunk scales from 114% to 100% over its first 0.1 s (same as the render)
  const age = t - cap.t0, sc = opts.caption_anim && st !== 'clean' && age >= 0 && age < .1 ? 1.14 - .14 * age / .1 : 1;
  g.save(); g.translate(cx, cy); g.scale(sc, sc); g.translate(-cx, -cy);
  let minX = Infinity, maxX = -Infinity;
  lines.forEach((idx, li) => {
    const lwid = idx.reduce((a, i) => a + widths[i], 0) + sp * (idx.length - 1);
    let x = cx - lwid / 2; minX = Math.min(minX, x); maxX = Math.max(maxX, x + lwid);
    const y = y0 + li * lh;
    idx.forEach(i => {
      const on = i === cap.hi, word = cap.texts[i];
      g.lineJoin = 'round';
      g.shadowColor = 'transparent'; g.shadowBlur = 0; g.shadowOffsetX = g.shadowOffsetY = 0;
      if (st === 'clean') {
        g.shadowColor = 'rgba(0,0,0,.6)'; g.shadowBlur = 6 * k; g.shadowOffsetX = g.shadowOffsetY = 4 * k;
        g.fillStyle = '#fff'; g.fillText(word, x, y);
      } else if (st === 'glow') {
        const s2 = on ? 1.12 : 1;
        g.save(); g.translate(x + widths[i] / 2, y); g.scale(s2, s2); g.translate(-(x + widths[i] / 2), -y);
        g.shadowColor = hi; g.shadowBlur = Math.max(5, fs1080 / 8) * k * 1.6;
        g.lineWidth = bord * 2; g.strokeStyle = hi; g.strokeText(word, x, y);
        g.shadowBlur = 0; g.fillStyle = '#fff'; g.fillText(word, x, y);
        g.restore();
      } else if (st === 'box' && on) {
        g.lineWidth = Math.max(8, Math.floor(fs1080 * .22)) * k * 2; g.strokeStyle = hi; g.strokeText(word, x, y);
        g.fillStyle = '#141414'; g.fillText(word, x, y);
      } else {
        g.lineWidth = bord * 2; g.strokeStyle = '#000'; g.strokeText(word, x, y);
        g.fillStyle = (st === 'pop' && on) || (st === 'karaoke' && i <= cap.hi) ? hi : '#fff';
        g.fillText(word, x, y);
      }
      x += widths[i] + sp;
    });
  });
  g.restore();
  hit.cap = { x: minX - bord, y: y0 - lh / 2 - bord, w: maxX - minX + 2 * bord, h: lines.length * lh + 2 * bord };
}
function drawGuides(g, Wd, Ht) {
  // what TikTok's own buttons and caption cover, so text can be kept clear of them
  g.save();
  g.fillStyle = 'rgba(255,70,90,.13)';
  g.fillRect(Wd * .86, Ht * .36, Wd * .14, Ht * .5); g.fillRect(0, Ht * .86, Wd, Ht * .14); g.fillRect(0, 0, Wd, Ht * .06);
  g.fillStyle = 'rgba(255,255,255,.55)'; g.font = `500 ${Math.round(Wd / 40)}px Inter, "Segoe UI", sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText('Covered by TikTok', Wd / 2, Ht * .93);
  if (dragState && dragState.snapX) { g.strokeStyle = 'rgba(255,214,102,.9)'; g.lineWidth = 1; g.setLineDash([6, 5]); g.beginPath(); g.moveTo(Wd / 2, 0); g.lineTo(Wd / 2, Ht); g.stroke(); }
  g.restore();
}
function drawSelection(g, r, strong) {
  if (!r) return;
  g.save(); g.strokeStyle = strong ? '#fff' : 'rgba(255,255,255,.7)'; g.lineWidth = 1.5; g.setLineDash(strong ? [] : [5, 4]);
  g.strokeRect(r.x - 4, r.y - 4, r.w + 8, r.h + 8);
  if (strong) { g.fillStyle = '#fff'; [[r.x - 4, r.y - 4], [r.x + r.w + 4, r.y - 4], [r.x - 4, r.y + r.h + 4], [r.x + r.w + 4, r.y + r.h + 4]].forEach(([x, y]) => { g.beginPath(); g.arc(x, y, 4, 0, 7); g.fill(); }); }
  g.restore();
}
let hoverWhat = null, phoneHover = false, dragState = null, suppressClick = false;
function renderPhone() {
  const Wd = phone.width, Ht = phone.height, g = pg;
  g.fillStyle = '#000'; g.fillRect(0, 0, Wd, Ht);
  hit.title = hit.cap = null;
  if (!W) return;
  let src = null, sw = 0, sh = 0;
  if (vodOk && vod.readyState >= 2 && vod.videoWidth) { src = vod; sw = vod.videoWidth; sh = vod.videoHeight; }
  else if (frame.complete && frame.naturalWidth) { src = frame; sw = frame.naturalWidth; sh = frame.naturalHeight; }
  let badge = '';
  if (src) {
    const capY = layoutDraw(g, src, sw, sh, Wd, Ht);
    const c = curClip();
    if (mode === 'clip' && c) {
      if (dragState) drawGuides(g, Wd, Ht);
      const tShow = pv && pv.c === c && pv.title != null ? pv.title : c.title;   // previewing "Hide it in the title"
      if (tShow.trim()) drawTitle(g, tShow, Wd, Ht);
      if (opts.captions) {
        const cap = captionAt(c, vod.currentTime);
        if (cap) drawCaption(g, cap, capY, Wd, Ht, vod.currentTime);
        else if (phoneHover || (dragState && dragState.what === 'cap')) {
          // nothing being said right now: show a stand-in so the captions can still be placed
          g.save(); g.globalAlpha = .55;
          const first = chunksFor(c)[0];
          drawCaption(g, { texts: first ? first.words.map(w => opts.uppercase ? w.word.toUpperCase() : w.word) : ['YOUR', 'CAPTIONS'], hi: 0, t0: -9 }, capY, Wd, Ht, 0);
          g.restore();
        }
        if (!cap && !c.listened && status.whisper && !phoneHover) badge = 'Listening for captions…';
      }
      const sel = dragState ? dragState.what : hoverWhat;
      if (sel) drawSelection(g, hit[sel], !!dragState);
    }
    if (!vodOk) badge = 'Still frame: this video can\'t play in the preview';
  }
  if (mode === 'free') badge = badge || 'Preview';
  $('#phoneBadge').textContent = badge;
}

/* --- drag the title and captions around the frame --- */
function phonePt(e) { const r = phone.getBoundingClientRect(); return [(e.clientX - r.left) * phone.width / r.width, (e.clientY - r.top) * phone.height / r.height]; }
function hitTest(x, y) {
  const inside = r => r && x >= r.x - 10 && x <= r.x + r.w + 10 && y >= r.y - 10 && y <= r.y + r.h + 10;
  if (inside(hit.title)) return 'title';
  if (inside(hit.cap)) return 'cap';
  return null;
}
phone.addEventListener('pointerdown', e => {
  if (mode !== 'clip' || !curClip() || e.button !== 0) return;
  const [x, y] = phonePt(e), what = hitTest(x, y); if (!what) return;
  const r = hit[what], Wd = phone.width, Ht = phone.height;
  dragState = { what, ox: x - (r.x + r.w / 2), oy: what === 'title' ? y - r.y : y - (r.y + r.h / 2), moved: false, sx: x, sy: y, snapX: false };
  if (what === 'cap' && opts.caption_pos !== 'custom') { opts.cap_x = .5; opts.cap_y = (r.y + r.h / 2) / Ht; }
  phone.setPointerCapture(e.pointerId); dirty = true;
});
phone.addEventListener('pointermove', e => {
  const [x, y] = phonePt(e), Wd = phone.width, Ht = phone.height;
  if (dragState) {
    if (!dragState.moved && Math.hypot(x - dragState.sx, y - dragState.sy) < 4) return;
    dragState.moved = true;
    let fx = (x - dragState.ox) / Wd; dragState.snapX = Math.abs(fx - .5) < .025; if (dragState.snapX) fx = .5;
    if (dragState.what === 'title') { opts.title_x = clamp(fx, .3, .7); opts.title_y = clamp((y - dragState.oy) / Ht, 0, .92); }
    else { opts.caption_pos = 'custom'; opts.cap_x = clamp(fx, .3, .7); opts.cap_y = clamp((y - dragState.oy) / Ht, 120 / OUT()[1], 1 - 120 / OUT()[1]); }
    dirty = true; return;
  }
  const h = mode === 'clip' ? hitTest(x, y) : null;
  phone.style.cursor = h ? 'move' : 'pointer';
  if (h !== hoverWhat) { hoverWhat = h; dirty = true; }
});
phone.addEventListener('pointerup', () => {
  if (!dragState) return;
  suppressClick = dragState.moved; dragState = null; dirty = true;
  if (suppressClick) syncControls();
});
phone.addEventListener('pointerenter', () => { phoneHover = true; dirty = true; });
phone.addEventListener('pointerleave', () => { phoneHover = false; if (hoverWhat) { hoverWhat = null; } dirty = true; });
function tick() {
  requestAnimationFrame(tick);
  if (!W || view !== 'review') return;
  const c = curClip();
  if (pv && !vod.paused) pv.started = true;     // play() takes a moment to start: only a pause after that ends the preview
  if (pv && (pv.c !== c || vod.currentTime >= pv.to || (vod.paused && pv.started) || performance.now() - pv.t0 > 15000)) {
    const pc = pv.c; stopPreview(); if (curClip() === pc) renderCheck(pc);
  }
  if (mode === 'clip' && c && !vod.paused) {
    if (vod.currentTime >= c.end - 0.04 || vod.currentTime < c.start - 1) { try { vod.currentTime = c.start; } catch {} }
  }
  fixAudio(mode === 'clip' ? c : null);
  if (mode === 'clip' && c) {
    const f = Math.max(0, Math.min(1, (vod.currentTime - c.start) / (c.end - c.start)));
    $('#scrubFill').style.width = (f * 100) + '%';
    $('#timeText').textContent = `${fmt(vod.currentTime - c.start)} / ${fmt(c.end - c.start)}`;
  } else if (mode === 'free') {
    $('#scrubFill').style.width = '0'; $('#timeText').textContent = fmt(vod.currentTime); $('#freeAt').textContent = fmt(vod.currentTime);
  } else { $('#scrubFill').style.width = '0'; $('#timeText').textContent = '0:00 / 0:00'; }
  if (dirty || !vod.paused) { dirty = false; renderPhone(); if (!$('#pane-captions').hidden) syncCapActive(); }
  if (mode === 'clip' && c) { edFollow(); if (edDirty || !vod.paused || ed.drag) { edDirty = false; drawEditor(); } }
  if (!vod.paused) drawHeatSoon();
}
requestAnimationFrame(tick);

/* =============================================================== timeline */
const heat = $('#heat');
let heatRaf = 0, heatLast = 0;
function drawHeat() { cancelAnimationFrame(heatRaf); heatRaf = requestAnimationFrame(drawHeatNow); }
function drawHeatSoon() { const now = performance.now(); if (now - heatLast > 250) { heatLast = now; drawHeat(); } }
function drawHeatNow() {
  if (!W || view !== 'review') return;
  const dpr = window.devicePixelRatio || 1, Wd = heat.clientWidth, Ht = heat.clientHeight;
  if (!Wd || !Ht) return;
  if (heat.width !== Wd * dpr || heat.height !== Ht * dpr) { heat.width = Wd * dpr; heat.height = Ht * dpr; }
  const g = heat.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, Wd, Ht);
  const D = Math.max(1, W.duration), curve = W.curve || [], n = curve.length, base = Ht - 14;
  $('#tlEnd').textContent = fmt(D);
  $('#tlLabel').textContent = W.kind === 'live' ? 'Whole stream so far. Click a moment to review it.' : 'Click a highlighted range to review it, or anywhere else to preview.';
  // clip ranges
  W.clips.forEach(c => {
    const a = absOf(c, c.start) / D * Wd, b = Math.max(a + 2, absOf(c, c.end) / D * Wd);
    const isCur = keyOf(c) === cur && mode === 'clip';
    g.fillStyle = isCur ? 'rgba(76,141,255,.20)' : c.state === 'kept' ? 'rgba(62,194,143,.13)' : c.state === 'skipped' ? 'rgba(255,255,255,.025)'
      : isGem(c) ? 'rgba(201,167,255,.10)' : 'rgba(255,255,255,.06)';
    g.fillRect(a, 0, b - a, base);
    if (isCur) { g.fillStyle = '#4c8dff'; g.fillRect(a, 0, b - a, 2); }
  });
  // signal: neutral bars, the hottest moments in accent
  const hi = Math.max(3, ...curve), bw = Wd / Math.max(1, n);
  for (let i = 0; i < n; i++) {
    const v = Math.max(0, curve[i] / hi), h = Math.max(1, v * (base - 8));
    g.fillStyle = v > 0.55 ? '#4c8dff' : v > 0.3 ? '#5a6274' : '#363a44';
    g.fillRect(i * bw, base - h, Math.max(1, bw - (bw > 3 ? 1 : 0)), h);
  }
  // markers
  g.font = '600 10.5px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center';
  const placed = [];
  const gemNo = c => W.clips.filter(isGem).indexOf(c) + 1, hypeNo = c => W.clips.filter(x => !isGem(x)).indexOf(c) + 1;
  W.clips.map((c, i) => ({ c, i, x: (absOf(c, c.start) + absOf(c, c.end)) / 2 / D * Wd }))
    .sort((a, b) => (keyOf(b.c) === cur) - (keyOf(a.c) === cur) || a.i - b.i)
    .forEach(({ c, i, x }) => {
      if (placed.some(px => Math.abs(px - x) < 14)) return;
      placed.push(x);
      g.fillStyle = keyOf(c) === cur && mode === 'clip' ? '#e9eaed' : c.state === 'kept' ? '#3ec28f' : c.state === 'skipped' ? '#4a4d56'
        : isGem(c) ? '#c9a7ff' : '#a3a6ae';
      g.fillText(W.kind === 'live' ? String(c.moment || i + 1) : isGem(c) ? 'G' + gemNo(c) : String(hypeNo(c)), x, Ht - 2);
    });
  // playhead (or live edge)
  let ph = null;
  const c = curClip();
  if (mode === 'clip' && c && vod.dataset.src === c.vod) ph = absOf(c, vod.currentTime);
  else if (mode === 'free') ph = vod.currentTime;
  if (ph != null) { g.fillStyle = '#e9eaed'; g.fillRect(Math.round(ph / D * Wd) - 1, 0, 2, base); }
  if (W.kind === 'live' && W.live && W.live.status === 'recording') { g.fillStyle = '#f2484d'; g.fillRect(Wd - 2, 0, 2, base); }
}
heat.addEventListener('click', e => {
  if (!W) return;
  const r = heat.getBoundingClientRect(), t = (e.clientX - r.left) / r.width * W.duration;
  const hit = W.clips.find(c => t >= absOf(c, c.start) && t <= absOf(c, c.end));
  if (hit) {
    const want = isGem(hit) ? 'gems' : filter === 'gems' || (hit.state !== 'todo' && filter === 'todo') ? (hit.state === 'todo' ? 'todo' : 'all') : filter;
    if (want !== filter) { filter = want; syncFilterTabs(); }
    select(keyOf(hit));
  }
  else if (W.kind === 'vod') preview(t);
  else toast('In live mode, use Clip last 30s to grab something that isn\'t highlighted.', '', 3500);
});
heat.addEventListener('mousemove', e => {
  if (!W) return;
  const r = heat.getBoundingClientRect(), dock = heat.parentElement.getBoundingClientRect(), x = e.clientX - r.left, tag = $('#hoverTag');
  const t = x / r.width * W.duration, hit = W.clips.find(c => t >= absOf(c, c.start) && t <= absOf(c, c.end));
  tag.style.display = 'block'; tag.textContent = hit ? `${fmt(t)}  ${hit.title || 'clip'}` : fmt(t);
  tag.style.left = Math.min(dock.width - 80, Math.max(60, e.clientX - dock.left)) + 'px';
});
heat.addEventListener('mouseleave', () => $('#hoverTag').style.display = 'none');
new ResizeObserver(drawHeat).observe(heat);

/* =============================================================== format */
$('#swatches').innerHTML = SWATCHES.map(c => `<button data-c="${c}" style="background:${c}" aria-label="Highlight color ${c}"></button>`).join('') +
  '<input type="color" id="capColor" aria-label="Custom highlight color">';
function syncControls() {
  $$('#layoutSeg button').forEach(b => b.classList.toggle('on', b.dataset.v === opts.layout));
  $$('#shapeSeg button').forEach(b => b.classList.toggle('on', b.dataset.v === (opts.aspect || '9:16')));
  $('#camBlock').hidden = layoutOf() !== 'split';
  $('#shapeHint').textContent = opts.aspect === '16:9' && opts.layout === 'split' ? 'Cam + game needs a tall frame, so a wide clip uses the whole frame.' : '';
  setShapeCanvas();
  $('#camH').value = Math.round(opts.cam_height * 100); $('#camHOut').textContent = $('#camH').value + '%';
  $('#gameX').value = Math.round(opts.game_x * 100);
  $('#gameXOut').textContent = opts.game_x < .45 ? 'Left' : opts.game_x > .55 ? 'Right' : 'Center';
  $('#zoom').value = Math.round(opts.game_zoom * 100); $('#zoomOut').textContent = (+opts.game_zoom.toFixed(2)) + '×';
  $('#caps').checked = opts.captions; $('#capBlock').hidden = !opts.captions;
  $('#capPos').value = opts.caption_pos;
  $$('#swatches button').forEach(b => b.classList.toggle('on', b.dataset.c === opts.caption_color.toLowerCase()));
  $('#capColor').value = opts.caption_color;
  $('#capSize').value = opts.caption_size; $('#capSizeOut').textContent = opts.caption_size + '%';
  $('#wpl').value = opts.words_per_line; $('#wplOut').textContent = opts.words_per_line;
  $('#model').value = opts.whisper_model; $('#lang').value = opts.language; $('#upper').checked = opts.uppercase;
  $('#hw').checked = opts.hw_encode;
  $('#capAnim').checked = opts.caption_anim;
  $('#titleSize').value = opts.title_size; $('#titleSizeOut').textContent = opts.title_size + '%';
  $$('#titleStyles .tile').forEach(b => b.classList.toggle('on', b.dataset.v === opts.title_style));
  $$('#capStyles .tile').forEach(b => b.classList.toggle('on', b.dataset.v === opts.caption_style));
  $$('#titleFont button').forEach(b => b.classList.toggle('on', b.dataset.v === opts.title_font));
  $$('#capFont button').forEach(b => b.classList.toggle('on', b.dataset.v === opts.caption_font));
  $$('#capStyles .sw b').forEach(b => styleTileWord(b, b.closest('.tile').dataset.v));
  $$('#titleStyles .tile').forEach(b => styleTitleTile(b.querySelector('.sw span'), b.dataset.v));
  placeBox(); dirty = true; store.set('opts', opts);
  if (view === 'review' && !$('#pane-captions').hidden) renderCapEditor();
  if (W && W.kind === 'live' && W.live && W.live.auto_render) liveSettingsSoon();
}
let lsTimer = 0; function liveSettingsSoon() { clearTimeout(lsTimer); lsTimer = setTimeout(() => liveSettings({ opts: JSON.parse(JSON.stringify(opts)), model: opts.whisper_model, language: opts.language }), 600); }
$('#layoutSeg').onclick = e => { const v = e.target.closest('button')?.dataset.v; if (v) { opts.layout = v; syncControls(); } };
$('#shapeSeg').onclick = e => { const v = e.target.closest('button')?.dataset.v; if (v) { opts.aspect = v; hideExact(); syncControls(); } };
$('#camH').oninput = e => { opts.cam_height = e.target.value / 100; syncControls(); };
$('#gameX').oninput = e => { opts.game_x = e.target.value / 100; syncControls(); };
$('#zoom').oninput = e => { opts.game_zoom = e.target.value / 100; syncControls(); };
$('#caps').onchange = e => { opts.captions = e.target.checked; syncControls(); };
$('#capPos').onchange = e => { opts.caption_pos = e.target.value; syncControls(); };
$('#swatches').onclick = e => { const c = e.target.dataset.c; if (c) { opts.caption_color = c; syncControls(); } };
$('#capColor').oninput = e => { opts.caption_color = e.target.value; syncControls(); };
$('#capSize').oninput = e => { opts.caption_size = +e.target.value; syncControls(); };
$('#wpl').oninput = e => { opts.words_per_line = +e.target.value; syncControls(); };
$('#model').onchange = e => { opts.whisper_model = e.target.value; syncControls(); relisten(); };
$('#lang').onchange = e => { opts.language = e.target.value; syncControls(); relisten(); };
$('#upper').onchange = e => { opts.uppercase = e.target.checked; syncControls(); };
$('#hw').onchange = e => { opts.hw_encode = e.target.checked; syncControls(); };
function relisten() { if (W && W.kind === 'vod') { W.clips.forEach(c => { c.listened = false; }); listen(); } }


/* --- Text tab: title and caption styles, fonts --- */
const TITLE_STYLES = [['box', 'White box'], ['dark', 'Dark box'], ['accent', 'Color box'], ['outline', 'Outline']];
const CAP_STYLES = [['pop', 'Pop'], ['box', 'Highlight'], ['karaoke', 'Karaoke'], ['glow', 'Glow'], ['clean', 'Clean']];
function styleTitleTile(el, v) {
  Object.assign(el.style, { padding: '3px 6px', borderRadius: '2px', background: 'none', color: '#fff', webkitTextStroke: '0', textShadow: 'none' });
  if (v === 'box') Object.assign(el.style, { background: '#fff', color: '#141414' });
  if (v === 'dark') Object.assign(el.style, { background: 'rgba(20,20,20,.92)' });
  if (v === 'accent') Object.assign(el.style, { background: opts.caption_color, color: '#141414' });
  if (v === 'outline') Object.assign(el.style, { textShadow: '0 0 2px #000,0 0 2px #000,0 0 2px #000' });
}
function styleTileWord(b, v) {
  const hi = opts.caption_color, sw = b.parentElement;
  sw.style.textShadow = v === 'clean' ? '1px 2px 3px rgba(0,0,0,.7)' : v === 'glow' ? `0 0 6px ${hi},0 0 2px ${hi}` : '0 0 2px #000,0 0 2px #000';
  Object.assign(b.style, { background: 'none', color: '#fff', textShadow: '' });
  if (v === 'pop' || v === 'karaoke') b.style.color = hi;
  if (v === 'box') Object.assign(b.style, { background: hi, color: '#141414', textShadow: 'none' });
  if (v === 'glow') b.style.fontSize = '15px';
  sw.querySelectorAll('i').forEach(i => { i.style.color = v === 'karaoke' ? hi : '#fff'; });
}
$('#titleStyles').innerHTML = TITLE_STYLES.map(([v, l]) => `<button class="tile" data-v="${v}"><span class="sw"><span>Title</span></span>${l}</button>`).join('');
$('#capStyles').innerHTML = CAP_STYLES.map(([v, l]) => `<button class="tile" data-v="${v}"><span class="sw"><i>HI</i><b>YOU</b></span>${l}</button>`).join('');
const fontButtons = () => Object.entries(FONTS).map(([k, f]) => `<button data-v="${k}" style="font:${fontCss(k, ({ montserrat: 9.5, luckiest: 11, poppins: 12, classic: 11.5 })[k] || 13 * f.scale)}" title="${f.label}">${f.label}</button>`).join('');
$('#titleFont').innerHTML = fontButtons(); $('#capFont').innerHTML = fontButtons();
$('#titleStyles').onclick = e => { const b = e.target.closest('.tile'); if (b) { opts.title_style = b.dataset.v; syncControls(); } };
$('#capStyles').onclick = e => { const b = e.target.closest('.tile'); if (b) { opts.caption_style = b.dataset.v; syncControls(); } };
$('#titleFont').onclick = e => { const b = e.target.closest('button'); if (b) { opts.title_font = b.dataset.v; syncControls(); } };
$('#capFont').onclick = e => { const b = e.target.closest('button'); if (b) { opts.caption_font = b.dataset.v; syncControls(); } };
$('#titleSize').oninput = e => { opts.title_size = +e.target.value; syncControls(); };
$('#capAnim').onchange = e => { opts.caption_anim = e.target.checked; syncControls(); };
$('#resetPos').onclick = () => { Object.assign(opts, { title_x: DEFAULTS.title_x, title_y: DEFAULTS.title_y, cap_x: .5, cap_y: .7, caption_pos: 'auto' }); syncControls(); toast('Title and captions are back in their usual places.', '', 2500); };

/* --- Captions tab: fix what Whisper heard --- */
let capRows = [], capActive = -1, capGroup = Date.now() % 1e6;
const fmtT = t => { t = Math.max(0, t); const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s < 10 ? '0' : ''}${s.toFixed(1)}`; };
function renderCapEditor() {
  const box = $('#capLines'), c = mode === 'clip' ? curClip() : null;
  capActive = -1;
  if (!c) { capRows = []; box.innerHTML = '<div class="cap-empty">Pick a clip to see its captions.</div>'; $('#capAdd').disabled = true; $('#capReset').hidden = true; return; }
  const kept = c.state === 'kept';
  $('#capAdd').disabled = kept || !opts.captions;
  $('#capReset').hidden = !c.wordsEdited || kept;
  capRows = chunksFor(c);
  if (!opts.captions) { box.innerHTML = '<div class="cap-empty">Captions are turned off. Turn them on in the Text tab.</div>'; return; }
  if (!capRows.length) {
    box.innerHTML = `<div class="cap-empty">${!c.listened && status.whisper ? 'Listening to this clip… its captions will show up here in a moment.'
      : status.whisper ? 'No speech found in this clip. Use Add caption to write your own.' : 'Captions need Whisper, which isn\'t available in this install. You can still add captions by hand.'}</div>`;
    return;
  }
  const flags = (c.safety && c.safety.flags || []).filter(f => f.source === 'speech');
  const flagFor = r => flags.find(f => f.start + c.start < r.end && f.end + c.start > r.start);
  box.innerHTML = capRows.map((r, i) => { const f = flagFor(r); return `<div class="cline${r.words.some(w => w.edited) ? ' edited' : ''}${f ? ' flag' : ''}" data-i="${i}" ${f ? `title="Content check: ${esc(f.label)}"` : ''}>
      <span class="ct tnum" title="Jump here">${fmtT(r.start - c.start)}</span>
      <input data-i="${i}" value="${esc(r.words.map(w => w.word).join(' '))}" spellcheck="true" ${kept ? 'disabled' : ''} aria-label="Caption at ${fmtT(r.start - c.start)}">
      <button class="del" data-del="${i}" title="Delete this caption" ${kept ? 'hidden' : ''}><svg class="ic"><use href="#i-x"/></svg></button></div>`; }).join('');
  syncCapActive(true);
}
function editWords(c) {
  // first edit: keep the original transcript so the edits can be undone
  if (!c.wordsEdited) { c.wordsOrig = c.words.map(w => ({ ...w })); c.words = c.words.map(w => ({ ...w })); c.wordsEdited = true; }
}
function rescanSoon(c) {
  saveClip(c, true);
  if (safetyCfg.enabled === false) return;
  if (safetyCfg.auto_recheck === false) {
    // checking again after edits is switched off: just say the last check is out of date
    if (c.safety) { c.check = Object.assign({}, c.check, { stale: true }); if (curClip() === c) renderCheck(c); }
    return;
  }
  clearTimeout(c._scanT);
  c._scanT = setTimeout(async () => {
    let r;
    try { r = await api('/api/check', Object.assign(clipSnapshot(c), { action: '' })); } catch { return; }
    const v = r.clips && r.clips[0]; if (!v) return;
    c.safety = v.safety; c.check = v.check;
    if (curClip() === c) { renderCheck(c); if (!$('#pane-captions').hidden && !$('#capLines').contains(document.activeElement)) renderCapEditor(); dirty = true; }
    renderRows();
  }, 350);
}
function applyCapEdit(i, text) {
  const c = curClip(); if (!c || c.state === 'kept') return;
  const before = snapEdit(c);
  editWords(c);
  const row = chunksFor(c)[i]; if (!row) return;
  const toks = text.trim().split(/\s+/).filter(Boolean);
  const g = ++capGroup;
  let fresh;
  if (toks.length === row.words.length) fresh = row.words.map((w, j) => ({ ...w, word: toks[j], g, edited: w.edited || toks[j] !== w.word }));
  else {
    // spread the new words over the line's own time, without running into the next line
    const after = c.words.find(w => w.start > row.words[row.words.length - 1].start && !row.words.includes(w));
    const a = row.words[0].start;
    let b = Math.max(row.words[row.words.length - 1].end, row.end);
    if (after) b = Math.min(b, after.start - 0.01);
    b = Math.max(b, a + 0.1);
    const d = (b - a) / Math.max(1, toks.length);
    fresh = toks.map((t, j) => ({ start: +(a + j * d).toFixed(3), end: +(a + (j + 1) * d - 0.01).toFixed(3), word: t, g, edited: true }));
  }
  c.words = c.words.filter(w => !row.words.includes(w)).concat(fresh).sort((x, y) => x.start - y.start);
  pushEdit(c, text.trim() ? 'caption edit' : 'caption deleted', before);
  renderCapEditor(); dirty = true; edDirty = true; rescanSoon(c);
}
function seekClip(t) { const c = curClip(); if (!c) return; vod.pause(); try { vod.currentTime = clamp(t, c.start, c.end - 0.05); } catch {} dirty = true; }
function syncCapActive(force) {
  if (!capRows.length) return;
  const t = vod.currentTime, i = capRows.findIndex(r => t >= r.start && t < r.end);
  if (i === capActive && !force) return;
  capActive = i;
  $$('#capLines .cline').forEach(el => el.classList.toggle('on', +el.dataset.i === i));
  const el = $(`#capLines .cline[data-i="${i}"]`);
  if (el && !$('#capLines').contains(document.activeElement)) el.scrollIntoView({ block: 'nearest' });
}
$('#capLines').addEventListener('focusin', e => { const inp = e.target.closest('input'); if (inp && capRows[+inp.dataset.i]) seekClip(capRows[+inp.dataset.i].start + 0.01); });
$('#capLines').addEventListener('click', e => {
  const d = e.target.closest('[data-del]'); if (d) { applyCapEdit(+d.dataset.del, ''); return; }
  const r = e.target.closest('.cline'); if (r && !e.target.closest('input')) seekClip(capRows[+r.dataset.i].start + 0.01);
});
$('#capLines').addEventListener('keydown', e => {
  const inp = e.target.closest('input'); if (!inp) return;
  if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
  else if (e.key === 'Escape') { inp.value = capRows[+inp.dataset.i].words.map(w => w.word).join(' '); inp.blur(); }
});
$('#capLines').addEventListener('change', e => {
  const inp = e.target.closest('input'); if (!inp) return;
  const row = capRows[+inp.dataset.i]; if (!row || inp.value.trim() === row.words.map(w => w.word).join(' ')) return;
  applyCapEdit(+inp.dataset.i, inp.value);
});
$('#capAdd').onclick = () => {
  const c = curClip(); if (!c || c.state === 'kept') return;
  const before = snapEdit(c);
  editWords(c);
  let t = clamp(vod.currentTime, c.start, c.end - 1);
  const inRow = chunksFor(c).find(r => t >= r.start && t < r.end); if (inRow) t = Math.min(inRow.end, c.end - 0.4);
  const nextW = c.words.find(w => w.start > t + 0.01);
  const len = Math.max(0.4, Math.min(1.2, (nextW ? nextW.start - 0.01 : c.end) - t)), g = ++capGroup;
  const nw = [{ start: +t.toFixed(3), end: +(t + len / 2).toFixed(3), word: 'New', g, edited: true }, { start: +(t + len / 2).toFixed(3), end: +(t + len).toFixed(3), word: 'caption', g, edited: true }];
  c.words = c.words.concat(nw).sort((x, y) => x.start - y.start);
  pushEdit(c, 'caption added', before);
  renderCapEditor(); dirty = true; edDirty = true; rescanSoon(c);
  const i = capRows.findIndex(r => r.words.includes(c.words.find(w => w === nw[0] || (w.start === nw[0].start && w.word === 'New'))));
  const inp = $(`#capLines input[data-i="${i}"]`); if (inp) { inp.focus(); inp.select(); }
};
$('#capReset').onclick = () => {
  const c = curClip(); if (!c || !c.wordsEdited) return;
  record(c, 'caption reset', () => { c.words = c.wordsOrig || []; c.wordsEdited = false; delete c.wordsOrig; });
  renderCapEditor(); dirty = true; rescanSoon(c); toast('Captions are back to what Whisper heard.', '', 2500);
};

const frame = $('#frame'), box = $('#box'), picker = $('#picker');
let frameKey = '';
function loadFrame(pid, t) {
  if (!pid) return;
  t = Math.round(t); const k = pid + ':' + t;
  if (k === frameKey) return; frameKey = k; frame.src = `/api/frame/${pid}?t=${t}`;
}
frame.onload = () => { placeBox(); dirty = true; };
function placeBox() { const [x, y, w, h] = opts.cam; Object.assign(box.style, { left: x * 100 + '%', top: y * 100 + '%', width: w * 100 + '%', height: h * 100 + '%' }); }
let drag = null;
const rel = e => { const r = picker.getBoundingClientRect();
  return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
picker.addEventListener('pointerdown', e => { const [x, y] = rel(e); drag = { x, y, prev: opts.cam.slice() }; picker.setPointerCapture(e.pointerId); });
picker.addEventListener('pointermove', e => {
  if (!drag) return;
  const [x, y] = rel(e);
  opts.cam = [Math.min(drag.x, x), Math.min(drag.y, y), Math.abs(x - drag.x), Math.abs(y - drag.y)];
  placeBox(); dirty = true;
});
picker.addEventListener('pointerup', () => {
  if (!drag) return;
  if (opts.cam[2] < 0.03 || opts.cam[3] < 0.03) opts.cam = drag.prev;
  drag = null; syncControls();
});

/* =============================================================== exports + TikTok */
let queueEpoch = 0;     // bumps whenever the UI changes the queue, so a stale refresh can't undo it
async function refreshQueue() {
  const epoch = queueEpoch;
  let items;
  try { items = (await api('/api/queue')).items; } catch { return; }
  if (epoch !== queueEpoch) return refreshQueue();
  queueItems = items;
  if (W) {
    for (const c of W.clips) {
      const q = queueFor(c);
      if (q && c.state !== 'kept') { c.state = 'kept'; saveClip(c); }
      else if (!q && c.state === 'kept') { c.state = 'todo'; saveClip(c); }
    }
    renderRows(); if (mode === 'clip') fillInspector();
  }
  renderExports(); updateStatusBar();
}
setInterval(() => {
  const active = queueItems.some(q => q.status === 'queued' || q.status === 'rendering' || (q.tiktok && q.tiktok.state === 'posting'));
  if (active || view === 'exports' || (W && W.kind === 'live' && W.live && W.live.auto_render)) refreshQueue();
}, 1500);

function renderExports() {
  const items = queueItems;
  const done = items.filter(q => q.status === 'done').length, active = items.filter(q => q.status === 'queued' || q.status === 'rendering').length;
  const failed = items.filter(q => q.status === 'error').length;
  $('#exSum').textContent = items.length ? `${done} ready${active ? `, ${active} rendering` : ''}${failed ? `, ${failed} failed` : ''}` : '';
  $('#exRetryAll').hidden = !failed; $('#exRetryAll span').textContent = failed > 1 ? `Retry ${failed} failed` : 'Retry failed';
  $('#exEmpty').hidden = !!items.length;
  $('#exTable').hidden = !items.length;
  patchList($('#exTable tbody'), items.slice().reverse().map(q => {
    let st;
    if (q.status === 'done') st = `<span class="st-done">${icon('check')}Ready</span>`;
    else if (q.status === 'rendering') st = `<span>${esc(q.step || 'Rendering')} ${Math.round(q.progress * 100)}%</span><span class="meter"><i style="width:${Math.round(q.progress * 100)}%"></i></span>`;
    else if (q.status === 'error') st = `<span class="st-err">${icon('alert')}Failed</span><span class="err-why" title="${esc(q.error)}">${esc(q.error)}</span><button class="btn sm retry" data-retry="${q.id}">${icon('refresh')}Retry</button>`;
    else st = '<span class="st-wait">Waiting</span>';
    const t = q.tiktok;
    const tt = !t ? '<span class="tt-cell">Not posted</span>'
      : t.state === 'posting' ? `<span class="tt-cell">${t.auto ? 'Sending to drafts…' : 'Uploading…'}</span>`
      : t.state === 'error' ? '<span class="tt-cell err">Failed</span>'
      : `<span class="tt-cell ok">${t.mode === 'draft' ? 'In TikTok drafts' : 'Posted'}</span>`;
    return `<tr data-id="${q.id}" class="${q.id === exSel ? 'sel' : ''}">
      <td><span class="vt" style="display:block"><img src="${q.thumb}" alt=""></span></td>
      <td><div class="tt1">${esc(q.title || 'Clip ' + q.rank)}</div><div class="tt2">${esc(q.source_title || '')}</div></td>
      <td class="r tnum">${fmt(q.duration)}</td>
      <td><div class="st-cell">${st}</div></td>
      <td>${tt}</td>
      <td><button class="ibtn row-x" data-x="${q.id}" title="${q.status === 'done' ? 'Remove from list (the file stays)' : q.status === 'error' ? 'Remove' : 'Cancel'}">${icon('x')}</button></td></tr>`;
  }).join(''), 'id');
  if (exSel && !items.some(q => q.id === exSel)) { exSel = null; }
  renderDetail(false);
}
$('#exRetryAll').onclick = () => retryRender('all');
$('#exRetry').onclick = () => { if (exSel) retryRender(exSel); };
$('#exRemove').onclick = async () => { if (!exSel) return; await api('/api/queue/remove', { id: exSel }).catch(() => {}); exSel = null; refreshQueue(); };
$('#exTable').addEventListener('click', async e => {
  const r = e.target.closest('[data-retry]');
  if (r) { e.stopPropagation(); retryRender(r.dataset.retry); return; }
  const x = e.target.closest('[data-x]');
  if (x) {
    e.stopPropagation();
    await api('/api/queue/remove', { id: x.dataset.x }).catch(() => {});
    if (exSel === x.dataset.x) exSel = null;
    refreshQueue(); return;
  }
  const tr = e.target.closest('tr[data-id]'); if (!tr) return;
  exSel = tr.dataset.id; renderExports(); renderDetail(true);
});
$('#exFolder').onclick = () => {
  const q = queueItems.find(x => x.id === exSel && x.folder) || queueItems.find(x => x.folder);
  api('/api/open_folder', { folder: q ? q.folder : 'root' }).catch(e => toast(e.message, 'err'));
};

let shownItem = '', shownState = '';
function renderDetail(force) {
  const q = queueItems.find(x => x.id === exSel);
  $('#exNone').hidden = !!q; $('#exBody').hidden = !q;
  if (!q) { shownItem = ''; $('#exVideo').removeAttribute('src'); return; }
  const stateKey = q.status + (q.url || '') + JSON.stringify(q.tiktok || {});
  if (!force && shownItem === q.id && shownState === stateKey) return;
  const newItem = shownItem !== q.id;
  shownItem = q.id; shownState = stateKey;
  $('#exTitle').textContent = q.title || 'Clip ' + q.rank;
  $('#exFile').textContent = q.name || (q.status === 'error' ? 'Not rendered' : q.status === 'queued' ? 'Waiting to render' : 'Rendering…');
  $('#exErrBox').hidden = q.status !== 'error';
  if (q.status === 'error') {
    $('#exErrMsg').textContent = q.error; $('#exErrDetail').textContent = q.detail || q.error;
    $('#exErrMore').hidden = !q.detail || q.detail === q.error;
  }
  const sf = q.safety;
  $('#exSafe').innerHTML = !sf ? '' : !sf.flags.length ? `<span class="safe ok">${icon('check')}Content check: nothing flagged</span>`
    : `<span class="safe ${sf.level}">${icon('alert')}Content check: ${esc([...new Set(sf.flags.map(f => f.label))].join(', '))}</span>${q.censored ? ` · ${plural(q.censored, 'word')} censored` : ''}`;
  $('#exFile').title = q.name || '';
  $('#exShow').disabled = $('#exCopy').disabled = q.status !== 'done';
  const v = $('#exVideo');
  if (q.status === 'done') { if (v.dataset.src !== q.url) { v.dataset.src = q.url; v.src = q.url; } }
  else { v.dataset.src = ''; v.removeAttribute('src'); }
  v.controls = q.status === 'done';
  $('#ttPanel').hidden = q.status !== 'done';
  if (q.status === 'done' && (newItem || force)) prepareTikTok(q);
  else if (q.status === 'done') renderTTStatus(q);
}
$('#exShow').onclick = () => { const q = queueItems.find(x => x.id === exSel); if (q) api('/api/open_folder', { folder: q.folder }).catch(e => toast(e.message, 'err')); };
$('#exCopy').onclick = () => { const q = queueItems.find(x => x.id === exSel); if (q) copyText(q.post || q.title || ''); };

let creator = null;
async function prepareTikTok(q) {
  $('#ttErr').textContent = '';
  try { ttStatus = await api('/api/tiktok'); } catch { ttStatus = {}; }
  const connected = ttStatus.connected;
  $('#ttConnect').hidden = connected; $('#ttForm').hidden = true; $('#ttLoading').hidden = !connected;
  $('#ttWho').textContent = connected ? (ttStatus.user.nickname || ttStatus.user.username || '') : '';
  renderTTStatus(q);
  if (!connected) return;
  // TikTok asks apps to fetch the latest creator settings every time the post screen opens
  try { creator = await api('/api/tiktok/creator', {}); }
  catch (e) { $('#ttLoading').hidden = true; $('#ttErr').textContent = e.message; $('#ttForm').hidden = false; $('#ttPost').disabled = $('#ttDraft').disabled = true; return; }
  $('#ttLoading').hidden = true; $('#ttForm').hidden = false;
  $('#ttWho').textContent = `Posting as ${creator.creator_nickname || creator.creator_username}`;
  $('#ttCaption').value = q.post || q.title || '';
  const sel = $('#ttPrivacy');
  sel.innerHTML = '<option value="" disabled selected>Choose…</option>' +
    (creator.privacy_level_options || []).map(p => `<option value="${p}">${PRIVACY[p] || p}</option>`).join('');
  [['#ttComment', 'comment_disabled'], ['#ttDuet', 'duet_disabled'], ['#ttStitch', 'stitch_disabled']].forEach(([id, k]) => {
    $(id).checked = false; $(id).disabled = !!creator[k];
    $(id).closest('.chk').title = creator[k] ? 'Turned off in your TikTok privacy settings' : '';
  });
  $('#ttDisclose').checked = false; $('#ttYourBrand').checked = false; $('#ttBranded').checked = false;
  syncTT();
}
function syncTT() {
  const q = queueItems.find(x => x.id === exSel);
  const disclose = $('#ttDisclose').checked, yours = $('#ttYourBrand').checked, branded = $('#ttBranded').checked;
  $('#ttDiscloseOpts').hidden = !disclose;
  // branded content can't be private
  const priv = [...$('#ttPrivacy').options].find(o => o.value === 'SELF_ONLY');
  if (priv) { priv.disabled = disclose && branded; priv.title = priv.disabled ? 'Branded content visibility cannot be set to private.' : ''; }
  if (disclose && branded && $('#ttPrivacy').value === 'SELF_ONLY') { $('#ttPrivacy').value = ''; toast('Branded content can\'t be private, so pick who can view it.', '', 4000); }
  $('#ttLabelHint').textContent = !disclose ? '' : branded ? 'Your video will be labeled "Paid partnership".' : yours ? 'Your video will be labeled "Promotional content".' : '';
  const music = '<a href="#" data-url="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en">Music Usage Confirmation</a>';
  const bc = '<a href="#" data-url="https://www.tiktok.com/legal/page/global/bc-policy/en">Branded Content Policy</a>';
  $('#ttDeclare').innerHTML = disclose && branded ? `By posting, you agree to TikTok's ${bc} and ${music}.` : `By posting, you agree to TikTok's ${music}.`;
  const tooLong = q && creator && creator.max_video_post_duration_sec && q.duration > creator.max_video_post_duration_sec;
  const needChoice = disclose && !yours && !branded;
  $('#ttPost').disabled = !$('#ttPrivacy').value || needChoice || tooLong || isPosting(q);
  $('#ttPost').title = needChoice ? 'You need to indicate if your content promotes yourself, a third party, or both.' : !$('#ttPrivacy').value ? 'Choose who can view this video first.' : '';
  $('#ttDraft').disabled = tooLong || isPosting(q);
  $('#ttErr').textContent = tooLong ? `This clip is longer than TikTok allows for your account (${fmt(creator.max_video_post_duration_sec)}).` : '';
  const n = $('#ttCaption').value.length; $('#ttCount').textContent = `${n} / 2200`;
}
const isPosting = q => !!(q && q.tiktok && q.tiktok.state === 'posting');
['#ttPrivacy', '#ttDisclose', '#ttYourBrand', '#ttBranded'].forEach(id => $(id).addEventListener('change', syncTT));
$('#ttCaption').addEventListener('input', syncTT);
$('#ttGoSettings').onclick = () => setView('settings');
function renderTTStatus(q) {
  const t = q.tiktok, el = $('#ttStatus');
  if (!t) { el.hidden = true; return; }
  el.hidden = false;
  if (t.state === 'posting') el.innerHTML = `<span id="ttProg">Uploading to TikTok…</span><span class="meter"><i id="ttBar"></i></span>`;
  else if (t.state === 'error') el.innerHTML = `<span class="err">${esc(t.error)}</span>`;
  else if (t.mode === 'draft') el.innerHTML = `<span class="ok">${icon('check')}Sent to your TikTok inbox.</span><span class="muted">Open TikTok on your phone and tap the notification to finish posting.</span>`;
  else el.innerHTML = `<span class="ok">${icon('check')}Posted to TikTok.</span><span class="muted">TikTok can take a few minutes to process it before it shows on your profile.</span>` +
    (t.url ? `<a href="#" data-url="${esc(t.url)}">${icon('link')} Open on TikTok</a>` : '');
}
async function postTikTok(modeName) {
  const q = queueItems.find(x => x.id === exSel); if (!q) return;
  const body = { item: q.id, mode: modeName, title: $('#ttCaption').value, privacy: $('#ttPrivacy').value,
    allow_comment: $('#ttComment').checked, allow_duet: $('#ttDuet').checked, allow_stitch: $('#ttStitch').checked,
    brand_organic: $('#ttDisclose').checked && $('#ttYourBrand').checked, brand_content: $('#ttDisclose').checked && $('#ttBranded').checked };
  $('#ttErr').textContent = '';
  let job;
  try { job = (await api('/api/tiktok/post', body)).job; } catch (e) { $('#ttErr').textContent = e.message; return; }
  q.tiktok = { state: 'posting' }; renderTTStatus(q); syncTT(); renderExports();
  const j = await waitJob(job, j => { if ($('#ttProg')) { $('#ttProg').textContent = j.step; $('#ttBar').style.width = Math.round(j.progress * 100) + '%'; } updateStatusBar(); });
  await refreshQueue();
  const q2 = queueItems.find(x => x.id === q.id);
  if (q2) renderTTStatus(q2);
  if (j && j.status === 'done') toast(modeName === 'draft' ? 'Sent to your TikTok drafts.' : 'Posted to TikTok.');
  else if (j && j.error) $('#ttErr').textContent = j.error;
  syncTT(); updateStatusBar();
}
$('#ttPost').onclick = () => postTikTok('direct');
$('#ttDraft').onclick = () => postTikTok('draft');

/* =============================================================== settings */
async function loadTikTok() {
  try { ttStatus = await api('/api/tiktok'); } catch { return; }
  const s = ttStatus;
  $('#ttRedirect').textContent = s.redirect_uri;
  $('#ttDot').className = 'status-dot ' + (s.connected ? 'on' : s.configured ? 'warn' : '');
  $('#ttState').textContent = s.connected ? 'Connected' : s.configured ? 'Not signed in' : 'Not set up';
  $('#ttAccount').hidden = !s.connected; $('#ttSetup').hidden = s.connected;
  if (s.connected) { $('#ttNick').textContent = s.user.nickname || s.user.username || 'your account'; $('#ttUser').textContent = s.user.username ? '@' + s.user.username : ''; }
  if (s.client_key && !$('#ttKey').value) $('#ttKey').value = s.client_key;
}
$('#ttCopyRedirect').onclick = () => copyText($('#ttRedirect').textContent, 'Redirect URI');
$('#ttConnectBtn').onclick = async () => {
  const key = $('#ttKey').value.trim(), secret = $('#ttSecret').value.trim();
  try {
    if (key && (secret || key !== ttStatus.client_key)) await api('/api/tiktok/config', { client_key: key, client_secret: secret });
    else if (!ttStatus.configured) throw new Error('Enter your app\'s Client key and Client secret.');
    await api('/api/tiktok/connect', { open: true });
    $('#ttConnectMsg').textContent = 'Finish signing in in your browser…';
    $('#ttSecret').value = '';
    const end = Date.now() + 5 * 60e3;
    while (Date.now() < end) {
      await new Promise(r => setTimeout(r, 2000));
      const s = await api('/api/tiktok').catch(() => ({}));
      if (s.connected) { $('#ttConnectMsg').textContent = ''; toast('TikTok connected.'); loadTikTok(); return; }
    }
    $('#ttConnectMsg').textContent = 'Sign-in timed out. Try again.';
  } catch (e) { $('#ttConnectMsg').textContent = ''; toast(e.message, 'err', 7000); }
};
$('#ttDisconnect').onclick = async () => {
  if (!await ask({ title: 'Disconnect TikTok?', body: 'VodClipper will sign out of your TikTok account. You can connect again any time.', ok: 'Disconnect' })) return;
  await api('/api/tiktok/disconnect', {}).catch(() => {}); loadTikTok();
};

function renderTools() {
  const rows = [['FFmpeg', status.ffmpeg, 'Cuts and renders video', 'ffmpeg'], ['yt-dlp', status.ytdlp, 'Downloads Twitch VODs and live streams', 'ytdlp'],
    ['TwitchDownloader', status.twitchdownloader, 'Gets chat replay for VODs', 'twitchdownloader'], ['Whisper', status.whisper, 'Captions, titles and Deep scan', ''],
    ['Laughter detector', status.laughter, 'Hears laughter, screams and cheering', 'laughter']];
  $('#toolTable tbody').innerHTML = rows.map(([n, ok, d, k]) => `<tr><td><span class="row"><span class="status-dot ${ok ? 'on' : 'warn'}"></span>${n}</span></td>
    <td class="muted">${ok ? d : 'Not installed'}</td><td class="r">${!ok && k && status.can_download_tools ? `<button class="btn sm" data-get="${k}">Download</button>` : ''}</td></tr>`).join('');
  $('#updYtdlp').disabled = !status.can_download_tools;
  $('#rootPath').textContent = status.workroot || ''; $('#rootPath').title = status.workroot || '';
}
$('#toolTable').onclick = e => { const b = e.target.closest('[data-get]'); if (b) runSetup([b.dataset.get]); };
$('#updYtdlp').onclick = () => runSetup(['ytdlp']);
$('#openRoot').onclick = () => api('/api/open_folder', { folder: 'root' }).catch(e => toast(e.message, 'err'));

let storage = null;
async function loadStorage() {
  try { storage = await api('/api/storage'); } catch { return; }
  const v = storage.vods, c = storage.clips;
  $('#storeRows').innerHTML = `
    <div class="store-row"><div>Downloaded VODs and live recordings<span class="tnum">${v.bytes ? plural(v.count, 'stream') + ', ' + fmtBytes(v.bytes) : 'None'}</span></div>
      <button class="btn danger" data-del="vods" ${v.bytes ? '' : 'disabled'}>${icon('trash')}Delete VODs</button></div>
    <div class="store-row"><div>Rendered clips<span class="tnum">${c.count ? plural(c.count, 'clip') + ', ' + fmtBytes(c.bytes) : 'None'}</span></div>
      <button class="btn danger" data-del="clips" ${c.count ? '' : 'disabled'}>${icon('trash')}Delete all clips</button></div>`;
  $('#uninstallBtn').hidden = !storage.uninstaller;
}
$('#storeRows').onclick = e => { const b = e.target.closest('[data-del]'); if (b && !b.disabled) deleteStuff(b.dataset.del); };
$('#uninstallBtn').onclick = async () => {
  if (!await ask({ title: 'Uninstall VodClipper?', ok: 'Uninstall', danger: true, body: 'VodClipper will close and the Windows uninstaller will open. It asks before deleting your VODs and clips.' })) return;
  try { await api('/api/uninstall', {}); document.body.innerHTML = '<div style="height:100vh;display:grid;place-items:center;color:#a3a6ae;font:14px Segoe UI,system-ui">Uninstalling VodClipper. Follow the uninstaller window.</div>'; }
  catch (e) { toast(e.message, 'err', 7000); }
};
async function deleteStuff(what) {
  const v = storage.vods, c = storage.clips;
  const ok = what === 'vods'
    ? await ask({ title: `Delete ${plural(v.count, 'downloaded stream')}?`, ok: 'Delete VODs', danger: true,
        body: `Frees ${fmtBytes(v.bytes)}. Rendered clips are kept, and so are any videos of your own that you opened with Browse. You can download a VOD again from its link.` })
    : await ask({ title: c.count === 1 ? 'Delete your clip?' : `Delete all ${c.count} clips?`, ok: 'Delete clips', danger: true,
        body: `Frees ${fmtBytes(c.bytes)}. Every rendered clip in ${storage.workroot} is deleted. This can't be undone. Downloaded VODs are kept.` });
  if (!ok) return;
  if (what === 'vods') { vod.pause(); vod.removeAttribute('src'); vod.dataset.src = ''; vod.load(); }
  const ev = $('#exVideo'); ev.pause(); ev.removeAttribute('src'); ev.dataset.src = ''; ev.load();
  await new Promise(r => setTimeout(r, 250));
  let res;
  try { res = await api('/api/delete', { what }); }
  catch (e) { toast(e.message, 'err', 7000); return; }
  if (W && what === 'vods' && (W.kind === 'live' ? W.clips.every(x => res.closed.includes(x.pid)) && W.clips.length : res.closed.includes(W.pid))) {
    W = null; cur = null; updateCrumb(); updateStatusBar();
  }
  const word = what === 'vods' ? 'stream' : 'clip';
  toast(res.failed.length ? `Deleted ${plural(res.deleted, word)}. ${res.failed.length} file(s) were in use; close any player showing them and try again.`
                          : `Deleted ${plural(res.deleted, word)} and freed ${fmtBytes(res.freed)}.`, '', 6000);
  await Promise.all([loadStorage(), loadRecent(), refreshQueue()]);
}

/* =============================================================== updates */
let upd = {}, updTimer = null, updAnnounced = '';
async function loadUpdate() {
  clearTimeout(updTimer);
  try { upd = await api('/api/update'); } catch { updTimer = setTimeout(loadUpdate, 30000); return; }
  renderUpdate();
  const fast = ['checking', 'downloading', 'installing'].includes(upd.state);
  updTimer = setTimeout(loadUpdate, fast ? 700 : (!upd.checked && upd.auto ? 4000 : 60000));
}
function renderUpdate() {
  const u = upd;
  const st = $('#updState');
  let text, cls = '';
  if (u.state === 'checking') text = 'Checking for updates…';
  else if (u.state === 'downloading') { text = `Downloading version ${u.latest}… ${Math.round(u.progress * 100)}%`; cls = 'new'; }
  else if (u.state === 'installing') { text = `Installing version ${u.latest}. VodClipper will close and reopen.`; cls = 'new'; }
  else if (u.state === 'error') { text = u.error; cls = 'err'; }
  else if (u.available) { text = `Version ${u.latest} is available${u.state === 'ready' ? ' and downloaded' : ''}.`; cls = 'new'; }
  else if (u.checked) { text = `You're up to date. Checked ${ago(u.checked)}.`; cls = 'ok'; }
  else text = 'Not checked yet.';
  st.textContent = text; st.className = 'upd-state ' + cls;
  const busyU = ['checking', 'downloading', 'installing'].includes(u.state);
  $('#updCheck').disabled = busyU;
  const go = $('#updGo');
  go.hidden = !u.available;
  go.disabled = busyU;
  go.innerHTML = u.can_install ? (u.state === 'downloading' ? 'Downloading…' : u.state === 'installing' ? 'Installing…' : 'Update now')
                               : 'Download from GitHub';
  $('#updMeter').hidden = u.state !== 'downloading';
  $('#updBar').style.width = Math.round((u.progress || 0) * 100) + '%';
  $('#updNotesWrap').hidden = !(u.available && u.notes);
  $('#updNotesTitle').textContent = `What's new in ${u.latest}`;
  $('#updNotes').textContent = u.notes || '';
  $('#updAuto').checked = u.auto !== false;
  $('#updTokenState').textContent = u.token ? `· token ${u.token_hint} saved` : '';
  $('#updTokenClear').hidden = !u.token;
  if (u.token || /private/i.test(u.error || '')) $('#updTokenBox').open = true;
  const sb = $('#sbUpdate');
  sb.hidden = !u.available;
  sb.innerHTML = u.state === 'downloading' ? `<i></i>Downloading update ${Math.round(u.progress * 100)}%`
               : u.state === 'installing' ? '<i></i>Installing update…' : `<i></i>Update to ${esc(u.latest)}`;
  if (u.available && updAnnounced !== u.latest && !busyU) {
    updAnnounced = u.latest;
    toast(`VodClipper ${u.latest} is available. Open Settings to update.`, '', 7000);
  }
}
async function startUpdate() {
  if (!upd.can_install) return openUrl(upd.page);
  const ok = await ask({ title: `Update to VodClipper ${upd.latest}?`,
    body: 'VodClipper will download the update, close, install it and open again. Your clips, projects and settings are kept.',
    ok: 'Update now' });
  if (!ok) return;
  try { upd = await api('/api/update/install', {}); renderUpdate(); loadUpdate(); }
  catch (e) { toast(e.message, 'err', 8000); }
}
$('#updGo').onclick = startUpdate;
$('#sbUpdate').onclick = () => { setView('settings'); $('#setUpdates').scrollIntoView({ block: 'center', behavior: 'smooth' }); };
$('#updCheck').onclick = async () => {
  upd.state = 'checking'; renderUpdate();
  try { upd = await api('/api/update/check', {}); } catch (e) { toast(e.message, 'err'); }
  renderUpdate(); loadUpdate();
};
$('#updAuto').onchange = async e => {
  try { upd = await api('/api/update/settings', { auto: e.target.checked }); renderUpdate(); } catch (err) { toast(err.message, 'err'); }
};
$('#updPage').onclick = e => { e.preventDefault(); openUrl(upd.page); };

/* =============================================================== content check + hotkey settings */
const SEV = { 3: 'Serious', 2: 'Medium', 1: 'Mild' };
const CENSOR_HINT = {
  off: 'Clips are flagged in Review and Exports. Nothing is changed.',
  mask: 'Flagged words show as f*** in captions and titles. The audio is left alone.',
  mute: 'Flagged words are hidden in captions and silenced in the audio.',
  bleep: 'Flagged words are hidden in captions and bleeped in the audio.',
};
function renderSafetySettings() {
  const c = safetyCfg;
  $('#sfOn').checked = c.enabled !== false; $('#sfBody').hidden = c.enabled === false;
  $('#sfCats').innerHTML = (c.category_info || []).map(k => `<tr><td>${esc(k.label)}<span class="muted small"> · ${SEV[k.severity]}</span></td>
    <td class="c"><input type="checkbox" data-check="${k.key}" ${c.categories[k.key] ? 'checked' : ''}></td>
    <td class="c"><input type="checkbox" data-censor="${k.key}" ${(c.censor_categories || []).includes(k.key) ? 'checked' : ''} ${c.categories[k.key] ? '' : 'disabled'}></td></tr>`).join('');
  $$('#sfCensor button').forEach(b => b.classList.toggle('on', b.dataset.v === c.censor));
  $('#sfCensorHint').textContent = CENSOR_HINT[c.censor] || '';
  $('#sfHold').checked = c.hold !== false;
  $$('#sfSens button').forEach(b => b.classList.toggle('on', b.dataset.v === (c.sensitivity || 'all')));
  $('#sfSensHint').textContent = SENS_HINT[c.sensitivity || 'all'];
  $$('#sfNotify button').forEach(b => b.classList.toggle('on', b.dataset.v === (c.notify || 'now')));
  $$('#sfExport button').forEach(b => b.classList.toggle('on', b.dataset.v === (c.export_flagged || 'ask')));
  $('#sfAuto').checked = c.auto !== false; $('#sfRemember').checked = c.remember_dismissed !== false; $('#sfRecheck').checked = c.auto_recheck !== false;
  if (document.activeElement !== $('#sfCustom')) $('#sfCustom').value = (c.custom || []).join(', ');
}
async function loadSafety() { try { safetyCfg = await api('/api/safety'); renderSafetySettings(); dirty = true; } catch {} }
async function saveSafety(patch) {
  try { safetyCfg = await api('/api/safety', patch); renderSafetySettings(); dirty = true; refreshChecks(); } catch (e) { toast(e.message, 'err'); }
}
$('#sfOn').onchange = e => saveSafety({ enabled: e.target.checked });
$('#sfHold').onchange = e => saveSafety({ hold: e.target.checked });
const SENS_HINT = { all: 'Flags everything it knows, swearing included.', skip_mild: 'Leaves out swearing and gambling talk.', serious: 'Only slurs, self-harm or harassment, personal info and your own words.' };
const segSave = (id, key) => { $(id).onclick = e => { const v = e.target.closest('button')?.dataset.v; if (v) saveSafety({ [key]: v }).then(() => { if (W) { renderRows(); const c = curClip(); if (c && mode === 'clip') renderCheck(c); } }); }; };
segSave('#sfSens', 'sensitivity'); segSave('#sfNotify', 'notify'); segSave('#sfExport', 'export_flagged');
$('#sfAuto').onchange = e => saveSafety({ auto: e.target.checked });
$('#sfRemember').onchange = e => saveSafety({ remember_dismissed: e.target.checked });
$('#sfRecheck').onchange = e => saveSafety({ auto_recheck: e.target.checked });
$('#sfCensor').onclick = e => { const v = e.target.closest('button')?.dataset.v; if (v) saveSafety({ censor: v }); };
$('#sfCats').onchange = e => {
  const k = e.target.dataset.check || e.target.dataset.censor; if (!k) return;
  if (e.target.dataset.check) saveSafety({ categories: Object.assign({}, safetyCfg.categories, { [k]: e.target.checked }) });
  else {
    const set = new Set(safetyCfg.censor_categories || []); e.target.checked ? set.add(k) : set.delete(k);
    saveSafety({ censor_categories: [...set] });
  }
};
$('#sfCustom').addEventListener('change', e => saveSafety({ custom: e.target.value }));

function renderHotkey() {
  const h = status.hotkey || {};
  $('#hkKeys').textContent = h.keys || 'Ctrl+Alt+C';
  $('#hkOn').checked = h.enabled !== false; $('#hkOn').disabled = !h.supported;
  $('#hkState').textContent = !h.supported ? 'Only available in the Windows app.' : h.error ? h.error
    : h.registered ? 'Ready. Press it while live clipping is running.' : 'Off';
}
$('#hkOn').onchange = async e => { try { status.hotkey = await api('/api/hotkey', { enabled: e.target.checked }); renderHotkey(); syncLiveCard(); } catch (err) { toast(err.message, 'err'); } };

$('#updTokenSave').onclick = async () => {
  const token = $('#updToken').value.trim(); if (!token) { $('#updToken').focus(); return; }
  try { upd = await api('/api/update/token', { token }); $('#updToken').value = ''; renderUpdate(); toast(upd.error ? upd.error : 'Token saved. Update checks can see the private repository now.', upd.error ? 'err' : '', 6000); }
  catch (e) { toast(e.message, 'err', 6000); }
};
$('#updTokenClear').onclick = async () => { try { upd = await api('/api/update/token', { token: '' }); renderUpdate(); toast('Token removed.', '', 2500); } catch (e) { toast(e.message, 'err'); } };

/* =============================================================== Twitch sign-in + learning settings */
function renderTwitch() {
  const t = twStatus;
  $('#twDot').className = 'status-dot ' + (t.signed_in ? (t.error ? 'warn' : 'on') : '');
  $('#twState').innerHTML = t.signed_in ? `${esc(t.login || 'Signed in')}${t.turbo ? ' · <span class="tw-ok">Turbo</span>' : t.turbo === false ? ' · no Turbo' : ''}` : 'Not signed in';
  $('#twLogin').hidden = !!t.signed_in;
  $('#twLogin').disabled = !t.window_login;
  $('#twLogin').title = t.window_login ? 'Opens Twitch\'s own login page in a small window' : 'Needs the VodClipper app window; paste a token below instead';
  $('#twLogout').hidden = !t.signed_in;
  $('#twMsg').textContent = t.error ? t.error : t.signed_in && t.turbo === false ? 'Ads still play on channels you aren\'t subbed to; VodClipper leaves them out of the recording.' : '';
  if (!t.window_login && !t.signed_in) $('#twPasteBox').open = true;
}
$('#twLogin').onclick = async () => {
  let job;
  try { job = (await api('/api/twitch/login', {})).job; } catch (e) { toast(e.message, 'err', 7000); return; }
  $('#twMsg').textContent = 'Sign in in the Twitch window…';
  const j = await waitJob(job);
  if (j && j.status === 'done') { toast('Signed in to Twitch.'); }
  else if (j && j.error) { $('#twMsg').textContent = j.error; }
  loadTwitch();
};
$('#twLogout').onclick = async () => {
  if (!await ask({ title: 'Sign out of Twitch?', body: 'Live recordings will skip ads by leaving them out instead of getting the ad-free stream.', ok: 'Sign out' })) return;
  try { twStatus = await api('/api/twitch/logout', {}); renderTwitch(); syncLiveCard(); } catch (e) { toast(e.message, 'err'); }
};
$('#twTokenSave').onclick = async () => {
  const token = $('#twToken').value.trim(); if (!token) { $('#twToken').focus(); return; }
  $('#twMsg').textContent = 'Checking with Twitch…';
  try { twStatus = await api('/api/twitch/token', { token }); $('#twToken').value = ''; $('#twPasteBox').open = false; renderTwitch(); syncLiveCard(); toast(`Signed in to Twitch as ${twStatus.login}.`); }
  catch (e) { $('#twMsg').textContent = e.message; }
};

let lrState = {};
async function loadLearning() { try { lrState = await api('/api/learning'); renderLearning(); } catch {} }
function renderLearning() {
  const L = lrState;
  $('#lrOn').checked = L.enabled !== false; $('#lrBody').hidden = L.enabled === false;
  const rows = L.profiles || [];
  $('#lrTable tbody').innerHTML = rows.length ? rows.map(r => `<tr><td>${esc(r.streamer)}</td>
    <td class="muted">${plural(r.decisions, 'decision')}, ${r.kept} kept${r.decisions < L.min_decisions ? ` · starts after ${L.min_decisions}` : ''}</td>
    <td class="r"><button class="btn sm" data-forget="${esc(r.streamer)}">Forget</button></td></tr>`).join('')
    : '<tr><td class="muted">Nothing learned yet. Keep and skip a few suggested clips.</td></tr>';
  $('#lrForgetAll').hidden = !rows.length;
  $('#lrTotal').textContent = L.total ? `${plural(L.total, 'decision')} in total` : '';
}
$('#lrOn').onchange = async e => { try { lrState = await api('/api/learning', { enabled: e.target.checked }); renderLearning(); } catch (err) { toast(err.message, 'err'); } };
$('#lrTable').onclick = async e => {
  const b = e.target.closest('[data-forget]'); if (!b) return;
  try { lrState = await api('/api/learning/forget', { streamer: b.dataset.forget }); renderLearning(); toast(`Forgot what VodClipper learned for ${b.dataset.forget}.`, '', 3000); } catch (err) { toast(err.message, 'err'); }
};
$('#lrForgetAll').onclick = async () => {
  if (!await ask({ title: 'Forget everything learned?', body: 'Clip ranking goes back to the default for every streamer.', ok: 'Forget', danger: true })) return;
  try { lrState = await api('/api/learning/forget', { streamer: '' }); renderLearning(); } catch (e) { toast(e.message, 'err'); }
};

/* =============================================================== boot */
setInterval(() => { fetch('/api/ping').catch(() => {}); }, 60000);
setInterval(loadStatus, 5000);
loadStatus().then(() => { updateStatusBar(); });
/* =============================================================== editor (3.4): timeline, trims, undo/redo */
const MIN_LEN = 1;                       // shortest clip, seconds
const SHAPES = { '9:16': [1080, 1920], '1:1': [1080, 1080], '16:9': [1920, 1080] };
const OUT = () => SHAPES[opts.aspect] || SHAPES['9:16'];
const layoutOf = () => (opts.layout === 'split' && opts.aspect === '16:9') ? 'full' : opts.layout;
const fpsOf = c => (c && c.fps) || 30;
const fmtA = t => {                      // stream time with hundredths: 1:23:45.20
  t = Math.max(0, t || 0); const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60;
  const ss = (s < 10 ? '0' : '') + s.toFixed(2);
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
};
function setShapeCanvas() {
  const [ow, oh] = OUT(), scale = 960 / Math.max(ow, oh);
  const w = Math.round(ow * scale / 2) * 2, h = Math.round(oh * scale / 2) * 2;
  if (phone.width !== w || phone.height !== h) { phone.width = w; phone.height = h; }
  $('#phoneBox').style.aspectRatio = `${ow} / ${oh}`;
  sizePhone(); dirty = true;
}

/* --- undo / redo: every edit to a clip's cut, title, post caption and captions --- */
const EDIT_KEYS = ['start', 'end', 'userTrim', 'title', 'userTitle', 'postText', 'userPost', 'words', 'wordsEdited', 'wordsOrig'];
const hist = { undo: [], redo: [] };
const snapEdit = c => JSON.parse(JSON.stringify(Object.fromEntries(EDIT_KEYS.map(k => [k, c[k] === undefined ? null : c[k]]))));
function pushEdit(c, label, before, group) {
  const after = snapEdit(c);
  if (JSON.stringify(before) === JSON.stringify(after)) return;
  const last = hist.undo[hist.undo.length - 1], now = Date.now();
  if (group && last && last.group === group && last.key === keyOf(c) && now - last.t < 1500) { last.after = after; last.t = now; }
  else hist.undo.push({ key: keyOf(c), label, before, after, group, t: now });
  if (hist.undo.length > 200) hist.undo.shift();
  hist.redo = [];
  syncUndo();
}
function record(c, label, fn, group) { const before = snapEdit(c); fn(); pushEdit(c, label, before, group); }
function applyEdit(c, s) { EDIT_KEYS.forEach(k => { if (s[k] === null) delete c[k]; else c[k] = JSON.parse(JSON.stringify(s[k])); }); }
function syncUndo() {
  const u = hist.undo[hist.undo.length - 1], r = hist.redo[hist.redo.length - 1];
  $('#edUndo').disabled = !u; $('#edRedo').disabled = !r;
  $('#edUndo').title = u ? `Undo ${u.label} (Ctrl+Z)` : 'Nothing to undo';
  $('#edRedo').title = r ? `Redo ${r.label} (Ctrl+Shift+Z)` : 'Nothing to redo';
}
function undoRedo(redo) {
  const from = redo ? hist.redo : hist.undo, to = redo ? hist.undo : hist.redo;
  const e = from[from.length - 1]; if (!e) return;
  const c = byKey(e.key);
  if (!c) { from.pop(); syncUndo(); return; }
  if (c.state === 'kept') { toast('That clip is in the render queue. Remove it from the queue to change it.', 'err', 4000); return; }
  from.pop(); applyEdit(c, redo ? e.after : e.before); to.push(e);
  if (cur !== e.key) select(e.key, false); else { fillInspector(); renderRows(); }
  if (!$('#pane-captions').hidden) renderCapEditor();
  drawHeat(); dirty = true; edDirty = true; rescanSoon(c);
  toast(`${redo ? 'Redone' : 'Undone'}: ${e.label}`, '', 1600);
  syncUndo();
}
$('#edUndo').onclick = () => undoRedo(false);
$('#edRedo').onclick = () => undoRedo(true);

/* --- trimming --- */
function setBounds(c, start, end, label, group) {
  const dur = clipMaxEnd(c);
  start = clamp(start, 0, Math.max(0, dur - MIN_LEN)); end = clamp(end, start + MIN_LEN, dur);
  if (Math.abs(start - c.start) < 1e-4 && Math.abs(end - c.end) < 1e-4) return false;
  record(c, label, () => { c.start = +start.toFixed(3); c.end = +end.toFixed(3); c.userTrim = true; }, group);
  afterTrim(c);
  return true;
}
function clipMaxEnd(c) { return W.kind === 'vod' ? W.duration : (vod.duration && isFinite(vod.duration) ? vod.duration : c.end + 30); }
function afterTrim(c) {
  fillInspector(); renderRows(); drawHeat(); dirty = true; edDirty = true; saveClip(c, true); rescanSoon(c);
  if (W.kind === 'vod' && c.words.length && (c.start < c.words[0].start - 6 || c.end > c.words[c.words.length - 1].end + 6)) { c.listened = false; listen([c.id]); }
}
function restoreOriginal(c) {
  if (!c.orig || c.state === 'kept') return;
  record(c, 'restore original cut', () => { c.start = c.orig[0]; c.end = c.orig[1]; c.userTrim = false; });
  afterTrim(c); edFit(c);
  try { vod.currentTime = c.start; } catch {}
  toast('Back to the original cut. Undo brings your trim back.', '', 2500);
}
$('#tRestore').onclick = () => { const c = curClip(); if (c) restoreOriginal(c); };
function syncTrimInfo(c) {
  $('#tStart').textContent = fmtA(absOf(c, c.start)); $('#tEnd').textContent = fmtA(absOf(c, c.end));
  const changed = c.orig && (Math.abs(c.orig[0] - c.start) > 0.01 || Math.abs(c.orig[1] - c.end) > 0.01);
  $('#tRestore').hidden = !changed || c.state === 'kept';
  $('#tLen').textContent = `${(c.end - c.start).toFixed(2)} s` + (changed ? ` · found as ${(c.orig[1] - c.orig[0]).toFixed(1)} s` : '');
}
function stepFrame(dir) {
  const c = curClip(); vod.pause();
  const f = 1 / fpsOf(c);
  try { vod.currentTime = Math.max(0, Math.round(vod.currentTime / f + dir) * f + f * 0.25); } catch {}
  dirty = true; edDirty = true;
}
$('#edFrameBack').onclick = () => stepFrame(-1);
$('#edFrameFwd').onclick = () => stepFrame(1);
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.5, 2];
function setSpeed(v) { vod.playbackRate = v; $('#edSpeed').value = String(v); }
$('#edSpeed').onchange = e => setSpeed(+e.target.value);
function speedStep(dir) { const i = SPEEDS.indexOf(vod.playbackRate); setSpeed(SPEEDS[clamp((i < 0 ? 3 : i) + dir, 0, SPEEDS.length - 1)]); toast(`Speed ${vod.playbackRate}×`, '', 1200); }

/* --- the timeline --- */
const edc = $('#edCanvas'), edg = edc.getContext('2d');
const ed = { key: null, a: 0, b: 60, data: null, fetching: false, drag: null, hover: null };
let edDirty = true;
function edDur(c) { return clipMaxEnd(c); }
function edFit(c) {
  if (!c) return;
  const len = c.end - c.start, pad = Math.max(2, len * 0.15), dur = edDur(c);
  ed.a = Math.max(0, c.start - pad); ed.b = Math.min(dur, c.end + pad);
  if (ed.b - ed.a < 2) ed.b = Math.min(dur, ed.a + 2);
  ed.key = keyOf(c); edDirty = true; edFetch(c);
}
function edZoom(f, at) {
  const c = curClip(); if (!c) return;
  const span = ed.b - ed.a, dur = edDur(c);
  at = at ?? (vod.currentTime >= ed.a && vod.currentTime <= ed.b ? vod.currentTime : (ed.a + ed.b) / 2);
  const ns = clamp(span * f, 1.5, Math.min(dur, 1800));
  const r = (at - ed.a) / span;
  ed.a = clamp(at - ns * r, 0, Math.max(0, dur - ns)); ed.b = ed.a + ns;
  edDirty = true; edFetch(c);
}
function edPan(dt) {
  const c = curClip(); if (!c) return;
  const span = ed.b - ed.a, dur = edDur(c);
  ed.a = clamp(ed.a + dt, 0, Math.max(0, dur - span)); ed.b = ed.a + span; edDirty = true; edFetch(c);
}
$('#edZoomIn').onclick = () => edZoom(0.5);
$('#edZoomOut').onclick = () => edZoom(2);
$('#edFit').onclick = () => edFit(curClip());
async function edFetch(c) {
  // loudness, laughter and chat for what's on screen (plus a screen either side), only when needed
  const d = ed.data, span = ed.b - ed.a;
  if (ed.fetching || (d && d.pid === c.pid && d.a <= ed.a && d.b >= Math.min(ed.b, edDur(c) - 0.5))) return;
  const a = Math.max(0, ed.a - span), b = ed.b + span;
  ed.fetching = true;
  try { const r = await api(`/api/strip/${c.pid}?a=${a.toFixed(2)}&b=${b.toFixed(2)}`); r.pid = c.pid; ed.data = r; }
  catch { ed.data = { pid: c.pid, a, b, hz: 10, wave: [], events: {}, chat: [], start: Math.floor(a) }; }
  ed.fetching = false; edDirty = true;
  const cc = curClip(); if (cc && cc.pid === c.pid && (ed.data.a > ed.a || ed.data.b < Math.min(ed.b, edDur(cc) - 0.5))) edFetch(cc);
}
function edSize() {
  const dpr = window.devicePixelRatio || 1, w = Math.max(100, edc.clientWidth), h = 92;
  if (edc.width !== Math.round(w * dpr)) { edc.width = Math.round(w * dpr); edc.height = Math.round(h * dpr); }
  return [w, h, dpr];
}
new ResizeObserver(() => { edDirty = true; }).observe(edc);
const ED = { ruler: [0, 14], wave: [16, 42], caps: [45, 62], ev: [65, 89] };
const tToX = (t, w) => (t - ed.a) / (ed.b - ed.a) * w;
const xToT = (x, w) => ed.a + x / w * (ed.b - ed.a);
function rulerStep(span, w) {
  const want = span / (w / 90);
  return [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800].find(s => s >= want) || 3600;
}
function drawEditor() {
  const c = curClip();
  if (!c || mode !== 'clip' || $('#editor').hidden) return;
  if (ed.key !== keyOf(c)) edFit(c);
  const [w, h, dpr] = edSize(), g = edg;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = '#0e0f11'; g.fillRect(0, 0, w, h);
  const span = ed.b - ed.a;
  // ruler (stream time)
  const st = rulerStep(span, w);
  g.font = '10px Inter, sans-serif'; g.textBaseline = 'top'; g.fillStyle = '#6d7079'; g.strokeStyle = '#26272c'; g.lineWidth = 1;
  for (let t = Math.ceil(ed.a / st) * st; t <= ed.b; t += st) {
    const x = Math.round(tToX(t, w)) + .5;
    g.beginPath(); g.moveTo(x, 10); g.lineTo(x, h); g.stroke();
    const label = st < 1 ? fmtA(absOf(c, t)).replace(/0$/, '') : fmt(absOf(c, t));
    g.fillText(label, x + 3, 1);
  }
  // waveform
  const d = ed.data;
  if (d && d.wave && d.wave.length) {
    const [y0, y1] = ED.wave, mid = (y0 + y1) / 2, amp = (y1 - y0) / 2, hz = d.hz || 10, base = d.hz === 1 ? d.start : d.a;
    g.fillStyle = '#3a3d46';
    for (let x = 0; x < w; x++) {
      const ta = xToT(x, w), tb = xToT(x + 1, w);
      let i0 = Math.floor((ta - base) * hz), i1 = Math.max(i0 + 1, Math.ceil((tb - base) * hz));
      let m = -90; for (let i = Math.max(0, i0); i < Math.min(d.wave.length, i1); i++) if (d.wave[i] > m) m = d.wave[i];
      if (m <= -89) continue;
      const v = clamp((m + 60) / 60, 0.02, 1) * amp;
      g.fillRect(x, mid - v, 1, v * 2);
    }
  }
  // captions (lines, or single words when zoomed in)
  const pps = w / span, [cy0, cy1] = ED.caps;
  const showWords = pps > 90;
  g.font = '10.5px Inter, sans-serif'; g.textBaseline = 'middle';
  const blocks = showWords ? c.words.filter(x => x.end >= ed.a && x.start <= ed.b).map(x => ({ start: x.start, end: x.end, text: censorWord(c, x), edited: x.edited }))
    : chunksFor(c).map(ch => ({ start: ch.start, end: ch.end, text: ch.words.map(x => censorWord(c, x)).join(' '), edited: ch.words.some(x => x.edited) }));
  for (const b of blocks) {
    const x0 = tToX(b.start, w), x1 = Math.max(x0 + 2, tToX(b.end, w));
    if (x1 < 0 || x0 > w) continue;
    g.fillStyle = b.edited ? '#4a4128' : '#25324a'; g.fillRect(x0, cy0, x1 - x0 - 1, cy1 - cy0);
    if (x1 - x0 > 18) {
      g.save(); g.beginPath(); g.rect(x0 + 2, cy0, x1 - x0 - 4, cy1 - cy0); g.clip();
      g.fillStyle = b.edited ? '#ffd666' : '#c6d4f0'; g.fillText(b.text, x0 + 3, (cy0 + cy1) / 2); g.restore();
    }
  }
  // detected events and chat
  const [ey0, ey1] = ED.ev;
  if (d && d.events) {
    const cols = { laugh: '#5ee6a8', scream: '#ff9f43', cheer: '#c9a7ff' };
    for (const [k, arr] of Object.entries(d.events)) {
      g.fillStyle = cols[k] || '#888';
      arr.forEach((v, i) => { if (v < 0.12) return; const t = d.start + i, x0 = tToX(t, w), x1 = tToX(t + 1, w); if (x1 < 0 || x0 > w) return;
        g.globalAlpha = clamp(v, 0.2, 0.9); g.fillRect(x0, ey0 + 3, Math.max(1, x1 - x0), ey1 - ey0 - 6); });
    }
    g.globalAlpha = 1;
  }
  if (d && d.chat && d.chat.length) {
    g.strokeStyle = '#4c8dff'; g.lineWidth = 1.2; g.beginPath();
    d.chat.forEach((v, i) => { const x = tToX(d.start + i + 0.5, w), y = ey1 - 2 - clamp(v, 0, 8) / 8 * (ey1 - ey0 - 4); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.stroke();
  }
  // content check flags and fixes
  for (const s of fixSpans(c)) { const x0 = tToX(s[0], w), x1 = tToX(s[1], w); g.fillStyle = 'rgba(62,194,143,.85)'; g.fillRect(x0, ey0, Math.max(2, x1 - x0), 3); }
  for (const f of ((c.safety && c.safety.flags) || []).filter(f => f.source === 'speech' && f.status === 'open')) {
    const x0 = tToX(c.start + f.start, w), x1 = tToX(c.start + f.end, w);
    g.fillStyle = f.severity >= 3 ? '#f05a5e' : f.severity === 2 ? '#e3a33b' : '#a3a6ae'; g.fillRect(x0, ey0, Math.max(3, x1 - x0), 3);
  }
  // outside the clip: dimmed; the clip: framed; original cut: dashed
  const xs = tToX(c.start, w), xe = tToX(c.end, w);
  g.fillStyle = 'rgba(14,15,17,.62)'; g.fillRect(0, 12, Math.max(0, xs), h); g.fillRect(xe, 12, Math.max(0, w - xe), h);
  if (c.orig && (Math.abs(c.orig[0] - c.start) > 0.01 || Math.abs(c.orig[1] - c.end) > 0.01)) {
    g.setLineDash([3, 3]); g.strokeStyle = '#6d7079';
    [c.orig[0], c.orig[1]].forEach(t => { const x = Math.round(tToX(t, w)) + .5; g.beginPath(); g.moveTo(x, 14); g.lineTo(x, h); g.stroke(); });
    g.setLineDash([]);
  }
  const locked = c.state === 'kept', acc = locked ? '#6d7079' : '#4c8dff';
  g.strokeStyle = acc; g.lineWidth = 1.5; g.strokeRect(xs, 13, xe - xs, h - 14);
  for (const [x, which] of [[xs, 's'], [xe, 'e']]) {
    const hot = ed.hover === which || (ed.drag && ed.drag.which === which);
    g.fillStyle = hot ? '#9cc0ff' : acc; g.fillRect(x - (which === 's' ? 0 : 6), 13, 6, h - 14);
    g.fillStyle = '#0e0f11'; for (let k = 0; k < 3; k++) g.fillRect(x - (which === 's' ? -2 : 4), h / 2 - 6 + k * 5, 2, 2);
    if (hot) { const label = fmtA(absOf(c, which === 's' ? c.start : c.end)); g.font = '600 10.5px Inter, sans-serif';
      const tw = g.measureText(label).width + 8, lx = clamp(x - tw / 2, 0, w - tw); g.fillStyle = '#e9eaed'; g.fillRect(lx, 14, tw, 14); g.fillStyle = '#0e0f11'; g.textBaseline = 'middle'; g.fillText(label, lx + 4, 21); }
  }
  // playhead
  const px = tToX(vod.currentTime, w);
  if (px >= 0 && px <= w) { g.fillStyle = '#fff'; g.fillRect(Math.round(px) - 1, 10, 2, h - 10); g.beginPath(); g.moveTo(px - 5, 10); g.lineTo(px + 5, 10); g.lineTo(px, 16); g.fill(); }
  $('#edTime').textContent = `${fmtA(absOf(c, vod.currentTime))} · frame ${Math.round(vod.currentTime * fpsOf(c))}`;
}
function edPos(e) { const r = edc.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top, r.width]; }
function edHit(x, w, c) {
  const xs = tToX(c.start, w), xe = tToX(c.end, w);
  if (Math.abs(x - xs) <= 8 && Math.abs(x - xs) <= Math.abs(x - xe)) return 's';
  if (Math.abs(x - xe) <= 8) return 'e';
  return null;
}
function edSnap(c, t, w, alt) {
  if (alt) return t;
  const tol = 6 / w * (ed.b - ed.a);
  let best = t, bd = tol;
  const cand = [vod.currentTime];
  for (const x of c.words) { cand.push(x.start - 0.05, x.end + 0.05); }
  for (const v of cand) { const dd = Math.abs(v - t); if (dd < bd) { bd = dd; best = v; } }
  return best;
}
edc.addEventListener('pointerdown', e => {
  const c = curClip(); if (!c || e.button !== 0) return;
  const [x, , w] = edPos(e), which = edHit(x, w, c);
  if (which && c.state !== 'kept') {
    ed.drag = { which, before: snapEdit(c), moved: false };
    vod.pause();
  } else {
    ed.drag = { which: 'seek' };
    try { vod.currentTime = clamp(xToT(x, w), 0, edDur(c)); } catch {}
  }
  edc.setPointerCapture(e.pointerId); edDirty = true; dirty = true;
});
edc.addEventListener('pointermove', e => {
  const c = curClip(); if (!c) return;
  const [x, , w] = edPos(e);
  if (!ed.drag) {
    const hv = c.state === 'kept' ? null : edHit(x, w, c);
    if (hv !== ed.hover) { ed.hover = hv; edDirty = true; }
    edc.style.cursor = hv ? 'ew-resize' : 'text';
    return;
  }
  const t = clamp(xToT(x, w), 0, edDur(c));
  if (ed.drag.which === 'seek') { try { vod.currentTime = t; } catch {} edDirty = true; dirty = true; return; }
  const v = edSnap(c, t, w, e.altKey);
  if (ed.drag.which === 's') c.start = +clamp(v, 0, c.end - MIN_LEN).toFixed(3);
  else c.end = +clamp(v, c.start + MIN_LEN, edDur(c)).toFixed(3);
  c.userTrim = true; ed.drag.moved = true;
  try { vod.currentTime = ed.drag.which === 's' ? c.start : Math.max(c.start, c.end - 0.04); } catch {}
  syncTrimInfo(c); edDirty = true; dirty = true;
});
edc.addEventListener('pointerup', () => {
  const c = curClip(), dr = ed.drag; ed.drag = null; edDirty = true;
  if (c && dr && dr.which !== 'seek' && dr.moved) { pushEdit(c, dr.which === 's' ? 'move start' : 'move end', dr.before); afterTrim(c); }
});
edc.addEventListener('pointerleave', () => { if (ed.hover) { ed.hover = null; edDirty = true; } });
edc.addEventListener('wheel', e => {
  const c = curClip(); if (!c) return;
  e.preventDefault();
  const [x, , w] = edPos(e);
  if (e.ctrlKey || e.metaKey) edZoom(e.deltaY > 0 ? 1.25 : 0.8, xToT(x, w));
  else edPan((Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) / w * (ed.b - ed.a));
}, { passive: false });
function edFollow() {
  // while playing, keep the playhead in view
  const c = curClip(); if (!c || vod.paused || ed.drag) return;
  const t = vod.currentTime, span = ed.b - ed.a;
  if (t > ed.b - span * 0.02 || t < ed.a) { ed.a = clamp(t - span * 0.1, 0, Math.max(0, edDur(c) - span)); ed.b = ed.a + span; edFetch(c); }
}

/* --- exact preview: a few seconds rendered by the real export pipeline --- */
let exactBusy = false;
async function exactPreview() {
  const c = curClip(); if (!c || mode !== 'clip' || exactBusy) return;
  exactBusy = true; const btn = $('#edExact'); btn.disabled = true;
  const clip = { id: c.id, start: c.start, end: c.end, title: (c.title || '').trim() };
  if (c.wordsEdited) clip.words = c.words.filter(w => w.start >= c.start - 0.2 && w.start < c.end);
  try {
    const { job } = await api('/api/preview_render', { pid: c.pid, clip, opts: JSON.parse(JSON.stringify(opts)), at: vod.currentTime });
    const j = await waitJob(job, j => { btn.textContent = j.step === 'Rendering' ? `Rendering ${Math.round(j.progress * 100)}%` : 'Getting ready…'; });
    if (j && j.status === 'done' && curClip() === c) showExact(j.result);
    else if (j && j.status === 'error') toast(j.error, 'err', 7000);
  } catch (e) { toast(e.message, 'err', 6000); }
  exactBusy = false; btn.disabled = false; btn.textContent = 'Exact preview';
}
function showExact(r) {
  const v = $('#filePlayer');
  vod.pause();
  v.src = r.url; v.hidden = false; v.loop = true; v.muted = userMuted; v.play().catch(() => {});
  $('#exactClose').hidden = false; $('#phoneBox').classList.add('exact');
  $('#exactClose').textContent = `Exact preview ${fmtA(r.start)}–${fmtA(r.end)} · close ×`;
}
function hideExact() {
  const v = $('#filePlayer'); if (v.hidden) return;
  v.pause(); v.removeAttribute('src'); v.load(); v.hidden = true;
  $('#exactClose').hidden = true; $('#phoneBox').classList.remove('exact');
}
$('#edExact').onclick = exactPreview;
$('#exactClose').onclick = hideExact;

/* --- shortcuts help --- */
function showKeys() { const d = $('#keysDlg'); if (!d.open) d.showModal(); }
$('#edKeys').onclick = showKeys;
$('#keysClose').onclick = () => $('#keysDlg').close();
document.addEventListener('keydown', e => {
  if (view !== 'review' || !W || $('#dlg').open || $('#keysDlg').open) return;
  const typing = e.target.closest('input,select,textarea');
  const mod = e.ctrlKey || e.metaKey;
  if (mod && !typing && (e.key === 'z' || e.key === 'Z' || e.key === 'y')) {
    e.preventDefault(); undoRedo(e.key === 'y' || e.shiftKey); return;
  }
  if (typing || mod || e.altKey) return;
  const c = curClip(), k = e.key;
  if (k === '?') { e.preventDefault(); showKeys(); return; }
  if (!c || mode !== 'clip') return;
  if (k === 'i' || k === 'I') { if (c.state !== 'kept') setBounds(c, vod.currentTime, c.end, 'set start', null); }
  else if (k === 'o' || k === 'O') { if (c.state !== 'kept') setBounds(c, c.start, vod.currentTime, 'set end', null); }
  else if (k === ',') stepFrame(-1);
  else if (k === '.') stepFrame(1);
  else if (k === '<') speedStep(-1);
  else if (k === '>') speedStep(1);
  else if (k === '[') { try { vod.currentTime = c.start; } catch {} edDirty = true; }
  else if (k === ']') { try { vod.currentTime = Math.max(c.start, c.end - 0.05); } catch {} edDirty = true; }
  else if (k === '+' || k === '=') edZoom(0.5);
  else if (k === '-' || k === '_') edZoom(2);
  else if (k === '0') edFit(c);
  else if (k === 'Escape') hideExact();
  else return;
  e.preventDefault();
});

loadRecent(); loadStorage(); syncControls(); refreshQueue(); updateCrumb(); loadUpdate(); loadSafety(); loadTwitch();
