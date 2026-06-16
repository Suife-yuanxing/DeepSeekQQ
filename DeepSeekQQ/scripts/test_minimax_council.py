"""Test MiniMax in Council Skill — dry-run + fast mode live test."""
import subprocess, sys, os

SCRIPTS = os.path.expandvars(r"%USERPROFILE%\.agents\skills\council\scripts")
PLAN = os.path.join(SCRIPTS, "council-verified-20260616-002834.md")

def run(cmd_args):
    env = os.environ.copy()
    # Key already in system env via setx — ensure current process has it
    for v in ["MINIMAX_API_KEY", "DEEPSEEK_API_KEY"]:
        if v not in env:
            val = os.getenv(v, "")
            if val:
                env[v] = val
    r = subprocess.run([sys.executable] + cmd_args, cwd=SCRIPTS, env=env,
                       capture_output=True, text=True, timeout=300)
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[:2000])
    return r.returncode

print("=" * 60)
print("1) Dry-run: deepseek + minimax")
print("=" * 60)
rc = run(["council_call.py", PLAN, "--mode=fast", "--models", "deepseek,minimax", "--dry-run"])

if rc == 0:
    print("\n" + "=" * 60)
    print("2) Live test: deepseek + minimax (fast mode)")
    print("=" * 60)
    rc2 = run(["council_call.py", PLAN, "--mode=fast", "--models", "deepseek,minimax"])
    sys.exit(rc2)
else:
    print("Dry-run failed, skipping live test")
    sys.exit(rc)
