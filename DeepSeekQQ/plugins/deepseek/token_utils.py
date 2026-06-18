"""Token 估算共享工具。

为 context_optimizer 和 context_compressor 提供统一的 token 数估算，
避免重复实现导致的行为不一致。
"""


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（B21 改进版）。

    CJK 统一表意文字: ~0.7 字符/token（DeepSeek tokenizer 实测约 0.6-0.8）
    英文/数字: ~4 字符/token
    标点/空格: ~6 字符/token（开销低）
    """
    cjk = 0
    latin = 0
    other = 0
    for ch in text:
        # 覆盖广义 CJK: 基本汉字、扩展A-F、兼容汉字、日韩汉字、假名、谚文、注音
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or    # CJK 基本
            0x3400 <= cp <= 0x4DBF or    # CJK 扩展A
            0x20000 <= cp <= 0x2A6DF or  # CJK 扩展B
            0xF900 <= cp <= 0xFAFF or    # CJK 兼容
            0x3040 <= cp <= 0x30FF or    # 日文假名
            0xAC00 <= cp <= 0xD7AF or    # 韩文谚文
            0x3100 <= cp <= 0x312F or    # 注音
            0x31A0 <= cp <= 0x31BF):     # 注音扩展
            cjk += 1
        elif ch.isascii() and (ch.isalpha() or ch.isdigit()):
            latin += 1
        else:
            other += 1
    # CJK: 0.7 字符/token, 拉丁: 4 字符/token, 其他: 6 字符/token
    return max(1, int(cjk / 0.7 + latin / 4 + other / 6))
