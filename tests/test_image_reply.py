"""测试 image_reply.py — 图片回复策略模块。"""
import pytest


class TestClassifyImage:
    """测试图片类型分类（关键词必须精确匹配代码中的关键词列表）。"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from plugins.deepseek.image_reply import (
            classify_image,
            IMAGE_TYPE_PHOTO_PET, IMAGE_TYPE_PHOTO_FOOD,
            IMAGE_TYPE_PHOTO_PERSON, IMAGE_TYPE_PHOTO_SCENERY,
            IMAGE_TYPE_SCREENSHOT_CHAT, IMAGE_TYPE_SCREENSHOT_WEB,
            IMAGE_TYPE_DOCUMENT, IMAGE_TYPE_UNKNOWN,
        )
        self.c = classify_image
        self.PET = IMAGE_TYPE_PHOTO_PET
        self.FOOD = IMAGE_TYPE_PHOTO_FOOD
        self.PERSON = IMAGE_TYPE_PHOTO_PERSON
        self.SCENERY = IMAGE_TYPE_PHOTO_SCENERY
        self.CHAT = IMAGE_TYPE_SCREENSHOT_CHAT
        self.WEB = IMAGE_TYPE_SCREENSHOT_WEB
        self.DOC = IMAGE_TYPE_DOCUMENT
        self.UNK = IMAGE_TYPE_UNKNOWN

    # ---- 萌宠（含"猫"或"狗"关键词）----
    def test_pet_cat(self):
        assert self.c("这只猫趴在窗台上晒太阳", "") == self.PET

    def test_pet_dog(self):
        assert self.c("一只狗在草地上奔跑", "") == self.PET

    def test_pet_priority_over_person(self):
        # "猫" 匹配萌宠，"人" 也匹配人物 — 萌宠优先级最高
        assert self.c("一只猫和一个女生在沙发上", "") == self.PET

    # ---- 美食（含"食物"、"美食"、"饭"、"菜"等）----
    def test_food(self):
        assert self.c("一份美味的食物摆在桌上", "") == self.FOOD

    def test_food_meal(self):
        assert self.c("一碗热腾腾的饭，配了两道菜", "") == self.FOOD

    def test_food_priority_over_person(self):
        # "食物" → 美食优先，"人" → 人物次之
        assert self.c("一份食物旁边坐着一个人", "") == self.FOOD

    # ---- 人物（含"人"、"脸"、"自拍"、"女"、"男"等）----
    def test_person_portrait(self):
        assert self.c("一个女生的自拍照，笑得很开心", "") == self.PERSON

    # ---- 风景（含"风景"、"天空"、"日落"等）----
    def test_scenery_sunset(self):
        assert self.c("海边的日落，天空是橙红色的", "") == self.SCENERY

    # ---- 截图（含"聊天"、"微信"、"对话"等）----
    def test_chat_screenshot(self):
        # 不含 "人" 关键词（person 在 chat 之前检查）
        assert self.c("微信聊天对话截图，群友们在讨论", "") == self.CHAT

    # ---- 网页/代码截图（含"代码"、"网页"、"python"等）----
    def test_web_screenshot_code(self):
        assert self.c("一段Python代码的截图，有一个bug", "") == self.WEB

    # ---- 文档（含"文档"、"发票"等）----
    def test_document(self):
        assert self.c("一张发票的照片，属于文档类型", "") == self.DOC

    # ---- 未知 ----
    def test_unknown(self):
        assert self.c("一张模糊的图片", "") == self.UNK

    def test_empty_vision_result(self):
        assert self.c("", "") == self.UNK

    # ---- 用户消息辅助分类 ----
    def test_user_msg_assists_classification(self):
        # 视觉结果不明确，但用户说"看看这个饭" — "饭"在 food_keywords
        assert self.c("一张图片", "看看这个饭") == self.FOOD

    def test_man_keyword_no_false_match(self):
        # "man" 已从关键词列表移除，"human" 不应误匹配为人物
        assert self.c("a humanoid robot standing in a lab", "") == self.UNK


class TestShouldAnalyzeInDetail:
    """测试详细分析判断。"""

    def test_explicit_request(self):
        from plugins.deepseek.image_reply import should_analyze_in_detail
        assert should_analyze_in_detail("帮我看看这是什么", 1) is True

    def test_analyze_keyword(self):
        from plugins.deepseek.image_reply import should_analyze_in_detail
        assert should_analyze_in_detail("分析一下这张图", 1) is True

    def test_multiple_images(self):
        from plugins.deepseek.image_reply import should_analyze_in_detail
        assert should_analyze_in_detail("发几张图", 3) is True

    def test_casual_single_image(self):
        from plugins.deepseek.image_reply import should_analyze_in_detail
        # 不含分析关键词 + 只有1张图 → 不需要详细分析
        assert should_analyze_in_detail("哈哈", 1) is False


class TestIsEmotionalShare:
    """测试情绪分享判断。"""

    def test_haha(self):
        from plugins.deepseek.image_reply import is_emotional_share
        assert is_emotional_share("哈哈这个好好笑") is True

    def test_cute(self):
        from plugins.deepseek.image_reply import is_emotional_share
        assert is_emotional_share("好可爱啊") is True

    def test_normal_message(self):
        from plugins.deepseek.image_reply import is_emotional_share
        assert is_emotional_share("帮我看看这个") is False


class TestGetImageReplyPrompt:
    """测试回复提示生成。"""

    def test_pet_reply_includes_hint(self):
        from plugins.deepseek.image_reply import get_image_reply_prompt, IMAGE_TYPE_PHOTO_PET
        prompt = get_image_reply_prompt(IMAGE_TYPE_PHOTO_PET, "一只猫", 100, "看看这个", None)
        assert "图片感知" in prompt
        assert "核心规则" in prompt

    def test_person_reply_affection_driven(self):
        from plugins.deepseek.image_reply import get_image_reply_prompt, IMAGE_TYPE_PHOTO_PERSON
        prompt_low = get_image_reply_prompt(IMAGE_TYPE_PHOTO_PERSON, "一个人", 30, "", None)
        prompt_high = get_image_reply_prompt(IMAGE_TYPE_PHOTO_PERSON, "一个人", 600, "", None)
        assert "克制" in prompt_low
        assert "大胆" in prompt_high

    def test_bot_mood_none_accepted(self):
        from plugins.deepseek.image_reply import get_image_reply_prompt, IMAGE_TYPE_PHOTO_PET
        prompt = get_image_reply_prompt(IMAGE_TYPE_PHOTO_PET, "一只猫", 100, "", None)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_global_constraint_included(self):
        from plugins.deepseek.image_reply import get_image_reply_prompt, IMAGE_TYPE_UNKNOWN
        prompt = get_image_reply_prompt(IMAGE_TYPE_UNKNOWN, "", 0, "", None)
        assert "核心规则" in prompt
        assert "只需要回复一条消息" in prompt
