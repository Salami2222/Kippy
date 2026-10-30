const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const {api,validateFile,sendTurn} = require('../kippy/static/app.js');
const root=path.resolve(__dirname,'../kippy/static');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
async function load({offline=false,mobile=false,initialCourses=[],legacy={conversations:[],materials:[]},selectedId=null,viewportHeight=null}={}) {
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://localhost:8000',runScripts:'outside-only'});
  const w=dom.window,courses=structuredClone(initialCourses),requests=[],uploads=[];let serial=20;const chatResolvers=new Map();
  const freshChat=(name)=>({id:(++serial).toString(16).padStart(32,'b'),title:name,messages:[],material_ids:[]});
  function normalize(c){c.conversations=c.conversations||[c.conversation];c.workspace=c.workspace||{mode:'single',single_id:c.conversation.id,left_id:c.conversation.id,right_id:null,active_panel:'left',split_initialized:0};return c;}
  courses.forEach(normalize);
  w.matchMedia=q=>({matches:mobile&&q.includes('980px')});w.requestAnimationFrame=cb=>cb();w.confirm=()=>true;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  if(viewportHeight!==null){const viewport=new w.EventTarget();viewport.height=viewportHeight;viewport.offsetTop=0;Object.defineProperty(w,'visualViewport',{value:viewport,configurable:true});}
  w.setInterval=()=>{};w.navigator.clipboard={writeText:async text=>{w.copied=text;}};
  if(selectedId)w.localStorage.setItem('kippy.course',selectedId);
  w.fetch=async(url,options={})=>{
    requests.push({url,...options});let result,status=200;const payload=options.body?JSON.parse(options.body):null;
    const parts=url.split('/'),course=courses.find(c=>c.id===parts[3]);
    if(url==='/api/status')result={max_upload_bytes:25*1048576};
    else if(url==='/api/ollama/status'){if(offline)throw Error('Offline');result={connected:true,model_available:true,message:'Qwen connected'};}
    else if(url==='/api/legacy')result=legacy;
    else if(url==='/api/courses'&&options.method==='POST'){
      if(!payload.name.trim()){status=400;result={error:'Name required'};}
      else {const id=(++serial).toString(16).padStart(32,'a');result=normalize({id,name:payload.name,code:payload.code||'',description:payload.description||'',conversation:freshChat('New Chat'),materials:[]});courses.push(result);}
    }
    else if(url==='/api/courses')result={courses:courses.map(({id,name,code,description})=>({id,name,code,description}))};
    else if(url==='/api/chat'){
      const c=courses.find(c=>c.id===payload.course_id),chat=c.conversations.find(x=>x.id===payload.conversation_id);chat.messages.push({role:'user',content:payload.message});
      await new Promise(resolve=>{chatResolvers.set(chat.id,resolve);});chat.messages.push({role:'assistant',content:'**Integration**\n\n$$y=Ce^x$$\n\n```python\nx = 2\n```',sources:c.materials.filter(m=>m.status==='ready').map(m=>({name:m.name,location:'page 1'}))});result={content:'Done'};
    }
    else if(course&&parts[4]==='layout'){Object.assign(course.workspace,payload);result=course.workspace;}
    else if(course&&parts[4]==='split'){
      course.splits=course.splits||[];if(!course.splits.includes(payload.request_id)&&(!course.workspace.split_initialized||payload.fresh)){const l=freshChat('Chat 1'),r=freshChat('Chat 2');course.conversations.push(l,r);Object.assign(course.workspace,{mode:'double',single_id:l.id,left_id:l.id,right_id:r.id,split_initialized:1,active_panel:'left'});}else course.workspace.mode='double';course.splits.push(payload.request_id);result=course;
    }
    else if(course&&parts[4]==='conversations'){
      const chat=course.conversations.find(x=>x.id===parts[5]);
      if(parts.length===5&&options.method==='POST'){result=freshChat(payload.name);course.conversations.push(result);}
      else if(!chat){status=404;result={error:'Chat not found'};}
      else if(options.method==='DELETE'){assert.equal(payload.confirm_conversation_id,chat.id);course.conversations.splice(course.conversations.indexOf(chat),1);const ids=course.conversations.map(x=>x.id);for(const field of ['single_id','left_id','right_id'])if(course.workspace[field]===chat.id)course.workspace[field]=null;if(!course.workspace.single_id)course.workspace.single_id=ids[0]||null;if(!course.workspace.left_id)course.workspace.left_id=ids.find(id=>id!==course.workspace.right_id)||null;if(!course.workspace.right_id&&course.workspace.split_initialized)course.workspace.right_id=ids.find(id=>id!==course.workspace.left_id)||null;result={deleted:true,course:{...course,conversation:course.conversations.find(c=>c.id===course.workspace.single_id)||null}};}
      else if(options.method==='POST'){chat.title=payload.name;result=chat;}
      else result=chat;
    }
    else if(course&&options.method==='DELETE'&&parts[4]==='materials'){course.materials.splice(course.materials.findIndex(m=>m.id===parts[5]),1);result={deleted:true};}
    else if(course&&options.method==='DELETE'){assert.equal(payload.confirm_course_id,course.id);assert.equal(payload.confirm_course_name,course.name);courses.splice(courses.indexOf(course),1);result={deleted:true,deleted_conversation_ids:course.conversations.map(c=>c.id)};}
    else if(course&&options.method==='POST'){Object.assign(course,payload);result=course;}
    else if(course)result={...course,conversation:course.conversations.find(c=>c.id===course.workspace.single_id)||course.conversations[0]||null};
    else {status=404;result={error:'Not found'};}
    return {ok:status<400,status,json:async()=>structuredClone(result)};
  };
  w.XMLHttpRequest=class {
    constructor(){this.upload={addEventListener:(_,cb)=>{this.processing=cb;}};}
    open(method,url){this.url=url;uploads.push({method,url});}setRequestHeader(){}
    send(file){this.processing();const c=courses.find(c=>c.id===this.url.split('/')[3]);const m={id:String(++serial).padStart(32,'d'),name:file.name,type:file.name.split('.').pop().toLowerCase(),status:'ready',warning:'Text only'};c.materials.push(m);this.status=201;this.responseText=JSON.stringify(m);this.onload();}
  };
  for(const n of ['vendor/marked.js','vendor/purify.js','vendor/katex/katex.min.js','vendor/katex/auto-render.min.js'])w.eval(fs.readFileSync(path.join(root,n),'utf8'));
  w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));await settle();
  const get=id=>w.document.getElementById(id);
  const create=async(name='Differential Equations',code='MATH246')=>{get('add-course').click();get('course-name').value=name;get('course-code-input').value=code;get('course-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();return courses.at(-1);};
  const upload=async(name='lecture.pdf')=>{Object.defineProperty(get('file-input'),'files',{value:[new w.File(['Lecture'],name)],configurable:true});get('file-input').dispatchEvent(new w.Event('change'));await settle();};
  return {w,dom,get,courses,requests,uploads,create,upload,finish:async(id)=>{const cid=id||chatResolvers.keys().next().value;chatResolvers.get(cid)();chatResolvers.delete(cid);await settle();}};
}

function finalCourse(a){a.get('continue-delete-course').click();a.get('delete-course-name').value=a.get('delete-course-title').textContent.slice(7,-1);a.get('delete-course-name').dispatchEvent(new a.w.Event('input'));}

test('fresh workspace has no courses or conversations and no global chat/library controls',async()=>{
  const a=await load();assert.equal(a.courses.length,0);assert.equal(a.get('empty-global').hidden,false);assert.equal(a.get('composer-area').hidden,true);
  assert.equal(a.get('panel-header').hidden,true);assert.equal(a.get('conversations'),null);assert.equal(a.get('upload-library'),null);
  assert.equal(a.requests.some(r=>r.method==='POST'),false);a.dom.window.close();
});
test('create a named course, persist selection, and rename the same conversation',async()=>{
  const a=await load();const c=await a.create();assert.equal(a.courses.length,1);assert.equal(a.get('course-title').textContent,c.name);assert.equal(a.get('composer-area').hidden,false);assert.equal(a.w.localStorage.getItem('kippy.course'),c.id);
  const cid=c.conversation.id;a.get('edit-course').click();a.get('course-name').value='Advanced ODEs';a.get('course-form').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();
  assert.equal(c.name,'Advanced ODEs');assert.equal(c.conversation.id,cid);assert.equal(a.get('course-title').textContent,'Advanced ODEs');a.dom.window.close();
});
test('switching courses and refreshing restore independent history',async()=>{
  const a=await load();const c=await a.create('Math'),b=await a.create('Chemistry');c.conversation.messages.push({role:'user',content:'Math memory'});b.conversation.messages.push({role:'user',content:'Chemistry memory'});
  a.w.document.querySelector(`[aria-label="Open course: Math"]`).click();await settle();assert.match(a.get('messages').textContent,/Math memory/);assert.doesNotMatch(a.get('messages').textContent,/Chemistry memory/);
  const saved=structuredClone(a.courses);a.dom.window.close();const restored=await load({initialCourses:saved,selectedId:c.id});assert.match(restored.get('messages').textContent,/Math memory/);assert.equal(restored.get('course-title').textContent,'Math');restored.dom.window.close();
});
test('course deletion dialog requires explicit confirmation and Cancel makes no request',async()=>{
  const a=await load();const c=await a.create();a.get('edit-course').click();a.get('delete-course').click();await settle();
  assert.equal(a.get('delete-course-dialog').open,true);assert.equal(a.get('delete-course-title').textContent,`Delete ${c.name}?`);
  assert.match(a.get('delete-course-dialog').textContent,/Every conversation/);assert.equal(a.w.document.activeElement,a.get('cancel-delete-course'));
  a.get('cancel-delete-course').click();await settle();assert.equal(a.courses.length,1);assert.equal(a.requests.filter(r=>r.method==='DELETE').length,0);
  a.get('delete-course').click();finalCourse(a);a.get('confirm-delete-course').click();await settle();assert.equal(a.courses.length,0);assert.equal(a.get('empty-global').hidden,false);assert.equal(a.get('delete-course-dialog').open,false);
  const r=a.requests.find(r=>r.method==='DELETE');assert.equal(JSON.parse(r.body).confirm_course_id,c.id);assert.equal(a.w.localStorage.getItem('kippy.course'),null);a.dom.window.close();
});
test('sidebar menu deletes an inactive course without changing active history or draft',async()=>{
  const a=await load({mobile:true});const math=await a.create('Math'),chem=await a.create('Chemistry');await a.upload('chemistry.txt');
  const history=chem.conversation.id;a.get('message').value='Unsent chemistry question';a.get('message').dispatchEvent(new a.w.Event('input'));a.get('sidebar-open').click();
  const toggle=a.get(`course-actions-${math.id}`);toggle.click();assert.equal(toggle.getAttribute('aria-expanded'),'true');
  const menu=a.get(`course-menu-${math.id}`);menu.querySelector('button').click();assert.equal(a.get('delete-course-title').textContent,'Delete Math?');
  finalCourse(a);a.get('confirm-delete-course').click();await settle();assert.equal(a.courses.length,1);assert.equal(a.courses[0].id,chem.id);assert.equal(chem.conversation.id,history);assert.equal(chem.materials.length,1);
  assert.equal(a.get('course-title').textContent,'Chemistry');assert.equal(a.get('message').value,'Unsent chemistry question');assert.equal(a.w.localStorage.getItem('kippy.course'),chem.id);a.dom.window.close();
});
test('deleting selected course selects a surviving course and persists that selection',async()=>{
  const a=await load();const math=await a.create('Math'),chem=await a.create('Chemistry');a.get(`course-actions-${chem.id}`).click();a.get(`course-menu-${chem.id}`).querySelector('button').click();finalCourse(a);a.get('confirm-delete-course').click();await settle();
  assert.equal(a.get('course-title').textContent,'Math');assert.equal(a.w.localStorage.getItem('kippy.course'),math.id);const saved=structuredClone(a.courses);a.dom.window.close();
  const b=await load({initialCourses:saved,selectedId:math.id});assert.equal(b.get('course-title').textContent,'Math');assert.equal(b.w.document.querySelectorAll('.course-entry').length,1);b.dom.window.close();
});
test('course menus close on Escape and outside click; deletion Escape cancels safely',async()=>{
  const a=await load({mobile:true});const c=await a.create('Math');a.get('sidebar-open').click();const t=a.get(`course-actions-${c.id}`),menu=a.get(`course-menu-${c.id}`);
  t.dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}));assert.equal(menu.hidden,false);assert.equal(a.w.document.activeElement,menu.querySelector('button'));
  a.w.document.dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Escape',cancelable:true}));assert.equal(menu.hidden,true);assert.equal(a.w.document.activeElement,t);assert.equal(a.get('sidebar').inert,false);
  t.click();a.w.document.body.click();assert.equal(menu.hidden,true);t.click();menu.querySelector('button').click();a.get('delete-course-dialog').dispatchEvent(new a.w.Event('cancel',{cancelable:true}));
  assert.equal(a.get('delete-course-dialog').open,false);assert.equal(a.courses.length,1);assert.equal(a.requests.some(r=>r.method==='DELETE'),false);a.dom.window.close();
});
test('failed deletion keeps the course and offers a visible retry error',async()=>{
  const a=await load();const c=await a.create('Math');const transport=a.w.fetch;a.w.fetch=async(url,options)=>{if(options?.method==='DELETE')throw Error('Deletion unavailable');return transport(url,options);};
  a.get(`course-actions-${c.id}`).click();a.get(`course-menu-${c.id}`).querySelector('button').click();finalCourse(a);a.get('confirm-delete-course').click();await settle();
  assert.equal(a.courses.length,1);assert.equal(a.get('final-delete-course-dialog').open,true);assert.match(a.get('delete-course-error').textContent,/Deletion unavailable/);assert.equal(a.get('confirm-delete-course').disabled,false);assert.equal(a.get('course-title').textContent,'Math');a.dom.window.close();
});
test('pending deletion prevents duplicate submissions and cancellation',async()=>{
  const a=await load();const c=await a.create('Math');const transport=a.w.fetch;let finish,count=0;a.w.fetch=async(url,options)=>{if(options?.method==='DELETE'){count++;await new Promise(resolve=>finish=resolve);}return transport(url,options);};
  a.get(`course-actions-${c.id}`).click();a.get(`course-menu-${c.id}`).querySelector('button').click();finalCourse(a);a.get('confirm-delete-course').click();await settle();
  a.get('confirm-delete-course').click();a.get('cancel-final-delete-course').click();a.get('final-delete-course-dialog').dispatchEvent(new a.w.Event('cancel',{cancelable:true}));assert.equal(count,1);assert.equal(a.get('final-delete-course-dialog').open,true);
  finish();await settle();assert.equal(a.courses.length,0);assert.equal(a.get('delete-course-dialog').open,false);a.dom.window.close();
});
test('decorative sidebar copy and its bordered box are removed; turtle remains',async()=>{
  const a=await load();assert.doesNotMatch(a.get('sidebar').textContent,/A quiet place to discover|A little focus|A little wonder|Your studies are saved on your server/);
  assert.equal(a.w.document.querySelector('.sidebar-note'),null);assert.equal(a.w.document.querySelector('.vignette-caption'),null);assert.equal(a.w.document.querySelectorAll('.study-turtle').length,1);a.dom.window.close();
});
test('course-scoped upload, materials drawer and removal use existing file transport',async()=>{
  const a=await load();const c=await a.create();a.get('show-materials').click();assert.equal(a.get('materials-dialog').open,true);await a.upload();assert.match(a.uploads[0].url,new RegExp(`/api/courses/${c.id}/materials`));assert.match(a.get('materials').textContent,/Ready/i);assert.equal(a.get('material-count').textContent,'1');
  a.w.document.querySelector('[aria-label="Remove material: lecture.pdf"]').click();await settle();assert.equal(c.materials.length,0);a.get('close-materials').click();assert.equal(a.get('materials-dialog').open,false);a.dom.window.close();
});
test('chat is course-scoped, guarded while busy, and renders Markdown math code sources and copy',async()=>{
  const a=await load();const c=await a.create();await a.upload();a.get('message').value='Explain integration';a.get('composer').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();
  assert.equal(a.get('thinking').hidden,false);assert.equal(a.get('send').disabled,true);assert.equal(a.get('add-course').disabled,true);a.get('composer').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();assert.equal(a.requests.filter(r=>r.url==='/api/chat').length,1);assert.equal(JSON.parse(a.requests.find(r=>r.url==='/api/chat').body).course_id,c.id);
  await a.finish();assert.ok(a.w.document.querySelector('.message-body strong'));assert.ok(a.w.document.querySelector('.katex'));assert.ok(a.w.document.querySelector('pre code'));assert.match(a.get('messages').textContent,/lecture.pdf · page 1/);assert.equal(a.get('thinking').hidden,true);
  a.w.document.querySelector('.copy-button').click();await settle();assert.match(a.w.copied,/Integration/);a.dom.window.close();
});
test('Markdown sanitization, bracket LaTeX and attribute escaping stay safe',async()=>{
  const a=await load();const c=await a.create();c.conversation.messages.push({role:'assistant',content:`<img src=x onerror="alert(1)"><script>alert(1)</script>\\[x^2\\]<a title='$" onmouseover="alert(1)$'>reference</a>`});a.w.document.querySelector('.course-entry').click();await settle();
  assert.equal(a.w.document.querySelector('.message-body img'),null);assert.equal(a.w.document.querySelector('.message-body script'),null);assert.equal(a.w.document.querySelector('.message-body a').getAttribute('onmouseover'),null);assert.ok(a.w.document.querySelector('.katex-display'));a.dom.window.close();
});
test('mobile course navigation opens and closes the sidebar and traps hidden navigation',async()=>{
  const a=await load({mobile:true});assert.equal(a.get('sidebar').inert,true);a.get('sidebar-open').click();assert.equal(a.get('sidebar').inert,false);await a.create();assert.equal(a.get('sidebar').inert,true);assert.equal(a.get('sidebar-shade').hidden,true);
  a.get('sidebar-open').click();a.w.document.dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Escape'}));assert.equal(a.get('sidebar').inert,true);a.dom.window.close();
});
test('offline model status leaves courses usable and long course names are text, not HTML',async()=>{
  const a=await load({offline:true});assert.match(a.get('connection').textContent,/unavailable/);await a.create('<script>alert(1)</script>日本語','');assert.match(a.get('course-title').textContent,/script/);assert.equal(a.get('courses').querySelector('script'),null);a.dom.window.close();
});
test('Enter sends, Shift+Enter and IME do not submit',async()=>{
  const a=await load();await a.create();a.get('message').value='Explain';for(const data of [{key:'Enter',shiftKey:true},{key:'Enter',isComposing:true}])a.get('message').dispatchEvent(new a.w.KeyboardEvent('keydown',{...data,bubbles:true,cancelable:true}));await settle();assert.equal(a.requests.filter(r=>r.url==='/api/chat').length,0);
  a.get('message').dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));await settle();assert.equal(a.requests.filter(r=>r.url==='/api/chat').length,1);await a.finish();a.dom.window.close();
});
test('upload processing locks course switching and errors stay visible without mixing files',async()=>{
  const a=await load();await a.create('Math');await a.create('Chemistry');let xhr;
  a.w.XMLHttpRequest=class{constructor(){xhr=this;this.upload={addEventListener:(_,cb)=>this.processing=cb};}open(){}setRequestHeader(){}send(){this.processing();}};
  Object.defineProperty(a.get('file-input'),'files',{value:[new a.w.File(['notes'],'lecture.txt')],configurable:true});a.get('file-input').dispatchEvent(new a.w.Event('change'));await settle();assert.match(a.get('upload-activity').textContent,/Processing/i);assert.equal(a.w.document.querySelector('[aria-label="Open course: Math"]').disabled,true);
  xhr.onerror();await settle();assert.match(a.get('notice-text').textContent,/connection lost/);assert.equal(a.get('send').disabled,false);a.dom.window.close();
});
test('unsupported uploads are refused and uploads without a course do not create one',async()=>{
  const a=await load();await a.upload('bad.exe');assert.equal(a.courses.length,0);assert.equal(a.uploads.length,0);await a.create();await a.upload('bad.exe');assert.equal(a.uploads.length,0);assert.match(a.get('notice-text').textContent,/Supported formats/);a.dom.window.close();
});
test('drag and drop uploads into the active course',async()=>{
  const a=await load();const c=await a.create();const e=new a.w.Event('drop',{cancelable:true});Object.defineProperty(e,'dataTransfer',{value:{files:[new a.w.File(['Notes'],'notes.txt')]}});a.get('workspace').dispatchEvent(e);await settle();assert.equal(c.materials.length,1);assert.match(a.uploads[0].url,new RegExp(c.id));a.dom.window.close();
});
test('suggestion chips prepare a course question without sending automatically',async()=>{
  const a=await load();await a.create();for(const chip of a.get('chat-left').querySelectorAll('[data-prompt]')){chip.click();assert.equal(a.get('message').value,chip.dataset.prompt);assert.equal(a.w.document.activeElement,a.get('message'));}assert.equal(a.requests.filter(r=>r.url==='/api/chat').length,0);a.dom.window.close();
});
test('existing studies are offered explicitly; chosen import IDs go only into course creation',async()=>{
  const a=await load({legacy:{conversations:[{id:'a'.repeat(32),title:'Old math'}],materials:[{id:'b'.repeat(32),name:'Old notes.pdf',imported:false}]}});
  assert.equal(a.courses.length,0);assert.equal(a.get('recover-studies').hidden,false);a.get('recover-studies').click();assert.equal(a.get('legacy-options').open,true);a.get('course-name').value='Recovered Math';a.get('legacy-conversation').value='a'.repeat(32);a.get('legacy-materials').querySelector('input').checked=true;a.get('course-form').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();
  const body=JSON.parse(a.requests.find(r=>r.url==='/api/courses'&&r.method==='POST').body);assert.equal(body.legacy_conversation_id,'a'.repeat(32));assert.deepEqual(body.legacy_material_ids,['b'.repeat(32)]);a.dom.window.close();
});
test('validation rejects unsupported, empty and oversized uploads',()=>{
  for(const name of ['x.pdf','x.PPTX','x.docx','x.txt','x.ZIP'])assert.equal(validateFile({name,size:10},100),'');
  for(const f of [{name:'x.exe',size:1},{name:'x.txt',size:0},{name:'x.txt',size:101}])assert.notEqual(validateFile(f,100),'');
});
test('request helper retains safe transport and HTTP/JSON errors',async()=>{
  const calls=[];await sendTurn('a'.repeat(32),'c'.repeat(32),' Explain ','b'.repeat(32),async(...args)=>calls.push(args));assert.deepEqual(calls[0],['/api/chat',{method:'POST',body:{course_id:'a'.repeat(32),conversation_id:'c'.repeat(32),message:'Explain',request_id:'b'.repeat(32)}}]);
  await assert.rejects(api('/test',{fetcher:async()=>({ok:false,json:async()=>({error:'Too large'})})}),/Too large/);await assert.rejects(api('/test',{fetcher:async()=>({json:async()=>{throw Error();}})}),/unreadable/);
});

test('successful course creation remains visible if a later list refresh fails',async()=>{
  const a=await load();const transport=a.w.fetch;a.w.fetch=async(url,options)=>{if(url==='/api/courses'&&(options?.method||'GET')==='GET')throw Error('Connection lost');return transport(url,options);};
  await a.create('Math');assert.equal(a.courses.length,1);assert.equal(a.get('course-dialog').open,false);assert.equal(a.get('course-title').textContent,'Math');assert.match(a.get('notice-text').textContent,/Connection lost/);assert.equal(a.w.document.querySelectorAll('.course-entry').length,1);a.dom.window.close();
});
test('confirmed deletion does not leave a phantom course after refresh failure',async()=>{
  const a=await load();await a.create('Math');const transport=a.w.fetch;a.w.fetch=async(url,options)=>{if(url==='/api/courses'&&(options?.method||'GET')==='GET')throw Error('Connection lost');return transport(url,options);};
  a.get('edit-course').click();a.get('delete-course').click();finalCourse(a);a.get('confirm-delete-course').click();await settle();assert.equal(a.courses.length,0);assert.equal(a.get('course-dialog').open,false);assert.equal(a.get('empty-global').hidden,false);assert.equal(a.w.document.querySelectorAll('.course-entry').length,0);assert.match(a.get('notice-text').textContent,/Connection lost/);a.dom.window.close();
});

async function newSavedChat(a,name,side='left'){
  a.get(side==='right'?'new-chat-right':'new-chat').click();a.get('chat-name').value=name;a.get('chat-form').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();return a.courses[0].conversations.at(-1);
}
function typeDraft(a,text,side='left'){const input=a.get(side==='right'?'message-right':'message');input.value=text;input.dispatchEvent(new a.w.Event('input'));}
function selectChat(a,id,side='left'){const select=a.get(side==='right'?'chat-selector-right':'chat-selector');select.value=id;select.dispatchEvent(new a.w.Event('change'));}

test('saved chats create empty named history and can rename and reopen original',async()=>{
  const a=await load();const c=await a.create();const original=c.conversation.id;c.conversation.messages.push({role:'user',content:'Original history'});
  const chat=await newSavedChat(a,'Lecture Review');assert.equal(chat.messages.length,0);assert.equal(a.get('chat-selector').value,chat.id);assert.equal(a.get('messages').textContent,'');
  a.get('rename-chat').click();a.get('chat-name').value='Exam Preparation';a.get('chat-form').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();assert.equal(chat.title,'Exam Preparation');assert.match(a.get('saved-chats').textContent,/Exam Preparation/);
  selectChat(a,original);await settle();assert.match(a.get('messages').textContent,/Original history/);assert.equal(c.conversations.length,2);a.dom.window.close();
});
test('first Double Chat creates a fresh pair, keeps original, and shares one material library',async()=>{
  const a=await load();const c=await a.create();await a.upload('variation.pdf');const original=c.conversation.id;c.conversation.messages.push({role:'user',content:'Original private context'});
  a.get('double-mode').click();await settle();const l=c.workspace.left_id,r=c.workspace.right_id;
  assert.equal(c.conversations.length,3);assert.notEqual(l,r);assert.notEqual(l,original);assert.equal(a.get('messages').textContent,'');assert.equal(a.get('messages-right').textContent,'');assert.match(a.get('layout-note').textContent,/not chat history/);
  assert.equal(c.materials.length,1);assert.equal(a.get('material-count').textContent,'1');assert.equal(a.get('chat-selector-right').querySelector(`option[value="${l}"]`).disabled,true);
  selectChat(a,original);await settle();assert.equal(c.workspace.right_id,r);assert.match(a.get('messages').textContent,/Original private context/);assert.equal(a.get('messages-right').textContent,'');a.dom.window.close();
});
test('layout toggles retain pair and independent drafts; explicit new pair alone creates chats',async()=>{
  const a=await load({mobile:true});const c=await a.create();a.get('double-mode').click();await settle();const pair=[c.workspace.left_id,c.workspace.right_id];
  typeDraft(a,'Draft A');typeDraft(a,'Draft B','right');a.get('tab-right').click();assert.equal(a.get('chat-right').classList.contains('targeted'),true);
  a.get('single-mode').click();await settle();assert.equal(a.get('chat-selector').value,pair[1]);assert.equal(a.get('message').value,'Draft B');typeDraft(a,'Edited B');
  a.get('double-mode').click();await settle();assert.deepEqual([c.workspace.left_id,c.workspace.right_id],pair);assert.equal(c.conversations.length,3);assert.equal(a.get('message').value,'Draft A');assert.equal(a.get('message-right').value,'Edited B');
  const saved=structuredClone(a.courses);a.dom.window.close();const restored=await load({mobile:true,initialCourses:saved,selectedId:c.id});assert.equal(restored.get('chat-right').hidden,false);assert.equal(restored.get('chat-selector').value,pair[0]);assert.equal(restored.get('chat-selector-right').value,pair[1]);
  restored.get('new-double-chat').click();await settle();assert.equal(restored.courses[0].conversations.length,5);assert.notEqual(restored.get('chat-selector').value,pair[0]);restored.dom.window.close();
});
test('both panels submit independently, remain editable separately, and never mix replies or scrolling',async()=>{
  const a=await load();const c=await a.create();a.get('double-mode').click();await settle();const l=c.workspace.left_id,r=c.workspace.right_id;
  a.get('feed-right').scrollTop=37;a.get('feed-right').dispatchEvent(new a.w.Event('scroll'));typeDraft(a,'LEFT_QUESTION');a.get('composer').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();
  assert.equal(a.get('thinking').hidden,false);assert.equal(a.get('message-right').disabled,false);assert.equal(a.get('feed-right').scrollTop,37);
  typeDraft(a,'RIGHT_QUESTION','right');a.get('composer-right').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();assert.equal(a.get('thinking-right').hidden,false);
  const requests=a.requests.filter(x=>x.url==='/api/chat');assert.equal(requests.length,2);assert.notEqual(JSON.parse(requests[0].body).request_id,JSON.parse(requests[1].body).request_id);
  await a.finish(r);assert.equal(a.get('thinking-right').hidden,true);assert.equal(a.get('thinking').hidden,false);assert.match(a.get('messages-right').textContent,/RIGHT_QUESTION/);assert.doesNotMatch(a.get('messages-right').textContent,/LEFT_QUESTION/);
  await a.finish(l);assert.equal(a.get('thinking').hidden,true);assert.match(a.get('messages').textContent,/LEFT_QUESTION/);assert.doesNotMatch(a.get('messages').textContent,/RIGHT_QUESTION/);assert.ok(a.get('messages-right').querySelector('.katex'));a.dom.window.close();
});
test('deleting a saved chat requires confirmation, preserves documents, and last chat can be recreated',async()=>{
  const a=await load();const c=await a.create();await a.upload();const first=c.conversation.id;await newSavedChat(a,'Second');a.get('delete-chat').click();a.get('cancel-delete-chat').click();assert.equal(c.conversations.length,2);
  a.get('delete-chat').click();a.get('confirm-delete-chat').click();await settle();assert.equal(c.conversations.length,1);assert.equal(c.materials.length,1);assert.equal(a.get('chat-selector').value,first);
  a.get('delete-chat').click();a.get('confirm-delete-chat').click();await settle();assert.equal(c.conversations.length,0);assert.equal(c.materials.length,1);assert.equal(a.get('composer-area').hidden,true);assert.equal(a.get('new-chat').disabled,false);
  await newSavedChat(a,'Fresh Start');assert.equal(c.conversations.length,1);assert.equal(a.get('composer-area').hidden,false);assert.equal(a.get('messages').textContent,'');a.dom.window.close();
});
test('switching courses restores only their saved chats and selected layout',async()=>{
  const a=await load();const math=await a.create('Math');a.get('double-mode').click();await settle();const pair=[math.workspace.left_id,math.workspace.right_id];const chem=await a.create('Chemistry');
  assert.equal(a.get('chat-right').hidden,true);assert.equal(chem.conversations.length,1);assert.doesNotMatch(a.get('saved-chats').textContent,/Chat 1/);
  a.w.document.querySelector('[aria-label="Open course: Math"]').click();await settle();assert.equal(a.get('chat-right').hidden,false);assert.deepEqual([a.get('chat-selector').value,a.get('chat-selector-right').value],pair);a.dom.window.close();
});
test('panel-local connection error preserves retry identity and the other draft',async()=>{
  const a=await load();await a.create();a.get('double-mode').click();await settle();typeDraft(a,'Keep right draft','right');const transport=a.w.fetch;const attempted=[];let fail=true;
  a.w.fetch=async(url,opt)=>{if(url==='/api/chat'){attempted.push(JSON.parse(opt.body));if(fail){fail=false;throw Error('Connection lost');}}return transport(url,opt);};
  typeDraft(a,'Retry this');a.get('composer').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();assert.match(a.get('panel-error').textContent,/Connection lost/);assert.equal(a.get('panel-error-right').hidden,true);assert.equal(a.get('message-right').value,'Keep right draft');
  a.get('composer').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();assert.equal(attempted[0].request_id,attempted[1].request_id);await a.finish();assert.equal(a.get('panel-error').hidden,true);assert.equal(a.get('message-right').value,'Keep right draft');a.dom.window.close();
});

test('confirmed chat creation is not duplicated after a follow-up read failure',async()=>{
  const a=await load();const c=await a.create();const transport=a.w.fetch;let fail=true;
  a.w.fetch=async(url,opt)=>{if(fail&&url===`/api/courses/${c.id}`&&(!opt?.method||opt.method==='GET')){fail=false;throw Error('List unavailable');}return transport(url,opt);};
  await newSavedChat(a,'Saved once');assert.equal(c.conversations.length,2);assert.equal(a.get('chat-dialog').open,true);assert.match(a.get('chat-error').textContent,/List unavailable/);
  a.get('chat-form').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();assert.equal(c.conversations.length,2);assert.equal(a.get('chat-selector').value,c.conversations[1].id);assert.equal(a.get('chat-dialog').open,false);a.dom.window.close();
});

test('course deletion requires separate warning, exact-name verification, and supports cancellation at either step',async()=>{
 const a=await load();const c=await a.create('Math');
 const open=()=>{a.get(`course-actions-${c.id}`).click();a.get(`course-menu-${c.id}`).querySelector('button').click();};
 open();assert.equal(a.get('confirm-delete-course').disabled,true);a.get('confirm-delete-course').dispatchEvent(new a.w.Event('click'));await settle();assert.equal(a.requests.some(r=>r.method==='DELETE'),false);
 a.get('cancel-delete-course').click();open();a.get('continue-delete-course').click();assert.equal(a.get('delete-course-dialog').open,false);assert.equal(a.get('final-delete-course-dialog').open,true);
 for(const name of ['math','Math ','','MATH']){a.get('delete-course-name').value=name;a.get('delete-course-name').dispatchEvent(new a.w.Event('input'));assert.equal(a.get('confirm-delete-course').disabled,true);a.get('confirm-delete-course').dispatchEvent(new a.w.Event('click'));}
 await settle();assert.equal(a.requests.some(r=>r.method==='DELETE'),false);a.get('cancel-final-delete-course').click();assert.equal(a.get('final-delete-course-dialog').open,false);assert.equal(a.get('delete-course-name').value,'');assert.equal(a.courses.length,1);
 open();finalCourse(a);assert.equal(a.get('confirm-delete-course').disabled,false);a.get('confirm-delete-course').click();await settle();assert.equal(a.courses.length,0);assert.equal(a.get('deletion-status').textContent,'Course deleted.');a.dom.window.close();
});
test('every saved conversation offers confirmed deletion, clears its draft and leaves shared files and other chats',async()=>{
 const a=await load();const c=await a.create('Math');await a.upload('notes.txt');a.get('message').value='Private draft';a.get('message').dispatchEvent(new a.w.Event('input'));const original=c.conversation.id;
 a.get('double-mode').click();await settle();const remove=()=>a.w.document.querySelector(`[aria-label="Delete conversation: New Chat"]`).click();
 remove();assert.match(a.get('delete-chat-description').textContent,/New Chat/);a.get('cancel-delete-chat').click();assert.equal(c.conversations.length,3);assert.equal(a.requests.some(r=>r.method==='DELETE'),false);
 remove();a.get('confirm-delete-chat').click();await settle();assert.equal(c.conversations.length,2);assert.equal(c.materials.length,1);assert.equal(JSON.parse(a.w.sessionStorage.getItem('kippy.drafts'))[original],undefined);assert.equal(a.get('deletion-status').textContent,'Conversation deleted.');
 const restored=await load({initialCourses:structuredClone(a.courses),selectedId:c.id});assert.equal(restored.courses[0].conversations.some(x=>x.id===original),false);a.dom.window.close();restored.dom.window.close();
});
test('conversation deletion failures remain visible and never report success',async()=>{
 const a=await load();await a.create('Math');const transport=a.w.fetch;a.w.fetch=async(url,options)=>{if(options?.method==='DELETE')throw Error('Cannot delete');return transport(url,options);};a.get('delete-chat').click();a.get('confirm-delete-chat').click();await settle();assert.equal(a.courses[0].conversations.length,1);assert.match(a.get('delete-chat-error').textContent,/Cannot delete/);assert.equal(a.get('deletion-status').hidden,true);a.dom.window.close();
});

function fileEvent(a,type,files,target){const e=new a.w.Event(type,{bubbles:true,cancelable:true});Object.defineProperty(e,'dataTransfer',{value:{files,types:['Files'],dropEffect:'none'}});(target||a.get('materials-drop-zone')).dispatchEvent(e);return e;}
test('materials drawer always exposes upload zone, Browse Files, limit and saved-file empty state',async()=>{
 const a=await load();await a.create();a.get('show-materials').click();await settle();assert.equal(a.get('materials-drop-zone').hidden,false);assert.match(a.get('materials-drop-zone').textContent,/Drag & drop files here/);assert.equal(a.get('upload-materials').textContent,'Browse Files');assert.match(a.get('materials-upload-help').textContent,/PDF.*PPTX.*DOCX.*TXT.*25 MB/);assert.equal(a.get('saved-file-count').textContent,'0 files');assert.match(a.get('materials').textContent,/No files uploaded yet/);
 let browsed=0;a.get('file-input').click=()=>browsed++;a.get('upload-materials').click();assert.equal(browsed,1);a.dom.window.close();
});
test('drawer nested drop uploads multiple files exactly once and resets highlighting',async()=>{
 const a=await load();const c=await a.create();a.get('show-materials').click();await settle();const files=[new a.w.File(['A'],'a.txt'),new a.w.File(['B'],'b.txt')];
 fileEvent(a,'dragenter',files,a.get('upload-materials'));assert.equal(a.get('materials-drop-zone').classList.contains('drag-over'),true);fileEvent(a,'dragleave',files,a.get('upload-materials'));assert.equal(a.get('materials-drop-zone').classList.contains('drag-over'),false);
 fileEvent(a,'dragenter',files);const e=fileEvent(a,'drop',files,a.get('upload-materials'));await settle();assert.equal(e.defaultPrevented,true);assert.equal(a.uploads.length,2);assert.equal(c.materials.length,2);assert.equal(a.get('saved-file-count').textContent,'2 files');assert.equal(a.get('materials-drop-zone').classList.contains('drag-over'),false);assert.equal(a.get('drop-hint').hidden,true);a.dom.window.close();
});
test('workspace nested drop uploads once and ordinary text drag does not show an overlay',async()=>{
 const a=await load();await a.create();const text=new a.w.Event('dragenter',{bubbles:true,cancelable:true});Object.defineProperty(text,'dataTransfer',{value:{types:['text/plain'],files:[]}});a.get('message').dispatchEvent(text);assert.equal(a.get('drop-hint').hidden,true);assert.equal(text.defaultPrevented,false);
 const files=[new a.w.File(['Notes'],'notes.txt')];fileEvent(a,'dragenter',files,a.get('message'));assert.equal(a.get('drop-hint').hidden,false);fileEvent(a,'drop',files,a.get('message'));await settle();assert.equal(a.uploads.length,1);assert.equal(a.get('drop-hint').hidden,true);a.dom.window.close();
});
test('reopening materials fetches persisted files and course switches never show foreign files',async()=>{
 const a=await load();const math=await a.create('Math');await a.upload('math.txt');const chem=await a.create('Chemistry');await a.upload('chem.txt');
 a.get('show-materials').click();await settle();assert.match(a.get('materials').textContent,/chem.txt/);assert.doesNotMatch(a.get('materials').textContent,/math.txt/);a.get('close-materials').click();chem.materials.push({id:'f'.repeat(32),name:'Saved elsewhere.pdf',type:'pdf',status:'failed',error:'No readable text'});a.get('show-materials').click();await settle();assert.equal(a.get('saved-file-count').textContent,'2 files');assert.match(a.get('materials').textContent,/Saved elsewhere.pdf/);assert.equal(a.w.document.querySelector('[data-status="failed"]').textContent.includes('Failed'),true);a.get('close-materials').click();
 const saved=structuredClone(a.courses);a.dom.window.close();const b=await load({initialCourses:saved,selectedId:math.id});b.get('show-materials').click();await settle();assert.equal(b.get('saved-file-count').textContent,'1 file');assert.match(b.get('materials').textContent,/math.txt/);assert.doesNotMatch(b.get('materials').textContent,/chem.txt|Saved elsewhere/);b.dom.window.close();
});
test('invalid drawer drops reject unsupported and oversized files without creating ready records',async()=>{
 const a=await load();await a.create();const big=new a.w.File(['Big'],'huge.txt');Object.defineProperty(big,'size',{value:26*1048576});fileEvent(a,'drop',[new a.w.File(['X'],'bad.exe'),big]);await settle();assert.equal(a.uploads.length,0);assert.equal(a.get('saved-file-count').textContent,'0 files');assert.match(a.get('materials-error').textContent,/under 25 MB/);a.dom.window.close();
});
test('failed upload is visible as unconfirmed, and refresh failure does not discard a confirmed saved file or abort the batch',async()=>{
 const a=await load();await a.create();a.w.XMLHttpRequest=class{constructor(){this.upload={addEventListener:()=>{}};}open(){}setRequestHeader(){}send(){this.onerror();}};fileEvent(a,'drop',[new a.w.File(['X'],'lost.txt')]);await settle();assert.match(a.get('upload-activity').textContent,/Failed.*not confirmed/);assert.equal(a.get('saved-file-count').textContent,'0 files');assert.equal(a.get('materials-drop-zone').getAttribute('aria-busy'),'false');a.dom.window.close();
 const b=await load();await b.create();const transport=b.w.fetch;b.w.fetch=async(url,options)=>{if(/api\/courses\/[a-f0-9]+$/.test(url)&&!options.method)throw Error('Read unavailable');if(/api\/courses\/[a-f0-9]+$/.test(url)&&options.method==='GET')throw Error('Read unavailable');return transport(url,options);};fileEvent(b,'drop',[new b.w.File(['A'],'a.txt'),new b.w.File(['B'],'b.txt')]);await settle();assert.equal(b.uploads.length,2);assert.equal(b.get('saved-file-count').textContent,'2 files');assert.match(b.get('materials').textContent,/a.txt/);assert.match(b.get('materials-error').textContent,/refresh saved course files/);b.dom.window.close();
});

test('ZIP drawer drop handles a partial batch response and never lists the ZIP as a material',async()=>{
 const a=await load();const c=await a.create('Math');a.w.XMLHttpRequest=class{
 constructor(){this.upload={addEventListener:(type,cb)=>{if(type==='load')this.processing=cb;if(type==='progress')this.progress=cb;}};}open(){}setRequestHeader(){}send(file){this.progress?.({lengthComputable:true,loaded:1,total:2});assert.match(a.get('upload-activity').textContent,/50%/);this.processing();assert.match(a.get('upload-activity').textContent,/Processing ZIP/);
 const good={id:'1'.repeat(32),name:'Lecture.pdf',type:'pdf',status:'ready'},bad={id:'2'.repeat(32),name:'broken.pdf',type:'pdf',status:'failed',error:'Not readable'};c.materials.push(good,bad);this.status=201;this.responseText=JSON.stringify({kind:'archive',name:file.name,materials:[good,bad],imported:[{name:'Week/Lecture.pdf',material_id:good.id}],failed:[{name:'Week/broken.pdf',error:bad.error}],skipped:[{name:'nested.zip',reason:'Nested archives are not extracted.'}],summary:{imported:1,failed:1,skipped:1}});this.onload();}};
 fileEvent(a,'drop',[new a.w.File(['zip fixture'],'lectures.zip')]);await settle();assert.equal(a.get('saved-file-count').textContent,'2 files');assert.match(a.get('archive-reports').textContent,/Successfully imported 1 file from lectures.zip/);assert.match(a.get('archive-reports').textContent,/Week\/broken.pdf.*Not readable/);assert.match(a.get('archive-reports').textContent,/nested.zip.*Skipped/);assert.doesNotMatch(a.get('materials').textContent,/lectures.zip/);assert.equal(c.materials.filter(m=>m.status==='ready').length,1);
 const restored=await load({initialCourses:structuredClone(a.courses),selectedId:c.id});restored.get('show-materials').click();await settle();assert.equal(restored.get('saved-file-count').textContent,'2 files');assert.match(restored.get('materials').textContent,/Lecture.pdf/);a.dom.window.close();restored.dom.window.close();
});
test('Browse Files advertises ZIP and rejected archives never create ready rows',async()=>{
 const a=await load();await a.create();assert.match(a.get('file-input').accept,/\.zip/);assert.match(a.get('materials-upload-help').textContent,/ZIP/);a.w.XMLHttpRequest=class{constructor(){this.upload={addEventListener:()=>{}};}open(){}setRequestHeader(){}send(){this.status=400;this.responseText=JSON.stringify({error:'Encrypted ZIP rejected; nothing imported.'});this.onload();}};
 fileEvent(a,'drop',[new a.w.File(['encrypted'],'locked.zip')]);await settle();assert.equal(a.get('saved-file-count').textContent,'0 files');assert.match(a.get('materials-error').textContent,/Encrypted ZIP rejected/);assert.match(a.get('upload-activity').textContent,/Failed/);assert.equal(a.get('archive-reports').hidden,true);a.dom.window.close();
});

test('visual viewport resizing keeps composer sizing and unsent chat state intact',async()=>{
 const a=await load({mobile:true,viewportHeight:844});await a.create();typeDraft(a,'Keep this question');const id=a.get('chat-selector').value;
 assert.equal(a.w.document.documentElement.style.getPropertyValue('--viewport-height'),'844px');a.w.visualViewport.height=420;a.w.visualViewport.offsetTop=12;a.w.visualViewport.dispatchEvent(new a.w.Event('resize'));
 assert.equal(a.w.document.body.style.height,'420px');assert.equal(a.w.document.body.style.top,'12px');assert.equal(a.w.document.documentElement.style.getPropertyValue('--viewport-height'),'420px');assert.equal(a.get('message').value,'Keep this question');assert.equal(a.get('chat-selector').value,id);
 a.w.visualViewport.height=844;a.w.visualViewport.offsetTop=0;a.w.visualViewport.dispatchEvent(new a.w.Event('resize'));assert.equal(a.w.document.body.style.height,'844px');assert.equal(a.get('message').value,'Keep this question');a.dom.window.close();
});
