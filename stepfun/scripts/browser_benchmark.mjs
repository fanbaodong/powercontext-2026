import { chromium } from 'playwright';
import { readFileSync,writeFileSync } from 'node:fs';
import os from 'node:os';
import assert from 'node:assert/strict';
const corpus=JSON.parse(readFileSync('public/data/corpus.json','utf8'));
const stamp=Date.now().toString();
const channel=process.env.BROWSER_CHANNEL||'chrome';
const fallbackOnly=process.argv.includes('--fallback-only');
const report=fallbackOnly?JSON.parse(readFileSync('evidence/browser-benchmark.json','utf8')):{scope:'Real browser embeddings + seekdb server hybridSearch; no embedding/result replay',
  time:new Date().toISOString(),device:{cpu:os.cpus()[0].model,logicalCpus:os.cpus().length,ramGiB:os.totalmem()/2**30,os:os.platform()+' '+os.release()},
  dataset:{documents:corpus.documents.length,queries:corpus.queries.length,repetitions:3},cases:[],checks:[],externalRequests:[]};
const save=()=>writeFileSync('evidence/browser-benchmark.json',JSON.stringify(report,null,2)+'\n');
const median=values=>{const a=[...values].sort((x,y)=>x-y);return a.length%2?a[Math.floor(a.length/2)]:(a[a.length/2-1]+a[a.length/2])/2;};
async function newPage(browser){const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('request',r=>{if(!/^https?:\/\/(127\.0\.0\.1|localhost):8787\//.test(r.url())&&!/^(blob|data):/.test(r.url()))report.externalRequests.push(r.url());});
  await page.goto('http://127.0.0.1:8787');await page.waitForFunction(()=>window.edgeApp&&document.querySelector('#db-status').textContent.includes('seekdb'));
  return page;}
let browser;
if(!fallbackOnly){
browser=await chromium.launch({channel,headless:true});
try {
  const page=await newPage(browser);report.browser=await browser.version();
  report.database=await page.evaluate(()=>window.edgeApp.api('/health'));
  const cases=[
    {id:'wasm_fp32_b1',backend:'wasm',dtype:'fp32',batchSize:1},
    {id:'wasm_q8_b1',backend:'wasm',dtype:'q8',batchSize:1},
    {id:'wasm_q8_b8',backend:'wasm',dtype:'q8',batchSize:8},
    {id:'gpu_fp32_b8',backend:'webgpu',dtype:'fp32',batchSize:8},
    {id:'gpu_fp16_b8',backend:'auto',dtype:'auto',batchSize:8},
  ];
  for(const cfg of cases){
    const c={id:cfg.id,requested:cfg,collection:'bench_'+stamp+'_'+cfg.id,imports:[],queries:[]};report.cases.push(c);
    c.load=await page.evaluate(o=>window.edgeApp.load(o),cfg);
    if(cfg.id.startsWith('gpu_')&&c.load.backend!=='webgpu')throw Error('Requested WebGPU measurement actually fell back; do not label it WebGPU');
    await page.evaluate(name=>window.edgeApp.createCollection(name),c.collection);
    // Keep the first import/query separate from warmed repeated samples.
    for(let repeat=0;repeat<4;repeat++){
      const result=await page.evaluate(({docs,batchSize})=>window.edgeApp.importDocuments(docs,{batchSize,useCache:false}),{docs:corpus.documents,batchSize:cfg.batchSize});
      assert.equal(result.count,corpus.documents.length,'Repeated imports must not duplicate documents');
      const sample={repeat,embeddingMs:result.embeddingMs,writeMs:result.writeMs,refreshMs:result.refreshMs,importMs:result.importMs,refreshCalls:result.refreshCalls};
      if(repeat===0)c.firstImport=sample;else c.imports.push(sample);
    }
    c.firstQuery=await page.evaluate(query=>window.edgeApp.search(query,{useCache:false,topK:3}),corpus.queries[0].text);
    for(let repeat=0;repeat<3;repeat++)for(const query of corpus.queries){
      const result=await page.evaluate(q=>window.edgeApp.search(q,{useCache:false,topK:3}),query.text);
      const titles=result.results.map(r=>r.metadata.title),grades=titles.map(t=>query.relevantTitles.includes(t)?1:0);
      const dcg=grades.reduce((s,g,i)=>s+g/Math.log2(i+2),0),ideal=query.relevantTitles.slice(0,3).reduce((s,_,i)=>s+1/Math.log2(i+2),0);
      c.queries.push({repeat,query:query.text,expected:query.relevantTitles,titles,recallAt3:grades.reduce((a,b)=>a+b,0)/query.relevantTitles.length,
        ndcgAt3:dcg/ideal,queryEmbeddingMs:result.queryEmbeddingMs,searchMs:result.searchMs,endToEndMs:result.endToEndMs});
    }
    c.summary={embeddingMsMedian:median(c.imports.map(x=>x.embeddingMs)),documentsPerSecond:corpus.documents.length/(median(c.imports.map(x=>x.embeddingMs))/1000),
      writeMsMedian:median(c.imports.map(x=>x.writeMs)),refreshMsMedian:median(c.imports.map(x=>x.refreshMs)),
      queryEmbeddingMsMedian:median(c.queries.map(x=>x.queryEmbeddingMs)),searchMsMedian:median(c.queries.map(x=>x.searchMs)),
      endToEndMsMedian:median(c.queries.map(x=>x.endToEndMs)),recallAt3:c.queries.reduce((s,x)=>s+x.recallAt3,0)/c.queries.length,
      ndcgAt3:c.queries.reduce((s,x)=>s+x.ndcgAt3,0)/c.queries.length};
    console.log('PASS',cfg.id,JSON.stringify(c.summary));save();
  }
  // Isolate write/refresh policy with identical model, batch size and documents.
  await page.evaluate(()=>window.edgeApp.load({backend:'wasm',dtype:'q8'}));
  report.refreshComparison=[];
  for(const mode of ['each','once']){
    const name='refresh_'+stamp+'_'+mode;await page.evaluate(n=>window.edgeApp.createCollection(n),name);
    for(let repeat=0;repeat<3;repeat++){
      const r=await page.evaluate(({docs,mode})=>window.edgeApp.importDocuments(docs,{batchSize:8,useCache:false,refreshMode:mode}),{docs:corpus.documents,mode});
      report.refreshComparison.push({mode,repeat,writeMs:r.writeMs,refreshMs:r.refreshMs,refreshCalls:r.refreshCalls,count:r.count});
    }
  }
  const increment={title:'Project Zephyr',text:'Project Zephyr keeps its emergency access code in the violet notebook. The code name is citrus comet. Only this newly imported note describes the violet notebook.',source:'incremental-test'};
  const active='increment_'+stamp;await page.evaluate(n=>window.edgeApp.createCollection(n),active);
  await page.evaluate(d=>window.edgeApp.importDocuments(d),corpus.documents.slice(0,2));
  const added=await page.evaluate(d=>window.edgeApp.importDocuments([d]),increment);assert.equal(added.count,3);
  const found=await page.evaluate(()=>window.edgeApp.search('Project Zephyr violet notebook access code',{useCache:false}));assert.equal(found.results[0].metadata.title,increment.title);
  const other='isolation_'+stamp;await page.evaluate(n=>window.edgeApp.createCollection(n),other);
  const empty=await page.evaluate(()=>window.edgeApp.search('Project Zephyr violet notebook access code'));assert.equal(empty.results.length,0);
  await page.evaluate(n=>window.edgeApp.selectCollection(n),active);
  await page.reload();await page.waitForFunction(()=>window.edgeApp&&document.querySelector('#active-label').textContent.startsWith('increment_'));
  const restored=await page.evaluate(()=>window.edgeApp.search('Project Zephyr violet notebook access code'));assert.equal(restored.results[0].metadata.title,increment.title);
  report.checks.push('Incremental import searchable after refresh','Duplicate import count unchanged','Collection isolation','Active collection restored after reload');
  report.incremental={collection:active,afterImportCount:added.count,topResult:found.results[0].metadata.title,otherCollectionResultCount:empty.results.length};
  await page.screenshot({path:'evidence/webgpu-demo.png',fullPage:true});
  save();
}finally{await browser.close();}
}

// Disable the actual GPU adapter for page and worker, rather than mocking embeddings.
browser=await chromium.launch({channel,headless:true,args:['--disable-gpu']});
try{
 const page=await newPage(browser);
 const loaded=await page.evaluate(()=>window.edgeApp.load());
 assert.equal(loaded.backend,'wasm');assert.equal(loaded.dtype,'q8');assert.match(loaded.reason,/不可用/);
 const name='fallback_'+stamp;await page.evaluate(n=>window.edgeApp.createCollection(n),name);
 const stored=await page.evaluate(d=>window.edgeApp.importDocuments(d),corpus.documents.slice(0,4));
 const result=await page.evaluate(()=>window.edgeApp.search('How can I avoid overwatering houseplants?'));
 assert.equal(result.results[0].metadata.title,'Plant watering');
 report.fallback={browserFlags:['--disable-gpu'],loaded,count:stored.count,result};
 report.checks.push('WebGPU unavailable -> automatic WASM INT8 -> import/search PASS');
 await page.screenshot({path:'evidence/wasm-fallback.png',fullPage:true});
 assert.deepEqual(report.externalRequests,[],'Unexpected external request during browser inference');
 report.checks.push('Browser page and workers use only local resources; no cloud inference');
 report.completed=true;save();console.log('PASS fallback and all state checks');
}finally{await browser.close();}
