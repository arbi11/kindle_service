'use strict';
const $ = (s, root = document) => root.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M9 21v-8h6v8"/>',
  book:'<path d="M12 6c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v15"/>',
  layers:'<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
  mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
  settings:'<path d="m10 3-.5 3-2 1-2.7-1.1-2 3.4L5 11v2l-2.2 1.7 2 3.4L7.5 17l2 1 .5 3h4l.5-3 2-1 2.7 1.1 2-3.4L19 13v-2l2.2-1.7-2-3.4L16.5 7l-2-1-.5-3z"/><circle cx="12" cy="12" r="3"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8.5 11.5 2.5 2.5 4.5-5"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  back:'<path d="M19 12H5m5-5-5 5 5 5"/>',
  external:'<path d="M14 3h7v7m0-7-11 11M10 3H4a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 2l1 4M4 12l1 4a8 8 0 0 0 13 2"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bookmark:'<path d="M6 3h12v18l-6-4-6 4z"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  video:'<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3z"/>',
  article:'<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7"/>',
  cloud:'<path d="M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5.5 5.5 0 0 1 0 11z"/>',
  kindle:'<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M8 6h8M8 9h8M8 12h5M11 18h2"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
  trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  leaf:'<path d="M20 3c-1 10-2 17-10 17a7 7 0 0 1-7-7C3 5 11 5 20 3ZM5 18 15 9"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
  moon:'<path d="M20 14A9 9 0 0 1 10 3a9 9 0 1 0 10 11Z"/>',
  type:'<path d="m3 19 6-14 6 14M5 14h8M17 11h6m-3 0v8"/>',
  folder:'<path d="M3 6V4h7l2 3h9v13H3z"/>',
  expand:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 3l6 6m12-6-6 6M3 21l6-6m12 6-6-6"/>',
  chevron:'<path d="m7 10 5 5 5-5"/>',
  rss:'<path d="M4 4a16 16 0 0 1 16 16M4 11a9 9 0 0 1 9 9"/><circle cx="5" cy="19" r="1"/>',
  graph:'<circle cx="5" cy="6" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="18" r="2"/><path d="m7 6 9-1M6 8l5 8m6-9-4 9"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">${icons[name] || icons.article}</svg>`;
let state = null, items = [], view = 'today', currentItem = null, currentEdition = null;
let filters = {kind:'all',status:'all',q:''}, readerTab = 'summary', readerTheme = document.documentElement.dataset.theme || 'light';
let readerSize = Number(localStorage.getItem('readerSize') || 20), lastView = 'today';
let refreshEpoch = 0;
let activeJob = null, knownJobIds = new Set(), searchTimer;
let connectionScope = 'all', readerReturnItemId = null, readerReturnScroll = 0;
let connectionFiltersExpanded = false;
let connectionsTab = 'yours';
let expandedConnection = null, connectionRailScroll = 0;
const connectionDrafts = new Map();
let raindropCollections = [];
let knowledge = null, libraryMode = 'stories', knowledgeMode = 'graph', settingsSection = 'preferences';
let knowledgeTopic = null, knowledgeConcept = null, knowledgeQuery = '';
const settingsDrafts = new Map();
let readerCitation = null, readerReturnFocus = null;
let themeSaving = false;
const dialog = $('#modal');

function themeButton(compact=false) {
  const dark=readerTheme==='dark';
  return `<button type="button" class="${compact?'icon-btn':'theme-toggle'}" data-action="toggle-theme" aria-label="Dark mode" aria-pressed="${dark}" title="Switch to ${dark?'light':'dark'} mode">${icon(dark?'sun':'moon')}${compact?'':`<span>Dark mode</span><small>${dark?'On':'Off'}</small>`}</button>`;
}
function applyTheme(theme) {
  const previous=document.documentElement.dataset.theme;
  readerTheme=['light','dark','sepia'].includes(theme)?theme:'light';
  document.documentElement.dataset.theme=readerTheme;
  $('meta[name="color-scheme"]').content=readerTheme==='dark'?'dark':'light';
  if(state) state.settings.reader_theme=readerTheme;
  if(settingsDrafts.has('settings-form')) settingsDrafts.get('settings-form').reader_theme=readerTheme;
  try {localStorage.setItem('readerTheme',readerTheme);} catch {}
  const shell=$('.reader-shell');
  if(shell) {shell.classList.remove('light','dark','sepia');shell.classList.add(readerTheme);}
  document.querySelectorAll('[data-action="toggle-theme"]').forEach(button=>{
    const dark=readerTheme==='dark';
    button.setAttribute('aria-pressed',String(dark));button.title=`Switch to ${dark?'light':'dark'} mode`;
    button.innerHTML=icon(dark?'sun':'moon')+(button.classList.contains('theme-toggle')?`<span>Dark mode</span><small>${dark?'On':'Off'}</small>`:'');
  });
  if($('#appearance-theme')) $('#appearance-theme').value=readerTheme;
  if(previous!==readerTheme) KnowledgeGraph.themeChanged();
}
async function setTheme(theme) {
  if(themeSaving) {applyTheme(readerTheme);return;}
  const previous=readerTheme;themeSaving=true;applyTheme(theme);
  try {const result=await api('/settings','POST',{settings:{reader_theme:readerTheme}});state.settings=result.settings;applyTheme(result.settings.reader_theme);}
  catch(error) {applyTheme(previous);throw error;}
  finally {themeSaving=false;}
}

async function api(path, method='GET', body) {
  const response = await fetch('/api' + path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-Daily-Edition':'1'}, ...(body !== undefined ? {body:JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'This action could not finish. Please try again.');
  return result;
}
function toast(message, error=false, editionId=null, itemId=null) {
  const el = document.createElement('div'); el.className = 'toast' + (error ? ' error':'');
  el.innerHTML = `${icon(error?'info':'check')}<div><p>${esc(message)}</p>${editionId?`<button class="toast-action" data-action="edition" data-id="${esc(editionId)}">Open edition ${icon('arrow')}</button>`:''}${itemId?`<button class="toast-action" data-action="read" data-id="${esc(itemId)}">Read article ${icon('arrow')}</button>`:''}</div><button aria-label="Dismiss">${icon('close')}</button>`;
  el.lastElementChild.onclick = () => el.remove(); $('#toasts').append(el);
  while($('#toasts').children.length>3) $('#toasts').firstElementChild.remove();
  setTimeout(() => el.remove(), error?15000:8500);
}
const minutes = words => Math.max(1, Math.round((words || 0)/220));
const shortDate = value => value ? new Date(value).toLocaleDateString(undefined,{month:'short',day:'numeric'}) : 'Not checked yet';
const timeOf = value => value ? new Date(value).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',timeZone:state?.settings.timezone}) : '';
const excerpt = item => item.takeaway || (new DOMParser()).parseFromString(item.summary_html || item.body_html,'text/html').body.textContent.slice(0,210);
const sample = item => item.is_sample ? '<span class="sample-tag">SAMPLE</span>' : '';

async function refresh(renderPage=true) {
  const epoch=++refreshEpoch;
  const query = {...(view==='library'&&libraryMode==='stories'?filters:{kind:'all',status:'all',q:''}), limit:200};
  const [data, library] = await Promise.all([api('/bootstrap'), api('/items?' + new URLSearchParams(query))]);
  const allItems = library.items;
  let page = library;
  while(page.has_more && epoch===refreshEpoch) {
    page = await api('/items?' + new URLSearchParams({...query, offset:page.next_offset}));
    allItems.push(...page.items);
  }
  const brain = data.knowledge_available ? await api('/knowledge').catch(error => ({available:true,error:error.message})) : null;
  if(epoch!==refreshEpoch) return;
  state = data; knowledge = brain; items = [...new Map(allItems.map(item=>[item.id,item])).values()];
  if(!themeSaving) applyTheme(state.settings.reader_theme);
  if (renderPage) render();
}
async function navigation(next) {
  clearTimeout(searchTimer);
  captureSettingsDraft();
  if(next==='delivery') {next='settings';settingsSection='delivery';}
  view=next;currentItem=null;currentEdition=null;
  if(next==='library') libraryMode='stories';
  window.scrollTo(0,0);
  try {await refresh();} catch(error) {toast(error.message,true);}
}
function connectionName(id) {
  return id==='all'?'All connections':state.catalogue?.find(c=>c.id===id)?.name || (id==='article'?'Saved articles':id==='other'?'Other reading':id);
}
function connectionChoices() {
  const ids=Object.keys(state.reading_connections || {});
  const ordered=(state.catalogue||[]).filter(c=>ids.includes(c.id)).map(c=>c.id);
  ordered.push(...ids.filter(id=>!ordered.includes(id)).sort());
  if(!['all','none'].includes(connectionScope)) for(const id of connectionScope.split(',')) if(!ordered.includes(id)) ordered.push(id);
  return ordered;
}
function matchesConnection(id) {
  return connectionScope==='all'||connectionScope!=='none'&&connectionScope.split(',').includes(id);
}
function connectionScopeLabel() {
  if(connectionScope==='all') return 'All connections';
  if(connectionScope==='none') return 'No connections selected';
  const ids=connectionScope.split(',');
  return ids.length>2?`${ids.length} connections`:ids.map(connectionName).join(', ');
}
function connectionFilter() {
  const ordered=connectionChoices();
  return `<section class="story-filters" aria-labelledby="story-filter-label"><h2 class="nav-label" id="story-filter-label">Filter stories</h2>
    <div class="connection-checklist" role="group" aria-label="Filter stories by connection">
      <div class="connection-filter-root"><label class="connection-check"><input id="story-connection-all" type="checkbox" ${connectionScope==='all'?'checked':''} aria-label="All connections"><span>All connections</span></label><button class="icon-btn connection-filter-toggle" type="button" data-action="toggle-story-connections" aria-expanded="${connectionFiltersExpanded}" aria-controls="story-connection-options" aria-label="${connectionFiltersExpanded?'Hide':'Show'} connections">${icon('chevron')}</button></div>
      <div id="story-connection-options" class="connection-filter-options" ${connectionFiltersExpanded?'':'hidden'}>${ordered.map(id=>`<label class="connection-check"><input id="story-connection-${esc(id)}" type="checkbox" data-story-connection="${esc(id)}" value="${esc(id)}" ${matchesConnection(id)?'checked':''}><span>${esc(connectionName(id))}</span></label>`).join('')||'<p class="hint">No collected stories yet.</p>'}</div>
    </div></section>`;
}
function setConnectionScope(id) {
  connectionScope=Array.isArray(id)?[...new Set(id)].sort().join(',')||'none':id;
  KnowledgeGraph.scopeChanged();
}
function toggleStoryConnection(id, checked) {
  const ids=connectionChoices(), selected=new Set(ids.filter(matchesConnection));
  if(checked) selected.add(id);else selected.delete(id);
  setConnectionScope(ids.every(id=>selected.has(id))?'all':[...selected]);
}
function syncConnectionCheckbox() {
  const checkbox=$('#story-connection-all');
  if(checkbox) checkbox.indeterminate=!['all','none'].includes(connectionScope);
}
function modeTabs(action,label,options,selected) {
  return `<div class="tabs section-tabs" role="group" aria-label="${label}">${options.map(([id,title])=>`<button type="button" data-action="${action}" data-value="${id}" class="${selected===id?'active':''}" aria-pressed="${selected===id}">${title}</button>`).join('')}</div>`;
}
function captureSettingsDraft() {
  const form=$('#settings-form')||$('#delivery-form');
  if(form) settingsDrafts.set(form.id,readForm(form));
}
function restoreSettingsDraft() {
  const form=$('#settings-form')||$('#delivery-form'), values=form&&settingsDrafts.get(form.id);
  if(values) for(const element of form.elements) if(element.name in values) {
    if(element.type==='checkbox') element.checked=values[element.name];else element.value=values[element.name];
  }
}
function render() {
  if (!state) return;
  if (currentItem) { renderReader(); return; }
  KnowledgeGraph.unmount();
  captureConnectionDraft();
  const pages = [['today','home','Today'],['library','book','Library'],['knowledge','graph','Knowledge']];
  const page = currentEdition ? editionPage() : ({today:todayPage,library:libraryPage,knowledge:knowledgePage,connections:connectionsPage,settings:settingsPage}[view] || todayPage)();
  $('#app').innerHTML = `
    <div class="shell digest-shell workspace-shell ${!currentEdition&&view==='knowledge'?'knowledge-shell':''}">
      <aside class="sidebar">
        <div class="digest-brand"><img class="brand-mark" src="/icon.svg" width="36" height="36" alt=""><span class="brand-name">Quib27</span></div>
        <nav class="nav primary-nav" aria-label="Main navigation">
          ${pages.map(([id,i,title]) => `<button data-nav="${id}" aria-label="${title}" ${id==='library'&&state.stats.unread?'aria-describedby="library-unread-count"':''} class="${view===id?'active':''}" ${view===id?'aria-current="page"':''}>${icon(i)}<span>${title}</span>${id==='library'&&state.stats.unread?`<span class="badge" id="library-unread-count" title="${state.stats.unread} unread ${state.stats.unread===1?'story':'stories'}">${state.stats.unread}</span>`:''}</button>`).join('')}
        </nav>
        ${!currentEdition&&(view==='today'||view==='knowledge'||view==='library'&&libraryMode==='stories')?connectionFilter():''}
        <div class="sidebar-foot sidebar-utilities">
          <button class="sidebar-settings ${view==='settings'?'active':''}" data-nav="settings" aria-label="Settings" ${view==='settings'?'aria-current="page"':''}>${icon('settings')}<span>Settings</span></button>
          ${themeButton(true)}
        </div>
      </aside>
      <div class="workspace"><main class="main" id="main-content">${page}</main>${connectionsRail()}</div>
    </div>`;
  restoreConnectionDraft();
  restoreSettingsDraft();
  syncConnectionCheckbox();
  updateTask();
  if(view==='knowledge'&&knowledgeMode==='graph'&&!currentEdition) KnowledgeGraph.mount();
}
function heading(title, subtitle, action='') { return `<div class="page-heading"><div><h1 class="page-title">${title}</h1>${subtitle?`<p class="page-subtitle">${subtitle}</p>`:''}</div>${action}</div>`; }
function empty(title, text, button='', i='book') { return `<div class="empty"><div class="empty-icon">${icon(i)}</div><h2>${title}</h2><p>${text}</p>${button}</div>`; }
function contentAttribution(item) {
  if(item.content_source==='public_copy' && item.content_url) {
    let host='public website';try {host=new URL(item.content_url).hostname;} catch {}
    return `<span>Public copy · ${esc(host)}</span><a class="reader-source" href="${esc(item.content_url)}" target="_blank" rel="noopener noreferrer">Open public copy ${icon('external')}</a>`;
  }
  return item.content_source==='freedium'?'<span>Text via Freedium</span>':item.content_source==='medium'?'<span>Text from Medium</span>':item.content_source==='subscriber'?'<span>Text via publication browser</span>':'';
}
function sourceIcon(kind) { return `<span class="source-icon ${kind}">${icon(kind==='video'?'video':'article')}</span>`; }
function itemRow(item,index=0) {
  return `<article class="article-row ${item.is_read?'is-read':''}" data-action="read" data-id="${item.id}" tabindex="0" role="button" aria-label="Read ${esc(item.title)}"><div class="article-no">${String(index+1).padStart(2,'0')}</div><div class="article-copy"><div class="article-meta">${sourceIcon(item.kind)}<span>${esc(item.author || (item.kind==='video'?'Video':'Article'))}</span><span>·</span><span>${minutes(item.words)} min read</span>${sample(item)}</div><h3>${esc(item.title)}</h3><div class="excerpt">${esc(excerpt(item))}</div></div><div class="row-end">${!item.is_read?'<i class="read-dot" title="Unread"></i>':''}<button class="icon-btn ${item.starred?'saved':''}" data-action="star" data-id="${item.id}" aria-label="${item.starred?'Unsave':'Save'} ${esc(item.title)}">${icon('bookmark')}</button></div></article>`;
}
function tabs() { return `<div class="tabs" aria-label="Filter reading">${[['all','Everything'],['article','Articles'],['video','Videos']].map(([id,text]) => `<button data-filter="kind" data-value="${id}" class="${filters.kind===id?'active':''}" aria-pressed="${filters.kind===id}">${text}</button>`).join('')}<button data-filter="status" data-value="${filters.status==='saved'?'all':'saved'}" class="${filters.status==='saved'?'active':''}" aria-pressed="${filters.status==='saved'}">${icon('bookmark')} Saved</button></div>`; }
function itemConnection(item) {
  if (item.connection_id) return item.connection_id;
  const source = state.sources.find(source => source.id === item.source_id);
  if (source) return source.kind;
  if (item.kind === 'video') return 'youtube';
  // Offline examples demonstrate the two connections without connecting an account.
  return item.is_sample ? 'raindrop' : 'other';
}
function connectionItems() { return items.filter(item => matchesConnection(itemConnection(item))); }
function digestItems() {
  return connectionItems()
    .sort((a,b) => (Date.parse(b.published) || Date.parse(b.created_at) || 0) - (Date.parse(a.published) || Date.parse(a.created_at) || 0));
}
function digestArticle(item) {
  const connection = itemConnection(item);
  const connectionName = state.catalogue?.find(c => c.id === connection)?.name;
  return `<article class="digest-article ${item.is_read?'is-read':''}" data-item-id="${esc(item.id)}">
    <header class="digest-article-header" data-action="read" data-id="${esc(item.id)}">
      <div class="article-meta">${sourceIcon(item.kind)}${connectionName?`<span class="connection-name">${connectionName}</span><span class="meta-divider">·</span>`:''}<span>${esc(item.author || 'Saved article')}</span><span class="meta-divider">·</span><time datetime="${esc(item.published)}">${shortDate(item.published)}</time>${sample(item)}</div>
      <h2><button class="digest-title" id="title-${esc(item.id)}" data-action="read" data-id="${esc(item.id)}" aria-label="Read ${esc(item.title)}" title="Open in reader"><span>${esc(item.title)}</span>${icon('arrow')}</button></h2>
      <p class="digest-excerpt">${esc(excerpt(item))}</p>
      <div class="digest-item-info"><span>${minutes(item.words)} min read</span><span>${item.kind==='video'?'Video summary':'Article'}</span>${item.is_read?'<span>Read</span>':''}</div>
    </header>
  </article>`;
}
function captureConnectionDraft() {
  const form = $('#rail-source-form');
  if (form) connectionDrafts.set(form.dataset.connectionId, readForm(form));
  const rail = $('.connection-rail');
  if (rail) connectionRailScroll = rail.scrollTop;
}
function restoreConnectionDraft() {
  const form = $('#rail-source-form'), values = form && connectionDrafts.get(form.dataset.connectionId);
  if (values) for (const element of form.elements) if (element.name in values) {
    if(element.type==='checkbox') element.checked=values[element.name];else element.value=values[element.name];
  }
  const rail = $('.connection-rail');
  if (rail) rail.scrollTop = connectionRailScroll;
}
const sourceNoun = kind => ({youtube:'channel',raindrop:'collection',substack:'publication',websites:'site',medium:'creator',publications:'publication'}[kind] || 'source');
const followLabel = kind => kind==='freedium'?'Import article':kind==='medium'?'Follow creators':kind==='article' ? 'Add article' : 'Follow '+sourceNoun(kind);
function freediumForm(prefix='rail') {
  return `<form id="${prefix==='rail'?'rail-source-form':'source-form'}" data-connection-id="freedium" data-source-kind="freedium">${sourceFields('freedium',prefix)}<button class="btn secondary small" type="submit">Import article</button><div class="form-feedback" role="status"></div></form>`;
}
function connectionPanel(connection) {
  const sources = state.sources.filter(source => source.kind === connection.id);
  const active = sources.filter(source => source.enabled).length;
  const open = expandedConnection === connection.id;
  const status = !sources.length ? (connection.id==='freedium'?'Ready to import':'Not connected') : !active ? 'Paused' : connection.setup==='feed' ? 'Connected' : `${active} ${sourceNoun(connection.id)}${active===1?'':'s'}`;
  return `<section class="rail-connection" data-rail-connection="${esc(connection.id)}">
    <h3><button id="connection-toggle-${esc(connection.id)}" class="connection-toggle" data-action="expand-connection" data-id="${esc(connection.id)}" aria-label="${esc(connection.name)} settings" aria-expanded="${open}" aria-controls="connection-options-${esc(connection.id)}">${icon(connection.icon)}<span><strong>${esc(connection.name)}</strong><small>${esc(status)}</small></span>${icon('chevron')}</button></h3>
    <div id="connection-options-${esc(connection.id)}" class="connection-options" role="region" aria-labelledby="connection-toggle-${esc(connection.id)}" ${open?'':'hidden'}>${open?`
      <p class="connection-description">${esc(connection.description)}</p>
      ${connection.id==='freedium'?freediumForm()+'<h4 class="optional-feed-label">Recently unlocked feed (optional)</h4>':''}
      ${sources.length?`<div class="rail-source-list">${sources.map(sourceRow).join('')}</div>`:''}
      ${connection.setup==='feed'?(sources.length?'':`<form id="rail-feed-form" data-connection-id="${esc(connection.id)}"><p class="hint">No account needed. Fetch articles when you are ready.</p><button class="btn secondary small" type="submit">Connect ${esc(connection.name)}</button><div class="form-feedback" role="status"></div></form>`):`
        <form id="rail-source-form" data-connection-id="${esc(connection.id)}" data-source-kind="${esc(connection.id)}">
          ${sources.length?`<h4>Add a ${sourceNoun(connection.id)}</h4>`:''}
          ${sourceFields(connection.id,'rail')}
          <button class="btn secondary small" type="submit">${followLabel(connection.id)}</button><div class="form-feedback" role="status"></div>
        </form>`}
    `:''}</div>
  </section>`;
}
function connectionsRail() {
  return `<aside class="connection-rail" aria-labelledby="connection-rail-title"><header class="connection-rail-heading"><h2 id="connection-rail-title">Connections</h2><p>Select one to view its settings.</p></header><div class="rail-connections">${(state.catalogue || []).map(connectionPanel).join('')}</div><button class="text-btn catalogue-link" data-nav="connections">Manage connections ${icon('arrow')}</button></aside>`;
}
function renderConnectionRail() {
  if (!$('.connection-rail')) return;
  captureConnectionDraft();$('.connection-rail').outerHTML=connectionsRail();restoreConnectionDraft();updateTask();
}
async function refreshConnections() {
  await refresh(false);
  if(view==='settings'&&!currentItem) {
    renderConnectionRail();
  } else render();
}
function todayPage() {
  const date=new Date().toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric',timeZone:state.settings.timezone});
  return `<div class="digest-layout"><header class="digest-heading"><div class="date">${icon('sun')} ${esc(date)}</div><h1>The Daily Quib</h1></header>
    <section class="digest-feed" aria-label="Stories">${storiesContext()}<div class="digest-list">${digestItems().map(digestArticle).join('')||storiesEmpty()}</div></section></div>`;
}
function libraryPage() {
  const modes=modeTabs('library-mode','Library views',[['stories','Stories'],['editions','Editions']],libraryMode);
  return heading('Your library','All your saved reading.')+`<div class="reading-view-bar">${modes}${libraryMode==='stories'?`<button class="text-btn" data-action="add-article">${icon('plus')} Add article</button>`:''}</div>`+(libraryMode==='editions'?editionsPage():libraryStories());
}
function storiesContext() {
  const count=digestItems().length;
  return `<div class="digest-toolbar"><div class="digest-context"><span>${esc(connectionScopeLabel())}</span><span class="meta-divider">·</span><span>${count} ${count===1?'story':'stories'}</span></div><span>Most recent</span></div>`;
}
function storiesEmpty() {
  const filtered=view==='library'&&(filters.q||filters.kind!=='all'||filters.status!=='all');
  const message=connectionScope==='none'?'Choose at least one connection in Filter stories.':filtered?'Try another search or reading filter.':connectionScope==='all'?'Articles from your connections will appear here.':'No stories from the selected connections yet.';
  return `<div class="digest-empty"><h2>No matching stories</h2><p>${message}</p></div>`;
}
function libraryStories() {
  const articles=digestItems();
  return `<section aria-label="Library stories"><div class="library-tools reading-tools">${tabs()}<div class="search-wrap">${icon('search')}<input id="search" placeholder="Search stories…" value="${esc(filters.q)}" aria-label="Search your library"></div></div>
    ${storiesContext()}${articles.length?`<div class="library-grid">${articles.map(libraryCard).join('')}</div>`:storiesEmpty()}</section>`;
}
function libraryCard(item) {
  return `<article class="reading-card ${item.is_read?'is-read':''}" data-item-id="${esc(item.id)}"><div class="article-meta">${sourceIcon(item.kind)}<span>${esc(item.author || connectionName(itemConnection(item)))}</span>${sample(item)}</div><h3><button class="card-title" id="title-${esc(item.id)}" data-action="read" data-id="${esc(item.id)}">${esc(item.title)}</button></h3><div class="excerpt">${esc(excerpt(item))}</div><div class="card-footer"><span>${minutes(item.words)} min read · ${shortDate(item.published)}</span><span class="read-label">${item.is_read?`${icon('check')} Read`:'Unread'}</span><button class="icon-btn ${item.starred?'saved':''}" data-action="star" data-id="${esc(item.id)}" aria-label="${item.starred?'Unsave':'Save'} ${esc(item.title)}">${icon('bookmark')}</button></div></article>`;
}
function editionsPage() {
  return `<section class="edition-archive reading-editions"><div class="section-heading"><div><h2>Your editions</h2><p class="section-note">Saved reading editions for the app, email or Kindle.</p></div><div class="inline-actions"><button class="btn secondary small" data-action="upload">${icon('download')} Import EPUB</button><button class="btn secondary small" data-action="prepare">${icon('plus')} Prepare edition</button></div></div>
    ${state.editions.map(e=>`<div class="archive-row"><div><h3>${esc(e.title)}</h3><p>${e.item_count} ${e.item_count===1?'piece':'pieces'} · ${minutes(e.words)} min read · ${shortDate(e.created_at)} ${e.is_sample?'· Sample':''}</p></div><div class="archive-actions"><button class="btn secondary small" data-action="edition" data-id="${e.id}">Open ${icon('arrow')}</button><a class="icon-btn" href="/api/editions/${e.id}/epub" aria-label="Export ${esc(e.title)}">${icon('download')}</a></div></div>`).join('')||'<p class="intro-line">Prepare an edition from your connections or import an EPUB.</p>'}</section>`;
}
function knowledgePage() {
  const options=[['graph','Graph'],['concepts','Concepts']];
  return heading('Knowledge','Explore your reading and the ideas it connects.')+modeTabs('knowledge-mode','Knowledge views',options,knowledgeMode)+(knowledgeMode==='graph'?KnowledgeGraph.page():conceptsPage());
}
function knowledgeScope() {
  const citations = new Map((knowledge.citations||[]).filter(c=>matchesConnection(c.connection_id)).map(c=>[c.id,c]));
  const insights = new Map((knowledge.insights||[]).filter(i=>i.citation_ids.some(id=>citations.has(id))).map(i=>[i.id,i]));
  const concepts = (knowledge.concepts||[]).filter(c=>c.insight_ids.some(id=>insights.has(id)));
  const topics = (knowledge.topics||[]).filter(t=>t.concept_ids.some(id=>concepts.some(c=>c.id===id)));
  return {citations,insights,concepts,topics};
}
function conceptsPage() {
  let result='';
  if(!knowledge?.available) return empty('No mapped concepts yet.','The graph still shows your saved readings. Concept browsing uses the existing local knowledge pilot.');
  if(knowledge.error) return result + empty('The knowledge pilot needs a moment.',esc(knowledge.error));
  const scoped = knowledgeScope();
  const topic = scoped.topics.find(t=>t.id===knowledgeTopic);
  const concept = scoped.concepts.find(c=>c.id===knowledgeConcept&&(!topic||topic.concept_ids.includes(c.id)));
  const connection = connectionScopeLabel();
  const covered = new Set(Array.from(scoped.citations.values()).map(c=>c.item_id)).size;
  const coverage = connectionScope==='all'?`Pilot: ${knowledge.stats.readings} of ${knowledge.stats.library} saved pieces`:`Showing ${covered} of ${knowledge.stats.readings} pilot pieces`;
  result += `<section class="knowledge-view"><div class="knowledge-intro"><h2>Concepts</h2><p>Explore topics, follow a concept, then check the passages behind each suggested insight.</p><p class="knowledge-coverage">${coverage} · ${esc(connection)}</p></div>
    <div class="knowledge-tools"><nav class="knowledge-breadcrumbs" aria-label="Knowledge location"><button data-action="knowledge-home">Topics</button>${topic?`<span>/</span><button data-action="knowledge-topic" data-id="${esc(topic.id)}">${esc(topic.title)}</button>`:''}${concept?`<span>/</span><span aria-current="page">${esc(concept.title)}</span>`:''}</nav><div class="search-wrap">${icon('search')}<input id="knowledge-search" aria-label="Search knowledge pilot" placeholder="Search this pilot…" value="${esc(knowledgeQuery)}"></div></div>
    <div id="knowledge-results">${knowledgeResults(scoped,topic,concept)}</div></section>`;
  return result;
}
function knowledgeResults(scoped,topic,concept) {
  if(knowledgeQuery.trim()) {
    const terms=knowledgeQuery.toLocaleLowerCase().trim().split(/\s+/);
    const matches=scoped.concepts.filter(c=>{
      const text=[c.title,c.description,...(knowledge.topics||[]).filter(t=>t.concept_ids.includes(c.id)).map(t=>t.title),...c.insight_ids.flatMap(id=>{
        const insight=scoped.insights.get(id);
        return insight?[insight.text,...insight.citation_ids.flatMap(cid=>{const citation=scoped.citations.get(cid);return citation?[citation.title,citation.quote]:[];})]:[];
      })].join(' ').toLocaleLowerCase();
      return terms.every(term=>text.includes(term));
    });
    return `<h3 class="knowledge-section-title">${matches.length} matching concept${matches.length===1?'':'s'}</h3>${matches.map(c=>knowledgeConceptRow(c,scoped)).join('')||'<p class="knowledge-empty">No concepts match. Search covers the selected pilot readings.</p>'}`;
  }
  if(concept) {
    const relatedTopics=scoped.topics.filter(t=>t.concept_ids.includes(concept.id));
    return `<header class="knowledge-detail-heading"><span class="small-label">Concept</span><h3>${esc(concept.title)}</h3><p>${esc(concept.description)}</p>${relatedTopics.length>1?`<div class="knowledge-shared">Appears in ${relatedTopics.map(t=>`<button class="text-btn" data-action="knowledge-topic" data-id="${esc(t.id)}">${esc(t.title)}</button>`).join('<span>·</span>')}</div>`:''}</header><p class="knowledge-draft-note">AI drafts for review. These describe source perspectives; they have not been independently verified or saved as your own conclusions.</p>${concept.insight_ids.filter(id=>scoped.insights.has(id)).map(id=>knowledgeInsight(scoped.insights.get(id),scoped)).join('')}`;
  }
  if(topic) return `<header class="knowledge-detail-heading"><span class="small-label">Topic</span><h3>${esc(topic.title)}</h3><p>${esc(topic.question)}</p></header><div class="knowledge-rows">${scoped.concepts.filter(c=>topic.concept_ids.includes(c.id)).map(c=>knowledgeConceptRow(c,scoped)).join('')}</div>`;
  return `<div class="knowledge-rows">${scoped.topics.map(t=>`<button class="knowledge-row knowledge-topic" data-action="knowledge-topic" data-id="${esc(t.id)}"><span><strong>${esc(t.title)}</strong><span>${esc(t.question)}</span><small>${scoped.concepts.filter(c=>t.concept_ids.includes(c.id)).length} concepts</small></span>${icon('arrow')}</button>`).join('')||'<p class="knowledge-empty">No mapped readings from the selected connections. Choose other connections to explore the pilot.</p>'}</div><p class="knowledge-footnote">${scoped.topics.length} topics · ${scoped.concepts.length} reusable concepts · ${scoped.insights.size} suggested insights. A concept can belong to more than one topic.</p>`;
}
function knowledgeConceptRow(concept,scoped) {
  const sources = new Set(concept.insight_ids.flatMap(id=>(scoped.insights.get(id)?.citation_ids||[]).flatMap(cid=>scoped.citations.has(cid)?[scoped.citations.get(cid).item_id]:[])));
  return `<button class="knowledge-row knowledge-concept" data-action="knowledge-concept" data-id="${esc(concept.id)}"><span><strong>${esc(concept.title)}</strong><span>${esc(concept.description)}</span><small>${sources.size} saved piece${sources.size===1?'':'s'}</small></span>${icon('arrow')}</button>`;
}
function knowledgeInsight(insight,scoped) {
  return `<article class="knowledge-insight" data-insight-id="${esc(insight.id)}"><div class="knowledge-insight-label">AI draft · Source perspective</div><p class="knowledge-insight-text">${esc(insight.text)}</p>${insight.citation_ids.filter(id=>scoped.citations.has(id)).map(id=>{
    const c=scoped.citations.get(id),connection=state.catalogue?.find(item=>item.id===c.connection_id)?.name||'Saved reading';
    return `<div class="knowledge-evidence"><div class="article-meta"><span>${esc(connection)}</span><span>·</span><span>${esc(c.material_label)}</span></div><h4>${esc(c.title)}</h4><blockquote>${esc(c.quote)}</blockquote>${c.content==='summary'?`<p class="knowledge-source-note">This passage comes from the saved summary.${!c.has_original?(c.kind==='video'?' An original transcript is not available here.':' The original article is not available here.'):''}</p>`:''}${c.available?`<button id="citation-${esc(c.id)}" class="text-btn" data-action="knowledge-source" data-id="${esc(c.id)}">Read this passage ${icon('arrow')}</button>`:`<p class="knowledge-source-note" role="status">${esc(c.reason)}</p>`}</div>`;
  }).join('')}</article>`;
}
function sourceRow(source) {
  return `<div class="connected-source" data-source-id="${esc(source.id)}">
    <div class="connected-source-info"><strong>${esc(source.title)}</strong><p>${source.item_count} ${source.item_count===1?'article':'articles'} · ${source.checked_at?'Checked '+shortDate(source.checked_at):'Not fetched yet'}${!source.enabled?' · Paused':''}</p>${source.kind==='publications'?`<p>${source.detail.preferences?esc(source.detail.preferences):'Recent articles · no preference filter'} · Up to ${source.detail.max_articles||5} per fetch</p><p>${source.detail.session_saved_at?'Session saved '+shortDate(source.detail.session_saved_at):'No saved login session'}</p>`:''}${source.error?`<p class="source-error">${esc(source.error)}</p>`:''}</div>
    <div class="connected-source-actions">
      ${source.kind==='publications'?`<button class="text-btn" data-action="publication-login" data-id="${esc(source.id)}">Sign in / reconnect</button><button class="text-btn" data-action="publication-edit" data-id="${esc(source.id)}">Preferences</button>`:''}
      <button class="text-btn" data-action="fetch-source" data-id="${esc(source.id)}" ${source.enabled?'':'disabled'} aria-label="Fetch articles from ${esc(source.title)}">${icon('refresh')} Fetch articles</button>
      <button class="toggle ${source.enabled?'on':''}" role="switch" aria-checked="${Boolean(source.enabled)}" aria-label="${source.enabled?'Pause':'Resume'} ${esc(source.title)}" data-action="toggle-source" data-id="${esc(source.id)}"></button>
      <button class="icon-btn" data-action="remove-source" data-id="${esc(source.id)}" aria-label="Disconnect ${esc(source.title)}">${icon('trash')}</button>
    </div>
  </div>`;
}
function connectedGroup(connection) {
  const sources = state.sources.filter(source => source.kind === connection.id);
  return `<article class="connection-group" data-connection-id="${esc(connection.id)}">
    <header class="connection-group-header"><div class="connection-identity">${icon(connection.icon)}<div><h2>${esc(connection.name)}</h2><p>${sources.length} ${sources.length===1?'source':'sources'} · ${sources.filter(s=>s.enabled).length} active</p></div></div>
      <div class="inline-actions"><button class="text-btn" data-connection="${esc(connection.id)}">Read articles ${icon('arrow')}</button>${connection.setup!=='feed'||connection.id==='freedium'?`<button class="btn secondary small" data-action="connection-setup" data-id="${esc(connection.id)}">${icon('plus')} ${connection.id==='freedium'?'Import article':'Add '+sourceNoun(connection.id)}</button>`:''}</div>
    </header>${sources.map(sourceRow).join('')}
  </article>`;
}
function catalogueCard(connection) {
  return `<article class="catalogue-card" data-catalogue-id="${esc(connection.id)}">
    <div class="catalogue-card-title"><span class="catalogue-icon">${icon(connection.icon)}</span><h2>${esc(connection.name)}</h2><span class="review-label">${icon('check')} Reviewed</span></div>
    <p class="catalogue-description">${esc(connection.description)}</p>
    <p class="catalogue-maintainer">Maintained by ${esc(connection.maintainer)}</p>
    <div class="catalogue-card-bottom"><span>${connection.source_count?'Connected':connection.id==='raindrop'?'Connect your account':connection.id==='publications'?'Local subscriber login':'No account needed'}</span><button class="btn secondary small" data-action="${connection.source_count?'manage-connection':'connection-setup'}" data-id="${esc(connection.id)}">${connection.source_count?'Manage':'Connect'}</button></div>
  </article>`;
}
function connectionsPage() {
  const catalogue = state.catalogue || [];
  const connected = catalogue.filter(connection => connection.source_count);
  const articles = state.sources.filter(source => source.kind==='article');
  return `<section class="connections-page">
    ${heading('Manage connections','Choose where your reading comes from.',connectionsTab==='yours'?'<button class="btn secondary small" data-action="connections-tab" data-value="browse">'+icon('plus')+' Add connection</button>':'')}
    <div class="connections-tabs" role="tablist" aria-label="Connections">
      <button role="tab" id="connections-yours-tab" aria-controls="connections-panel" aria-selected="${connectionsTab==='yours'}" tabindex="${connectionsTab==='yours'?'0':'-1'}" data-action="connections-tab" data-value="yours">Your connections</button>
      <button role="tab" id="connections-browse-tab" aria-controls="connections-panel" aria-selected="${connectionsTab==='browse'}" tabindex="${connectionsTab==='browse'?'0':'-1'}" data-action="connections-tab" data-value="browse">Browse connections</button>
    </div>
    <div id="connections-panel" role="tabpanel" aria-labelledby="connections-${connectionsTab}-tab">
      ${connectionsTab==='browse'?`<div class="catalogue-grid">${catalogue.map(catalogueCard).join('')}</div><p class="connections-note">Connections and updates are reviewed before they appear here. <a href="https://github.com/arbi11/kindle_service/tree/main/connections" target="_blank" rel="noopener noreferrer">Contribute a connection ${icon('external')}</a></p>`:
      `<div class="connected-groups">${connected.map(connectedGroup).join('')}${articles.length?`<article class="connection-group" data-connection-id="article"><header class="connection-group-header"><div class="connection-identity">${icon('article')}<h2>Saved articles</h2></div></header>${articles.map(sourceRow).join('')}</article>`:''}${!connected.length&&!articles.length?'<div class="connections-empty"><h2>No connections yet</h2><p>Choose Add connection to start collecting articles.</p></div>':''}</div>`}
    </div>
  </section>`;
}
function connectionSetup(id) {
  const connection = state.catalogue.find(c => c.id===id);
  if (!connection) return;
  if(id==='freedium') {
    modal(`<h2 id="modal-title">Read an article</h2><p class="description">Paste its original article or Freedium link. The article is fetched from Freedium and saved in your Quib27 library for reading here.</p>${freediumForm('source')}<p class="mini muted">Freedium is a third-party service and receives the article URL. Availability depends on that service.</p>`,'FREEDIUM');
    return;
  }
  if (connection.setup!=='feed') {showSourceModal(connection.id);return;}
  modal(`<h2 id="modal-title">Connect ${esc(connection.name)}</h2><p class="description">${esc(connection.description)}</p><div class="connection-access"><h3>What this connection accesses</h3><ul>${connection.permissions.map(permission=>`<li>${esc(permission)}</li>`).join('')}</ul></div><p class="description">Articles use your existing reading and summary settings. You can pause or disconnect this feed at any time.</p><form id="feed-connection-form" data-connection-id="${esc(connection.id)}"><div id="modal-feedback"></div><button class="btn full" type="submit">Connect ${esc(connection.name)}</button></form>`,'ADD CONNECTION');
}
function field(name,label,value,type='text',hint='',extra='',prefix='f') { return `<div class="field ${extra}"><label for="${prefix}-${name}">${label}</label><input id="${prefix}-${name}" name="${name}" type="${type}" value="${esc(value)}" ${type==='email'&&name.endsWith('_to')?'multiple':''} ${type==='number'?'inputmode="numeric"':''}>${hint?`<span class="hint">${hint}</span>`:''}</div>`; }
function check(name,label,description,checked) { return `<label class="checkbox-row"><input type="checkbox" name="${name}" ${checked?'checked':''}><span>${label}${description?`<small>${description}</small>`:''}</span></label>`; }
function credential(name,label,placeholder,hint='',prefix='f') { const saved = state.secrets.configured[name];return `<div class="field full"><label for="${prefix}-${name}">${label}</label><input autocomplete="new-password" id="${prefix}-${name}" name="${name}" type="password" placeholder="${saved?'Saved · leave blank to keep it':esc(placeholder)}"><span class="hint">${hint}</span>${saved?`<span class="key-state">${icon('lock')} Stored in your ${esc(state.secrets.storage)} <button class="text-btn" type="button" data-action="clear-secret" data-name="${name}">Remove</button></span>`:''}</div>`; }
function settingsRail() { return `<aside class="settings-rail"><h3 class="rail-heading">A library that belongs to you.</h3><p>Sources, articles and reading history stay on this device. There’s no Quib27 account to create.</p><ul class="privacy-list"><li>${icon('check')} Your own API key</li><li>${icon('check')} Direct provider connections</li><li>${icon('check')} No analytics or tracking</li><li>${icon('check')} Read offline once prepared</li></ul><p class="footnote">When you enable summaries, source text is sent directly to your chosen AI provider. Email delivery uses your chosen mail provider.<br><br>Credentials use your ${esc(state.secrets.storage)}.</p></aside>`; }
function settingsPage() {
  const tabs=modeTabs('settings-section','Settings sections',[['preferences','Preferences'],['delivery','Delivery'],['about','About']],settingsSection);
  return heading('Settings','')+tabs+({preferences:preferencesPage,delivery:deliveryPage,about:aboutPage}[settingsSection]||preferencesPage)();
}
function aboutPage() {
  return `<section class="form-card about-card"><h2>Quib27</h2><dl><dt>Version</dt><dd>v${esc(state.version)}</dd><dt>Library</dt><dd>Stored on this device</dd><dt>Location</dt><dd class="data-location">${esc(state.data_directory)}</dd></dl><p class="description">Articles, connections and reading history are stored locally. Enabled AI summaries send source text to your chosen model provider. Email and Kindle delivery use your configured mail provider.</p></section>
    ${state.legacy_available?`<section class="form-card"><h2>Import existing service</h2><p class="description">Copy the original laptop service’s settings, sources and recent editions. Automatic sending starts paused in Quib27.</p><button class="btn secondary small" data-action="import">Import existing service</button></section>`:''}`;
}
function preferencesPage() {
  const s = {...state.settings,...settingsDrafts.get('settings-form')};
  return `<div class="settings-layout"><form id="settings-form"><section class="form-card"><h2>Appearance</h2><div class="field"><label for="appearance-theme">Theme</label><select id="appearance-theme" name="reader_theme"><option value="light" ${s.reader_theme==='light'?'selected':''}>Light</option><option value="dark" ${s.reader_theme==='dark'?'selected':''}>Dark</option><option value="sepia" ${s.reader_theme==='sepia'?'selected':''}>Sepia reader</option></select><span class="hint">Dark mode applies to the whole app and the reader. Changes are saved immediately.</span></div></section><section class="form-card"><h2>AI connection</h2><p class="description">Bring your own key. Usage is billed by your provider; Quib27 is free.</p><div class="form-grid"><div class="field"><label for="f-provider">Provider</label><select id="f-provider" name="provider"><option value="openai" ${s.provider==='openai'?'selected':''}>OpenAI</option><option value="openrouter" ${s.provider==='openrouter'?'selected':''}>OpenRouter</option></select></div>${field('model','Model ID',s.model,'text','Use a model available in your provider account.')}${credential(s.provider+'_key','API key','Paste your '+(s.provider==='openai'?'OpenAI':'OpenRouter')+' key','Your key stays on this computer and is never returned to the interface.')}</div><div class="form-actions"><button class="btn secondary small" type="button" data-action="test-ai">${icon('check')} Check model</button><span class="hint">Makes one small API request.</span></div></section><section class="form-card"><h2>Reading preferences</h2><p class="description">Summaries give more room to the details you care about.</p><div class="form-grid"><div class="field full"><label for="f-interests">Your interests</label><textarea name="interests" id="f-interests" placeholder="For example: engineering, thoughtful investing, science, and the stories behind new ideas.">${esc(s.interests)}</textarea></div>${field('summary_words','Words per summary',s.summary_words,'number','150–2,000 words. Around 220 words is one minute of reading.')}${field('reading_minutes','Daily reading budget',s.reading_minutes,'number','Minutes of reading per edition.')}<div class="field full">${check('summarize_articles','Include AI summaries of saved articles','The full article stays available in the reader. Without a key, articles are saved in full.',s.summarize_articles)}</div></div></section><section class="form-card"><h2>Processing limits</h2><p class="description">Keep processing time and API usage predictable.</p><div class="form-grid">${field('max_items_per_run','Maximum new pieces per run',s.max_items_per_run,'number','1–20 pieces. Additional items wait for a later run.')}${field('lookback_days','Look back this many days',s.lookback_days,'number','1–30 days of recent uploads and bookmarks.')}<div class="field full">${check('audio_fallback','Allow cloud transcription when captions are unavailable','May download video audio and add transcription charges. Caption access can vary by video.',s.audio_fallback)}</div>${field('audio_minutes_per_video','Audio budget per run',s.audio_minutes_per_video,'number','Maximum cloud transcription minutes, also capped per video. Shared across the run.')}</div><div class="setting-info">API requests use the provider you selected. Failed summaries are reported beside their connection; the app does not switch to another paid provider.</div></section><div class="form-actions"><button class="btn" type="submit">Save preferences ${icon('check')}</button><span class="hint">Applies to the next edition.</span></div></form>${settingsRail()}</div>`;
}
function deliveryPage() {
  const s=state.settings;
  return `<div class="settings-layout"><form id="delivery-form"><section class="form-card"><h2>Where do you like to read?</h2><p class="description">Every edition is available here. Choose additional ways to receive it.</p><div class="form-grid"><div class="field full">${check('email_enabled','A personal newsletter','The entire edition, beautifully formatted in the email body.',s.email_enabled)}</div>${field('email_to','Newsletter email address',s.email_to,'email','Separate multiple addresses with commas.','full')}<div class="field full" style="margin-top:8px">${check('kindle_enabled','Send to Kindle','A distraction-free EPUB attachment for your Kindle library.',s.kindle_enabled)}</div>${field('kindle_to','Send-to-Kindle address',s.kindle_to,'text','Add your sender to Amazon’s Approved Personal Document E-mail List.','full')}</div></section><section class="form-card"><h2>Sent through your own email.</h2><p class="description">Use your mail provider’s SMTP details. For Gmail, use an App Password.</p><div class="form-grid">${field('smtp_host','SMTP server',s.smtp_host)}${field('smtp_port','Port',s.smtp_port,'number','465 for secure SMTP, or 587 with STARTTLS.')}${field('smtp_username','Email / username',s.smtp_username,'email')}${field('smtp_sender','Sender email',s.smtp_sender,'email','Leave blank to use your username.')}${credential('smtp_password','App password','Your mail provider’s app password','Encrypted transport is required for all delivery connections.')}</div><div class="form-actions"><button class="btn secondary small" type="button" data-action="test-smtp">${icon('check')} Check connection</button><span class="hint">Signs in without sending a message.</span></div></section><section class="form-card"><h2>A rhythm that fits your day.</h2><p class="description">The app prepares one edition after a missed slot when the laptop wakes up.</p><div class="form-grid">${field('timezone','Timezone',s.timezone,'text','For example: Asia/Kolkata or America/New_York.')}${field('delivery_times','Preparation times',s.delivery_times.join(', '),'text','24-hour times, separated by commas. Up to four.')}<div class="field full">${check('schedule_enabled','Prepare editions automatically','Keep Quib27 running in the background, with your laptop awake and connected.',s.schedule_enabled)}</div><div class="field full">${check('auto_send','Send each scheduled edition automatically','Use the email and Kindle destinations enabled above.',s.auto_send)}</div>${state.autostart_supported?`<div class="field full">${check('autostart','Launch at login','Starts in the system tray so your reading routine can continue.',state.autostart)}</div>`:''}</div><div class="setting-info">Closing the window keeps the app in the tray when a schedule is enabled. Choose Quit in the tray menu to stop it.</div></section><div class="form-actions"><button class="btn" type="submit">Save delivery settings ${icon('check')}</button></div></form><aside class="settings-rail"><h3 class="rail-heading">Read wherever feels right.</h3><p>Receive an edition in your inbox, or let your Kindle hold the reading for later. You can always open it here, too.</p><ul class="privacy-list"><li>${icon('check')} Full newsletter in the email</li><li>${icon('check')} EPUB attachment for Kindle</li><li>${icon('check')} Delivery history on this device</li><li>${icon('check')} No repeated successful sends</li></ul><p class="footnote">Your mail server confirms acceptance. Kindle processing and inbox delivery happen after that, through your email provider and Amazon.</p></aside></div><section class="form-card" style="margin-top:28px"><h2>Delivery history</h2><p class="description">A record of what your mail server accepted.</p>${state.deliveries.length?`<table class="delivery-history"><thead><tr><th>Edition</th><th>Destination</th><th>Status</th><th>When</th></tr></thead><tbody>${state.deliveries.map(d=>`<tr><td>${esc(d.edition_title || 'Edition')}</td><td class="target">${icon(d.channel==='kindle'?'kindle':'mail')} ${esc(d.target)}</td><td><span class="status-chip ${d.status==='sent'?'':'warn'}">${esc(d.status==='sent'?'Accepted by mail server':d.status)}</span>${d.message&&d.status!=='sent'?`<p class="history-error">${esc(d.message)}</p>`:''}${d.status==='uncertain'?`<div class="inline-actions" style="margin-top:9px"><button class="text-btn" data-action="resolve-delivery" data-id="${d.id}" data-value="sent">Received it</button><button class="text-btn" data-action="resolve-delivery" data-id="${d.id}" data-value="retry">Allow retry</button></div>`:''}</td><td>${shortDate(d.created_at)}<br>${timeOf(d.created_at)}</td></tr>`).join('')}</tbody></table>`:'<p class="intro-line">Your first delivery will appear here. Open an edition in Library to preview and send it.</p>'}</section>`;
}
function editionPage() {
  const e=currentEdition, words=e.items.reduce((sum,i)=>sum+i.words,0);
  return `<button class="text-btn" data-action="back-edition" style="margin-bottom:25px">${icon('back')} Back to editions</button>`+heading('An edition to settle into.','A few good things, gathered in one quiet place.')+`<section class="edition-listing"><div class="edition-summary"><div><div class="small-label" style="margin-bottom:11px">${e.is_sample?'Sample edition':shortDate(e.created_at)+' · Your daily edition'}</div><h2>${esc(e.title)}</h2><p>${e.items.length} pieces · About ${minutes(words)} minutes of reading</p></div><div class="edition-actions"><a class="btn secondary small" href="/api/editions/${e.id}/epub">${icon('download')} EPUB</a><a class="btn secondary small" href="/api/editions/${e.id}/html">HTML</a><button class="btn small" data-action="send" data-id="${e.id}">${icon('mail')} Preview & send</button></div></div><div class="article-list">${e.items.map(itemRow).join('')}</div></section>`;
}
async function openReader(id,citation=null) {
  const item = await api('/items/'+id);
  if(citation) {
    const body=citation.content==='summary'?item.summary_html:item.body_html;
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(body)))).map(n=>n.toString(16).padStart(2,'0')).join('');
    if(hash!==citation.content_hash) throw new Error('The saved text has changed. Reopen Knowledge to check this citation again.');
  }
  if (!currentItem) { readerReturnScroll = window.scrollY; readerReturnItemId = id; readerReturnFocus=document.activeElement?.id||null; }
  currentItem = item; readerCitation=citation; readerTab = citation?citation.content:currentItem.summary_html?'summary':'original'; lastView=view; renderReader();
  if(citation) $('.cited-passage')?.scrollIntoView({block:'center'}); else window.scrollTo(0,0);
}
function renderReader() {
  KnowledgeGraph.unmount();
  captureConnectionDraft();
  const item=currentItem, queue=readerCitation?[]:currentEdition?currentEdition.items:view==='knowledge'&&knowledgeMode==='graph'?KnowledgeGraph.readingQueue():digestItems();
  const position=queue.findIndex(i=>i.id===item.id), next=position>=0?queue[position+1]:null;
  $('#app').innerHTML=`<div class="reader-shell ${readerTheme}" style="--reader-size:${readerSize}px"><header class="reader-toolbar"><div class="left"><button class="icon-btn" data-action="reader-back" aria-label="Back to reading list">${icon('back')}</button><span class="reader-brand" title="Quib27"><img class="brand-mark" src="/icon.svg" width="26" height="26" alt="">Quib27</span><span class="reader-label">A little space to read.</span></div><div class="right">${sample(item)}<button class="icon-btn" data-action="font-smaller" aria-label="Smaller text">A−</button><button class="icon-btn" data-action="font-larger" aria-label="Larger text">A+</button>${themeButton(true)}<button class="icon-btn ${item.starred?'saved':''}" data-action="star" data-id="${item.id}" aria-label="${item.starred?'Unsave':'Save'} this piece">${icon('bookmark')}</button></div></header><div class="reader-progress" id="reading-progress"></div><main class="reader-content"><div class="article-meta">${sourceIcon(item.kind)} ${esc(item.author || 'Your reading')} <span>·</span> ${shortDate(item.published)}</div><h1>${esc(item.title)}</h1><div class="reading-info"><span>${icon('clock')} ${minutes(item.words)} min read</span><span>·</span><span>${item.kind==='video'?'Video summary':'Saved article'}</span>${contentAttribution(item)}${item.url?`<a class="reader-source" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">Open source ${icon('external')}</a>`:''}</div>${item.summary_html&&item.body_html?`<div class="reader-tabs"><button data-action="reader-tab" data-value="summary" class="${readerTab==='summary'?'active':''}">Summary</button><button data-action="reader-tab" data-value="original" class="${readerTab==='original'?'active':''}">${item.kind==='video'?'Transcript':'Full article'}</button></div>`:''}${item.selection_reason?`<p class="reader-selection">Selected for you: ${esc(item.selection_reason)}</p>`:''}${item.takeaway&&readerTab==='summary'?`<div class="takeaway"><span class="eyebrow">The thought to take with you</span>${esc(item.takeaway)}</div>`:''}<article class="prose">${readerTab==='summary'&&item.summary_html?item.summary_html:item.body_html}</article><footer class="reader-end"><p>A little more time, well spent.</p><div class="inline-actions"><button class="btn ${item.is_read?'secondary':''}" data-action="mark-read" data-id="${item.id}">${icon('check')} ${item.is_read?'Read':'Mark as read'}</button>${next?`<button class="icon-btn" data-action="read" data-id="${next.id}" aria-label="Read next piece">${icon('arrow')}</button>`:''}</div></footer></main></div>`;
  decorateCitedPassage();
  if(lastView==='knowledge') $('[data-action="reader-back"]').setAttribute('aria-label','Back to knowledge');
}
function decorateCitedPassage() {
  if(!readerCitation) return;
  $('[data-action="reader-back"]').setAttribute('aria-label','Back to knowledge');
  const note=document.createElement('p');note.className='reader-citation-note';
  note.textContent='From Knowledge · '+readerCitation.material_label;
  $('.prose').before(note);
  if(readerTab!==readerCitation.content) {note.textContent+=' · The cited passage is in the other text tab.';return;}
  const blocks=Array.from($('.prose').querySelectorAll('p,h1,h2,h3,h4,li,pre,blockquote,figcaption')).filter(n=>!n.querySelector('p,li,pre,blockquote')&&n.textContent.trim());
  const block=blocks[readerCitation.paragraph];
  if(block&&block.textContent.replace(/\s+/g,' ').trim().includes(readerCitation.quote)) {
    block.classList.add('cited-passage');block.dataset.citationId=readerCitation.id;
  } else note.textContent+=' · This passage could not be located. Please check the saved text.';
}
function modal(content, eyebrow='QUIB27') {
  dialog.innerHTML=`<div class="modal-head"><span class="eyebrow">${eyebrow}</span><button class="icon-btn" data-action="close-modal" aria-label="Close">${icon('close')}</button></div><div class="modal-body">${content}</div>`;
  if (!dialog.open) dialog.showModal();
}
function sourceFields(kind,prefix='source') {
  if(kind==='publications') return `${field('title','Name (optional)','','text','','',prefix)}<div class="field"><label for="${prefix}-url">Website, section or feed URL</label><input id="${prefix}-url" name="url" type="url" placeholder="https://your-publication.com/business" required><span class="hint">Choose a page that lists the articles you want to follow.</span></div><div class="field"><label for="${prefix}-preferences">What would you like to read?</label><textarea id="${prefix}-preferences" name="preferences" maxlength="3000" rows="3" placeholder="Economic policy and long-term investing. Skip daily stock tips."></textarea><span class="hint">Written preferences use your AI connection. Leave blank to collect recent articles without AI selection.</span></div>${field('max_articles','Articles per fetch',5,'number','1–10, within your overall preparation limit.','',prefix)}${check('freedium_fallback','Try Freedium if needed','Sends unreadable article URLs to Freedium. Availability varies.',true)}${check('search_fallback','Search for public copies if needed','Sends article titles to web search when the earlier retrieval options fail.',true)}<p class="hint">When OpenAI is configured, fetched article text is checked for text type and original-story match. After adding, use Sign in / reconnect if subscriber access is needed. The browser session stays on this laptop.</p>`;
  if(kind==='youtube') return `<div class="field"><label for="${prefix}-url">Channel URL or @handle</label><input id="${prefix}-url" name="url" placeholder="@YourFavouriteChannel" required><span class="hint">Follow recent uploads from this channel.</span></div>`;
  if(kind==='substack') return `<div class="field"><label for="${prefix}-url">Publication address</label><input id="${prefix}-url" name="url" placeholder="https://your-publication.substack.com" required><span class="hint">A publication’s own custom domain also works. Public posts only; no account or email signup needed.</span></div>`;
  if(kind==='websites') return `<div class="field"><label for="${prefix}-url">Website or feed address</label><input id="${prefix}-url" name="url" placeholder="https://your-favourite-blog.com" required><span class="hint">We look for an RSS or Atom feed. You can also paste the feed address directly.</span></div>`;
  if(kind==='medium') return `<div class="field"><label for="${prefix}-url">Creators to follow</label><textarea class="medium-creators" id="${prefix}-url" name="url" rows="3" maxlength="12000" placeholder="@creator\nhttps://medium.com/@another-creator" required></textarea><span class="hint">One @handle or Medium profile link per line, up to 20 at a time. New posts are collected when you fetch articles or your reading schedule runs.</span><span class="hint">Excerpts use Freedium automatically. Article URLs are sent to that service. No Medium login is needed.</span></div>`;
  if(kind==='freedium') return `<div class="field"><label for="${prefix}-url">Article URL</label><input id="${prefix}-url" name="url" type="url" placeholder="https://publication.com/article" required><span class="hint">Original publication and Freedium links are accepted. Availability depends on Freedium; complete articles are saved for offline reading.</span></div>`;
  if(kind==='article') return `<div class="field"><label for="${prefix}-url">Article URL</label><input id="${prefix}-url" name="url" type="url" placeholder="https://example.com/a-good-read" required><span class="hint">Publicly accessible articles work best. Sign-in and paywalls may prevent extraction.</span></div>`;
  return `${credential('raindrop_token','Raindrop token','Paste your personal test token',`Get a token from <a href="https://app.raindrop.io/settings/integrations" target="_blank" rel="noopener noreferrer">Raindrop’s integrations settings ↗</a>. It stays on this device.`,prefix)}
    <button class="text-btn check-raindrop" type="button" data-action="test-raindrop">${icon('check')} Check connection & load collections</button>
    <div class="field"><label for="${prefix}-collection">Collection</label><select name="collection_id" id="${prefix}-collection"><option value="0">All saved articles</option><option value="-1">Unsorted</option>${raindropCollections.map(c=>`<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('')}</select></div>
    ${field('collection_name','Display name','Saved articles','text','','',prefix)}
    ${field('tags','Filter by tags (optional)','','text','Comma-separated tags. A bookmark can match any one.','',prefix)}`;
}
function showSourceModal(kind='youtube') {
  const types=[...(state.catalogue || []).filter(c=>c.setup!=='feed').map(c=>[c.id,c.icon,c.name]),['article','article','Article']];
  modal(`<h2 id="modal-title">A few good sources.</h2><p class="description">Choose what you want to keep up with. You can pause or remove a source at any time.</p><div class="source-tabs">${types.map(([id,i,label])=>`<button data-action="source-tab" data-value="${id}" class="${id===kind?'active':''}">${icon(i)}${label}</button>`).join('')}</div><form id="source-form" data-source-kind="${kind}">${sourceFields(kind)}<div id="modal-feedback" class="form-feedback" role="status"></div><button class="btn full" type="submit">${icon('plus')} ${followLabel(kind)}</button></form>`,'FOLLOW WHAT MATTERS');
}
function setupModal() {
  modal(`<h2 id="modal-title">Make room for your kind of reading.</h2><p class="description">Start with your AI key and a few interests. Then choose your sources. You can read saved articles without an AI key, too.</p><form id="setup-form"><div class="field"><label for="setup-provider">AI provider</label><select id="setup-provider" name="provider"><option value="openai">OpenAI</option><option value="openrouter">OpenRouter</option></select></div><div class="field"><label for="setup-key">API key (optional for articles)</label><input id="setup-key" name="api_key" type="password" autocomplete="new-password" placeholder="Your own provider key"><span class="hint">AI usage is billed directly by your provider.</span></div><div class="field"><label for="setup-interests">What do you enjoy reading about?</label><textarea id="setup-interests" name="interests" placeholder="A few topics, in your own words…">${esc(state.settings.interests)}</textarea></div><div id="modal-feedback"></div><button class="btn full" type="submit">Choose your sources ${icon('arrow')}</button></form>${state.legacy_available?'<div style="text-align:center;margin-top:20px"><button class="text-btn" data-action="import">Or import your existing laptop setup</button></div>':''}`,'A LITTLE MORE YOU');
}
async function sendModal(id) {
  const edition=await api('/editions/'+id), targets=[];
  for(const channel of ['email','kindle']) if(state.settings[channel+'_enabled']) for(const address of state.settings[channel+'_to'].split(/[,;\n]/).map(x=>x.trim()).filter(Boolean)) targets.push([channel,address]);
  if (!targets.length) { toast('Add an email or Kindle destination in Settings → Delivery first.'); navigation('delivery'); return; }
  modal(`<h2 id="modal-title">Ready to send?</h2><p class="description">${esc(edition.title)}<br>${edition.items.length} pieces · About ${minutes(edition.items.reduce((a,i)=>a+i.words,0))} minutes to read.</p>${edition.is_sample?'<div class="modal-success">This is a sample edition. You can send it to try the delivery experience.</div>':''}<div class="small-label" style="margin-bottom:12px">Your destinations</div>${targets.map(([channel,address])=>`<div class="recipient">${icon(channel==='kindle'?'kindle':'mail')} ${esc(address)} <span class="mini muted">${channel==='kindle'?'EPUB':'Newsletter'}</span></div>`).join('')}<p class="description" style="margin-top:18px;margin-bottom:15px">Previously successful sends to these addresses will be skipped. Your mail server confirms acceptance; inbox and Kindle processing happen afterwards.</p><div class="inline-actions"><a class="text-btn" href="/api/editions/${id}/html">Download email preview ${icon('external')}</a><a class="text-btn" href="/api/editions/${id}/epub">Download EPUB ${icon('download')}</a></div><div class="modal-footer"><button class="btn secondary" data-action="close-modal">Keep reading</button><button class="btn" data-action="confirm-send" data-id="${id}">${icon('mail')} Send edition</button></div>`,'YOUR PERSONAL NEWSLETTER');
}
async function startTask(kind,payload={}) {
  const result=await api('/jobs','POST',{kind,payload}); activeJob=result.job_id;
  await refresh(false); updateTask(); return result;
}
function updateTask() {
  const job=state.jobs.find(j=>['queued','running'].includes(j.status));
  $('#task').innerHTML=job?`<div class="task-card"><p><span class="spinner"></span>${esc(job.message)}</p><div class="task-track"><span style="width:${Math.max(4,job.progress)}%"></span></div></div>`:'';
  document.querySelectorAll('[data-action="prepare"],[data-action="fetch-source"],[data-action="test-raindrop"],#rail-source-form [type="submit"]').forEach(button=>{
    const paused=button.dataset.action==='fetch-source'&&!state.sources.find(source=>source.id===button.dataset.id)?.enabled;
    button.disabled=Boolean(job)||paused;
  });
}
async function poll() {
  try {
    const next=await api('/bootstrap');
    const finished=next.jobs.filter(j=>['done','failed','interrupted'].includes(j.status)&&!knownJobIds.has(j.id));
    for(const job of next.jobs) if(['done','failed','interrupted'].includes(job.status)) knownJobIds.add(job.id);
    state=next; updateTask();
    for(const job of finished) {
      const result=JSON.parse(job.result||'{}');
      toast(job.message,job.status!=='done',result.edition_id,job.kind==='import_freedium'&&job.status==='done'?result.item_id:null);
      if(job.kind==='test_raindrop'&&job.status==='done') {
        raindropCollections=result.collections || [];
        document.querySelectorAll('select[name="collection_id"]').forEach(select=>{
          const chosen=select.value;
          select.innerHTML='<option value="0">All saved articles</option><option value="-1">Unsorted</option>'+raindropCollections.map(c=>`<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('');
          select.value=chosen;
        });
      }
    }
    if(finished.some(j=>['prepare','add_youtube','add_subscription','import_freedium','login_publication','import_legacy','import_epub','send'].includes(j.kind))&&!currentItem&&!dialog.open) await refreshConnections();
  } catch(e) { /* An app shutdown or a brief local interruption should not erase the reader. */ }
}
function readForm(form) { const values={}; for(const element of form.elements) if(element.name) values[element.name]=element.type==='checkbox'?element.checked:element.type==='number'?Number(element.value):element.value; return values; }
async function saveSettingsForm(form,quiet=false) {
  const values=readForm(form), credentials={};
  for(const name of ['openai_key','openrouter_key','smtp_password','raindrop_token']) if(name in values) {if(values[name]) credentials[name]=values[name];delete values[name];}
  const autostart=values.autostart; delete values.autostart;
  if(typeof values.delivery_times==='string') values.delivery_times=values.delivery_times.split(',').map(t=>t.trim()).filter(Boolean);
  const result=await api('/settings','POST',{settings:values,credentials});
  if(autostart!==undefined&&autostart!==state.autostart) await api('/autostart','POST',{enabled:autostart});
  settingsDrafts.delete(form.id);
  form.querySelectorAll('input[type="password"]').forEach(input=>{input.value='';});
  state.settings=result.settings;state.secrets=result.secrets;applyTheme(state.settings.reader_theme);
  if(!quiet) {toast('Your preferences are saved.');await refresh();}
}
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['source-form','rail-source-form','settings-form','delivery-form','setup-form','feed-connection-form','rail-feed-form'].includes(form.id)) return;
  event.preventDefault();const button=$('[type="submit"]',form);if(button) button.disabled=true;
  const feedback=$('.form-feedback',form);if(feedback) feedback.innerHTML='';
  try {
    if(['feed-connection-form','rail-feed-form'].includes(form.id)) {
      const result=await api('/sources','POST',{kind:form.dataset.connectionId});
      dialog.close();connectionsTab='yours';await refreshConnections();toast(result.message);
    }
    else if(['settings-form','delivery-form'].includes(form.id)) await saveSettingsForm(form);
    else if(form.id==='setup-form') {
      const values=readForm(form), credentials={};if(values.api_key) credentials[values.provider+'_key']=values.api_key;
      await api('/settings','POST',{settings:{provider:values.provider,model:values.provider==='openai'?'gpt-6-luna':'openai/gpt-6-luna',interests:values.interests,onboarding_done:true},credentials});
      dialog.close();await refresh(false);await navigation('connections');showSourceModal();
    } else {
      const values=readForm(form), kind=form.dataset.sourceKind;
      if(kind==='raindrop'&&values.raindrop_token) await api('/settings','POST',{credentials:{raindrop_token:values.raindrop_token}});
      const body=kind==='raindrop'?{kind,collection_id:Number(values.collection_id),title:values.collection_name||$('select[name="collection_id"] option:checked',form)?.textContent||'Saved articles',tags:values.tags.split(',').map(t=>t.trim()).filter(Boolean)}:{kind,url:values.url};
      if(kind==='publications') Object.assign(body,{title:values.title,preferences:values.preferences,max_articles:values.max_articles,freedium_fallback:values.freedium_fallback,search_fallback:values.search_fallback});
      const result=await api(form.dataset.sourceId?'/sources/'+form.dataset.sourceId:kind==='freedium'?'/freedium/import':'/sources',form.dataset.sourceId?'PATCH':'POST',body);dialog.close();connectionsTab='yours';
      form.reset();connectionDrafts.delete(kind);
      if(result.job_id) activeJob=result.job_id;else toast(result.message);
      await refreshConnections();updateTask();
    }
  } catch(e) {
    const feedback=$('.form-feedback',form)||$('#modal-feedback');if(feedback) feedback.innerHTML=`<div class="modal-error">${esc(e.message)}</div>`;else toast(e.message,true);
  } finally {if(button?.isConnected) button.disabled=false;updateTask();}
});
document.addEventListener('click',async event=>{
  const target=event.target.closest('[data-action],[data-nav],[data-filter],[data-connection]');if(!target) return;
  event.stopPropagation();
  try {
    if(target.dataset.connection) {
      setConnectionScope(target.dataset.connection);
      connectionFiltersExpanded=true;
      filters={kind:'all',status:'all',q:''};
      await navigation('today');
      return;
    }
    if(target.dataset.nav) {await navigation(target.dataset.nav);return;}
    if(target.dataset.filter) {filters[target.dataset.filter]=target.dataset.value;await refresh();return;}
    const a=target.dataset.action,id=target.dataset.id;
    if(a==='close-modal') dialog.close();
    else if(a==='library-mode') {libraryMode=target.dataset.value==='editions'?'editions':'stories';await refresh();}
    else if(a==='toggle-story-connections') {
      connectionFiltersExpanded=!connectionFiltersExpanded;
      $('#story-connection-options').hidden=!connectionFiltersExpanded;
      target.setAttribute('aria-expanded',String(connectionFiltersExpanded));
      target.setAttribute('aria-label',`${connectionFiltersExpanded?'Hide':'Show'} connections`);
    }
    else if(a==='knowledge-mode') {knowledgeMode=target.dataset.value==='concepts'?'concepts':'graph';render();}
    else if(a==='settings-section') {captureSettingsDraft();settingsSection=target.dataset.value;render();}
    else if(a==='knowledge-home') {knowledgeTopic=null;knowledgeConcept=null;knowledgeQuery='';render();}
    else if(a==='knowledge-topic') {knowledgeTopic=id;knowledgeConcept=null;knowledgeQuery='';render();window.scrollTo(0,0);}
    else if(a==='knowledge-concept') {
      const parents=knowledge.topics.filter(t=>t.concept_ids.includes(id));
      if(!parents.some(t=>t.id===knowledgeTopic)) knowledgeTopic=parents[0]?.id||null;
      knowledgeConcept=id;knowledgeQuery='';render();window.scrollTo(0,0);
    }
    else if(a==='knowledge-source') {const citation=knowledgeMode==='graph'?KnowledgeGraph.citation(id):knowledge?.citations.find(c=>c.id===id);if(citation?.available) await openReader(citation.item_id,citation);}
    else if(a==='expand-connection') {
      captureConnectionDraft();expandedConnection=expandedConnection===id?null:id;
      renderConnectionRail();
      document.getElementById('connection-toggle-'+id)?.focus({preventScroll:true});
    }
    else if(a==='connections-tab') {connectionsTab=target.dataset.value;render();$('#connections-'+connectionsTab+'-tab')?.focus({preventScroll:true});}
    else if(a==='connection-setup') connectionSetup(id);
    else if(a==='manage-connection') {connectionsTab='yours';render();document.querySelector('.connection-group[data-connection-id="'+id+'"]')?.scrollIntoView({block:'nearest'});}
    else if(a==='add-article') modal(`<h2 id="modal-title">Add article</h2><form id="source-form" data-source-kind="article">${sourceFields('article')}<button class="btn full" type="submit">Add article</button><div class="form-feedback" role="status"></div></form>`);
    else if(a==='publication-login') {const result=await api('/publications/'+id+'/login','POST',{});activeJob=result.job_id;await refresh(false);updateTask();}
    else if(a==='publication-edit') {
      const source=state.sources.find(s=>s.id===id);
      modal(`<h2 id="modal-title">${esc(source.title)} preferences</h2><form id="source-form" data-source-kind="publications" data-source-id="${id}">${sourceFields('publications','edit-publication')}<button class="btn full" type="submit">Save preferences</button><div class="form-feedback" role="status"></div></form>`);
      const values={title:source.title,url:source.url,...source.detail};
      for(const element of $('#source-form').elements) if(element.name in values) {if(element.type==='checkbox') element.checked=values[element.name];else element.value=values[element.name];}
    }
    else if(a==='fetch-source') {await startTask('prepare',{source_id:id});updateTask();}
    else if(a==='prepare') {if(!state.sources.some(s=>s.enabled)) {await navigation('connections');showSourceModal();}else await startTask('prepare');}
    else if(a==='add-source') showSourceModal();
    else if(a==='connect-raindrop') showSourceModal('raindrop');
    else if(a==='source-tab') showSourceModal(target.dataset.value);
    else if(a==='setup') setupModal();
    else if(a==='import') {modal(`<h2 id="modal-title">Bring your existing setup along.</h2><p class="description">Copy your laptop service’s sources, AI and email settings, delivery history identifiers, and up to eight recent editions into Quib27.</p><div class="setting-info">Your existing service keeps running. Automatic preparation and sending start paused in this app. Enable them in Delivery when you are ready to switch.</div><div class="modal-footer"><button class="btn secondary" data-action="close-modal">Keep exploring</button><button class="btn" data-action="confirm-import">Import setup ${icon('arrow')}</button></div>`);}
    else if(a==='confirm-import') {dialog.close();await startTask('import_legacy');}
    else if(a==='upload') $('#epub-file').click();
    else if(a==='read') await openReader(id);
    else if(a==='edition') {captureSettingsDraft();currentEdition=await api('/editions/'+id);currentItem=null;view='library';libraryMode='editions';render();window.scrollTo(0,0);}
    else if(a==='back-edition') {currentEdition=null;render();}
    else if(a==='reader-back') {if(currentEdition)currentEdition=await api('/editions/'+currentEdition.id);currentItem=null;readerCitation=null;view=lastView;await refresh();window.scrollTo(0,readerReturnScroll);(document.getElementById(readerReturnFocus)||document.getElementById('title-'+readerReturnItemId)||document.querySelector('[data-action="read"][data-id="'+CSS.escape(readerReturnItemId)+'"]'))?.focus({preventScroll:true});}
    else if(a==='reader-tab') {readerTab=target.dataset.value;renderReader();}
    else if(a==='toggle-theme') await setTheme(readerTheme==='dark'?'light':'dark');
    else if(a==='font-smaller'||a==='font-larger') {readerSize=Math.min(28,Math.max(16,readerSize+(a==='font-larger'?1:-1)));localStorage.setItem('readerSize',readerSize);await api('/settings','POST',{settings:{reader_size:readerSize}});$('.reader-shell').style.setProperty('--reader-size',readerSize+'px');}
    else if(a==='star') {const item=currentItem?.id===id?currentItem:items.find(i=>i.id===id)||currentEdition?.items.find(i=>i.id===id);if(item){const value=!item.starred;await api('/items/'+id,'PATCH',{starred:value});item.starred=value?1:0;if(currentItem) {const scroll=window.scrollY;renderReader();window.scrollTo(0,scroll);}else await refresh();}}
    else if(a==='mark-read') {await api('/items/'+id,'PATCH',{is_read:true});currentItem.is_read=1;target.innerHTML=icon('check')+' Read';target.classList.add('secondary');toast('Marked as read.');}
    else if(a==='toggle-source') {const source=state.sources.find(s=>s.id===id);await api('/sources/'+id,'PATCH',{enabled:!source.enabled});await refreshConnections();}
    else if(a==='remove-source') {const source=state.sources.find(s=>s.id===id);modal(`<h2 id="modal-title">Stop following this source?</h2><p class="description">${esc(source.title)}<br>Reading already in your library stays available.</p><div class="modal-footer"><button class="btn secondary" data-action="close-modal">Keep following</button><button class="btn danger" data-action="confirm-remove-source" data-id="${id}">Remove source</button></div>`);}
    else if(a==='confirm-remove-source') {await api('/sources/'+id,'DELETE',{});dialog.close();await refreshConnections();toast('Source removed. Your saved reading stays in the library.');}
    else if(a==='send') await sendModal(id);
    else if(a==='resolve-delivery') {await api('/deliveries/'+id+'/resolve','POST',{resolution:target.dataset.value});await refresh();toast(target.dataset.value==='sent'?'Receipt confirmed.':'Retry allowed. Check your mailbox before sending this edition again.');}
    else if(a==='confirm-send') {dialog.close();await startTask('send',{edition_id:id});}
    else if(a==='test-ai') {await saveSettingsForm($('#settings-form'),true);await startTask('test_ai');}
    else if(a==='test-smtp') {await saveSettingsForm($('#delivery-form'),true);await startTask('test_smtp');}
    else if(a==='test-raindrop') {
      const form=target.closest('form'), input=$('input[name="raindrop_token"]',form);
      const feedback=$('.form-feedback',form);if(feedback) feedback.innerHTML='';
      if(input?.value) {
        await api('/settings','POST',{credentials:{raindrop_token:input.value}});
        input.value='';input.placeholder='Saved · leave blank to keep it';captureConnectionDraft();
      }
      await startTask('test_raindrop');
    }
    else if(a==='clear-secret') {modal(`<h2 id="modal-title">Remove this saved credential?</h2><p class="description">Requests that need this credential will pause until you add it again.</p><div class="modal-footer"><button class="btn secondary" data-action="close-modal">Keep it</button><button class="btn danger" data-action="confirm-clear-secret" data-name="${esc(target.dataset.name)}">Remove credential</button></div>`);}
    else if(a==='confirm-clear-secret') {await api('/settings','POST',{credentials:{[target.dataset.name]:''}});for(const draft of [...settingsDrafts.values(),...connectionDrafts.values()]) delete draft[target.dataset.name];dialog.close();await refresh();toast('Saved credential removed.');}
  } catch(e) {toast(e.message,true);}
});
document.addEventListener('input',event=>{
  if(event.target.id==='knowledge-search') {
    knowledgeQuery=event.target.value;
    const scoped=knowledgeScope(),topic=scoped.topics.find(t=>t.id===knowledgeTopic),concept=scoped.concepts.find(c=>c.id===knowledgeConcept);
    $('#knowledge-results').innerHTML=knowledgeResults(scoped,topic,concept);
  }
  if(event.target.id==='search') {
    clearTimeout(searchTimer);const position=event.target.selectionStart;
    filters.q=event.target.value;
    searchTimer=setTimeout(async()=>{await refresh();const input=$('#search');input?.focus();input?.setSelectionRange(position,position);},250);
  }
});
document.addEventListener('change',async event=>{
  if(event.target.id==='story-connection-all'||event.target.dataset.storyConnection) {
    const input=event.target;
    if(input.id==='story-connection-all') setConnectionScope(input.checked?'all':'none');
    else toggleStoryConnection(input.dataset.storyConnection,input.checked);
    render();document.getElementById(input.id)?.focus({preventScroll:true});return;
  }
  if(event.target.id==='appearance-theme') {try {await setTheme(event.target.value);} catch(error) {toast(error.message,true);}return;}
  if(event.target.id==='f-provider') {
    const values=readForm($('#settings-form')),provider=event.target.value;
    state.settings={...state.settings,provider,model:provider==='openai'?'gpt-6-luna':'openai/gpt-6-luna',interests:values.interests,summary_words:values.summary_words,reading_minutes:values.reading_minutes,max_items_per_run:values.max_items_per_run,lookback_days:values.lookback_days,summarize_articles:values.summarize_articles,audio_fallback:values.audio_fallback,audio_minutes_per_video:values.audio_minutes_per_video};render();
  }
  if(event.target.matches('select[name="collection_id"]')) {const input=$('input[name="collection_name"]',event.target.form);if(input) input.value=event.target.selectedOptions[0].textContent;}
  if(event.target.id==='epub-file'&&event.target.files[0]) {
    const file=event.target.files[0];event.target.value='';
    if(file.size>25*1024*1024) {toast('Choose an EPUB smaller than 25 MB.',true);return;}
    try {const buffer=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<buffer.length;i+=8192) binary+=String.fromCharCode(...buffer.subarray(i,i+8192));const result=await api('/import-epub','POST',{content:btoa(binary)});activeJob=result.job_id;await refresh(false);updateTask();} catch(e) {toast(e.message,true);}
  }
});
document.addEventListener('keydown',async event=>{
  if(event.target.matches('[role="tab"][data-action="connections-tab"]')&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
    event.preventDefault();connectionsTab=event.key==='Home'?'yours':event.key==='End'?'browse':connectionsTab==='yours'?'browse':'yours';render();$('#connections-'+connectionsTab+'-tab')?.focus();
  }
  if(event.key==='Enter'&&event.target.matches('[role="button"][data-action]')) event.target.click();
  if(event.key==='Escape'&&currentItem&&!dialog.open) $('[data-action="reader-back"]')?.click();
  if(event.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)&&!dialog.open) {event.preventDefault();if(view==='knowledge'&&!currentItem){$(knowledgeMode==='graph'?'#graph-search':'#knowledge-search')?.focus();return;}await navigation('library');$('#search')?.focus();}
});
dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
window.addEventListener('scroll',()=>{if(currentItem){const max=document.documentElement.scrollHeight-window.innerHeight;const bar=$('#reading-progress');if(bar)bar.style.width=(max>0?window.scrollY/max*100:100)+'%';}},{passive:true});
async function boot() {
  try {await refresh();readerTheme=state.settings.reader_theme||'light';readerSize=state.settings.reader_size||20;for(const j of state.jobs) if(['done','failed','interrupted'].includes(j.status)) knownJobIds.add(j.id);updateTask();setInterval(poll,1800);}
  catch(e) {$('#app').innerHTML=`<div class="error-full"><img class="brand-mark" src="/icon.svg" width="40" height="40" alt="Quib27"><h1 style="margin-top:25px">Your reading room needs a moment.</h1><p>${esc(e.message)}</p><p>Close this window and reopen Quib27 from its launcher.</p></div>`;}
}
boot();
