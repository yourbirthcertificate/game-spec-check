'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const L = window.GSC;

const PICKS = [
  ['steam', '1245620', 'Elden Ring'],
  ['steam', '1091500', 'Cyberpunk 2077'],
  ['steam', '1086940', "Baldur's Gate 3"],
  ['steam', '730', 'Counter-Strike 2'],
  ['steam', '1174180', 'Red Dead Redemption 2'],
  ['steam', '553850', 'Helldivers 2'],
  ['steam', '2246340', 'Monster Hunter Wilds'],
  ['steam', '413150', 'Stardew Valley'],
  ['pcgw', 'Fortnite', 'Fortnite'],
  ['pcgw', 'Valorant', 'Valorant'],
];

const ICONS = {
  pass: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  close: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.2v.1"/>',
  warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.2v.1"/>',
  fail: '<path d="M6 6l12 12M18 6L6 18"/>',
  unknown: '<path d="M9 9a3 3 0 1 1 4.5 2.6c-.9.6-1.5 1.2-1.5 2.4M12 17.6v.1"/>',
};
// Answer tier -> the status colour and icon it borrows.
const TIER_STATUS = { great: 'pass', ok: 'pass', close: 'close', fail: 'fail', partial: 'unknown', unknown: 'unknown' };
const GROUP_STATUS = { met: 'pass', borderline: 'close', failing: 'fail', incomplete: 'unknown' };
const GROUP_NAME = { met: 'Meets the specs', borderline: 'Borderline', failing: 'Below the specs',
                     incomplete: 'Incomplete', unchecked: 'Not checked yet' };
const LEVEL_WORD = { pass: 'met', close: 'close', fail: 'not met', partial: 'partly checked', unknown: 'unclear' };
const SOURCE_NAME = { steam: 'Steam', pcgw: 'PCGamingWiki', pasted: 'Pasted' };
const ROW_ORDER = ['cpu', 'gpu', 'ram', 'vram', 'os', 'directx', 'storage'];
const PART_NAME = { cpu: 'Processor', gpu: 'Graphics', ram: 'Memory', vram: 'Video memory', os: 'Windows', directx: 'DirectX' };
const KIND_NAME = { desktop: 'Desktop', laptop: 'Laptop', planned: 'Planned build', upgrade: 'Upgrade' };
const FIELD_LABEL = { os: 'Windows', cpu: 'Processor', gpu: 'Graphics', ram: 'Memory', vram: 'Video memory',
                      directx: 'DirectX', storage: 'Storage' };

const state = {
  specs: null,
  profiles: [],
  providers: [],
  profile: 'real',
  override: { cpu: '', gpu: '', ram: '', vram: '' },
  current: null,        // {source, id, name, installed}
  sources: {},          // source -> game result
  active: null,
  pcgwPending: false,
  req: 0,
  showNums: false,
  pasteLast: null,
  lib: null,
  wanted: [],
  out: new Map(),       // target id -> check entry
  filters: { ...L.DEFAULT_FILTERS },
  includeWanted: false,
  open: null,           // expanded row target
  choosing: null,
  run: 0,
  busy: false,
  progress: { done: 0, total: 0 },
  scan: null,
  plan: { picked: new Set(), rows: new Map(), run: 0, busy: false, editing: null },
};

const icon = k => `<svg class="i s-${k}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[k] || ICONS.unknown}</svg>`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = n => Number(n).toLocaleString('en-US');
const times = r => (r >= 10 ? '10×+' : r >= 3 ? Math.round(r) + '×' : r.toFixed(1) + '×');
const list = xs => (xs.length > 1 ? xs.slice(0, -1).join(', ') + ' and ' + xs.at(-1) : xs[0] || '');

// "12th Gen Intel(R) Core(TM) i7-12700H" -> "Intel Core i7-12700H"
const tidy = s => String(s || '')
  .replace(/\((R|TM|C)\)/gi, '')
  .replace(/\b\d+(st|nd|rd|th) Gen\b/i, '')
  .replace(/\s+CPU @.*$/i, '')
  .replace(/\s+\d+-Core Processor.*$/i, '')
  .replace(/\s+/g, ' ').trim();
const shortGpu = s => tidy(s).replace(/^NVIDIA /i, '').replace(/ GPU$/i, '');

async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v !== '' && v != null) url.searchParams.set(k, v);
  }
  return request(url, {});
}

async function post(path, body) {
  return request(new URL(path, location.origin), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  });
}

async function request(url, opts) {
  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new Error('The app has stopped responding. Start it again and reload this page.');
  }
  const data = await res.json().catch(() => ({ error: 'The app sent back something unreadable.' }));
  if (!res.ok) throw new Error(data.error || 'That request failed.');
  return data;
}

const pc = () => L.pcParams(state.profile, state.override);
const hasOverride = () => Object.values(state.override).some(Boolean);
const simulating = () => state.profile !== 'real' || hasOverride();
const profileById = id => state.profiles.find(p => p.id === id);

/* ------------------------------------------------------------ this PC */

async function loadSpecs(refresh) {
  $('#rescan').disabled = true;
  try {
    state.specs = await api('/api/specs', refresh ? { refresh: 1 } : {});
    renderSpecs();
  } catch (e) {
    $('#pc-summary').innerHTML = `<span class="error">${esc(e.message)}</span>`;
  } finally {
    $('#rescan').disabled = false;
  }
}

function dxText(cap) {
  if (!cap || !cap.directx) return 'DirectX support of this graphics chip is unknown.';
  const fl = cap.feature_level ? ` (feature level ${cap.feature_level.replace('_', '.')})` : '';
  return `DirectX ${cap.directx}${fl}, ${cap.source === 'detected' ? 'reported by Direct3D' : 'estimated from the model'}.`;
}

function renderSpecs() {
  const s = state.specs;
  if (!s) return;
  const cpu = s.cpu, gpu = s.gpu;
  const winName = (s.os.name || 'Windows').replace(/\s+(Home|Pro|Enterprise|Education).*$/i, '').replace(/\s+\d+-bit$/i, '');
  const bits = [tidy(cpu.name), gpu ? shortGpu(gpu.name) : 'no graphics card found',
    s.ram_gb ? `${s.ram_gb} GB memory` : null, winName].filter(Boolean);
  $('#pc-summary').innerHTML = '<span class="tag">Detected</span>' +
    bits.map(esc).join('<span class="sep">·</span>');

  const usedGpu = state.override.gpu || (gpu && gpu.name);
  const cores = [cpu.cores && `${cpu.cores} cores`, cpu.threads && `${cpu.threads} threads`].filter(Boolean).join(', ');
  let html = `<div class="spec"><div class="k">Processor</div><div>
      <div class="v">${esc(tidy(cpu.name))}</div>
      <div class="sub">${esc(cores)}${cpu.score ? '' : (cores ? '. ' : '') + "This model isn't in the speed table yet. Pick the closest one on the right to get answers."}</div>
    </div></div>`;

  html += '<div class="spec"><div class="k">Graphics</div><div>';
  if (gpu) {
    html += `<div class="v">${esc(tidy(gpu.name))}</div>
      <div class="sub">${gpu.vram_gb ? esc(gpu.vram_gb + ' GB of video memory. ') : ''}${esc(dxText(gpu.directx))} ${esc(s.gpu_reason)}</div>`;
  } else {
    html += `<div class="sub">${esc(s.gpu_reason)}</div>`;
  }
  if (s.adapters.length > 1) {
    html += '<div class="adapters" role="group" aria-label="Graphics chip used for checks">';
    for (const a of s.adapters) {
      const why = a.virtual ? 'virtual display, ignored'
        : a.selected ? 'fastest, used by games'
        : a.score ? 'slower' : 'not in the speed table';
      html += `<button class="adapter" type="button" data-adapter="${esc(a.name)}"
        aria-pressed="${a.name === usedGpu}" ${a.virtual ? 'disabled' : ''}>
        <span class="dot"></span><span class="name">${esc(tidy(a.name))}</span><span class="why">${esc(why)}</span></button>`;
    }
    html += '</div>';
  }
  html += '</div></div>';

  const screen = s.display
    ? `${s.display.width} × ${s.display.height}${s.display.hz ? `, ${s.display.hz} Hz` : ''}` : '';
  html += `
    <div class="spec"><div class="k">Memory</div><div class="v">${s.ram_gb ? esc(s.ram_gb + ' GB') : 'Unknown'}</div></div>
    <div class="spec"><div class="k">Windows</div><div>
      <div class="v">${esc(s.os.name || 'Windows')}</div>
      ${s.dx_runtime ? `<div class="sub">Includes the DirectX ${esc(s.dx_runtime)} runtime. What games can use depends on the graphics chip.</div>` : ''}</div></div>
    <div class="spec"><div class="k">Free space</div><div>
      ${s.drives.map(d => `<div class="v">${esc(Math.round(d.free_gb))} GB free on ${esc(d.id)} <span class="sub">of ${esc(Math.round(d.size_gb))} GB</span></div>`).join('') || '<div class="sub">Unknown</div>'}
    </div></div>
    ${screen ? `<div class="spec"><div class="k">Screen</div><div class="v">${esc(screen)}</div></div>` : ''}
    ${s.system ? `<div class="spec"><div class="k">Model</div><div class="v">${esc(s.system)}</div></div>` : ''}`;
  $('#pc-body').innerHTML = html;
}

function renderProfileSelect() {
  const sel = $('#profile');
  sel.innerHTML = '<option value="real">This PC (detected)</option>' + state.profiles.map(p =>
    `<option value="${esc(p.id)}">${esc(p.name)} (${esc(KIND_NAME[p.kind] || 'profile')}, simulated)</option>`).join('');
  if (state.profile !== 'real' && !profileById(state.profile)) state.profile = 'real';
  sel.value = state.profile;
}

$('#profile').addEventListener('change', e => {
  state.profile = e.target.value;
  renderSim();
  refreshAnswers();
});

$('#pc-toggle').addEventListener('click', () => {
  const box = $('#pc-details');
  box.hidden = !box.hidden;
  $('#pc-toggle').setAttribute('aria-expanded', String(!box.hidden));
  $('#pc-toggle').textContent = box.hidden ? 'Details and upgrades' : 'Hide details';
});

$('#pc-body').addEventListener('click', e => {
  const btn = e.target.closest('.adapter');
  if (!btn || btn.disabled) return;
  const name = btn.dataset.adapter;
  const best = state.specs.gpu && state.specs.gpu.name;
  setOverride({ ...state.override, gpu: name === best ? '' : name });
});

$('#rescan').addEventListener('click', async () => {
  await loadSpecs(true);
  refreshAnswers();
});

/* ------------------------------------------------- trying other parts */

function setOverride(next) {
  state.override = next;
  renderSpecs();
  renderSim();
  refreshAnswers();
}

// Hardware changed (profile, parts, rescan): every stored answer is stale.
function refreshAnswers() {
  state.run++;
  state.busy = false;
  state.out.clear();
  state.plan.run++;
  state.plan.busy = false;
  state.plan.rows.clear();
  state.choosing = null;
  normalizeFilters();
  if (state.lib) renderLibrary();
  renderWanted();
  renderPlan();
  if (state.current && state.current.source === 'pasted') comparePasted();
  else if (state.current) openGame(state.current.source, state.current.id, state.current.name, true, state.current.installed);
}

function profileLabel() {
  const p = state.profile !== 'real' ? profileById(state.profile) : null;
  return (p ? p.name : 'This PC') + (hasOverride() ? ' with changed parts' : '');
}

function renderSim() {
  const el = $('#sim');
  if (!simulating()) { el.hidden = true; return; }
  const o = state.override;
  const p = state.profile !== 'real' ? profileById(state.profile) : null;
  const parts = [o.cpu, o.gpu && tidy(o.gpu), o.ram && `${o.ram} GB of memory`, o.vram && `${o.vram} GB of video memory`].filter(Boolean);
  const what = p
    ? `the saved profile "${p.name}"${parts.length ? `, with ${list(parts)}` : ''}`
    : `this PC with ${list(parts)}`;
  el.hidden = false;
  el.innerHTML = `<span><strong>Simulated.</strong> Answers below are for ${esc(what)}, not the detected hardware.
    ${p ? 'Parts the profile leaves blank come from this PC.' : ''}</span>
    <button class="btn" type="button" id="sim-reset">Back to my real PC</button>`;
}

function resetOverride() {
  for (const k of ['cpu', 'gpu', 'ram', 'vram']) $('#wi-' + k).value = '';
  $('#wi-error').hidden = true;
  state.profile = 'real';
  $('#profile').value = 'real';
  setOverride({ cpu: '', gpu: '', ram: '', vram: '' });
}
$('#sim').addEventListener('click', e => { if (e.target.id === 'sim-reset') resetOverride(); });
$('#wi-reset').addEventListener('click', resetOverride);

function hardwareSuggest(input, kind) {
  let timer;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      try {
        const data = await api('/api/hardware', { kind, q });
        const dl = document.getElementById(input.getAttribute('list'));
        if (dl) dl.innerHTML = data.results.map(r => `<option value="${esc(r.name)}"></option>`).join('');
      } catch { /* suggestions are optional */ }
    }, 200);
  });
}
hardwareSuggest($('#wi-cpu'), 'cpu');
hardwareSuggest($('#wi-gpu'), 'gpu');

// "gtx 1050 ti" -> "GeForce GTX 1050 Ti", or an error for unknown parts.
async function placeParts(next) {
  for (const kind of ['cpu', 'gpu']) {
    if (!next[kind]) continue;
    const known = kind === 'gpu' && state.specs && state.specs.adapters.some(a => a.name === next[kind]);
    if (known) continue;
    const data = await api('/api/hardware', { kind, q: next[kind] });
    if (!data.reading) {
      throw new Error(`Couldn't place "${next[kind]}". Pick one of the suggestions that appear as you type.`);
    }
    next[kind] = data.reading.matched || next[kind];
  }
  return next;
}

$('#whatif-form').addEventListener('submit', async e => {
  e.preventDefault();
  const err = $('#wi-error');
  err.hidden = true;
  const next = { cpu: $('#wi-cpu').value.trim(), gpu: $('#wi-gpu').value.trim(), ram: $('#wi-ram').value, vram: $('#wi-vram').value };
  try {
    await placeParts(next);
    $('#wi-cpu').value = next.cpu; $('#wi-gpu').value = next.gpu;
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
    return;
  }
  setOverride(next);
});

$('#wi-save').addEventListener('click', () => {
  const o = { cpu: $('#wi-cpu').value.trim(), gpu: $('#wi-gpu').value.trim(), ram: $('#wi-ram').value, vram: $('#wi-vram').value };
  state.plan.editing = { name: '', kind: 'upgrade', ...o };
  showTab('plan');
});

/* -------------------------------------------------------------- search */

const qEl = $('#q'), resEl = $('#results');
const search = new L.SearchState();
let searchTimer, activeIdx = -1;

function closeResults() {
  resEl.hidden = true;
  qEl.setAttribute('aria-expanded', 'false');
  activeIdx = -1;
}

function dismissResults() {
  // A dismissed list must not come back when a slow answer arrives.
  clearTimeout(searchTimer);
  search.invalidate();
  closeResults();
}

function showResults(html) {
  resEl.innerHTML = html;
  resEl.hidden = false;
  qEl.setAttribute('aria-expanded', 'true');
}

function renderHits(warning) {
  const hits = search.hits;
  const warn = warning ? `<li class="empty small">${esc(warning)}</li>` : '';
  if (!hits.length) return showResults(warn + '<li class="empty">Nothing by that name. Try fewer words.</li>');
  showResults(warn + hits.map((h, i) => `
    <li role="option" id="hit-${i}" data-i="${i}" aria-selected="${i === activeIdx}">
      ${h.image ? `<img src="${esc(h.image)}" alt="" loading="lazy">` : '<span class="noimg"></span>'}
      <span class="name">${esc(h.name)}</span>
      <span class="from">${esc(SOURCE_NAME[h.source] || h.source)}</span>
    </li>`).join(''));
}

async function doSearch(term) {
  const token = search.begin(term);
  showResults('<li class="empty"><span class="spin"></span>Searching…</li>');
  try {
    const data = await api('/api/search', { q: term });
    if (!search.accept(token, data.results)) return;
    activeIdx = -1;
    renderHits(data.warning);
  } catch (e) {
    if (!search.fail(token)) return;
    showResults(`<li class="empty error">${esc(e.message)}</li>`);
  }
}

qEl.addEventListener('input', () => {
  clearTimeout(searchTimer);
  search.invalidate();          // older answers no longer match the box
  const term = qEl.value.trim();
  if (term.length < 2) return closeResults();
  showResults('<li class="empty"><span class="spin"></span>Searching…</li>');
  searchTimer = setTimeout(() => doSearch(term), 280);
});
qEl.addEventListener('focus', () => {
  if (search.pickable(qEl.value.trim()).length) renderHits();
});
qEl.addEventListener('keydown', e => {
  if (e.key === 'Escape') return dismissResults();
  const hits = search.pickable(qEl.value.trim());
  if (e.key === 'Enter') {
    e.preventDefault();
    if (!resEl.hidden && hits.length) pick(hits[Math.max(activeIdx, 0)]);
    return;
  }
  if (resEl.hidden || !hits.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    activeIdx = (activeIdx + (e.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
    renderHits();
    const el = $('#hit-' + activeIdx);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }
});
resEl.addEventListener('click', e => {
  const li = e.target.closest('li[data-i]');
  if (li) pick(search.hits[+li.dataset.i]);
});
document.addEventListener('click', e => { if (!e.target.closest('.search') && !resEl.hidden) dismissResults(); });

function pick(hit) {
  if (!hit) return;
  dismissResults();
  qEl.value = hit.name;
  openGame(hit.source, hit.id, hit.name);
}

/* ------------------------------------------------- popular and recent */

function loadRecent() {
  try {
    const items = JSON.parse(localStorage.getItem('recent') || '[]');
    return Array.isArray(items) ? items.filter(r => r && r.source && r.id) : [];
  } catch { return []; }
}

function remember(game) {
  if (simulating() || game.source === 'pasted') return;    // only real answers go in the history
  const entry = { source: game.source, id: game.id, name: game.name, tier: game.summary.tier };
  const items = [entry, ...loadRecent().filter(r => !(r.source === entry.source && r.id === entry.id))].slice(0, 6);
  try { localStorage.setItem('recent', JSON.stringify(items)); } catch { /* history is optional */ }
  renderChips();
}

function renderChips() {
  const chip = (s, id, name, tier) => `<button class="pick" type="button" data-source="${esc(s)}" data-id="${esc(id)}" data-name="${esc(name)}">
    ${tier ? `<span class="dot s-${TIER_STATUS[tier] || 'unknown'}"></span>` : ''}${esc(name)}</button>`;
  const recent = loadRecent();
  $('#recent').hidden = !recent.length;
  $('#recent').innerHTML = '<span class="lbl">Recent</span>' + recent.map(r => chip(r.source, r.id, r.name, r.tier)).join('');
  const seen = new Set(recent.map(r => r.source + r.id));
  $('#picks').innerHTML = '<span class="lbl">Popular</span>' +
    PICKS.filter(([s, id]) => !seen.has(s + id)).map(([s, id, name]) => chip(s, id, name)).join('');
}

$('.ask').addEventListener('click', e => {
  const b = e.target.closest('.pick');
  if (b) openGame(b.dataset.source, b.dataset.id, b.dataset.name);
});

/* -------------------------------------------------------------- answer */

async function openGame(source, id, name, quiet, installed) {
  const seq = ++state.req;
  state.current = { source, id, name, installed: !!installed };
  state.sources = {};
  state.active = source;
  state.pcgwPending = false;
  history.replaceState(null, '', '#' + source + '/' + encodeURIComponent(id));
  showTab('check');
  const box = $('#game');
  box.innerHTML = `<div class="state"><span class="spin"></span>Looking up ${esc(name || 'that game')}…</div>`;
  if (!quiet) box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  const extra = installed ? { installed: 1 } : {};
  let game;
  try {
    game = await api('/api/game', { source, id, ...pc(), ...extra });
    if (seq !== state.req) return;
    state.sources[game.source] = game;
    state.active = game.source;
    state.current.name = game.name;
    remember(game);
  } catch (e) {
    if (seq !== state.req) return;
    box.innerHTML = `<div class="state"><strong>That lookup didn't work</strong>${esc(e.message)}
      <div style="margin-top:12px"><button class="btn" type="button" id="retry">Try again</button></div></div>`;
    return;
  }
  const appid = source === 'steam' && game.source === 'steam' ? id : null;
  state.pcgwPending = !!appid;
  renderGame();
  if (!appid) return;
  try {
    const second = await api('/api/pcgw-for', { appid, ...pc(), ...extra });
    if (seq !== state.req) return;
    if (second.found) {
      state.sources.pcgw = second;
      // Steam page without usable requirements: show the wiki's instead.
      if (!Object.keys(state.sources.steam.levels).length) state.active = 'pcgw';
    }
  } catch { /* the second source is optional */ }
  if (seq !== state.req) return;
  state.pcgwPending = false;
  renderGame();
}

function cellNote(r) {
  if (r.status === 'unknown') return r.note || "Can't be compared automatically.";
  const pct = r.ratio != null ? Math.round(r.ratio * 100) : null;
  if (r.key === 'cpu' || r.key === 'gpu') {
    const x = r.ratio;
    const speed = x >= 1.5 ? `Yours is about ${times(x)} as fast.`
      : x >= 1.1 ? 'Yours is a bit faster.'
      : x >= 0.95 ? 'About the same as yours.'
      : x >= 0.8 ? `Yours is a little slower (about ${pct}% as fast).`
      : `Yours is slower (about ${pct}% as fast).`;
    return r.note ? `${speed} ${r.note}` : speed;
  }
  if (r.status === 'pass') return r.note || '';
  if (r.key === 'ram' || r.key === 'vram') {
    return r.status === 'fail' ? `Well short of this (about ${pct}%).` : 'A little less than this.';
  }
  return r.note || '';
}

function reqCell(r, label) {
  if (!r) return `<td data-label="${label}"><span class="muted">Not listed</span></td>`;
  const note = cellNote(r);
  const nums = r.target_score && r.your_score
    ? `<div class="nums">Speed score ${r.estimated ? 'about ' : ''}${num(r.your_score)} for yours, ${num(r.target_score)} for the ${esc(r.target)}.</div>` : '';
  return `<td data-label="${label}"><div class="req">${icon(r.status)}<div>
    <div>${esc(r.required)}</div>
    ${note ? `<div class="req-note">${esc(note)}</div>` : ''}${nums}</div></div></td>`;
}

function sideBySide(g) {
  const levels = [['minimum', 'Minimum'], ['recommended', 'Recommended']].filter(([k]) => g.levels[k]);
  const byKey = {};
  for (const [k] of levels) for (const r of g.levels[k].rows) (byKey[r.key] ||= {})[k] = r;
  const keys = ROW_ORDER.filter(k => byKey[k]);
  const head = levels.map(([k, label]) => {
    const v = g.levels[k].verdict;
    const st = v === 'partial' ? 'unknown' : v;
    return `<th scope="col">${label}<span class="lv s-${st}">${icon(st)}${LEVEL_WORD[v]}</span></th>`;
  }).join('');
  const body = keys.map(key => {
    const any = byKey[key].minimum || byKey[key].recommended;
    return `<tr><th scope="row">${key === 'os' ? 'Windows' : esc(any.label)}</th>
      <td class="mine" data-label="${simulating() ? 'Simulated PC' : 'This PC'}">${esc(tidy(any.yours))}</td>
      ${levels.map(([k, label]) => reqCell(byKey[key][k], label)).join('')}</tr>`;
  }).join('');
  const vague = levels.every(([k]) => g.levels[k].rows.every(r => r.status === 'unknown'));
  const raw = vague || g.source === 'pasted'
    ? levels.map(([k, label]) => g.levels[k].raw && `${label}: ${g.levels[k].raw}`).filter(Boolean).join('\n\n') : '';
  return `<table>
      <colgroup><col class="c-part"><col><col span="${levels.length}"></colgroup>
      <thead><tr><th scope="col"><span class="sr">Part</span></th><th scope="col">${simulating() ? 'Simulated PC' : 'This PC'}</th>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>
    ${raw ? `<p class="raw">As published:\n${esc(raw)}</p>` : ''}`;
}

function evidence(g) {
  const items = [];
  const seen = new Set();
  for (const lv of ['minimum', 'recommended']) {
    const level = g.levels[lv];
    if (!level) continue;
    for (const r of level.rows) {
      if (!['cpu', 'gpu', 'ram', 'vram', 'os', 'directx'].includes(r.key)) continue;
      const text = r.status === 'unknown' ? (r.unknown_reason === 'not_listed' ? `Not listed in the ${lv} specs.` : r.note) : r.evidence;
      if (!text) continue;
      const key = r.key + text;
      if (seen.has(key)) continue;
      seen.add(key);
      const kind = r.status === 'unknown' ? (r.unknown_reason === 'not_listed' ? 'Not listed' : 'Not checked')
        : r.estimated ? 'Estimate' : 'Checked';
      items.push(`<li><span class="ev-k">${esc(PART_NAME[r.key] || r.label)} <span class="muted small">${lv === 'minimum' ? 'min' : 'rec'}</span></span><span class="ev-tag">${kind}</span><span>${esc(text)}</span></li>`);
    }
  }
  const nc = g.summary.not_checked || [];
  const src = g.source === 'pasted'
    ? `Requirements you pasted${g.attribution ? ` (source: ${esc(g.attribution)})` : ''}.`
    : `Requirements from ${esc(g.source_name || SOURCE_NAME[g.source])}.`;
  return `<details class="evidence"><summary>How this was checked</summary>
    <p class="small">${src} ${simulating() ? 'Compared with a simulated PC, not the detected hardware.' : 'Compared with the detected hardware.'}</p>
    <ul>${items.join('')}</ul>
    <p class="small muted">Speed scores are estimates from a hand-made table. Meeting the specs says nothing exact about
      frame rates or graphics settings, and checks only what the listing names.${nc.length ? ` This listing also mentions ${esc(list(nc))}, which ${nc.length > 1 ? 'are' : 'is'} not checked.` : ''}</p>
  </details>`;
}

function renderGame() {
  const g = state.sources[state.active];
  if (!g) return;
  const art = (state.sources.steam && state.sources.steam.image) || g.image;
  const sum = g.summary;
  const status = TIER_STATUS[sum.tier];
  const available = Object.keys(state.sources);
  const hasLevels = Object.keys(g.levels).length > 0;

  const issues = sum.issues.length ? `<div class="issues"><h3>${esc(sum.issues_title)}</h3><ul>
      ${sum.issues.map(i => `<li>${icon(i.status)}<span>${esc(i.text)}</span></li>`).join('')}</ul></div>` : '';

  const switcher = available.length > 1
    ? `<span>Requirements from<span class="seg">${available.map(s =>
        `<button type="button" data-src="${s}" aria-pressed="${s === state.active}">${SOURCE_NAME[s]}</button>`).join('')}</span></span>`
    : `<span>Requirements from ${esc(SOURCE_NAME[state.active] || state.active)}${state.pcgwPending ? '. Checking PCGamingWiki too…' : ''}</span>`;
  const links = [...new Map(available.flatMap(s => state.sources[s].links || []).map(l => [l.label, l])).values()];
  const linkHtml = links.map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join('');
  const dis = (state.sources.pcgw && state.sources.pcgw.disagreements) || [];
  const disHtml = dis.length ? `<div class="disagree"><strong>Steam and PCGamingWiki list different requirements.</strong>
      <ul>${dis.map(d => `<li>${esc(d)}</li>`).join('')}</ul>
      <span class="small muted">Each source is compared on its own. Switch between them below.</span></div>` : '';
  const notes = [g.fallback_note, ...Object.values(g.errors || {}).filter(() => !g.fallback_note)].filter(Boolean);

  const table = hasLevels
    ? `<section class="side ${state.showNums ? 'show-nums' : ''}">
        <div class="side-head"><h2>Side by side</h2>
          <label class="toggle" title="Rough speed ratings for each part. Bigger is faster.">
            <input type="checkbox" id="nums" ${state.showNums ? 'checked' : ''}>Show speed scores</label></div>
        ${sideBySide(g)}
        ${evidence(g)}
        <div class="side-foot">${switcher}<span class="grow"></span>${linkHtml}</div>
      </section>`
    : `<section class="side"><div class="side-foot">${switcher}<span class="grow"></span>${linkHtml}</div></section>`;

  const inWanted = state.wanted.some(w => (g.source === 'steam' || g.source === 'pcgw') && w.source === g.source && w.ref === g.id);
  const actions = `<div class="row-btns answer-actions">
      ${g.source !== 'pasted' ? `<button class="btn" type="button" data-act="want" ${inWanted ? 'disabled' : ''}>${inWanted ? 'In wanted games' : 'Save to wanted games'}</button>` : ''}
      ${hasLevels ? '<button class="btn" type="button" data-act="report-png">Save report image</button><button class="btn" type="button" data-act="report-svg">Save as SVG</button>' : ''}
    </div>`;

  $('#game').innerHTML = `
    <article class="answer t-${sum.tier} ${art ? '' : 'noart'}">
      ${art ? `<img src="${esc(art)}" alt="">` : ''}
      <div>
        <p class="game-name">${esc(g.name)}${simulating() ? ` <span class="pill">Simulated: ${esc(profileLabel())}</span>` : ''}</p>
        <h2 class="headline"><span class="badge">${icon(status)}</span>${esc(sum.headline)}</h2>
        <p class="detail">${esc(sum.detail)}${g.windows === false && !hasLevels ? ' Steam does not list it as a Windows game.' : ''}</p>
        ${issues}
        ${sum.storage_note ? `<p class="note-line">${esc(sum.storage_note)}</p>` : ''}
        ${notes.map(n => `<p class="note-line">${esc(n)}</p>`).join('')}
        ${actions}
      </div>
    </article>
    ${disHtml}
    ${table}`;
}

$('#game').addEventListener('click', async e => {
  const src = e.target.closest('[data-src]');
  if (src) { state.active = src.dataset.src; renderGame(); return; }
  if (e.target.id === 'retry' && state.current) {
    openGame(state.current.source, state.current.id, state.current.name, false, state.current.installed);
    return;
  }
  const act = e.target.closest('[data-act]');
  if (!act) return;
  const g = state.sources[state.active];
  if (act.dataset.act === 'want') {
    try {
      await post('/api/wanted/add', { source: g.source, ref: g.id, title: g.name });
      await loadState();
      renderGame();
    } catch (ex) { alert(ex.message); }
  } else if (act.dataset.act.startsWith('report')) {
    saveReport(g, act.dataset.act === 'report-svg' ? 'svg' : 'png', act);
  }
});
$('#game').addEventListener('change', e => {
  if (e.target.id !== 'nums') return;
  state.showNums = e.target.checked;
  $('.side').classList.toggle('show-nums', state.showNums);
});

/* ------------------------------------------------------------- reports */

function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

async function saveReport(g, kind, btn) {
  btn.disabled = true;
  try {
    const { svg } = await post('/api/report', {
      name: g.name, source_name: g.source_name || SOURCE_NAME[g.source], attribution: g.attribution || '',
      profile_label: profileLabel(), simulated: simulating(), summary: g.summary, levels: g.levels,
    });
    const base = (g.name || 'game').replace(/[^\w\- ]+/g, '').trim().slice(0, 60) || 'game';
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    if (kind === 'svg') return download(blob, `${base} - spec check.svg`);
    const url = URL.createObjectURL(blob);
    const img = new Image();
    await new Promise((ok, fail) => { img.onload = ok; img.onerror = fail; img.src = url; });
    const canvas = document.createElement('canvas');
    canvas.width = img.width * 2; canvas.height = img.height * 2;
    const ctx = canvas.getContext('2d');
    ctx.scale(2, 2);
    ctx.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
    canvas.toBlob(b => download(b, `${base} - spec check.png`), 'image/png');
  } catch (ex) {
    alert('The report could not be made: ' + (ex.message || 'image error'));
  } finally {
    btn.disabled = false;
  }
}

/* --------------------------------------------------------------- paste */

function renderPasteForm() {
  const box = $('#paste');
  box.hidden = false;
  box.innerHTML = `<h2>Paste requirements</h2>
    <p class="small muted">Copy them from a store page, a forum post or the box. If one block has "Minimum" and
      "Recommended" headings, it is split for you. Nothing is sent anywhere.</p>
    <div class="two">
      <label>Game name<input id="pa-title" maxlength="160" placeholder="Game name"></label>
      <label>Where they came from (optional)<input id="pa-attr" maxlength="300" placeholder="A link or a note, like 'back of the box'"></label>
    </div>
    <div class="two">
      <label>Minimum<textarea id="pa-min" rows="7" maxlength="6000" placeholder="OS: Windows 10&#10;Processor: Intel Core i5-8400&#10;Memory: 8 GB RAM&#10;Graphics: GTX 1060"></textarea></label>
      <label>Recommended (optional)<textarea id="pa-rec" rows="7" maxlength="6000"></textarea></label>
    </div>
    <div class="row-btns"><button class="btn primary" type="button" id="pa-read">Read the requirements</button>
      <button class="btn" type="button" id="pa-close">Close</button></div>
    <div id="pa-review"></div>`;
}

$('#paste-open').addEventListener('click', () => { renderPasteForm(); $('#pa-title').focus(); });

function renderPasteReview(levels) {
  const box = $('#pa-review');
  const lvKeys = ['minimum', 'recommended'].filter(k => levels[k]);
  if (!lvKeys.length) { box.innerHTML = '<p class="error small">No requirements were found in that text.</p>'; return; }
  box.innerHTML = `<h3>Check what was read</h3>
    <p class="small muted">Edit anything that is wrong. Fields marked "guessed" came from a line without a label.
      Empty fields are not checked.</p>
    <div class="paste-grid">${lvKeys.map(lv => `<fieldset data-level="${lv}"><legend>${lv === 'minimum' ? 'Minimum' : 'Recommended'}</legend>
      ${Object.keys(FIELD_LABEL).map(f => {
        const unsure = levels[lv].uncertain.includes(f);
        return `<label class="${unsure ? 'unsure' : ''}">${FIELD_LABEL[f]}${unsure ? ' <span class="pill warn">guessed</span>' : ''}
          <input data-field="${f}" maxlength="400" value="${esc(levels[lv].fields[f] || '')}"></label>`;
      }).join('')}
      ${levels[lv].unlabelled.length ? `<p class="small muted">Not used: ${esc(levels[lv].unlabelled.join(' / '))}</p>` : ''}
    </fieldset>`).join('')}</div>
    <div class="row-btns"><button class="btn primary" type="button" id="pa-compare">Compare</button>
      <button class="btn" type="button" id="pa-want">Save to wanted games</button></div>`;
  box.dataset.raw = JSON.stringify(Object.fromEntries(lvKeys.map(k => [k, levels[k].raw])));
}

function pastedLevels() {
  const raw = JSON.parse($('#pa-review').dataset.raw || '{}');
  const levels = {};
  for (const fs of document.querySelectorAll('#pa-review fieldset')) {
    const fields = {};
    for (const inp of fs.querySelectorAll('input[data-field]')) if (inp.value.trim()) fields[inp.dataset.field] = inp.value.trim();
    levels[fs.dataset.level] = { fields, raw: raw[fs.dataset.level] || '' };
  }
  return levels;
}

async function comparePasted() {
  const p = state.pasteLast;
  if (!p) return;
  const seq = ++state.req;
  state.current = { source: 'pasted', id: 'pasted', name: p.title };
  try {
    const res = await post('/api/paste/compare', { ...p, pc: pc() });
    if (seq !== state.req) return;
    state.sources = { pasted: res };
    state.active = 'pasted';
    renderGame();
    $('#game').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch (ex) {
    if (seq !== state.req) return;
    $('#game').innerHTML = `<div class="state"><strong>Could not compare</strong>${esc(ex.message)}</div>`;
  }
}

$('#paste').addEventListener('click', async e => {
  const id = e.target.id;
  if (id === 'pa-close') { $('#paste').hidden = true; return; }
  if (id === 'pa-read') {
    try {
      const data = await post('/api/paste/parse', { minimum: $('#pa-min').value, recommended: $('#pa-rec').value });
      renderPasteReview(data.levels);
    } catch (ex) { $('#pa-review').innerHTML = `<p class="error small">${esc(ex.message)}</p>`; }
  }
  if (id === 'pa-compare' || id === 'pa-want') {
    const title = $('#pa-title').value.trim() || 'Pasted requirements';
    const attribution = $('#pa-attr').value.trim();
    const levels = pastedLevels();
    if (id === 'pa-compare') {
      state.pasteLast = { title, attribution, levels };
      comparePasted();
    } else {
      try {
        await post('/api/wanted/add', { source: 'pasted', title, attribution, levels });
        await loadState();
        e.target.textContent = 'Saved';
        e.target.disabled = true;
      } catch (ex) { alert(ex.message); }
    }
  }
});

/* ------------------------------------------------- checking many games */

// Library games and wanted games share one runner and one results map.
function items() {
  const out = [];
  if (state.lib) {
    for (const g of state.lib.games) out.push({ id: g.game_id, title: g.title, providers: g.providers, game: g });
  }
  if (state.includeWanted) {
    for (const w of state.wanted) out.push({ id: 'w_' + w.id, title: w.title, providers: ['wanted'], wanted: w });
  }
  return out;
}

function outcomes() {
  const m = new Map();
  for (const [k, v] of state.out) m.set(k, L.outcome(v));
  return m;
}

function normalizeFilters() {
  state.filters = L.normalizeFilters(state.filters, items(), outcomes());
}

async function checkTargets(targets, after) {
  const run = ++state.run;
  state.busy = true;
  state.progress = { done: 0, total: targets.length };
  for (const t of targets) state.out.delete(t);
  normalizeFilters();
  after();
  const queue = [...targets];
  const worker = async () => {
    while (queue.length && run === state.run) {
      const target = queue.shift();
      let entry;
      try {
        entry = await api('/api/check', { target, ...pc() });
      } catch (e) {
        entry = { error: e.message };
      }
      if (run !== state.run) return;
      state.out.set(target, entry);
      state.progress.done++;
      after();
    }
  };
  await Promise.all([worker(), worker()]);
  if (run !== state.run) return;
  state.busy = false;
  after();
}

function stopChecks() {
  state.run++;
  state.busy = false;
}

/* ------------------------------------------------------------- library */

async function loadLibrary() {
  const box = $('#library');
  if (!state.lib) box.innerHTML = '<div class="state"><span class="spin"></span>Loading your games…</div>';
  try {
    state.lib = await api('/api/library');
    normalizeFilters();
    renderLibrary();
  } catch (e) {
    box.innerHTML = `<div class="state"><strong>The game list couldn't be read</strong>${esc(e.message)}</div>`;
  }
}

async function startScan() {
  try {
    state.scan = await post('/api/scan/start');
    renderLibrary();
    pollScan(state.scan.job);
  } catch (e) { alert(e.message); }
}

async function pollScan(job) {
  while (state.scan && state.scan.job === job && state.scan.running) {
    await new Promise(r => setTimeout(r, 400));
    try { state.scan = await api('/api/scan/status', { job }); } catch { break; }
    renderLibrary();
  }
  const s = state.scan;
  state.scan = null;
  await loadLibrary();
  if (s && s.error) alert('The scan stopped: ' + s.error);
}

const STATUS_TEXT = {
  ok: p => (p.count ? `${p.count} found` : 'no games found'),
  not_installed: () => 'not installed',
  error: () => 'failed',
  cancelled: () => 'cancelled',
  not_scanned: () => 'not scanned',
};

function providerChips(lib) {
  return `<div class="prov">${lib.providers.map(p => {
    const st = p.status === 'ok' ? (p.count ? 'pass' : 'unknown') : p.status === 'error' ? 'fail' : 'unknown';
    const kept = p.kept ? ` (showing ${p.kept} from the last scan)` : '';
    const msg = [p.message, kept].filter(Boolean).join(' ');
    // Installed programs and shortcuts mostly repeat launcher games; their
    // count is of possible finds, and only new ones reach the list or review.
    const heuristic = ['programs', 'shortcuts'].includes(p.id) && p.status === 'ok' && p.count;
    const text = heuristic ? `${p.count} looked at` : (STATUS_TEXT[p.status] || STATUS_TEXT.not_scanned)(p);
    return `<span class="chip s-${heuristic ? 'unknown' : st}" title="${esc(msg)}"><strong>${esc(p.name)}</strong> ${esc(text)}${esc(kept)}</span>`;
  }).join('')}</div>`;
}

function filterBar(c) {
  const f = state.filters;
  const groups = [['all', 'All'], ['met', GROUP_NAME.met], ['borderline', 'Borderline'], ['failing', 'Below'],
                  ['incomplete', 'Incomplete'], ['unchecked', 'Not checked']];
  const limits = Object.keys(c.limits);
  const provs = Object.keys(c.providers);
  const pname = id => (id === 'wanted' ? 'Wanted' : (state.providers.find(p => p.id === id) || {}).name || (id === 'manual' ? 'Added by you' : id));
  return `<div class="filters" role="group" aria-label="Filter by result">
      ${groups.filter(([k]) => k === 'all' || c[k]).map(([k, label]) =>
        `<button type="button" data-filter="${k}" aria-pressed="${k === f.group}">${label}${k === 'all' ? '' : ' ' + c[k]}</button>`).join('')}
    </div>
    <div class="filters selects">
      <label>Limiting part <select data-f="limit"><option value="any">Any</option>
        ${limits.map(l => `<option value="${l}" ${f.limit === l ? 'selected' : ''}>${PART_NAME[l] || l} (${c.limits[l]})</option>`).join('')}</select></label>
      <label>Launcher <select data-f="provider"><option value="all">All</option>
        ${provs.map(p => `<option value="${esc(p)}" ${f.provider === p ? 'selected' : ''}>${esc(pname(p))} (${c.providers[p]})</option>`).join('')}</select></label>
      <label class="toggle"><input type="checkbox" data-f="storage" ${f.storage ? 'checked' : ''} ${c.storage ? '' : 'disabled'}>Needs disk space (${c.storage})</label>
      <label class="toggle"><input type="checkbox" id="inc-wanted" ${state.includeWanted ? 'checked' : ''}>Include wanted games</label>
    </div>`;
}

function rowSummary(item, entry) {
  if (!entry) {
    const g = item.game;
    if (g) {
      const missing = g.present ? '' : ' Folder missing: it may have been moved or uninstalled.';
      return { badge: '', sub: [g.providers.map(pnameOf).join(', '), g.size_gb ? `${g.size_gb} GB` : ''].filter(Boolean).join(' · ') + missing };
    }
    return { badge: '', sub: 'Wanted' };
  }
  if (entry.error) return { badge: `<span class="lv s-unknown">${icon('unknown')}Lookup failed</span>`, sub: entry.error };
  if (entry.needs_choice) return { badge: `<span class="lv s-unknown">${icon('unknown')}Choose the game</span>`, sub: entry.how };
  const s = entry.result.summary;
  const st = TIER_STATUS[s.tier];
  const limits = (s.limits || []).map(l => PART_NAME[l] || l);
  return { badge: `<span class="lv s-${st}">${icon(st)}${esc(s.headline)}</span>`,
           sub: limits.length ? `Limited by: ${list(limits)}.` : s.detail };
}

function pnameOf(id) {
  if (id === 'manual') return 'Added by you';
  if (id === 'wanted') return 'Wanted';
  return (state.providers.find(p => p.id === id) || {}).name || id;
}

function installLine(inst) {
  const play = inst.launch && inst.present
    ? `<button class="btn small" type="button" data-play="${esc(inst.install_id)}">Play</button>` : '';
  const setExe = `<button class="link small" type="button" data-setexe="${esc(inst.install_id)}">${inst.launch ? 'Change exe' : 'Set exe'}</button>`;
  return `<li><span><strong>${esc(pnameOf(inst.provider))}</strong>${inst.edition ? ` · ${esc(inst.edition)}` : ''}
      ${inst.present ? '' : '<span class="pill warn">folder missing</span>'}
      <span class="path">${esc(inst.path || '')}</span></span>
      <span class="row-btns">${play}${inst.provider === 'steam' || inst.provider === 'epic' || inst.provider === 'ubisoft' || inst.provider === 'xbox' ? '' : setExe}</span></li>`;
}

function detailHtml(item, entry) {
  const parts = [];
  if (entry && entry.result) {
    const r = entry.result;
    const id = r.identity || {};
    parts.push(`<p class="small">Requirements: ${esc(r.source_name)}${r.name && r.name !== item.title ? ` page "${esc(r.name)}"` : ''}.
      ${esc(id.how || '')} ${item.game ? '<button class="link small" type="button" data-choose>Choose the correct game</button>' : ''}</p>`);
    if (r.fallback_note) parts.push(`<p class="small muted">${esc(r.fallback_note)}</p>`);
    if (r.summary.issues.length) parts.push(`<ul class="issues-mini">${r.summary.issues.map(i => `<li>${icon(i.status)}<span>${esc(i.text)}</span></li>`).join('')}</ul>`);
    parts.push(`<button class="btn small" type="button" data-full>Full comparison</button>`);
  } else if (entry && entry.needs_choice) {
    parts.push(`<p class="small">${esc(entry.how)} ${entry.errors && entry.errors.length ? esc(entry.errors.join(' ')) : ''}</p>`);
  }
  if (state.choosing === item.id || (entry && entry.needs_choice && state.choosing !== 'closed:' + item.id)) {
    parts.push(chooserHtml(item, entry));
  }
  if (item.game) {
    parts.push(`<ul class="installs">${item.game.installs.map(installLine).join('')}</ul>`);
    parts.push(`<div class="row-btns"><button class="link small" type="button" data-hide>Hide this game</button></div>`);
  } else if (item.wanted) {
    parts.push(`<div class="row-btns"><button class="link small" type="button" data-unwant="${esc(item.wanted.id)}">Remove from wanted</button></div>`);
  }
  return `<div class="lib-detail">${parts.join('')}</div>`;
}

function chooserHtml(item, entry) {
  const cands = (entry && entry.candidates) || [];
  return `<div class="chooser" data-for="${esc(item.id)}">
    <p class="small"><strong>Choose the correct game</strong> for "${esc(item.title)}". Your choice is saved on this PC.</p>
    <div class="two"><input class="ch-q" value="${esc(item.game ? item.game.title_base : item.title)}" aria-label="Search for the game">
      <button class="btn small" type="button" data-chsearch>Search</button></div>
    <ul class="ch-list">${cands.map(c => chooseLine(c)).join('')}</ul>
  </div>`;
}

function chooseLine(c) {
  return `<li><button class="link" type="button" data-pickmap data-source="${esc(c.source)}" data-id="${esc(c.id)}" data-name="${esc(c.name)}">${esc(c.name)}</button>
    <span class="small muted">${esc(SOURCE_NAME[c.source] || c.source)}</span></li>`;
}

function renderLibrary() {
  const box = $('#library'), lib = state.lib;
  if (!lib) return;
  const all = items();
  const outs = outcomes();
  const c = L.counts(all, outs);
  const f = state.filters;
  const scanning = state.scan && state.scan.running;
  const scanBtn = scanning
    ? `<button class="btn" type="button" id="scan-cancel">Cancel scan</button>`
    : `<button class="btn ${lib.scanned ? '' : 'primary'}" type="button" id="scan">${lib.scanned ? 'Scan again' : 'Find my games'}</button>`;
  const checkBtn = state.busy
    ? `<button class="btn" type="button" id="check-stop">Stop (${state.progress.done} of ${state.progress.total})</button>`
    : all.length ? `<button class="btn primary" type="button" id="check-all">${state.out.size ? 'Check again' : `Check all ${all.length}`}</button>` : '';
  const when = lib.scanned_at ? new Date(lib.scanned_at * 1000).toLocaleString() : '';

  let body = '';
  if (!lib.scanned && !lib.manual.length) {
    body = `<div class="state"><strong>No scan yet</strong>"Find my games" reads the install records of Steam, Epic, GOG,
      the Xbox app, EA, Ubisoft, Battle.net and itch.io, plus installed programs and Start menu shortcuts. Nothing is run
      and no drive is searched from top to bottom.</div>`;
  } else if (!all.length) {
    body = `<div class="state"><strong>No games found</strong>The launchers below were checked. Add a folder or an exe if a game is missing.</div>`;
  } else {
    const shown = all.filter(it => L.matches(it, outs.get(it.id), f));
    const order = ['met', 'borderline', 'failing', 'incomplete', 'unchecked'];
    const by = {};
    for (const it of shown) { const o = outs.get(it.id); (by[o ? o.group : 'unchecked'] ||= []).push(it); }
    body = order.filter(k => by[k]).map(k => `<h2 class="grp s-${GROUP_STATUS[k] || 'unknown'}">${GROUP_NAME[k]} <span class="muted">${by[k].length}</span></h2>
      <div class="lib">${by[k].map(it => {
        const entry = state.out.get(it.id);
        const r = rowSummary(it, entry);
        const open = state.open === it.id;
        const play = it.game && it.game.installs.find(i => i.launch && i.present);
        return `<div class="lib-item ${open ? 'open' : ''}" data-target="${esc(it.id)}">
          <div class="lib-row-wrap">
            <button class="lib-row" type="button" aria-expanded="${open}"><span class="name">${esc(it.title)}</span>${r.badge}<span class="sub">${esc(r.sub)}</span></button>
            ${play ? `<button class="btn small play" type="button" data-play="${esc(play.install_id)}" title="Start through ${esc(pnameOf(play.provider))}">Play</button>` : ''}
          </div>
          ${open ? detailHtml(it, entry) : ''}</div>`;
      }).join('')}</div>`).join('') || '<p class="state">Nothing matches these filters.</p>';
  }

  const review = lib.review.length ? `<details class="panel review"><summary>${lib.review.length} possible game${lib.review.length > 1 ? 's' : ''} to review</summary>
      <p class="small muted">These were found through installed programs, shortcuts or your folders, and might not be games.</p>
      <ul>${lib.review.map(i => `<li><span><strong>${esc(i.title)}</strong> <span class="small muted">${esc(i.evidence)}</span>
        <span class="path">${esc(i.path || '')}</span></span>
        <span class="row-btns"><button class="btn small" type="button" data-decide="accept" data-id="${esc(i.install_id)}">It's a game</button>
        <button class="btn small" type="button" data-decide="ignore" data-id="${esc(i.install_id)}">Ignore</button></span></li>`).join('')}</ul></details>` : '';

  const hidden = lib.hidden.length ? `<details class="panel"><summary>${lib.hidden.length} hidden</summary><ul>
      ${lib.hidden.map(h => `<li><span>${esc(h.title)}</span><button class="link small" type="button" data-unhide="${esc(h.game_id)}">Show again</button></li>`).join('')}</ul></details>` : '';

  const add = `<details class="panel"><summary>Add a game the scan missed</summary>
      <form id="add-folder" class="two"><input name="path" placeholder="A folder of games, like D:\\Games" aria-label="Folder path">
        <button class="btn small" type="submit">Add folder</button></form>
      ${lib.folders.length ? `<ul>${lib.folders.map(fd => `<li><span class="path">${esc(fd)}</span><button class="link small" type="button" data-rmfolder="${esc(fd)}">Remove</button></li>`).join('')}</ul>
        <p class="small muted">Folders are read at the next scan, one level deep.</p>` : ''}
      <form id="add-exe" class="two"><input name="exe" placeholder="Full path of the game's .exe" aria-label="Exe path">
        <input name="title" placeholder="Name (optional)" aria-label="Game name">
        <button class="btn small" type="submit">Add game</button></form>
      ${lib.manual.length ? `<ul>${lib.manual.map(m => `<li><span>${esc(m.title)} <span class="path">${esc(m.exe)}</span></span><button class="link small" type="button" data-rmmanual="${esc(m.id)}">Remove</button></li>`).join('')}</ul>` : ''}
      <p id="add-error" class="error small" hidden></p></details>`;

  box.innerHTML = `
    <div class="lib-head">
      <div><h1>My games</h1>
        <p class="muted">${lib.games.length} installed${when ? `, last scanned ${esc(when)}` : ''}. What can you play tonight?
          Check them all, then open one for details or press Play.</p></div>
      <div class="row-btns">${scanBtn}${checkBtn}</div>
    </div>
    ${scanning ? `<div class="progress"><div style="width:${(state.scan.done / Math.max(state.scan.total, 1) * 100).toFixed(1)}%"></div></div>
      <p class="small muted">Scanning ${esc(state.scan.provider || '')}… ${state.scan.items} records read.</p>` : ''}
    ${state.busy ? `<div class="progress"><div style="width:${(state.progress.done / Math.max(state.progress.total, 1) * 100).toFixed(1)}%"></div></div>` : ''}
    ${lib.scanned ? providerChips(lib) : ''}
    ${lib.recovered && lib.recovered.length ? `<p class="small error">Some saved data could not be read and was reset: ${esc(lib.recovered.join(', '))}.</p>` : ''}
    ${review}
    ${all.length ? filterBar(c) : ''}
    ${body}
    ${hidden}${add}`;
}

$('#library').addEventListener('click', async e => {
  const t = e.target;
  if (t.id === 'scan') return startScan();
  if (t.id === 'scan-cancel' && state.scan) { await post('/api/scan/cancel', { job: state.scan.job }).catch(() => {}); return; }
  if (t.id === 'check-all') return checkTargets(items().map(i => i.id), renderLibrary);
  if (t.id === 'check-stop') { stopChecks(); return renderLibrary(); }
  const f = t.closest('[data-filter]');
  if (f) { state.filters.group = f.dataset.filter; return renderLibrary(); }
  const play = t.closest('[data-play]');
  if (play) return launch(play);
  if (t.dataset.decide) { await act(post('/api/library/decide', { install_id: t.dataset.id, decision: t.dataset.decide })); return; }
  if (t.dataset.unhide) { await act(post('/api/library/hide', { game_id: t.dataset.unhide, hidden: false })); return; }
  if (t.dataset.rmfolder) { await act(post('/api/library/folders', { action: 'remove', path: t.dataset.rmfolder })); return; }
  if (t.dataset.rmmanual) { await act(post('/api/library/manual', { action: 'remove', id: t.dataset.rmmanual })); return; }
  if (t.dataset.unwant) { await post('/api/wanted/remove', { id: t.dataset.unwant }).catch(() => {}); await loadState(); return renderLibrary(); }
  if (t.dataset.setexe) {
    const exe = prompt('Full path of the game\'s .exe (leave empty to clear):');
    if (exe !== null) await act(post('/api/library/exe', { install_id: t.dataset.setexe, exe: exe.trim() }));
    return;
  }
  const item = t.closest('.lib-item');
  if (!item) return;
  const target = item.dataset.target;
  const it = items().find(i => i.id === target);
  if (t.closest('.lib-row')) { state.open = state.open === target ? null : target; return renderLibrary(); }
  if (t.hasAttribute('data-hide')) { await act(post('/api/library/hide', { game_id: target, hidden: true })); return; }
  if (t.hasAttribute('data-full')) {
    const r = state.out.get(target).result;
    if (r.source === 'pasted') { state.pasteLast = { title: it.title, levels: it.wanted.levels, attribution: it.wanted.attribution || '' }; showTab('check'); return comparePasted(); }
    return openGame(r.source, r.id, it.title, false, !!it.game);
  }
  if (t.hasAttribute('data-choose')) { state.choosing = target; return renderLibrary(); }
  if (t.hasAttribute('data-chsearch')) {
    const ul = item.querySelector('.ch-list');
    ul.innerHTML = '<li class="small muted"><span class="spin"></span>Searching…</li>';
    try {
      const data = await api('/api/search', { q: item.querySelector('.ch-q').value });
      ul.innerHTML = data.results.map(chooseLine).join('') || '<li class="small muted">Nothing found.</li>';
    } catch (ex) { ul.innerHTML = `<li class="small error">${esc(ex.message)}</li>`; }
    return;
  }
  if (t.hasAttribute('data-pickmap')) {
    try {
      await post('/api/mapping', { key: it.game.key, source: t.dataset.source, id: t.dataset.id, name: t.dataset.name });
      state.choosing = null;
      checkTargets([target], renderLibrary);
    } catch (ex) { alert(ex.message); }
  }
});

$('#library').addEventListener('change', e => {
  const k = e.target.dataset.f;
  if (k) {
    state.filters[k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    return renderLibrary();
  }
  if (e.target.id === 'inc-wanted') {
    state.includeWanted = e.target.checked;
    normalizeFilters();
    renderLibrary();
  }
});

$('#library').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.target;
  const err = $('#add-error');
  try {
    if (form.id === 'add-folder') await post('/api/library/folders', { action: 'add', path: form.path.value });
    if (form.id === 'add-exe') await post('/api/library/manual', { action: 'add', exe: form.exe.value, title: form.title.value });
    await loadLibrary();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

async function act(promise) {
  try { await promise; } catch (ex) { alert(ex.message); }
  await loadLibrary();
}

async function launch(btn) {
  btn.disabled = true;
  try {
    const r = await post('/api/launch', { install_id: btn.dataset.play });
    btn.textContent = 'Starting…';
    btn.title = r.how;
    setTimeout(() => { btn.textContent = 'Play'; btn.disabled = false; }, 4000);
  } catch (ex) {
    alert(ex.message);
    btn.disabled = false;
  }
}

/* -------------------------------------------------------------- wanted */

async function loadState() {
  try {
    const s = await api('/api/state');
    state.profiles = s.profiles;
    state.wanted = s.wanted;
    state.providers = s.providers;
    state.categories = s.categories;
    renderProfileSelect();
    if (s.recovered.length) {
      const n = $('#notice');
      n.hidden = false;
      n.innerHTML = `<span>Some saved data could not be read and was set aside: ${esc(s.recovered.join(', '))}. The app started those over.</span>`;
    }
  } catch { /* the page still works without saved data */ }
}

function renderWanted() {
  const box = $('#wanted');
  const ws = state.wanted;
  if (!ws.length) {
    box.innerHTML = `<div class="lib-head"><div><h1>Wanted games</h1></div></div>
      <div class="state"><strong>Nothing saved yet</strong>Look up a game under "Check a game" and choose "Save to wanted games",
      or paste requirements from anywhere and save them.</div>`;
    return;
  }
  const outs = outcomes();
  box.innerHTML = `<div class="lib-head">
      <div><h1>Wanted games</h1><p class="muted">Games you are thinking about, installed or not. They are included in upgrade comparisons.</p></div>
      <div class="row-btns">${state.busy ? `<button class="btn" type="button" id="w-stop">Stop (${state.progress.done} of ${state.progress.total})</button>`
        : `<button class="btn primary" type="button" id="w-check">Check all ${ws.length}</button>`}</div></div>
    <div class="lib">${ws.map(w => {
      const id = 'w_' + w.id;
      const entry = state.out.get(id);
      const r = rowSummary({ id, title: w.title, wanted: w }, entry);
      const o = outs.get(id);
      const src = w.source === 'pasted' ? `Pasted${w.attribution ? ': ' + w.attribution : ''}` : SOURCE_NAME[w.source];
      return `<div class="lib-item" data-target="${esc(id)}"><div class="lib-row-wrap">
        <button class="lib-row" type="button" data-wopen="${esc(id)}"><span class="name">${esc(w.title)}</span>${r.badge}
          <span class="sub">${esc(entry ? r.sub : src)}${o && o.storage ? ' Needs more free disk space.' : ''}</span></button>
        <button class="link small" type="button" data-wrm="${esc(w.id)}">Remove</button></div></div>`;
    }).join('')}</div>`;
}

$('#wanted').addEventListener('click', async e => {
  const t = e.target;
  if (t.id === 'w-check') return checkTargets(state.wanted.map(w => 'w_' + w.id), () => { renderWanted(); if (state.lib) renderLibrary(); });
  if (t.id === 'w-stop') { stopChecks(); return renderWanted(); }
  if (t.dataset.wrm) { await post('/api/wanted/remove', { id: t.dataset.wrm }).catch(() => {}); await loadState(); return renderWanted(); }
  const open = t.closest('[data-wopen]');
  if (open) {
    const w = state.wanted.find(x => 'w_' + x.id === open.dataset.wopen);
    if (!w) return;
    if (w.source === 'pasted') { state.pasteLast = { title: w.title, levels: w.levels, attribution: w.attribution || '' }; showTab('check'); comparePasted(); }
    else openGame(w.source, w.ref, w.title);
  }
});

/* ---------------------------------------------------- upgrades/profiles */

function profileForm(p) {
  return `<form id="prof-form" class="panel" autocomplete="off">
    <h2>${p.id ? 'Edit profile' : 'New profile'}</h2>
    <p class="small muted">A profile is a set of parts to check games against: another PC, a laptop or a build you are planning.
      Blank parts use this PC's detected hardware.</p>
    <input type="hidden" name="id" value="${esc(p.id || '')}">
    <div class="two">
      <label>Name<input name="name" maxlength="60" required value="${esc(p.name || '')}" placeholder="Planned build"></label>
      <label>Kind<select name="kind">${Object.entries(KIND_NAME).map(([k, v]) => `<option value="${k}" ${p.kind === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    </div>
    <div class="two">
      <label>Processor<input name="cpu" list="dl-pcpu" maxlength="120" value="${esc(p.cpu || '')}" placeholder="Same as this PC"><datalist id="dl-pcpu"></datalist></label>
      <label>Graphics card<input name="gpu" list="dl-pgpu" maxlength="120" value="${esc(p.gpu || '')}" placeholder="Same as this PC"><datalist id="dl-pgpu"></datalist></label>
    </div>
    <div class="two">
      <label>Memory (GB)<input name="ram" type="number" min="1" max="2048" step="1" value="${esc(p.ram || '')}" placeholder="Same as this PC"></label>
      <label>Video memory (GB)<input name="vram" type="number" min="0.5" max="256" step="0.5" value="${esc(p.vram || '')}" placeholder="From the card"></label>
    </div>
    <p id="prof-error" class="error small" hidden></p>
    <div class="row-btns"><button class="btn primary" type="submit">Save profile</button><button class="btn" type="button" id="prof-cancel">Cancel</button></div>
  </form>`;
}

function renderPlan() {
  const box = $('#plan');
  const P = state.plan;
  const profs = state.profiles;
  const targets = planTargets();
  const rows = targets.map(t => P.rows.get(t.id)).filter(Boolean);
  const picked = profs.filter(p => P.picked.has(p.id));
  let html = `<div class="lib-head"><div><h1>Profiles and upgrades</h1>
      <p class="muted">Save other PCs or planned parts, then see what each would change across your installed and wanted games,
        compared with this PC as detected.</p></div>
      <div class="row-btns"><button class="btn" type="button" id="prof-new">New profile</button></div></div>`;
  if (P.editing) html += profileForm(P.editing);
  html += profs.length ? `<div class="lib profiles">${profs.map(p => `<div class="prof-row">
      <label class="toggle"><input type="checkbox" data-pick="${esc(p.id)}" ${P.picked.has(p.id) ? 'checked' : ''}>
        <span><strong>${esc(p.name)}</strong> <span class="pill">${esc(KIND_NAME[p.kind] || p.kind)}, simulated</span><br>
        <span class="small muted">${esc([p.cpu, p.gpu, p.ram && p.ram + ' GB memory', p.vram && p.vram + ' GB video memory'].filter(Boolean).join(' · '))}</span></span></label>
      <span class="row-btns"><button class="link small" type="button" data-edit="${esc(p.id)}">Edit</button>
        <button class="link small" type="button" data-del="${esc(p.id)}">Delete</button></span></div>`).join('')}</div>`
    : '<div class="state"><strong>No profiles yet</strong>Create one, or use "Save as profile" under "Details and upgrades".</div>';

  if (profs.length) {
    const canRun = picked.length && targets.length;
    html += `<div class="row-btns plan-run">
      ${P.busy ? `<button class="btn" type="button" id="plan-stop">Stop (${rows.length} of ${targets.length})</button>`
        : `<button class="btn primary" type="button" id="plan-go" ${canRun ? '' : 'disabled'}>Compare ${picked.length || ''} across ${targets.length} game${targets.length === 1 ? '' : 's'}</button>`}
      <span class="small muted">${targets.length ? '' : 'Scan "My games" or save wanted games first. '}${picked.length ? '' : 'Tick up to five profiles to compare.'}</span></div>`;
  }
  if (rows.length && picked.length) html += planResults(picked, targets, rows);
  box.innerHTML = html;
  if (P.editing) {
    hardwareSuggest(box.querySelector('[name=cpu]'), 'cpu');
    hardwareSuggest(box.querySelector('[name=gpu]'), 'gpu');
  }
}

function planTargets() {
  const out = [];
  if (state.lib) for (const g of state.lib.games) out.push({ id: g.game_id, title: g.title, kind: 'Installed' });
  for (const w of state.wanted) out.push({ id: 'w_' + w.id, title: w.title, kind: 'Wanted' });
  return out;
}

function planResults(picked, targets, rows) {
  const cats = state.categories || [];
  const catName = Object.fromEntries(cats);
  const summary = picked.map(p => {
    const t = L.planTotals(rows, p.id);
    const lim = Object.entries(t.limits).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${PART_NAME[k] || k} (${n})`);
    return `<div class="plan-card"><h3>${esc(p.name)}</h3><ul class="small">
      <li class="s-pass">${t.newly_recommended} newly meet the recommended specs</li>
      <li class="s-pass">${t.newly_minimum} newly meet the minimum specs</li>
      ${t.now_met ? `<li>${t.now_met} meet the specs that could not be fully checked before</li>` : ''}
      <li class="s-fail">${t.still_below} below the specs, as on this PC or unclear before</li>
      <li class="s-close">${t.borderline} borderline</li>
      ${t.worse ? `<li class="s-fail">${t.worse} worse than this PC</li>` : ''}
      <li class="s-unknown">${t.incomplete + t.not_checked} incomplete or not checked (not counted as gains)</li>
      <li class="muted">${t.no_change} no change</li></ul>
      ${lim.length ? `<p class="small">Still holding games back: ${esc(lim.join(', '))}.</p>` : ''}</div>`;
  }).join('');
  const word = s => (s ? LEVEL_WORD[s] || s : 'not listed');
  const cell = st => st ? `<span class="s-${GROUP_STATUS[st.group] || 'unknown'}">${esc(st.headline || '')}</span>
      <div class="small muted">Min: ${word(st.minimum)} · Rec: ${word(st.recommended)}${st.limits.length ? ` · Limited by ${esc(list(st.limits.map(l => PART_NAME[l] || l)))}` : ''}</div>` : '';
  const table = `<table class="plan-table"><thead><tr><th scope="col">Game</th><th scope="col">This PC</th>
      ${picked.map(p => `<th scope="col">${esc(p.name)}</th>`).join('')}</tr></thead><tbody>
      ${targets.map(t => {
        const r = state.plan.rows.get(t.id);
        if (!r) return '';
        if (r.error || r.needs_choice) {
          return `<tr><th scope="row">${esc(t.title)}<div class="small muted">${t.kind}</div></th><td colspan="${picked.length + 1}" class="muted small">${esc(r.error || 'Needs "Choose the correct game" in My games first.')}</td></tr>`;
        }
        return `<tr><th scope="row">${esc(t.title)}<div class="small muted">${t.kind}</div></th>
          <td data-label="This PC">${cell(r.states.real)}</td>
          ${picked.map(p => `<td data-label="${esc(p.name)}">${cell(r.states[p.id])}<div class="small">${esc(catName[r.changes[p.id]] || '')}</div></td>`).join('')}</tr>`;
      }).join('')}</tbody></table>`;
  return `<div class="plan-cards">${summary}</div><section class="side">${table}</section>`;
}

async function runPlan() {
  const P = state.plan;
  const run = ++P.run;
  P.busy = true;
  P.rows.clear();
  const pids = ['real', ...state.profiles.filter(p => P.picked.has(p.id)).map(p => p.id)];
  const queue = planTargets().map(t => t.id);
  renderPlan();
  const worker = async () => {
    while (queue.length && run === P.run) {
      const target = queue.shift();
      let r;
      try { r = await api('/api/plan', { target, profiles: pids.join(',') }); } catch (e) { r = { error: e.message }; }
      if (run !== P.run) return;
      P.rows.set(target, r);
      renderPlan();
    }
  };
  await Promise.all([worker(), worker()]);
  if (run !== P.run) return;
  P.busy = false;
  renderPlan();
}

$('#plan').addEventListener('click', async e => {
  const t = e.target, P = state.plan;
  if (t.id === 'prof-new') { P.editing = { name: '', kind: 'planned' }; return renderPlan(); }
  if (t.id === 'prof-cancel') { P.editing = null; return renderPlan(); }
  if (t.id === 'plan-go') return runPlan();
  if (t.id === 'plan-stop') { P.run++; P.busy = false; return renderPlan(); }
  if (t.dataset.edit) { P.editing = { ...profileById(t.dataset.edit) }; return renderPlan(); }
  if (t.dataset.del) {
    if (!confirm('Delete this profile?')) return;
    await post('/api/profiles/delete', { id: t.dataset.del }).catch(() => {});
    P.picked.delete(t.dataset.del);
    const wasActive = state.profile === t.dataset.del;
    await loadState();
    if (wasActive) { state.profile = 'real'; renderSim(); refreshAnswers(); }
    return renderPlan();
  }
});

$('#plan').addEventListener('change', e => {
  const id = e.target.dataset.pick;
  if (!id) return;
  const P = state.plan;
  if (e.target.checked && P.picked.size >= 5) { e.target.checked = false; return; }
  if (e.target.checked) P.picked.add(id); else P.picked.delete(id);
  renderPlan();
});

$('#plan').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target;
  const err = $('#prof-error');
  const next = { cpu: f.cpu.value.trim(), gpu: f.gpu.value.trim() };
  try {
    await placeParts(next);
    const saved = await post('/api/profiles/save', {
      id: f.id.value || undefined, name: f.name.value, kind: f.kind.value,
      cpu: next.cpu, gpu: next.gpu, ram: f.ram.value, vram: f.vram.value,
    });
    state.plan.editing = null;
    state.plan.picked.add(saved.id);
    await loadState();
    if (state.profile === saved.id) refreshAnswers();
    renderPlan();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

/* ---------------------------------------------------------------- tabs */

function showTab(name) {
  for (const t of ['check', 'library', 'wanted', 'plan']) {
    $('#tab-' + t).hidden = t !== name;
    $('#tabbtn-' + t).setAttribute('aria-selected', String(t === name));
  }
  if (name === 'library') loadLibrary();
  if (name === 'wanted') renderWanted();
  if (name === 'plan') {
    renderPlan();
    if (!state.lib) api('/api/library').then(l => { state.lib = l; renderPlan(); }).catch(() => {});
  }
}
$('.tabs').addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (b) showTab(b.dataset.tab);
});

/* ---------------------------------------------------------------- init */

renderChips();
loadState();
loadSpecs().then(() => {
  const m = location.hash.match(/^#(steam|pcgw)\/(.+)$/);
  if (m) openGame(m[1], decodeURIComponent(m[2]), '', true);
});
