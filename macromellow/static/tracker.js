/* Local-only daily totals. This file also runs under Node for focused tests. */
(function (root) {
  'use strict';

  const KEY = 'macromellow.daily-macros.v1';
  const GOALS_KEY = 'macromellow.macro-goals.v1';
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

  function emptyGoals() {
    return {calories: null, protein_g: null, carbohydrates_g: null, fat_g: null};
  }

  function validNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1000000;
  }

  function round(value) {
    return Math.round((value + Number.EPSILON) * 100) / 100;
  }

  function goalProgress(consumed, goal) {
    if (goal === null) return null;
    return {
      remaining: round(Math.max(0, goal - consumed)),
      exceeded: round(Math.max(0, consumed - goal)),
      percent: goal === 0 ? (consumed >= 0 ? 100 : 0) : Math.min(100, Math.max(0, round(consumed / goal * 100)))
    };
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

    function readGoals() {
      let saved;
      try { saved = JSON.parse(storage.getItem(GOALS_KEY)); }
      catch { return emptyGoals(); }
      if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return emptyGoals();
      return Object.fromEntries(FIELDS.map(field => [field,
        saved[field] === null || saved[field] === undefined ? null :
          validNumber(saved[field]) && saved[field] >= 0 ? saved[field] : null]));
    }

    let state = read(newYorkDate(now()));
    let goals = readGoals();
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
    function getGoals() { return {...goals}; }
    function reloadGoals() { goals = readGoals(); return getGoals(); }
    function setGoals(changes) {
      if (!changes || typeof changes !== 'object') throw new Error('Enter goal values to save.');
      const next = {...goals};
      for (const field of FIELDS) {
        if (!Object.hasOwn(changes, field)) continue;
        const value = changes[field];
        if (value !== null && (!validNumber(value) || value < 0))
          throw new Error('Goals must be zero or positive numbers.');
        next[field] = value === null ? null : round(value);
      }
      goals = next;
      storage.setItem(GOALS_KEY, JSON.stringify(goals));
      return getGoals();
    }
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
    return {getState, reload, checkDay, adjust, addResult, reset, getGoals, reloadGoals, setGoals};
  }

  const api = {KEY, GOALS_KEY, FIELDS, newYorkDate, nextDayBoundary, goalProgress, createTracker};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MacroTracker = api;
})(typeof window === 'undefined' ? globalThis : window);
