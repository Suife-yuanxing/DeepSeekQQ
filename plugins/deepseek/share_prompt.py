"""分享相关的 Prompt 生成逻辑。"""
import re
from typing import Any
from typing import Dict
from typing import List

from .share_parser import _is_valid_share


def format_shares_for_prompt(shares: List[Dict[str, Any]], user_msg: str = "") -> str:
    valid_shares = [s for s in shares if _is_valid_share(s)]
    if not valid_shares:
        return ""
    multi_keywords = ["分别", "都讲", "全部", "每个", "这些", "讲讲"]
    want_multi = any(kw in user_msg for kw in multi_keywords)
    target = valid_shares[-3:] if want_multi else valid_shares[-1:]
    lines = ["【用户最近分享的外部内容】"]
    for i, s in enumerate(target, 1):
        lines.append(f"{i}. [{s['type']}] {s['source']}")
        if s.get('summary'):
            lines.append(f"   摘要: {s['summary'][:400]}")
        if s.get('needs_paste') and s.get('platform') == '小黑盒':
            lines.append("   ⚠️ 小黑盒的内容网页端无法自动读取。请用户把正文复制粘贴过来，我再帮你分析~")
        elif s.get('fetch_failed'):
            # 内容抓取失败，明确告诉 LLM 不要编造
            lines.append("   ❌ 该链接内容无法读取（可能是视频或需要登录）。直接告诉用户「我这边打不开这个链接诶」或「没看到内容哦」，绝对不要编造任何内容！")
        elif s.get('platform') == 'douyin':
            lines.append("   📹 这是一个抖音视频，基于标题和描述主动分析/讨论这个视频，不要只说「看到了」或「收到」。")
        elif s.get('platform') == 'bilibili' and s.get('restricted'):
            lines.append("   📹 这是一个B站视频，基于标题和描述主动分析/讨论这个视频，不要只说「看到了」或「收到」。")
        elif s.get('restricted'):
            lines.append(
                f"   ⚠️ 该内容来自{s.get('platform', '第三方平台')}，网页端无法获取完整正文，需要登录APP查看。请基于标题和自身知识回答，不要编造正文细节。"
            )
        if s.get('comments'):
            lines.append(f"   热评: {s['comments'][:300]}")
    lines.append(
        "注意：如果用户接下来的问题与上述内容明显相关，请基于这些内容结合上下文回答；如果不相关，请正常聊天，不必刻意提及。"
    )
    # 图片回复行为指引
    has_image = any(s.get("type") == "图片" for s in target)
    if has_image:
        lines.append(
            "【重要】你已经通过视觉模型看到了这张图片的详细内容（见上方摘要）。"
            "绝对不要说「我看不到图片」「我不支持查看图片」「我没有这个功能」——你已经看到了！\n"
            "图片回复要求：不要说「我看到了一张图片」这种空话。"
            "要对图片内容做出具体反应——如果有趣就调侃，如果好看就夸，"
            "如果有文字就评论文字内容，像朋友发图给你看一样自然回复。"
        )
    # 表情/贴纸行为指引
    has_sticker = any(s.get("type") == "表情" for s in target)
    if has_sticker:
        lines.append(
            "【表情回复指引】用户刚刚发了一个表情/贴纸。请注意：\n"
            "1. 根据聊天上下文判断这个表情是否合适——如果刚才在聊严肃话题，用户突然发搞笑表情，你可以调侃这一点\n"
            "2. 不要干巴巴地描述表情内容（比如「你发了一个笑脸」），要对表情做出反应\n"
            "3. 如果表情的情绪和当前话题相符，可以顺着话题继续聊\n"
            "4. 如果适合，你也可以用 [sticker:情绪] 回一个表情"
        )
    return "\n".join(lines)


# ==================== 专业分析模式 ====================

def _extract_keywords(text: str) -> set:
    stopwords = {
        "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
        "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
        "自己", "这", "那"
    }
    words = re.findall(r'[\u4e00-\u9fff]{2,6}', text[:500])
    return set(w for w in words if w not in stopwords and len(w) > 1)


def _check_articles_related(shares: List[Dict[str, Any]]) -> bool:
    if len(shares) < 2:
        return False
    texts = [s.get("summary", "") for s in shares[-2:]]
    if len(texts) < 2 or not all(texts):
        return False
    kw1 = _extract_keywords(texts[0])
    kw2 = _extract_keywords(texts[1])
    if not kw1 or not kw2:
        return False
    intersection = kw1 & kw2
    return len(intersection) >= 3 or (len(intersection) / min(len(kw1), len(kw2))) > 0.15


def build_analysis_prompt(shares: List[Dict[str, Any]], user_question: str) -> str:
    valid_shares = [s for s in shares if _is_valid_share(s)]
    if not valid_shares:
        return ""

    comment_keywords = ["评论", "评论区", "留言", "网友", "热评", "高赞", "大家怎么说", "怎么看评论"]
    ask_comment = any(kw in user_question for kw in comment_keywords)

    needs_paste = any(s.get("needs_paste") and s.get("platform") == "小黑盒" for s in valid_shares)
    if needs_paste:
        return "[小黑盒内容需要用户粘贴正文后才能分析]"

    is_related = _check_articles_related(valid_shares)
    target_shares = valid_shares[-2:] if is_related else valid_shares[-1:]

    content_blocks = []
    has_restricted = False
    for i, s in enumerate(target_shares, 1):
        block = f"【内容{i}】类型：{s.get('type', '未知')} | 来源：{s.get('source', '未知')}"
        if s.get("platform") == "douyin":
            block += "\n📹 状态：这是一个抖音视频，仅有标题和描述可用。主动分析视频主题回复，可以讨论、吐槽、发表看法。"
        elif s.get("platform") == "bilibili" and s.get("restricted"):
            block += "\n📹 状态：这是一个B站视频，仅有标题和描述可用。主动分析视频主题回复，可以讨论、吐槽、发表看法。"
        elif s.get("restricted") and not s.get("needs_paste"):
            block += f"\n⚠️ 状态：该内容来自{s.get('platform', '第三方平台')}，网页端无法获取完整正文，仅有标题和描述。"
            block += f"\n标题描述：{s.get('summary', '')[:300]}"
            has_restricted = True
        else:
            block += f"\n摘要：{s.get('summary', '')[:600]}"
        if ask_comment and s.get("comments"):
            block += f"\n评论区精选：{s['comments'][:400]}"
        content_blocks.append(block)

    relation_hint = ""
    if is_related and len(target_shares) > 1:
        relation_hint = "\n注意：上述两篇内容有关联，请做对比分析或联动解读，指出它们的共同点和差异。"

    comment_hint = ""
    if ask_comment:
        comment_hint = "\n用户特别关注了评论区/网友观点，请结合上述摘要和评论区内容回答，如果评论内容不足，请诚实说明。"

    restricted_hint = ""
    if has_restricted:
        restricted_hint = (
            "\n⚠️ 重要：部分内容因平台限制无法获取正文，请诚实告知用户'这个链接需要登录APP才能看完整内容'，"
            "然后基于标题和自身知识做简要回答，绝对不要编造正文细节。"
        )

    content_text = "\n\n".join(content_blocks)
    compare_hint = "如果有多篇内容，请做对比或联动分析，不要孤立看待每篇" if is_related else "聚焦核心论点，深入剖析"
    prompt = (
        f"【分析任务】用户分享了 {len(target_shares)} 个内容，请基于以下材料回答用户问题。\n\n"
        f"{content_text}{relation_hint}{comment_hint}{restricted_hint}\n\n"
        f"用户的问题：{user_question}\n\n"
        f"要求：\n"
        f"1. 先基于上述材料做客观、有条理的分析（分点或分段）\n"
        f"2. 分析要具体，引用材料中的细节和数据，不要泛泛而谈\n"
        f"3. {compare_hint}\n"
        f"4. 分析完后，用你猫娘的语气做一句简短个性化点评（调侃、吐槽、认同都可以）\n"
        f"5. 整体语气仍然是你在聊天，但分析部分要专业、有信息量、有深度\n"
        f"6. 如果材料不足以下结论，请诚实说明，不要编造"
    )
    return prompt
