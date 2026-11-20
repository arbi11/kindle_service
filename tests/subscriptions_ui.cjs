/* Follow public publications through the same UI used in the installed app. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async()=>{
  const runtimePath = process.argv[2];
  if(!runtimePath) throw new Error('Pass a fresh connection_ui_server runtime.json');
  const runtime = JSON.parse(fs.readFileSync(runtimePath,'utf8'));
  const output = path.join(path.dirname(runtimePath),'screenshots');fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE || '/usr/bin/google-chrome',headless:true});
  const page = await browser.newPage({viewport:{width:1500,height:960}});
  const errors=[], external=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(!request.url().startsWith('http://127.0.0.1:')&&!request.url().startsWith('data:'))external.push(request.url());});
  const panel = id=>page.locator('.connection-rail [data-rail-connection="'+id+'"]');
  const toggle = name=>page.locator('.connection-rail').getByRole('button',{name:name+' settings',exact:true});
  const boot = ()=>page.evaluate(async()=>await(await fetch('/api/bootstrap')).json());
  const shot = name=>page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
  const waitSource = kind=>page.waitForFunction(async kind=>{
    const data=await(await fetch('/api/bootstrap')).json();return data.sources.some(s=>s.kind===kind);
  },kind,{timeout:30000});
  const waitPrepared = count=>page.waitForFunction(async count=>{
    const data=await(await fetch('/api/bootstrap')).json();return data.jobs.filter(j=>j.kind==='prepare'&&j.status==='done').length>=count;
  },count,{timeout:30000});
  try {
    await page.goto(`http://127.0.0.1:${runtime.port}/?session=${runtime.token}`);
    await page.getByRole('heading',{name:'The Daily Quib',exact:true}).waitFor();
    await toggle('Substack').click();
    assert.equal(await panel('substack').locator('input[type="password"]').count(),0);
    await panel('substack').getByLabel('Publication address',{exact:true}).fill('https://letters.example/');
    await toggle('Websites & blogs').click();
    await panel('websites').getByLabel('Website or feed address',{exact:true}).fill('https://unfinished.example/');
    await toggle('Substack').click();
    assert.equal(await panel('substack').getByLabel('Publication address',{exact:true}).inputValue(),'https://letters.example/');
    await panel('substack').getByRole('button',{name:'Follow publication',exact:true}).click();
    await waitSource('substack');
    await panel('substack').getByRole('switch',{name:'Pause Useful Letters',exact:true}).waitFor();
    await panel('substack').locator('[data-action="fetch-source"]').click();await waitPrepared(1);
    await page.locator('.connection-nav').getByRole('button',{name:'Substack',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.digest-article').length===1 && document.querySelector('.connection-nav [data-connection="substack"]')?.getAttribute('aria-pressed')==='true');
    assert.equal(await page.locator('.digest-article').count(),1);
    await page.locator('.digest-title').click();await page.locator('.prose').waitFor();
    assert.match(await page.locator('.prose').innerText(),/complete public article/);
    assert.equal(await page.evaluate(()=>Boolean(window.feedWasExecuted)),false);
    assert.equal(await page.locator('.prose script,.prose img').count(),0);
    await shot('01-substack-full-reader');
    await page.getByRole('button',{name:'Back to reading list',exact:true}).click();
    await panel('substack').getByRole('switch').click();
    await panel('substack').getByRole('switch',{name:'Resume Useful Letters',exact:true}).waitFor();
    assert.equal(await panel('substack').locator('[data-action="fetch-source"]').isDisabled(),true);
    await panel('substack').getByRole('switch').click();
    await panel('substack').getByRole('switch',{name:'Pause Useful Letters',exact:true}).waitFor();
    await panel('substack').locator('[data-action="fetch-source"]').click();await waitPrepared(2);
    assert.equal((await boot()).stats.total,1);

    await page.locator('.primary-nav').getByRole('button',{name:'Sources',exact:true}).click();
    await page.getByRole('tab',{name:'Browse connections',exact:true}).click();
    await page.locator('[data-catalogue-id="websites"]').getByRole('button',{name:'Connect',exact:true}).click();
    await page.locator('#source-form').getByLabel('Website or feed address',{exact:true}).fill('https://blog.example/');
    await page.locator('#source-form').getByRole('button',{name:'Follow site',exact:true}).click();
    await waitSource('websites');
    await toggle('Websites & blogs').click();
    await panel('websites').getByRole('switch',{name:'Pause A Thoughtful Blog',exact:true}).waitFor();
    await panel('websites').locator('[data-action="fetch-source"]').click();await waitPrepared(3);
    await page.locator('.connection-nav').getByRole('button',{name:'Websites & blogs',exact:true}).click();
    await page.locator('.digest-title').waitFor();assert.equal(await page.locator('.digest-article').count(),1);
    await shot('02-blog-connection');
    await page.locator('.digest-title').click();await page.locator('.prose').waitFor();
    assert.equal(await page.locator('.prose p').count()>=2,true);
    assert.equal(await page.locator('.prose a').getAttribute('href'),'https://blog.example/reference');
    await page.getByRole('button',{name:'Back to reading list',exact:true}).click();
    await panel('websites').locator('[data-action="remove-source"]').click();
    await page.getByRole('button',{name:'Remove source',exact:true}).click();
    await panel('websites').getByRole('button',{name:'Follow site',exact:true}).waitFor();
    assert.equal(await page.locator('.digest-article').count(),1);
    await page.locator('.connection-nav').getByRole('button',{name:'All connections',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.digest-article').length===2);
    await shot('03-combined-reading');
    await page.reload();await page.locator('.digest-title').first().waitFor();
    const state=await boot();assert.equal(state.stats.total,2);assert.equal(state.deliveries.length,0);
    assert.equal(state.settings.schedule_enabled,false);assert.equal(state.secrets.configured.openai_key,false);
    assert.equal(errors.length,0,errors.join('\n'));assert.equal(external.length,0,external.join('\n'));
    console.log(JSON.stringify({result:'PASS',checks:['Substack custom domain setup in right panel','unsaved subscription drafts','publisher full text sanitized','direct reader and return','pause resume and duplicate fetch','website feed discovery through catalogue','Atom paragraphs and relative links','disconnect retains reading and filter','combined feed persists','no keys, sending or browser network'],screenshots:output}));
  } catch(error) {await shot('failure');throw error;} finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
