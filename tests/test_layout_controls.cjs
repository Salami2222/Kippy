const test = require('node:test');
const assert = require('node:assert/strict');
const {loadApp} = require('./helpers/app_harness.cjs');

test('only Chat and Food data navigation remain; no sidebar collapse mechanism exists', () => {
  const fs = require('node:fs'); const path = require('node:path');
  for (const name of ['index.html', 'app.js', 'app.css']) {
    const source = fs.readFileSync(path.join(__dirname, '../macromellow/static', name), 'utf8');
    assert.doesNotMatch(source, /tracker-tab|tracker-toggle|setMacrosVisible|macros-hidden/);
  }
});

test('permanent dashboard and records drawer stay synchronized through navigation', async () => {
  const app = await loadApp();
  const before = app.run('JSON.stringify(tracker.getState())');
  await app.get('tracker-expand').emit('click');
  assert.equal(app.get('tracker-details').open, true);
  assert.equal(app.get('tracker-expand').getAttribute('aria-expanded'), 'true');
  app.get('tracker-details').close();
  assert.equal(app.get('tracker-expand').getAttribute('aria-expanded'), 'false');
  await app.get('tracker-expand').emit('click');
  await app.get('browse-tab').emit('click');
  assert.equal(app.get('tracker-details').open, false);
  assert.equal(app.get('chat-view').hidden, true);
  assert.equal(app.get('browse-view').hidden, false);
  await app.get('chat-tab').emit('click');
  assert.equal(app.get('chat-view').hidden, false);
  assert.equal(app.run('JSON.stringify(tracker.getState())'), before);
});

test('direct dialogs are mutually exclusive and cancellation never writes data', async () => {
  const app = await loadApp({compact: true});
  await app.get('adjust-macros').emit('click');
  assert.equal(app.get('macro-adjust').open, true);
  await app.get('edit-daily-targets').emit('click');
  assert.equal(app.get('macro-adjust').open, false);
  assert.equal(app.get('adjust-macros').getAttribute('aria-expanded'), 'false');
  assert.equal(app.get('daily-targets').open, true);
  assert.equal(app.get('goal-input-calories').value, 100);
  app.get('goal-input-calories').value = '999';
  await app.get('targets-cancel').emit('click');
  await app.get('edit-daily-targets').emit('click');
  assert.equal(app.get('goal-input-calories').value, 100);
  app.get('daily-targets').close(); // Native Escape invokes close too.
  assert.equal(app.get('edit-daily-targets').getAttribute('aria-expanded'), 'false');
  assert.equal(app.requests.some(r => r.options?.method === 'POST'), false);
});

const totals = {calories: 2100, protein_g: 150, carbohydrates_g: 180, fat_g: 50};
const goals = {calories: 2500, protein_g: 180, carbohydrates_g: 300, fat_g: 75};
const snapshot = (changes = {}) => ({date: '2026-10-21', totals, goals, entries: [], ...changes});

test('adjustment modes send only supplied nutrients and render backend-confirmed totals', async () => {
  for (const mode of ['add', 'subtract', 'set']) {
    const app = await loadApp();
    app.applyTracker(snapshot());
    const expected = {...totals, protein_g: mode === 'set' ? 20 : mode === 'add' ? 170 : 130};
    app.setStateHandler((url, payload) => {
      assert.equal(url, '/api/tracker/adjust');
      assert.deepEqual(JSON.parse(JSON.stringify(payload)), {mode, values: {protein_g: 20}});
      return snapshot({totals: expected});
    });
    await app.get('adjust-macros').emit('click');
    app.get('tracker-mode').value = mode;
    app.get('tracker-input-protein').value = '20';
    await app.get('tracker-form').emit('submit');
    assert.equal(app.get('macro-adjust').open, false);
    assert.equal(app.get('tracker-protein').textContent, String(expected.protein_g));
    assert.equal(app.get('tracker-calories').textContent, '2,100');
    assert.equal(app.get('tracker-status').textContent, 'Today’s totals updated.');
  }
});

test('failed adjustments retain inputs, stay open, and can be retried without duplicate submissions', async () => {
  const {deferred} = require('./helpers/app_harness.cjs');
  const app = await loadApp(); const pending = deferred();
  app.setStateHandler(() => pending.promise);
  await app.get('adjust-macros').emit('click');
  app.get('tracker-mode').value = 'add'; app.get('tracker-input-calories').value = '123';
  const submission = app.get('tracker-form').emit('submit');
  await app.get('tracker-form').emit('submit');
  await app.get('adjust-cancel').emit('click');
  assert.equal(app.get('macro-adjust').open, true);
  assert.equal(app.get('macro-apply').disabled, true);
  assert.equal(app.requests.filter(r => r.url === '/api/tracker/adjust').length, 1);
  pending.reject(new Error('Connection lost')); await submission;
  assert.equal(app.get('macro-adjust').open, true);
  assert.equal(app.get('tracker-input-calories').value, '123');
  assert.equal(app.get('adjust-error').textContent, 'Connection lost');
  assert.equal(app.get('adjust-error').hidden, false);
  assert.equal(app.get('macro-apply').disabled, false);
});

test('target save supports clearing independent targets without changing totals or celebrating', async () => {
  const app = await loadApp(); app.applyTracker(snapshot());
  app.setStateHandler((url, payload) => {
    assert.equal(url, '/api/tracker/goals');
    assert.deepEqual(JSON.parse(JSON.stringify(payload)), {goals: {calories: 2400, protein_g: 100, carbohydrates_g: null, fat_g: null}});
    return snapshot({goals: payload.goals});
  });
  await app.get('edit-daily-targets').emit('click');
  app.get('goal-input-calories').value = '2400'; app.get('goal-input-protein').value = '100';
  app.get('goal-input-carbs').value = ''; app.get('goal-input-fat').value = '';
  await app.get('goal-form').emit('submit');
  assert.equal(app.get('daily-targets').open, false);
  assert.equal(app.get('tracker-calories').textContent, '2,100');
  assert.equal(app.get('goal-carbs').textContent, 'No goal set');
  assert.equal(app.state().state, 'idle');
});

test('failed target save preserves the draft and permits cancellation', async () => {
  const app = await loadApp();
  app.setStateHandler(() => { throw new Error('Cannot save targets'); });
  await app.get('edit-daily-targets').emit('click'); app.get('goal-input-calories').value = '2300';
  await app.get('goal-form').emit('submit');
  assert.equal(app.get('daily-targets').open, true);
  assert.equal(app.get('goal-input-calories').value, '2300');
  assert.equal(app.get('targets-error').textContent, 'Cannot save targets');
  assert.equal(app.get('targets-save').disabled, false);
  await app.get('targets-cancel').emit('click'); assert.equal(app.get('daily-targets').open, false);
});

test('reset requires confirmation, clears confirmed totals, and preserves targets', async () => {
  const app = await loadApp(); app.applyTracker(snapshot());
  app.setStateHandler(url => {
    assert.equal(url, '/api/tracker/reset');
    return snapshot({totals: {calories: 0, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  });
  await app.get('adjust-macros').emit('click'); app.setConfirmed(false);
  await app.get('tracker-reset').emit('click');
  assert.equal(app.requests.some(r => r.url === '/api/tracker/reset'), false);
  assert.equal(app.get('macro-adjust').open, true);
  app.setConfirmed(true); await app.get('tracker-reset').emit('click');
  assert.equal(app.get('macro-adjust').open, false);
  assert.equal(app.get('tracker-calories').textContent, '0');
  assert.equal(app.get('goal-calories').textContent, 'Limit: 2,500 kcal');
  assert.match(app.dialogs[0], /targets.*stay saved/);
});

test('confirmed adjustment celebrates protein while burned and returns to burned idle', async () => {
  const app = await loadApp(); app.imageLoads[4].onload(); app.applyTracker(snapshot());
  app.setStateHandler(() => snapshot({totals: {...totals, calories: 2600, protein_g: 180}, celebration: {date: '2026-10-21', kind: 'protein_goal'}}));
  await app.get('adjust-macros').emit('click');
  app.get('tracker-mode').value = 'add'; app.get('tracker-input-protein').value = '30';
  await app.get('tracker-form').emit('submit');
  assert.equal(app.state().state, 'celebrating');
  app.finishCelebration(); assert.equal(app.state().state, 'idle'); assert.equal(app.state().burned, true);
});

test('composer grows to a bounded height and shrinks after clearing a draft', async () => {
  const app = await loadApp();
  app.get('chat-input').scrollHeight = 400;
  await app.get('chat-input').emit('input');
  assert.equal(app.get('chat-input').style.height, '128px');
  app.get('chat-input').scrollHeight = 36;
  await app.get('chat-input').emit('input');
  assert.equal(app.get('chat-input').style.height, '40px');
});

test('compact chat follows visual viewport height and offset when the keyboard reduces space', async () => {
  const listeners = new Map();
  const viewport = {height: 844, offsetTop: 0, addEventListener: (name, callback) => listeners.set(name, callback)};
  const app = await loadApp({compact: true, viewport});
  assert.equal(app.document.documentElement.style['--app-height'], '844px');
  viewport.height = 420; viewport.offsetTop = 24;
  listeners.get('resize')(); listeners.get('scroll')();
  assert.equal(app.document.documentElement.style['--app-height'], '420px');
  assert.equal(app.document.documentElement.style['--app-top'], '24px');
  viewport.height = 844; viewport.offsetTop = 0; listeners.get('resize')();
  assert.equal(app.document.documentElement.style['--app-height'], '844px');
});
