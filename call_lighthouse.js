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

let result = null;
let lineBuffer = '';

proc.stdout.on('data', (d) => {
  lineBuffer += d.toString();
  const lines = lineBuffer.split('\n');
  lineBuffer = lines.pop(); // 保留未完成的行
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const j = JSON.parse(line);
      if (j.id === 2) {
        result = j;
        proc.kill();
        break;
      }
    } catch (e) { /* 非 JSON 行，忽略 */ }
  }
});

proc.stderr.on('data', (d) => { console.error('STDERR:', d.toString()); });
proc.on('error', (err) => { console.error('Error:', err.message); process.exit(1); });

// Send initialize
const init = JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'cli',version:'1.0.0'}}}) + '\n';
proc.stdin.write(init);

// Send tools/call immediately after init
const call = JSON.stringify({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:toolName,arguments:JSON.parse(args)}}) + '\n';
proc.stdin.write(call);

// 超时兜底：15 秒后强制退出
setTimeout(() => {
  proc.kill();
  if (result) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.error('Timeout: no valid response received');
    process.exit(1);
  }
  process.exit(0);
}, 15000);

proc.on('close', () => {
  if (result) {
    console.log(JSON.stringify(result, null, 2));
    process.exit(0);
  }
});
