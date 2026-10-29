import { chromium } from 'playwright';
const browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL||'chrome',headless:true});
try {
 const page=await browser.newPage();
 page.on('console',m=>{if(m.type()==='error')console.log('BROWSER',m.text().slice(0,500));});
 page.on('pageerror',e=>console.log('PAGE ERROR',e.message));
 await page.goto('http://127.0.0.1:8787');
 await page.waitForFunction(()=>!!window.edgeApp);
 console.log('BROWSER',await browser.version());
 console.log('LOAD',await page.evaluate(()=>window.edgeApp.load()));
 console.log('COLLECTION',await page.evaluate(()=>window.edgeApp.createCollection('probe_'+Date.now())));
 console.log('IMPORT',await page.evaluate(async()=>{
   const corpus=await fetch('/data/corpus.json').then(r=>r.json());
   const result=await window.edgeApp.importDocuments(corpus.documents.slice(0,4));
   return {...result,ids:result.ids.length};
 }));
 console.log('SEARCH',JSON.stringify(await page.evaluate(()=>window.edgeApp.search('How can I avoid overwatering plants?')),null,2));
 await page.screenshot({path:'evidence/browser-probe.png',fullPage:true});
}finally{await browser.close();}
