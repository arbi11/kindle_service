'use strict';

// All assets and graph data stay on the local app server. No external graph service.
const KnowledgeGraph = (() => {
  let network=null, nodeSet=null, edgeSet=null, data=null, sequence=0, selected=null, ready=false;
  let filters={from:'',to:''}, query='', cacheKey='', loadedAt=0, fontScale=null;
  const views=new Map();
  const types={connection:'Connection',topic:'Topic',concept:'Concept',insight:'Suggested insight',reading:'Reading'};
  const colors={connection:'#6c7865',topic:'#67805b',concept:'#7796a2',insight:'#b29a6c',reading:'#aab59e'};
  const connectionColors={youtube:'#ad8d84',raindrop:'#8199ad',freedium:'#9a98a0',substack:'#b8a17e',websites:'#7c9d94',other:'#aab59e'};
  const sourceName = id => state.catalogue?.find(c=>c.id===id)?.name || (id==='other'?'Other reading':id);
  const key = () => [connectionScope,filters.from,filters.to,state.stats.total].join('|');
  const dark = () => document.documentElement.dataset.theme==='dark';
  const nodeTheme = n => ({
    color:{background:n.type==='reading'?(connectionColors[n.connection_id]||colors.reading):colors[n.type],
      border:n.type==='reading'?(connectionColors[n.connection_id]||colors.reading):colors[n.type],
      highlight:{background:dark()?'#34432f':'#e4eadc',border:dark()?'#b5cca4':'#344f38'},
      hover:{background:dark()?'#34432f':'#e4eadc',border:dark()?'#b5cca4':'#344f38'}},
    font:{color:n.type==='topic'?'#fff':dark()?'#e1e7dc':'#40533a',strokeWidth:n.type==='topic'?0:3,strokeColor:dark()?'#1c211d':'#faf9f5'},
  });
  const edgeColor = e => ({color:e.relation==='collected'?(dark()?'#56634f':'#d8ded1'):(dark()?'#839776':'#9da993'),highlight:dark()?'#bdd8aa':'#557447',hover:dark()?'#bdd8aa':'#557447'});
  function themeChanged() {
    if(!network||!data) return;
    nodeSet.update(data.nodes.map(n=>{const theme=nodeTheme(n);return {id:n.id,color:theme.color,font:{...nodeSet.get(n.id).font,...theme.font}};}));
    edgeSet.update(data.edges.map(e=>({id:e.id,color:edgeColor(e)})));
    network.redraw();
  }

  function page() {
    return `<section class="knowledge-graph-page">
      <p class="knowledge-scope">${esc(connectionScopeLabel())}</p>
      <form id="graph-filters" class="graph-filters">
        <label>Published from<input id="graph-from" name="from" type="date" value="${esc(filters.from)}"></label>
        <label>Published through<input id="graph-to" name="to" type="date" value="${esc(filters.to)}"></label>
        <button class="btn secondary small" type="submit">Apply dates</button>
        <button class="text-btn" type="button" data-graph-action="reset">Reset dates</button>
      </form>
      <p class="graph-date-note">Publication dates saved in your library · ${esc(state.settings.timezone)} · Both dates included.</p>
      <div id="graph-status" class="graph-status" role="status" aria-live="polite">Loading your graph…</div>
      <p id="graph-coverage" class="graph-coverage"></p>
      <div class="graph-toolbar"><div class="graph-legend" aria-label="Graph legend">${Object.entries(types).map(([id,label])=>`<span><i style="background:${colors[id]}"></i>${label}</span>`).join('')}</div>
        <div class="graph-zoom"><button class="icon-btn" data-graph-action="zoom-out" aria-label="Zoom out" disabled>−</button><output id="graph-zoom-level" aria-label="Graph zoom">…</output><button class="icon-btn" data-graph-action="zoom-in" aria-label="Zoom in" disabled>+</button><button class="text-btn" data-graph-action="fit" disabled>Fit graph</button></div></div>
      <div class="graph-search"><label for="graph-search">Find a reading or idea</label><input type="search" id="graph-search" placeholder="Search this graph…" value="${esc(query)}" autocomplete="off"><div id="graph-search-results" class="graph-search-results" aria-live="polite"></div></div>
      <div class="graph-surface" id="graph-surface"><div id="graph-canvas" role="region" aria-label="Interactive knowledge graph. Drag nodes to arrange them, drag the background to pan, and scroll to zoom." tabindex="0"></div><div id="graph-empty" class="graph-empty" hidden></div><section id="graph-selection" class="graph-selection" aria-label="Selected graph node" aria-live="polite" hidden></section></div>
      <p class="graph-help">Drag a node to move it. Drag empty space to pan. Scroll to zoom. Select a node to inspect its sources.</p>
    </section>`;
  }

  function unmount() {
    sequence++;
    if(network) {
      if(ready) views.set(cacheKey,{positions:network.getPositions(),position:network.getViewPosition(),scale:network.getScale()});
      network.destroy();network=null;nodeSet=null;edgeSet=null;
      ready=false;
    }
  }

  async function mount(force=false) {
    const canvas=document.getElementById('graph-canvas');if(!canvas) return;
    const ticket=++sequence;
    try {
      if(force||!data||cacheKey!==key()||Date.now()-loadedAt>60000) {
        const result=await api('/knowledge/graph?'+new URLSearchParams({connection:connectionScope,from:filters.from,to:filters.to}));
        if(ticket!==sequence||!canvas.isConnected) return;
        data=result;cacheKey=key();loadedAt=Date.now();
      }
      if(ticket!==sequence||!canvas.isConnected) return;
      document.getElementById('graph-status').textContent=`${data.stats.readings} of ${data.stats.library} readings · ${data.stats.nodes} nodes · ${data.stats.edges} links`;
      document.getElementById('graph-coverage').textContent=`Mapped concepts: ${data.stats.mapped} reading${data.stats.mapped===1?'':'s'}. Source links only: ${data.stats.unmapped} reading${data.stats.unmapped===1?'':'s'}. Suggested insights are AI drafts.`+(data.warning?' '+data.warning:'');
      draw(canvas);search();
    } catch(error) {
      if(ticket!==sequence||!canvas.isConnected) return;
      data=null;cacheKey='';
      document.getElementById('graph-status').textContent=error.message;
      document.getElementById('graph-status').classList.add('graph-error');
      document.getElementById('graph-empty').hidden=false;
      document.getElementById('graph-empty').textContent='The graph could not be loaded. Check the filters and try again.';
    }
  }

  function draw(canvas) {
    if(network) {network.destroy();network=null;}
    ready=false;
    if(!data.nodes.length) {
      document.getElementById('graph-empty').hidden=false;
      document.getElementById('graph-empty').textContent=connectionScope==='none'?'Choose at least one connection in Filter stories.':'No readings match the selected connections and date range. Try a wider range or reset the dates.';
      document.getElementById('graph-selection').innerHTML='<p>No node selected.</p>';
      selected=null;return;
    }
    if(!window.vis?.Network) throw new Error('The local graph renderer could not load. Reopen Quib27 to try again.');
    const previous=views.get(cacheKey);
    const nodes=data.nodes.map(n=>{
      const tooltip=document.createElement('div');tooltip.textContent=types[n.type]+': '+n.label;
      const theme=nodeTheme(n);
      return {id:n.id,label:n.type==='reading'||n.type==='insight'?'':n.label.replace(/(.{18,30})\s/g,'$1\n'),
        title:tooltip,shape:n.type==='topic'?'box':'dot',size:n.type==='connection'?22:n.type==='concept'?15:n.type==='insight'?8:n.mapped?7:4,
        color:theme.color,
        font:{face:'Arial',size:n.type==='connection'?15:13,...theme.font},
        borderWidth:1,margin:n.type==='topic'?9:0,...(previous?.positions[n.id]||{})};
    });
    nodeSet=new vis.DataSet(nodes);
    fontScale=null;
    edgeSet=new vis.DataSet(data.edges.map(e=>({...e,title:e.relation==='collected'?'Collected through this connection':e.relation==='cites'?'Cites this saved reading':e.relation==='includes'?'Topic includes concept':'Concept has this perspective',
      width:e.relation==='collected'?.55:1.3,color:edgeColor(e),smooth:{type:'continuous',roundness:.15}})));
    network=new vis.Network(canvas,{nodes:nodeSet,edges:edgeSet},{
      autoResize:true,layout:{randomSeed:27,improvedLayout:false},
      interaction:{hover:true,tooltipDelay:180,keyboard:{enabled:true,bindToWindow:false},hideEdgesOnDrag:false},
      physics:{enabled:!previous,solver:'barnesHut',barnesHut:{gravitationalConstant:-6500,centralGravity:.12,springLength:120,springConstant:.035,damping:.3,avoidOverlap:.4},stabilization:{enabled:!previous,iterations:200,fit:false},minVelocity:1},
      nodes:{chosen:true},edges:{selectionWidth:1.5},manipulation:{enabled:false}});
    if(previous) network.moveTo({position:previous.position,scale:previous.scale,animation:false});
    const instance=network;
    const layoutReady=()=>{
      if(network!==instance)return;
      if(!previous) {network.setOptions({physics:false});fitGraph();}
      ready=true;canvas.dataset.layoutReady='true';
      document.querySelectorAll('.graph-zoom button').forEach(button=>button.disabled=false);
      zoomLabel();
    };
    network.once('stabilizationIterationsDone',layoutReady);
    network.on('zoom',zoomLabel);
    network.on('click',event=>{
      if(event.nodes.length) select(event.nodes[0],false);
      else if(event.edges.length) describeEdge(event.edges[0]);
      else {selected=null;network.unselectAll();describe();}
    });
    canvas.dataset.nodeCount=String(data.nodes.length);
    canvas.dataset.edgeCount=String(data.edges.length);
    if(selected&&data.nodes.some(n=>n.id===selected)) select(selected,false);else {selected=null;describe();}
    if(previous) layoutReady();
    zoomLabel();
  }

  function zoomLabel() {
    const output=document.getElementById('graph-zoom-level');
    if(output&&network) {
      const scale=network.getScale();output.textContent=Math.round(scale*100)+'%';
      if(fontScale===null||Math.abs(scale-fontScale)>.015) {
        fontScale=scale;
        nodeSet.update(data.nodes.filter(n=>!['reading','insight'].includes(n.type)).map(n=>({id:n.id,font:{...nodeSet.get(n.id).font,size:Math.min(70,Math.max(13,(n.type==='connection'?13:12)/scale))}})));
      }
    }
  }

  function fitGraph() {
    if(!network)return;
    network.fit({animation:false});
    // Leave room for labels, which stay readable while the node map shrinks.
    network.moveTo({scale:network.getScale()*.8,animation:false});
    zoomLabel();
  }

  function select(id,focus=true) {
    if(!network||!data.nodes.some(n=>n.id===id)) return;
    selected=id;network.selectNodes([id]);
    if(focus) network.focus(id,{scale:Math.max(network.getScale(),.85),animation:false});
    zoomLabel();describe();
  }

  function describeEdge(id) {
    const edge=data.edges.find(e=>e.id===id);if(!edge)return;
    const a=data.nodes.find(n=>n.id===edge.from),b=data.nodes.find(n=>n.id===edge.to);
    const relation={collected:'Collected through',includes:'Topic includes concept',perspective:'Concept has this perspective',cites:'Insight cites reading'}[edge.relation];
    const panel=document.getElementById('graph-selection');panel.hidden=false;selected=null;
    delete panel.dataset.selectedNode;
    panel.innerHTML=`<div class="small-label">${esc(relation)}</div><p>${esc(a.label)} → ${esc(b.label)}</p><button class="text-btn" data-graph-node="${esc(a.id)}">Inspect first node</button> · <button class="text-btn" data-graph-node="${esc(b.id)}">Inspect second node</button>`;
  }

  function describe() {
    const panel=document.getElementById('graph-selection');if(!panel)return;
    const node=data?.nodes.find(n=>n.id===selected);
    if(!node) {panel.hidden=true;delete panel.dataset.selectedNode;return;}
    panel.hidden=false;
    const adjacent=new Set(data.edges.flatMap(e=>e.from===node.id?[e.to]:e.to===node.id?[e.from]:[]));
    const neighbours=data.nodes.filter(n=>adjacent.has(n.id));
    let content=`<div class="small-label">${esc(types[node.type])}</div><h2>${esc(node.label)}</h2>`;
    if(node.type==='reading') {
      content+=`<p class="graph-reading-meta">${esc(sourceName(node.connection_id))} · ${esc(node.published_day||'Publication date unavailable')}</p><p>${node.mapped?'This reading supports mapped concepts. Follow its insight links to inspect the cited passages.':'This reading is linked to its connection. Its concepts have not been mapped yet.'}</p><button id="graph-read-${esc(node.item_id)}" class="btn secondary small" data-action="read" data-id="${esc(node.item_id)}">Open in reader ${icon('arrow')}</button>`;
    } else if(node.type==='insight') {
      const scope={citations:new Map(data.citations.map(c=>[c.id,c]))};
      content='<div class="small-label">Suggested insight · AI draft</div>'+knowledgeInsight(data.insights.find(i=>'insight:'+i.id===node.id),scope);
    } else {
      content+=`<p>${esc(node.description||'')}</p>`;
      if(node.type==='connection') content+=`<p>${neighbours.length} reading${neighbours.length===1?'':'s'} in this filtered graph.</p>`;
    }
    if(neighbours.length) content+=`<details class="graph-neighbours"><summary>${neighbours.length} connected node${neighbours.length===1?'':'s'}</summary><ul>${neighbours.map(n=>`<li><button class="text-btn" data-graph-node="${esc(n.id)}"><small>${esc(types[n.type])}</small> ${esc(n.label)}</button></li>`).join('')}</ul></details>`;
    panel.innerHTML=content;panel.dataset.selectedNode=node.id;
  }

  function search() {
    const results=document.getElementById('graph-search-results');if(!results)return;
    const input=document.getElementById('graph-search');query=input?.value||'';
    const terms=query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    if(!terms.length) {results.innerHTML='';return;}
    const matches=(data?.nodes||[]).filter(n=>terms.every(term=>(n.label+' '+(n.description||'')).toLocaleLowerCase().includes(term)));
    results.innerHTML=matches.length?`<div class="graph-search-count">${matches.length} matches</div>${matches.slice(0,10).map(n=>`<button data-graph-node="${esc(n.id)}"><small>${esc(types[n.type])}</small><span>${esc(n.label)}</span></button>`).join('')}`:'<p>No nodes match this search in the filtered graph.</p>';
  }

  function apply(next) {
    unmount();filters=next;selected=null;query='';
    render();
  }
  function scopeChanged() {unmount();selected=null;query='';}
  function readingQueue() {return (data?.nodes||[]).filter(n=>n.type==='reading').map(n=>items.find(i=>i.id===n.item_id)).filter(Boolean);}

  document.addEventListener('submit',event=>{
    if(event.target.id!=='graph-filters')return;event.preventDefault();
    const form=event.target;if(!form.reportValidity())return;
    const values=new FormData(form);apply({from:values.get('from'),to:values.get('to')});
  });
  document.addEventListener('input',event=>{if(event.target.id==='graph-search')search();});
  document.addEventListener('click',event=>{
    const target=event.target.closest('[data-graph-action],[data-graph-node]');if(!target)return;
    if(target.dataset.graphNode) {
      select(target.dataset.graphNode);query='';
      const searchInput=document.getElementById('graph-search');if(searchInput)searchInput.value='';
      const results=document.getElementById('graph-search-results');if(results)results.innerHTML='';
      return;
    }
    const action=target.dataset.graphAction;
    if(action==='reset') {apply({from:'',to:''});return;}
    if(!network)return;
    if(action==='fit') fitGraph();
    if(action==='zoom-in'||action==='zoom-out') {network.moveTo({scale:Math.min(5,Math.max(.04,network.getScale()*(action==='zoom-in'?1.3:1/1.3))),animation:false});zoomLabel();}
  });
  return {page,mount,unmount,themeChanged,scopeChanged,readingQueue,citation:id=>data?.citations.find(c=>c.id===id)};
})();
