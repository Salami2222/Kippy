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

test('backend totals synchronize all avatars and historical profiles without changing conversation state', async () => {
  const app = await loadApp();
  app.setHistory([{id: 'old-answer', role: 'assistant', reactions: {},
    payload: {kind: 'answer', message: 'Earlier answer'}}]);
  await app.run('conversationId = "restored-conversation"; loadConversation()');
  const profiles = () => app.run('document.querySelectorAll("[data-mellow-profile]")');
  assert.equal(profiles().length, 2);
  app.run('mascot.startThinking()');
  app.applyTracker({totals: {calories: 101, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(app.state().state, 'thinking');
  assert.equal(app.state().burned, true);
  assert.ok(app.slots.every(slot => slot.dataset.burned === 'true' && slot.querySelector('img').src.endsWith('thinking-burned.png')));
  assert.ok(profiles().every(img => img.src.endsWith('profile-burned.png')));
  app.applyTracker({totals: {calories: 100, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(app.state().burned, false);
  assert.ok(profiles().every(img => img.src.endsWith('/profile.png')));
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
