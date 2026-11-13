/* State rules the page relies on, kept free of the DOM so they can be tested
   (tests/js/logic.test.js runs them under Node). */
(function (root) {
  'use strict';

  const GROUPS = ['met', 'borderline', 'failing', 'incomplete'];
  const TIER_GROUP = { great: 'met', ok: 'met', close: 'borderline', fail: 'failing',
                       partial: 'incomplete', unknown: 'incomplete' };
  const DEFAULT_FILTERS = { group: 'all', limit: 'any', provider: 'all', storage: false };

  // A check result for one game -> {group, limits, storage, tier}.
  function outcome(entry) {
    if (!entry) return null;
    if (entry.error) return { group: 'incomplete', tier: 'unknown', limits: [], storage: false, error: true };
    if (entry.needs_choice) return { group: 'incomplete', tier: 'unknown', limits: [], storage: false, choose: true };
    const res = entry.result || entry;
    const s = res.summary || {};
    const base = (res.levels && (res.levels.minimum || res.levels.recommended)) || {};
    const storage = (base.rows || []).some(r => r.key === 'storage' && r.status === 'warn');
    return { group: TIER_GROUP[s.tier] || 'incomplete', tier: s.tier || 'unknown',
             limits: s.limits || base.limits || [], storage };
  }

  function matches(game, out, f) {
    if (f.provider !== 'all' && !(game.providers || []).includes(f.provider)) return false;
    if (f.group !== 'all') {
      if (f.group === 'unchecked') { if (out) return false; }
      else if (!out || out.group !== f.group) return false;
    }
    if (f.limit !== 'any' && !(out && out.limits.includes(f.limit))) return false;
    if (f.storage && !(out && out.storage)) return false;
    return true;
  }

  // Which filter values still match at least one game. A filter that would
  // hide everything because its results were cleared goes back to "all".
  function normalizeFilters(f, games, outcomes) {
    const next = Object.assign({}, DEFAULT_FILTERS, f);
    const anyWith = test => games.some(g => test(g, outcomes.get(g.id)));
    if (next.provider !== 'all' && !anyWith(g => (g.providers || []).includes(next.provider))) next.provider = 'all';
    if (next.group !== 'all' && !anyWith((g, o) => next.group === 'unchecked' ? !o : o && o.group === next.group)) next.group = 'all';
    if (next.limit !== 'any' && !anyWith((g, o) => o && o.limits.includes(next.limit))) next.limit = 'any';
    if (next.storage && !anyWith((g, o) => o && o.storage)) next.storage = false;
    return next;
  }

  function counts(games, outcomes) {
    const c = { all: games.length, unchecked: 0, met: 0, borderline: 0, failing: 0, incomplete: 0,
                limits: {}, providers: {}, storage: 0 };
    for (const g of games) {
      const o = outcomes.get(g.id);
      if (!o) c.unchecked++;
      else {
        c[o.group]++;
        for (const l of o.limits) c.limits[l] = (c.limits[l] || 0) + 1;
        if (o.storage) c.storage++;
      }
      for (const p of g.providers || []) c.providers[p] = (c.providers[p] || 0) + 1;
    }
    return c;
  }

  // Search requests carry a sequence number. Typing, clearing or dismissing
  // bumps it, so an answer that arrives late is dropped, and Enter only picks
  // from results that belong to the text in the box right now.
  class SearchState {
    constructor() { this.seq = 0; this.hits = []; this.term = null; this.pending = false; }
    begin(term) { this.seq++; this.pending = true; this.hits = []; this.term = null; return { seq: this.seq, term }; }
    invalidate() { this.seq++; this.pending = false; this.hits = []; this.term = null; }
    accept(token, hits) {
      if (token.seq !== this.seq) return false;
      this.pending = false; this.hits = hits; this.term = token.term;
      return true;
    }
    fail(token) { if (token.seq !== this.seq) return false; this.pending = false; this.hits = []; this.term = token.term; return true; }
    pickable(currentTerm) { return !this.pending && this.term !== null && this.term === currentTerm ? this.hits : []; }
  }

  // Planner totals for one option. Unknown results never count as gains.
  function planTotals(rows, pid) {
    const t = { newly_recommended: 0, newly_minimum: 0, now_met: 0, borderline: 0, still_below: 0,
                worse: 0, incomplete: 0, no_change: 0, not_checked: 0, limits: {} };
    for (const r of rows) {
      const cat = r && r.changes ? r.changes[pid] : null;
      if (!cat) { t.not_checked++; continue; }
      t[cat]++;
      if (cat === 'still_below' || cat === 'borderline' || cat === 'worse') {
        for (const l of (r.states[pid] || {}).limits || []) t.limits[l] = (t.limits[l] || 0) + 1;
      }
    }
    return t;
  }

  // Query parameters for a check with a profile and quick changes.
  function pcParams(profileId, override) {
    const p = {};
    if (profileId && profileId !== 'real') p.profile = profileId;
    for (const k of ['cpu', 'gpu', 'ram', 'vram']) if (override && override[k]) p[k] = override[k];
    return p;
  }

  const api = { GROUPS, TIER_GROUP, DEFAULT_FILTERS, outcome, matches, normalizeFilters, counts,
                SearchState, planTotals, pcParams };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.GSC = api;
})(typeof window !== 'undefined' ? window : globalThis);
