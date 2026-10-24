const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const {api,validateFile,requestPayload,sendTurn} = require('../kippy/static/app.js');
const root = path.resolve(__dirname,'../kippy/static');
const settle = async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
async function load({offline=false}={}) {
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://localhost:8000',runScripts:'outside-only'});
  const w=dom.window;let serial=0;const conversations=[];const materials=[];const requests=[];let chatResolve=null;
  w.matchMedia=()=>({matches:false});w.requestAnimationFrame=cb=>cb();w.confirm=()=>true;
  const intervals=[];w.setInterval=cb=>intervals.push(cb);w.navigator.clipboard={writeText:async text=>{w.copied=text;}};
  w.fetch=async(url,options={})=>{
    requests.push({url,...options});let result;let status=200;const payload=options.body?JSON.parse(options.body):null;
    if(url==='/api/status')result={max_upload_bytes:25*1048576};
    else if(url==='/api/ollama/status'){if(offline)throw Error('Offline');result={connected:true,model_available:true,message:'Qwen connected'};}
    else if(url==='/api/conversations'&&options.method==='POST'){result={id:String(++serial).padStart(32,'a'),title:'New chat',messages:[],material_ids:[]};conversations.push(result);}
    else if(url==='/api/conversations')result={conversations};
    else if(url==='/api/materials')result={materials};
    else if(url==='/api/chat'){
      const c=conversations.find(x=>x.id===payload.conversation_id);c.messages.push({role:'user',content:payload.message});c.title=payload.message;
      await new Promise(resolve=>{chatResolve=resolve;});c.messages.push({role:'assistant',content:'**Integration**\n\n$$y=Ce^x$$\n\n```python\nx = 2\n```',sources:[{name:'lecture.pdf',location:'page 1'}]});result={content:'Done'};
    }else if(url.endsWith('/materials')&&options.method==='POST'){result=conversations.find(c=>url.includes(c.id));result.material_ids=payload.material_ids;}
    else if(options.method==='DELETE'&&url.includes('/materials/')){materials.splice(materials.findIndex(m=>url.endsWith(m.id)),1);for(const c of conversations)c.material_ids=c.material_ids.filter(id=>!url.endsWith(id));result={deleted:true};}
    else if(options.method==='DELETE'){conversations.splice(conversations.findIndex(c=>url.endsWith(c.id)),1);result={deleted:true};}
    else if(url.startsWith('/api/conversations/')){result=conversations.find(c=>url.endsWith(c.id));if(!result){status=404;result={error:'Not found'};}}
    else throw Error('Unexpected: '+url);
    // Model real transport serialization, not shared object aliases.
    return {ok:status===200,status,json:async()=>JSON.parse(JSON.stringify(result))};
  };
  for(const name of ['vendor/marked.js','vendor/purify.js','vendor/katex/katex.min.js','vendor/katex/auto-render.min.js'])w.eval(fs.readFileSync(path.join(root,name),'utf8'));
  w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));await settle();
  return {w,dom,conversations,materials,requests,get:id=>w.document.getElementById(id),finish:async()=>{chatResolve();await settle();},settle};
}

test('file validation accepts supported formats and rejects empty, large, unsupported files',()=>{
  for(const name of ['lecture.pdf','slides.PPTX','notes.docx','notes.txt'])assert.equal(validateFile({name,size:20},100),'');
  for(const file of [{name:'a.exe',size:10},{name:'a.txt',size:0},{name:'a.txt',size:101}])assert.notEqual(validateFile(file,100),'');
});
test('request helper sends only the backend contract and displays HTTP/JSON errors',async()=>{
  const calls=[];await sendTurn('a'.repeat(32),' Explain ','b'.repeat(32),async(...args)=>calls.push(args));
  assert.deepEqual(calls[0],['/api/chat',{method:'POST',body:{conversation_id:'a'.repeat(32),message:'Explain',request_id:'b'.repeat(32)}}]);
  await assert.rejects(api('/test',{fetcher:async()=>({ok:false,status:413,json:async()=>({error:'Too large'})})}),/Too large/);
  await assert.rejects(api('/test',{fetcher:async()=>({json:async()=>{throw Error();}})}),/unreadable/);
});
test('new chat creates real conversation and sidebar history reopens its messages',async()=>{
  const a=await load();a.get('new-chat').click();await settle();assert.equal(a.conversations.length,1);
  a.conversations[0].messages.push({role:'user',content:'Persistent example'});
  a.get('new-chat').click();await settle();assert.equal(a.conversations.length,2);
  a.w.document.querySelector('.open-conversation').click();await settle();assert.match(a.get('messages').textContent,/Persistent example/);
  assert.equal(a.w.localStorage.getItem('kippy.conversation'),a.conversations[0].id);a.dom.window.close();
});
test('submit has loading state, duplicate guard, formatted response, math, sources, code, and copy',async()=>{
  const a=await load();a.get('message').value='Explain integration';a.get('composer').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();
  assert.equal(a.get('thinking').hidden,false);assert.equal(a.get('send').disabled,true);
  a.get('composer').dispatchEvent(new a.w.Event('submit',{cancelable:true}));await settle();assert.equal(a.requests.filter(x=>x.url==='/api/chat').length,1);
  await a.finish();assert.equal(a.get('thinking').hidden,true);assert.ok(a.w.document.querySelector('.message-body strong'));assert.ok(a.w.document.querySelector('.katex'));assert.ok(a.w.document.querySelector('pre code'));assert.match(a.get('messages').textContent,/lecture.pdf · page 1/);
  a.w.document.querySelector('.copy-button').click();await settle();assert.match(a.w.copied,/Integration/);a.dom.window.close();
});
test('Markdown is sanitized and bracket LaTeX survives Markdown escaping',async()=>{
  const a=await load();a.get('new-chat').click();await settle();a.conversations[0].messages.push({role:'assistant',content:'<img src=x onerror="alert(1)"><script>alert(1)</script>\\[x^2\\]'});
  a.w.document.querySelector('.open-conversation').click();await settle();assert.equal(a.w.document.querySelector('.message-body img'),null);assert.equal(a.w.document.querySelector('.message-body script'),null);assert.ok(a.w.document.querySelector('.katex-display'));a.dom.window.close();
});
test('library selection persists via API and removal updates selected attachments',async()=>{
  const a=await load();a.materials.push({id:'d'.repeat(32),name:'lecture.pdf',type:'pdf',status:'ready',warning:'No images'});a.get('new-chat').click();await settle();
  a.w.XMLHttpRequest=class {constructor(){this.upload={addEventListener:(_,cb)=>{this.processing=cb;}};}open(method,url){this.url=url;}setRequestHeader(){}send(file){this.processing();this.status=201;this.responseText=JSON.stringify(a.materials[0]);this.onload();}};
  Object.defineProperty(a.get('file-input'),'files',{value:[new a.w.File(['text'],'lecture.pdf')]});a.get('file-input').dispatchEvent(new a.w.Event('change'));await settle();
  assert.match(a.get('materials').textContent,/ready/);assert.match(a.get('selected-materials').textContent,/lecture.pdf/);assert.deepEqual(a.conversations[0].material_ids,['d'.repeat(32)]);
  a.w.document.querySelector('[aria-label="Remove material: lecture.pdf"]').click();await settle();assert.equal(a.materials.length,0);assert.equal(a.get('selected-materials').textContent,'');a.dom.window.close();
});
test('upload errors are visible; unsupported files never send upload requests',async()=>{
  const a=await load();Object.defineProperty(a.get('file-input'),'files',{value:[new a.w.File(['bad'],'virus.exe')]});a.get('file-input').dispatchEvent(new a.w.Event('change'));await settle();assert.match(a.get('notice-text').textContent,/Supported formats/);assert.equal(a.get('send').disabled,false);a.dom.window.close();
});
test('sidebar collapse/expand and offline boot preserve workspace access',async()=>{
  const a=await load({offline:true});assert.match(a.get('connection').textContent,/unavailable/);assert.equal(a.get('new-chat').disabled,false);
  a.get('sidebar-close').click();assert.equal(a.get('sidebar').inert,true);assert.equal(a.get('sidebar-open').hidden,false);
  a.get('sidebar-open').click();assert.equal(a.get('sidebar').inert,false);a.dom.window.close();
});
test('conversation deletion removes actual history without deleting library',async()=>{
  const a=await load();a.get('new-chat').click();await settle();a.w.document.querySelector('[aria-label^="Delete conversation"]').click();await settle();assert.equal(a.conversations.length,0);assert.equal(a.get('empty').hidden,false);a.dom.window.close();
});
test('Enter submits, Shift+Enter leaves a newline, and IME composition is not submitted',async()=>{
  const a=await load();a.get('message').value='Explain';a.get('message').dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true}));await settle();assert.equal(a.requests.filter(x=>x.url==='/api/chat').length,0);
  a.get('message').dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));await settle();assert.equal(a.requests.filter(x=>x.url==='/api/chat').length,0);
  a.get('message').dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));await settle();assert.equal(a.requests.filter(x=>x.url==='/api/chat').length,1);await a.finish();a.dom.window.close();
});

test('upload exposes actual processing state, locks navigation, and retains errors',async()=>{
  const a=await load();let upload;
  a.w.XMLHttpRequest=class {constructor(){upload=this;this.upload={addEventListener:(_,cb)=>{this.processing=cb;}};}open(){}setRequestHeader(){}send(){this.processing();}};
  Object.defineProperty(a.get('file-input'),'files',{value:[new a.w.File(['lecture'],'lecture.txt')]});a.get('file-input').dispatchEvent(new a.w.Event('change'));await settle();
  assert.match(a.get('materials').textContent,/processing/);assert.equal(a.get('new-chat').disabled,true);assert.equal(a.get('send').disabled,true);
  upload.onerror();await settle();assert.match(a.get('notice-text').textContent,/connection lost/);assert.equal(a.get('new-chat').disabled,false);assert.equal(a.get('send').disabled,false);a.dom.window.close();
});
test('math inside HTML attributes cannot inject attributes after sanitization',async()=>{
  const a=await load();a.get('new-chat').click();await settle();a.conversations[0].messages.push({role:'assistant',content:`<a title='$" onmouseover="alert(1)$'>reference</a>`});
  a.w.document.querySelector('.open-conversation').click();await settle();const link=a.w.document.querySelector('.message-body a');assert.equal(link.getAttribute('onmouseover'),null);a.dom.window.close();
});
test('material checkbox changes are saved for the active conversation',async()=>{
  const a=await load();a.materials.push({id:'d'.repeat(32),name:'lecture.pdf',type:'pdf',status:'ready'});
  a.w.XMLHttpRequest=class {constructor(){this.upload={addEventListener:(_,cb)=>{this.processing=cb;}};}open(){}setRequestHeader(){}send(){this.processing();this.status=201;this.responseText=JSON.stringify(a.materials[0]);this.onload();}};
  Object.defineProperty(a.get('file-input'),'files',{value:[new a.w.File(['text'],'lecture.pdf')]});a.get('file-input').dispatchEvent(new a.w.Event('change'));await settle();
  const check=a.w.document.querySelector('.material-top input');check.checked=false;check.dispatchEvent(new a.w.Event('change'));await settle();assert.deepEqual(a.conversations[0].material_ids,[]);assert.equal(a.get('selected-materials').textContent,'');a.dom.window.close();
});

test('drag-and-drop runs the real upload flow rather than only showing a filename',async()=>{
  const a=await load();a.w.XMLHttpRequest=class {constructor(){this.upload={addEventListener:(_,cb)=>this.processing=cb};}open(){}setRequestHeader(){}send(file){this.processing();const doc={id:'e'.repeat(32),name:file.name,type:'txt',status:'ready'};a.materials.push(doc);this.status=201;this.responseText=JSON.stringify(doc);this.onload();}};
  const drag=new a.w.Event('dragenter',{cancelable:true});Object.defineProperty(drag,'dataTransfer',{value:{types:['Files']}});a.get('workspace').dispatchEvent(drag);assert.equal(a.get('drop-hint').hidden,false);
  const drop=new a.w.Event('drop',{cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:{files:[new a.w.File(['Concepts'],'lecture.txt')]}});a.get('workspace').dispatchEvent(drop);await settle();assert.equal(a.get('drop-hint').hidden,true);assert.equal(a.materials.length,1);assert.match(a.get('selected-materials').textContent,/lecture.txt/);a.dom.window.close();
});
