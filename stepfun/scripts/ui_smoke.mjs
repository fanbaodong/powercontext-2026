import {chromium} from 'playwright';
import {writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 await page.goto('http://127.0.0.1:8787');await page.waitForFunction(()=>window.edgeApp&&!document.querySelector('#create').disabled);
 const name='ui_'+Date.now();await page.fill('#name',name);await page.click('#create');await page.waitForFunction(()=>!document.querySelector('#create').disabled);
 await page.fill('#title','Weekend train');await page.fill('#text','The weekend mountain train leaves at sunrise. Bring a blue jacket and a paper ticket. The station cafe serves hot chocolate.');
 await page.click('#import');await page.waitForFunction(()=>!document.querySelector('#import').disabled,{},{timeout:120000});
 assert.equal(await page.textContent('#error'),'');
 await page.fill('#text','');await page.setInputFiles('#files',{name:'bicycle.txt',mimeType:'text/plain',buffer:Buffer.from('Repair a bicycle puncture by applying a patch to the inner tube. Inspect the tire for sharp glass and pump it up after the glue dries.')});
 await page.click('#import');await page.waitForFunction(()=>!document.querySelector('#import').disabled);
 assert.equal(await page.textContent('#error'),'');
 await page.fill('#query','How do I repair a punctured bicycle tire?');await page.click('#search-form button');await page.waitForFunction(()=>!document.querySelector('#query').disabled);
 assert.match(await page.textContent('#results'),/bicycle\.txt/);
 await page.reload();await page.waitForFunction(()=>!document.querySelector('#query').disabled&&window.edgeApp);
 assert.equal(await page.inputValue('#collections'),name);
 await page.fill('#query','What should I bring on the mountain train?');await page.click('#search-form button');await page.waitForFunction(()=>!document.querySelector('#query').disabled);
 assert.match(await page.textContent('#results'),/blue jacket/);
 const ui={browser:await browser.version(),backend:await page.textContent('#backend'),collection:name,checks:['Create via UI','Paste text','Upload .txt','Incremental search','Reload restores collection','Original text displayed']};
 writeFileSync('evidence/edge-ui.json',JSON.stringify(ui,null,2)+'\n');await page.screenshot({path:'evidence/edge-ui.png',fullPage:true});
 // Leave a useful named example collection for the user, using genuine browser embeddings.
 await page.evaluate(()=>window.edgeApp.createCollection('my_notes'));
 await page.evaluate(async()=>window.edgeApp.importDocuments((await fetch('/data/corpus.json').then(r=>r.json())).documents));
 await page.fill('#query','How can I avoid overwatering houseplants?');await page.click('#search-form button');await page.waitForFunction(()=>!document.querySelector('#query').disabled);
 await page.screenshot({path:'evidence/app-demo.png',fullPage:true});console.log('PASS Edge UI',JSON.stringify(ui));
}finally{await browser.close();}
