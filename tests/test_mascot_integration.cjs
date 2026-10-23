const assert = require('node:assert/strict');
const {test} = require('node:test');
const {loadApp, deferred, settle} = require('./helpers/app_harness.cjs');

test('real send handler thinks, responds to answers and clarifications, then returns to idle', async () => {
  const app = await loadApp();
  for (const data of [{kind: 'answer', message: 'Found it.'},
    {kind: 'clarification', message: 'Which serving size?'},
    {kind: 'acknowledgment', acknowledgment_only: true, message: 'Understood.'}]) {
    const response = app.enqueueChat();
    const send = app.run('sendMessage("Look up my meal")');
    await settle();
    assert.equal(app.state().state, 'thinking');
    assert.equal(app.get('chat-loading').hidden, false);
    assert.equal(app.get('send').disabled, true);
    assert.ok(app.slots.every(slot => slot.dataset.state === 'thinking'));
    response.resolve(data);
    await send;
    assert.equal(app.state().state, 'talking');
    assert.equal(app.get('chat-loading').hidden, true);
    assert.equal(app.get('send').disabled, false);
    app.finishTalking();
    assert.equal(app.state().state, 'idle');
  }
});

test('server errors and disconnected requests clear thinking, and a later send reconnects', async () => {
  const app = await loadApp();
  const rejected = app.enqueueChat();
  const first = app.run('sendMessage("Find rice")');
  await settle();
  rejected.resolve({kind: 'error', message: 'Ollama unavailable.'});
  await first;
  assert.equal(app.state().state, 'idle');
  assert.equal(app.get('send').disabled, false);

  const disconnected = app.enqueueChat();
  const second = app.run('sendMessage("Try again")');
  await settle();
  assert.equal(app.state().state, 'thinking');
  disconnected.reject(new Error('Network disconnected'));
  await second;
  assert.equal(app.state().state, 'idle');
  assert.equal(app.get('chat-loading').hidden, true);
  assert.equal(app.get('send').disabled, false);

  const restored = app.enqueueChat();
  const third = app.run('sendMessage("Now try again")');
  await settle();
  assert.equal(app.state().state, 'thinking');
  restored.resolve({kind: 'answer', message: 'Connection restored.'});
  await third;
  assert.equal(app.state().state, 'talking');
  assert.equal(app.requests.filter(request => request.url === '/api/chat').length, 3);
});

test('new chat clears a response animation and its delayed idle callback cannot stop the next request', async () => {
  const app = await loadApp();
  const response = app.enqueueChat();
  const first = app.run('sendMessage("Hello")');
  await settle();
  response.resolve({kind: 'answer', message: 'Hello.'});
  await first;
  assert.equal(app.state().state, 'talking');
  await app.get('new-chat').emit('click');
  assert.equal(app.state().state, 'idle');
  assert.equal(app.get('chat-welcome').hidden, false);
  assert.equal(app.get('chat-messages').children.length, 0);
  const next = app.enqueueChat();
  const second = app.run('sendMessage("Find lunch")');
  await settle();
  app.finishTalking();
  assert.equal(app.state().state, 'thinking');
  next.resolve({kind: 'answer', message: 'Lunch found.'});
  await second;
  assert.equal(app.state().state, 'talking');
});

test('backend totals update the sole header mascot without adding artwork to history', async () => {
  const app = await loadApp();
  app.setHistory([{id: 'old-answer', role: 'assistant', reactions: {},
    payload: {kind: 'answer', message: 'Earlier answer'}}]);
  await app.run('conversationId = "restored-conversation"; loadConversation()');
  const profiles = () => app.run('document.querySelectorAll("[data-mellow-profile]")');
  assert.equal(app.slots.length, 1);
  assert.equal(profiles().length, 0);
  assert.equal(app.get('chat-messages').querySelectorAll('img').length, 0);
  app.run('mascot.startThinking()');
  app.applyTracker({totals: {calories: 101, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(app.state().state, 'thinking');
  assert.equal(app.state().burned, true);
  assert.ok(app.slots.every(slot => slot.dataset.burned === 'true' && slot.querySelector('img').src.endsWith('talking-burned.png')));
  assert.equal(profiles().length, 0);
  app.applyTracker({totals: {calories: 100, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(app.state().burned, false);
  assert.equal(profiles().length, 0);
  app.applyTracker({totals: {calories: 101, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  app.applyTracker({goals: {calories: 200, protein_g: null, carbohydrates_g: null, fat_g: null}});
  assert.equal(app.state().burned, false);
});

test('day rollover clears the burned avatar immediately while backend refresh is pending', async () => {
  const app = await loadApp();
  app.setTime('2026-10-22T03:59:59Z');
  app.applyTracker({date: '2026-10-21', totals: {calories: 101, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(app.state().burned, true);
  app.setTime('2026-10-22T04:00:00Z');
  app.setServerTracker({date: '2026-10-22', totals: {calories: 0, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  const rollover = app.run('checkTrackerDay()');
  assert.equal(app.state().burned, false);
  assert.ok(app.slots.every(slot => slot.dataset.burned === 'false'));
  await rollover;
  assert.equal(app.run('tracker.getGoals().calories'), 100);
});


test('independent AI polling shows offline/recovery without duplicate checks or resending chat', async () => {
  const app = await loadApp();
  app.setAIStatus(new Error('Ollama offline'));
  await app.pollAI();
  assert.equal(app.get('ai-status').classList.contains('offline'), true);
  app.setAIStatus({model_available: true, model: 'Qwen', message: 'Connected'});
  await app.pollAI();
  assert.equal(app.get('ai-status').classList.contains('offline'), false);
  const before = app.requests.length;
  const pending = deferred();
  app.setAIStatus(pending);
  const first = app.run('checkAI()');
  const second = app.run('checkAI()');
  await settle();
  assert.equal(app.requests.length, before + 1);
  pending.resolve({model_available: true, model: 'Qwen', message: 'Connected'});
  await Promise.all([first, second]);
  app.setHidden(true);
  const hiddenCount = app.requests.length;
  await app.pollAI();
  assert.equal(app.requests.length, hiddenCount);
  assert.equal(app.requests.filter(request => request.url === '/api/chat').length, 0);
});

test('the single header slot uses only sleeping and talking artwork throughout normal and burned lifecycles', async () => {
  const app = await loadApp();
  const slot = app.get('mellow-mascot');
  for (const burned of [false, true]) {
    app.applyTracker({totals: {calories: burned ? 101 : 100, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
    for (const [action, state, pose] of [['reset', 'idle', 'idle'], ['startThinking', 'thinking', 'talking'], ['respond', 'talking', 'talking']]) {
      app.run(`mascot.${action}()`);
      assert.equal(slot.dataset.state, state);
      assert.equal(slot.dataset.burned, String(burned));
      assert.equal(slot.querySelector('img').getAttribute('src'), !burned ? (pose === 'idle' ? '/assets/mellow/sleep_00.png' : '/assets/mellow/talk_00.png') : `/assets/mellow/${pose}${burned ? '-burned' : ''}.png`);
    }
    app.finishTalking();
    assert.equal(slot.dataset.state, 'idle');
  }
  app.applyTracker({totals: {calories: 50, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(slot.dataset.burned, 'false');
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/sleep_00.png');
});

test('application markup has one mascot, immediately before its header name, and no other artwork', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '../macromellow/static/index.html'), 'utf8');
  assert.equal((html.match(/data-mellow-mascot/g) || []).length, 1);
  assert.equal((html.match(/<img\b/g) || []).length, 1);
  assert.match(html, /class="chat-identity"><span id="mellow-mascot"[^]*?<\/span><div><h2>Mellow<\/h2>/);
  assert.doesNotMatch(html, /data-mellow-profile|welcome-icon/);
});


test('atlas readiness retains a static fallback until loaded and never changes activity state', async () => {
  const app = await loadApp();
  const slot = app.get('mellow-mascot');
  assert.equal(slot.classList.contains('sleep-ready'), false);
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/sleep_00.png');
  assert.equal(app.imageLoads.length, 2);
  assert.equal(app.imageLoads[0].src, '/assets/mellow/mellow_sleeping_10frame_atlas.png');
  app.run('mascot.startThinking()');
  app.imageLoads[0].onload();
  assert.equal(slot.classList.contains('sleep-ready'), true);
  assert.equal(slot.dataset.state, 'thinking');
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/talk_00.png');
  app.run('mascot.respond()');
  app.finishTalking();
  assert.equal(slot.dataset.state, 'idle');
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/sleep_00.png');
});


test('talking readiness is independent and never substitutes normal artwork for burned states', async () => {
  const app = await loadApp();
  const slot = app.get('mellow-mascot');
  assert.equal(app.imageLoads[1].src, '/assets/mellow/talking-v1-atlas.png');
  app.run('mascot.startThinking()');
  assert.equal(slot.classList.contains('talk-ready'), false);
  app.applyTracker({totals: {calories: 101, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  app.imageLoads[1].onload();
  assert.equal(slot.classList.contains('talk-ready'), true);
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/talking-burned.png');
  app.applyTracker({totals: {calories: 100, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/talk_00.png');
  app.run('mascot.respond()');
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/talk_00.png');
  app.finishTalking();
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/sleep_00.png');
});
