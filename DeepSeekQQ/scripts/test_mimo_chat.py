"""测试 Mimo Chat LLM 能力的独立脚本。
用法:
  set MIMO_API_KEY=sk-xxx
  python scripts/test_mimo_chat.py
"""
import json, os, sys, time
import urllib.request
import urllib.error

# 优先从环境变量读取，fallback 到临时文件
API_KEY = os.getenv("MIMO_API_KEY", "")
if not API_KEY:
    _key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_mimo_key.tmp")
    if os.path.exists(_key_file):
        with open(_key_file) as f:
            API_KEY = f.read().strip()
BASE_URL = "https://api.xiaomimimo.com/v1"

# 要测试的 Chat 模型列表
CHAT_MODELS = ["mimo-v2.5-flash", "mimo-v2.5", "mimo-v2.5-pro"]
# ⚠️ V2 系列已于 2026-06 下线，全面切换 V2.5


def call_mimo_chat(model: str, system_prompt: str, user_prompt: str, max_tokens: int = 300) -> dict:
    """调用 Mimo Chat API，返回结果字典。"""
    t0 = time.time()
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode()

    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "api-key": API_KEY,
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            elapsed = time.time() - t0
            content = data["choices"][0]["message"]["content"]
            return {
                "model": model,
                "status": "success",
                "content": content,
                "tokens": data.get("usage", {}),
                "time_s": round(elapsed, 2),
                "finish_reason": data["choices"][0].get("finish_reason", "?"),
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return {"model": model, "status": "error", "http_code": e.code, "body": body}
    except Exception as e:
        return {"model": model, "status": "error", "error": str(e)}


def main():
    if not API_KEY:
        print("❌ 请设置 MIMO_API_KEY 环境变量")
        print("   set MIMO_API_KEY=sk-xxx")
        sys.exit(1)

    print("=" * 70)
    print("Mimo Chat LLM 能力验证")
    print(f"Base URL: {BASE_URL}")
    print(f"API Key:  {API_KEY[:12]}...{API_KEY[-4:]}")
    print("=" * 70)

    # ── 测试 1: 简单对话能力 ──
    print("\n── 测试 1: 简单对话（mimo-v2.5-pro）──")
    r = call_mimo_chat(
        "mimo-v2.5-pro",
        "你是一个AI助手。请简洁回答。",
        "请用一句话介绍你自己",
    )
    print_result(r)

    # ── 测试 2: 方案审查能力 ──
    print("\n── 测试 2: 方案审查（mimo-v2.5-pro）──")
    plan = """方案：为Web应用添加Redis缓存层
- 在API层和数据库之间插入Redis
- 缓存用户session（TTL=30min）
- 缓存热门查询结果（TTL=5min，LRU淘汰）
- 使用Redis Cluster保证高可用"""

    r = call_mimo_chat(
        "mimo-v2.5-pro",
        """你是资深架构审查专家。审查方案时关注:
1. 架构合理性
2. 实现可行性
3. 潜在风险
请用JSON格式输出: {"score": 1-10, "issues": [], "suggestions": []}""",
        f"请审查以下方案:\n\n{plan}",
        max_tokens=500,
    )
    print_result(r)

    # ── 测试 3: JSON 结构化输出验证 ──
    if r["status"] == "success":
        print("\n── 测试 3: JSON 可解析性 ──")
        try:
            # 尝试从响应中提取 JSON
            content = r["content"]
            # 找到第一个 { 和最后一个 }
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(content[start:end])
                print(f"✅ JSON 解析成功")
                print(f"   score: {parsed.get('score', '?')}")
                print(f"   issues: {len(parsed.get('issues', []))} 条")
                print(f"   suggestions: {len(parsed.get('suggestions', []))} 条")
            else:
                print("⚠️ 响应中未找到 JSON")
        except json.JSONDecodeError as e:
            print(f"⚠️ JSON 解析失败: {e}")

    # ── 测试 4: 其他模型可用性检查 ──
    print("\n── 测试 4: 其他 Chat 模型检查 ──")
    for model in CHAT_MODELS[1:]:  # mimo-v2.5, mimo-v2.5-pro
        r = call_mimo_chat(
            model,
            "请简洁回答。",
            "回复OK即可",
            max_tokens=10,
        )
        status = "✅" if r["status"] == "success" else "❌"
        time_str = f"{r.get('time_s', '?')}s" if r["status"] == "success" else ""
        print(f"   {status} {model:20s} {time_str}")

    print("\n" + "=" * 70)
    print("验证完成")


def print_result(r):
    if r["status"] == "success":
        print(f"   状态: ✅ 成功 | 耗时: {r['time_s']}s | tokens: {r['tokens']}")
        print(f"   finish_reason: {r['finish_reason']}")
        print(f"   --- 回复 ---")
        print(f"   {r['content'][:600]}")
    else:
        print(f"   状态: ❌ 失败 | {r.get('http_code', '?')} | {r.get('body', r.get('error', '?'))[:300]}")


if __name__ == "__main__":
    main()
