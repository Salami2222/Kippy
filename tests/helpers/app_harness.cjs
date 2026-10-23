const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// A small DOM surface for exercising the real app event handlers under Node.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.style = {setProperty(name, value) { this[name] = value; }};
    this.open = false;
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
  async emit(name, details = {}) {
    const event = {preventDefault() {}, target: this, detail: 1, ...details};
    await Promise.all((this.listeners.get(name) || []).map(callback => callback(event)));
  }
  get parentElement() { return this.parent || null; }
  closest(selector) {
    return selector.split(',').some(part => matches(this, part.trim())) ? this : this.parent?.closest(selector) || null;
  }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(node => [node, ...node.querySelectorAll('*')]);
    return descendants.filter(node => matches(node, selector));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  get firstChild() { return this.children[0]; }
  get firstElementChild() { return this.children[0]; }
  get isConnected() { return Boolean(this.parent); }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.emit('close'); }
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

async function loadApp({compact = false, viewport = null} = {}) {
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
  get('browse-view').hidden = true;
  get('tracker-expand').append(new Element('span'));
  const slots = [get('mellow-mascot')];
  slots[0].dataset.mellowMascot = '';
  slots[0].append(new Element('img'));
  const selectorNodes = new Map();
  const documentListeners = new Map();
  const document = {
    documentElement: new Element(),
    getElementById: get,
    createElement: tag => new Element(tag),
    createElementNS: (namespace, tag) => new Element(tag),
    createDocumentFragment: () => new Element('fragment'),
    addEventListener(name, callback) { documentListeners.set(name, callback); },
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
  const imageLoads = [];
  const clipboard = []; const dialogs = [];
  let clipboardError = null, confirmed = false;
  let stateHandler = null;
  const requests = [];
  class AppDate extends Date {
    constructor(...args) { super(...(args.length ? args : [instant])); }
    static now() { return instant; }
  }
  const context = vm.createContext({
    document, console, URL, Intl, Date: AppDate,
    Image: class { constructor() { imageLoads.push(this); } },
    navigator: {clipboard: {async writeText(text) { if (clipboardError) throw clipboardError; clipboard.push(text); }}},
    confirm(message) { dialogs.push(message); return confirmed; },
    getSelection: () => ({toString: () => ''}),
    localStorage: {getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    crypto: {randomUUID: () => `test-${++uuid}`},
    matchMedia: () => ({matches: compact}), visualViewport: viewport, addEventListener() {},
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
      else if (url === '/api/reaction' || url === '/api/message-feedback') {
        const payload = JSON.parse(options.body);
        if (stateHandler) data = await stateHandler(url, payload);
        else {
          const entry = history.find(entry => entry.id === payload.message_id);
          entry.reactions[url === '/api/reaction' ? 'user' : 'user_feedback'] = payload.active;
          data = {active: payload.active};
        }
      }
      else throw new Error(`Unexpected request: ${url}`);
      return {ok: true, status: 200, json: async () => data};
    }
  });
  context.window = context;
  for (const name of ['tracker.js', 'mascot.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../macromellow/static', name), 'utf8'), context, {filename: name});
  }
  await settle();
  return {
    get, slots, requests, imageLoads, clipboard, dialogs, document,
    setConfirmed: value => { confirmed = value; },
    setClipboardError: error => { clipboardError = error; },
    setStateHandler: handler => { stateHandler = handler; },
    emitDocument: (name, event) => documentListeners.get(name)?.(event),
    async loadHistory(entries) {
      history = entries;
      await vm.runInContext("conversationId='c'.repeat(32); loadConversation()", context);
    },
    finishCelebration() { for (const [id,timer] of [...timers]) if(timer.delay === 1685) {timers.delete(id);timer.callback();} },
    finishCopy() { for (const [id,timer] of [...timers]) if(timer.delay === 1400) {timers.delete(id);timer.callback();} },
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


module.exports = {loadApp, deferred, settle};
