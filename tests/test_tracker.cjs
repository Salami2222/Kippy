const assert = require('node:assert/strict');
const {test} = require('node:test');
const {createTracker, newYorkDate, nextDayBoundary} = require('../macromellow/static/tracker.js');

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
