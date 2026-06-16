#!/usr/bin/env python3
"""去重算法验证脚本。

使用 20 组中文 issue 标题对，人工标注是否应去重，
对比 CJK 2-gram Jaccard 算法的判定结果。

用法:
  python verify_dedup.py
"""

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SKILL_DIR))

from council_call import _keyword_jaccard, JACCARD_THRESHOLD

# ═══════════════════════════════════════════════════════════════════
# 测试数据集：20 组中文 issue 标题对 + 人工标注
# label: 1 = 应去重（语义相同/高度重叠），0 = 不应去重（不同问题）
# ═══════════════════════════════════════════════════════════════════

TEST_PAIRS = [
    # ── 应去重（语义相同或高度重叠） ──
    {
        "a": "缺少错误处理机制导致系统不稳定",
        "b": "缺少错误处理机制会导致服务崩溃",
        "label": 1,
        "reason": "核心问题相同：缺少错误处理",
    },
    {
        "a": "API密钥硬编码在配置文件中存在安全风险",
        "b": "API密钥硬编码于代码中存在安全隐患",
        "label": 1,
        "reason": "核心问题相同：API Key 硬编码",
    },
    {
        "a": "数据库连接池未配置超时时间",
        "b": "数据库连接池缺少超时配置",
        "label": 1,
        "reason": "完全同义：连接池超时配置缺失",
    },
    {
        "a": "日志系统缺少结构化输出格式",
        "b": "日志输出格式不够结构化",
        "label": 1,
        "reason": "核心问题相同：日志结构化不足",
    },
    {
        "a": "缓存策略未设置过期时间导致内存泄漏",
        "b": "缓存没有过期时间会造成内存泄漏风险",
        "label": 1,
        "reason": "完全同义：缓存过期缺失→内存泄漏",
    },
    {
        "a": "用户输入未做SQL注入防护",
        "b": "缺少SQL注入防护措施",
        "label": 1,
        "reason": "完全同义：SQL注入防护缺失",
    },
    {
        "a": "并发请求未做限流控制",
        "b": "未对并发请求进行限流",
        "label": 1,
        "reason": "完全同义：并发限流缺失",
    },
    {
        "a": "配置文件缺少环境变量覆盖机制",
        "b": "配置文件不支持环境变量覆盖",
        "label": 1,
        "reason": "完全同义：环境变量覆盖缺失",
    },
    {
        "a": "消息队列消费者没有重试逻辑",
        "b": "消息队列消费端缺少重试机制",
        "label": 1,
        "reason": "核心问题相同：消费者重试缺失",
    },
    {
        "a": "服务端口号硬编码为8080",
        "b": "端口8080硬编码无法配置",
        "label": 1,
        "reason": "完全同义：端口硬编码",
    },

    # ── 不应去重（不同问题） ──
    {
        "a": "缺少错误处理机制导致系统不稳定",
        "b": "数据库连接池配置不合理影响性能",
        "label": 0,
        "reason": "不同领域：错误处理 vs 数据库连接池",
    },
    {
        "a": "API密钥硬编码在配置文件中存在安全风险",
        "b": "API接口缺少认证鉴权机制",
        "label": 0,
        "reason": "不同问题：Key存储 vs 认证机制",
    },
    {
        "a": "日志系统缺少结构化输出格式",
        "b": "日志文件没有轮转策略导致磁盘占满",
        "label": 0,
        "reason": "不同子问题：格式 vs 轮转",
    },
    {
        "a": "缓存策略未设置过期时间导致内存泄漏",
        "b": "没有使用分布式缓存导致单点瓶颈",
        "label": 0,
        "reason": "不同方向：过期策略 vs 架构选型",
    },
    {
        "a": "用户输入未做SQL注入防护",
        "b": "用户密码未做哈希存储",
        "label": 0,
        "reason": "不同安全问题：注入防护 vs 密码存储",
    },
    {
        "a": "并发请求未做限流控制",
        "b": "并发请求处理性能不足需优化",
        "label": 0,
        "reason": "不同问题：限流策略 vs 性能优化",
    },
    {
        "a": "微服务间通信使用HTTP明文传输",
        "b": "HTTP通信超时时间设置过长",
        "label": 0,
        "reason": "不同关注点：安全(加密) vs 可靠性(超时)",
    },
    {
        "a": "单点登录Token过期时间过短影响体验",
        "b": "Token刷新机制存在竞态条件",
        "label": 0,
        "reason": "不同问题：过期时长 vs 刷新竞态",
    },
    {
        "a": "监控指标采集频率过低导致告警延迟",
        "b": "告警通知渠道单一只有邮件",
        "label": 0,
        "reason": "不同环节：采集 vs 通知",
    },
    {
        "a": "部署流程依赖手动操作容易出错",
        "b": "CI/CD流水线缺少代码质量检查门禁",
        "label": 0,
        "reason": "不同问题：手动部署 vs 质量门禁",
    },
]


def verify_threshold(pairs: list[dict], threshold: float) -> dict:
    """给定 Jaccard 阈值，计算准确率/召回率/F1。"""
    tp = tn = fp = fn = 0

    for pair in pairs:
        sim = _keyword_jaccard(pair["a"], pair["b"])
        predicted = 1 if sim > threshold else 0
        actual = pair["label"]

        if predicted == 1 and actual == 1:
            tp += 1
        elif predicted == 1 and actual == 0:
            fp += 1
        elif predicted == 0 and actual == 1:
            fn += 1
        else:
            tn += 1

    total = len(pairs)
    accuracy = (tp + tn) / total if total > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "threshold": threshold,
        "total": total,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def main():
    print("=" * 65)
    print("🔬 Council 去重算法验证 — CJK 2-gram Jaccard")
    print("=" * 65)
    print(f"\n测试数据集: {len(TEST_PAIRS)} 组中文 issue 标题对")
    print(f"应去重: {sum(1 for p in TEST_PAIRS if p['label'] == 1)} 组")
    print(f"不应去重: {sum(1 for p in TEST_PAIRS if p['label'] == 0)} 组")

    # ── 逐对详情 ──
    print("\n" + "-" * 65)
    print(f"{'标题A':　<30s} │ {'标题B':　<30s} │ {'Sim':>6} │ {'预期':>4} │ {'结果':>4}")
    print("-" * 65)

    errors = []
    for i, pair in enumerate(TEST_PAIRS):
        sim = _keyword_jaccard(pair["a"], pair["b"])
        predicted = 1 if sim > JACCARD_THRESHOLD else 0
        status = "✅" if predicted == pair["label"] else "❌"

        if predicted != pair["label"]:
            errors.append({
                "index": i,
                "a": pair["a"],
                "b": pair["b"],
                "similarity": round(sim, 4),
                "expected": pair["label"],
                "predicted": predicted,
                "reason": pair["reason"],
            })

        # 截断显示
        a_short = pair["a"][:28] + ".." if len(pair["a"]) > 30 else pair["a"]
        b_short = pair["b"][:28] + ".." if len(pair["b"]) > 30 else pair["b"]
        print(f"{a_short:<30s} │ {b_short:<30s} │ {sim:>6.4f} │ {'去重' if pair['label'] else '保留':>4s} │ {status} {'去重' if predicted else '保留'}")

    # ── 阈值扫描 ──
    print("\n" + "=" * 65)
    print("📊 阈值扫描结果")
    print("=" * 65)
    print(f"{'阈值':>8} │ {'准确率':>8} │ {'精确率':>8} │ {'召回率':>8} │ {'F1':>8} │ {'TP':>4} {'TN':>4} {'FP':>4} {'FN':>4}")
    print("-" * 65)

    best = None
    for t in [0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9]:
        r = verify_threshold(TEST_PAIRS, t)
        marker = " ⭐" if best is None or r["f1"] > best["f1"] else ""
        print(f"{r['threshold']:>8.2f} │ {r['accuracy']:>8.4f} │ {r['precision']:>8.4f} │ {r['recall']:>8.4f} │ {r['f1']:>8.4f} │ {r['tp']:>4} {r['tn']:>4} {r['fp']:>4} {r['fn']:>4}{marker}")
        if best is None or r["f1"] > best["f1"]:
            best = r

    # ── 当前阈值的报告 ──
    current = verify_threshold(TEST_PAIRS, JACCARD_THRESHOLD)
    print(f"\n{'=' * 65}")
    print(f"📋 当前阈值 ({JACCARD_THRESHOLD}) 详细报告")
    print(f"{'=' * 65}")
    print(f"  准确率 (Accuracy):  {current['accuracy']:.2%}")
    print(f"  精确率 (Precision): {current['precision']:.2%}")
    print(f"  召回率 (Recall):    {current['recall']:.2%}")
    print(f"  F1 分数:           {current['f1']:.2%}")
    print(f"  TP={current['tp']}  TN={current['tn']}  FP={current['fp']}  FN={current['fn']}")

    # ── 误判详情 ──
    if errors:
        print(f"\n{'=' * 65}")
        print(f"⚠️  误判详情 ({len(errors)} 组)")
        print(f"{'=' * 65}")
        for e in errors:
            direction = "漏去重（应合并但未合并）" if e["expected"] == 1 else "误去重（不应合并但合并了）"
            print(f"\n  [{direction}] Sim={e['similarity']:.4f}")
            print(f"    A: {e['a']}")
            print(f"    B: {e['b']}")
            print(f"    原因: {e['reason']}")

        print(f"\n  💡 建议：当前纯 N-gram Jaccard 无法捕捉语义等价。")
        print(f"     对中文场景，可考虑在 Round 3 中引入 LLM 语义去重，")
        print(f"     或用 Sentence-BERT (paraphrase-multilingual) 作为辅助判定。")
    else:
        print(f"\n  🎉 当前阈值 ({JACCARD_THRESHOLD}) 无误判！")

    # ── 最佳阈值推荐 ──
    if best:
        print(f"\n{'=' * 65}")
        print(f"🏆 最佳阈值: {best['threshold']} (F1={best['f1']:.2%})")
        print(f"{'=' * 65}")

    return current


if __name__ == "__main__":
    main()
