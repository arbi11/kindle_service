/* Full UI exercise against an isolated local app. No real AI or mail calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async()=>{
  const runtimePath = process.argv[2];
  if(!runtimePath) throw new Error('Pass the isolated app runtime.json path');
  const runtime = JSON.parse(fs.readFileSync(runtimePath,'utf8'));
  // These offsets deliberately differ from lexicographic order. The feed must
  // show the actual newest article first, independently of the source timezone.
  execFileSync('python3',['-c',`
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute('SELECT COUNT(*) FROM items WHERE is_sample=0').fetchone()[0] == 0, 'Use a fresh isolated sample profile'
    rows = [('2026-11-19T02:00:00+00:00','sample-0'), ('2026-11-19T08:30:00+05:30','sample-1'), ('2026-11-19T04:00:00+00:00','sample-2')]
    db.executemany('UPDATE items SET published=? WHERE external_id=? AND is_sample=1', rows)
`,path.join(path.dirname(runtimePath),'library.sqlite3')]);
  const output = path.resolve(path.dirname(runtimePath),'screenshots');fs.mkdirSync(output,{recursive:true});
  const fixture = http.createServer((request,response)=>{
    response.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
    response.end('<!doctype html><html><head><title>A useful local reading test</title><meta name="author" content="Local test"></head><body><main><article><h1>A useful local reading test</h1>'+Array.from({length:12},(_,i)=>'<p>A reading routine should preserve the important details. This paragraph is part '+(i+1)+' of a synthetic article used to verify extraction, local storage, EPUB generation and the reader. A $3 million loss and a $2 million hedge are deliberately different figures. Caveats belong beside the conclusion. This is test material, not investment advice.</p>').join('')+'</article></main></body></html>');
  });
  await new Promise(resolve=>fixture.listen(0,'127.0.0.1',resolve));
  const fixtureUrl='http://127.0.0.1:'+fixture.address().port+'/article';
  const browser=await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE || '/usr/bin/google-chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1360,height:950}});
  const errors=[],externalRequests=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(!request.url().startsWith('http://127.0.0.1:')&&!request.url().startsWith('data:'))externalRequests.push(request.url());});
  const shot=name=>page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
  const nav=async name=>{await page.getByRole('button',{name,exact:true}).first().click();await page.waitForFunction(name=>document.querySelector('.nav button.active')?.getAttribute('aria-label')===name,name);};
  const click=async(action,id)=>{await page.locator(`[data-action="${action}"]${id?`[data-id="${id}"]`:''}`).first().click();};
  try {
    await page.goto(`http://127.0.0.1:${runtime.port}/?session=${runtime.token}`);
    await page.getByRole('heading',{name:'Readers Digest',exact:true}).waitFor();
    assert.equal(await page.locator('.digest-article').count(),3);
    assert.deepEqual(await page.locator('.digest-title > span').allTextContents(),['Your reading, at your pace','A summary that keeps the story intact','Make a little room for a good read']);
    assert.equal(await page.locator('.hero,.edition-feature,.setup-card').count(),0);
    await page.getByRole('button',{name:'YouTube',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.digest-article').length===1);
    assert.match(await page.locator('.digest-article .connection-name').innerText(),/YouTube/);
    await click('preview');await click('read');await page.locator('.prose').waitFor();
    assert.equal(await page.getByRole('button',{name:'Read next piece',exact:true}).count(),0);
    await click('reader-back');await page.locator('.article-preview:not([hidden])').waitFor();
    assert.equal(await page.locator('[data-connection="youtube"]').getAttribute('aria-current'),'page');
    await page.getByRole('button',{name:'Raindrop',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.digest-article').length===2);
    await nav('All articles');
    await page.waitForFunction(()=>document.querySelectorAll('.digest-article').length===3);
    assert.equal(await page.locator('#digest-sort').inputValue(),'recent');
    assert.equal(await page.locator('#digest-sort option[value="relevant"]').evaluate(option=>option.disabled),true);
    await shot('01-today');
    await click('preview');
    await page.locator('.article-preview:not([hidden])').waitFor();
    assert.equal(await page.locator('.reader-shell').count(),0);
    assert.equal(await page.locator('.digest-title[aria-expanded="true"]').count(),1);
    await shot('01b-inline-preview');
    await page.locator('.digest-title').nth(1).click();
    assert.equal(await page.locator('.article-preview:not([hidden])').count(),1);
    await page.locator('.digest-title').nth(1).press('Space');
    assert.equal(await page.locator('.article-preview:not([hidden])').count(),0);
    await click('preview');await click('read');await page.locator('.prose').waitFor();
    await click('font-larger');await page.waitForFunction(()=>getComputedStyle(document.querySelector('.prose')).fontSize==='21px');
    await click('reader-theme');await click('reader-theme');await page.locator('.reader-shell.dark').waitFor();await shot('02-reader-dark');
    await click('star');await click('mark-read');await click('reader-back');
    await page.locator('.article-preview:not([hidden])').waitFor();
    assert.equal(await page.locator('.article-preview:not([hidden])').count(),1);
    assert.equal(await page.locator('.digest-article.is-read').count(),1);
    await nav('Library');await page.locator('[data-filter="status"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('.reading-card').length===1);
    assert.equal(await page.locator('.reading-card').count(),1);
    await page.locator('[data-filter="status"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('.reading-card').length===3);
    await page.getByRole('textbox',{name:'Search your library'}).fill('no matching article anywhere');
    await page.getByRole('heading',{name:'A little space for something good.'}).waitFor();
    await page.getByRole('textbox',{name:'Search your library'}).fill('');
    await page.waitForFunction(()=>document.querySelectorAll('.reading-card').length===3);await shot('03-library');
    await nav('Manage connections');await click('add-article');
    await page.locator('[data-action="source-tab"][data-value="article"]').click();
    await page.getByLabel('Article URL',{exact:true}).fill(fixtureUrl);
    await page.getByRole('button',{name:'Add article',exact:true}).click();
    await page.locator('.connected-source').waitFor();assert.equal(await page.locator('.connected-source').count(),1);
    await click('toggle-source');await page.waitForFunction(()=>document.querySelector('[role="switch"]').getAttribute('aria-checked')==='false');
    await click('toggle-source');await page.waitForFunction(()=>document.querySelector('[role="switch"]').getAttribute('aria-checked')==='true');
    await shot('04-sources');
    await nav('Library');await click('prepare');
    await page.getByText(/Your edition is ready: 1 pieces/).first().waitFor({timeout:30000});
    await nav('All articles');
    await page.waitForFunction(()=>document.querySelectorAll('.digest-article').length===1);
    assert.match(await page.locator('.digest-title').innerText(),/A useful local reading test/);await shot('05-prepared');
    assert.equal(await page.locator('.connection-nav [data-connection="raindrop"]').count(),0);
    await click('preview');await click('read');await page.locator('.prose').waitFor();
    assert.match(await page.locator('.prose').innerText(),/\$3 million/);
    assert.match(await page.locator('.prose').innerText(),/\$2 million/);
    await click('reader-theme');await shot('06-reader');await click('reader-back');
    await nav('Library');await click('edition');await page.locator('.edition-summary').waitFor();
    const downloadPromise=page.waitForEvent('download');
    await page.getByRole('link',{name:'EPUB',exact:true}).click();
    const download=await downloadPromise;await download.saveAs(path.join(output,'test-edition.epub'));
    assert.equal(download.suggestedFilename(),'Daily-Edition.epub');
    await nav('Settings');
    await page.getByLabel('Your interests',{exact:true}).fill('Science, engineering and careful explanations.');
    await page.getByLabel('Words per summary',{exact:true}).fill('450');
    await page.getByLabel('API key',{exact:true}).fill('sk-synthetic-ui-test-only');
    await page.getByRole('button',{name:'Save preferences',exact:true}).click();
    await page.getByText('Your preferences are saved.',{exact:true}).first().waitFor();
    assert.equal(await page.getByLabel('API key',{exact:true}).inputValue(),'');
    assert.equal(await page.getByLabel('Words per summary',{exact:true}).inputValue(),'450');
    assert.ok(!(await page.locator('body').innerText()).includes('sk-synthetic-ui-test-only'));await shot('07-settings');
    await nav('Delivery');
    await page.getByRole('checkbox',{name:'A personal newsletter',exact:false}).check();
    await page.getByLabel('Newsletter email address',{exact:true}).fill('reader@example.com');
    await page.getByLabel('Email / username',{exact:true}).fill('sender@example.com');
    await page.getByLabel('Sender email',{exact:true}).fill('sender@example.com');
    await page.getByRole('button',{name:'Save delivery settings',exact:true}).click();
    await page.waitForTimeout(200);await shot('08-delivery');
    await click('send-latest');await page.getByRole('heading',{name:'Ready to send?'}).waitFor();
    assert.match(await page.locator('dialog').innerText(),/reader@example.com/);
    assert.equal(await page.locator('[data-action="confirm-send"]').count(),1);
    await shot('09-delivery-preview');await click('close-modal');
    await page.reload();await page.locator('.shell').waitFor();
    const boot=await page.evaluate(async()=>await(await fetch('/api/bootstrap')).json());
    assert.equal(boot.settings.reader_size,21);assert.equal(boot.settings.reader_theme,'light');
    assert.equal(boot.deliveries.length,0);assert.equal(boot.settings.schedule_enabled,false);
    assert.equal(boot.settings.summary_words,450);
    assert.equal(errors.length,0,errors.join('\n'));assert.equal(externalRequests.length,0,externalRequests.join('\n'));
    await page.setViewportSize({width:1000,height:800});await nav('All articles');await shot('10-small-desktop');
    assert.equal(await page.locator('.rail').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.setViewportSize({width:900,height:700});
    assert.equal(await page.locator('.rail').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    console.log(JSON.stringify({result:'PASS',checks:['connection filtering and combined startup feed','inline previews and keyboard controls','expand and return to preview','reading panel at laptop widths','reader themes and font persistence','save and read state','search and filters','source creation and toggles','real local article extraction','edition generation','EPUB download','settings and secret masking','email preview without sending','no external browser requests','no JavaScript errors'],screenshots:output}));
  } finally {await browser.close();fixture.close();}
})().catch(error=>{console.error(error.stack);process.exit(1);});
