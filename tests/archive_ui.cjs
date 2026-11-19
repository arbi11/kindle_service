/* Read-only audit of an isolated, populated library. No AI or mail calls. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');

(async()=>{
  const runtimePath=process.argv[2];
  if(!runtimePath) throw new Error('Pass the populated reader runtime.json');
  const runtime=JSON.parse(fs.readFileSync(runtimePath,'utf8'));
  const output=path.join(process.cwd(),'artifacts/archive-ui-audit');fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE||'/usr/bin/google-chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1360,height:900}});
  const errors=[],external=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(!request.url().startsWith('http://127.0.0.1:')&&!request.url().startsWith('data:'))external.push(request.url());});
  try {
    await page.goto(`http://127.0.0.1:${runtime.port}/?session=${runtime.token}`);
    await page.getByRole('heading',{name:'The Daily Quib',exact:true}).waitFor();
    const records=await page.evaluate(async()=>{
      const items=[];let offset=0;
      while(true) {
        const data=await(await fetch('/api/items?limit=100&offset='+offset)).json();items.push(...data.items);
        if(!data.has_more) return items;
        offset=data.next_offset;
      }
    });
    assert.ok(records.length>200,'Use a library that crosses the old 200-story limit');
    await page.waitForFunction(count=>document.querySelectorAll('.digest-article').length===count,records.length);
    assert.equal(new Set(records.map(item=>item.id)).size,records.length);
    const sorted=[...records].sort((a,b)=>(Date.parse(b.published)||0)-(Date.parse(a.published)||0));
    assert.deepEqual(await page.locator('.digest-article').evaluateAll(nodes=>nodes.map(node=>node.dataset.itemId)),sorted.map(item=>item.id));
    await page.screenshot({path:path.join(output,'01-complete-history.png')});
    for(const connection of ['youtube','raindrop','freedium']) {
      const expected=records.filter(item=>item.connection_id===connection).length;
      if(!expected)continue;
      await page.locator('.connection-nav [data-connection="'+connection+'"]').click();
      await page.waitForFunction(count=>document.querySelectorAll('.digest-article').length===count,expected);
    }
    await page.locator('.connection-nav [data-connection="all"]').click();
    await page.waitForFunction(count=>document.querySelectorAll('.digest-article').length===count,records.length);
    const oldest=sorted.at(-1);
    const title=page.locator('.digest-title[data-id="'+oldest.id+'"]');
    await title.scrollIntoViewIfNeeded();
    const returnScroll=await page.evaluate(()=>window.scrollY);
    await title.click();
    await page.locator('.prose').waitFor();
    assert.equal(await page.locator('.reader-content > h1').innerText(),oldest.title);
    const complete=await page.evaluate(html=>new DOMParser().parseFromString(html,'text/html').body.textContent.trim(),oldest.summary_html||oldest.body_html);
    assert.equal((await page.locator('.prose').textContent()).trim(),complete);
    await page.screenshot({path:path.join(output,'02-oldest-story.png')});
    await page.getByRole('button',{name:'Back to reading list',exact:true}).click();
    await title.waitFor();
    assert.ok(Math.abs((await page.evaluate(()=>window.scrollY))-returnScroll)<2,'Back restores the reading list position');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'title-'+oldest.id);
    assert.equal(await page.locator('.article-preview,[data-action="preview"]').count(),0);
    await page.locator('.primary-nav [data-nav="library"]').click();
    await page.waitForFunction(count=>document.querySelectorAll('.reading-card').length===count,records.length);
    await page.screenshot({path:path.join(output,'03-full-library.png')});
    assert.equal(errors.length,0,errors.join('\n'));assert.equal(external.length,0,external.join('\n'));
    console.log(JSON.stringify({result:'PASS',stories:records.length,connections:Object.fromEntries(['youtube','raindrop','freedium'].map(id=>[id,records.filter(item=>item.connection_id===id).length])),checks:['all history pages loaded','no duplicate story IDs','chronological order','connection filters','oldest full story and return position','full library','no browser network or script errors']}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exit(1);});
