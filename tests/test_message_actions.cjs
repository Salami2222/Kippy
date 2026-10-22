const assert = require('node:assert/strict');
const {test} = require('node:test');
const {loadApp, deferred, settle} = require('./helpers/app_harness.cjs');

const userId = '1'.repeat(32), mellowId = '2'.repeat(32);
function history(approval = false) {
  return [
    {id: userId, role: 'user', payload: {message: 'What is in this meal?'}, reactions: {mellow: true}},
    {id: mellowId, role: 'assistant', payload: {message: 'Here is the verified meal.', kind: 'conversation',
      ...(approval ? {approval: {operation:'log',date:'2026-10-21',result_id:'result'}} : {})}, reactions: {}}
  ];
}
const message = (app, role) => app.get('chat-messages').querySelectorAll('.message').find(node => node.classList.contains(role));
const action = (app, role, name) => message(app, role).querySelector(`.message-action-${name}`);

test('each restored message gets exactly three outline actions, with role-specific wrappers and no badges', async () => {
  const app = await loadApp(); await app.loadHistory(history());
  for (const role of ['user','assistant']) {
    const node = message(app,role), toolbar = node.querySelector('.message-actions');
    assert.equal(node.parentElement.classList.contains(role), true);
    assert.equal(toolbar.querySelectorAll('button').length, 3);
    assert.equal(toolbar.querySelectorAll('svg').length, 3);
    assert.equal(node.tabIndex, 0);
    assert.equal(action(app,role,'like').getAttribute('aria-pressed'), 'false');
  }
  assert.equal(app.get('chat-messages').querySelectorAll('.reaction-badge').length, 0);
  assert.equal(app.get('chat-messages').querySelectorAll('.reaction-receipt').length, 0);
});

test('ordinary assistant and user feedback toggle through separate routes and survive history reload', async () => {
  const app = await loadApp(); await app.loadHistory(history());
  for (const [role,url] of [['assistant','/api/reaction'],['user','/api/message-feedback']]) {
    await action(app,role,'like').emit('click');
    assert.equal(action(app,role,'like').getAttribute('aria-pressed'), 'true');
    await app.run('loadConversation()');
    assert.equal(action(app,role,'like').getAttribute('aria-pressed'), 'true');
    await action(app,role,'like').emit('click');
    assert.equal(action(app,role,'like').getAttribute('aria-pressed'), 'false');
    const calls = app.requests.filter(request => request.url === url);
    assert.equal(calls.length,2);
    assert.deepEqual(calls.map(request => JSON.parse(request.options.body).active), [true,false]);
    assert.ok(calls.every(request => !JSON.parse(request.options.body).confirm_approval));
  }
  assert.equal(app.dialogs.length,0);
});

test('meal approval requires explicit confirmation; cancellation sends nothing and rapid clicks cannot duplicate it', async () => {
  const app = await loadApp(); await app.loadHistory(history(true));
  await action(app,'assistant','like').emit('click');
  assert.equal(app.requests.filter(request => request.url === '/api/reaction').length, 0);
  assert.match(app.dialogs[0], /2026-10-21/);
  assert.match(app.dialogs[0], /updates your tracker/);
  app.setConfirmed(true);
  const pending = deferred(); app.setStateHandler(() => pending.promise);
  const first = action(app,'assistant','like').emit('click');
  await settle();
  await action(app,'assistant','like').emit('click');
  assert.equal(app.requests.filter(request => request.url === '/api/reaction').length, 1);
  const payload = JSON.parse(app.requests.find(request => request.url === '/api/reaction').options.body);
  assert.equal(payload.confirm_approval,true); assert.equal(payload.message_id,mellowId);
  pending.resolve({active:true}); await first;
});

test('reply focuses composer, displays a bounded preview, sends only target ID and restores persisted quote', async () => {
  const app = await loadApp(); await app.loadHistory(history());
  await action(app,'assistant','reply').emit('click');
  assert.equal(app.get('reply-preview').hidden,false);
  assert.equal(app.get('reply-author').textContent,'Replying to Mellow');
  assert.equal(app.get('chat-input').value,'');
  assert.equal(app.get('chat-input').focused,true);
  const pending = app.enqueueChat();
  const send = app.run('sendMessage("Explain that serving")'); await settle();
  const payload = JSON.parse(app.requests.find(request => request.url === '/api/chat').options.body);
  assert.equal(payload.reply_to,mellowId); assert.equal(payload.message,'Explain that serving');
  assert.equal(Object.hasOwn(payload,'reply_reference'),false);
  pending.resolve({kind:'conversation',message:'Okay'}); await send;
  assert.equal(app.get('reply-preview').hidden,true);
  const entries=history(); entries.push({id:'3'.repeat(32),role:'user',payload:{message:'Explain that serving',reply_reference:{message_id:mellowId,role:'assistant',text:'Here is the verified meal.'}},reactions:{}});
  await app.loadHistory(entries);
  const quote=app.get('chat-messages').querySelector('.message-quote');
  assert.equal(quote.querySelector('strong').textContent,'Replying to Mellow');
  assert.equal(quote.querySelector('span').textContent,'Here is the verified meal.');
});

test('cancel, Escape and new-chat clear reply targeting without changing the input text', async () => {
  const app=await loadApp(); await app.loadHistory(history());
  for (const mode of ['cancel','escape','new-chat']) {
    if(mode === 'new-chat') await app.loadHistory(history());
    await action(app,'user','reply').emit('click');
    assert.equal(app.get('reply-author').textContent,'Replying to You');
    if(mode === 'cancel') await app.get('reply-cancel').emit('click');
    else if(mode === 'escape') await app.get('chat-input').emit('keydown',{key:'Escape'});
    else await app.get('new-chat').emit('click');
    assert.equal(app.get('reply-preview').hidden,true);
  }
});

test('copy uses message text, excludes debugging/actions, signals success then restores icon and handles denied clipboard', async () => {
  const app=await loadApp(); const entries=history();
  entries[1].payload.debug={user_request:'private debug content',response_time_ms:10};
  await app.loadHistory(entries);
  await action(app,'assistant','copy').emit('click');
  assert.equal(app.clipboard[0],'Here is the verified meal.');
  assert.equal(action(app,'assistant','copy').getAttribute('aria-label'),'Message copied');
  app.finishCopy();
  assert.equal(action(app,'assistant','copy').getAttribute('aria-label'),'Copy message');
  app.setClipboardError(new Error('Permission denied'));
  await action(app,'assistant','copy').emit('click');
  assert.equal(app.clipboard.length,1);
  assert.match(message(app,'assistant').querySelector('.message-action-status').textContent,/Could not copy/);
});

test('copied nutrition retains official source, missing values, and unconfirmed-portion warnings', async () => {
  const app=await loadApp();
  const value=app.run(`messagePlainText({message:'Please confirm your portion.',cards:[{
    name:'Rice',dining_hall:'South Campus Dining Hall',menu_date:'2026-10-21',serving_size:'3 oz',
    portion_confirmed:false,per_serving:{calories:90,protein_g:null,carbohydrates_g:20,fat_g:0},
    source_url:'https://nutrition.umd.edu/label.aspx?RecNumAndPort=1*1'
  }],notes:['Your portion has not been calculated.'],debug:{user_request:'Do not copy debugging'}})`);
  assert.match(value,/Portion unconfirmed/);
  assert.match(value,/per listed serving only/);
  assert.match(value,/Calories: 90 kcal/);
  assert.match(value,/Protein: Unavailable/);
  assert.match(value,/nutrition.umd.edu/);
  assert.match(value,/Your portion has not been calculated/);
  assert.doesNotMatch(value,/Do not copy debugging/);
});

test('touch taps reveal actions, scrolling and interactive controls do not; keyboard actions work', async () => {
  const app=await loadApp(); await app.loadHistory(history());
  const node=message(app,'assistant'), row=node.parentElement;
  await node.emit('pointerdown',{pointerType:'touch',clientX:20,clientY:20});
  await node.emit('pointerup',{pointerType:'touch',clientX:20,clientY:21});
  assert.equal(row.classList.contains('actions-open'),true);
  await node.emit('pointerdown',{pointerType:'touch',clientX:20,clientY:20});
  await node.emit('pointerup',{pointerType:'touch',clientX:20,clientY:80});
  assert.equal(row.classList.contains('actions-open'),true);
  await node.emit('pointerdown',{pointerType:'touch',clientX:20,clientY:20});
  await node.emit('pointercancel');
  await node.emit('pointerup',{pointerType:'touch',clientX:20,clientY:20});
  assert.equal(row.classList.contains('actions-open'),true);
  app.emitDocument('pointerdown',{target:app.get('chat-input')});
  assert.equal(row.classList.contains('actions-open'),false);
  await node.emit('pointerdown',{pointerType:'touch',clientX:20,clientY:20});
  await node.emit('pointerup',{pointerType:'touch',clientX:20,clientY:20});
  await action(app,'assistant','like').emit('click');
  assert.equal(message(app,'assistant').parentElement.classList.contains('actions-open'),true);
  const current=message(app,'assistant');
  await current.emit('keydown',{key:'Enter'});
  assert.equal(action(app,'assistant','like').focused,true);
  await action(app,'assistant','like').emit('click',{detail:0});
  assert.equal(action(app,'assistant','like').focused,true);
});
