#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""RG660MK 新疆口音近音纠错 —— 纯标准库，无第三方依赖。

对应《RG660MK 智能音箱新疆口音识别优化设计 V1.0》：
  §6.1 热词/上下文偏置：get_hotwords() 产出送 whisper 的偏置词表；
  §6.3 发音混淆建模：对"控制类短句"在意图层做受限拼音编辑距离纠错，
        用可解释的口音混淆规则把 ASR 近音误识别拉回已知设备命令。

红线（照抄设计 §6.3 / §14）：
  - 只对控制类短句纠错，绝不改开放域问答文本（避免把正常词错改成业务词）；
  - 混淆规则是"待验证维度"，最终应由真实录音统计确认，不是拍脑袋定死；
  - 纠错必须可解释、可回滚：每次纠错都返回命中的规则和编辑距离。

设计取舍：设备端不装 pypinyin，这里内置一张"业务词表内字符"的拼音映射
（只覆盖设备词/房间词/命令词/新疆地名，符合 §6.1"只加实际业务需要的词"）。
字符不在表内时按整体不可纠错处理，宁可不纠也不误纠。

CLI:
  accent_correct.py selftest        跑内置用例（离线，不碰任何设备）
  accent_correct.py "开灯"          对单句试纠错，打印结果与命中规则
"""
import sys

# ---------------------------------------------------------------------------
# 1. 业务词表内字符的拼音映射（声母, 韵母）。仅覆盖设备/房间/命令/新疆地名用字。
#    韵母不带声调——口音优化关注音节混淆，声调在近音纠错里不作硬约束。
# ---------------------------------------------------------------------------
PINYIN = {
    # 命令动词
    "打": ("d", "a"), "开": ("k", "ai"), "关": ("g", "uan"), "闭": ("b", "i"),
    "调": ("t", "iao"), "高": ("g", "ao"), "低": ("d", "i"), "增": ("z", "eng"),
    "大": ("d", "a"), "减": ("j", "ian"), "小": ("x", "iao"), "暂": ("z", "an"),
    "停": ("t", "ing"), "继": ("j", "i"), "续": ("x", "u"), "设": ("sh", "e"),
    "置": ("zh", "i"), "切": ("q", "ie"), "换": ("h", "uan"), "把": ("b", "a"),
    "给": ("g", "ei"), "我": ("w", "o"), "一": ("y", "i"), "下": ("x", "ia"),
    # 设备词
    "客": ("k", "e"), "厅": ("t", "ing"), "灯": ("d", "eng"), "卧": ("w", "o"),
    "室": ("sh", "i"), "厨": ("ch", "u"), "房": ("f", "ang"), "卫": ("w", "ei"),
    "生": ("sh", "eng"), "间": ("j", "ian"), "阳": ("y", "ang"), "台": ("t", "ai"),
    "书": ("sh", "u"), "空": ("k", "ong"), "风": ("f", "eng"),
    "扇": ("sh", "an"), "窗": ("ch", "uang"), "帘": ("l", "ian"), "摄": ("sh", "e"),
    "像": ("x", "iang"), "头": ("t", "ou"), "电": ("d", "ian"), "视": ("sh", "i"),
    "插": ("ch", "a"), "座": ("z", "uo"), "加": ("j", "ia"), "湿": ("sh", "i"),
    "器": ("q", "i"), "扫": ("s", "ao"), "地": ("d", "i"), "机": ("j", "i"),
    "人": ("r", "en"),
    # 房间词补充
    "主": ("zh", "u"), "次": ("c", "i"), "餐": ("c", "an"),
    # 与设备词同音的常见 ASR 近音字（供 fuzzy 命中，不新增语义）
    "登": ("d", "eng"), "灯2": ("d", "eng"), "市": ("sh", "i"),
    # 新疆及常用地名用字
    "乌": ("w", "u"), "鲁": ("l", "u"), "木": ("m", "u"), "齐": ("q", "i"),
    "喀": ("k", "a"), "什": ("sh", "i"), "伊": ("y", "i"), "犁": ("l", "i"),
    "克": ("k", "e"), "拉": ("l", "a"), "玛": ("m", "a"), "依": ("y", "i"),
    "吐": ("t", "u"), "番": ("f", "an"), "阿": ("a", ""), "苏": ("s", "u"),
    "和": ("h", "e"), "田": ("t", "ian"), "库": ("k", "u"), "尔": ("e", "r"),
    "勒": ("l", "e"), "昌": ("ch", "ang"), "吉": ("j", "i"), "哈": ("h", "a"),
    "密": ("m", "i"), "石": ("sh", "i"), "河": ("h", "e"), "子": ("z", "i"),
    "温": ("w", "en"), "度": ("d", "u"), "亮": ("l", "iang"),
}


def hanzi_pinyin(ch):
    """返回单字 (声母, 韵母)；表外字符返回 None（视为不可纠错）。"""
    return PINYIN.get(ch)


# ---------------------------------------------------------------------------
# 2. 新疆口音混淆规范化：把易混声母/韵母折叠到同一"模糊等价类"。
#    这些是设计 §6.3 列出的待验证维度——上真机后应按混淆矩阵统计校准。
# ---------------------------------------------------------------------------
def fuzz_initial(sm):
    """声母模糊化：翘舌/平舌合并、n/l 合并、f/h 合并（西北官话常见）。"""
    m = {
        "zh": "z", "ch": "c", "sh": "s",   # 翘舌 -> 平舌
        "n": "l", "l": "l",                 # n/l 不分 -> 统一 l
        "f": "h", "h": "h",                 # f/h 相混（如"发/花"）
        "r": "l",                            # r 声母弱化（部分口音近 l/y）
    }
    return m.get(sm, sm)


def fuzz_final(ym):
    """韵母模糊化：前后鼻音合并、部分单韵母合并。"""
    m = {
        "ang": "an", "eng": "en", "ing": "in", "ong": "on",  # 后鼻 -> 前鼻
        "uang": "uan", "iang": "ian", "iong": "ion",
    }
    return m.get(ym, ym)


def fuzz_syllable(ch):
    """单字 -> 口音模糊音节串（如 "增" zeng -> "zen"）。表外字返回原字符。"""
    p = hanzi_pinyin(ch)
    if p is None:
        return ch
    sm, ym = p
    return fuzz_initial(sm) + fuzz_final(ym)


def fuzz_key(text):
    """整串 -> 模糊音节序列。

    表外字符不丢弃，保留为字符本身作为独立 token——否则它们会"凭空消失"，
    让含噪声字的短句误配到更短的命令（如"打开窗连"错纠成"打开灯"）。
    保留后它们必须参与匹配，宁可不纠也不误纠（设计 §14）。
    只跳过标点与空白（它们对音节无意义）。
    """
    key = []
    for c in text:
        if c.isspace() or c in "，,。.!！?？、；;：:":
            continue
        p = hanzi_pinyin(c)
        key.append(fuzz_syllable(c) if p is not None else c)
    return key


# ---------------------------------------------------------------------------
# 3. 编辑距离（音节级 Levenshtein）
# ---------------------------------------------------------------------------
def _edit_distance(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# 4. 已知控制命令短语（§6.3：只对控制类短句做受限纠错）
#    结构：规范文本 -> 该文本的模糊音节 key。运行时按当前上下文可裁剪。
# ---------------------------------------------------------------------------
CONTROL_PHRASES = [
    "开灯", "关灯", "打开灯", "关闭灯",
    "打开客厅灯", "关闭客厅灯", "打开卧室灯", "关闭卧室灯",
    "打开厨房灯", "打开书房灯", "打开阳台灯",
    "打开空调", "关闭空调", "打开风扇", "关闭风扇",
    "打开窗帘", "关闭窗帘", "打开电视", "关闭电视",
    "打开摄像头", "关闭摄像头",
    "调高温度", "调低温度", "调高亮度", "调低亮度",
]

# 预计算每个命令的模糊 key，避免每轮重复算
_PHRASE_KEYS = [(p, fuzz_key(p)) for p in CONTROL_PHRASES]


def correct_command(text, max_dist=1, phrases=None):
    """对疑似控制命令做口音近音纠错。

    仅当 text 的模糊音节序列与某个已知命令的编辑距离 <= max_dist 时，
    才把 text 纠成该命令。返回 dict：
      {"corrected": 命中命令, "distance": d, "rule": "fuzzy_pinyin_edit",
       "from": 原文, "fuzz": 模糊key}
    无命中返回 None（调用方应保持原文，转 LLM/Hermes 兜底）。

    max_dist=1 是保守默认：只纠"差一个音节"的近音误识别，
    避免把不相干的话硬拽成设备命令（§14 红线）。
    """
    key = fuzz_key(text)
    if not key:
        return None
    table = _PHRASE_KEYS if phrases is None else [(p, fuzz_key(p)) for p in phrases]
    best = None
    for phrase, pkey in table:
        # 长度差已超过阈值就跳过，省去无谓计算
        if abs(len(key) - len(pkey)) > max_dist:
            continue
        d = _edit_distance(key, pkey)
        if d <= max_dist and (best is None or d < best[1]):
            best = (phrase, d)
            if d == 0:
                break
    if best is None:
        return None
    # 与已知命令完全同音（d==0）且原文就是规范命令，无需纠错
    if best[1] == 0 and best[0] == text:
        return None
    return {"corrected": best[0], "distance": best[1], "rule": "fuzzy_pinyin_edit",
            "from": text, "fuzz": "".join(key)}


# ---------------------------------------------------------------------------
# 5. 热词/上下文偏置词表（§6.1）——供主程序拼进 whisper 的 prompt
# ---------------------------------------------------------------------------
HOTWORDS = {
    "device": ["客厅灯", "卧室灯", "厨房灯", "书房灯", "阳台灯",
               "空调", "风扇", "窗帘", "摄像头", "电视", "加湿器"],
    "room": ["客厅", "卧室", "主卧", "次卧", "厨房", "卫生间", "阳台", "书房", "餐厅"],
    "verb": ["打开", "关闭", "调高", "调低", "暂停", "继续", "切换"],
    "place": ["乌鲁木齐", "喀什", "伊犁", "克拉玛依", "吐鲁番", "阿克苏",
              "和田", "库尔勒", "昌吉", "哈密", "石河子"],
    "query": ["天气", "气温", "温度", "湿度", "亮度"],
}


def get_hotwords(context=None):
    """产出热词列表。context 给定时（如当前房间）优先返回相关词，
    否则返回全量业务词。§6.1 建议动态词表优于全局大词表。"""
    if context and context in HOTWORDS:
        return list(HOTWORDS[context])
    words = []
    for group in HOTWORDS.values():
        words.extend(group)
    return words


def whisper_prompt(context=None):
    """把热词拼成 whisper initial-prompt（逗号分隔的偏置提示）。
    whisper.cpp 支持 --prompt，把业务词作为上文提示可提升专有词命中。"""
    return "，".join(get_hotwords(context))


# ---------------------------------------------------------------------------
# 6. selftest
# ---------------------------------------------------------------------------
def selftest():
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("[PASS] " if cond else "[FAIL] ") + name + ("  " + detail if detail else ""))

    # --- 模糊音节：翘舌/平舌、前后鼻音、n/l 应折叠到同一 key ---
    ck("翘舌平舌合并 室(shi)->si", fuzz_syllable("室") == "si", fuzz_syllable("室"))
    ck("前后鼻音合并 灯(deng)->den", fuzz_syllable("灯") == "den", fuzz_syllable("灯"))
    ck("nl合并 鲁(lu)保持l", fuzz_syllable("鲁") == "lu", fuzz_syllable("鲁"))

    # --- 控制命令近音纠错：口音误识别应拉回规范命令 ---
    ck("规范命令不误纠 开灯", correct_command("开灯") is None)

    # "登"deng 与"灯"deng 同音 -> 应纠回"打开客厅灯"
    r = correct_command("打开客厅登")
    ck("打开客厅登 -> 打开客厅灯", r is not None and r["corrected"] == "打开客厅灯", str(r))

    # 翘舌/平舌口音："市"shi == "室"shi 同模糊音
    r2 = correct_command("打开卧市灯")
    ck("打开卧市灯 -> 打开卧室灯", r2 is not None and r2["corrected"] == "打开卧室灯", str(r2))

    # --- 红线：开放域/不相干句子不得被纠成命令 ---
    ck("开放域不误纠 相对论", correct_command("相对论是怎么回事") is None)
    ck("开放域不误纠 讲个笑话", correct_command("讲个笑话") is None)
    ck("空串安全", correct_command("") is None)

    # --- 热词偏置 ---
    ck("热词含新疆地名", "乌鲁木齐" in get_hotwords())
    ck("上下文热词 room", "卧室" in get_hotwords("room"))
    ck("whisper_prompt 非空", len(whisper_prompt()) > 0)

    print("\naccent_correct selftest: PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "selftest":
        return selftest()
    text = sys.argv[1]
    r = correct_command(text)
    if r:
        print("纠错: %r -> %r (距离=%d 规则=%s)" % (r["from"], r["corrected"], r["distance"], r["rule"]))
    else:
        print("未纠错(保持原文或转兜底): %r" % text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
