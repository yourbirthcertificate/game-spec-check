'use strict';

const $ = (sel, root = document) => root.querySelector(sel);

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
const TIER_STATUS = { great: 'pass', ok: 'pass', close: 'close', fail: 'fail', unknown: 'unknown' };
const LEVEL_WORD = { pass: 'met', close: 'close', fail: 'not met', unknown: 'unclear' };
const SOURCE_NAME = { steam: 'Steam', pcgw: 'PCGamingWiki' };
const ROW_ORDER = ['cpu', 'gpu', 'ram', 'vram', 'os', 'directx', 'storage'];

const state = {
  specs: null,
  override: { cpu: '', gpu: '', ram: '' },
  current: null,        // {source, id, name}
  sources: {},          // source -> game result
  active: null,
  pcgwPending: false,
  req: 0,
  showNums: false,
  lib: null,
  libResults: new Map(),
  libFilter: 'all',
  libRun: 0,
  libBusy: false,
};

const icon = k => `<svg class="i s-${k}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[k] || ICONS.unknown}</svg>`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = n => Number(n).toLocaleString('en-US');
const times = r => (r >= 10 ? '10×+' : r >= 3 ? Math.round(r) + '×' : r.toFixed(1) + '×');

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
  let res;
  try {
    res = await fetch(url);
  } catch {
    throw new Error('The app has stopped responding. Start it again and reload this page.');
  }
  const data = await res.json().catch(() => ({ error: 'The app sent back something unreadable.' }));
  if (!res.ok) throw new Error(data.error || 'That request failed.');
  return data;
}

const overrides = () => ({ ...state.override });
const simulating = () => Object.values(state.override).some(Boolean);

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

function renderSpecs() {
  const s = state.specs;
  const cpu = s.cpu, gpu = s.gpu;
  const winName = (s.os.name || 'Windows').replace(/\s+(Home|Pro|Enterprise|Education).*$/i, '').replace(/\s+\d+-bit$/i, '');
  const bits = [tidy(cpu.name), gpu ? shortGpu(gpu.name) : 'no graphics card found',
    s.ram_gb ? `${s.ram_gb} GB memory` : null, winName].filter(Boolean);
  $('#pc-summary').innerHTML = '<span class="tag">This PC</span>' +
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
      <div class="sub">${gpu.vram_gb ? esc(gpu.vram_gb + ' GB of video memory. ') : ''}${esc(s.gpu_reason)}</div>`;
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
      ${s.directx ? `<div class="sub">DirectX ${esc(s.directx)}</div>` : ''}</div></div>
    <div class="spec"><div class="k">Free space</div><div>
      ${s.drives.map(d => `<div class="v">${esc(Math.round(d.free_gb))} GB free on ${esc(d.id)} <span class="sub">of ${esc(Math.round(d.size_gb))} GB</span></div>`).join('') || '<div class="sub">Unknown</div>'}
    </div></div>
    ${screen ? `<div class="spec"><div class="k">Screen</div><div class="v">${esc(screen)}</div></div>` : ''}
    ${s.system ? `<div class="spec"><div class="k">Model</div><div class="v">${esc(s.system)}</div></div>` : ''}`;
  $('#pc-body').innerHTML = html;
}

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

function refreshAnswers() {
  state.libRun++;
  state.libBusy = false;
  state.libResults.clear();
  if (state.lib) renderLibrary();
  if (state.current) openGame(state.current.source, state.current.id, state.current.name, true);
}

function renderSim() {
  const el = $('#sim');
  if (!simulating()) { el.hidden = true; return; }
  const o = state.override;
  const parts = [o.cpu, o.gpu && tidy(o.gpu), o.ram && `${o.ram} GB of memory`].filter(Boolean);
  const list = parts.length > 1 ? parts.slice(0, -1).join(', ') + ' and ' + parts.at(-1) : parts[0];
  el.hidden = false;
  el.innerHTML = `<span>Answers below assume this PC had ${esc(list)}.</span>
    <button class="btn" type="button" id="sim-reset">Back to my real PC</button>`;
}

function resetOverride() {
  $('#wi-cpu').value = ''; $('#wi-gpu').value = ''; $('#wi-ram').value = '';
  $('#wi-error').hidden = true;
  setOverride({ cpu: '', gpu: '', ram: '' });
}
$('#sim').addEventListener('click', e => { if (e.target.id === 'sim-reset') resetOverride(); });
$('#wi-reset').addEventListener('click', resetOverride);

for (const kind of ['cpu', 'gpu']) {
  let timer;
  $('#wi-' + kind).addEventListener('input', e => {
    clearTimeout(timer);
    const q = e.target.value.trim();
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      try {
        const data = await api('/api/hardware', { kind, q });
        $('#dl-' + kind).innerHTML = data.results.map(r => `<option value="${esc(r.name)}"></option>`).join('');
      } catch { /* suggestions are optional */ }
    }, 200);
  });
}

$('#whatif-form').addEventListener('submit', async e => {
  e.preventDefault();
  const err = $('#wi-error');
  err.hidden = true;
  const next = { cpu: $('#wi-cpu').value.trim(), gpu: $('#wi-gpu').value.trim(), ram: $('#wi-ram').value };
  try {
    for (const kind of ['cpu', 'gpu']) {
      if (!next[kind]) continue;
      const data = await api('/api/hardware', { kind, q: next[kind] });
      if (!data.reading) {
        throw new Error(`Couldn't place "${next[kind]}". Pick one of the suggestions that appear as you type.`);
      }
      // Show the part's proper name ("gtx 1050 ti" -> "GeForce GTX 1050 Ti").
      next[kind] = data.reading.matched || next[kind];
      $('#wi-' + kind).value = next[kind];
    }
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
    return;
  }
  setOverride(next);
});

/* -------------------------------------------------------------- search */

const qEl = $('#q'), resEl = $('#results');
let searchTimer, searchSeq = 0, hits = [], activeIdx = -1;

function closeResults() {
  resEl.hidden = true;
  qEl.setAttribute('aria-expanded', 'false');
  activeIdx = -1;
}

function showResults(html) {
  resEl.innerHTML = html;
  resEl.hidden = false;
  qEl.setAttribute('aria-expanded', 'true');
}

function renderHits() {
  if (!hits.length) return showResults('<li class="empty">Nothing by that name. Try fewer words.</li>');
  showResults(hits.map((h, i) => `
    <li role="option" id="hit-${i}" data-i="${i}" aria-selected="${i === activeIdx}">
      ${h.image ? `<img src="${esc(h.image)}" alt="" loading="lazy">` : '<span class="noimg"></span>'}
      <span class="name">${esc(h.name)}</span>
      <span class="from">${SOURCE_NAME[h.source]}</span>
    </li>`).join(''));
}

async function doSearch(term) {
  const seq = ++searchSeq;
  showResults('<li class="empty"><span class="spin"></span>Searching…</li>');
  try {
    const data = await api('/api/search', { q: term });
    if (seq !== searchSeq) return;
    hits = data.results;
    activeIdx = -1;
    renderHits();
  } catch (e) {
    if (seq !== searchSeq) return;
    hits = [];
    showResults(`<li class="empty error">${esc(e.message)}</li>`);
  }
}

qEl.addEventListener('input', () => {
  clearTimeout(searchTimer);
  const term = qEl.value.trim();
  if (term.length < 2) { searchSeq++; hits = []; return closeResults(); }
  searchTimer = setTimeout(() => doSearch(term), 280);
});
qEl.addEventListener('focus', () => { if (hits.length && qEl.value.trim().length >= 2) renderHits(); });
qEl.addEventListener('keydown', e => {
  if (e.key === 'Escape') return closeResults();
  if (resEl.hidden || !hits.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    activeIdx = (activeIdx + (e.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
    renderHits();
    const el = $('#hit-' + activeIdx);
    if (el) el.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    pick(hits[Math.max(activeIdx, 0)]);
  }
});
resEl.addEventListener('click', e => {
  const li = e.target.closest('li[data-i]');
  if (li) pick(hits[+li.dataset.i]);
});
document.addEventListener('click', e => { if (!e.target.closest('.search')) closeResults(); });

function pick(hit) {
  if (!hit) return;
  closeResults();
  qEl.value = hit.name;
  openGame(hit.source, hit.id, hit.name);
}

/* ------------------------------------------------- popular and recent */

function loadRecent() {
  try {
    const list = JSON.parse(localStorage.getItem('recent') || '[]');
    return Array.isArray(list) ? list : [];
  } catch { return []; }
}

function remember(game) {
  if (simulating()) return;         // only real answers go in the history
  const entry = { source: game.source, id: game.id, name: game.name, tier: game.summary.tier };
  const list = [entry, ...loadRecent().filter(r => !(r.source === entry.source && r.id === entry.id))].slice(0, 6);
  try { localStorage.setItem('recent', JSON.stringify(list)); } catch { /* history is optional */ }
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

async function openGame(source, id, name, quiet) {
  const seq = ++state.req;
  state.current = { source, id, name };
  state.sources = {};
  state.active = source;
  state.pcgwPending = false;
  history.replaceState(null, '', '#' + source + '/' + encodeURIComponent(id));
  showTab('check');
  const box = $('#game');
  box.innerHTML = `<div class="state"><span class="spin"></span>Looking up ${esc(name || 'that game')}…</div>`;
  if (!quiet) box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  try {
    const game = await api('/api/game', { source, id, ...overrides() });
    if (seq !== state.req) return;
    state.sources[source] = game;
    state.current.name = game.name;
    remember(game);
  } catch (e) {
    if (seq !== state.req) return;
    box.innerHTML = `<div class="state"><strong>That lookup didn't work</strong>${esc(e.message)}
      <div style="margin-top:12px"><button class="btn" type="button" id="retry">Try again</button></div></div>`;
    return;
  }
  state.pcgwPending = source === 'steam';
  renderGame();
  if (source !== 'steam') return;
  try {
    const second = await api('/api/pcgw-for', { appid: id, ...overrides() });
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
  if (r.status === 'pass') return '';
  if (r.key === 'ram' || r.key === 'vram') return 'You have less than this.';
  return r.note || '';
}

function reqCell(r, label) {
  if (!r) return `<td data-label="${label}"><span class="muted">Not listed</span></td>`;
  const note = cellNote(r);
  const nums = r.target_score
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
    return `<th scope="col">${label}<span class="lv s-${v}">${icon(v)}${LEVEL_WORD[v]}</span></th>`;
  }).join('');
  const body = keys.map(key => {
    const any = byKey[key].minimum || byKey[key].recommended;
    return `<tr><th scope="row">${key === 'os' ? 'Windows' : esc(any.label)}</th>
      <td class="mine" data-label="This PC">${esc(tidy(any.yours))}</td>
      ${levels.map(([k, label]) => reqCell(byKey[key][k], label)).join('')}</tr>`;
  }).join('');
  const vague = levels.every(([k]) => g.levels[k].rows.every(r => r.status === 'unknown'));
  const raw = vague ? levels.map(([k, label]) => g.levels[k].raw && `${label}: ${g.levels[k].raw}`).filter(Boolean).join('\n\n') : '';
  return `<table>
      <colgroup><col class="c-part"><col><col span="${levels.length}"></colgroup>
      <thead><tr><th scope="col"><span class="sr">Part</span></th><th scope="col">This PC</th>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>
    ${raw ? `<p class="raw">As published:\n${esc(raw)}</p>` : ''}`;
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
    : `<span>Requirements from ${SOURCE_NAME[state.active]}${state.pcgwPending ? '. Checking PCGamingWiki too…' : ''}</span>`;
  const links = [...new Map(available.flatMap(s => state.sources[s].links).map(l => [l.label, l])).values()];

  const table = hasLevels
    ? `<section class="side ${state.showNums ? 'show-nums' : ''}">
        <div class="side-head"><h2>Side by side</h2>
          <label class="toggle" title="Rough speed ratings for each part. Bigger is faster.">
            <input type="checkbox" id="nums" ${state.showNums ? 'checked' : ''}>Show speed scores</label></div>
        ${sideBySide(g)}
        <div class="side-foot">${switcher}<span class="grow"></span>
          ${links.map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join('')}</div>
      </section>`
    : `<section class="side"><div class="side-foot">${switcher}<span class="grow"></span>
        ${links.map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join('')}</div></section>`;

  $('#game').innerHTML = `
    <article class="answer t-${sum.tier} ${art ? '' : 'noart'}">
      ${art ? `<img src="${esc(art)}" alt="">` : ''}
      <div>
        <p class="game-name">${esc(g.name)}</p>
        <h2 class="headline"><span class="badge">${icon(status)}</span>${esc(sum.headline)}</h2>
        <p class="detail">${esc(sum.detail)}${g.windows === false && !hasLevels ? ' Steam does not list it as a Windows game.' : ''}</p>
        ${issues}
        ${sum.storage_note ? `<p class="note-line">${esc(sum.storage_note)}</p>` : ''}
      </div>
    </article>
    ${table}`;
}

$('#game').addEventListener('click', e => {
  const src = e.target.closest('[data-src]');
  if (src) { state.active = src.dataset.src; renderGame(); }
  if (e.target.id === 'retry' && state.current) {
    openGame(state.current.source, state.current.id, state.current.name);
  }
});
$('#game').addEventListener('change', e => {
  if (e.target.id !== 'nums') return;
  state.showNums = e.target.checked;
  $('.side').classList.toggle('show-nums', state.showNums);
});

/* ------------------------------------------------------------- library */

const FILTERS = [['all', 'All'], ['great', 'Runs well'], ['ok', 'Runs'], ['close', 'Just about'],
                 ['fail', 'Probably not'], ['unknown', 'Not sure']];

async function loadLibrary() {
  const box = $('#library');
  box.innerHTML = '<div class="state"><span class="spin"></span>Looking for installed Steam games…</div>';
  try {
    state.lib = await api('/api/library');
    renderLibrary();
  } catch (e) {
    box.innerHTML = `<div class="state"><strong>The Steam library couldn't be read</strong>${esc(e.message)}</div>`;
  }
}

function renderLibrary() {
  const box = $('#library'), lib = state.lib;
  if (!lib.found) {
    box.innerHTML = '<div class="state"><strong>Steam isn\'t installed on this PC</strong>You can still look up any game by name under "Check a game".</div>';
    return;
  }
  if (!lib.games.length) {
    box.innerHTML = '<div class="state"><strong>No Steam games are installed yet</strong>Once some are, they show up here and can all be checked at once.</div>';
    return;
  }
  const done = state.libResults.size, total = lib.games.length;
  const counts = {};
  for (const r of state.libResults.values()) {
    const t = r.error ? 'unknown' : r.summary.tier;
    counts[t] = (counts[t] || 0) + 1;
  }
  const btnText = state.libBusy ? `Checked ${done} of ${total}…` : done ? 'Check again' : `Check all ${total}`;
  const rows = lib.games.filter(g => {
    if (state.libFilter === 'all') return true;
    const r = state.libResults.get(g.appid);
    return r && (r.error ? 'unknown' : r.summary.tier) === state.libFilter;
  }).map(g => {
    const r = state.libResults.get(g.appid);
    let badge = '', sub = `${g.size_gb} GB on ${g.drive}`;
    if (r && r.error) {
      badge = `<span class="lv s-unknown">${icon('unknown')}Lookup failed</span>`;
      sub = r.error;
    } else if (r) {
      const st = TIER_STATUS[r.summary.tier];
      badge = `<span class="lv s-${st}">${icon(st)}${esc(r.summary.headline)}</span>`;
      sub = r.summary.issues.length ? r.summary.issues.map(i => i.text).join(' ') : r.summary.detail;
    }
    return `<button class="lib-row" type="button" data-id="${esc(g.appid)}" data-name="${esc(g.name)}">
      <span class="name">${esc(g.name)}</span>${badge}<span class="sub">${esc(sub)}</span></button>`;
  }).join('');

  box.innerHTML = `
    <div class="lib-head">
      <div><h1>Your Steam games</h1>
        <p class="muted">${total} installed on this PC. Check them all in one go, or pick one for the full comparison.</p></div>
      <button class="btn primary" type="button" id="lib-run" ${state.libBusy ? 'disabled' : ''}>${esc(btnText)}</button>
    </div>
    ${state.libBusy ? `<div class="progress"><div style="width:${(done / total * 100).toFixed(1)}%"></div></div>` : ''}
    ${done ? `<div class="filters">${FILTERS.filter(([k]) => k === 'all' || counts[k]).map(([k, label]) =>
      `<button type="button" data-filter="${k}" aria-pressed="${k === state.libFilter}">${label}${k === 'all' ? '' : ' ' + counts[k]}</button>`).join('')}</div>` : ''}
    <div class="lib">${rows || '<p class="state">Nothing in this group.</p>'}</div>`;
}

async function checkLibrary() {
  const run = ++state.libRun;
  state.libBusy = true;
  state.libResults.clear();
  state.libFilter = 'all';
  renderLibrary();
  const queue = [...state.lib.games];
  const worker = async () => {
    while (queue.length && run === state.libRun) {
      const g = queue.shift();
      let result;
      try {
        result = await api('/api/game', { source: 'steam', id: g.appid, ...overrides() });
      } catch (e) {
        result = { error: e.message };
      }
      if (run !== state.libRun) return;
      state.libResults.set(g.appid, result);
      renderLibrary();
    }
  };
  await Promise.all([worker(), worker()]);
  if (run !== state.libRun) return;
  state.libBusy = false;
  renderLibrary();
}

$('#library').addEventListener('click', e => {
  if (e.target.id === 'lib-run') return checkLibrary();
  const f = e.target.closest('[data-filter]');
  if (f) { state.libFilter = f.dataset.filter; return renderLibrary(); }
  const row = e.target.closest('.lib-row');
  if (row) openGame('steam', row.dataset.id, row.dataset.name);
});

/* ---------------------------------------------------------------- tabs */

function showTab(name) {
  for (const t of ['check', 'library']) {
    $('#tab-' + t).hidden = t !== name;
    $('#tabbtn-' + t).setAttribute('aria-selected', String(t === name));
  }
  if (name === 'library' && !state.lib) loadLibrary();
}
$('.tabs').addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (b) showTab(b.dataset.tab);
});

/* ---------------------------------------------------------------- init */

renderChips();
loadSpecs().then(() => {
  const m = location.hash.match(/^#(steam|pcgw)\/(.+)$/);
  if (m) openGame(m[1], decodeURIComponent(m[2]), '', true);
});
