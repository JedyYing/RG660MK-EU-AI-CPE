# -*- coding: utf-8 -*-
"""RG660MK 新疆口音识别优化 —— 离线口音纠错 + 标准普通话回归测试。

对应《RG660MK 智能音箱新疆口音识别优化设计 V1.0》：
  §6.3 发音混淆建模：口音近音误识别应被纠回正确设备命令；
  §7   测试集设计：口音专项集 + 标准普通话回归集（保证不退化）；
  §2   保护标准普通话基线：正常问答/天气/时间不得被误纠成设备命令。

不依赖真实音频/whisper/联网。用法：
  python3 -m pytest tests/test_accent.py -q
  或 python3 tests/test_accent.py   （plain 模式，打印 PASS/FAIL 汇总）
"""
import os
import sys
import unittest

_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.dirname(_here), os.path.join(_here, "..", "voice-assistant"), os.path.join(_here, "..")):
    if os.path.exists(os.path.join(_cand, "voice_assistant.py")):
        sys.path.insert(0, _cand)
        break
import voice_assistant as va
import accent_correct as ac


class FuzzSyllableTests(unittest.TestCase):
    """口音模糊音节：易混声母/韵母应折叠到同一等价类（设计 §6.3）。"""

    def test_retroflex_merge(self):
        # 翘舌 zh/ch/sh -> 平舌 z/c/s
        self.assertEqual(ac.fuzz_syllable("室"), "si")   # shi -> si
        self.assertEqual(ac.fuzz_syllable("视"), "si")   # shi -> si
        self.assertEqual(ac.fuzz_syllable("厨"), "cu")   # chu -> cu

    def test_nasal_merge(self):
        # 后鼻音 -> 前鼻音
        self.assertEqual(ac.fuzz_syllable("灯"), "den")   # deng -> den
        self.assertEqual(ac.fuzz_syllable("厅"), "tin")   # ting -> tin
        self.assertEqual(ac.fuzz_syllable("空"), "kon")   # kong -> kon

    def test_out_of_table_char_kept(self):
        # 表外字符必须保留为字面 token，不能凭空消失（否则会误配短命令）
        key = ac.fuzz_key("打开窗连")
        self.assertIn("连", key)  # "连"不在业务表 -> 原样保留

    def test_punctuation_skipped(self):
        self.assertEqual(ac.fuzz_key("开灯。"), ac.fuzz_key("开灯"))


class AccentCorrectionTests(unittest.TestCase):
    """口音专项集：近音误识别 -> 正确设备命令（设计 §6.3 / §7）。"""

    # ac 层近音纠错：口音误识别 -> 正确命令（含灯/窗帘/空调等业务词）
    # (口音误识别文本, 期望纠正到的命令)
    ACCENT_CASES = [
        ("打开客厅登", "打开客厅灯"),   # 登/灯 同音
        ("打开卧市灯", "打开卧室灯"),   # 市/室 翘舌平舌
        ("关闭客厅登", "关闭客厅灯"),
        ("打开厨房登", "打开厨房灯"),
        ("打开窗连", "打开窗帘"),       # 连/帘 同音(lian)
        ("关闭空挑", "关闭空调"),       # 挑/调 同音(tiao)
    ]

    def test_accent_corrected_to_command(self):
        for wrong, want in self.ACCENT_CASES:
            with self.subTest(wrong=wrong):
                fix = ac.correct_command(wrong)
                self.assertIsNotNone(fix, "应纠错: %s" % wrong)
                self.assertEqual(fix["corrected"], want)
                self.assertEqual(fix["rule"], "fuzzy_pinyin_edit")
                self.assertLessEqual(fix["distance"], 1)

    # 端到端流向 DEVICE_CONTROL 的口音用例：本系统唯一可控设备是灯泡
    # (TOOLS 无窗帘/空调工具)，故只有纠成"...灯"的命令才落 DEVICE_CONTROL；
    # 窗帘/空调纠错文本更干净，但按现有业务路由走 LLM 兜底，属预期。
    LIGHT_FLOW_CASES = ["打开客厅登", "关闭客厅登", "打开厨房登"]

    def test_accent_flows_to_device_control(self):
        # 端到端(纯逻辑): 不含"灯"字的口音误识别经 classify 应纠回并落 DEVICE_CONTROL
        for wrong in self.LIGHT_FLOW_CASES:
            with self.subTest(wrong=wrong):
                self.assertNotIn("灯", wrong)  # 确认走的是纠错路径而非直接命中
                intent, slots = va.classify(wrong)
                self.assertEqual(intent, "DEVICE_CONTROL", "%s 应纠成设备控制" % wrong)
                self.assertIn("accent_fix", slots)  # 保留溯源

    def test_correction_is_explainable(self):
        fix = ac.correct_command("打开客厅登")
        # 可解释、可回滚: 带原文/纠正/规则/距离/模糊key
        for field in ("from", "corrected", "rule", "distance", "fuzz"):
            self.assertIn(field, fix)


class NoFalsePositiveTests(unittest.TestCase):
    """红线：不误纠（设计 §14 '宁可不纠也不误纠'）。"""

    # 这些既非规范命令、也不是命令的近音，绝不能被纠成设备命令
    NON_COMMANDS = [
        "相对论是怎么回事", "讲个笑话", "今天心情不好", "帮我写封邮件",
        "我想听音乐", "给妈妈打电话", "明天要开会",
    ]

    def test_non_command_not_corrected(self):
        for text in self.NON_COMMANDS:
            with self.subTest(text=text):
                self.assertIsNone(ac.correct_command(text),
                                  "不应纠错: %s" % text)

    def test_non_command_not_device_control(self):
        for text in self.NON_COMMANDS:
            with self.subTest(text=text):
                intent, _ = va.classify(text)
                self.assertNotEqual(intent, "DEVICE_CONTROL",
                                    "%s 不应被判为设备控制" % text)

    def test_empty_and_whitespace_safe(self):
        self.assertIsNone(ac.correct_command(""))
        self.assertIsNone(ac.correct_command("   "))


class StandardMandarinRegressionTests(unittest.TestCase):
    """标准普通话回归集：口音优化不得让基线退化（设计 §2 / §9）。

    这些是已在原基线正确分类的标准普通话，接入口音纠错后分类必须不变。
    """

    REGRESSION = [
        ("今天上海什么天气", "WEATHER"),
        ("北京明天会下雨吗", "WEATHER"),
        ("今天星期几", "LOCAL_DATE_TIME"),
        ("现在几点", "LOCAL_DATE_TIME"),
        ("1+1等于几", "LOCAL_CALCULATOR"),
        ("给我把灯打开", "DEVICE_CONTROL"),
        ("开灯", "DEVICE_CONTROL"),
        ("关灯", "DEVICE_CONTROL"),
        ("相对论是怎么回事", "LLM_OR_HERMES"),
        ("今天讲讲相对论", "LLM_OR_HERMES"),
        ("时间为什么会变慢", "LLM_OR_HERMES"),
        ("", "EMPTY"),
    ]

    def test_regression_unchanged(self):
        for text, want in self.REGRESSION:
            with self.subTest(text=text):
                self.assertEqual(va.classify(text)[0], want)

    def test_standard_command_not_marked_accent_fix(self):
        # 标准命令直接命中，不应带 accent_fix 溯源（说明没走纠错路径）
        _, slots = va.classify("给我把灯打开")
        self.assertNotIn("accent_fix", slots)


class XinjiangPlaceNameTests(unittest.TestCase):
    """新疆地名进入天气城市识别（设计 §6.1）。"""

    PLACES = ["乌鲁木齐", "喀什", "伊犁", "克拉玛依", "吐鲁番", "库尔勒", "哈密"]

    def test_place_extracted_for_weather(self):
        for city in self.PLACES:
            with self.subTest(city=city):
                intent, slots = va.classify(city + "今天天气怎么样")
                self.assertEqual(intent, "WEATHER")
                self.assertEqual(slots.get("city"), city)

    def test_place_in_hotwords(self):
        hot = ac.get_hotwords()
        for city in ["乌鲁木齐", "喀什", "伊犁"]:
            self.assertIn(city, hot)


class HotwordBiasTests(unittest.TestCase):
    """热词/上下文偏置（设计 §6.1）。"""

    def test_context_scoped_hotwords(self):
        room = ac.get_hotwords("room")
        self.assertIn("卧室", room)
        self.assertNotIn("乌鲁木齐", room)  # room 上下文不含地名

    def test_whisper_prompt_nonempty(self):
        self.assertTrue(ac.whisper_prompt())
        self.assertIn("客厅灯", ac.whisper_prompt())


# ---- plain 模式：无 pytest 时也能 python3 tests/test_accent.py 跑 ----
if __name__ == "__main__":
    import unittest as _ut
    loader = _ut.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = _ut.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
