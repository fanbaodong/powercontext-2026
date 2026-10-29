import './style.css';
const $=id=>document.getElementById(id);
const worker=new Worker(new URL('./embedding.worker.js',import.meta.url),{type:'module'});
let sequence=0,busy=false,active=localStorage.getItem('stepfun.collection')||'my_notes',config=null;
const waiting=new Map();
worker.onerror=()=>{for(const p of waiting.values())p.reject(Error('浏览器推理 Worker 启动失败，请刷新页面并检查模型文件'));waiting.clear();};
worker.onmessage=({data})=>{
  if(data.id){const p=waiting.get(data.id);waiting.delete(data.id);data.error?p.reject(Error(data.error)):p.resolve(data.result);return;}
  if(data.event==='backend') showBackend(data);
  if(data.event==='progress') $('status').textContent=`加载本地模型 ${data.file||''} ${Math.round(data.progress||0)}%`;
  if(data.event==='embedding-progress') $('status').textContent=`浏览器正在向量化 ${data.done}/${data.total}`;
};
function rpc(type,fields={}){return new Promise((resolve,reject)=>{const id=++sequence;waiting.set(id,{resolve,reject});worker.postMessage({id,type,...fields});});}
function showBackend(c){config=c;$('backend').textContent=`${c.backend.toUpperCase()} · ${c.dtype.toUpperCase()}`;$('backend-note').textContent=c.reason||'优先使用设备 WebGPU，本地模型已就绪';}
async function api(path,body){const r=await fetch('/api'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await r.json();if(!r.ok)throw Error(data.error||r.statusText);return data;}
function notice(message){$('status').textContent=message;}
function lock(value){busy=value;document.querySelectorAll('button,input,textarea,select').forEach(e=>e.disabled=value);}
async function run(task){if(busy)return;lock(true);$('error').textContent='';try{await task();}catch(e){$('error').textContent=e.message;notice('操作未完成，请查看错误提示');}finally{lock(false);}}
function clearResults(){ $('results').replaceChildren();$('timings').textContent=''; }
async function collections(){const rows=await api('/collections');const select=$('collections');select.replaceChildren();
  for(const c of rows){const o=document.createElement('option');o.value=c.name;o.textContent=`${c.name} · ${c.count} 块 · ${c.state==='ready'?'可检索':'需刷新'}`;select.append(o);}
  if(!rows.some(c=>c.name===active))active=rows[0]?.name||'';select.value=active;localStorage.setItem('stepfun.collection',active);
  $('active-label').textContent=active||'请先创建知识库';return rows;}
async function createCollection(name){await api('/collections',{name});active=name;clearResults();await collections();return name;}
async function selectCollection(name){active=name;localStorage.setItem('stepfun.collection',name);clearResults();await collections();}
async function load(options={}){const c=await rpc('load',{options});showBackend(c);notice('模型已就绪，文档和查询均在此浏览器计算');return c;}
async function importDocuments(documents,options={}){
  const target=options.collection||active;if(!target)throw Error('请先创建知识库');
  const start=performance.now();
  const embedded=await rpc('embed',{texts:documents.map(d=>d.text),batchSize:options.batchSize||8,useCache:options.useCache!==false});showBackend(embedded.config);
  const stored=await api(`/collections/${target}/documents`,{documents:documents.map((d,i)=>({...d,vector:embedded.vectors[i],dtype:embedded.config.dtype})),refreshMode:options.refreshMode||'once'});
  const result={...stored,embeddingMs:embedded.embeddingMs,importMs:performance.now()-start,cacheHits:embedded.cacheHits,config:embedded.config};
  if(target===active){await collections();clearResults();notice(`已写入并刷新 ${documents.length} 块；当前知识库 ${stored.count} 块`);$('timings').textContent=`向量化 ${result.embeddingMs.toFixed(1)} ms · 写入 ${stored.writeMs.toFixed(1)} ms · 索引刷新 ${stored.refreshMs.toFixed(1)} ms`;}
  return result;
}
function render(result){$('results').replaceChildren();for(const [i,r] of result.results.entries()){
  const card=document.createElement('article'),h=document.createElement('h3'),p=document.createElement('p'),meta=document.createElement('small');
  h.textContent=`${i+1}. ${r.metadata?.title||'未命名文档'}`;p.textContent=r.text;meta.textContent=`seekdb 排序 · ${r.id.slice(0,12)} · ${r.metadata?.dtype||''}`;
  card.append(h,p,meta);$('results').append(card);
}if(!result.results.length)$('results').textContent='没有结果，请先导入文本。';}
async function search(query,options={}){
  const target=options.collection||active;if(!target)throw Error('请先选择知识库');const t=performance.now();
  const embedded=await rpc('embed',{texts:[query],batchSize:1,useCache:options.useCache!==false});showBackend(embedded.config);
  const data=await api(`/collections/${target}/search`,{query,vector:embedded.vectors[0],topK:options.topK||5});
  if(target===active)render(data);
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const result={...data,queryEmbeddingMs:embedded.embeddingMs,endToEndMs:performance.now()-t,config:embedded.config};
  if(target===active){$('timings').textContent=`查询向量 ${result.queryEmbeddingMs.toFixed(1)} ms · seekdb ${data.searchMs.toFixed(1)} ms · 提交到展示 ${result.endToEndMs.toFixed(1)} ms`;notice(`知识库 ${target} · 找到 ${data.results.length} 条结果`);}
  return result;
}
function chunks(text,title){const words=text.trim().split(/\s+/);const out=[];let part=[];for(const word of words){
  if(part.length>=90||part.join(' ').length+word.length>800){if(part.length)out.push(part.join(' '));part=[];}
  if(word.length>800){for(let i=0;i<word.length;i+=800)out.push(word.slice(i,i+800));}else part.push(word);
}if(part.length)out.push(part.join(' '));return out.filter(Boolean).map((text,i)=>({title:`${title} · ${i+1}`,text,source:title}));}
$('load').onclick=()=>run(()=>load({backend:$('mode').value}));
$('create').onclick=()=>run(()=>createCollection($('name').value.trim()));
$('collections').onchange=()=>run(()=>selectCollection($('collections').value));
$('refresh').onclick=()=>run(async()=>{await api(`/collections/${active}/refresh`,{});await collections();notice('索引刷新完成');});
$('import').onclick=()=>run(async()=>{let docs=[];const files=Array.from($('files').files);
  if(files.length)for(const f of files){if(f.size>1024*1024)throw Error('单文件最多 1 MB');docs.push(...chunks(await f.text(),f.name));}
  if($('text').value.trim())docs.push(...chunks($('text').value,$('title').value.trim()||'粘贴文本'));
  if(!docs.length||docs.length>128)throw Error('请导入 1–128 个文本块；可分批导入更多内容');await importDocuments(docs);
});
$('sample').onclick=()=>run(async()=>{const corpus=await fetch('/data/corpus.json').then(r=>r.json());await importDocuments(corpus.documents);});
$('search-form').onsubmit=e=>{e.preventDefault();run(()=>search($('query').value.trim()));};
window.edgeApp={load,createCollection,selectCollection,importDocuments,search,collections,api,chunks,get config(){return config;}};
run(async()=>{const h=await api('/health');$('db-status').textContent=h.version;await collections();notice('数据库已连接。创建知识库后加载模型或直接导入。');});
