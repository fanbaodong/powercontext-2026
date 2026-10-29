import {SeekdbClient} from 'seekdb';
import {spawn} from 'node:child_process';
// A fresh seekdb container needs initialization time. Never remove its volume to retry.
for(let attempt=0;attempt<40;attempt++){
 const c=new SeekdbClient({host:process.env.SEEKDB_HOST||'127.0.0.1',port:Number(process.env.SEEKDB_PORT||2891),
   user:process.env.SEEKDB_USER||'root',password:process.env.SEEKDB_PASSWORD||'',database:'test'});
 let ready=false;
 try{await c.execute('SELECT 1');ready=true;}
 catch(e){if(attempt===39)throw e;if(attempt===0)console.log('Waiting for local seekdb initialization…');}
 finally{await c.close().catch(()=>{});}
 if(ready)break;
 await new Promise(resolve=>setTimeout(resolve,3000));
}
const child=spawn(process.execPath,['server/index.js','--production'],{stdio:'inherit'});
child.on('exit',code=>process.exit(code??1));
process.on('SIGINT',()=>child.kill('SIGINT'));
process.on('SIGTERM',()=>child.kill('SIGTERM'));
