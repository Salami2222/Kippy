const els = {
  chatTab: document.getElementById('chat-tab'),
  trackerTab: document.getElementById('tracker-tab'),
  browseTab: document.getElementById('browse-tab'),
  chatView: document.getElementById('chat-view'),
  chatTop: document.querySelector('.chat-top'),
  trackerPanel: document.querySelector('.daily-tracker'),
  trackerExpand: document.getElementById('tracker-expand'),
  browseView: document.getElementById('browse-view'),
  toastStack: document.getElementById('toast-stack'),
  messages: document.getElementById('chat-messages'),
  welcome: document.getElementById('chat-welcome'),
  loading: document.getElementById('chat-loading'),
  form: document.getElementById('chat-form'),
  input: document.getElementById('chat-input'),
  send: document.getElementById('send'),
  newChat: document.getElementById('new-chat'),
  aiStatus: document.getElementById('ai-status'),
  aiTitle: document.getElementById('ai-status-title'),
  aiDetail: document.getElementById('ai-status-detail'),
  trackerDate: document.getElementById('tracker-date'),
  trackerValues: {calories: document.getElementById('tracker-calories'), protein_g: document.getElementById('tracker-protein'),
    carbohydrates_g: document.getElementById('tracker-carbs'), fat_g: document.getElementById('tracker-fat')},
  goalValues: {calories: document.getElementById('goal-calories'), protein_g: document.getElementById('goal-protein'),
    carbohydrates_g: document.getElementById('goal-carbs'), fat_g: document.getElementById('goal-fat')},
  goalBars: {calories: document.getElementById('progress-calories'), protein_g: document.getElementById('progress-protein'),
    carbohydrates_g: document.getElementById('progress-carbs'), fat_g: document.getElementById('progress-fat')},
  goalBalances: {calories: document.getElementById('balance-calories'), protein_g: document.getElementById('balance-protein'),
    carbohydrates_g: document.getElementById('balance-carbs'), fat_g: document.getElementById('balance-fat')},
  goalControls: document.getElementById('goal-controls'),
  goalForm: document.getElementById('goal-form'),
  goalInputs: {calories: document.getElementById('goal-input-calories'), protein_g: document.getElementById('goal-input-protein'),
    carbohydrates_g: document.getElementById('goal-input-carbs'), fat_g: document.getElementById('goal-input-fat')},
  trackerForm: document.getElementById('tracker-form'),
  trackerMode: document.getElementById('tracker-mode'),
  trackerInputs: {calories: document.getElementById('tracker-input-calories'), protein_g: document.getElementById('tracker-input-protein'),
    carbohydrates_g: document.getElementById('tracker-input-carbs'), fat_g: document.getElementById('tracker-input-fat')},
  trackerReset: document.getElementById('tracker-reset'),
  trackerStatus: document.getElementById('tracker-status'),
  mealList: document.getElementById('meal-list'),
  memoryList: document.getElementById('memory-list'),
  memoryForm: document.getElementById('memory-form'),
  memoryCategory: document.getElementById('memory-category'),
  memoryKey: document.getElementById('memory-key'),
  memoryValue: document.getElementById('memory-value'),
  memoryCancel: document.getElementById('memory-cancel'),
  search: document.getElementById('search'),
  hall: document.getElementById('hall'),
  date: document.getElementById('menu-date'),
  refresh: document.getElementById('refresh'),
  refreshStatus: document.getElementById('refresh-status'),
  loadErrors: document.getElementById('load-errors'),
  meta: document.getElementById('dataset-meta'),
  count: document.getElementById('result-count'),
  empty: document.getElementById('empty'),
  table: document.getElementById('table-wrap'),
  rows: document.getElementById('food-rows'),
  shown: document.getElementById('shown-count'),
  more: document.getElementById('show-more')
};

let datasets = new Map();
let visibleLimit = 100;
let polling = null;
let wasRefreshing = false;
let lastRefreshState = null;
let conversationId = null;
try { conversationId = window.localStorage.getItem('macromellow.conversation.v1'); } catch { /* Browser storage is optional. */ }
let chatBusy = false;
let historyLoading = Promise.resolve();
let trackerStorage;
let trackerPersists = true;
try {
  trackerStorage = window.localStorage;
  trackerStorage.getItem(MacroTracker.KEY);
} catch {
  trackerPersists = false;
  const memory = new Map();
  trackerStorage = {getItem: key => memory.get(key) || null, setItem: (key, value) => memory.set(key, value)};
}
let tracker = MacroTracker.createTracker(trackerStorage);
let trackerTimer = null;
let backendEntries = [];
let savedMemories = [];
let memoryEditingId = null;
const mascot = MellowMascot.createController({render: renderMascot});
mascot.syncTracker(tracker);

function renderMascot(snapshot) {
  document.querySelectorAll('[data-mellow-mascot]').forEach(slot => {
    slot.dataset.state = snapshot.state;
    slot.dataset.burned = String(snapshot.burned);
    const img = slot.querySelector('img');
    if (img.getAttribute('src') !== snapshot.asset) img.setAttribute('src', snapshot.asset);
  });
  document.querySelectorAll('[data-mellow-profile]').forEach(img => {
    if (img.getAttribute('src') !== snapshot.profileAsset) img.setAttribute('src', snapshot.profileAsset);
  });
}

async function stateRequest(path, payload) {
  return request(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-MacroMellow-Request': 'state'},
    body: JSON.stringify(payload)});
}

function applyBackendTracker(snapshot) {
  backendEntries = snapshot.entries || [];
  const stateJSON = JSON.stringify({date: snapshot.date, totals: snapshot.totals,
    addedResultIds: snapshot.addedResultIds || []});
  const goalsJSON = JSON.stringify(snapshot.goals);
  if (trackerStorage.getItem(MacroTracker.KEY) !== stateJSON) trackerStorage.setItem(MacroTracker.KEY, stateJSON);
  if (trackerStorage.getItem(MacroTracker.GOALS_KEY) !== goalsJSON) trackerStorage.setItem(MacroTracker.GOALS_KEY, goalsJSON);
  tracker.reload();
  tracker.reloadGoals();
  renderTracker();
  renderMealList();
}

async function loadBackendTracker() {
  const migrationFlag = 'macromellow.backend-migrated.v1';
  const previous = trackerStorage.getItem(MacroTracker.KEY);
  if (!trackerStorage.getItem(migrationFlag) && previous) {
    try {
      const goalBackup = trackerStorage.getItem(MacroTracker.GOALS_KEY);
      trackerStorage.setItem('macromellow.browser-backup.v1', JSON.stringify({state: JSON.parse(previous),
        goals: goalBackup ? JSON.parse(goalBackup) : null}));
      const migrationIdKey = 'macromellow.migration-id.v1';
      let migrationId = trackerStorage.getItem(migrationIdKey);
      if (!migrationId) {
        migrationId = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `browser-${Date.now()}`;
        trackerStorage.setItem(migrationIdKey, migrationId);
      }
      const imported = await stateRequest('/api/tracker/import', {migration_key: migrationId,
        state: JSON.parse(previous), goals: goalBackup ? JSON.parse(goalBackup) : {}});
      if (imported.conflict) showToast('Backend totals already exist. Your earlier browser totals remain in a local backup for review.', 'info');
      trackerStorage.setItem(migrationFlag, '1');
      applyBackendTracker(imported.tracker);
      return;
    } catch (error) {
      renderTracker();
      trackerMessage(`Could not import saved browser totals: ${error.message}`, true);
      return;
    }
  }
  const snapshot = await request('/api/tracker');
  applyBackendTracker(snapshot);
  trackerStorage.setItem(migrationFlag, '1');
}

function renderMealList() {
  els.mealList.replaceChildren();
  const replaced = new Set(backendEntries.filter(entry => entry.kind === 'meal' && entry.status === 'active')
    .map(entry => entry.replaces_id).filter(Boolean));
  const meals = backendEntries.filter(entry => entry.kind === 'meal' && !replaced.has(entry.id));
  if (!meals.length) {
    const empty = document.createElement('p'); empty.className = 'list-empty';
    empty.textContent = 'No meals logged today.'; els.mealList.append(empty); return;
  }
  for (const meal of meals) {
    const item = document.createElement('div'); item.className = 'personal-item';
    const title = document.createElement('strong'); title.textContent = meal.label;
    const detail = document.createElement('span');
    detail.textContent = `${meal.status === 'removed' ? 'Removed · ' : ''}${meal.cards.map(card => `${card.quantity} × ${card.name}`).join(', ')} · ${trackerNumber(meal.delta.calories)} kcal`;
    const actions = document.createElement('div'); actions.className = 'personal-actions';
    const remove = document.createElement('button'); remove.type = 'button';
    remove.textContent = meal.status === 'active' ? 'Remove meal' : 'Restore meal';
    remove.addEventListener('click', async () => {
      try { const updated = await stateRequest(meal.status === 'active' ? '/api/meal/remove' : '/api/meal/restore',
        {entry_id: meal.id});
        applyBackendTracker(updated.tracker); trackerMessage(meal.status === 'active' ? 'Logged meal removed. You can restore it here.' : 'Logged meal restored.'); }
      catch (error) { trackerMessage(error.message, true); }
    });
    actions.append(remove); item.append(title, detail);
    for (const card of meal.cards) {
      const url = safeSource(card.source_url);
      if (!url) continue;
      const link = document.createElement('a'); link.className = 'source-link'; link.href = url;
      link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = `${card.name} · UMD label ↗`;
      item.append(link);
    }
    item.append(actions); els.mealList.append(item);
  }
}

function renderMemoryList() {
  els.memoryList.replaceChildren();
  if (!savedMemories.length) {
    const empty = document.createElement('p'); empty.className = 'list-empty';
    empty.textContent = 'No saved preferences or favorites.'; els.memoryList.append(empty); return;
  }
  for (const memory of savedMemories) {
    const item = document.createElement('div'); item.className = 'personal-item';
    const title = document.createElement('strong'); title.textContent = memory.key;
    const detail = document.createElement('span'); detail.textContent = `${memory.category.replaceAll('_', ' ')} · ${memory.value}`;
    const actions = document.createElement('div'); actions.className = 'personal-actions';
    const edit = document.createElement('button'); edit.type = 'button'; edit.textContent = 'Edit';
    edit.addEventListener('click', () => { memoryEditingId = memory.id; els.memoryCategory.value = memory.category;
      els.memoryKey.value = memory.key; els.memoryValue.value = memory.value;
      els.memoryCancel.hidden = false; els.memoryKey.focus(); });
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Delete';
    remove.addEventListener('click', async () => {
      try { const updated = await stateRequest('/api/memory/delete', {id: memory.id});
        savedMemories = updated.memories; renderMemoryList(); trackerMessage('Personal memory deleted.'); }
      catch (error) { trackerMessage(error.message, true); }
    });
    actions.append(edit, remove); item.append(title, detail, actions); els.memoryList.append(item);
  }
}

function trackerMessage(message, error = false) {
  els.trackerStatus.textContent = message;
  els.trackerStatus.classList.toggle('error', error);
  showToast(message, error ? 'error' : 'success');
}

function showToast(message, kind = 'success') {
  const toast = document.createElement('div');
  toast.className = `toast ${kind}`;
  const icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.setAttribute('aria-hidden', 'true');
  icon.textContent = kind === 'error' ? '!' : kind === 'info' ? 'i' : '✓';
  const content = document.createElement('span');
  content.textContent = message;
  const close = document.createElement('button');
  close.type = 'button';
  close.setAttribute('aria-label', 'Dismiss notification');
  close.textContent = '×';
  close.addEventListener('click', () => toast.remove());
  toast.append(icon, content, close);
  els.toastStack.append(toast);
  while (els.toastStack.children.length > 3) els.toastStack.firstElementChild.remove();
  setTimeout(() => {
    if (!toast.isConnected) return;
    toast.classList.add('leaving');
    setTimeout(() => toast.remove(), 190);
  }, 3900);
}

function trackerNumber(value) {
  return Number(value).toLocaleString(undefined, {maximumFractionDigits: 2});
}

function populateGoalInputs() {
  const goals = tracker.getGoals();
  for (const field of MacroTracker.FIELDS) els.goalInputs[field].value = goals[field] === null ? '' : goals[field];
}

function renderTracker() {
  const state = tracker.getState();
  const goals = tracker.getGoals();
  mascot.syncTracker(tracker);
  els.trackerDate.textContent = humanDate(state.date);
  for (const field of MacroTracker.FIELDS) {
    const consumed = state.totals[field];
    const goal = goals[field];
    const unit = field === 'calories' ? 'kcal' : 'g';
    const value = els.trackerValues[field];
    const nextText = trackerNumber(consumed);
    if (value.textContent !== nextText) {
      value.textContent = nextText;
      value.classList.remove('value-change');
      void value.offsetWidth;
      value.classList.add('value-change');
    }
    els.goalValues[field].textContent = goal === null ? 'No goal set' : `Goal ${trackerNumber(goal)} ${unit}`;
    const bar = els.goalBars[field];
    const balance = els.goalBalances[field];
    bar.hidden = goal === null;
    if (goal === null) {
      balance.textContent = '';
      balance.classList.remove('exceeded');
    } else {
      const progress = MacroTracker.goalProgress(consumed, goal);
      bar.querySelector('i').style.width = `${progress.percent}%`;
      bar.setAttribute('aria-valuenow', String(progress.percent));
      const wording = progress.exceeded > 0 ? `Exceeded by ${trackerNumber(progress.exceeded)} ${unit}` :
        `${trackerNumber(progress.remaining)} ${unit} remaining`;
      balance.textContent = wording;
      balance.classList.toggle('exceeded', progress.exceeded > 0);
      bar.setAttribute('aria-valuetext', `${trackerNumber(consumed)} of ${trackerNumber(goal)} ${unit} consumed; ${wording}`);
    }
  }
  document.querySelectorAll('.add-result-button').forEach(button => {
    const old = button.dataset.trackerDate !== state.date;
    const added = state.addedResultIds.includes(button.dataset.resultId);
    button.disabled = old || added;
    button.textContent = old ? 'Previous day result' : added ? 'Added to today ✓' : 'Add result to today';
  });
}

async function checkTrackerDay() {
  try {
    if (tracker.checkDay()) {
      // Update the local day immediately, even while the server is reconnecting.
      renderTracker();
      await loadBackendTracker();
      trackerMessage('New York day changed. Today’s totals started at zero.');
      scheduleTrackerRollover();
    }
  } catch (error) { trackerMessage(`Could not load daily totals: ${error.message}`, true); }
}

function scheduleTrackerRollover() {
  if (trackerTimer) clearTimeout(trackerTimer);
  trackerTimer = setTimeout(() => { checkTrackerDay(); scheduleTrackerRollover(); },
    Math.max(1, MacroTracker.nextDayBoundary(new Date()) - Date.now() + 25));
}

function setTrackerExpanded(expanded) {
  els.trackerPanel.classList.toggle('is-collapsed', !expanded);
  els.trackerExpand.setAttribute('aria-expanded', String(expanded));
  els.trackerExpand.firstChild.textContent = expanded ? 'Hide details ' : 'Show details ';
}

function switchView(view) {
  const browsing = view === 'browse';
  els.chatView.hidden = browsing;
  els.browseView.hidden = !browsing;
  els.chatTab.classList.toggle('active', view === 'chat');
  els.trackerTab.classList.toggle('active', view === 'tracker');
  els.browseTab.classList.toggle('active', browsing);
  els.chatTab.toggleAttribute('aria-current', view === 'chat');
  els.trackerTab.toggleAttribute('aria-current', view === 'tracker');
  els.browseTab.toggleAttribute('aria-current', browsing);
  if (window.matchMedia('(max-width: 900px)').matches) {
    if (view === 'tracker') setTrackerExpanded(true);
    if (view === 'chat') setTrackerExpanded(false);
  }
  const target = browsing ? els.browseView : view === 'tracker' ? els.trackerPanel : els.chatTop;
  const scrollBehavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  requestAnimationFrame(() => target.scrollIntoView({block: 'start', behavior: scrollBehavior}));
  if (browsing) els.search.focus({preventScroll: true});
  else if (view === 'chat') els.input.focus({preventScroll: true});
}

function formatAmount(value, unit = '') {
  return value === null || value === undefined ? 'Unavailable' : `${Number(value).toLocaleString(undefined, {maximumFractionDigits: 1})}${unit}`;
}

function safeSource(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && parsed.hostname === 'nutrition.umd.edu' && parsed.pathname === '/label.aspx' ? parsed.href : null;
  } catch { return null; }
}

function appendBubble(role, text) {
  const shouldScroll = role === 'user' || nearMessagesBottom();
  const bubble = document.createElement('div');
  bubble.className = `message ${role}`;
  const label = document.createElement('span');
  label.className = 'message-label';
  label.textContent = role === 'user' ? 'You' : 'Mellow';
  if (role === 'assistant') {
    const profile = document.createElement('img');
    profile.className = 'mellow-profile';
    profile.dataset.mellowProfile = '';
    profile.src = mascot.getState().profileAsset;
    profile.alt = '';
    profile.width = 24;
    profile.height = 24;
    label.prepend(profile);
  }
  const body = document.createElement('p');
  body.textContent = text;
  bubble.append(label, body);
  els.messages.append(bubble);
  if (shouldScroll) requestAnimationFrame(() => { els.messages.scrollTop = els.messages.scrollHeight; });
  return bubble;
}

function nearMessagesBottom() {
  return els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight < 100;
}

function nutritionGrid(values) {
  const grid = document.createElement('div');
  grid.className = 'nutrition-grid';
  for (const [label, key, unit] of [['Calories', 'calories', ''], ['Protein', 'protein_g', ' g'],
                                    ['Carbs', 'carbohydrates_g', ' g'], ['Fat', 'fat_g', ' g']]) {
    const stat = document.createElement('div');
    stat.className = 'nutrition-stat';
    const value = document.createElement('strong');
    value.textContent = formatAmount(values?.[key], unit);
    const title = document.createElement('span');
    title.textContent = label;
    stat.append(value, title);
    grid.append(stat);
  }
  return grid;
}

function debugRow(label, value) {
  const row = document.createElement('div');
  row.className = 'debug-row';
  const title = document.createElement('strong');
  title.textContent = label;
  const content = document.createElement('span');
  content.textContent = value === null || value === undefined || value === '' ? 'Not specified' : String(value);
  row.append(title, content);
  return row;
}

function debugSection(parent, heading) {
  const section = document.createElement('section');
  section.className = 'debug-section';
  const title = document.createElement('h4');
  title.textContent = heading;
  section.append(title);
  parent.append(section);
  return section;
}

function debugSource(parent, candidate, prefix = '') {
  const line = document.createElement('div');
  line.className = 'debug-candidate';
  const name = document.createElement('span');
  const value = Number(candidate.name_score);
  const level = value >= .98 ? 'exact name' : value >= .86 ? 'strong name match' : value >= .55 ? 'uncertain name match' : 'weak name match';
  const score = candidate.name_score === undefined ? '' : ` · ${level} (${value.toFixed(2)})`;
  const context = [candidate.meal, candidate.station].filter(Boolean).join(' · ');
  name.textContent = `${prefix}${candidate.name || 'Unnamed food'} · ${hallLabel(candidate.dining_hall)} · ${candidate.menu_date || 'date unknown'}${context ? ` · ${context}` : ''}${score}`;
  line.append(name);
  const url = safeSource(candidate.source_url);
  if (url) {
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = 'UMD label ↗';
    line.append(link);
  }
  parent.append(line);
}

function debugFood(food) {
  const hall = ({yahentamitsi: 'Yahentamitsi (The Y)', south: 'South Campus Dining Hall', '251-north': '251 North'})[food.hall];
  return `${food.name || 'Unnamed food'} · ${food.quantity} ${food.unit || 'unit unknown'} · ${hall ? hallLabel(hall) : 'hall not specified'} · ${food.date || 'date not specified'}`;
}

function appendDebug(bubble, debug) {
  if (!debug) return;
  const details = document.createElement('details');
  details.className = 'debug-panel';
  const summary = document.createElement('summary');
  const warningCount = debug.warnings?.length || 0;
  summary.textContent = `Developer debugging${debug.error ? ' · error' : warningCount ? ` · ${warningCount} note${warningCount === 1 ? '' : 's'}` : ''}`;
  details.append(summary);
  const body = document.createElement('div');
  body.className = 'debug-body';
  const requestSection = debugSection(body, 'Request and Mellow interpretation');
  requestSection.append(debugRow('Original request', debug.user_request));
  requestSection.append(debugRow('Model', debug.model));
  requestSection.append(debugRow('Action', debug.model_interpretation?.action));
  requestSection.append(debugRow('Backend operation', debug.model_interpretation?.operation || debug.tool_action?.operation || 'None'));
  requestSection.append(debugRow('Response type', debug.response_type || debug.model_interpretation?.response_type));
  if (debug.planner) requestSection.append(debugRow('Planner diagnostics', JSON.stringify(debug.planner)));
  if (debug.pending_clarification) requestSection.append(debugRow('Pending clarification', JSON.stringify(debug.pending_clarification)));
  if (debug.reaction_target) requestSection.append(debugRow('Reaction target message', debug.reaction_target));
  if (debug.model_interpretation?.target) requestSection.append(debugRow('Target meal', debug.model_interpretation.target));
  if (debug.model_interpretation?.label) requestSection.append(debugRow('Meal label', debug.model_interpretation.label));
  if (debug.model_interpretation?.log_date) requestSection.append(debugRow('Log date', debug.model_interpretation.log_date));
  if (debug.model_interpretation?.memory_key) requestSection.append(debugRow('Memory',
    `${debug.model_interpretation.memory_category || 'unspecified'} · ${debug.model_interpretation.memory_key}: ${debug.model_interpretation.memory_value || ''}`));
  const extracted = debug.model_interpretation?.foods || [];
  if (extracted.length) extracted.forEach((food, index) => requestSection.append(debugRow(`Extracted food ${index + 1}`, debugFood(food))));
  else requestSection.append(debugRow('Extracted foods', 'None'));
  if (debug.resolved_foods?.length) debug.resolved_foods.forEach((food, index) => requestSection.append(debugRow(`Meal item ${index + 1}`, debugFood(food))));

  const matches = debugSection(body, 'UMD matching');
  matches.append(debugRow('Scoring', 'Heuristic coverage, token precision, and character similarity; not a probability. Hall and date filter the records first.'));
  if (!debug.matches?.length) matches.append(debugRow('Match attempts', 'None'));
  for (const match of debug.matches || []) {
    const group = document.createElement('div');
    group.className = 'debug-match';
    group.append(debugRow('Requested', `${match.requested?.name || 'Unknown'} · ${match.effective_hall ? hallLabel(match.effective_hall) : 'any hall'} · ${match.effective_date || 'date unknown'}`));
    group.append(debugRow('Decision', match.status));
    if (match.selected) {
      debugSource(group, match.selected, 'Selected: ');
      if (match.selected.signals) group.append(debugRow('Match signals', JSON.stringify(match.selected.signals)));
      group.append(debugRow('Listed serving', match.selected.serving_size || 'Missing from UMD label'));
    }
    const alternatives = (match.candidates || []).filter(candidate => !match.selected || candidate.name !== match.selected.name || candidate.source_url !== match.selected.source_url || candidate.dining_hall !== match.selected.dining_hall);
    alternatives.slice(0, 3).forEach(candidate => debugSource(group, candidate, 'Alternative: '));
    matches.append(group);
  }

  const math = debugSection(body, 'Nutrition calculation');
  if (!debug.calculations?.length) math.append(debugRow('Calculations', 'No official values calculated.'));
  for (const calculation of debug.calculations || []) {
    const group = document.createElement('div');
    group.className = 'debug-match';
    if (calculation.conversion) group.append(debugRow('Portion conversion', calculation.conversion));
    group.append(debugRow('Food', `${calculation.name} · ${calculation.quantity} listed serving(s) × ${calculation.serving_size || 'size unavailable'}`));
    for (const [label, key, unit] of [['Calories', 'calories', ''], ['Protein', 'protein_g', ' g'], ['Carbs', 'carbohydrates_g', ' g'], ['Fat', 'fat_g', ' g']]) {
      const per = calculation.per_serving?.[key];
      const value = calculation.result?.[key];
      group.append(debugRow(label, per === null || per === undefined ? 'UMD value missing → total unavailable' :
        `${formatAmount(per, unit)} per serving × ${calculation.quantity} = ${formatAmount(value, unit)}`));
    }
    math.append(group);
  }
  math.append(debugRow('Final total', debug.total ?
    `Calories ${formatAmount(debug.total.calories)} · Protein ${formatAmount(debug.total.protein_g, ' g')} · Carbs ${formatAmount(debug.total.carbohydrates_g, ' g')} · Fat ${formatAmount(debug.total.fat_g, ' g')}` :
    'No complete meal total calculated for this response.'));

  const flags = debugSection(body, 'Accuracy notes and timing');
  if (debug.tool_action) flags.append(debugRow('Tool result', JSON.stringify(debug.tool_action)));
  if (debug.warnings?.length) debug.warnings.forEach(note => {
    const warning = debugRow('Check', note);
    warning.classList.add('debug-alert');
    flags.append(warning);
  });
  else if (!debug.error) flags.append(debugRow('Checks', 'No specific assumption flagged; verify the label when accuracy matters.'));
  if (debug.error) {
    const error = debugRow(`Error · ${debug.error.stage}`, `${debug.error.message} · ${debug.error.category || 'unknown'} · ${JSON.stringify(debug.error.details || {})}`);
    error.classList.add('debug-alert');
    flags.append(error);
  }
  flags.append(debugRow('Mellow response time', debug.ai_response_time_ms == null ? 'Not called' : `${debug.ai_response_time_ms} ms`));
  flags.append(debugRow('Total response time', debug.response_time_ms == null ? 'Unavailable' : `${debug.response_time_ms} ms`));
  details.append(body);
  bubble.append(details);
}

function appendResult(data) {
  const shouldScroll = nearMessagesBottom();
  const bubble = appendBubble('assistant', data.message);
  if (data.kind === 'clarification') bubble.classList.add('clarification');
  if (data.kind === 'error') bubble.classList.add('error');
  for (const card of data.cards || []) {
    const panel = document.createElement('article');
    panel.className = 'nutrition-card';
    const kicker = document.createElement('div');
    kicker.className = 'card-kicker';
    kicker.textContent = `${hallLabel(card.dining_hall)} · ${humanDate(card.menu_date)}`;
    const title = document.createElement('h3');
    title.textContent = card.name;
    const serving = document.createElement('p');
    serving.className = 'card-serving';
    const portionKnown = card.portion_confirmed !== false;
    serving.textContent = portionKnown ? `${card.reported_quantity ?? card.quantity} ${card.unit || 'serving'} → ${card.quantity} listed serving${card.quantity === 1 ? '' : 's'} × ${card.serving_size || 'size unavailable'} · ${card.match}` :
      `Reported ${card.reported_quantity} ${card.unit} · UMD serving: ${card.serving_size || 'unavailable'} · portion needs confirmation`;
    panel.append(kicker, title, serving);
    if (!portionKnown) {
      const label = document.createElement('p'); label.className = 'result-note';
      label.textContent = 'Official values per listed serving below. Your portion has not been calculated.';
      panel.append(label, nutritionGrid(card.per_serving));
    } else panel.append(nutritionGrid(card.nutrition));
    const linkUrl = safeSource(card.source_url);
    if (linkUrl) {
      const link = document.createElement('a');
      link.className = 'source-link';
      link.href = linkUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open official UMD label ↗';
      panel.append(link);
    }
    bubble.append(panel);
  }
  if (data.total && data.cards?.length) {
    const total = document.createElement('div');
    total.className = 'total-card';
    const title = document.createElement('strong');
    title.textContent = data.nutrition_complete === false ? 'Partial nutrition total · missing UMD values' : data.cards.length > 1 ? 'Meal total' : 'Estimated total';
    total.append(title, nutritionGrid(data.total));
    bubble.append(total);
  }
  if (data.confirmed_subtotal) {
    const subtotal = document.createElement('div'); subtotal.className = 'total-card';
    const title = document.createElement('strong'); title.textContent = 'Confirmed subtotal · excludes unresolved foods and portions';
    subtotal.append(title, nutritionGrid(data.confirmed_subtotal)); bubble.append(subtotal);
  }
  if (data.unresolved_items?.length) {
    const pending = document.createElement('p'); pending.className = 'result-note';
    pending.textContent = 'Still checking: ' + data.unresolved_items.map(item => `${item.name} (${item.field})`).join(' · ');
    bubble.append(pending);
  }
  if (data.kind === 'tracker' && data.total) {
    const total = document.createElement('div'); total.className = 'total-card';
    const title = document.createElement('strong'); title.textContent = 'Today’s tracked totals';
    total.append(title, nutritionGrid(data.total)); bubble.append(total);
    const goals = document.createElement('p'); goals.className = 'result-note';
    goals.textContent = `Goals: calories ${data.tracker.goals.calories ?? 'not set'} kcal · protein ${data.tracker.goals.protein_g ?? 'not set'} g · carbs ${data.tracker.goals.carbohydrates_g ?? 'not set'} g · fat ${data.tracker.goals.fat_g ?? 'not set'} g`;
    bubble.append(goals);
  }
  if (data.kind === 'history' && data.meals?.length) {
    for (const meal of data.meals.slice(0, 8)) {
      const line = document.createElement('p'); line.className = 'result-note';
      line.textContent = `${meal.date} · ${meal.label} · ${meal.status} · ${trackerNumber(meal.delta.calories)} kcal`;
      bubble.append(line);
      for (const card of meal.cards || []) {
        const url = safeSource(card.source_url);
        if (!url) continue;
        const link = document.createElement('a'); link.className = 'source-link';
        link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = `${card.name} · UMD label ↗`; bubble.append(link);
      }
    }
  }
  if (data.kind === 'memories' && data.memories?.length) {
    for (const memory of data.memories.slice(0, 12)) {
      const line = document.createElement('p'); line.className = 'result-note';
      line.textContent = `${memory.category.replaceAll('_', ' ')} · ${memory.key}: ${memory.value}`;
      bubble.append(line);
    }
  }
  if (data.kind === 'result' && data.total) {
    const complete = MacroTracker.FIELDS.every(field => typeof data.total[field] === 'number' && Number.isFinite(data.total[field]));
    if (complete) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'inline-button add-result-button';
      button.dataset.trackerDate = tracker.getState().date;
      button.dataset.resultId = data.result_id || '';
      button.textContent = 'Add result to today';
      button.addEventListener('click', async () => {
        try {
          button.disabled = true;
          const update = await stateRequest('/api/tracker/add-result', {result_id: button.dataset.resultId,
            conversation_id: conversationId});
          applyBackendTracker(update.tracker);
          trackerMessage(update.added ? 'Mellow’s result added to today’s editable totals.' : 'This result was already added.');
        } catch (error) { button.disabled = false; trackerMessage(error.message, true); }
      });
      bubble.append(button);
    } else {
      const note = document.createElement('p');
      note.className = 'result-note';
      note.textContent = 'Some UMD values are missing, so this result cannot be added automatically. You can enter your own test values above.';
      bubble.append(note);
    }
  }
  if (data.options?.length) {
    const list = document.createElement('div');
    list.className = 'match-options';
    for (const option of data.options) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `${option.name} · ${hallLabel(option.hall)}`;
      button.addEventListener('click', () => { els.input.value = `I meant ${option.name} at ${hallLabel(option.hall)} on ${option.date}.`; els.input.focus(); });
      list.append(button);
    }
    bubble.append(list);
  }
  for (const note of data.notes || []) {
    const text = document.createElement('p');
    text.className = 'result-note';
    text.textContent = note;
    bubble.append(text);
  }
  if (data.kind === 'needs_data' || data.needs_refresh) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'inline-button';
    button.textContent = 'Browse or refresh UMD data →';
    button.addEventListener('click', () => switchView('browse'));
    bubble.append(button);
  }
  appendDebug(bubble, data.debug);
  if (data.message_id) attachReactionButton(bubble, data);
  if (shouldScroll) requestAnimationFrame(() => { els.messages.scrollTop = els.messages.scrollHeight; });
  return bubble;
}

async function checkAI() {
  try {
    const status = await request('/api/ollama/status');
    els.aiStatus.classList.toggle('offline', !status.model_available);
    els.aiTitle.textContent = status.model_available ? 'Mellow connected' : 'Mellow unavailable';
    els.aiDetail.textContent = `${status.model} · ${status.message}`;
  } catch (error) {
    els.aiStatus.classList.add('offline');
    els.aiTitle.textContent = 'Mellow status unavailable';
    els.aiDetail.textContent = error.message;
  }
}

function setChatBusy(busy) {
  chatBusy = busy;
  if (busy) mascot.startThinking();
  else if (mascot.getState().state === 'thinking') mascot.fail();
  els.loading.hidden = !busy;
  els.send.disabled = busy;
  els.messages.querySelectorAll('.reaction-button').forEach(button => { button.disabled = busy; });
}

function attachReactionButton(bubble, data) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'reaction-button';
  const active = Boolean(data.user_reaction);
  button.classList.toggle('is-selected', active);
  button.setAttribute('aria-pressed', String(active));
  button.setAttribute('aria-label', active ? 'Remove your thumbs-up reaction (does not undo actions)' :
    data.approval ? `Approve logging this meal for ${data.approval.date} with a thumbs-up` : 'React thumbs-up to this Mellow message');
  button.title = button.getAttribute('aria-label');
  button.textContent = active ? '👍 You' : '👍';
  button.disabled = chatBusy;
  button.addEventListener('click', async () => {
    if (chatBusy) return;
    setChatBusy(true);
    try {
      const update = await stateRequest('/api/reaction', {conversation_id: conversationId,
        message_id: data.message_id, active: !active});
      if (update.tracker) applyBackendTracker(update.tracker);
      await loadConversation();
      if (update.response?.kind === 'error') {
        mascot.fail();
        showToast(update.response.message, 'error');
      } else {
        if (update.response) mascot.respond();
        showToast(active ? 'Reaction removed. Logged meals are unchanged.' :
          update.response?.message || 'Reaction saved.', 'info');
      }
    } catch (error) { mascot.fail(); showToast(error.message, 'error'); }
    finally { setChatBusy(false); }
  });
  bubble.append(button);
}

function mellowReaction(bubble) {
  const badge = document.createElement('span');
  badge.className = 'reaction-badge mellow-reaction';
  badge.textContent = '👍 Mellow';
  badge.title = 'Mellow understood this message. This acknowledgment is not proof of successful execution.';
  badge.setAttribute('aria-label', badge.title);
  bubble.append(badge);
}

async function loadConversation() {
  if (!conversationId) return;
  const id = conversationId;
  const history = await request(`/api/conversation?id=${encodeURIComponent(id)}`);
  if (id !== conversationId) return;
  const atBottom = nearMessagesBottom();
  const scrollTop = els.messages.scrollTop;
  const previous = new Set([...els.messages.children].map(node => node.dataset.messageId));
  els.messages.replaceChildren();
  els.welcome.hidden = history.messages.length > 0;
  const bubbles = new Map();
  for (const entry of history.messages) {
    const data = entry.payload;
    let bubble;
    if (entry.role === 'user') {
      bubble = appendBubble('user', data.message);
      if (entry.reactions.mellow) mellowReaction(bubble);
    } else if (data.response_type === 'acknowledgment' || data.acknowledgment_only) {
      // No empty assistant bubble: keep diagnostic details attached to the understood request.
      const parent = bubbles.get(data.reply_to);
      if (parent?.classList.contains('user') && data.debug) appendDebug(parent, data.debug);
      else if (parent?.querySelector('.reaction-button[aria-pressed="true"]')) {
        const receipt = document.createElement('span');
        receipt.className = 'reaction-receipt';
        receipt.textContent = '👍 Mellow acknowledged your reaction';
        parent.append(receipt);
      }
      continue;
    } else {
      bubble = appendResult({...data, message_id: entry.id, user_reaction: entry.reactions.user});
    }
    bubble.dataset.messageId = entry.id;
    if (previous.has(entry.id)) bubble.classList.add('restored-message');
    bubbles.set(entry.id, bubble);
  }
  // Rendered history never applies old tracker snapshots to today's dashboard.
  requestAnimationFrame(() => { els.messages.scrollTop = atBottom ? els.messages.scrollHeight : scrollTop; });
}

async function sendMessage(message) {
  await historyLoading;
  if (chatBusy || !message.trim()) return;
  setChatBusy(true);
  els.welcome.hidden = true;
  appendBubble('user', message.trim());
  els.input.value = '';
  if (!conversationId) conversationId = crypto.randomUUID().replaceAll('-', '');
  try { window.localStorage.setItem('macromellow.conversation.v1', conversationId); } catch { /* Optional. */ }
  const requestId = crypto.randomUUID().replaceAll('-', '');
  try {
    const data = await request('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-MacroMellow-Request': 'chat'},
      body: JSON.stringify({message: message.trim(), conversation_id: conversationId, request_id: requestId})
    });
    if (data.tracker) applyBackendTracker(data.tracker);
    if (data.memories) { savedMemories = data.memories; renderMemoryList(); }
    await loadConversation();
    if (data.kind === 'error') {
      mascot.fail();
      showToast(data.message, 'error');
    } else {
      mascot.respond();
      if (data.acknowledgment_only) showToast(data.message, 'success');
    }
  } catch (error) {
    mascot.fail();
    const bubble = appendBubble('assistant', error.message);
    bubble.classList.add('error');
    appendDebug(bubble, error.debug);
    showToast('The request could not be verified. Check your tracker before resending.', 'error');
  } finally {
    setChatBusy(false);
    els.input.focus();
  }
}

const hallLabel = name => ({
  'Yahentamitsi (The Y)': 'The Y',
  'South Campus Dining Hall': 'South Campus',
  '251 North': '251 North'
})[name] || name || 'Unknown hall';

function humanDate(value) {
  if (!value) return 'Unknown date';
  const [year, month, day] = value.split('-').map(Number);
  return new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', year: 'numeric'}).format(new Date(year, month - 1, day));
}

function fetchedAt(value) {
  if (!value) return 'Retrieval time unknown';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Retrieval time unknown' : `Retrieved ${new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(parsed)}`;
}

async function request(url, options) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`The local server returned an unreadable response (${response.status}).`); }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status}).`);
    error.debug = data.debug;
    throw error;
  }
  return data;
}

function showStatus(message, kind = '') {
  els.refreshStatus.textContent = message;
  els.refreshStatus.className = `status ${kind}`.trim();
  els.refreshStatus.hidden = !message;
}

function setRefreshing(running) {
  els.refresh.disabled = running;
  els.refresh.classList.toggle('is-loading', running);
  els.refresh.querySelector('.button-label').textContent = running ? 'Refreshing UMD…' : 'Refresh UMD data';
}

function cell(text, className = '') {
  const td = document.createElement('td');
  if (className) td.className = className;
  td.textContent = text;
  return td;
}

function nutrition(value, unit = '') {
  return value === null || value === undefined || value === '' ? '—' : `${value}${unit}`;
}

function row(food) {
  const tr = document.createElement('tr');
  const name = document.createElement('td');
  const title = document.createElement('span');
  title.className = 'food-name';
  title.textContent = food.name || 'Unnamed food';
  name.append(title);
  const context = [food.meal, food.station].filter(Boolean).join(' · ');
  if (context) {
    const small = document.createElement('span');
    small.className = 'food-meta';
    small.textContent = context;
    name.append(small);
  }
  const fields = ['serving_size', 'calories', 'protein_g', 'carbohydrates_g', 'fat_g'];
  const missingCount = fields.filter(key => food[key] === null || food[key] === undefined || food[key] === '').length;
  if (missingCount) {
    const missing = document.createElement('span');
    missing.className = 'missing-badge';
    missing.textContent = missingCount === fields.length ? 'UMD nutrition unavailable' : 'Incomplete nutrition';
    name.append(missing);
  }
  tr.append(name);
  const hall = document.createElement('td');
  const badge = document.createElement('span');
  badge.className = 'hall-badge';
  badge.textContent = hallLabel(food.dining_hall);
  hall.append(badge);
  tr.append(hall);
  for (const [key, unit] of [['serving_size', ''], ['calories', ''], ['protein_g', ' g'], ['carbohydrates_g', ' g'], ['fat_g', ' g']]) {
    const value = food[key];
    tr.append(cell(nutrition(value, unit), value === null || value === undefined || value === '' ? 'unavailable' : 'number'));
  }
  const source = document.createElement('td');
  try {
    const url = new URL(food.source_url);
    if (url.protocol === 'https:' && url.hostname === 'nutrition.umd.edu' && url.pathname === '/label.aspx') {
      const link = document.createElement('a');
      link.className = 'source-link';
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'View UMD label ↗';
      source.append(link);
    } else source.textContent = '—';
  } catch { source.textContent = '—'; }
  tr.append(source);
  ['Food', 'Dining hall', 'Serving', 'Calories', 'Protein', 'Carbs', 'Fat', 'Source'].forEach((label, index) => {
    tr.children[index].dataset.label = label;
  });
  return tr;
}

function render() {
  const selected = datasets.get(els.date.value);
  els.rows.replaceChildren();
  if (!selected) {
    els.meta.textContent = 'No saved UMD data for this date.';
    els.count.textContent = '0 results';
    els.empty.innerHTML = '<strong>No saved menu for this date</strong>Choose a saved date, or refresh a date currently offered by UMD.';
    els.empty.hidden = false;
    els.table.hidden = true;
    els.more.hidden = true;
    els.shown.textContent = '';
    return;
  }
  const query = els.search.value.trim().toLocaleLowerCase();
  const hall = els.hall.value;
  const foods = selected.foods.filter(food => (!hall || food.dining_hall === hall) && (!query || (food.name || '').toLocaleLowerCase().includes(query)));
  const missing = selected.foods.filter(food => [food.serving_size, food.calories, food.protein_g, food.carbohydrates_g, food.fat_g].some(value => value === null || value === undefined)).length;
  els.meta.textContent = `${humanDate(selected.menu_date)} · ${fetchedAt(selected.retrieved_at)} · ${selected.foods.length.toLocaleString()} menu appearances · ${missing} with missing nutrition`;
  els.count.textContent = `${foods.length.toLocaleString()} result${foods.length === 1 ? '' : 's'}`;
  els.empty.hidden = foods.length > 0;
  els.table.hidden = foods.length === 0;
  if (!foods.length) els.empty.innerHTML = '<strong>No foods match these filters</strong>Try a different name, dining hall, or date.';
  const shown = foods.slice(0, visibleLimit);
  const fragment = document.createDocumentFragment();
  shown.forEach(food => fragment.append(row(food)));
  els.rows.append(fragment);
  els.shown.textContent = foods.length ? `Showing ${shown.length.toLocaleString()} of ${foods.length.toLocaleString()} results` : '';
  els.more.hidden = shown.length >= foods.length;
}

async function loadDatasets(keepDate = true) {
  const payload = await request('/api/datasets');
  datasets = new Map(payload.datasets.map(item => [item.menu_date, item]));
  if (!keepDate || !els.date.value) els.date.value = payload.datasets[0]?.menu_date || payload.today;
  els.loadErrors.hidden = !payload.load_errors.length;
  els.loadErrors.textContent = payload.load_errors.length ? `Could not read ${payload.load_errors.length} saved JSON file(s): ${payload.load_errors.join('; ')}` : '';
  render();
}

async function syncStatus() {
  try {
    const status = await request('/api/status');
    const running = status.state === 'running';
    setRefreshing(running);
    if (status.state !== 'idle') showStatus(status.message, status.state === 'running' || status.state === 'success' ? '' : status.state);
    if (lastRefreshState !== null && status.state !== lastRefreshState && ['success', 'warning', 'error'].includes(status.state)) {
      showToast(status.message, status.state === 'success' ? 'success' : status.state === 'warning' ? 'info' : 'error');
    }
    lastRefreshState = status.state;
    if (running) {
      if (!wasRefreshing && status.date) { els.date.value = status.date; render(); }
      wasRefreshing = true;
      if (!polling) polling = setInterval(syncStatus, 2000);
    } else {
      if (polling) { clearInterval(polling); polling = null; }
      if (wasRefreshing) { wasRefreshing = false; await loadDatasets(true); }
    }
  } catch (error) {
    showStatus(`Could not check refresh status: ${error.message}`, 'error');
    showToast(`Could not check refresh status: ${error.message}`, 'error');
    setRefreshing(false);
    if (polling) { clearInterval(polling); polling = null; }
  }
}

els.search.addEventListener('input', () => { visibleLimit = 100; render(); });
els.trackerForm.addEventListener('submit', async event => {
  event.preventDefault();
  const values = {};
  for (const field of MacroTracker.FIELDS) {
    const raw = els.trackerInputs[field].value.trim();
    if (raw !== '') values[field] = Number(raw);
  }
  try {
    const updated = await stateRequest('/api/tracker/adjust', {mode: els.trackerMode.value, values});
    els.trackerForm.reset();
    applyBackendTracker(updated);
    trackerMessage('Today’s totals updated.');
  } catch (error) { trackerMessage(error.message, true); }
});
els.goalControls.addEventListener('toggle', () => { if (els.goalControls.open) populateGoalInputs(); });
els.goalForm.addEventListener('submit', async event => {
  event.preventDefault();
  const changes = {};
  for (const field of MacroTracker.FIELDS) {
    const raw = els.goalInputs[field].value.trim();
    changes[field] = raw === '' ? null : Number(raw);
  }
  try {
    const updated = await stateRequest('/api/tracker/goals', {goals: changes});
    applyBackendTracker(updated);
    trackerMessage('Daily goals saved. They will stay when today’s totals reset.');
  } catch (error) { trackerMessage(error.message, true); }
});
els.trackerReset.addEventListener('click', async () => {
  try {
    const updated = await stateRequest('/api/tracker/reset', {});
    els.trackerForm.reset();
    applyBackendTracker(updated);
    trackerMessage('Today’s totals reset to zero.');
  } catch (error) { trackerMessage(error.message, true); }
});
els.memoryCancel.addEventListener('click', () => { memoryEditingId = null; els.memoryForm.reset(); els.memoryCancel.hidden = true; });
els.memoryForm.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const payload = {category: els.memoryCategory.value, key: els.memoryKey.value, value: els.memoryValue.value};
    const updated = await stateRequest(memoryEditingId ? '/api/memory/update' : '/api/memory/save',
      memoryEditingId ? {...payload, id: memoryEditingId} : payload);
    savedMemories = updated.memories;
    renderMemoryList();
    memoryEditingId = null; els.memoryForm.reset(); els.memoryCancel.hidden = true;
    trackerMessage('Personal memory saved. It does not log a meal.');
  } catch (error) { trackerMessage(error.message, true); }
});
window.addEventListener('storage', event => {
  if (event.key === MacroTracker.KEY || event.key === MacroTracker.GOALS_KEY)
    loadBackendTracker().catch(error => trackerMessage(error.message, true));
});
window.addEventListener('focus', () => { checkTrackerDay(); loadBackendTracker().catch(error => trackerMessage(error.message, true)); scheduleTrackerRollover(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) { checkTrackerDay(); loadBackendTracker().catch(error => trackerMessage(error.message, true)); scheduleTrackerRollover(); } });
setInterval(checkTrackerDay, 60000);
scheduleTrackerRollover();
if (!trackerPersists) trackerMessage('Browser storage is unavailable; SQLite still saves totals and goals locally.', true);
els.chatTab.addEventListener('click', () => switchView('chat'));
els.trackerTab.addEventListener('click', () => switchView('tracker'));
els.browseTab.addEventListener('click', () => switchView('browse'));
els.trackerExpand.addEventListener('click', () => setTrackerExpanded(els.trackerPanel.classList.contains('is-collapsed')));
els.newChat.addEventListener('click', () => {
  if (chatBusy) return;
  mascot.reset();
  conversationId = null;
  try { window.localStorage.removeItem('macromellow.conversation.v1'); } catch { /* Optional. */ }
  els.messages.replaceChildren();
  els.welcome.hidden = false;
  els.input.value = '';
  els.input.focus();
});
els.form.addEventListener('submit', event => { event.preventDefault(); sendMessage(els.input.value); });
els.input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); els.form.requestSubmit(); }
});
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));
els.hall.addEventListener('change', () => { visibleLimit = 100; render(); });
els.date.addEventListener('change', () => { visibleLimit = 100; render(); });
els.more.addEventListener('click', () => { visibleLimit += 100; render(); });
els.refresh.addEventListener('click', async () => {
  if (!els.date.value) { showStatus('Choose a menu date before refreshing.', 'error'); showToast('Choose a menu date before refreshing.', 'error'); return; }
  setRefreshing(true);
  showStatus('Starting UMD refresh…');
  showToast('Refreshing the selected UMD menu. This can take a few minutes.', 'info');
  try {
    await request('/api/refresh', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-MacroMellow-Request': 'refresh'},
      body: JSON.stringify({date: els.date.value})
    });
    wasRefreshing = true;
    await syncStatus();
  } catch (error) {
    setRefreshing(false);
    showStatus(error.message, 'error');
    showToast(error.message, 'error');
  }
});

historyLoading = loadConversation().catch(error => showToast(`Could not restore conversation: ${error.message}`, 'error'));

(async () => { await Promise.all([historyLoading, loadDatasets(false), checkAI(), loadBackendTracker(),
  request('/api/memories').then(data => { savedMemories = data.memories; renderMemoryList(); })]); await syncStatus(); })().catch(error => {
  renderTracker();
  els.meta.textContent = 'Saved data could not be loaded.';
  els.count.textContent = '0 results';
  els.empty.innerHTML = '<strong>Could not load UMD data</strong>Check that the local server is running and the JSON files are readable.';
  els.empty.hidden = false;
  els.table.hidden = true;
  showStatus(error.message, 'error');
});
