/* Vanilla JS client. The model is called only by the Python backend. */
(function (root) {
  'use strict';
  const supported = /\.(pdf|pptx|docx|txt)$/i;
  function validateFile(file, limit) {
    if (!supported.test(file.name)) return 'Supported formats: PDF, PPTX, DOCX, and TXT.';
    if (!file.size || file.size > limit) return `Choose a nonempty file under ${Math.round(limit / 1048576)} MB.`;
    return '';
  }
  async function api(path, {method = 'GET', body, fetcher = root.fetch.bind(root)} = {}) {
    const response = await fetcher(path, {method, headers: method === 'GET' ? {} : {'Content-Type': 'application/json', 'X-Kippy-Request': '1'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    let data;
    try { data = await response.json(); } catch { throw new Error('Kippy returned an unreadable response. Please try again.'); }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }
  function requestPayload(conversationId, message, requestId) {
    return {conversation_id: conversationId, message: message.trim(), request_id: requestId};
  }
  async function sendTurn(conversationId, message, requestId, transport = api) {
    return transport('/api/chat', {method: 'POST', body: requestPayload(conversationId, message, requestId)});
  }
  function renderMarkdown(text, marked, purifier) {
    // Never permit model-generated images, raw forms, scripts, or privileged attributes.
    const math = [];
    text = text.replace(/\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^\n$]+?\$/g, value => { math.push(value); return `KIPPYMATHPLACEHOLDER${math.length-1}END`; });
    const html = purifier.sanitize(marked.parse(text, {breaks: true, gfm: true}), {USE_PROFILES: {html: true}, FORBID_TAGS: ['img','form','input','button','iframe','style'], FORBID_ATTR: ['style']});
    return html.replace(/KIPPYMATHPLACEHOLDER(\d+)END/g, (match, index) => (math[index] || match).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('\"','&quot;').replaceAll("'",'&#39;'));
  }
  const helpers = {api, validateFile, requestPayload, sendTurn, renderMarkdown};
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (!root.document) return;

  const $ = id => document.getElementById(id);
  const state = {conversation: null, conversations: [], materials: [], pending: new Map(), busy: false, uploads: 0, maxBytes: 25 * 1048576, collapsed: false};
  let switchSequence = 0;
  let dragDepth = 0;
  function notice(message) { $('notice-text').textContent = message; $('notice').hidden = !message; }
  function remember(id) { try { if (id) localStorage.setItem('kippy.conversation', id); else localStorage.removeItem('kippy.conversation'); } catch { /* Server history remains authoritative. */ } }
  function lastConversation() { try { return localStorage.getItem('kippy.conversation'); } catch { return null; } }
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
  function button(text, className, action, label) { const node = element('button', className, text); node.type = 'button'; if (label) node.setAttribute('aria-label', label); node.addEventListener('click', action); return node; }
  function sidebar(visible) {
    const mobile = matchMedia('(max-width: 760px)').matches;
    if (mobile) document.body.classList.toggle('sidebar-visible', visible);
    else { state.collapsed = !visible; document.body.classList.toggle('sidebar-collapsed', !visible); }
    $('sidebar-shade').hidden = !(mobile && visible);
    $('sidebar-open').hidden = !mobile && !state.collapsed;
    $('sidebar-open').setAttribute('aria-expanded', String(visible));
    // Prevent tabbing into an off-screen panel.
    $('sidebar').inert = mobile ? !visible : state.collapsed;
  }
  function resize() { $('message').style.height = 'auto'; $('message').style.height = `${Math.min(matchMedia('(max-height: 500px)').matches ? 85 : 150, $('message').scrollHeight)}px`; }
  function viewport() {
    if (root.visualViewport) { document.body.style.height = `${root.visualViewport.height}px`; document.body.style.top = `${root.visualViewport.offsetTop}px`; document.body.style.position = 'fixed'; document.body.style.width = '100%'; }
    resize();
  }
  function busy(active) {
    state.busy = active;
    $('thinking').hidden = !active;
    $('send').disabled = active;
    $('message').disabled = active;
    $('new-chat').disabled = active;
    renderLists();
  }
  function renderLists() {
    $('conversations').replaceChildren();
    if (!state.conversations.length) $('conversations').append(element('p','list-empty','Your conversations will appear here.'));
    for (const conversation of state.conversations) {
      const row = element('div','conversation-row' + (conversation.id === state.conversation?.id ? ' active' : ''));
      const open = button(conversation.title,'open-conversation', () => openConversation(conversation.id)); open.disabled = state.busy || state.uploads > 0; open.title = conversation.title;
      if (conversation.id === state.conversation?.id) open.setAttribute('aria-current','true');
      const remove = button('×','delete-button',async () => {
        if (!confirm('Delete this conversation and its messages? Uploaded materials will remain in the library.')) return;
        try { await api(`/api/conversations/${conversation.id}`,{method:'DELETE'}); if (state.conversation?.id === conversation.id) {state.conversation = null; remember(null); renderConversation();} await loadConversations(); } catch(error) {notice(error.message);}
      },`Delete conversation: ${conversation.title}`); remove.disabled = state.busy || state.uploads > 0;
      row.append(open,remove); $('conversations').append(row);
    }
    $('materials').replaceChildren();
    if (!state.materials.length && !state.pending.size) $('materials').append(element('p','list-empty','No materials yet. Add a lecture to get started.'));
    for (const file of [...state.pending.values(), ...state.materials]) {
      const row = element('div','material-row'); const top = element('div','material-top'); const label = element('label');
      const check = element('input'); check.type = 'checkbox'; check.checked = state.conversation?.material_ids.includes(file.id) || false; check.disabled = file.status !== 'ready' || state.busy || state.uploads > 0;
      check.setAttribute('aria-label',`Use ${file.name} in this conversation`);
      check.addEventListener('change',async () => {
        if(state.busy || state.uploads) return;
        const selected=check.checked;
        state.uploads++;$('send').disabled=true;$('new-chat').disabled=true;renderLists();
        try { const conversation = await ensureConversation(); const ids = new Set(conversation.material_ids); if(selected) ids.add(file.id); else ids.delete(file.id);
          state.conversation = await api(`/api/conversations/${conversation.id}/materials`,{method:'POST',body:{material_ids:[...ids]}});
        } catch(error) {notice(error.message);}
        finally {state.uploads--;$('send').disabled=state.busy;$('new-chat').disabled=state.busy;renderLists();renderSelected();}
      });
      row.dataset.status=file.status;row.dataset.selected=String(check.checked);
      const type=element('span','file-type',(file.type||file.name.split('.').pop()).toUpperCase());type.setAttribute('aria-hidden','true');
      label.append(check,type,element('span','',file.name)); top.append(label);
      if(file.id) {const remove=button('×','delete-button',async()=>{
        if(!confirm(`Remove “${file.name}” from the library and all conversations? Existing answers remain saved.`)) return;
        try {await api(`/api/materials/${file.id}`,{method:'DELETE'}); await loadMaterials(); if(state.conversation) state.conversation=await api(`/api/conversations/${state.conversation.id}`); renderLists();renderSelected();}catch(error){notice(error.message);}
      },`Remove material: ${file.name}`); remove.disabled=state.busy || state.uploads>0;top.append(remove);}
      const detail=element('p','material-detail'+(file.status==='failed'?' material-error':''),`${(file.type||file.name.split('.').pop()).toUpperCase()} · ${file.status}${file.error ? ' — '+file.error : ''}`);
      row.append(top,detail);
      if(file.warning) {const disclosure=element('details','material-detail'); disclosure.append(element('summary','','Extraction limits'),element('p','',file.warning));row.append(disclosure);}
      $('materials').append(row);
    }
  }
  function renderSelected() {
    $('selected-materials').replaceChildren();
    for(const id of state.conversation?.material_ids || []) {const file=state.materials.find(x=>x.id===id);if(file) {const chip=element('span','material-chip',file.name);chip.title=file.name;$('selected-materials').append(chip);}}
  }
  function renderConversation() {
    $('conversation-title').textContent=state.conversation?.title || 'New chat';
    const messages=state.conversation?.messages || [];
    $('empty').hidden=messages.length>0;
    $('messages').replaceChildren();
    for(const message of messages) {
      const article=element('article',`message ${message.role}${message.error?' error':''}`);
      if(message.role==='user') article.textContent=message.content;
      else {
        article.append(element('div','message-label','KIPPY'));
        const body=element('div','message-body');
        body.innerHTML=renderMarkdown(message.content,root.marked,root.DOMPurify);
        for(const link of body.querySelectorAll('a')) {link.target='_blank';link.rel='noopener noreferrer';}
        if(root.renderMathInElement) root.renderMathInElement(body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false}],throwOnError:false,trust:false,maxExpand:1000});
        article.append(body);
        if(message.sources?.length) {const details=element('details','sources');const list=element('ul');for(const source of message.sources)list.append(element('li','',`${source.name} · ${source.location}`));details.append(element('summary','',`Context provided · ${message.sources.length} source location${message.sources.length===1?'':'s'}`),list);article.append(details);}
        const copy=button('Copy response','copy-button',async()=>{try{await navigator.clipboard.writeText(message.content);copy.textContent='Copied';setTimeout(()=>copy.textContent='Copy response',1400);}catch{notice('Copy was unavailable. Select the response text and copy it manually.');}});article.append(copy);
      }
      $('messages').append(article);
    }
    renderSelected(); renderLists();
    requestAnimationFrame(()=>{$('feed').scrollTop=$('feed').scrollHeight;});
  }
  async function loadConversations() {state.conversations=(await api('/api/conversations')).conversations;renderLists();}
  async function loadMaterials() {state.materials=(await api('/api/materials')).materials;renderLists();renderSelected();}
  async function openConversation(id) {
    if(state.busy || state.uploads) return;
    const sequence=++switchSequence;
    try {const conversation=await api(`/api/conversations/${id}`);if(sequence!==switchSequence)return;state.conversation=conversation;remember(id);$('message').value='';resize();renderConversation();sidebar(!matchMedia('(max-width: 760px)').matches);$('message').focus();}catch(error){notice(error.message);}
  }
  async function ensureConversation() {
    if(state.conversation) return state.conversation;
    state.conversation=await api('/api/conversations',{method:'POST',body:{}});remember(state.conversation.id);await loadConversations();return state.conversation;
  }
  async function newChat() {
    if(state.busy || state.uploads) return;
    try {state.conversation=await api('/api/conversations',{method:'POST',body:{}});remember(state.conversation.id);await loadConversations();$('message').value='';resize();renderConversation();sidebar(!matchMedia('(max-width: 760px)').matches);$('message').focus();}catch(error){notice(error.message);}
  }
  async function submit(event) {
    event.preventDefault();const message=$('message').value.trim();if(!message || state.busy || state.uploads) return;
    busy(true);notice('');
    try {
      const conversation=await ensureConversation();
      conversation.messages.push({role:'user',content:message});$('message').value='';resize();renderConversation();
      const result=await sendTurn(conversation.id,message,crypto.randomUUID().replaceAll('-',''));
      state.conversation=await api(`/api/conversations/${conversation.id}`);await loadConversations();renderConversation();if(result.error) notice(result.error);
    }catch(error){notice(error.message);$('message').value=message;try{if(state.conversation)state.conversation=await api(`/api/conversations/${state.conversation.id}`);renderConversation();}catch{/* Preserve current view while disconnected. */}}
    finally{busy(false);$('message').focus();resize();}
  }
  function transfer(file, onProcessing) {
    return new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST',`/api/materials?filename=${encodeURIComponent(file.name)}`);xhr.setRequestHeader('X-Kippy-Request','1');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=180000;
      xhr.upload.addEventListener('load',onProcessing);
      xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300)throw new Error(data.error||'Upload failed.');resolve(data);}catch(error){reject(error);}};
      xhr.onerror=()=>reject(new Error('Upload connection lost. Check the library before retrying.'));
      xhr.ontimeout=()=>reject(new Error('Upload processing timed out. Check the library before retrying.'));
      xhr.send(file);
    });
  }
  async function uploadFiles(files) {
    if(state.busy || state.uploads) {notice('Please wait for the current response or upload to finish.');return;}
    state.uploads++;$('send').disabled=true;$('new-chat').disabled=true;notice('');
    const accepted=[];
    try {
      for(const file of files) {
        const error=validateFile(file,state.maxBytes);if(error){notice(`${file.name}: ${error}`);continue;}
        const token=crypto.randomUUID();state.pending.set(token,{name:file.name,status:'uploading'});renderLists();
        try {const saved=await transfer(file,()=>{state.pending.get(token).status='processing';renderLists();});if(saved.status==='ready')accepted.push(saved.id);else notice(`${saved.name}: ${saved.error}`);}
        catch(error){notice(`${file.name}: ${error.message}`);}
        finally{state.pending.delete(token);await loadMaterials();}
      }
      if(accepted.length){const conversation=await ensureConversation();const ids=[...new Set([...conversation.material_ids,...accepted])];if(ids.length<=20)state.conversation=await api(`/api/conversations/${conversation.id}/materials`,{method:'POST',body:{material_ids:ids}});else notice('Files saved. Select up to 20 materials in the library.');renderConversation();}
    }catch(error){notice(error.message);}finally{state.uploads--; $('send').disabled=state.busy;$('new-chat').disabled=state.busy;renderLists();$('file-input').value='';}
  }
  async function connection() {
    try {const status=await api('/api/ollama/status');$('connection').textContent=status.connected&&status.model_available?'Qwen connected':'Qwen offline';$('connection').title=status.message;$('connection').classList.toggle('ready',status.connected&&status.model_available);}
    catch{$('connection').textContent='Connection unavailable';$('connection').classList.remove('ready');}
  }
  document.querySelectorAll('[data-prompt]').forEach(chip=>chip.addEventListener('click',()=>{
    if(state.busy||state.uploads)return;
    $('message').value=chip.dataset.prompt;resize();$('message').focus();
  }));
  $('composer').addEventListener('submit',submit);$('message').addEventListener('input',resize);
  $('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}});
  $('new-chat').addEventListener('click',newChat);$('notice-close').addEventListener('click',()=>notice(''));
  for(const id of ['attach','upload-library','upload-empty']) $(id).addEventListener('click',()=>{if(state.busy||state.uploads){notice('Please wait for the current response or upload.');return;}$('file-input').click();});
  $('file-input').addEventListener('change',()=>uploadFiles([...$('file-input').files]));
  $('sidebar-close').addEventListener('click',()=>sidebar(false));$('sidebar-open').addEventListener('click',()=>sidebar(true));$('sidebar-shade').addEventListener('click',()=>sidebar(false));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&matchMedia('(max-width: 760px)').matches){sidebar(false);$('sidebar-open').focus();}});
  root.addEventListener('resize',()=>{sidebar(matchMedia('(max-width: 760px)').matches?document.body.classList.contains('sidebar-visible'):!state.collapsed);viewport();});
  root.visualViewport?.addEventListener('resize',viewport);root.visualViewport?.addEventListener('scroll',viewport);
  const drop=$('workspace');drop.addEventListener('dragenter',event=>{if(event.dataTransfer.types.includes('Files')){event.preventDefault();dragDepth++;$('drop-hint').hidden=false;}});
  drop.addEventListener('dragover',event=>{if(event.dataTransfer.types.includes('Files'))event.preventDefault();});
  drop.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('drop-hint').hidden=true;}});
  drop.addEventListener('drop',event=>{event.preventDefault();dragDepth=0;$('drop-hint').hidden=true;uploadFiles([...event.dataTransfer.files]);});
  async function boot(){
    sidebar(!matchMedia('(max-width: 760px)').matches);viewport();
    try{const status=await api('/api/status');state.maxBytes=status.max_upload_bytes;$('upload-limit').textContent=`${Math.round(state.maxBytes/1048576)} MB`;await Promise.all([loadConversations(),loadMaterials()]);const id=lastConversation();if(id&&state.conversations.some(c=>c.id===id))await openConversation(id);else renderConversation();}catch(error){notice(`Could not load your saved workspace: ${error.message}`);}
    connection();setInterval(()=>{if(!document.hidden)connection();},30000);$('message').focus();
  }
  boot();
})(typeof window==='undefined'?globalThis:window);
