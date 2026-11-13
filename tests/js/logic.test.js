// Tests for web/logic.js. Run with:  node tests/js/logic.test.js
// (tests/test_web_logic.py runs this when Node is installed.)
'use strict';

const L = require('../../web/logic.js');
const results = [];

function test(name, fn) {
  try { fn(); results.push([true, name]); } catch (e) { results.push([false, name + ': ' + e.message]); }
}
function eq(a, b) {
  const x = JSON.stringify(a), y = JSON.stringify(b);
  if (x !== y) throw new Error(`expected ${y}, got ${x}`);
}

const games = [
  { id: 'g_1', providers: ['steam'] },
  { id: 'g_2', providers: ['epic'] },
  { id: 'w_3', providers: ['wanted'] },
];
const res = (tier, limits = [], storage = false) => ({ result: { summary: { tier, limits },
  levels: { minimum: { rows: storage ? [{ key: 'storage', status: 'warn' }] : [] } } } });

test('outcome groups', () => {
  eq(L.outcome(res('great')).group, 'met');
  eq(L.outcome(res('partial')).group, 'incomplete');
  eq(L.outcome({ error: 'x' }).group, 'incomplete');
  eq(L.outcome({ needs_choice: true }).choose, true);
  eq(L.outcome(res('fail', ['gpu'], true)).storage, true);
  eq(L.outcome(null), null);
});

test('filters reset when results are cleared', () => {
  const outs = new Map([['g_1', L.outcome(res('fail', ['gpu']))]]);
  let f = { ...L.DEFAULT_FILTERS, group: 'failing', limit: 'gpu' };
  eq(L.normalizeFilters(f, games, outs).group, 'failing');
  // After an upgrade, a reset or a rescan every result is cleared.
  f = L.normalizeFilters(f, games, new Map());
  eq([f.group, f.limit], ['all', 'any']);
});

test('filters keep valid values and drop missing providers', () => {
  const outs = new Map([['g_1', L.outcome(res('great'))]]);
  eq(L.normalizeFilters({ provider: 'epic' }, games, outs).provider, 'epic');
  eq(L.normalizeFilters({ provider: 'gog' }, games, outs).provider, 'all');
  eq(L.normalizeFilters({ storage: true }, games, outs).storage, false);
  eq(L.normalizeFilters({ group: 'unchecked' }, games, outs).group, 'unchecked');
});

test('matches and counts', () => {
  const outs = new Map([['g_1', L.outcome(res('fail', ['gpu', 'ram']))], ['w_3', L.outcome(res('ok', [], true))]]);
  const f = { ...L.DEFAULT_FILTERS, limit: 'gpu' };
  eq(games.filter(g => L.matches(g, outs.get(g.id), f)).map(g => g.id), ['g_1']);
  const c = L.counts(games, outs);
  eq([c.failing, c.met, c.unchecked, c.limits.gpu, c.storage, c.providers.wanted], [1, 1, 1, 1, 1, 1]);
  eq(games.filter(g => L.matches(g, outs.get(g.id), { ...L.DEFAULT_FILTERS, storage: true })).map(g => g.id), ['w_3']);
});

test('late search answers are dropped', () => {
  const s = new L.SearchState();
  const first = s.begin('hal');
  const second = s.begin('halo');
  eq(s.accept(first, [{ name: 'Half-Life' }]), false);
  eq(s.accept(second, [{ name: 'Halo' }]), true);
  eq(s.pickable('halo').length, 1);
  eq(s.pickable('halo 2').length, 0);       // text changed since the answer
});

test('dismissing invalidates pending searches', () => {
  const s = new L.SearchState();
  const t = s.begin('doom');
  s.invalidate();
  eq(s.accept(t, [{ name: 'DOOM' }]), false);
  eq(s.pickable('doom').length, 0);
});

test('enter cannot pick while a search is pending', () => {
  const s = new L.SearchState();
  s.accept(s.begin('doom'), [{ name: 'DOOM' }]);
  s.begin('doom');
  eq(s.pickable('doom').length, 0);
});

test('plan totals never count unknowns as gains', () => {
  const rows = [
    { changes: { p: 'newly_minimum' }, states: { p: { limits: [] } } },
    { changes: { p: 'incomplete' }, states: { p: { limits: [] } } },
    { changes: { p: 'still_below' }, states: { p: { limits: ['gpu'] } } },
    { error: 'x' },
  ];
  const t = L.planTotals(rows, 'p');
  eq([t.newly_minimum, t.incomplete, t.still_below, t.not_checked, t.limits.gpu], [1, 1, 1, 1, 1]);
});

test('pc params', () => {
  eq(L.pcParams('real', { cpu: '', gpu: 'RTX 4070', ram: '32', vram: '' }), { gpu: 'RTX 4070', ram: '32' });
  eq(L.pcParams('abc', {}), { profile: 'abc' });
});

let failed = 0;
for (const [ok, name] of results) {
  console.log((ok ? 'ok   ' : 'FAIL ') + name);
  if (!ok) failed++;
}
console.log(`${results.length - failed} of ${results.length} passed`);
if (typeof process !== 'undefined') process.exitCode = failed ? 1 : 0;
