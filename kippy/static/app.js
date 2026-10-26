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
  function requestPayload(courseId, message, requestId) {
    return {course_id: courseId, message: message.trim(), request_id: requestId};
  }
  async function sendTurn(courseId, message, requestId, transport = api) {
    return transport('/api/chat', {method: 'POST', body: requestPayload(courseId, message, requestId)});
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
  const state = {courses: [], course: null, legacy: {conversations: [], materials: []}, busy: false, pending: new Map(), maxBytes: 25*1048576, collapsed: false, editing: false, menuId: null, deleteTarget: null};
  let dragDepth = 0;
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
  function resize() {$('message').style.height='auto';$('message').style.height=`${Math.min(matchMedia('(max-height: 500px)').matches?85:150,$('message').scrollHeight)}px`;}
  function viewport() {document.documentElement.style.setProperty('--viewport-height',`${root.visualViewport?.height||root.innerHeight}px`);if(root.visualViewport){Object.assign(document.body.style,{height:`${root.visualViewport.height}px`,top:`${root.visualViewport.offsetTop}px`,position:'fixed',width:'100%'});}resize();}
  function controls() {
    for(const id of ['add-course','add-first-course','edit-course','save-course','delete-course','upload-materials','upload-empty','attach','send','message','confirm-delete-course','cancel-delete-course'])$(id).disabled=state.busy;
    renderCourses();
  }
  function busy(active, thinking=false) {state.busy=active;$('thinking').hidden=!(active&&thinking);controls();renderMaterials();}
  function renderCourses() {
    state.menuId=null;$('courses').replaceChildren();
    if(!state.courses.length)$('courses').append(element('p','list-empty','Your courses will grow here.'));
    for(const course of state.courses) {
      const open=button('','course-entry',()=>openCourse(course.id),`Open course: ${course.name}`);open.disabled=state.busy;open.title=course.name;
      const icon=element('span','book-icon');icon.setAttribute('aria-hidden','true');
      const title=element('span','course-entry-text');title.append(element('span','course-entry-name',course.name));if(course.code)title.append(element('span','course-entry-code',course.code));
      open.append(icon,title);if(course.id===state.course?.id){open.classList.add('active');open.setAttribute('aria-current','true');}
      const row=element('div','course-row');
      const toggle=button('⋯','course-menu-toggle',()=>toggleCourseMenu(course.id),`Course actions: ${course.name}`);toggle.disabled=state.busy;toggle.id=`course-actions-${course.id}`;toggle.setAttribute('aria-haspopup','menu');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',`course-menu-${course.id}`);
      const menu=element('div','course-menu');menu.id=`course-menu-${course.id}`;menu.hidden=true;menu.setAttribute('role','menu');menu.setAttribute('aria-label',`Actions for ${course.name}`);
      const remove=button('Delete Course','',()=>requestCourseDeletion(course));remove.setAttribute('role','menuitem');remove.disabled=state.busy;menu.append(remove);
      toggle.addEventListener('keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();toggleCourseMenu(course.id,true);}});
      row.addEventListener('focusout',()=>queueMicrotask(()=>{if(state.menuId===course.id&&!row.contains(document.activeElement))closeCourseMenu();}));
      row.append(open,toggle,menu);$('courses').append(row);
    }
    const pending=state.legacy.conversations.length||state.legacy.materials.some(m=>!m.imported);
    $('recover-studies').hidden=!pending;$('recover-studies').disabled=state.busy;
  }
  function renderMaterials() {
    $('materials').replaceChildren();
    const materials=[...state.pending.values(),...(state.course?.materials||[])];
    $('material-count').textContent=String(state.course?.materials.length||0);
    if(!materials.length)$('materials').append(element('p','list-empty','No materials yet. Upload your first lecture or set of notes.'));
    for(const file of materials) {
      const row=element('div','material-row pixel-panel');row.dataset.status=file.status;
      const top=element('div','material-top');const name=element('div','material-name');
      const type=element('span','file-type',(file.type||file.name.split('.').pop()).toUpperCase());type.setAttribute('aria-hidden','true');name.append(type,element('span','',file.name));top.append(name);
      if(file.id){const remove=button('×','delete-button',async()=>{
        if(state.busy||!confirm(`Remove “${file.name}” from this course? Its extracted text and original file will be deleted. Saved responses remain.`))return;
        busy(true);try{await api(`/api/courses/${state.course.id}/materials/${file.id}`,{method:'DELETE'});await refreshCourse();}catch(e){notice(e.message);}finally{busy(false);}
      },`Remove material: ${file.name}`);remove.disabled=state.busy;top.append(remove);}
      row.append(top,element('p','material-detail',`${file.status}${file.error?' — '+file.error:''}`));
      if(file.warning){const d=element('details','material-detail');d.append(element('summary','','Extraction limits'),element('p','',file.warning));row.append(d);}$('materials').append(row);
    }
  }
  function renderMessages() {
    const messages=state.course?.conversation.messages||[];$('messages').replaceChildren();
    for(const m of messages){
      const article=element('article',`message ${m.role}${m.error?' error':''}${m.role==='user'?' pixel-panel':''}`);
      if(m.role==='user')article.textContent=m.content;
      else {
        article.append(element('div','message-label','Kippy'));const body=element('div','message-body');body.innerHTML=renderMarkdown(m.content,root.marked,root.DOMPurify);
        for(const a of body.querySelectorAll('a')){a.target='_blank';a.rel='noopener noreferrer';}
        root.renderMathInElement?.(body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false}],throwOnError:false,trust:false,maxExpand:1000});article.append(body);
        if(m.sources?.length){const details=element('details','sources');const list=element('ul');for(const s of m.sources)list.append(element('li','',`${s.name} · ${s.location}`));details.append(element('summary','',`Context provided · ${m.sources.length} source location${m.sources.length===1?'':'s'}`),list);article.append(details);}
        const copy=button('Copy response','copy-button',async()=>{try{await navigator.clipboard.writeText(m.content);copy.textContent='Copied';setTimeout(()=>copy.textContent='Copy response',1400);}catch{notice('Copy was unavailable. Select the text and copy it manually.');}});article.append(copy);
      }
      $('messages').append(article);
    }
    $('empty-global').hidden=!!state.course;$('empty-course').hidden=!state.course||messages.length>0;
    requestAnimationFrame(()=>{$('feed').scrollTop=$('feed').scrollHeight;});
  }
  function render() {
    $('course-title').textContent=state.course?.name||'Your study journal';$('course-code').textContent=state.course?.code||'';$('course-code').hidden=!state.course?.code;
    $('course-controls').hidden=!state.course;$('composer-area').hidden=!state.course;
    $('materials-course-name').textContent=state.course?.name||'';
    $('course-welcome-label').textContent=state.course?.code||'YOUR COURSE WORKSPACE';
    renderMessages();renderMaterials();controls();resize();
  }
  async function loadCourses() {state.courses=(await api('/api/courses')).courses;renderCourses();}
  async function loadLegacy() {state.legacy=await api('/api/legacy');renderCourses();}
  async function refreshCourse() {if(state.course){const id=state.course.id;const course=await api(`/api/courses/${id}`);if(state.course?.id===id)state.course=course;}render();}
  async function openCourse(id) {
    if(state.busy)return;busy(true);notice('');
    try{const course=await api(`/api/courses/${id}`);state.course=course;remember(id);$('message').value='';render();sidebar(!matchMedia('(max-width: 760px)').matches);$('message').focus();}
    catch(e){notice(e.message);}finally{busy(false);}
  }
  function editCourse(editing=false, recover=false) {
    if(state.busy)return;state.editing=editing;$('course-form').reset();$('course-error').hidden=true;
    $('course-dialog-title').textContent=editing?'Edit course':'Add a course';$('save-course').textContent=editing?'Save changes':'Create course';$('delete-course').hidden=!editing;
    $('course-name').value=editing?state.course.name:'';$('course-code-input').value=editing?state.course.code:'';$('course-description').value=editing?state.course.description:'';
    $('legacy-options').hidden=editing||!(state.legacy.conversations.length||state.legacy.materials.length);$('legacy-options').open=recover;
    $('legacy-conversation').replaceChildren(new Option('Start a fresh conversation',''));
    for(const c of state.legacy.conversations)$('legacy-conversation').append(new Option(c.title,c.id));
    $('legacy-materials').replaceChildren();for(const m of state.legacy.materials){const label=element('label','legacy-file');const check=element('input');check.type='checkbox';check.value=m.id;label.append(check,element('span','',m.name+(m.imported?' (previously copied)':'')));$('legacy-materials').append(label);}
    $('course-dialog').showModal();$('course-name').focus();
  }
  $('course-form').addEventListener('submit',async e=>{
    e.preventDefault();if(state.busy||!$('course-form').reportValidity())return;
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
    if(state.busy)return;const wasOpen=state.menuId===id;closeCourseMenu();
    if(!wasOpen||focus){state.menuId=id;$(`course-menu-${id}`).hidden=false;$(`course-actions-${id}`).setAttribute('aria-expanded','true');$(`course-menu-${id}`).querySelector('button').focus();}
  }
  function requestCourseDeletion(course) {
    if(state.busy||!course)return;closeCourseMenu();state.deleteTarget={id:course.id,name:course.name};
    $('delete-course-title').textContent=`Delete ${course.name}?`;$('delete-course-error').hidden=true;$('delete-course-dialog').showModal();$('cancel-delete-course').focus();
  }
  function cancelCourseDeletion() {if(state.busy)return;const id=state.deleteTarget?.id;$('delete-course-dialog').close();state.deleteTarget=null;if($('course-dialog').open)$('delete-course').focus();else $(`course-actions-${id}`)?.focus();}
  $('delete-course').addEventListener('click',()=>requestCourseDeletion(state.course));
  $('cancel-delete-course').addEventListener('click',cancelCourseDeletion);
  $('delete-course-dialog').addEventListener('cancel',e=>{e.preventDefault();cancelCourseDeletion();});
  $('confirm-delete-course').addEventListener('click',async()=>{
    if(state.busy||!state.deleteTarget)return;const c=state.deleteTarget;const wasActive=state.course?.id===c.id;
    busy(true);$('delete-course-error').hidden=true;
    try{
      await api(`/api/courses/${c.id}`,{method:'DELETE',body:{confirm_course_id:c.id}});
      state.courses=state.courses.filter(x=>x.id!==c.id);state.deleteTarget=null;$('delete-course-dialog').close();
      if(wasActive){state.course=null;remember(null);$('message').value='';if($('course-dialog').open)$('course-dialog').close();}
      render();
      if(wasActive&&state.courses.length){state.course=await api(`/api/courses/${state.courses[0].id}`);remember(state.course.id);render();}
      await Promise.all([loadCourses(),loadLegacy()]);
    }catch(e){if($('delete-course-dialog').open){$('delete-course-error').textContent=e.message;$('delete-course-error').hidden=false;}else notice(e.message);}
    finally{busy(false);if(!$('delete-course-dialog').open){if(state.course&&document.body.classList.contains('sidebar-visible'))$('courses').querySelector('[aria-current="true"]')?.focus();else if(state.course)$('message').focus();else $('add-course').focus();}}
  });
  async function submit(e) {
    e.preventDefault();const message=$('message').value.trim();if(!message||!state.course||state.busy)return;
    const id=state.course.id;busy(true,true);notice('');
    try{state.course.conversation.messages.push({role:'user',content:message});$('message').value='';renderMessages();resize();const result=await sendTurn(id,message,crypto.randomUUID().replaceAll('-',''));await refreshCourse();if(result.error)notice(result.error);}
    catch(error){notice(error.message);$('message').value=message;try{await refreshCourse();}catch{/* Preserve draft while disconnected. */}}
    finally{busy(false);$('message').focus();resize();}
  }
  function transfer(courseId,file,onProcessing) {
    return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST',`/api/courses/${courseId}/materials?filename=${encodeURIComponent(file.name)}`);xhr.setRequestHeader('X-Kippy-Request','1');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=180000;xhr.upload.addEventListener('load',onProcessing);
      xhr.onload=()=>{try{const d=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300)throw Error(d.error||'Upload failed.');resolve(d);}catch(e){reject(e);}};xhr.onerror=()=>reject(Error('Upload connection lost. Check course Materials before retrying.'));xhr.ontimeout=()=>reject(Error('Processing timed out. Check course Materials before retrying.'));xhr.send(file);});
  }
  async function uploadFiles(files) {
    if(!state.course){notice('Create or open a course before uploading materials.');return;}if(state.busy){notice('Please wait for the current response or upload.');return;}
    const id=state.course.id;busy(true);notice('');
    try{for(const file of files){const error=validateFile(file,state.maxBytes);if(error){notice(`${file.name}: ${error}`);continue;}
      const token=crypto.randomUUID();state.pending.set(token,{name:file.name,status:'uploading'});renderMaterials();
      try{const doc=await transfer(id,file,()=>{const pending=state.pending.get(token);if(pending){pending.status='processing';renderMaterials();}});if(doc.status!=='ready')notice(`${doc.name}: ${doc.error}`);}
      catch(e){notice(`${file.name}: ${e.message}`);}finally{state.pending.delete(token);await refreshCourse();}
    }}catch(e){notice(e.message);}finally{busy(false);$('file-input').value='';}
  }
  async function connection(){try{const s=await api('/api/ollama/status');$('connection').textContent=s.connected&&s.model_available?'Qwen connected':'Qwen offline';$('connection').title=s.message;$('connection').classList.toggle('ready',s.connected&&s.model_available);}catch{$('connection').textContent='Connection unavailable';$('connection').classList.remove('ready');}}
  $('composer').addEventListener('submit',submit);$('message').addEventListener('input',resize);$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}});
  for(const id of ['add-course','add-first-course'])$(id).addEventListener('click',()=>editCourse());
  $('recover-studies').addEventListener('click',()=>editCourse(false,true));$('edit-course').addEventListener('click',()=>editCourse(true));
  $('close-course-dialog').addEventListener('click',()=>{if(!state.busy)$('course-dialog').close();});$('course-dialog').addEventListener('cancel',e=>{if(state.busy)e.preventDefault();});
  $('show-materials').addEventListener('click',()=>{if(state.course)$('materials-dialog').showModal();});$('close-materials').addEventListener('click',()=>$('materials-dialog').close());
  for(const id of ['attach','upload-materials','upload-empty'])$(id).addEventListener('click',()=>{if(state.course&&!state.busy)$('file-input').click();});
  $('file-input').addEventListener('change',()=>uploadFiles([...$('file-input').files]));
  for(const chip of document.querySelectorAll('[data-prompt]'))chip.addEventListener('click',()=>{if(state.course&&!state.busy){$('message').value=chip.dataset.prompt;resize();$('message').focus();}});
  $('notice-close').addEventListener('click',()=>notice(''));$('sidebar-close').addEventListener('click',()=>sidebar(false));$('sidebar-open').addEventListener('click',()=>sidebar(true));$('sidebar-shade').addEventListener('click',()=>sidebar(false));
  document.addEventListener('click',e=>{if(!e.target.closest('.course-row'))closeCourseMenu();});
  document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(state.menuId){e.preventDefault();closeCourseMenu(true);return;}if(!$('course-dialog').open&&!$('materials-dialog').open&&!$('delete-course-dialog').open&&matchMedia('(max-width: 760px)').matches){sidebar(false);$('sidebar-open').focus();}});
  root.addEventListener('resize',()=>{sidebar(matchMedia('(max-width: 760px)').matches?document.body.classList.contains('sidebar-visible'):!state.collapsed);viewport();});root.visualViewport?.addEventListener('resize',viewport);root.visualViewport?.addEventListener('scroll',viewport);
  const drop=$('workspace');drop.addEventListener('dragenter',e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();dragDepth++;$('drop-hint').hidden=false;}});drop.addEventListener('dragover',e=>{if(e.dataTransfer.types.includes('Files'))e.preventDefault();});drop.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('drop-hint').hidden=true;}});drop.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('drop-hint').hidden=true;uploadFiles([...e.dataTransfer.files]);});
  async function boot(){
    busy(true);sidebar(!matchMedia('(max-width: 760px)').matches);viewport();
    try{const s=await api('/api/status');state.maxBytes=s.max_upload_bytes;$('upload-limit').textContent=`${Math.round(state.maxBytes/1048576)} MB`;await Promise.all([loadCourses(),loadLegacy()]);let id;try{id=localStorage.getItem('kippy.course');}catch{}if(state.courses.length){const chosen=state.courses.some(c=>c.id===id)?id:state.courses[0].id;state.course=await api(`/api/courses/${chosen}`);remember(chosen);}render();}
    catch(e){notice(`Could not load your saved courses: ${e.message}`);}finally{busy(false);}
    connection();setInterval(()=>{if(!document.hidden)connection();},30000);
  }
  boot();
})(typeof window==='undefined'?globalThis:window);
