import subprocess, json, sys, time, os

tool_name = sys.argv[1]
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

secret_id = os.environ.get('TENCENTCLOUD_SECRET_ID', '')
secret_key = os.environ.get('TENCENTCLOUD_SECRET_KEY', '')
if not secret_id or not secret_key:
    print("Error: TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY must be set", file=sys.stderr)
    sys.exit(1)

proc = subprocess.Popen(
    ['npx.cmd', '-y', 'lighthouse-mcp-server'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env={
        **os.environ,
        'TENCENTCLOUD_SECRET_ID': secret_id,
        'TENCENTCLOUD_SECRET_KEY': secret_key
    }
)

# Send initialize
init = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0.0"}}}) + '\n'
proc.stdin.write(init.encode())
proc.stdin.flush()

time.sleep(3)

# Send tools/call
call = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":tool_name,"arguments":args}}) + '\n'
proc.stdin.write(call.encode())
proc.stdin.flush()

time.sleep(10)
proc.terminate()

stdout = proc.stdout.read().decode()
stderr = proc.stderr.read().decode()

if stderr:
    print("STDERR:", stderr[:500], file=sys.stderr)

for line in stdout.split('\n'):
    try:
        j = json.loads(line)
        if j.get('id') == 2:
            print(json.dumps(j, indent=2, ensure_ascii=False))
    except:
        pass

if not stdout.strip():
    print("No output received")
