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
    document:{documentElement:{dataset:{theme:'light'}},querySelector:s=>s==='#modal'?modal:null,getElementById(){return null;},
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
      reading_connections:{youtube:185,raindrop:76,publications:4},sources:[],editions:[],stats:{total:265},settings:{timezone:'Asia/Kolkata'}};
    var renders=0,refreshes=0;
    var originalRefresh=refresh;
    render=()=>{renders++};refresh=async()=>{refreshes++};
  `,context);
  return {run:code=>vm.runInContext(code,context),handlers};
}

test('library search does not remove archived origins from the expandable checkboxes',()=>{
  const w=workspace();
  w.run(`items=[{connection_id:'publications'}];filters={kind:'article',status:'saved',q:'investing'};`);
  const html=w.run('connectionFilter()');
  assert.match(html, /value="youtube"/);
  assert.match(html, /value="raindrop"/);
  assert.match(html, /value="publications"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /id="story-connection-options"[^>]* hidden/);
  assert.doesNotMatch(html, /<select/);
});

test('Today, Library and Knowledge share multiple selected connections',async()=>{
  const w=workspace();
  w.run(`setConnectionScope(['youtube','raindrop']);connectionFiltersExpanded=true;filters={kind:'video',status:'saved',q:'engineering'};`);
  await w.run(`navigation('knowledge')`);
  assert.match(w.run('KnowledgeGraph.page()'),/YouTube/);
  assert.match(w.run('KnowledgeGraph.page()'),/Raindrop/);
  assert.doesNotMatch(w.run('KnowledgeGraph.page()'),/id="graph-connection"/);
  await w.run(`navigation('today')`);
  await w.run(`navigation('library')`);
  assert.equal(w.run('connectionScope'),'raindrop,youtube');
  assert.equal(w.run('connectionFiltersExpanded'),true);
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
  await handler({target:{id:'story-connection-all',checked:false,dataset:{}}});
  await handler({target:{id:'story-connection-youtube',checked:true,dataset:{storyConnection:'youtube'}}});
  assert.equal(w.run('view'),'knowledge');
  assert.equal(w.run('knowledgeMode'),'concepts');
  assert.equal(w.run('connectionScope'),'youtube');
});

test('leaving Library before search finishes preserves the typed query',async()=>{
  const w=workspace();
  for(const handler of w.handlers.input) handler({target:{id:'search',value:'just typed',selectionStart:10}});
  await w.run(`navigation('knowledge')`);
  assert.equal(w.run('filters.q'),'just typed');
});

test('connection checkboxes combine readings, allow none, and restore all',()=>{
  const w=workspace();
  w.run(`items=[{id:'a',connection_id:'youtube'},{id:'b',connection_id:'raindrop'},{id:'c',connection_id:'publications'}];`);
  w.run(`toggleStoryConnection('publications',false)`);
  assert.equal(w.run(`connectionItems().map(i=>i.id).join(',')`),'a,b');
  w.run(`toggleStoryConnection('youtube',false);toggleStoryConnection('raindrop',false)`);
  assert.equal(w.run('connectionScope'),'none');
  assert.equal(w.run('connectionItems().length'),0);
  assert.match(w.run('storiesEmpty()'),/Choose at least one connection/);
  w.run(`toggleStoryConnection('raindrop',true);toggleStoryConnection('youtube',true);toggleStoryConnection('publications',true)`);
  assert.equal(w.run('connectionScope'),'all');
  assert.equal(w.run('connectionItems().length'),3);
});

test('Today is a publication-ordered list and Library has cards and reading tools',()=>{
  const w=workspace();
  w.run(`items=[{id:'old',title:'Older article',connection_id:'raindrop',takeaway:'Article preview',published:'2020-01-01'},
    {id:'new',title:'Newer video',connection_id:'youtube',takeaway:'Video preview',published:'2026-11-22'}];`);
  const today=w.run('todayPage()'), library=w.run('libraryPage()');
  assert.match(today,/The Daily Quib/);
  assert.match(today,/class="digest-list"/);
  assert.ok(today.indexOf('Newer video')<today.indexOf('Older article'));
  assert.doesNotMatch(today,/id="search"|data-filter=|reading-card/);
  assert.match(library,/class="library-grid"/);
  assert.match(library,/class="reading-card/);
  assert.match(library,/aria-label="Search your library"/);
  assert.match(library,/data-filter="kind" data-value="video"/);
  assert.match(library,/data-action="star"/);
  w.run(`libraryMode='editions'`);
  assert.match(w.run('libraryPage()'),/Import EPUB/);
  assert.match(w.run('libraryPage()'),/Prepare edition/);
});

test('Library search and bookmark filters never restrict Today',async()=>{
  const w=workspace();
  w.run(`var requested=[];var bootstrapData=state;applyTheme=()=>{};
    api=async path=>{if(path==='/bootstrap')return bootstrapData;requested.push(path);return {items:[],has_more:false};};
    filters={kind:'video',status:'saved',q:'engineering'};view='library';`);
  await w.run('originalRefresh(false)');
  let query=new URLSearchParams(w.run('requested.at(-1)').split('?')[1]);
  assert.equal(query.get('q'),'engineering');assert.equal(query.get('status'),'saved');
  w.run(`view='today'`);
  await w.run('originalRefresh(false)');
  query=new URLSearchParams(w.run('requested.at(-1)').split('?')[1]);
  assert.equal(query.get('q'),'');assert.equal(query.get('kind'),'all');assert.equal(query.get('status'),'all');
  assert.equal(w.run('filters.q'),'engineering');
});

test('Concepts retain only evidence from the checked connections, including none',()=>{
  const w=workspace();
  w.run(`knowledge={citations:[{id:'a',connection_id:'youtube'},{id:'b',connection_id:'raindrop'},{id:'c',connection_id:'publications'}],
    insights:[{id:'i',citation_ids:['a','b']},{id:'j',citation_ids:['c']}],
    concepts:[{id:'combined',insight_ids:['i']},{id:'excluded',insight_ids:['j']}],
    topics:[{id:'topic',concept_ids:['combined','excluded']}]};setConnectionScope(['youtube','raindrop']);`);
  assert.equal(w.run(`Array.from(knowledgeScope().citations.keys()).join(',')`),'a,b');
  assert.equal(w.run(`knowledgeScope().concepts.map(c=>c.id).join(',')`),'combined');
  w.run(`setConnectionScope('none')`);
  assert.equal(w.run('knowledgeScope().topics.length'),0);
  assert.equal(w.run('knowledgeScope().citations.size'),0);
});
