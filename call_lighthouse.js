const { spawn } = require('child_process');

const toolName = process.argv[2];
const args = process.argv[3] || '{}';

if (!process.env.TENCENTCLOUD_SECRET_ID || !process.env.TENCENTCLOUD_SECRET_KEY) {
  console.error('Error: TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY must be set');
  process.exit(1);
}

const proc = spawn('npx', ['-y', 'lighthouse-mcp-server'], {
  env: {
    ...process.env,
    TENCENTCLOUD_SECRET_ID: process.env.TENCENTCLOUD_SECRET_ID,
    TENCENTCLOUD_SECRET_KEY: process.env.TENCENTCLOUD_SECRET_KEY
  },
  stdio: ['pipe', 'pipe', 'pipe']
});

let buffer = '';
proc.stdout.on('data', (d) => { buffer += d.toString(); });
proc.stderr.on('data', (d) => { console.error('STDERR:', d.toString()); });

proc.on('error', (err) => { console.error('Error:', err.message); process.exit(1); });

// Send initialize
const init = JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'cli',version:'1.0.0'}}}) + '\n';
proc.stdin.write(init);

let sentCall = false;
setTimeout(() => {
  if (!sentCall) {
    sentCall = true;
    const call = JSON.stringify({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:toolName,arguments:JSON.parse(args)}}) + '\n';
    proc.stdin.write(call);
  }
}, 3000);

setTimeout(() => {
  proc.kill();
  // Extract the tools/call response
  const lines = buffer.split('\n');
  for (const line of lines) {
    try {
      const j = JSON.parse(line);
      if (j.id === 2) console.log(JSON.stringify(j, null, 2));
    } catch(e) {}
  }
  process.exit(0);
}, 20000);
