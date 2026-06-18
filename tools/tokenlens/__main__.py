"""TokenLens CLI 入口"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Windows 控制台 UTF-8 编码
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

from .config import Config
from .format_utils import format_cost, format_tokens
from .parser import Aggregator


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s | %(name)s | %(message)s",
    )


def print_banner(config: Config, jsonl_count: int, warnings: list[str]) -> None:
    """打印启动横幅"""
    print(f"""
╔══════════════════════════════════════════════╗
║           TokenLens v1.0                      ║
╚══════════════════════════════════════════════╝""")
    print(f"  数据目录: {config.data_dir} ({jsonl_count} 个 JSONL 文件)")
    llm_status = "已启用" if config.llm_enabled else "已禁用"
    print(f"  LLM 功能: {llm_status}")

    if warnings:
        print("\n  ⚠️  警告:")
        for w in warnings:
            print(f"    - {w}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TokenLens — 自建 Token 用量看板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m tools.tokenlens                          # 启动 Web 服务器 (127.0.0.1:8090)
  python -m tools.tokenlens --port 8888                # 自定义端口
  python -m tools.tokenlens --host 0.0.0.0             # 局域网访问（有安全警告）
  python -m tools.tokenlens --cli --period week        # CLI 模式
  python -m tools.tokenlens --data-dir ~/tokenlens-data
        """,
    )

    parser.add_argument("--port", type=int, default=8090, help="服务器端口 (默认: 8090)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="绑定地址 (默认: 127.0.0.1)")
    parser.add_argument("--data-dir", type=str, default=None, help="JSONL 数据目录")
    parser.add_argument("--tz", type=int, default=8, help="时区偏移 (默认: +8)")
    parser.add_argument("--cli", action="store_true", help="CLI 模式（不启动服务器）")
    parser.add_argument("--models", action="store_true", help="CLI 模式: 显示模型统计")
    parser.add_argument("--cache", action="store_true", help="CLI 模式: 显示缓存建议")
    parser.add_argument("--period", type=str, default="week", help="时间范围: day|week|month|3month|year")
    parser.add_argument("--fetch-pricing", action="store_true", help="从官网获取最新模型定价并缓存")
    parser.add_argument("--show-pricing", action="store_true", help="显示当前使用的定价表")
    parser.add_argument("--billing", action="store_true", help="从官方 API 获取真实账单（需 API Key）")
    parser.add_argument("--billing-days", type=int, default=30, help="账单查询天数 (默认: 30)")
    parser.add_argument("--verbose", action="store_true", help="详细日志")

    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("tokenlens")

    # 初始化配置
    Config.init(data_dir=args.data_dir)
    Config.tz_offset = args.tz

    # 启动验证
    try:
        warnings = Config.validate()
    except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
        print(f"❌ 配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    # 统计 JSONL 文件数
    jsonl_count = sum(1 for _ in Config.data_dir.rglob("*.jsonl"))

    # ─── 定价管理 ──────────────────────────────────────
    if args.fetch_pricing or args.show_pricing:
        from .pricing import PRICING, reload_pricing

    if args.fetch_pricing:
        print("🔍 正在从官网获取最新定价...")
        print()
        from .pricing_fetcher import get_fetcher

        fetcher = get_fetcher()
        try:
            new_pricing = fetcher.fetch_sync(force=True)
            reload_pricing()
            print("✅ 定价已更新并缓存")
            print(f"   缓存位置: {fetcher.cache_path}")
            print(f"   模型数量: {len(new_pricing)}")
            print()
        except Exception as e:
            logger.error(f"定价获取失败: {e}")
            print(f"⚠️  获取失败，使用本地缓存/默认值: {e}")
            print()

    if args.show_pricing:
        from .pricing import PRICING
        print("📋 当前定价表 (RMB / 百万 token)")
        print()
        print(f"{'模型':<30} {'输入':>8} {'缓存读':>8} {'输出':>8}")
        print("-" * 58)
        for model, prices in sorted(PRICING.items()):
            print(
                f"{model:<30} "
                f"{prices['input']:>8.4f} "
                f"{prices['cache_read']:>8.4f} "
                f"{prices['output']:>8.4f}"
            )
        print()
        # 检查缓存来源
        from .pricing import CACHE_PATH
        if CACHE_PATH.exists():
            import json, time as _time
            try:
                with open(CACHE_PATH, "r") as f:
                    c = json.load(f)
                ts = c.get("_fetched_iso", "unknown")
                print(f"📦 缓存来源: {CACHE_PATH} ({ts})")
            except Exception:
                print(f"📦 缓存来源: {CACHE_PATH}")
        else:
            print("📦 使用硬编码默认值（运行 --fetch-pricing 获取最新定价）")
        print()

    if args.fetch_pricing or args.show_pricing:
        if not args.cli and not args.models:
            # 仅定价操作，直接退出
            return

    # ─── 官方账单 ──────────────────────────────────────
    if args.billing:
        from .billing_fetcher import fetch_billing_sync

        print("🏦 正在从官方 API 获取余额...")
        print()

        ds_key = os.getenv("DEEPSEEK_API_KEY", "")
        ms_key = os.getenv("MOONSHOT_API_KEY", "") or os.getenv("KIMI_API_KEY", "")

        if not ds_key and not ms_key:
            print("⚠️  未设置任何 API Key！")
            print("   请设置: DEEPSEEK_API_KEY / MOONSHOT_API_KEY / KIMI_API_KEY")
            print()
        else:
            if ds_key:
                print(f"   ✅ DEEPSEEK_API_KEY  已设置")
            if ms_key:
                print(f"   ✅ KIMI_API_KEY      已设置")
            print()

            billing = fetch_billing_sync()

            # 计算本地估算
            from .pricing import calc_cost
            agg = Aggregator(data_dir=Config.data_dir)
            agg.scan(force=True)
            local_total = 0.0
            for r in agg._records:
                cost = calc_cost(r["model"], r["input_tokens"], r["cache_read_tokens"], r["output_tokens"])
                if cost is not None:
                    local_total += cost

            print("📊 官方余额 & 实际花费")
            print(f"   {'平台':<12} {'当前余额':>10} {'上次余额':>10} {'本次花费':>10} {'累计花费':>10}")
            print(f"   {'-'*52}")

            for platform, s in billing.platforms.items():
                if s.error:
                    print(f"   {platform:<12} {'❌ ' + s.error}")
                else:
                    print(
                        f"   {platform:<12} "
                        f"¥{s.current_balance or 0:>9.2f} "
                        f"¥{s.previous_balance or 0:>9.2f} "
                        f"¥{s.spent_since_last:>9.2f} "
                        f"¥{s.total_spent_tracked:>9.2f}"
                    )

            print()
            print(f"   💰 官方累计花费: ¥{billing.total_official_spend:.2f}")
            print(f"   📐 本地估算:     ¥{local_total:.2f}")

            if billing.is_first_run:
                print()
                print("   ⚠️  首次运行，无历史余额数据。")
                print("   下次运行时，余额变化将反映实际花费。")
                print(f"   历史文件: {Path.home() / '.tokenlens' / 'balance_history.json'}")

            if billing.discrepancy_pct is not None:
                dp = billing.discrepancy_pct
                tag = "✅" if abs(dp) < 0.2 else "⚠️ 偏差较大"
                print(f"   📏 偏差: {dp:+.1%}  {tag}")
            print()

        if not args.cli and not args.models:
            return

    if args.cli:
        # CLI 模式
        agg = Aggregator(data_dir=Config.data_dir)
        summary = agg.scan(force=True)
        print(f"扫描完成: {summary}")
        print()

        if args.models or args.cache:
            models = agg.get_models()
            print("模型                        输入          缓存读        输出          命中率    费用       消息数")
            print("-" * 100)
            for m in models:
                print(
                    f"{m['model']:<26} "
                    f"{format_tokens(m['input']):>12}  "
                    f"{format_tokens(m['cache_read']):>12}  "
                    f"{format_tokens(m['output']):>12}  "
                    f"{m['cache_hit_rate']:>5.1%}    "
                    f"{format_cost(m.get('cost') or 0):>8}    "
                    f"{m['count']:>6}"
                )

            if args.cache:
                from .advisor import get_rule_advice
                for m in models:
                    hr = m.get("cache_hit_rate", 0)
                    result = get_rule_advice(hr)
                    print(f"\n  [{m['model']}] {result['severity']} — {result['advice']}")

    else:
        # Web 服务器模式
        print_banner(Config, jsonl_count, warnings)

        # 安全警告
        if args.host != "127.0.0.1":
            print("=" * 60)
            print("⚠️  安全警告!")
            print("=" * 60)
            print()
            print("TokenLens 将监听所有网络接口!")
            print("JSONL 数据包含你的完整对话内容，请确保网络安全。")
            print()
            print(f"服务器将启动在: http://{args.host}:{args.port}")
            print()
            print("推荐使用 Tailscale 进行远程安全访问：")
            print("  tailscale serve --bg --https=8443 http://127.0.0.1:8090")
            print("=" * 60)
            print()

            # 确认提示
            confirm = input("确认启动? (yes/no): ")
            if confirm.lower() not in ("yes", "y"):
                print("已取消")
                sys.exit(0)

        # 初始化聚合器
        from .server import init_aggregator
        agg = init_aggregator(Config.data_dir)
        logger.info(f"数据扫描完成: {len(agg._records)} 条记录, {agg.total_files} 个文件")

        # 启动 uvicorn
        import uvicorn
        uvicorn.run(
            "tools.tokenlens.server:app",
            host=args.host,
            port=args.port,
            workers=1,  # 单 worker 确保聚合器单例
            log_level="info",
            access_log=True,
        )


if __name__ == "__main__":
    main()
