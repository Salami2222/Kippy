const assert = require('node:assert/strict');
const {test} = require('node:test');
const {loadApp} = require('./helpers/app_harness.cjs');
const source = 'https://fdc.nal.usda.gov/food-details/748967/nutrients';
const total = {calories:148.9,protein_g:12.5,carbohydrates_g:1,fat_g:10};
const card = {name:'Eggs, Grade A, Large, egg whole',requested_name:'eggs',source:'USDA FoodData Central',
  source_url:source,dining_hall:'USDA common foods',menu_date:'2026-10-21',portion_confirmed:true,
  quantity:1.006,reported_quantity:2,unit:'each',serving_size:'100 g',grams:100.6,
  portion_description:'2 large eggs',match:'official name',nutrition:total};
const result = {kind:'result',message:'USDA reference estimate',cards:[card],total,result_id:'verified-eggs',nutrition_complete:true,
  notes:['Assuming 50.3 g per large egg.'],debug:{matches:[{source:card.source,requested:{name:'eggs'},selected:card,
    effective_date:card.menu_date,status:'exact USDA description'}],calculations:[{name:card.name,quantity:1.006,serving_size:'100 g',per_serving:{calories:148,protein_g:12.4,carbohydrates_g:0.96,fat_g:9.96},result:total}],warnings:[]}};
function text(node) { return [node.textContent,...node.children.map(text)].join(' '); }

test('source links allow only official UMD/USDA records and reject unsafe USDA variations', async () => {
  const app=await loadApp();
  assert.equal(app.run(`safeSource(${JSON.stringify(source)})`),source);
  const umd='https://nutrition.umd.edu/label.aspx?RecNumAndPort=123%2A1';
  assert.equal(app.run(`safeSource(${JSON.stringify(umd)})`),umd);
  for(const url of ['http://fdc.nal.usda.gov/food-details/123/nutrients',
    'https://fdc.nal.usda.gov.evil.test/food-details/123/nutrients','javascript:alert(1)',
    'https://fdc.nal.usda.gov/food-details/eggs/nutrients',source+'?api_key=private',
    'https://private@fdc.nal.usda.gov/food-details/123/nutrients',
    'https://fdc.nal.usda.gov:444/food-details/123/nutrients']) {
    assert.equal(app.run(`safeSource(${JSON.stringify(url)})`),null);
  }
});

test('restored USDA nutrition/debug cards show source, assumptions and verified links', async () => {
  const app=await loadApp();
  await app.loadHistory([{id:'a'.repeat(32),role:'assistant',reactions:{},payload:result}]);
  const rendered=text(app.get('chat-messages'));
  assert.match(rendered,/USDA source matching/);
  assert.match(rendered,/2 large eggs/);
  assert.match(rendered,/50.3 g/);
  assert.match(rendered,/0.96 g per serving/);
  assert.match(rendered,/9.96 g per serving/);
  assert.ok(!rendered.includes('Open official UMD label'));
  assert.ok(app.get('chat-messages').querySelectorAll('a').some(link=>link.href===source));
  assert.equal(app.get('chat-messages').querySelectorAll('img').length,0);
  assert.equal(app.slots.length,1);
});

test('USDA add-result uses the existing tracker API and is disabled after confirmed logging', async () => {
  const app=await loadApp();
  app.run(`appendResult(${JSON.stringify(result)})`);
  app.setStateHandler(async (url,payload)=> {
    assert.equal(url,'/api/tracker/add-result');
    assert.equal(payload.result_id,result.result_id);
    return {added:true,tracker:{date:card.menu_date,totals:total,goals:{calories:null,protein_g:null,carbohydrates_g:null,fat_g:null},entries:[],addedResultIds:[result.result_id]}};
  });
  const button=app.get('chat-messages').querySelector('.add-result-button');
  assert.equal(button.disabled,false);
  await button.emit('click');
  assert.equal(button.disabled,true);
  assert.equal(button.textContent,'Added to today ✓');
  assert.equal(app.get('tracker-protein').textContent,'12.5');
});
