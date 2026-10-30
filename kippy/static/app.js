/* Vanilla JS client. The model is called only by the Python backend. */
(function (root) {
  'use strict';
  const supported = /\.(pdf|pptx|docx|txt|zip)$/i;
  function validateFile(file, limit) {
    if (!supported.test(file.name)) return 'Supported formats: PDF, PPTX, DOCX, TXT, and ZIP.';
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
  function requestPayload(courseId, conversationId, message, requestId) {
    return {course_id: courseId, conversation_id: conversationId, message: message.trim(), request_id: requestId};
  }
  async function sendTurn(courseId, conversationId, message, requestId, transport = api) {
    return transport('/api/chat', {method: 'POST', body: requestPayload(courseId, conversationId, message, requestId)});
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
  const state = {courses: [], course: null, legacy: {conversations: [], materials: []}, busy: false, pending: new Map(), importReports:new Map(), maxBytes: 25*1048576, collapsed: false, editing: false, menuId: null, deleteTarget: null, deleteStage:null, chats:new Map(), turns:new Map(), errors:new Map(), drafts:new Map(), scrolls:new Map(), activePanel:'left', chatEditing:null, deleteChatTarget:null, retries:new Map()};
  const dragDepth = new Map();
  try{state.drafts=new Map(Object.entries(JSON.parse(sessionStorage.getItem('kippy.drafts')||'{}')));}catch{/* Optional draft persistence. */}
  const sides=['left','right'];
  const view=(id,side='left')=>$(id+(side==='right'?'-right':''));
  const layout=()=>state.course?.workspace||{mode:'single',single_id:null,left_id:null,right_id:null,active_panel:'left',split_initialized:0};
  const chatId=side=>side==='right'?layout().right_id:(layout().mode==='double'?layout().left_id:layout().single_id);
  const working=()=>state.busy||state.turns.size>0;
  const uuid=()=>crypto.randomUUID().replaceAll('-','');
  function saveDraft(id,text){if(!id)return;state.drafts.set(id,text);try{sessionStorage.setItem('kippy.drafts',JSON.stringify(Object.fromEntries(state.drafts)));}catch{/* Server history is authoritative. */}}
  function setActive(side){if(layout().mode!=='double'||state.activePanel===side&&layout().single_id===chatId(side))return;state.activePanel=side;layout().active_panel=side;layout().single_id=chatId(side)||layout().single_id;try{localStorage.setItem(`kippy.active.${state.course.id}`,JSON.stringify({left:layout().left_id,right:layout().right_id,side}));}catch{}renderTargets();flushActive();}
  function renderTargets(){for(const side of sides){$('chat-'+side).classList.toggle('targeted',layout().mode!=='double'?side==='left':side===state.activePanel);$('tab-'+side).setAttribute('aria-pressed',String(side===state.activePanel));}}
  function flushActive(){if(!state.course||working())return;const courseId=state.course.id;api(`/api/courses/${courseId}/layout`,{method:'POST',body:{active_panel:state.activePanel,single_id:layout().single_id}}).catch(()=>{/* A later selection or turn persists this again; local panel preference is retained. */});}
  async function loadVisible(force=false){for(const side of sides){if(side==='right'&&layout().mode!=='double')continue;const id=chatId(side);if(id&&(force||!state.chats.has(id)))state.chats.set(id,await api(`/api/courses/${state.course.id}/conversations/${id}`));}}
  function restorePanel(){state.activePanel=layout().active_panel||'left';try{const old=JSON.parse(localStorage.getItem(`kippy.active.${state.course.id}`)||'null');if(old&&old.left===layout().left_id&&old.right===layout().right_id&&sides.includes(old.side))state.activePanel=old.side;}catch{}}

  function element(tag, cls, text) { const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n; }
  function button(text, cls, action, label) {const n=element('button',cls,text);n.type='button';if(label)n.setAttribute('aria-label',label);n.addEventListener('click',action);return n;}
  function notice(text) {$('notice-text').textContent=text;$('notice').hidden=!text;$('materials-error').textContent=text;$('materials-error').hidden=!text;}
  function remember(id) {try {if(id)localStorage.setItem('kippy.course',id);else localStorage.removeItem('kippy.course');}catch{/* Server storage remains authoritative. */}}
  function sidebar(visible) {
    const mobile=matchMedia('(max-width: 760px)').matches;
    if(mobile)document.body.classList.toggle('sidebar-visible',visible);
    else {state.collapsed=!visible;document.body.classList.toggle('sidebar-collapsed',!visible);}
    $('sidebar-shade').hidden=!(mobile&&visible);$('sidebar-open').hidden=!mobile&&!state.collapsed;
    $('sidebar-open').setAttribute('aria-expanded',String(visible));$('sidebar').inert=mobile?!visible:state.collapsed;
  }
  function resize(side) {for(const panel of side?[side]:sides){const input=view('message',panel);input.style.height='auto';input.style.height=`${Math.min(matchMedia('(max-height: 500px)').matches?85:150,input.scrollHeight)}px`;}}
  function viewport() {document.documentElement.style.setProperty('--viewport-height',`${root.visualViewport?.height||root.innerHeight}px`);if(root.visualViewport){Object.assign(document.body.style,{height:`${root.visualViewport.height}px`,top:`${root.visualViewport.offsetTop}px`,position:'fixed',width:'100%'});}resize();}
  function controls() {
    const locked=working();
    for(const id of ['add-course','add-first-course','edit-course','save-course','delete-course','upload-materials','upload-empty','recover-studies','single-mode','double-mode','new-double-chat','save-chat'])$(id).disabled=locked;
    for(const id of ['cancel-delete-course','continue-delete-course','cancel-final-delete-course','delete-course-name','confirm-delete-chat','cancel-delete-chat'])$(id).disabled=state.busy;
    $('confirm-delete-course').disabled=state.busy||state.deleteStage!=='final'||$('delete-course-name').value!==state.deleteTarget?.name;
    for(const side of sides){const id=chatId(side);for(const chip of view('empty-course',side).querySelectorAll('[data-prompt]'))chip.disabled=locked||!id;for(const name of ['send','message'])view(name,side).disabled=state.busy||!id||state.turns.has(id);for(const name of ['chat-selector','new-chat','rename-chat','delete-chat','attach','upload-empty'])view(name,side).disabled=locked||(!id&&name!=='new-chat'&&name!=='chat-selector');}
    renderCourses();
  }
  function busy(active) {state.busy=active;controls();renderMaterials();}
  function renderCourses() {
    state.menuId=null;$('courses').replaceChildren();
    if(!state.courses.length)$('courses').append(element('p','list-empty','Your courses will grow here.'));
    for(const [courseIndex,course] of state.courses.entries()) {
      const open=button('','course-entry',()=>openCourse(course.id),`Open course: ${course.name}`);open.disabled=working();open.title=course.name;
      const icon=element('span','book-icon');icon.setAttribute('aria-hidden','true');
      const title=element('span','course-entry-text');title.append(element('span','course-entry-name',course.name));if(course.code)title.append(element('span','course-entry-code',course.code));
      open.append(icon,title);if(course.id===state.course?.id){open.classList.add('active');open.setAttribute('aria-current','true');}
      const row=element('div','course-row');row.dataset.icon=['compass','flask','book'][courseIndex%3];
      const toggle=button('⋯','course-menu-toggle',()=>toggleCourseMenu(course.id),`Course actions: ${course.name}`);toggle.disabled=working();toggle.id=`course-actions-${course.id}`;toggle.setAttribute('aria-haspopup','menu');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',`course-menu-${course.id}`);
      const menu=element('div','course-menu');menu.id=`course-menu-${course.id}`;menu.hidden=true;menu.setAttribute('role','menu');menu.setAttribute('aria-label',`Actions for ${course.name}`);
      const remove=button('Delete Course','',()=>requestCourseDeletion(course));remove.setAttribute('role','menuitem');remove.disabled=working();menu.append(remove);
      toggle.addEventListener('keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();toggleCourseMenu(course.id,true);}});
      row.addEventListener('focusout',()=>queueMicrotask(()=>{if(state.menuId===course.id&&!row.contains(document.activeElement))closeCourseMenu();}));
      row.append(open,toggle,menu);$('courses').append(row);
      if(course.id===state.course?.id){const list=element('div','saved-chats');list.id='saved-chats';list.append(element('h3','','Saved chats'));const add=button('＋ New Chat','sidebar-new-chat',()=>editChat(null,layout().mode==='double'?state.activePanel:'left'));add.disabled=working();list.append(add);
        for(const chat of state.course.conversations||[]){const pick=button(chat.title,'saved-chat',()=>chooseChat(layout().mode==='double'?state.activePanel:'left',chat.id),`Open chat: ${chat.title}`);pick.disabled=working();if(chat.id===chatId('left')||layout().mode==='double'&&chat.id===chatId('right'))pick.classList.add('active');if(layout().mode==='double'&&(chat.id===chatId('left')||chat.id===chatId('right')))pick.append(element('span','chat-position',chat.id===chatId('left')?'A':'B'));const row=element('div','saved-chat-row');const menu=element('details','chat-menu');const toggle=element('summary','','⋯');toggle.setAttribute('aria-label',`Conversation actions: ${chat.title}`);const remove=button('Delete Conversation','',()=>requestChatDeletion(chat.id),`Delete conversation: ${chat.title}`);remove.disabled=working();menu.append(toggle,remove);row.append(pick,menu);list.append(row);}$('courses').append(list);}

    }
    const pending=state.legacy.conversations.length||state.legacy.materials.some(m=>!m.imported);
    $('recover-studies').hidden=!pending;$('recover-studies').disabled=working();
  }
  function renderMaterials() {
    $('materials').replaceChildren();$('upload-activity').replaceChildren();$('archive-reports').replaceChildren();
    const reports=state.importReports.get(state.course?.id)||[];$('archive-reports').hidden=!reports.length;
    for(const report of reports){const block=element('div','archive-report');block.append(element('p','',`Successfully imported ${report.summary.imported} ${report.summary.imported===1?'file':'files'} from ${report.name}. ${report.summary.failed} failed; ${report.summary.skipped} skipped.`));if(report.failed.length||report.skipped.length){const details=element('details');details.open=true;const list=element('ul');for(const file of report.failed)list.append(element('li','archive-failed',`${file.name} — Failed: ${file.error}`));for(const file of report.skipped)list.append(element('li','',`${file.name} — Skipped: ${file.reason}`));details.append(element('summary','','Import details'),list);block.append(details);}$('archive-reports').append(block);}
    const materials=state.course?.materials||[],pending=[...state.pending.values()].filter(f=>f.courseId===state.course?.id);
    $('material-count').textContent=String(materials.length);$('saved-file-count').textContent=`${materials.length} ${materials.length===1?'file':'files'}`;
    $('materials-drop-zone').classList.toggle('upload-busy',state.busy&&pending.some(f=>f.status!=='failed'));
    $('materials-drop-zone').setAttribute('aria-busy',String(pending.some(f=>f.status!=='failed')));
    $('upload-activity').hidden=!pending.length;
    for(const file of pending){const row=element('p',`upload-feedback ${file.status}`,`${file.name} — ${file.status==='processing'?(/\.zip$/i.test(file.name)?'Processing ZIP — extracting and importing files…':'Processing'):file.status==='failed'?'Failed (upload not confirmed)':`Uploading${file.percent===undefined?'':` ${file.percent}%`}`}${file.error?' · '+file.error:''}`);$('upload-activity').append(row);}
    if(!materials.length)$('materials').append(element('p','list-empty','No files uploaded yet. Drag your lecture notes, slides, or study guides above to get started.'));
    for(const file of materials) {
      const row=element('div','material-row pixel-panel');row.dataset.status=file.status;
      const top=element('div','material-top');const name=element('div','material-name');
      const type=element('span','file-type',(file.type||file.name.split('.').pop()).toUpperCase());type.setAttribute('aria-label',`${file.type?.toUpperCase()||'Document'} file`);const filename=element('span','material-filename',file.name);filename.title=file.name;name.append(type,filename);top.append(name);
      if(file.id){const remove=button('×','delete-button',async()=>{
        if(working()||!confirm(`Remove “${file.name}” from this course? Its extracted text and original file will be deleted. Saved responses remain.`))return;
        busy(true);try{await api(`/api/courses/${state.course.id}/materials/${file.id}`,{method:'DELETE'});state.course.materials=state.course.materials.filter(f=>f.id!==file.id);renderMaterials();await refreshMaterials();}catch(e){notice(e.message);}finally{busy(false);}
      },`Remove material: ${file.name}`);remove.disabled=working();top.append(remove);}
      const status={uploading:'Uploading',processing:'Processing',ready:'Ready',failed:'Failed'}[file.status]||'Unknown';
      row.append(top,element('p','material-detail',`${status}${file.error?' — '+file.error:''}`));
      if(file.warning){const d=element('details','material-detail');d.append(element('summary','','Extraction limits'),element('p','',file.warning));row.append(d);}$('materials').append(row);
    }
  }
  let materialRead=0;
  async function refreshMaterials(){if(!state.course)return;const id=state.course.id,ticket=++materialRead;const course=await api(`/api/courses/${id}`);if(state.course?.id===id&&ticket===materialRead){state.course.materials=course.materials;renderMaterials();}}
  async function openMaterials(){if(!state.course)return;$('materials-dialog').showModal();renderMaterials();try{await refreshMaterials();}catch(e){notice(`Could not refresh saved course files: ${e.message}`);}}
  function renderMessages(side='left',bottom=false) {
    const id=chatId(side),messages=state.chats.get(id)?.messages||[],feed=view('feed',side),box=view('messages',side);
    box.replaceChildren();
    for(const m of messages){const article=element('article',`message ${m.role}${m.error?' error':''}${m.role==='user'?' pixel-panel':''}`);
      if(m.role==='user')article.textContent=m.content;
      else {article.append(element('div','message-label','Kippy'));const body=element('div','message-body');body.innerHTML=renderMarkdown(m.content,root.marked,root.DOMPurify);
        for(const a of body.querySelectorAll('a')){a.target='_blank';a.rel='noopener noreferrer';}
        root.renderMathInElement?.(body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false}],throwOnError:false,trust:false,maxExpand:1000});article.append(body);
        if(m.sources?.length){const details=element('details','sources'),list=element('ul');for(const src of m.sources)list.append(element('li','',`${src.name} · ${src.location}`));details.append(element('summary','',`Context provided · ${m.sources.length} source location${m.sources.length===1?'':'s'}`),list);article.append(details);}
        const copy=button('Copy response','copy-button',async()=>{try{await navigator.clipboard.writeText(m.content);copy.textContent='Copied';setTimeout(()=>copy.textContent='Copy response',1400);}catch{notice('Copy was unavailable. Select the text and copy it manually.');}});article.append(copy);
      }box.append(article);
    }
    if(side==='left')$('empty-global').hidden=!!state.course;
    view('empty-course',side).hidden=!state.course||messages.length>0;
    view('thinking',side).hidden=!state.turns.has(id);
    view('panel-error',side).textContent=state.errors.get(id)||'';view('panel-error',side).hidden=!state.errors.get(id);
    requestAnimationFrame(()=>{if(chatId(side)===id)feed.scrollTop=bottom||!state.scrolls.has(id)?feed.scrollHeight:state.scrolls.get(id);});
  }
  function render() {
    const l=layout();if(state.course?.conversation&&!state.chats.has(state.course.conversation.id))state.chats.set(state.course.conversation.id,state.course.conversation);
    $('course-title').textContent=state.course?.name||'Your study journal';$('course-code').textContent=state.course?.code||'';$('course-code').hidden=!state.course?.code;
    $('course-controls').hidden=!state.course;$('chat-toolbar').hidden=!state.course;$('materials-course-name').textContent=state.course?.name||'';
    $('chat-deck').classList.toggle('double',l.mode==='double');$('chat-right').hidden=l.mode!=='double';$('new-double-chat').hidden=!l.split_initialized;
    $('single-mode').setAttribute('aria-pressed',String(l.mode!=='double'));$('double-mode').setAttribute('aria-pressed',String(l.mode==='double'));
    for(const side of sides){const id=chatId(side),selector=view('chat-selector',side);selector.replaceChildren();selector.append(new Option('Choose a saved chat…',''));for(const chat of state.course?.conversations||[]){const opt=new Option(chat.title,chat.id);opt.disabled=l.mode==='double'&&chat.id===chatId(side==='left'?'right':'left');selector.append(opt);}selector.value=id||'';
      view('panel-header',side).hidden=!state.course;view('composer-area',side).hidden=!state.course||!id;view('course-welcome-label',side).textContent=state.course?.code||'YOUR COURSE WORKSPACE';view('message',side).value=state.drafts.get(id)||'';renderMessages(side);
    }
    renderTargets();renderMaterials();controls();resize();
  }
  async function loadCourses() {state.courses=(await api('/api/courses')).courses;renderCourses();}
  async function loadLegacy() {state.legacy=await api('/api/legacy');renderCourses();}
  async function refreshCourse() {if(state.course){const id=state.course.id;const course=await api(`/api/courses/${id}`);if(state.course?.id===id){state.course=course;restorePanel();await loadVisible(true);}}render();}
  async function openCourse(id) {
    if(working())return;busy(true);notice('');
    try{const course=await api(`/api/courses/${id}`);state.course=course;restorePanel();remember(id);$('layout-note').hidden=true;await loadVisible(true);render();sidebar(!matchMedia('(max-width: 760px)').matches);$('message').focus();}
    catch(e){notice(e.message);}finally{busy(false);}
  }
  function editCourse(editing=false, recover=false) {
    if(working())return;state.editing=editing;$('course-form').reset();$('course-error').hidden=true;
    $('course-dialog-title').textContent=editing?'Edit course':'Add a course';$('save-course').textContent=editing?'Save changes':'Create course';$('delete-course').hidden=!editing;
    $('course-name').value=editing?state.course.name:'';$('course-code-input').value=editing?state.course.code:'';$('course-description').value=editing?state.course.description:'';
    $('legacy-options').hidden=editing||!(state.legacy.conversations.length||state.legacy.materials.length);$('legacy-options').open=recover;
    $('legacy-conversation').replaceChildren(new Option('Start a fresh conversation',''));
    for(const c of state.legacy.conversations)$('legacy-conversation').append(new Option(c.title,c.id));
    $('legacy-materials').replaceChildren();for(const m of state.legacy.materials){const label=element('label','legacy-file');const check=element('input');check.type='checkbox';check.value=m.id;label.append(check,element('span','',m.name+(m.imported?' (previously copied)':'')));$('legacy-materials').append(label);}
    $('course-dialog').showModal();$('course-name').focus();
  }
  $('course-form').addEventListener('submit',async e=>{
    e.preventDefault();if(working()||!$('course-form').reportValidity())return;
    const data={name:$('course-name').value,code:$('course-code-input').value,description:$('course-description').value};
    if(!state.editing){data.legacy_conversation_id=$('legacy-conversation').value||null;data.legacy_material_ids=[...$('legacy-materials').querySelectorAll('input:checked')].map(n=>n.value);}
    busy(true);$('course-error').hidden=true;
    try{state.course=await api(state.editing?`/api/courses/${state.course.id}`:'/api/courses',{method:'POST',body:data});remember(state.course.id);const index=state.courses.findIndex(c=>c.id===state.course.id);if(index<0)state.courses.push(state.course);else state.courses[index]=state.course;$('message').value='';$('course-dialog').close();render();sidebar(!matchMedia('(max-width: 760px)').matches);await Promise.all([loadCourses(),loadLegacy()]);}
    catch(error){if($('course-dialog').open){$('course-error').textContent=error.message;$('course-error').hidden=false;}else notice(error.message);}finally{busy(false);if(!$('course-dialog').open)$('message').focus();}
  });
  function closeCourseMenu(focus=false) {
    const id=state.menuId;state.menuId=null;
    if(id){const menu=$(`course-menu-${id}`),toggle=$(`course-actions-${id}`);if(menu)menu.hidden=true;if(toggle){toggle.setAttribute('aria-expanded','false');if(focus)toggle.focus();}}
  }
  function toggleCourseMenu(id,focus=false) {
    if(working())return;const wasOpen=state.menuId===id;closeCourseMenu();
    if(!wasOpen||focus){state.menuId=id;$(`course-menu-${id}`).hidden=false;$(`course-actions-${id}`).setAttribute('aria-expanded','true');$(`course-menu-${id}`).querySelector('button').focus();}
  }
  function requestCourseDeletion(course) {
    if(working()||!course)return;$('deletion-status').hidden=true;closeCourseMenu();state.deleteTarget={id:course.id,name:course.name};state.deleteStage='warning';$('delete-course-name').value='';$('confirm-delete-course').disabled=true;
    $('delete-course-title').textContent=`Delete ${course.name}?`;$('delete-course-description').textContent=`Are you sure you want to delete '${course.name}'?`;$('delete-course-error').hidden=true;$('delete-course-dialog').showModal();$('cancel-delete-course').focus();
  }
  function cancelCourseDeletion() {if(state.busy)return;const id=state.deleteTarget?.id;$('delete-course-dialog').close();$('final-delete-course-dialog').close();state.deleteTarget=null;state.deleteStage=null;$('delete-course-name').value='';$('confirm-delete-course').disabled=true;if($('course-dialog').open)$('delete-course').focus();else $(`course-actions-${id}`)?.focus();}
  $('delete-course').addEventListener('click',()=>requestCourseDeletion(state.course));
  $('cancel-delete-course').addEventListener('click',cancelCourseDeletion);$('cancel-final-delete-course').addEventListener('click',cancelCourseDeletion);
  $('final-delete-course-dialog').addEventListener('cancel',e=>{e.preventDefault();cancelCourseDeletion();});
  $('continue-delete-course').addEventListener('click',()=>{if(working()||state.deleteStage!=='warning'||!state.deleteTarget||!$('delete-course-dialog').open)return;$('delete-course-dialog').close();state.deleteStage='final';$('final-delete-course-description').textContent=`You are about to permanently delete '${state.deleteTarget.name}' and all of its conversations, uploaded materials, and AI context.`;$('final-delete-course-dialog').showModal();$('delete-course-name').focus();});
  $('delete-course-name').addEventListener('input',()=>{$('confirm-delete-course').disabled=state.busy||state.deleteStage!=='final'||$('delete-course-name').value!==state.deleteTarget?.name;});
  $('delete-course-dialog').addEventListener('cancel',e=>{e.preventDefault();cancelCourseDeletion();});
  $('confirm-delete-course').addEventListener('click',async()=>{
    if(working()||!state.deleteTarget||state.deleteStage!=='final'||!$('final-delete-course-dialog').open||$('delete-course-name').value!==state.deleteTarget.name)return;const c=state.deleteTarget;const wasActive=state.course?.id===c.id;
    busy(true);$('delete-course-error').hidden=true;
    try{
      const result=await api(`/api/courses/${c.id}`,{method:'DELETE',body:{confirm_course_id:c.id,confirm_course_name:$('delete-course-name').value}});
      forgetChats(result.deleted_conversation_ids||[]);state.importReports.delete(c.id);for(const [token,pending] of state.pending)if(pending.courseId===c.id)state.pending.delete(token);try{localStorage.removeItem(`kippy.active.${c.id}`);}catch{}deletionSuccess('Course deleted.');
      state.courses=state.courses.filter(x=>x.id!==c.id);state.deleteTarget=null;state.deleteStage=null;$('delete-course-name').value='';$('final-delete-course-dialog').close();
      if(wasActive){state.course=null;remember(null);$('message').value='';if($('course-dialog').open)$('course-dialog').close();}
      render();
      if(wasActive&&state.courses.length){state.course=await api(`/api/courses/${state.courses[0].id}`);restorePanel();await loadVisible(true);remember(state.course.id);render();}
      await Promise.all([loadCourses(),loadLegacy()]);
    }catch(e){if($('final-delete-course-dialog').open){$('delete-course-error').textContent=e.message;$('delete-course-error').hidden=false;}else notice(e.message);}
    finally{busy(false);if(!$('final-delete-course-dialog').open){if(state.course&&document.body.classList.contains('sidebar-visible'))$('courses').querySelector('[aria-current="true"]')?.focus();else if(state.course)$('message').focus();else $('add-course').focus();}}
  });
  async function submit(e,side='left') {
    e.preventDefault();const id=chatId(side),courseId=state.course?.id,message=view('message',side).value.trim();if(!message||!id||state.busy||state.turns.has(id))return;
    setActive(side);const retry=state.retries.get(id),requestId=retry?.message===message?retry.requestId:uuid();state.retries.set(id,{message,requestId});state.turns.set(id,requestId);state.errors.delete(id);saveDraft(id,'');view('message',side).value='';
    const chat=state.chats.get(id);if(!(retry?.message===message&&chat.messages.at(-1)?.role==='user'&&chat.messages.at(-1)?.content===message))chat.messages.push({role:'user',content:message});controls();renderMessages(side,true);resize(side);
    try{const result=await sendTurn(courseId,id,message,requestId);state.chats.set(id,await api(`/api/courses/${courseId}/conversations/${id}`));state.retries.delete(id);if(result.error)state.errors.set(id,result.error);}
    catch(error){state.errors.set(id,`${error.message} Check this chat’s saved history before resending.`);saveDraft(id,message);try{state.chats.set(id,await api(`/api/courses/${courseId}/conversations/${id}`));}catch{/* Keep the draft and request identity for a safe retry. */}}
    finally{state.turns.delete(id);if(state.course?.id===courseId){if(chatId(side)===id){view('message',side).value=state.drafts.get(id)||'';renderMessages(side,true);resize(side);}controls();flushActive();}}
  }
  async function changeLayout(data){if(working()||!state.course)return;busy(true);try{state.course.workspace=await api(`/api/courses/${state.course.id}/layout`,{method:'POST',body:data});await loadVisible(true);render();}catch(e){notice(e.message);}finally{busy(false);}}
  async function splitChat(fresh=false){if(working()||!state.course)return;busy(true);try{state.course=await api(`/api/courses/${state.course.id}/split`,{method:'POST',body:{request_id:uuid(),fresh}});state.activePanel='left';await loadVisible();$('layout-note').textContent='Your previous conversations are saved. This fresh pair shares course materials, not chat history.';$('layout-note').hidden=false;render();}catch(e){notice(e.message);}finally{busy(false);}}
  async function chooseChat(side,id){if(!id||working())return;if(layout().mode==='double'&&id===chatId(side==='left'?'right':'left')){setActive(side==='left'?'right':'left');return;}
    const data=layout().mode==='double'?{[side+'_id']:id,single_id:id,active_panel:side}:{single_id:id};state.activePanel=side;await changeLayout(data);
  }
  function editChat(id,side){if(working()||!state.course)return;state.chatEditing={id,side,requestId:uuid()};$('chat-dialog-title').textContent=id?'Rename Chat':'New Chat';$('chat-name').value=id?state.chats.get(id)?.title||'New Chat':'New Chat';$('save-chat').textContent=id?'Save Name':'Create Chat';$('chat-error').hidden=true;$('chat-dialog').showModal();$('chat-name').focus();$('chat-name').select();}
  $('chat-form').addEventListener('submit',async e=>{e.preventDefault();if(working()||!$('chat-form').reportValidity())return;const edit=state.chatEditing;busy(true);try{const path=`/api/courses/${state.course.id}/conversations`;const chat=await api(edit.id?path+'/'+edit.id:path,{method:'POST',body:{name:$('chat-name').value,request_id:edit.requestId}});state.chats.set(chat.id,chat);state.chatEditing={...edit,id:chat.id,selectAfterCreate:!edit.id||edit.selectAfterCreate};$('save-chat').textContent='Save Name';state.course=await api(`/api/courses/${state.course.id}`);
      if(!edit.id||edit.selectAfterCreate){const data=layout().mode==='double'?{[edit.side+'_id']:chat.id,single_id:chat.id,active_panel:edit.side}:{single_id:chat.id};state.course.workspace=await api(`/api/courses/${state.course.id}/layout`,{method:'POST',body:data});state.activePanel=edit.side;}
      $('chat-dialog').close();await loadVisible(true);render();
    }catch(error){$('chat-error').textContent=error.message;$('chat-error').hidden=false;}finally{busy(false);}});
  function deletionSuccess(text){$('deletion-status').textContent=text;$('deletion-status').hidden=false;}
  function forgetChats(ids){for(const id of ids){for(const cache of [state.chats,state.drafts,state.errors,state.retries,state.scrolls,state.turns])cache.delete(id);}try{sessionStorage.setItem('kippy.drafts',JSON.stringify(Object.fromEntries(state.drafts)));}catch{}}
  function requestChatDeletion(id){if(working()||!id)return;$('deletion-status').hidden=true;const chat=state.course?.conversations.find(c=>c.id===id);if(!chat)return;for(const menu of document.querySelectorAll('.chat-menu'))menu.open=false;state.deleteChatTarget=id;$('delete-chat-title').textContent=`Delete ${chat.title}?`;$('delete-chat-description').textContent=`Are you sure you want to delete '${chat.title}'? This will permanently delete its messages and conversation history. This action cannot be undone.`;$('delete-chat-error').hidden=true;$('delete-chat-dialog').showModal();$('cancel-delete-chat').focus();}
  function cancelChatDelete(){if(state.busy)return;$('delete-chat-dialog').close();state.deleteChatTarget=null;}
  $('cancel-delete-chat').addEventListener('click',cancelChatDelete);$('delete-chat-dialog').addEventListener('cancel',e=>{e.preventDefault();cancelChatDelete();});
  $('confirm-delete-chat').addEventListener('click',async()=>{if(working()||!state.deleteChatTarget||!$('delete-chat-dialog').open)return;const id=state.deleteChatTarget;busy(true);try{const result=await api(`/api/courses/${state.course.id}/conversations/${id}`,{method:'DELETE',body:{confirm_conversation_id:id}});state.course=result.course;forgetChats([id]);try{localStorage.removeItem(`kippy.active.${state.course.id}`);}catch{}deletionSuccess('Conversation deleted.');$('delete-chat-dialog').close();state.deleteChatTarget=null;restorePanel();render();await loadVisible(true);render();}catch(e){if($('delete-chat-dialog').open){$('delete-chat-error').textContent=e.message;$('delete-chat-error').hidden=false;}else notice(e.message);}finally{busy(false);}});
  function transfer(courseId,file,onProcessing,onProgress) {
    return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST',`/api/courses/${courseId}/materials?filename=${encodeURIComponent(file.name)}`);xhr.setRequestHeader('X-Kippy-Request','1');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=180000;xhr.upload.addEventListener('progress',e=>{if(e.lengthComputable)onProgress?.(Math.round(e.loaded/e.total*100));});xhr.upload.addEventListener('load',onProcessing);
      xhr.onload=()=>{try{const d=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300)throw Error(d.error||'Upload failed.');resolve(d);}catch(e){reject(e);}};xhr.onerror=()=>reject(Error('Upload connection lost. Check course Materials before retrying.'));xhr.ontimeout=()=>reject(Error('Processing timed out. Check course Materials before retrying.'));xhr.send(file);});
  }
  async function uploadFiles(files) {
    if(!state.course){notice('Create or open a course before uploading materials.');return;}if(working()){notice('Please wait for the current response or upload.');return;}
    const id=state.course.id;state.importReports.set(id,[]);busy(true);notice('');
    for(const [token,pending] of state.pending)if(pending.courseId===id&&pending.status==='failed')state.pending.delete(token);
    try{for(const file of files){const error=validateFile(file,state.maxBytes);if(error){notice(`${file.name}: ${error}`);continue;}
      const token=crypto.randomUUID();state.pending.set(token,{courseId:id,name:file.name,status:'uploading'});renderMaterials();
      try{const doc=await transfer(id,file,()=>{const pending=state.pending.get(token);if(pending){pending.status='processing';renderMaterials();}},percent=>{const pending=state.pending.get(token);if(pending){pending.percent=percent;renderMaterials();}});const materials=doc.kind==='archive'?doc.materials:[doc];if(state.course?.id===id){for(const material of materials){const old=state.course.materials.findIndex(f=>f.id===material.id);if(old<0)state.course.materials.push(material);else state.course.materials[old]=material;}}if(doc.kind==='archive'){state.importReports.get(id).push(doc);}else if(doc.status!=='ready')notice(`${doc.name}: ${doc.error}`);state.pending.delete(token);}
      catch(e){const pending=state.pending.get(token);pending.status='failed';pending.error=e.message;notice(`${file.name}: ${e.message}`);}
      finally{try{await refreshMaterials();}catch(e){notice(`Could not refresh saved course files: ${e.message}`);}renderMaterials();}
    }}catch(e){notice(e.message);}finally{busy(false);$('file-input').value='';}
  }
  async function connection(){try{const s=await api('/api/ollama/status');$('connection').textContent=s.connected&&s.model_available?'Qwen connected':'Qwen offline';$('connection').title=s.message;$('connection').classList.toggle('ready',s.connected&&s.model_available);}catch{$('connection').textContent='Connection unavailable';$('connection').classList.remove('ready');}}
  for(const side of sides){$('chat-'+side).addEventListener('pointerdown',()=>setActive(side));view('composer',side).addEventListener('submit',e=>submit(e,side));view('message',side).addEventListener('input',()=>{saveDraft(chatId(side),view('message',side).value);resize(side);});view('message',side).addEventListener('focus',()=>setActive(side));view('message',side).addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();view('composer',side).requestSubmit();}});
    view('feed',side).addEventListener('scroll',()=>{const id=chatId(side);if(id)state.scrolls.set(id,view('feed',side).scrollTop);});
    view('chat-selector',side).addEventListener('change',()=>chooseChat(side,view('chat-selector',side).value));view('new-chat',side).addEventListener('click',()=>editChat(null,side));view('rename-chat',side).addEventListener('click',()=>editChat(chatId(side),side));view('delete-chat',side).addEventListener('click',()=>requestChatDeletion(chatId(side)));
    for(const name of ['attach','upload-empty'])view(name,side).addEventListener('click',()=>{if(state.course&&!working())$('file-input').click();});
  }
  $('single-mode').addEventListener('click',()=>{if(layout().mode==='double')changeLayout({mode:'single',single_id:chatId(state.activePanel)||chatId('left')});});
  $('double-mode').addEventListener('click',()=>layout().split_initialized?changeLayout({mode:'double'}):splitChat());$('new-double-chat').addEventListener('click',()=>splitChat(true));
  for(const side of sides)$('tab-'+side).addEventListener('click',()=>setActive(side));
  $('close-chat-dialog').addEventListener('click',()=>{if(!state.busy)$('chat-dialog').close();});$('chat-dialog').addEventListener('cancel',e=>{if(state.busy)e.preventDefault();});
  for(const id of ['add-course','add-first-course'])$(id).addEventListener('click',()=>editCourse());
  $('recover-studies').addEventListener('click',()=>editCourse(false,true));$('edit-course').addEventListener('click',()=>editCourse(true));
  $('close-course-dialog').addEventListener('click',()=>{if(!state.busy)$('course-dialog').close();});$('course-dialog').addEventListener('cancel',e=>{if(state.busy)e.preventDefault();});
  $('show-materials').addEventListener('click',openMaterials);$('close-materials').addEventListener('click',()=>$('materials-dialog').close());
  for(const id of ['upload-materials'])$(id).addEventListener('click',()=>{if(state.course&&!working())$('file-input').click();});
  $('file-input').addEventListener('change',()=>uploadFiles([...$('file-input').files]));
  for(const chip of document.querySelectorAll('[data-prompt]'))chip.addEventListener('click',()=>{if(state.course&&!working()){const side=chip.closest('.chat-panel').id==='chat-right'?'right':'left';saveDraft(chatId(side),chip.dataset.prompt);view('message',side).value=chip.dataset.prompt;resize(side);view('message',side).focus();}});
  $('notice-close').addEventListener('click',()=>notice(''));$('sidebar-close').addEventListener('click',()=>sidebar(false));$('sidebar-open').addEventListener('click',()=>sidebar(true));$('sidebar-shade').addEventListener('click',()=>sidebar(false));
  document.addEventListener('click',e=>{if(!e.target.closest('.course-row'))closeCourseMenu();});
  document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(state.menuId){e.preventDefault();closeCourseMenu(true);return;}if(!$('course-dialog').open&&!$('materials-dialog').open&&!$('delete-course-dialog').open&&!$('chat-dialog').open&&!$('delete-chat-dialog').open&&!$('final-delete-course-dialog').open&&matchMedia('(max-width: 760px)').matches){sidebar(false);$('sidebar-open').focus();}});
  root.addEventListener('resize',()=>{sidebar(matchMedia('(max-width: 760px)').matches?document.body.classList.contains('sidebar-visible'):!state.collapsed);viewport();});root.visualViewport?.addEventListener('resize',viewport);root.visualViewport?.addEventListener('scroll',viewport);
  function fileDrag(e){return !!e.dataTransfer&&(Array.from(e.dataTransfer.types||[]).includes('Files')||e.dataTransfer.files?.length>0);}
  function clearDrop(){dragDepth.clear();$('drop-hint').hidden=true;$('materials-drop-zone').classList.remove('drag-over');}
  function installDrop(host,isDrawer=false){
    const highlight=active=>{if(isDrawer)$('materials-drop-zone').classList.toggle('drag-over',active);else $('drop-hint').hidden=!active;};
    host.addEventListener('dragenter',e=>{if(!fileDrag(e))return;e.preventDefault();e.stopPropagation();dragDepth.set(host,(dragDepth.get(host)||0)+1);highlight(true);});
    host.addEventListener('dragover',e=>{if(!fileDrag(e))return;e.preventDefault();e.stopPropagation();if(e.dataTransfer)e.dataTransfer.dropEffect=working()?'none':'copy';highlight(true);});
    host.addEventListener('dragleave',e=>{if(!dragDepth.has(host))return;e.stopPropagation();const depth=Math.max(0,(dragDepth.get(host)||0)-1);dragDepth.set(host,depth);if(!depth||!host.contains(e.relatedTarget)&&e.relatedTarget){highlight(false);dragDepth.delete(host);}});
    host.addEventListener('drop',e=>{if(!fileDrag(e))return;e.preventDefault();e.stopPropagation();const files=Array.from(e.dataTransfer.files||[]);clearDrop();if(files.length)uploadFiles(files);});
  }
  installDrop($('workspace'));installDrop($('materials-dialog'),true);
  $('materials-dialog').addEventListener('close',clearDrop);root.addEventListener('dragend',clearDrop);root.addEventListener('blur',clearDrop);
  // Refuse browser navigation for file drops outside either upload surface; normal text drags remain untouched.
  for(const type of ['dragover','drop'])document.addEventListener(type,e=>{if(fileDrag(e)){e.preventDefault();if(type==='drop')clearDrop();}});
  async function boot(){
    busy(true);sidebar(!matchMedia('(max-width: 760px)').matches);viewport();
    try{const s=await api('/api/status');state.maxBytes=s.max_upload_bytes;$('upload-limit').textContent=`${Math.round(state.maxBytes/1048576)} MB`;await Promise.all([loadCourses(),loadLegacy()]);let id;try{id=localStorage.getItem('kippy.course');}catch{}if(state.courses.length){const chosen=state.courses.some(c=>c.id===id)?id:state.courses[0].id;state.course=await api(`/api/courses/${chosen}`);restorePanel();await loadVisible();remember(chosen);}render();}
    catch(e){notice(`Could not load your saved courses: ${e.message}`);}finally{busy(false);}
    connection();setInterval(()=>{if(!document.hidden)connection();},30000);
  }
  boot();
})(typeof window==='undefined'?globalThis:window);
