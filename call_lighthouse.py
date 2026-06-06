import subprocess, json, sys, os, threading

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

# Send tools/call
call = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":tool_name,"arguments":args}}) + '\n'
proc.stdin.write(call.encode())
proc.stdin.flush()

# 动态读取 stdout，直到收到 id=2 的响应或超时
result = None
stderr_lines = []

def read_stdout():
    global result
    for line in proc.stdout:
        try:
            j = json.loads(line.decode().strip())
            if j.get('id') == 2:
                result = j
                break
        except (json.JSONDecodeError, ValueError):
            pass

def read_stderr():
    for line in proc.stderr:
        stderr_lines.append(line.decode(errors='replace'))

t_out = threading.Thread(target=read_stdout, daemon=True)
t_err = threading.Thread(target=read_stderr, daemon=True)
t_out.start()
t_err.start()

t_out.join(timeout=15)
proc.terminate()

if stderr_lines:
    err_text = ''.join(stderr_lines)[:500]
    print("STDERR:", err_text, file=sys.stderr)

if result:
    print(json.dumps(result, indent=2, ensure_ascii=False))
else:
    print("No valid response received (timeout or empty output)")
