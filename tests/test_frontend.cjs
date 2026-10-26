const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const {api,validateFile,sendTurn} = require('../kippy/static/app.js');
const root=path.resolve(__dirname,'../kippy/static');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
async function load({offline=false,mobile=false,initialCourses=[],legacy={conversations:[],materials:[]},selectedId=null}={}) {
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://localhost:8000',runScripts:'outside-only'});
  const w=dom.window,courses=structuredClone(initialCourses),requests=[],uploads=[];let serial=20,chatResolve;
  w.matchMedia=q=>({matches:mobile&&q.includes('760px')});w.requestAnimationFrame=cb=>cb();w.confirm=()=>true;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
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
      else {const id=(++serial).toString(16).padStart(32,'a');result={id,name:payload.name,code:payload.code||'',description:payload.description||'',conversation:{id:id.replace('a','b'),messages:[],material_ids:[]},materials:[]};courses.push(result);}
    }
    else if(url==='/api/courses')result={courses:courses.map(({id,name,code,description})=>({id,name,code,description}))};
    else if(url==='/api/chat'){
      const c=courses.find(c=>c.id===payload.course_id);c.conversation.messages.push({role:'user',content:payload.message});
      await new Promise(resolve=>{chatResolve=resolve;});c.conversation.messages.push({role:'assistant',content:'**Integration**\n\n$$y=Ce^x$$\n\n```python\nx = 2\n```',sources:c.materials.filter(m=>m.status==='ready').map(m=>({name:m.name,location:'page 1'}))});result={content:'Done'};
    }
    else if(course&&options.method==='DELETE'&&parts[4]==='materials'){course.materials.splice(course.materials.findIndex(m=>m.id===parts[5]),1);result={deleted:true};}
    else if(course&&options.method==='DELETE'){assert.equal(payload.confirm_course_id,course.id);courses.splice(courses.indexOf(course),1);result={deleted:true};}
    else if(course&&options.method==='POST'){Object.assign(course,payload);result=course;}
    else if(course)result=course;
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
  return {w,dom,get,courses,requests,uploads,create,upload,finish:async()=>{chatResolve();await settle();}};
}

test('fresh workspace has no courses or conversations and no global chat/library controls',async()=>{
  const a=await load();assert.equal(a.courses.length,0);assert.equal(a.get('empty-global').hidden,false);assert.equal(a.get('composer-area').hidden,true);
  assert.equal(a.get('new-chat'),null);assert.equal(a.get('conversations'),null);assert.equal(a.get('upload-library'),null);
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
test('course deletion requires explicit confirmation and sends the course ID',async()=>{
  const a=await load();const c=await a.create();a.get('edit-course').click();let text='';a.w.confirm=t=>{text=t;return false;};a.get('delete-course').click();await settle();assert.equal(a.courses.length,1);assert.match(text,/entire conversation and all uploaded/);
  a.w.confirm=()=>true;a.get('delete-course').click();await settle();assert.equal(a.courses.length,0);assert.equal(a.get('empty-global').hidden,false);const r=a.requests.find(r=>r.method==='DELETE');assert.equal(JSON.parse(r.body).confirm_course_id,c.id);a.dom.window.close();
});
test('course-scoped upload, materials drawer and removal use existing file transport',async()=>{
  const a=await load();const c=await a.create();a.get('show-materials').click();assert.equal(a.get('materials-dialog').open,true);await a.upload();assert.match(a.uploads[0].url,new RegExp(`/api/courses/${c.id}/materials`));assert.match(a.get('materials').textContent,/ready/);assert.equal(a.get('material-count').textContent,'1');
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
  Object.defineProperty(a.get('file-input'),'files',{value:[new a.w.File(['notes'],'lecture.txt')],configurable:true});a.get('file-input').dispatchEvent(new a.w.Event('change'));await settle();assert.match(a.get('materials').textContent,/processing/);assert.equal(a.w.document.querySelector('[aria-label="Open course: Math"]').disabled,true);
  xhr.onerror();await settle();assert.match(a.get('notice-text').textContent,/connection lost/);assert.equal(a.get('send').disabled,false);a.dom.window.close();
});
test('unsupported uploads are refused and uploads without a course do not create one',async()=>{
  const a=await load();await a.upload('bad.exe');assert.equal(a.courses.length,0);assert.equal(a.uploads.length,0);await a.create();await a.upload('bad.exe');assert.equal(a.uploads.length,0);assert.match(a.get('notice-text').textContent,/Supported formats/);a.dom.window.close();
});
test('drag and drop uploads into the active course',async()=>{
  const a=await load();const c=await a.create();const e=new a.w.Event('drop',{cancelable:true});Object.defineProperty(e,'dataTransfer',{value:{files:[new a.w.File(['Notes'],'notes.txt')]}});a.get('workspace').dispatchEvent(e);await settle();assert.equal(c.materials.length,1);assert.match(a.uploads[0].url,new RegExp(c.id));a.dom.window.close();
});
test('suggestion chips prepare a course question without sending automatically',async()=>{
  const a=await load();await a.create();for(const chip of a.w.document.querySelectorAll('[data-prompt]')){chip.click();assert.equal(a.get('message').value,chip.dataset.prompt);assert.equal(a.w.document.activeElement,a.get('message'));}assert.equal(a.requests.filter(r=>r.url==='/api/chat').length,0);a.dom.window.close();
});
test('existing studies are offered explicitly; chosen import IDs go only into course creation',async()=>{
  const a=await load({legacy:{conversations:[{id:'a'.repeat(32),title:'Old math'}],materials:[{id:'b'.repeat(32),name:'Old notes.pdf',imported:false}]}});
  assert.equal(a.courses.length,0);assert.equal(a.get('recover-studies').hidden,false);a.get('recover-studies').click();assert.equal(a.get('legacy-options').open,true);a.get('course-name').value='Recovered Math';a.get('legacy-conversation').value='a'.repeat(32);a.get('legacy-materials').querySelector('input').checked=true;a.get('course-form').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();
  const body=JSON.parse(a.requests.find(r=>r.url==='/api/courses'&&r.method==='POST').body);assert.equal(body.legacy_conversation_id,'a'.repeat(32));assert.deepEqual(body.legacy_material_ids,['b'.repeat(32)]);a.dom.window.close();
});
test('validation rejects unsupported, empty and oversized uploads',()=>{
  for(const name of ['x.pdf','x.PPTX','x.docx','x.txt'])assert.equal(validateFile({name,size:10},100),'');
  for(const f of [{name:'x.exe',size:1},{name:'x.txt',size:0},{name:'x.txt',size:101}])assert.notEqual(validateFile(f,100),'');
});
test('request helper retains safe transport and HTTP/JSON errors',async()=>{
  const calls=[];await sendTurn('a'.repeat(32),' Explain ','b'.repeat(32),async(...args)=>calls.push(args));assert.deepEqual(calls[0],['/api/chat',{method:'POST',body:{course_id:'a'.repeat(32),message:'Explain',request_id:'b'.repeat(32)}}]);
  await assert.rejects(api('/test',{fetcher:async()=>({ok:false,json:async()=>({error:'Too large'})})}),/Too large/);await assert.rejects(api('/test',{fetcher:async()=>({json:async()=>{throw Error();}})}),/unreadable/);
});

test('successful course creation remains visible if a later list refresh fails',async()=>{
  const a=await load();const transport=a.w.fetch;a.w.fetch=async(url,options)=>{if(url==='/api/courses'&&(options?.method||'GET')==='GET')throw Error('Connection lost');return transport(url,options);};
  await a.create('Math');assert.equal(a.courses.length,1);assert.equal(a.get('course-dialog').open,false);assert.equal(a.get('course-title').textContent,'Math');assert.match(a.get('notice-text').textContent,/Connection lost/);assert.equal(a.w.document.querySelectorAll('.course-entry').length,1);a.dom.window.close();
});
test('confirmed deletion does not leave a phantom course after refresh failure',async()=>{
  const a=await load();await a.create('Math');const transport=a.w.fetch;a.w.fetch=async(url,options)=>{if(url==='/api/courses'&&(options?.method||'GET')==='GET')throw Error('Connection lost');return transport(url,options);};
  a.get('edit-course').click();a.get('delete-course').click();await settle();assert.equal(a.courses.length,0);assert.equal(a.get('course-dialog').open,false);assert.equal(a.get('empty-global').hidden,false);assert.equal(a.w.document.querySelectorAll('.course-entry').length,0);assert.match(a.get('notice-text').textContent,/Connection lost/);a.dom.window.close();
});
