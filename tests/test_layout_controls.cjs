const test = require('node:test');
const assert = require('node:assert/strict');
const {loadApp} = require('./helpers/app_harness.cjs');

test('details drawer retains drafts and keeps navigation synchronized through close and view changes', async () => {
  const app = await loadApp();
  app.get('goal-input-calories').value = '2100';
  await app.get('tracker-expand').emit('click');
  assert.equal(app.get('tracker-details').open, true);
  assert.equal(app.get('tracker-tab').classList.contains('active'), true);
  await app.get('tracker-details-close').emit('click');
  assert.equal(app.get('tracker-expand').getAttribute('aria-expanded'), 'false');
  assert.equal(app.get('chat-tab').classList.contains('active'), true);
  await app.get('tracker-tab').emit('click');
  app.get('tracker-details').close(); // Native Escape closes the modal through the same close event.
  assert.equal(app.get('tracker-tab').classList.contains('active'), false);
  assert.equal(app.get('goal-input-calories').value, '2100');
  await app.get('tracker-tab').emit('click');
  await app.get('browse-tab').emit('click');
  assert.equal(app.get('tracker-details').open, false);
  assert.equal(app.get('chat-view').hidden, true);
  assert.equal(app.get('browse-view').hidden, false);
  await app.get('chat-tab').emit('click');
  assert.equal(app.get('chat-view').hidden, false);
});

test('summary can collapse without changing tracker state and Daily macros restores it', async () => {
  const app = await loadApp();
  const before = JSON.stringify(app.run('tracker.getState()'));
  await app.get('tracker-toggle').emit('click');
  assert.equal(app.get('daily-tracker').hidden, true);
  assert.equal(app.get('tracker-toggle').getAttribute('aria-expanded'), 'false');
  await app.get('tracker-tab').emit('click');
  assert.equal(app.get('daily-tracker').hidden, false);
  assert.equal(app.get('tracker-details').open, true);
  assert.equal(JSON.stringify(app.run('tracker.getState()')), before);
});

test('mobile summary opens details on tap without swallowing interactive controls', async () => {
  const app = await loadApp({compact: true});
  await app.get('daily-tracker').emit('click', {target: {closest: () => true}});
  assert.equal(app.get('tracker-details').open, false);
  await app.get('daily-tracker').emit('click');
  assert.equal(app.get('tracker-details').open, true);
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
