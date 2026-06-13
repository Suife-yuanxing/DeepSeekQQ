"""表情包标签重分配脚本 — 解决 cute 标签 70% 占比问题。

目标分布：cute ~25, love ~12, shy ~10, happy ~10, default ~8, funny ~6, sad ~5
策略：基于场景信息（卖萌/撒娇/无辜/可爱/日常）重新分配 cute 标签。
"""
import json
import random
import copy
import sys

TAG_FILE = sys.argv[1] if len(sys.argv) > 1 else "data/stickers/sticker_tags.json"

with open(TAG_FILE, encoding="utf-8") as f:
    data = json.load(f)

random.seed(42)
original = copy.deepcopy(data)

# 收集所有 cute 标签文件
cute_files = []
for fn, entry in data.items():
    if isinstance(entry, dict) and "cute" in entry.get("tags", []):
        cute_files.append((fn, entry.get("scenes", [])))


def cute_priority(item):
    """有卖萌的优先保留为 cute，有撒娇的优先移走。"""
    _, scenes = item
    s = set(scenes)
    score = 0
    if "卖萌" in s:
        score += 10
    if "可爱" in s:
        score += 5
    if "无辜" in s:
        score += 3
    if "撒娇" in s:
        score -= 2
    if "日常" in s and len(s) <= 2:
        score -= 5
    return -score  # 降序


cute_files.sort(key=cute_priority)

# 目标数量
targets = {"cute": 25, "love": 12, "shy": 10, "happy": 10, "default": 8, "funny": 6, "sad": 5}
counts = {k: 0 for k in targets}

for fn, scenes in cute_files:
    s = set(scenes)

    # 保留 cute：有卖萌或有可爱且无撒娇
    if counts["cute"] < targets["cute"] and (
        "卖萌" in s or ("可爱" in s and "撒娇" not in s)
    ):
        counts["cute"] += 1
        continue

    # → love：撒娇 + (可爱或无辜)
    if counts["love"] < targets["love"] and "撒娇" in s and ("可爱" in s or "无辜" in s):
        data[fn]["tags"] = ["love"]
        counts["love"] += 1
        continue

    # → shy：(无辜+日常) 或 (撒娇且无卖萌)
    if counts["shy"] < targets["shy"] and (
        ("无辜" in s and "日常" in s) or ("撒娇" in s and "卖萌" not in s)
    ):
        data[fn]["tags"] = ["shy"]
        counts["shy"] += 1
        continue

    # → default：纯日常
    if counts["default"] < targets["default"] and "日常" in s and "卖萌" not in s:
        data[fn]["tags"] = ["default"]
        counts["default"] += 1
        continue

    # → happy：有可爱
    if counts["happy"] < targets["happy"] and "可爱" in s:
        data[fn]["tags"] = ["happy"]
        counts["happy"] += 1
        continue

    # → funny：有卖萌
    if counts["funny"] < targets["funny"] and "卖萌" in s:
        data[fn]["tags"] = ["funny"]
        counts["funny"] += 1
        continue

    # → sad：有无辜
    if counts["sad"] < targets["sad"] and "无辜" in s:
        data[fn]["tags"] = ["sad"]
        counts["sad"] += 1
        continue

    # 剩余保持 cute
    counts["cute"] += 1

# 统计新分布
tag_count = {}
for fn, entry in data.items():
    if isinstance(entry, dict):
        for t in entry.get("tags", ["default"]):
            tag_count[t] = tag_count.get(t, 0) + 1

print("=== 新分布 ===")
for k, v in sorted(tag_count.items(), key=lambda x: -x[1]):
    old = sum(1 for e in original.values() if isinstance(e, dict) and k in e.get("tags", []))
    delta = v - old
    sign = "+" if delta > 0 else ""
    print(f"  {k}: {v}张 (原{old}, {sign}{delta})")

# 写入
with open(TAG_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n已写入 {TAG_FILE}")

# 变更明细
print("\n=== 变更明细 ===")
changes = 0
for fn in data:
    old_t = original[fn].get("tags", ["default"])[0] if fn in original and isinstance(original[fn], dict) else "default"
    new_t = data[fn].get("tags", ["default"])[0] if isinstance(data[fn], dict) else "default"
    if old_t != new_t:
        scenes = data[fn].get("scenes", []) if isinstance(data[fn], dict) else []
        print(f"  {fn}: {old_t} -> {new_t}  scenes={scenes}")
        changes += 1
print(f"\n共变更 {changes} 张")
