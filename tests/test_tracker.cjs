const assert = require('node:assert/strict');
const {test} = require('node:test');
const {createTracker, newYorkDate, nextDayBoundary, goalProgress} = require('../macromellow/static/tracker.js');

function memoryStorage() {
  const data = new Map();
  return {getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value)};
}

test('uses New York dates and finds midnight across daylight saving changes', () => {
  assert.equal(newYorkDate(new Date('2026-10-20T03:59:59.999Z')), '2026-10-19');
  assert.equal(newYorkDate(new Date('2026-10-20T04:00:00.000Z')), '2026-10-20');
  assert.equal(new Date(nextDayBoundary(new Date('2026-10-20T01:00:00Z'))).toISOString(), '2026-10-20T04:00:00.000Z');
  assert.equal(new Date(nextDayBoundary(new Date('2026-11-01T04:00:01Z'))).toISOString(), '2026-11-02T05:00:00.000Z');
});

test('adjustments change fields independently and persist across tracker restart', () => {
  const storage = memoryStorage();
  const now = () => new Date('2026-10-20T16:00:00Z');
  const tracker = createTracker(storage, now);
  tracker.adjust('add', {calories: 230.25, protein_g: 12});
  tracker.adjust('subtract', {protein_g: 2});
  tracker.adjust('set', {carbohydrates_g: 45, fat_g: 0});
  assert.deepEqual(createTracker(storage, now).getState().totals,
    {calories: 230.25, protein_g: 10, carbohydrates_g: 45, fat_g: 0});
  tracker.adjust('subtract', {calories: 250.25});
  assert.equal(tracker.getState().totals.calories, -20);
  assert.throws(() => tracker.adjust('add', {}), /at least one/);
});

test('AI result must be complete and can be added only once', () => {
  const tracker = createTracker(memoryStorage(), () => new Date('2026-10-20T16:00:00Z'));
  const result = {calories: 360, protein_g: 48.8, carbohydrates_g: 3.8, fat_g: 15.4};
  assert.equal(tracker.addResult('turn-1', result).added, true);
  assert.equal(tracker.addResult('turn-1', result).added, false);
  assert.equal(tracker.getState().totals.calories, 360);
  assert.throws(() => tracker.addResult('turn-2', {...result, fat_g: null}), /incomplete/);
  assert.equal(tracker.getState().totals.fat_g, 15.4);
});

test('a running tracker starts a new zero day and resets duplicate protection', () => {
  const storage = memoryStorage();
  let instant = new Date('2026-10-20T03:59:59Z');
  const tracker = createTracker(storage, () => instant);
  const result = {calories: 100, protein_g: 5, carbohydrates_g: 10, fat_g: 4};
  tracker.addResult('turn-1', result);
  assert.equal(tracker.getState().date, '2026-10-19');
  instant = new Date('2026-10-20T04:00:00Z');
  assert.equal(tracker.checkDay(), true);
  assert.deepEqual(tracker.getState().totals, {calories: 0, protein_g: 0, carbohydrates_g: 0, fat_g: 0});
  assert.equal(createTracker(storage, () => instant).getState().date, '2026-10-20');
  assert.equal(tracker.addResult('turn-1', result).added, true);
  tracker.reset();
  assert.equal(tracker.getState().totals.calories, 0);
  assert.equal(tracker.addResult('turn-1', result).added, true);
});

test('goals persist independently through daily rollover, reset, and restart', () => {
  const storage = memoryStorage();
  let instant = new Date('2026-10-20T03:59:59Z');
  const now = () => instant;
  const tracker = createTracker(storage, now);
  tracker.setGoals({calories: 2000, protein_g: 120});
  tracker.adjust('set', {calories: 500, protein_g: 50});
  assert.equal(tracker.getGoals().carbohydrates_g, null);
  assert.equal(tracker.getGoals().calories, 2000);
  assert.equal(tracker.getState().totals.protein_g, 50);
  instant = new Date('2026-10-20T04:00:00Z');
  assert.equal(tracker.checkDay(), true);
  assert.equal(tracker.getState().totals.calories, 0);
  assert.equal(tracker.getGoals().calories, 2000);
  tracker.reset();
  assert.equal(tracker.getGoals().protein_g, 120);
  const restarted = createTracker(storage, now);
  assert.equal(restarted.getGoals().calories, 2000);
  restarted.setGoals({protein_g: null, fat_g: 70});
  assert.deepEqual(restarted.getGoals(), {calories: 2000, protein_g: null, carbohydrates_g: null, fat_g: 70});
  assert.throws(() => restarted.setGoals({calories: -1}), /zero or positive/);
  assert.equal(restarted.getGoals().calories, 2000);
});

test('goal progress reports remaining or exceeded without deriving calories', () => {
  assert.deepEqual(goalProgress(600, 1000), {remaining: 400, exceeded: 0, percent: 60});
  assert.deepEqual(goalProgress(1100, 1000), {remaining: 0, exceeded: 100, percent: 100});
  assert.deepEqual(goalProgress(5, 0), {remaining: 0, exceeded: 5, percent: 100});
  assert.equal(goalProgress(25, null), null);
  const tracker = createTracker(memoryStorage(), () => new Date('2026-10-20T16:00:00Z'));
  tracker.setGoals({protein_g: 100});
  tracker.adjust('set', {protein_g: 50});
  assert.equal(tracker.getState().totals.calories, 0);
  assert.equal(tracker.getGoals().calories, null);
});
