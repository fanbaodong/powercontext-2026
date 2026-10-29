import express from 'express';
import { SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from 'seekdb';
import { performance } from 'node:perf_hooks';
import { createHash } from 'node:crypto';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
// Keep credentials server-side. This simple loader never overwrites process settings.
if (existsSync(path.join(root, '.env'))) {
  for (const line of readFileSync(path.join(root, '.env'), 'utf8').split(/\r?\n/)) {
    const match = line.match(/^([A-Z_]+)=(.*)$/);
    if (match && process.env[match[1]] === undefined) process.env[match[1]] = match[2];
  }
}
const port = Number(process.env.APP_PORT || 8787);
const connection = { host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2891),
  user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '', queryTimeout: 30000 };
const database = 'stepfun_knowledge';
const bootstrap = new SeekdbClient({ ...connection, database: 'test' });
await bootstrap.execute(`CREATE DATABASE IF NOT EXISTS ${database}`);
await bootstrap.close();
const db = new SeekdbClient({ ...connection, database });
await db.execute(`CREATE TABLE IF NOT EXISTS kb_catalog (
  name VARCHAR(48) PRIMARY KEY, state VARCHAR(24) NOT NULL, revision BIGINT NOT NULL DEFAULT 0,
  model_revision VARCHAR(64) NOT NULL)`);
const MODEL_REVISION = '751bff37182d3f1213fa05d7196b954e230abad9';
const schema = new Schema({ fulltextIndex: new FulltextIndexConfig('beng'),
  vectorIndex: new VectorIndexConfig({ hnsw: { dimension: 384, distance: 'cosine' }, embeddingFunction: null }) });

// seekdb-js hybridSearch uses session variables across three SQL statements.
// Serialize operations on this one client to prevent cross-request/collection contamination.
let tail = Promise.resolve();
function serial(task) { const result = tail.then(task); tail = result.catch(() => {}); return result; }
function fail(message, status = 400) { const error = new Error(message); error.status = status; throw error; }
function nameOf(value) {
  if (typeof value !== 'string' || !/^[a-z][a-z0-9_]{2,47}$/.test(value)) fail('知识库名称需为 3–48 位小写字母、数字或下划线，以字母开头');
  return value;
}
function vectorOf(value) {
  if (!Array.isArray(value) || value.length !== 384 || !value.every(Number.isFinite)) fail('必须提供浏览器生成的 384 维有限数值向量');
  if (Math.abs(value.reduce((s,x)=>s+x*x,0)-1) > 0.05) fail('向量必须经过 L2 归一化');
  return value;
}
async function catalog(name) {
  const rows = await db.execute('SELECT * FROM kb_catalog WHERE name=?', [name]);
  if (!rows?.length) fail('知识库不存在，请先创建', 404);
  return rows[0];
}
const app = express();
app.use((req,res,next) => {
  // Local demo: reject browser requests from other origins; no public CORS policy.
  const origin = req.headers.origin;
  if (origin && origin !== `http://127.0.0.1:${port}` && origin !== `http://localhost:${port}`) return res.status(403).json({error:'Local same-origin access only'});
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  next();
});
app.use(express.json({limit:'6mb'}));
function route(method,url,fn) { app[method](url,async(req,res,next)=>{ try { res.json(await serial(()=>fn(req))); } catch(e) { next(e); } }); }
route('get','/api/health',async()=>({ok:true,database:'seekdb',version:(await db.execute('SELECT VERSION() AS version'))[0].version,
  sdk:'seekdb@1.4.0',modelRevision:MODEL_REVISION}));
route('get','/api/collections',async()=>{
  const rows=await db.execute('SELECT * FROM kb_catalog ORDER BY name');
  for(const row of rows) row.count=await (await db.getCollection({name:row.name,embeddingFunction:null})).count();
  return rows;
});
route('post','/api/collections',async req=>{
  const name=nameOf(req.body.name);
  await db.getOrCreateCollection({name,schema,embeddingFunction:null});
  await db.execute("INSERT IGNORE INTO kb_catalog(name,state,model_revision) VALUES(?,'ready',?)",[name,MODEL_REVISION]);
  return catalog(name);
});
route('post','/api/collections/:name/documents',async req=>{
  const name=nameOf(req.params.name); await catalog(name);
  const docs=req.body.documents;
  if(!Array.isArray(docs)||!docs.length||docs.length>128) fail('每次导入 1–128 个文本块');
  for(const d of docs) {
    if(typeof d.text!=='string'||!d.text.trim()||d.text.length>2000) fail('每个文本块需为 1–2000 字符');
    if(typeof d.title!=='string'||d.title.length>180) fail('标题最多 180 字符');
    if(!['fp32','fp16','q8'].includes(d.dtype)) fail('需要明确的浏览器精度');
    vectorOf(d.vector);
  }
  const collection=await db.getCollection({name,embeddingFunction:null});
  const each=req.body.refreshMode==='each';
  await db.execute("UPDATE kb_catalog SET state='needs_refresh' WHERE name=?",[name]);
  let writeMs=0,refreshMs=0;
  const groups=each?docs.map(d=>[d]):[docs];
  const ids=[];
  for(const batch of groups) {
    const keys=batch.map(d=>createHash('sha256').update(d.title+'\0'+d.text).digest('hex'));
    let t=performance.now();
    await collection.upsert({ids:keys,documents:batch.map(d=>d.text),embeddings:batch.map(d=>d.vector),
      metadatas:batch.map(d=>({title:d.title,dtype:d.dtype,model_revision:MODEL_REVISION,source:d.source||'text'}))});
    writeMs+=performance.now()-t; t=performance.now();
    await collection.refresh_index(); refreshMs+=performance.now()-t; ids.push(...keys);
  }
  await db.execute("UPDATE kb_catalog SET state='ready',revision=revision+1 WHERE name=?",[name]);
  return {ids,count:await collection.count(),writeMs,refreshMs,refreshCalls:groups.length,...await catalog(name)};
});
route('post','/api/collections/:name/refresh',async req=>{
  const name=nameOf(req.params.name);await catalog(name);
  const c=await db.getCollection({name,embeddingFunction:null});await c.refresh_index();
  await db.execute("UPDATE kb_catalog SET state='ready',revision=revision+1 WHERE name=?",[name]);return catalog(name);
});
route('post','/api/collections/:name/search',async req=>{
  const name=nameOf(req.params.name),state=await catalog(name);
  if(state.state!=='ready') fail('导入未完成或索引需要刷新，请点击刷新索引',409);
  const {query,vector}=req.body;
  if(typeof query!=='string'||!query.trim()||query.length>500) fail('查询需为 1–500 字符');
  vectorOf(vector);
  const c=await db.getCollection({name,embeddingFunction:null}),t=performance.now();
  // RRF and both retrieval branches run INSIDE seekdb via DBMS_HYBRID_SEARCH.GET_SQL.
  // The browser and Node server never combine or rerank separate result lists.
  const result=await c.hybridSearch({query:{whereDocument:{$contains:query},nResults:20},
    knn:{queryEmbeddings:[vector],nResults:20},rank:{rrf:{rankWindowSize:20,rankConstant:60}},
    nResults:Math.min(10,Math.max(1,Number(req.body.topK)||5)),include:['documents','metadatas','distances']});
  return {name,revision:state.revision,searchMs:performance.now()-t,
    results:result.ids[0].map((id,i)=>({id,text:result.documents[0][i],metadata:result.metadatas[0][i],score:result.distances[0][i]}))};
});
app.use('/models',express.static(path.join(root,'public/models'),{maxAge:'1y',immutable:true}));
app.use('/data',express.static(path.join(root,'public/data')));
app.use('/wasm',express.static(path.join(root,'node_modules/onnxruntime-web/dist'),{maxAge:'1y'}));
if(process.argv.includes('--production')) app.use(express.static(path.join(root,'dist')));
else { const {createServer}=await import('vite'); const vite=await createServer({root,server:{middlewareMode:true},appType:'spa'});app.use(vite.middlewares); }
app.use((e,req,res,next)=>{ console.error('Request failed:',e.message);res.status(e.status||500).json({error:e.message}); });
app.listen(port,'127.0.0.1',()=>console.log(`Stepfun knowledge base ready: http://127.0.0.1:${port}`));
