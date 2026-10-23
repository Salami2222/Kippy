const assert = require('node:assert/strict');
const {test} = require('node:test');
const {hasExceededGoal, assetFor, createController} = require('../macromellow/static/mascot.js');
const {createTracker, KEY, GOALS_KEY} = require('../macromellow/static/tracker.js');

const fields = ['calories', 'protein_g', 'carbohydrates_g', 'fat_g'];

function memoryStorage() {
  const data = new Map();
  return {getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value)};
}

function fakeClock() {
  let nextId = 1;
  const pending = new Map();
  const callbacks = [];
  return {
    setTimeout(callback, delay) {
      const id = nextId++;
      pending.set(id, {callback, delay});
      callbacks.push(callback);
      return id;
    },
    clearTimeout(id) { pending.delete(id); },
    finish() {
      const timers = [...pending.values()];
      pending.clear();
      timers.forEach(timer => timer.callback());
    },
    pending,
    callbacks
  };
}

test('each positive configured goal burns only when strictly exceeded', () => {
  for (const field of fields) {
    assert.equal(hasExceededGoal({[field]: 9}, {[field]: 10}), false);
    assert.equal(hasExceededGoal({[field]: 10}, {[field]: 10}), false);
    assert.equal(hasExceededGoal({[field]: 10.01}, {[field]: 10}), true);
    assert.equal(hasExceededGoal({calories: 999, [field]: 11}, {[field]: 10}), true);
  }
});

test('unconfigured, zero, negative, nonfinite, and nonnumeric goals do not burn', () => {
  for (const goal of [undefined, null, 0, -1, NaN, Infinity, -Infinity, '10']) {
    assert.equal(hasExceededGoal({calories: 100}, {calories: goal}), false);
  }
  for (const total of [undefined, null, NaN, Infinity, -Infinity, '100']) {
    assert.equal(hasExceededGoal({calories: total}, {calories: 10}), false);
  }
  assert.equal(hasExceededGoal(null, {calories: 10}), false);
  assert.equal(hasExceededGoal({calories: 100}, undefined), false);
  assert.equal(hasExceededGoal({calories: 100}, {}), false);
});

test('sleeping is idle and both active states use talking artwork, with matching burned variants', () => {
  for (const burned of [false, true]) {
    const suffix = burned ? '-burned' : '';
    for (const [state, pose] of [['idle', 'idle'], ['thinking', 'talking'], ['talking', 'talking']]) {
      assert.equal(assetFor(state, burned), `/assets/mellow/${pose}${suffix}.png`);
      assert.equal(assetFor(state, burned, true), `/assets/mellow/profile${suffix}.png`);
    }
  }
  assert.equal(assetFor('unknown'), '/assets/mellow/idle.png');
});

test('request lifecycle returns from talking to idle and cancels stale timers', () => {
  const clock = fakeClock();
  const rendered = [];
  const mascot = createController({...clock, render: value => rendered.push(value), talkingDuration: 1600});
  assert.equal(rendered[0].state, 'idle');
  mascot.startThinking();
  assert.equal(mascot.getState().state, 'thinking');
  mascot.respond();
  assert.equal(mascot.getState().state, 'talking');
  assert.equal([...clock.pending.values()][0].delay, 1600);
  const staleResponse = clock.callbacks[0];
  mascot.startThinking();
  assert.equal(clock.pending.size, 0);
  staleResponse();
  assert.equal(mascot.getState().state, 'thinking');
  mascot.respond();
  mascot.respond();
  assert.equal(clock.pending.size, 1);
  clock.finish();
  assert.equal(mascot.getState().state, 'idle');
});

test('burned state remains independent from thinking and response timing', () => {
  const clock = fakeClock();
  const tracker = createTracker(memoryStorage(), () => new Date('2026-10-21T16:00:00Z'));
  tracker.setGoals({protein_g: 20});
  tracker.adjust('set', {protein_g: 21});
  const mascot = createController({...clock});
  mascot.syncTracker(tracker);
  for (const [state, pose] of [['idle', 'idle'], ['thinking', 'talking'], ['talking', 'talking']]) {
    if (state === 'thinking') mascot.startThinking();
    if (state === 'talking') mascot.respond();
    assert.equal(mascot.getState().asset, `/assets/mellow/${pose}-burned.png`);
    assert.equal(mascot.getState().profileAsset, '/assets/mellow/profile-burned.png');
  }
  const responseTimer = [...clock.pending.keys()][0];
  tracker.adjust('subtract', {protein_g: 1});
  mascot.syncTracker(tracker);
  assert.equal(mascot.getState().state, 'talking');
  assert.equal(mascot.getState().burned, false);
  assert.deepEqual([...clock.pending.keys()], [responseTimer]);
  clock.finish();
  assert.equal(mascot.getState().asset, '/assets/mellow/idle.png');
});

test('tracker adjustments, reset, goal edits, and backend refresh all recompute burned state', () => {
  const storage = memoryStorage();
  const tracker = createTracker(storage, () => new Date('2026-10-21T16:00:00Z'));
  const mascot = createController();
  tracker.setGoals({calories: 100});
  tracker.adjust('set', {calories: 101});
  assert.equal(mascot.syncTracker(tracker).burned, true);
  tracker.setGoals({calories: 150});
  assert.equal(mascot.syncTracker(tracker).burned, false);
  tracker.setGoals({calories: 50});
  assert.equal(mascot.syncTracker(tracker).burned, true);
  tracker.reset();
  assert.equal(mascot.syncTracker(tracker).burned, false);

  // Mirror the app's server refresh: overwrite persisted data, then reload it.
  storage.setItem(KEY, JSON.stringify({date: '2026-10-21', totals: {
    calories: 75, protein_g: 0, carbohydrates_g: 0, fat_g: 0
  }, addedResultIds: []}));
  tracker.reload();
  assert.equal(mascot.syncTracker(tracker).burned, true);
  storage.setItem(GOALS_KEY, JSON.stringify({calories: 80}));
  tracker.reloadGoals();
  assert.equal(mascot.syncTracker(tracker).burned, false);
  tracker.setGoals({calories: null});
  assert.equal(mascot.syncTracker(tracker).burned, false);
});

test('New York midnight resets the variant while persistent goals survive restart', () => {
  const storage = memoryStorage();
  let instant = new Date('2026-10-22T03:59:59Z');
  const tracker = createTracker(storage, () => instant);
  tracker.setGoals({fat_g: 20});
  tracker.adjust('set', {fat_g: 21});
  const first = createController();
  assert.equal(first.syncTracker(tracker).burned, true);
  const restarted = createController();
  assert.equal(restarted.syncTracker(createTracker(storage, () => instant)).burned, true);
  instant = new Date('2026-10-22T04:00:00Z');
  assert.equal(first.syncTracker(tracker).burned, false);
  assert.equal(tracker.getState().date, '2026-10-22');
  assert.equal(tracker.getGoals().fat_g, 20);
  assert.equal(createController().syncTracker(createTracker(storage, () => instant)).burned, false);
});

test('failure, reset, and disposal cancel pending callbacks', () => {
  const clock = fakeClock();
  let renders = 0;
  const mascot = createController({...clock, render: () => renders++});
  mascot.respond();
  mascot.fail();
  assert.equal(mascot.getState().state, 'idle');
  assert.equal(clock.pending.size, 0);
  mascot.respond();
  mascot.reset();
  assert.equal(mascot.getState().state, 'idle');
  assert.equal(clock.pending.size, 0);
  mascot.respond();
  const staleResponse = clock.callbacks.at(-1);
  mascot.dispose();
  const previousRenders = renders;
  assert.equal(clock.pending.size, 0);
  staleResponse();
  mascot.respond();
  mascot.startThinking();
  assert.equal(clock.pending.size, 0);
  assert.equal(renders, previousRenders);
});
