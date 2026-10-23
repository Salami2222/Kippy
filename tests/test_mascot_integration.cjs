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
  assert.ok(app.slots.every(slot => slot.dataset.burned === 'true' && slot.querySelector('img').src.endsWith('burned-talk-00.png')));
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
      assert.equal(slot.querySelector('img').getAttribute('src'), !burned ? (pose === 'idle' ? '/assets/mellow/sleep_00.png' : '/assets/mellow/talk_00.png') : `/assets/mellow/burned-${pose === "idle" ? "sleep" : "talk"}-00.png`);
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
  assert.equal(app.imageLoads.length, 5);
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
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/burned-talk-00.png');
  app.applyTracker({totals: {calories: 100, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/talk_00.png');
  app.run('mascot.respond()');
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/talk_00.png');
  app.finishTalking();
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/sleep_00.png');
});


test('burned atlases preload independently and follow activity, correction, and return-to-idle', async () => {
  const app = await loadApp();
  const slot = app.get('mellow-mascot');
  assert.deepEqual(app.imageLoads.slice(2, 4).map(image => image.src), [
    '/assets/mellow/burned-sleep-atlas.png', '/assets/mellow/burned-talk-atlas.png'
  ]);
  app.applyTracker({totals: {calories: 101, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/burned-sleep-00.png');
  assert.equal(slot.classList.contains('burned-sleep-ready'), false);
  app.imageLoads[2].onload();
  assert.equal(slot.classList.contains('burned-sleep-ready'), true);
  app.run('mascot.startThinking()');
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/burned-talk-00.png');
  app.imageLoads[3].onload();
  assert.equal(slot.classList.contains('burned-talk-ready'), true);
  assert.equal(slot.dataset.state, 'thinking');
  app.run('mascot.respond()');
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/burned-talk-00.png');
  app.finishTalking();
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/burned-sleep-00.png');
  app.applyTracker({totals: {calories: 100, protein_g: 0, carbohydrates_g: 0, fat_g: 0}});
  assert.equal(slot.querySelector('img').getAttribute('src'), '/assets/mellow/sleep_00.png');
  assert.equal(slot.dataset.burned, 'false');
});

test('confirmed milestone plays once, preloads atlas, and never replays plain tracker restoration', async () => {
  const app = await loadApp();
  const snapshot = {date: '2026-10-21', totals: {calories: 0, protein_g: 10, carbohydrates_g: 0, fat_g: 0},
    goals: {calories: null, protein_g: 10, carbohydrates_g: null, fat_g: null}, entries: []};
  app.applyTracker(snapshot);
  assert.equal(app.state().state, 'idle');
  assert.equal(app.imageLoads[4].src, '/assets/mellow/happy_bounce_atlas.png');
  app.applyTracker({...snapshot, celebration: {date: snapshot.date, kind: 'protein_goal'}});
  assert.equal(app.state().state, 'celebrating');
  assert.equal(app.get('mellow-mascot').querySelector('img').src, '/assets/mellow/happy_bounce_00.png');
  app.imageLoads[4].onload();
  assert.equal(app.get('mellow-mascot').classList.contains('happy-ready'), true);
  assert.equal(app.get('mellow-mascot').dataset.happyAnimated, 'false'); // No mid-playback restart.
  app.run('mascot.reset()'); // Regular reset cannot interrupt the celebration.
  assert.equal(app.state().state, 'celebrating');
  app.finishCelebration();
  app.applyTracker({...snapshot, celebration: {date: snapshot.date, kind: 'protein_goal'}});
  assert.equal(app.state().state, 'idle');
  assert.equal(app.slots.length, 1);
});


test('real chat completion cannot interrupt a freshly confirmed protein celebration', async () => {
  const app = await loadApp();
  app.imageLoads[4].onload();
  const response = app.enqueueChat();
  const send = app.run('sendMessage("Confirm that meal")');
  await settle();
  assert.equal(app.state().state, 'thinking');
  response.resolve({kind: 'answer', message: 'Meal logged.', tracker: {
    date: '2026-10-21', totals: {calories: 101, protein_g: 11, carbohydrates_g: 0, fat_g: 0},
    goals: {calories: 100, protein_g: 10, carbohydrates_g: null, fat_g: null}, entries: [],
    celebration: {date: '2026-10-21', kind: 'protein_goal'}
  }});
  await send;
  assert.equal(app.state().state, 'celebrating');
  assert.equal(app.get('mellow-mascot').dataset.happyAnimated, 'true');
  app.finishTalking();
  assert.equal(app.state().state, 'celebrating');
  app.finishCelebration();
  assert.equal(app.state().state, 'idle');
  assert.equal(app.state().asset, '/assets/mellow/burned-sleep-00.png');
});

test('dashboard distinguishes calorie allowance and positive protein completion at every boundary', async () => {
  const app = await loadApp();
  const goals = {calories: 2500, protein_g: 180, carbohydrates_g: 200, fat_g: 70};
  const cases = [
    [2100, 150, '400 kcal remaining', '30 g remaining', false, false],
    [2500, 180, 'Calorie limit reached.', 'Protein goal reached!', false, true],
    [2600, 195, 'Over calorie limit by 100 kcal.', 'Protein goal reached!', true, true],
    [2100, 195, '400 kcal remaining', 'Protein goal reached!', false, true],
  ];
  for (const [calories, protein_g, calorieText, proteinText, burned, complete] of cases) {
    app.applyTracker({totals: {calories, protein_g, carbohydrates_g: 0, fat_g: 0}, goals});
    assert.equal(app.get('balance-calories').textContent, calorieText);
    assert.equal(app.get('balance-protein').textContent, proteinText);
    assert.equal(app.get('goal-calories').textContent, `Limit: ${calories.toLocaleString('en-US')} / 2,500 kcal`);
    assert.equal(app.get('goal-protein').textContent, `Goal: ${protein_g} / 180 g`);
    assert.equal(app.get('progress-protein').classList.contains('completed'), complete);
    assert.equal(app.get('balance-protein').classList.contains('completed'), complete);
    assert.equal(app.get('balance-protein').classList.contains('exceeded'), false);
    assert.equal(app.get('progress-protein').classList.contains('exceeded'), false);
    assert.equal(app.get('progress-calories').classList.contains('exceeded'), burned);
    assert.equal(app.state().burned, burned);
    assert.equal(app.state().state, 'idle'); // Loading a reached goal never fabricates a milestone.
  }
  app.applyTracker({goals: {calories: null, protein_g: null, carbohydrates_g: null, fat_g: null}});
  assert.equal(app.get('goal-calories').textContent, 'No limit set');
  assert.equal(app.get('progress-protein').classList.contains('completed'), false);
});

test('protein celebration returns to the correct normal or calorie-burned pose without changing stored values', async () => {
  for (const calories of [2500, 2501]) {
    const app = await loadApp();
    app.imageLoads[4].onload();
    const snapshot = {date: '2026-10-21', totals: {calories, protein_g: 195, carbohydrates_g: 0, fat_g: 0},
      goals: {calories: 2500, protein_g: 180, carbohydrates_g: null, fat_g: null}, entries: [],
      celebration: {date: '2026-10-21', kind: 'protein_goal'}};
    app.applyTracker(snapshot);
    const saved = app.run('JSON.stringify([tracker.getState(),tracker.getGoals()])');
    assert.equal(app.state().state, 'celebrating');
    app.finishCelebration();
    assert.equal(app.state().state, 'idle');
    assert.equal(app.state().burned, calories > 2500);
    assert.equal(app.get('balance-protein').textContent, 'Protein goal reached!');
    app.run('renderTracker(); tracker.reload(); tracker.reloadGoals(); renderTracker()');
    app.applyTracker(snapshot); // Duplicate delivery cannot replay it either.
    assert.equal(app.state().state, 'idle');
    assert.equal(app.run('JSON.stringify([tracker.getState(),tracker.getGoals()])'), saved);
  }
});
