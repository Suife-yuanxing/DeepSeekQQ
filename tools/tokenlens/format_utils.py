"""数字格式化 + 时区处理"""

from datetime import datetime, timezone, timedelta


def format_tokens(n: int) -> str:
    """完整数字格式化（桌面端）"""
    return f"{n:,}"


def format_tokens_short(n: int) -> str:
    """缩写（移动端）：2,001,359,360 → 2.00B"""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    elif n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def format_cost(n: float, mobile: bool = False) -> str:
    """费用格式化"""
    if mobile:
        return f"¥{n:,.0f}"
    return f"¥{n:,.2f}"


def get_period_boundary(period: str, tz_offset: int = 8) -> datetime:
    """计算时间周期的起始边界（UTC datetime）

    period: day | week | month | 3month | year
    所有 period 对齐到本地时间 00:00，保持一致性：
    - day = 今天 00:00 本地时间 ~ now
    - week = 7 天前 00:00 本地时间 ~ now（滚动窗口，非自然周）
    - month = 30 天前 00:00 本地时间 ~ now（滚动窗口）
    - 3month = 90 天前 00:00 本地时间 ~ now
    - year = 365 天前 00:00 本地时间 ~ now
    """
    local_tz = timezone(timedelta(hours=tz_offset))
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(local_tz)

    # 所有 period 都对齐到本地时间 00:00，保持一致性
    days_map = {
        "day": 0,      # 今天 00:00
        "week": 7,     # 7 天前 00:00
        "month": 30,   # 30 天前 00:00
        "3month": 90,  # 90 天前 00:00
        "year": 365,   # 365 天前 00:00
    }
    delta_days = days_map.get(period, 7)

    # 计算本地时间的起始点（00:00），然后减去天数
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_local = start_local - timedelta(days=delta_days)
    return start_local.astimezone(timezone.utc)


def parse_timestamp(ts: str | None) -> datetime | None:
    """解析 ISO 8601 时间戳"""
    if ts is None:
        return None
    try:
        # 处理各种 ISO 8601 格式
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError, AttributeError):
        return None


def is_in_period(ts: str, period: str, tz_offset: int = 8) -> bool:
    """判断时间戳是否在指定周期内"""
    dt = parse_timestamp(ts)
    if dt is None:
        return False
    boundary = get_period_boundary(period, tz_offset)
    if boundary.tzinfo is None:
        boundary = boundary.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= boundary


def get_period_label(period: str, tz_offset: int = 8) -> dict:
    """返回周期的人类可读标签 + 日期范围

    返回: {"label": "过去7天", "start": "2026-06-10", "end": "2026-06-17"}
    """
    labels = {
        "day": "今天",
        "week": "过去7天",
        "month": "过去30天",
        "3month": "过去90天",
        "year": "过去一年",
    }

    boundary = get_period_boundary(period, tz_offset)
    local_tz = timezone(timedelta(hours=tz_offset))
    now_utc = datetime.now(timezone.utc)
    end_local = now_utc.astimezone(local_tz)
    start_local = boundary.astimezone(local_tz)

    return {
        "label": labels.get(period, period),
        "start": start_local.strftime("%Y-%m-%d"),
        "end": end_local.strftime("%Y-%m-%d"),
        "period": period,
    }


def short_id(s: str, n: int = 8) -> str:
    """截断 UUID/session ID 用于显示"""
    return s[:n] if len(s) > n else s
