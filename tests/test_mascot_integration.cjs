const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// A small DOM surface for exercising the real app event handlers under Node.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.listeners = new Map();
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.scrollTop = 0;
    this.scrollHeight = 200;
    this.clientHeight = 200;
    this.hidden = false;
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: name => { if (!this.classList.contains(name)) this.className += ` ${name}`; },
      remove: name => { this.className = this.className.split(/\s+/).filter(value => value !== name).join(' '); },
      toggle: (name, active) => {
        const enabled = active === undefined ? !this.classList.contains(name) : active;
        this.classList[enabled ? 'add' : 'remove'](name);
        return enabled;
      }
    };
  }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  prepend(node) { node.parent = this; this.children.unshift(node); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'src') this.src = String(value); }
  getAttribute(name) { return this.attributes[name] ?? (name === 'src' ? this.src : null); }
  toggleAttribute(name, on) { if (on) this.setAttribute(name, ''); else delete this.attributes[name]; }
  addEventListener(name, callback) { this.listeners.set(name, [...(this.listeners.get(name) || []), callback]); }
  async emit(name) { await Promise.all((this.listeners.get(name) || []).map(callback => callback({preventDefault() {}}))); }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(node => [node, ...node.querySelectorAll('*')]);
    return descendants.filter(node => matches(node, selector));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  get firstChild() { return this.children[0]; }
  get firstElementChild() { return this.children[0]; }
  get isConnected() { return Boolean(this.parent); }
  focus() {}
  scrollIntoView() {}
  reset() {}
}

function matches(node, selector) {
  if (selector === '*') return true;
  if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
  if (selector.startsWith('[data-')) {
    const key = selector.slice(6, -1).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    return Object.hasOwn(node.dataset, key);
  }
  return node.tagName === selector;
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

const settle = () => new Promise(resolve => setImmediate(resolve));

async function loadApp() {
  let instant = new Date('2026-10-21T16:00:00Z').getTime();
  const fields = {calories: 0, protein_g: 0, carbohydrates_g: 0, fat_g: 0};
  let serverTracker = {date: '2026-10-21', totals: {...fields}, goals: {
    calories: 100, protein_g: null, carbohydrates_g: null, fat_g: null
  }, entries: [], addedResultIds: []};
  let history = [];
  const ids = new Map();
  const nodes = [];
  const get = id => {
    if (!ids.has(id)) { const element = new Element(); ids.set(id, element); nodes.push(element); }
    return ids.get(id);
  };
  for (const id of ['progress-calories', 'progress-protein', 'progress-carbs', 'progress-fat']) get(id).append(new Element('i'));
  const label = new Element('span'); label.className = 'button-label'; get('refresh').append(label);
  get('tracker-expand').append(new Element('span'));
  const slots = [new Element(), new Element()];
  for (const slot of slots) { slot.dataset.mellowMascot = ''; slot.append(new Element('img')); nodes.push(slot); }
  const profile = new Element('img'); profile.dataset.mellowProfile = ''; nodes.push(profile);
  const selectorNodes = new Map();
  const document = {
    getElementById: get,
    createElement: tag => new Element(tag),
    createDocumentFragment: () => new Element('fragment'),
    addEventListener() {},
    querySelector(selector) {
      if (!selectorNodes.has(selector)) { const node = new Element(); nodes.push(node); selectorNodes.set(selector, node); }
      return selectorNodes.get(selector);
    },
    querySelectorAll: selector => nodes.flatMap(node => [node, ...node.querySelectorAll('*')]).filter(node => matches(node, selector))
  };
  const storage = new Map([['macromellow.backend-migrated.v1', '1']]);
  const timers = new Map();
  const intervals = new Map();
  let aiStatus = {model_available: true, model: 'Qwen', message: 'Connected'};
  let timerId = 0;
  let uuid = 0;
  const pendingChat = [];
  const requests = [];
  class AppDate extends Date {
    constructor(...args) { super(...(args.length ? args : [instant])); }
    static now() { return instant; }
  }
  const context = vm.createContext({
    document, console, URL, Intl, Date: AppDate,
    localStorage: {getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    crypto: {randomUUID: () => `test-${++uuid}`},
    matchMedia: () => ({matches: false}), addEventListener() {},
    requestAnimationFrame: callback => callback(),
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
    clearTimeout: id => timers.delete(id),
    setInterval(callback, delay) { const id = ++timerId; intervals.set(id, {callback, delay}); return id; }, clearInterval() {},
    async fetch(url, options) {
      requests.push({url, options});
      let data;
      if (url === '/api/chat') data = await pendingChat.shift().promise;
      else if (url.startsWith('/api/conversation?')) data = {messages: history};
      else if (url === '/api/datasets') data = {datasets: [], today: serverTracker.date, load_errors: []};
      else if (url === '/api/ollama/status') {
        if (aiStatus instanceof Error) throw aiStatus;
        data = aiStatus.promise ? await aiStatus.promise : aiStatus;
      }
      else if (url === '/api/tracker') data = serverTracker;
      else if (url === '/api/memories') data = {memories: []};
      else if (url === '/api/status') data = {state: 'idle'};
      else throw new Error(`Unexpected request: ${url}`);
      return {ok: true, status: 200, json: async () => data};
    }
  });
  context.window = context;
  for (const name of ['tracker.js', 'mascot.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../macromellow/static', name), 'utf8'), context, {filename: name});
  }
  await settle();
  return {
    get, slots, profile, requests,
    setAIStatus: status => { aiStatus = status; },
    setHidden: hidden => { document.hidden = hidden; },
    async pollAI() {
      for (const timer of intervals.values()) if (timer.delay === 30000) timer.callback();
      await settle();
    },
    run: source => vm.runInContext(source, context),
    state: () => vm.runInContext('mascot.getState()', context),
    enqueueChat() { const pending = deferred(); pendingChat.push(pending); return pending; },
    setHistory: messages => { history = messages; },
    applyTracker(changes) {
      serverTracker = {...serverTracker, ...changes};
      context.updatedTracker = serverTracker;
      vm.runInContext('applyBackendTracker(updatedTracker)', context);
    },
    setTime: iso => { instant = new Date(iso).getTime(); },
    setServerTracker: snapshot => { serverTracker = {...serverTracker, ...snapshot}; },
    finishTalking() {
      for (const [id, timer] of [...timers]) {
        if (timer.delay === 1600) { timers.delete(id); timer.callback(); }
      }
    }
  };
}

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
