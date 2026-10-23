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

test('calorie limits and unchanged carb/fat goals burn only when strictly exceeded', () => {
  for (const field of fields.filter(field => field !== 'protein_g')) {
    assert.equal(hasExceededGoal({[field]: 9}, {[field]: 10}), false);
    assert.equal(hasExceededGoal({[field]: 10}, {[field]: 10}), false);
    assert.equal(hasExceededGoal({[field]: 10.01}, {[field]: 10}), true);
    assert.equal(hasExceededGoal({calories: 999, [field]: 11}, {[field]: 10}), true);
  }
});

test('unconfigured, negative, nonfinite, and nonnumeric limits do not burn', () => {
  for (const goal of [undefined, null, -1, NaN, Infinity, -Infinity, '10']) {
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
      assert.equal(assetFor(state, burned), !burned ? (pose === 'idle' ? '/assets/mellow/sleep_00.png' : '/assets/mellow/talk_00.png') : `/assets/mellow/burned-${pose === "idle" ? "sleep" : "talk"}-00.png`);
      assert.equal(assetFor(state, burned, true), `/assets/mellow/profile${suffix}.png`);
    }
  }
  assert.equal(assetFor('unknown'), '/assets/mellow/sleep_00.png');
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
  tracker.setGoals({calories: 20});
  tracker.adjust('set', {calories: 21});
  const mascot = createController({...clock});
  mascot.syncTracker(tracker);
  for (const [state, pose] of [['idle', 'idle'], ['thinking', 'talking'], ['talking', 'talking']]) {
    if (state === 'thinking') mascot.startThinking();
    if (state === 'talking') mascot.respond();
    assert.equal(mascot.getState().asset, `/assets/mellow/burned-${pose === "idle" ? "sleep" : "talk"}-00.png`);
    assert.equal(mascot.getState().profileAsset, '/assets/mellow/profile-burned.png');
  }
  const responseTimer = [...clock.pending.keys()][0];
  tracker.adjust('subtract', {calories: 1});
  mascot.syncTracker(tracker);
  assert.equal(mascot.getState().state, 'talking');
  assert.equal(mascot.getState().burned, false);
  assert.deepEqual([...clock.pending.keys()], [responseTimer]);
  clock.finish();
  assert.equal(mascot.getState().asset, '/assets/mellow/sleep_00.png');
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

test('celebration overrides burned activity while async responses and tracker updates continue underneath', () => {
  const clock = fakeClock();
  const mascot = createController({...clock});
  const tracker = createTracker(memoryStorage(), () => new Date('2026-10-22T16:00:00Z'));
  tracker.setGoals({protein_g: 10, calories: 100});
  tracker.adjust('set', {protein_g: 11, calories: 101});
  mascot.syncTracker(tracker);
  mascot.startThinking();
  mascot.celebrate();
  const celebration = [...clock.pending.values()].find(t => t.delay === 1685);
  assert.equal(mascot.getState().state, 'celebrating');
  assert.equal(mascot.getState().asset, '/assets/mellow/happy_bounce_00.png');
  mascot.respond();
  mascot.syncTracker(tracker);
  mascot.celebrate();
  assert.equal(clock.pending.size, 2); // Duplicate calls cannot restart the overlay.
  assert.equal(mascot.getState().state, 'celebrating');
  celebration.callback();
  assert.equal(mascot.getState().state, 'talking');
  assert.equal(mascot.getState().burned, true);
  assert.equal(mascot.getState().asset, '/assets/mellow/burned-talk-00.png');
});

test('celebration completion restores the latest idle or thinking state and disposal cancels all timers', () => {
  for (const next of ['idle', 'thinking']) {
    const clock = fakeClock();
    const mascot = createController({...clock});
    mascot.celebrate();
    if (next === 'idle') mascot.fail(); else mascot.startThinking();
    assert.equal(mascot.getState().state, 'celebrating');
    clock.finish();
    assert.equal(mascot.getState().state, next);
    mascot.celebrate(); mascot.respond(); mascot.dispose();
    assert.equal(clock.pending.size, 0);
    clock.callbacks.forEach(callback => callback());
    assert.notEqual(mascot.getState().state, 'celebrating');
  }
});

test('protein above its minimum never burns; a configured zero calorie limit is still a limit', () => {
  for (const total of [9, 10, 11, 999]) {
    assert.equal(hasExceededGoal({protein_g: total, calories: 2500}, {protein_g: 10, calories: 2500}), false);
  }
  assert.equal(hasExceededGoal({protein_g: 200, calories: 2501}, {protein_g: 180, calories: 2500}), true);
  assert.equal(hasExceededGoal({calories: 0}, {calories: 0}), false);
  assert.equal(hasExceededGoal({calories: 1}, {calories: 0}), true);
  for (const field of ['carbohydrates_g', 'fat_g']) {
    assert.equal(hasExceededGoal({[field]: 1}, {[field]: 0}), false);
    assert.equal(hasExceededGoal({[field]: 11}, {[field]: 10}), true);
  }
});
