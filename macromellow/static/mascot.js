/* Presentation state only. Macro values remain owned by MacroTracker. */
(function (root) {
  'use strict';

  const FIELDS = ['calories', 'protein_g', 'carbohydrates_g', 'fat_g'];
  const CELEBRATION_DURATION = 1685; // Sum of happy-bounce-animation.json durations_ms.
  const STATES = ['idle', 'thinking', 'talking'];

  function hasExceededGoal(totals, goals) {
    if (!totals || !goals) return false;
    return FIELDS.some(field => {
      const goal = goals[field];
      const total = totals[field];
      return typeof goal === 'number' && Number.isFinite(goal) && goal > 0 &&
        typeof total === 'number' && Number.isFinite(total) && total > goal;
    });
  }

  function assetFor(state, burned = false, profile = false) {
    if (state === 'celebrating' && !profile) return '/assets/mellow/happy_bounce_00.png';
    // Processing and responding share the happy pose; keep their timing states distinct.
    const pose = profile ? 'profile' : state === 'thinking' ? 'talking' : STATES.includes(state) ? state : 'idle';
    if (pose === 'idle') return burned ? '/assets/mellow/burned-sleep-00.png' : '/assets/mellow/sleep_00.png';
    if (pose === 'talking') return burned ? '/assets/mellow/burned-talk-00.png' : '/assets/mellow/talk_00.png';
    return `/assets/mellow/${pose}${burned ? '-burned' : ''}.png`;
  }

  function createController(options = {}) {
    const render = options.render || (() => {});
    const schedule = options.setTimeout || root.setTimeout.bind(root);
    const cancel = options.clearTimeout || root.clearTimeout.bind(root);
    const duration = Number.isFinite(options.talkingDuration) && options.talkingDuration >= 0 ?
      options.talkingDuration : 1600;
    let state = 'idle';
    let burned = false;
    let timer = null;
    let revision = 0;
    let disposed = false;
    let celebrationTimer = null;

    function getState() {
      const visible = celebrationTimer !== null ? 'celebrating' : state;
      return {state: visible, burned, asset: assetFor(visible, burned), profileAsset: assetFor(state, burned, true)};
    }

    function emit() { if (!disposed) render(getState()); }

    function clearTimer() {
      revision += 1;
      if (timer !== null) cancel(timer);
      timer = null;
    }

    function setState(next) {
      if (disposed) return getState();
      clearTimer();
      state = next;
      emit();
      return getState();
    }

    function startThinking() { return setState('thinking'); }

    function respond() {
      if (disposed) return getState();
      setState('talking');
      const current = revision;
      timer = schedule(() => {
        if (disposed || current !== revision) return;
        timer = null;
        state = 'idle';
        emit();
      }, duration);
      return getState();
    }

    function celebrate() {
      if (disposed || celebrationTimer !== null) return getState();
      celebrationTimer = schedule(() => {
        if (disposed) return;
        celebrationTimer = null;
        emit(); // The underlying activity/burned state may have changed during playback.
      }, CELEBRATION_DURATION);
      emit();
      return getState();
    }

    function reset() { return setState('idle'); }

    function syncTracker(tracker) {
      if (disposed) return getState();
      // getState checks the New York date and rolls the tracker forward if needed.
      const next = hasExceededGoal(tracker.getState().totals, tracker.getGoals());
      if (next !== burned) {
        burned = next;
        emit();
      }
      return getState();
    }

    function dispose() {
      clearTimer();
      if (celebrationTimer !== null) cancel(celebrationTimer);
      celebrationTimer = null;
      disposed = true;
    }

    emit();
    return {celebrate, startThinking, respond, fail: reset, reset, syncTracker, getState, dispose};
  }

  const api = {CELEBRATION_DURATION, hasExceededGoal, assetFor, createController};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MellowMascot = api;
})(typeof window === 'undefined' ? globalThis : window);
