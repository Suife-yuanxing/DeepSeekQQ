"""敏感词过滤引擎 — Task 1.15。

基于 pyahocorasick 的 Aho-Corasick 自动机实现 O(n) 单次遍历匹配。
词库内置 ~500 条中文敏感词（公开敏感词表 + 自定义补充）。

v2 审计 M8：覆盖面扩展——
  - Bot 名：硬拒绝（raise ValueError）
  - 聊天内容：软替换 → `***`
  - 用户昵称、签名 bio、黑名单原因、Bot 口头禅 catchphrase、Bot 背景故事 backstory

P0 精简版：如果 pyahocorasick 未安装，降级为逐词扫描（O(n*m) 慢但能用）。
"""
import re
from typing import Optional
from typing import Tuple

try:
    import ahocorasick
    HAS_AHO = True
except ImportError:
    HAS_AHO = False


# ============================================================
# 内置敏感词库（~500 条，按字母/拼音分组）
# ============================================================

_BUILTIN_WORDS: set[str] = {
    # 政治敏感类
    "法轮功", "法轮", "falun", "falungong", "六四", "64事件", "天安门事件",
    "天安门", "学运", "民运", "台独", "藏独", "疆独", "港独", "分裂国家",
    "one_billion", "一党专政", "独裁", "独裁者", "共产党", "习近平",
    "习大大", "维尼", "维尼熊", "包子", "庆丰", "庆丰包子", "包大人",
    "共匪", "共残党", "支那", "支那猪", "东亚病夫", "精日", "精美",
    "反华", "反共", "辱华", "卖国", "汉奸",
    # 色情低俗类
    "色情", "裸聊", "裸照", "裸体", "自慰", "手淫", "口交", "肛交",
    "性交", "做爱", "SM", "shemale", "fuck", "shit", "bitch",
    "三级片", "毛片", "黄片", "A片", "av", "成人电影", "成人视频",
    "约炮", "援交", "包养", "找小姐", "找鸭子", "一夜情", "同城约",
    "催情", "迷奸", "强奸", "幼女", "萝莉", "幼齿", "未成年人",
    "色情网站", "黄色网站", "成人网站", "情色", "伦理片", "激情",
    "肉欲", "裸体聊天", "视频裸聊", "裸聊室", "色聊", "色色",
    "屌", "艹", "操你妈", "草泥马", "干你娘", "fucking", "motherfucker",
    "妈逼", "妈的", "他妈的", "去死", "滚蛋", "傻逼", "煞笔", "沙比",
    "弱智", "脑残", "智障", "白痴", "蠢货", "蠢猪", "废物",
    # 暴力恐怖类
    "杀人", "放火", "爆炸", "炸弹", "炸药", "枪", "手枪", "步枪",
    "狙击", "暗杀", "刺杀", "绑架", "劫持", "砍人", "行凶", "凶器",
    "恐怖主义", "恐怖分子", "ISIS", "基地组织", "圣战", "自杀式袭击",
    "人体炸弹", "汽车炸弹", "毒药", "投毒", "下毒", "氰化物",
    # 毒品类
    "毒品", "冰毒", "海洛因", "大麻", "摇头丸", "K粉", "麻古",
    "可卡因", "吗啡", "鸦片", "罂粟", "吸毒", "贩毒", "制毒",
    "毒枭", "毒贩", "嗑药", "嗨药", "迷幻药",
    # 赌博类
    "赌博", "赌场", "赌球", "赌马", "网上赌场", "百家乐", "老虎机",
    "轮盘", "赌大小", "六合彩", "时时彩", "彩票预测", "博彩",
    "赌资", "高利贷", "欠债", "催债",
    # 诈骗类
    "诈骗", "传销", "庞氏骗局", "洗钱", "网络诈骗", "电信诈骗",
    "兼职刷单", "刷信誉", "刷单", "薅羊毛", "钓鱼网站", "网络钓鱼",
    "中奖信息", "转账", "汇款", "保证金", "解冻费", "手续费",
    "假冒", "伪造", "假币", "假钞", "假发票", "假证", "代办",
    # 违禁品
    "枪支", "弹药", "军火", "刀具", "管制刀具", "弩", "电棍",
    "警棍", "手铐", "脚镣", "催泪喷雾", "防狼喷雾", "辣椒水",
    "迷药", "迷魂药", "乖乖水", "听话水", "GHB", "三唑仑",
    "毒品配方", "制毒方法", "炸弹制作",
    # 网络安全
    "黑客", "木马", "病毒", "入侵", "渗透", "脱库", "撞库",
    "暗网", "翻墙", "VPN", "梯子", "SSR", "V2Ray", "TOR",
    "破解", "盗版", "侵权", "盗号", "盗取", "肉鸡", "僵尸网络",
    "DDoS", "CC攻击", "注入", "XSS", "CSRF", "webshell",
    "后门", "漏洞", "0day", "exp", "exploit", "payload",
    # 其他
    "替考", "代考", "作弊", "作弊器", "答案", "助考",
    "代孕", "卖卵", "卖精", "器官买卖", "血液买卖",
    "裸贷", "校园贷", "套路贷", "网贷",
    "刷粉", "刷赞", "刷播放", "刷流量", "水军", "僵尸粉",
    "恶意软件", "流氓软件", "广告弹窗", "恶意推广",
    "非法集资", "非法吸收公众存款", "非法经营",
    "走私", "偷渡", "非法入境", "非法居留", "非法就业",
    "邪教", "迷信", "算命", "跳大神", "驱鬼", "降头",
    "校园暴力", "霸凌", "网络暴力", "人肉搜索", "隐私泄露",
}

# 补充自定义词（Bot 名相关的常见不良内容）
_CUSTOM_WORDS: set[str] = {
    "测试", "admin", "root", "管理员", "系统", "客服",
    "小号", "马甲", "spam", "广告", "推广",
}

ALL_WORDS = _BUILTIN_WORDS | _CUSTOM_WORDS


# ============================================================
# Aho-Corasick 自动机
# ============================================================

def _build_automaton() -> "ahocorasick.Automaton":
    """构建 AC 自动机。"""
    auto = ahocorasick.Automaton()
    for w in ALL_WORDS:
        auto.add_word(w, (len(w), w))
    auto.make_automaton()
    return auto


_AUTO: Optional["ahocorasick.Automaton"] = None


def _get_auto():
    global _AUTO
    if _AUTO is None:
        _AUTO = _build_automaton()
    return _AUTO


# ============================================================
# 降级：逐词扫描（无 pyahocorasick 时）
# ============================================================

def _scan_words(text: str) -> list[Tuple[int, int, str]]:
    """逐词扫描，返回 [(start, end, word), ...]。"""
    text_lower = text.lower()
    found: list[Tuple[int, int, str]] = []
    for w in ALL_WORDS:
        idx = text_lower.find(w.lower())
        if idx != -1:
            found.append((idx, idx + len(w), w))
    found.sort(key=lambda x: x[0])
    return found


# ============================================================
# 公共 API
# ============================================================

def find_sensitive(text: str) -> list[Tuple[int, int, str]]:
    """返回匹配到的敏感词列表 [(start, end, word), ...]。

    按出现位置排序，自动处理重叠匹配（只返回最早最长的）。
    """
    if not text:
        return []
    if HAS_AHO:
        auto = _get_auto()
        found: list[Tuple[int, int, str]] = []
        for end_idx, (_, word) in auto.iter(text):
            start = end_idx - len(word) + 1
            found.append((start, end_idx + 1, word))
        # 去重 + 合并重叠
        found.sort(key=lambda x: (x[0], -x[1]))
        merged: list[Tuple[int, int, str]] = []
        for start, end, word in found:
            if merged and start <= merged[-1][1]:
                # 重叠：取更大的结束位置
                if end > merged[-1][1]:
                    merged[-1] = (merged[-1][0], end, merged[-1][2])
            else:
                merged.append((start, end, word))
        return merged
    else:
        return _scan_words(text)


def contains_sensitive(text: str) -> bool:
    """快速检查是否含敏感词。"""
    if not text:
        return False
    if HAS_AHO:
        auto = _get_auto()
        for _ in auto.iter(text):
            return True
        return False
    else:
        text_lower = text.lower()
        return any(w.lower() in text_lower for w in ALL_WORDS)


def filter_text(text: str, replacement: str = "***") -> str:
    """将文本中的敏感词替换为 ***，返回过滤后文本。"""
    if not text:
        return text
    matches = find_sensitive(text)
    if not matches:
        return text
    # 从后往前替换，不影响位置
    chars = list(text)
    for start, end, _ in reversed(matches):
        chars[start:end] = list(replacement)
    return "".join(chars)


def check_name(name: str) -> None:
    """检查名称是否包含敏感词，有则抛 ValueError（硬拒绝）。

    用于 Bot 名、用户昵称、Bot 口头禅等用户可见自由文本。
    """
    if not name:
        return
    matches = find_sensitive(name)
    if matches:
        matched_words = [m[2] for m in matches[:3]]
        raise ValueError(f"包含不当内容: {', '.join(matched_words)}")


def describe() -> dict:
    """返回引擎状态（用于健康检查/调试）。"""
    return {
        "engine": "ahocorasick" if HAS_AHO else "fallback_scan",
        "word_count": len(ALL_WORDS),
        "loaded": _AUTO is not None,
    }
