import {existsSync,writeFileSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
if(!existsSync('.env')){
 writeFileSync('.env',`SEEKDB_HOST=127.0.0.1\nSEEKDB_PORT=2891\nSEEKDB_USER=root\nSEEKDB_PASSWORD=${randomBytes(30).toString('base64url')}\nAPP_PORT=8787\n`,{mode:0o600});
 console.log('Generated local .env. Credentials are not printed.');
}else console.log('Existing .env preserved.');
