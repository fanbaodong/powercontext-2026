import { env, pipeline } from '@huggingface/transformers';
env.allowRemoteModels=false;
env.allowLocalModels=true;
env.localModelPath='/models/';
env.backends.onnx.wasm.wasmPaths='/wasm/';
env.backends.onnx.wasm.numThreads=Math.min(4,self.navigator.hardwareConcurrency||1);
let extractor,config,cache=new Map();
const send=(event,data)=>self.postMessage({event,...data});
async function load(options={}) {
  let backend=options.backend||'auto',dtype=options.dtype||'auto',reason='',adapterInfo=null;
  if(backend!=='wasm') {
    const adapter=await self.navigator.gpu?.requestAdapter({powerPreference:'high-performance'}).catch(()=>null);
    if(adapter) {
      adapterInfo={vendor:adapter.info?.vendor,architecture:adapter.info?.architecture,description:adapter.info?.description,
        shaderF16:adapter.features.has('shader-f16')};
      if(dtype==='auto'&&!adapter.features.has('shader-f16')) {backend='wasm';reason='GPU 不支持 FP16，自动降级 WASM INT8';}
      else backend='webgpu';
    } else {backend='wasm';reason='WebGPU 不可用，自动降级 WASM INT8';}
  } else reason='指定 WASM 对照测试';
  if(dtype==='auto') dtype=backend==='webgpu'?'fp16':'q8';
  if(backend==='wasm'&&dtype==='fp16') {dtype='q8';reason+='；WASM 使用 INT8';}
  if(config?.backend===backend&&config?.dtype===dtype) return {...config,loadMs:0,fetchSpanMs:0,assets:[],reused:true};
  if(extractor) await extractor.dispose();extractor=null;cache.clear();
  performance.clearResourceTimings();
  const start=performance.now();
  const create=(device,precision)=>pipeline('feature-extraction','Xenova/all-MiniLM-L6-v2',{
    device,dtype:precision,local_files_only:true,
    progress_callback:p=>send('progress',{status:p.status,file:p.file,progress:p.progress}),
  });
  try {extractor=await create(backend,dtype);}
  catch(error) {
    if(backend!=='webgpu') throw error;
    reason='WebGPU 模型初始化失败，自动降级 WASM INT8：'+String(error.message||error).slice(0,150);
    backend='wasm';dtype=options.dtype==='fp32'?'fp32':'q8';extractor=await create(backend,dtype);
  }
  const assets=performance.getEntriesByType('resource').filter(e=>e.name.includes('/models/')).map(e=>({file:new URL(e.name).pathname,durationMs:e.duration,transferBytes:e.transferSize,encodedBytes:e.encodedBodySize,startMs:e.startTime,endMs:e.responseEnd}));
  const fetchSpanMs=assets.length?Math.max(...assets.map(e=>e.endMs))-Math.min(...assets.map(e=>e.startMs)):0;
  config={backend,dtype,reason,adapterInfo,loadMs:performance.now()-start,fetchSpanMs,assets,threads:env.backends.onnx.wasm.numThreads};
  send('backend',config);return config;
}
async function embed(texts,batchSize=8,useCache=true) {
  if(!extractor) await load();
  const start=performance.now(),vectors=[],pending=[],positions=[];let cacheHits=0;
  texts.forEach((text,i)=>{if(useCache&&cache.has(text)){vectors[i]=cache.get(text);cacheHits++;}else{pending.push(text);positions.push(i);}});
  for(let i=0;i<pending.length;i+=batchSize) {
    const batch=pending.slice(i,i+batchSize);
    let output;
    try {output=await extractor(batch,{pooling:'mean',normalize:true,truncation:true,max_length:128});}
    catch(error) {
      if(config.backend!=='webgpu') throw error;
      // Runtime GPU loss/unsupported operation must preserve the usable fallback path.
      await load({backend:'wasm',dtype:'q8'});
      config.reason='WebGPU 推理失败，已重新在 WASM 计算本次全部输入';send('backend',config);
      return embed(texts,batchSize,false);
    }
    const rows=output.tolist();
    rows.forEach((row,j)=>{vectors[positions[i+j]]=row;if(useCache){if(cache.size>=512)cache.delete(cache.keys().next().value);cache.set(batch[j],row);}});
    send('embedding-progress',{done:Math.min(i+batchSize,pending.length),total:pending.length});
  }
  return {vectors,embeddingMs:performance.now()-start,cacheHits,config};
}
let queue=Promise.resolve();
self.onmessage=({data})=>{queue=queue.then(async()=>{
  try {const result=data.type==='load'?await load(data.options):await embed(data.texts,data.batchSize,data.useCache);
    self.postMessage({id:data.id,result});
  }catch(e){self.postMessage({id:data.id,error:String(e.message||e)});}
});};
