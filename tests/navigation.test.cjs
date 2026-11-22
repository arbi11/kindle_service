/* Application-state regressions. No browser, external requests or profile data. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function workspace() {
  const handlers={click:[],submit:[],change:[]};
  const modal={addEventListener(){}};
  const context=vm.createContext({
    document:{documentElement:{dataset:{theme:'light'}},querySelector:s=>s==='#modal'?modal:null,
      addEventListener:(name,handler)=>(handlers[name]||=[]).push(handler)},
    window:{addEventListener(){},scrollTo(){}},
    localStorage:{getItem(){return null;}}, URLSearchParams, Date, Map, Set,
    setTimeout,clearTimeout,console,
  });
  for(const name of ['knowledge-graph.js','app.js']) {
    const file=path.join(__dirname,'../desktop/ui',name);
    vm.runInContext(fs.readFileSync(file,'utf8').replace(/\nboot\(\);\s*$/, '\n'),context,{filename:name});
  }
  vm.runInContext(`
    state={catalogue:[{id:'youtube',name:'YouTube'},{id:'raindrop',name:'Raindrop'},{id:'publications',name:'Publications'}],
      reading_connections:{youtube:185,raindrop:76,publications:4},stats:{total:265},settings:{timezone:'Asia/Kolkata'}};
    var renders=0,refreshes=0;
    render=()=>{renders++};refresh=async()=>{refreshes++};
  `,context);
  return {run:code=>vm.runInContext(code,context),handlers};
}

test('reading search does not remove archived origins from the connection selector',()=>{
  const w=workspace();
  w.run(`items=[{connection_id:'publications'}];filters={kind:'article',status:'saved',q:'investing'};`);
  const html=w.run('connectionFilter()');
  assert.match(html, /value="youtube"/);
  assert.match(html, /value="raindrop"/);
  assert.match(html, /value="publications"/);
});

test('reading and graph use one connection selection across navigation',async()=>{
  const w=workspace();
  w.run(`setConnectionScope('youtube');filters={kind:'video',status:'saved',q:'engineering'};`);
  await w.run(`navigation('knowledge')`);
  assert.match(w.run('KnowledgeGraph.page()'),/YouTube/);
  assert.doesNotMatch(w.run('KnowledgeGraph.page()'),/id="graph-connection"/);
  await w.run(`navigation('reading')`);
  assert.equal(w.run('connectionScope'),'youtube');
  assert.equal(w.run('filters.q'),'engineering');
  assert.equal(w.run('filters.kind'),'video');
  assert.equal(w.run('filters.status'),'saved');
});

test('resetting graph dates preserves the selected connection',()=>{
  const w=workspace();
  w.run(`setConnectionScope('raindrop');`);
  w.handlers.click[0]({target:{closest:()=>({dataset:{graphAction:'reset'}})}});
  assert.equal(w.run('connectionScope'),'raindrop');
  assert.match(w.run('KnowledgeGraph.page()'),/Raindrop/);
});

test('delivery opens inside Settings and does not reset reading filters',async()=>{
  const w=workspace();
  w.run(`filters.q='retained reading search';`);
  await w.run(`navigation('delivery')`);
  assert.equal(w.run('view'),'settings');
  assert.equal(w.run('settingsSection'),'delivery');
  assert.equal(w.run('filters.q'),'retained reading search');
});

test('connection filter changes stay in the current knowledge view',async()=>{
  const w=workspace();
  w.run(`view='knowledge';knowledgeMode='concepts';`);
  const handler=w.handlers.change[0];
  await handler({target:{id:'story-connection',value:'youtube'}});
  assert.equal(w.run('view'),'knowledge');
  assert.equal(w.run('knowledgeMode'),'concepts');
  assert.equal(w.run('connectionScope'),'youtube');
});

test('leaving reading before search finishes preserves the typed query',async()=>{
  const w=workspace();
  for(const handler of w.handlers.input) handler({target:{id:'search',value:'just typed',selectionStart:10}});
  await w.run(`navigation('knowledge')`);
  assert.equal(w.run('filters.q'),'just typed');
});
