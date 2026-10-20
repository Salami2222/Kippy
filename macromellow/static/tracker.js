/* Local-only daily totals. This file also runs under Node for focused tests. */
(function (root) {
  'use strict';

  const KEY = 'macromellow.daily-macros.v1';
  const FIELDS = ['calories', 'protein_g', 'carbohydrates_g', 'fat_g'];
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit'
  });

  function newYorkDate(value) {
    const parts = Object.fromEntries(formatter.formatToParts(value).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }

  function nextDayBoundary(value) {
    const day = newYorkDate(value);
    let low = value.getTime();
    let high = low + 36 * 60 * 60 * 1000;
    while (high - low > 1) {
      const middle = Math.floor((low + high) / 2);
      if (newYorkDate(new Date(middle)) === day) low = middle;
      else high = middle;
    }
    return high;
  }

  function empty(day) {
    return {date: day, totals: {calories: 0, protein_g: 0, carbohydrates_g: 0, fat_g: 0}, addedResultIds: []};
  }

  function validNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1000000;
  }

  function round(value) {
    return Math.round((value + Number.EPSILON) * 100) / 100;
  }

  function createTracker(storage, now = () => new Date()) {
    function read(day) {
      let saved;
      try { saved = JSON.parse(storage.getItem(KEY)); }
      catch { return empty(day); }
      if (!saved || saved.date !== day || !saved.totals || !Array.isArray(saved.addedResultIds) ||
          !FIELDS.every(field => validNumber(saved.totals[field]))) return empty(day);
      return {date: day, totals: Object.fromEntries(FIELDS.map(field => [field, saved.totals[field]])),
        addedResultIds: saved.addedResultIds.filter(id => typeof id === 'string').slice(-1000)};
    }

    let state = read(newYorkDate(now()));
    function save() { storage.setItem(KEY, JSON.stringify(state)); }
    function snapshot() { return {date: state.date, totals: {...state.totals}, addedResultIds: [...state.addedResultIds]}; }
    function checkDay() {
      const day = newYorkDate(now());
      if (state.date === day) return false;
      state = empty(day);
      save();
      return true;
    }
    function reload() { state = read(newYorkDate(now())); return snapshot(); }
    function getState() { checkDay(); return snapshot(); }
    function adjust(mode, values) {
      checkDay();
      if (!['add', 'subtract', 'set'].includes(mode)) throw new Error('Choose add, subtract, or set.');
      const entries = FIELDS.filter(field => values[field] !== undefined);
      if (!entries.length) throw new Error('Enter at least one macro value.');
      if (entries.some(field => !validNumber(values[field]))) throw new Error('Enter finite numbers for macro values.');
      const next = {...state.totals};
      for (const field of entries) {
        const amount = values[field];
        const result = mode === 'set' ? amount : next[field] + (mode === 'subtract' ? -amount : amount);
        if (!validNumber(result)) throw new Error('The resulting total is too large.');
        next[field] = round(result);
      }
      state.totals = next;
      save();
      return snapshot();
    }
    function addResult(id, values) {
      checkDay();
      if (typeof id !== 'string' || !id) throw new Error('This result has no tracking ID.');
      if (state.addedResultIds.includes(id)) return {added: false, state: snapshot()};
      if (!values || !FIELDS.every(field => validNumber(values[field]) && values[field] >= 0))
        throw new Error('This result has incomplete nutrition values. Use manual adjustments instead.');
      const next = Object.fromEntries(FIELDS.map(field => [field, round(state.totals[field] + values[field])]));
      if (!FIELDS.every(field => validNumber(next[field]))) throw new Error('The resulting total is too large.');
      state.totals = next;
      state.addedResultIds.push(id);
      state.addedResultIds = state.addedResultIds.slice(-1000);
      save();
      return {added: true, state: snapshot()};
    }
    function reset() { state = empty(newYorkDate(now())); save(); return snapshot(); }
    return {getState, reload, checkDay, adjust, addResult, reset};
  }

  const api = {KEY, FIELDS, newYorkDate, nextDayBoundary, createTracker};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MacroTracker = api;
})(typeof window === 'undefined' ? globalThis : window);
