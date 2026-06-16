"""Probe MiniMax API for valid model names."""
import os, urllib.request, json

# Get key from registry
import subprocess
result = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "[Environment]::GetEnvironmentVariable('MINIMAX_API_KEY', 'User')"],
    capture_output=True, text=True
)
api_key = result.stdout.strip()

models = ["MiniMax-M1", "abab6.5s-chat", "minimax-m1", "abab6s-chat", "MiniMax-Text-01"]
url = "https://api.minimaxi.com/v1/chat/completions"

for m in models:
    payload = json.dumps({
        "model": m,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 10
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            print(f"✅ {m} — OK")
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())
        msg = err.get("error", {}).get("message", str(e))
        print(f"❌ {m} — {msg}")
    except Exception as e:
        print(f"⚠️ {m} — {e}")
