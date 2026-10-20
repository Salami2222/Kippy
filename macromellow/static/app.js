const els = {
  chatTab: document.getElementById('chat-tab'),
  browseTab: document.getElementById('browse-tab'),
  chatView: document.getElementById('chat-view'),
  browseView: document.getElementById('browse-view'),
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
let conversationId = null;
let chatBusy = false;

function switchView(view) {
  const browsing = view === 'browse';
  els.chatView.hidden = browsing;
  els.browseView.hidden = !browsing;
  els.chatTab.classList.toggle('active', !browsing);
  els.browseTab.classList.toggle('active', browsing);
  els.chatTab.toggleAttribute('aria-current', !browsing);
  els.browseTab.toggleAttribute('aria-current', browsing);
  if (browsing) els.search.focus(); else els.input.focus();
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
  const bubble = document.createElement('div');
  bubble.className = `message ${role}`;
  const label = document.createElement('span');
  label.className = 'message-label';
  label.textContent = role === 'user' ? 'You' : 'MacroMellow';
  const body = document.createElement('p');
  body.textContent = text;
  bubble.append(label, body);
  els.messages.append(bubble);
  els.messages.scrollTop = els.messages.scrollHeight;
  return bubble;
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

function appendResult(data) {
  const bubble = appendBubble('assistant', data.message);
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
    serving.textContent = `${card.quantity} listed serving${card.quantity === 1 ? '' : 's'} × ${card.serving_size || 'size unavailable'} · ${card.match}`;
    panel.append(kicker, title, serving, nutritionGrid(card.nutrition));
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
    title.textContent = data.cards.length > 1 ? 'Meal total' : 'Estimated total';
    total.append(title, nutritionGrid(data.total));
    bubble.append(total);
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
  els.messages.scrollTop = els.messages.scrollHeight;
}

async function checkAI() {
  try {
    const status = await request('/api/ollama/status');
    els.aiStatus.classList.toggle('offline', !status.model_available);
    els.aiTitle.textContent = status.model_available ? 'AI connected' : 'AI unavailable';
    els.aiDetail.textContent = `${status.model} · ${status.message}`;
  } catch (error) {
    els.aiStatus.classList.add('offline');
    els.aiTitle.textContent = 'AI status unavailable';
    els.aiDetail.textContent = error.message;
  }
}

async function sendMessage(message) {
  if (chatBusy || !message.trim()) return;
  chatBusy = true;
  els.welcome.hidden = true;
  appendBubble('user', message.trim());
  els.input.value = '';
  els.loading.hidden = false;
  els.send.disabled = true;
  try {
    const data = await request('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-MacroMellow-Request': 'chat'},
      body: JSON.stringify({message: message.trim(), conversation_id: conversationId})
    });
    conversationId = data.conversation_id;
    appendResult(data);
  } catch (error) {
    appendBubble('assistant', error.message);
    await checkAI();
  } finally {
    chatBusy = false;
    els.loading.hidden = true;
    els.send.disabled = false;
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
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
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
    setRefreshing(false);
    if (polling) { clearInterval(polling); polling = null; }
  }
}

els.search.addEventListener('input', () => { visibleLimit = 100; render(); });
els.chatTab.addEventListener('click', () => switchView('chat'));
els.browseTab.addEventListener('click', () => switchView('browse'));
els.newChat.addEventListener('click', () => {
  if (chatBusy) return;
  conversationId = null;
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
  if (!els.date.value) { showStatus('Choose a menu date before refreshing.', 'error'); return; }
  setRefreshing(true);
  showStatus('Starting UMD refresh…');
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
  }
});

(async () => { await Promise.all([loadDatasets(false), checkAI()]); await syncStatus(); })().catch(error => {
  els.meta.textContent = 'Saved data could not be loaded.';
  els.count.textContent = '0 results';
  els.empty.innerHTML = '<strong>Could not load UMD data</strong>Check that the local server is running and the JSON files are readable.';
  els.empty.hidden = false;
  els.table.hidden = true;
  showStatus(error.message, 'error');
});
