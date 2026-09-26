"""Offline regressions; fake PCM input exercises the actual recorder state machine."""
import datetime
import io
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import voice_assistant as va
import vad


class AudioTests(unittest.TestCase):
    def test_bulb_spoken_results(self):
        cases = [
            ('on', 1, '❌ 指令失败: {"code":2001,"msg":"device is offline","success":false}', '灯泡离线了，请检查灯泡电源和网络连接。'),
            ('on', 0, '✅ 已发送开灯指令', '已发送开灯指令。'),
            ('off', 0, '✅ 已发送关灯指令', '已发送关灯指令。'),
            ('on', 1, 'authentication failed', '灯泡控制失败，请稍后再试。'),
            ('on', 0, '', '没有收到灯泡的有效确认，请稍后再试。'),
            ('status', 0, '{"success":true,"result":[{"code":"switch_led","value":false}]}', '灯泡当前是关着的。'),
            ('status', 0, '{"success":true,"result":[]}', '暂时无法确认灯泡的开关状态。'),
            ('status', 0, '{"success":false}', '灯泡控制失败，请稍后再试。'),
        ]
        for action, rc, output, expected in cases:
            with self.subTest(action=action, output=output):
                result = SimpleNamespace(returncode=rc, stdout=output, stderr='')
                with patch.object(va.subprocess, 'run', return_value=result):
                    self.assertEqual(va.execute_tool('control_bulb', {'action': action}), expected)

    def test_latency_budget_for_escalating_intents(self):
        """可能与 Hermes 相关的意图不得用 7 秒快速失败。
        2026-09-21 更新：股票名解析失败会升级 Hermes（实测 ~23-29 秒），
        STOCK_QUOTE 因此也改走 30/60 双超时（设备实测 7 秒必超时，用户可见故障）。"""
        with patch.object(va, 'handle_turn') as handler:
            va.respond('请查看移远通信的收盘价')
            self.assertEqual(handler.call_args.kwargs['hard_secs'], va.HARD_TIMEOUT_SECS)
            self.assertEqual(handler.call_args.kwargs['giveup_text'], va.GIVEUP_TEXT)
        with patch.object(va, 'handle_turn') as handler:
            va.respond('相对论是怎么回事')
            self.assertEqual(handler.call_args.kwargs['hard_secs'], va.HARD_TIMEOUT_SECS)
            self.assertEqual(handler.call_args.kwargs['giveup_text'], va.GIVEUP_TEXT)

    def test_latency_budget_and_short_context(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'short.wav')
            with wave.open(path, 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\x00\x00' * 32000)
            self.assertEqual(va.asr_context(path), 384)
            result = SimpleNamespace(returncode=0, stdout='你好小皮')
            with patch.object(va.subprocess, 'run', return_value=result) as run:
                self.assertEqual(va.transcribe(path), '你好小皮')
                self.assertEqual(run.call_args.kwargs['timeout'], 8)
        with patch.object(va, 'handle_turn') as handler:
            va.respond('今天上海什么天气')
            self.assertEqual(handler.call_args.kwargs['hard_secs'], 7)
            self.assertEqual(handler.call_args.kwargs['giveup_text'], va.FAST_TIMEOUT_TEXT)

    def test_fixed_tts_cache_avoids_network(self):
        import hashlib
        from types import ModuleType
        fake = ModuleType('edge_tts')
        fake.Communicate = lambda *a, **k: self.fail('cached prompt must not synthesize')
        with tempfile.TemporaryDirectory() as folder, patch.object(va, 'TTS_CACHE', folder):
            text = '在呢，请说'
            key = hashlib.sha256((va.VOICE + '\n' + text).encode()).hexdigest()
            path = os.path.join(folder, key + '.mp3')
            with open(path, 'wb') as f:
                f.write(b'cached audio')
            with patch.dict(sys.modules, {'edge_tts': fake}):
                self.assertEqual(va.prepare_tts(text), (path, False))

    def test_user_questions(self):
        for text, expected in [('今天上海什么天气？', 'WEATHER'), ('相对论是怎么回事？', 'LLM_OR_HERMES'), ('1+1等于几？', 'LOCAL_CALCULATOR'), ('给我把灯打开', 'DEVICE_CONTROL'), ('今天讲讲相对论', 'LLM_OR_HERMES'), ('时间为什么会变慢', 'LLM_OR_HERMES'), ('今天星期几？', 'LOCAL_DATE_TIME'), ('', 'EMPTY')]:
            with self.subTest(text=text):
                self.assertEqual(va.classify(text)[0], expected)
        self.assertEqual(va.route('1+1等于几？'), ('2', False))

    def test_observed_calc_transcript(self):
        self.assertEqual(va.route('一家一等一集'), ('2', False))
        self.assertEqual(va.route('一家一等於幾'), ('2', False))
        self.assertEqual(va.route('一加一等一節'), ('2', False))
        self.assertEqual(va.route('一加一等一节？'), ('2', False))
        self.assertIsNone(va.is_calc('一加一等于三'))

    def test_weather_fallback_and_failure_message(self):
        import json
        current = {"time": "2026-09-16T10:00", "weather_code": 3,
                   "temperature_2m": 26.7, "apparent_temperature": 26.8,
                   "relative_humidity_2m": 46}
        data = io.BytesIO(json.dumps({"current": current}).encode())
        with patch.object(va.urllib.request, 'urlopen', side_effect=[OSError('certificate expired'), data]) as request:
            reply = va.execute_tool('get_weather', {"city": "上海"})
            self.assertIn('上海现在阴', reply)
            self.assertIn('26.7', reply)
            self.assertTrue(request.call_args.args[0].startswith('https://api.open-meteo.com/'))
        with patch.object(va.urllib.request, 'urlopen', side_effect=OSError('offline')):
            reply = va.execute_tool('get_weather', {"city": "上海"})
            self.assertEqual(reply, '天气服务暂时连接失败，请稍后再试。')

    def test_direct_answer_does_not_fall_through_to_hermes(self):
        with patch.object(va, 'llm_tools', return_value=('相对论描述时空和引力。', False)):
            self.assertEqual(va.route('相对论是怎么回事'), ('相对论描述时空和引力。', False))

    def test_wake_and_inline_command(self):
        for text in ['你好，小皮', '你好小皮', '你好，夏皮']:
            self.assertTrue(va.is_wake(text))
            self.assertEqual(va.strip_wake(text), '')
        self.assertEqual(va.strip_wake('你好，小皮，今天上海什么天气？'), '今天上海什么天气')
        for text in ['皮肤', '吉他', '招呼', '(招呼)']:
            self.assertFalse(va.is_wake(text))

    def test_multiline_and_failed_asr(self):
        with patch.object(va.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='今天上海\n什么天气？\n[BLANK_AUDIO]\n')):
            self.assertEqual(va.transcribe('unused'), '今天上海什么天气？')
        with patch.object(va.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='今天星期一')):
            self.assertEqual(va.transcribe('unused'), '')
        with patch.object(va.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='(招呼)\n(字幕:J Chong)')):
            self.assertEqual(va.transcribe('unused'), '')

    def record_frames(self, voice_count, amplitude=200):
        n = 480
        noise = struct.pack('<480h', *[int(40 * math.sin(2 * math.pi * 400 * i / 16000)) for i in range(n)])
        tone = struct.pack('<480h', *[int(amplitude * math.sin(2 * math.pi * 400 * i / 16000)) for i in range(n)])
        proc = SimpleNamespace(stdout=io.BytesIO(noise * 15 + tone * voice_count + noise * 35), terminate=lambda: None, wait=lambda **kw: 0)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'record.wav')
            with open(path, 'wb') as stale:
                stale.write(b'old recording')
            with patch.object(vad.subprocess, 'Popen', return_value=proc):
                got = vad.record_utterance(path, rate=16000, start_wait_s=1.5)
            if got:
                with wave.open(path) as wav:
                    data = wav.readframes(wav.getnframes())
                # VAD scales energy only; WAV must not be amplified twice.
                self.assertLessEqual(max(struct.unpack('<%dh' % (len(data)//2), data)), amplitude)
            else:
                self.assertFalse(os.path.exists(path))
            return got

    def test_weak_short_command(self):
        self.assertTrue(self.record_frames(8))  # 240ms, raw RMS ~141 < old threshold

    def test_noise_and_click_rejected(self):
        self.assertFalse(self.record_frames(0))
        self.assertFalse(self.record_frames(5))  # 150ms impulse; padding must not count as speech

    def test_failed_fixed_record_never_reuses_wav(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'record.wav')
            with open(path, 'wb') as stale:
                stale.write(b'old recording')
            with patch.object(va.subprocess, 'run', side_effect=OSError('device missing')):
                with self.assertRaises(OSError):
                    va.record(1, path)
            self.assertFalse(os.path.exists(path))


class RealTimeQuoteTests(unittest.TestCase):
    """2026-09-18 修复:L1 本地行情 + 拒绝不得成为终答 + Hermes 输出清洗。

    背景:问"请查看移远通信的收盘价",答"我查不了股市行情"。
    实测根因是 dispatch() 把模型的"我查不了"当成最终答案直接播报,短路了 Hermes。
    """

    @staticmethod
    def _resp(text):
        class _Ctx:
            def __enter__(self_):
                return io.BytesIO(text.encode('gbk'))

            def __exit__(self_, *args):
                return False
        return _Ctx()

    @staticmethod
    def _tencent(name, price, prev, change, pct, stamp):
        # 真实字段布局: 3=现价 4=昨收 30=时间戳 31=涨跌额 32=涨跌幅
        field = ['1', name, '603236', price, prev, '0'] + ['0'] * 24
        field += [stamp, change, pct, '0', '0']
        return 'v_sh603236="%s";' % '~'.join(field)

    @staticmethod
    def _sina(name, price, prev, day, clock):
        # 真实字段布局: 2=昨收 3=现价 30=日期 31=时间
        field = [name, '0', prev, price] + ['0'] * 26
        field += [day, clock, '00']
        return 'var hq_str_sh603236="%s";' % ','.join(field)

    def test_every_dispatch_branch_returns_a_pair(self):
        """dispatch 的契约是 (reply, need_hermes)。返回裸字符串会让 _turn_handler
        拆包失败,整条链路退化成"我走神了，再说一次"。"""
        cases = [('EMPTY', {}, ''), ('WEATHER', {'city': '上海'}, '今天上海什么天气'),
                 ('STOCK_QUOTE', {'query': '请查看移远通信的收盘价'}, 'x'),
                 ('LOCAL_DATE_TIME', {}, '今天星期几'), ('LOCAL_CALCULATOR', {'expr': '1+1'}, '1+1等于几')]
        for intent, slots, text in cases:
            with self.subTest(intent=intent):
                with patch.object(va.urllib.request, 'urlopen', side_effect=OSError('offline')):
                    reply, need_hermes = va.dispatch(intent, slots, text)
                self.assertIsInstance(need_hermes, bool)
                self.assertTrue(reply is None or isinstance(reply, str))

    def test_stock_intent_and_priority(self):
        for text in ['请查看移远通信的收盘价', '移远通信今天股价多少', '603236现在多少钱',
                     '上证指数今天收盘多少', '中兴通讯的股价']:
            with self.subTest(text=text):
                self.assertEqual(va.classify(text)[0], 'STOCK_QUOTE')
        # 既有优先级(设计文档 §2.2)不得被新意图挤掉
        for text, expected in [('现在几点天气', 'WEATHER'), ('今天上海什么天气', 'WEATHER'),
                               ('今天星期几', 'LOCAL_DATE_TIME'), ('1+1等于几', 'LOCAL_CALCULATOR'),
                               ('给我把灯打开', 'DEVICE_CONTROL'), ('', 'EMPTY')]:
            with self.subTest(text=text):
                self.assertEqual(va.classify(text)[0], expected)
        # 推断不出交易所的 6 位数字不是股票代码
        self.assertNotEqual(va.classify('123456是多少')[0], 'STOCK_QUOTE')

    def test_symbol_resolution_offline(self):
        self.assertEqual(va.resolve_symbol('请查看移远通信的收盘价'), ('sh603236', '移远通信'))
        self.assertEqual(va.resolve_symbol('亿元通信的股价'), ('sh603236', '亿元通信'))
        self.assertEqual(va.resolve_symbol('603236现在多少钱'), ('sh603236', None))
        self.assertEqual(va.resolve_symbol('000063'), ('sz000063', None))
        self.assertEqual(va.resolve_symbol('上证指数今天收盘多少'), ('sh000001', '上证指数'))
        # 概念问题不该被当成股票名去联网检索(应转 Hermes 解释)
        with patch.object(va.urllib.request, 'urlopen', side_effect=AssertionError('must not look up')):
            self.assertIsNone(va.resolve_symbol('什么是股票'))

    def test_quote_formatting_and_fallback(self):
        closed = '20260918161500'
        intraday = datetime.datetime.now().strftime('%Y%m%d') + '103000'
        cases = [
            (self._tencent('移远通信', '72.54', '73.66', '-1.12', '-1.52', closed),
             '移远通信今天收盘72.54元，跌1.12元，跌幅百分之1.52。'),
            (self._tencent('移远通信', '73.00', '72.00', '1', '1.39', closed),
             '移远通信今天收盘73元，涨1元，涨幅百分之1.39。'),
            (self._tencent('移远通信', '72.00', '72.00', '0', '0', closed),
             '移远通信今天收盘72元，与上一交易日收盘持平。'),
            # 盘中要说"最新价",不能把未收盘的报价说成收盘价
            (self._tencent('移远通信', '72.54', '73.66', '-1.12', '-1.52', intraday),
             '移远通信今天最新价72.54元，跌1.12元，跌幅百分之1.52。'),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                with patch.object(va.urllib.request, 'urlopen', return_value=self._resp(payload)):
                    self.assertEqual(va.get_stock_quote('sh603236'), expected)
        # 主接口(腾讯)失败回退备接口(新浪),与天气同款主备结构
        with patch.object(va.urllib.request, 'urlopen',
                          side_effect=[OSError('primary down'),
                                       self._resp(self._sina('移远通信', '72.540', '73.660',
                                                             '2026-09-18', '15:34:59'))]):
            self.assertEqual(va.get_stock_quote('sh603236'),
                             '移远通信今天收盘72.54元，跌1.12元，跌幅百分之1.52。')
        with patch.object(va.urllib.request, 'urlopen', side_effect=[OSError('a'), OSError('b')]):
            self.assertEqual(va.get_stock_quote('sh603236'), va.QUOTE_FAIL_TEXT)

    def test_stock_route_hits_live_data_not_llm(self):
        payload = self._tencent('移远通信', '72.54', '73.66', '-1.12', '-1.52', '20260918161500')
        with patch.object(va.urllib.request, 'urlopen', return_value=self._resp(payload)):
            with patch.object(va, 'llm_tools', side_effect=AssertionError('must not call LLM')):
                self.assertEqual(va.route('请查看移远通信的收盘价'),
                                 ('移远通信今天收盘72.54元，跌1.12元，跌幅百分之1.52。', False))

    def test_refusal_never_becomes_the_answer(self):
        """2026-09-18 设备实测抓到的真实回绝措辞 —— 都不是答案,必须转 Hermes。"""
        refusals = [
            '抱歉，我这边没有股票行情功能，查不了移远通信的收盘价。你可以打开股票软件或财经网站查看实时数据哦。',
            '抱歉，我暂时没法查询实时汇率，建议您打开手机银行或财经网站看一下最新报价。',
            '抱歉，我暂时查不到油价信息，没法告诉你92号汽油今天的价格。',
            '我暂时没法查新闻哦，只能帮你查天气、控制灯泡、拍照、做人脸或坐姿检测。',
            '抱歉，我没有查询火车票的功能，帮不了你订票或查余票。',
        ]
        for reply in refusals:
            with self.subTest(reply=reply[:12]):
                self.assertTrue(va.is_capability_refusal(reply))
                with patch.object(va, 'llm_tools', return_value=(reply, False)):
                    self.assertEqual(va.route('今天美元兑人民币汇率多少'), (None, True))
        self.assertFalse(va.is_capability_refusal('相对论描述时空和引力。'))

    def test_live_data_answer_without_tool_is_not_trusted(self):
        # 模型"答"了实时数据却没调工具 -> 可能是编造的数字,必须升级而不是念出来
        with patch.object(va, 'llm_tools', return_value=('今天美元兑人民币汇率是7.12。', False)):
            self.assertEqual(va.route('今天美元兑人民币汇率多少'), (None, True))
        # 工具已执行过 -> 结果是可信的;普通知识问答仍直接用模型答案(第二轮行为不回退)
        with patch.object(va, 'llm_tools', return_value=('刚查到的实时结果。', True)):
            self.assertEqual(va.route('今天有什么新闻'), ('刚查到的实时结果。', False))
        with patch.object(va, 'llm_tools', return_value=('相对论描述时空和引力。', False)):
            self.assertEqual(va.route('相对论是怎么回事'), ('相对论描述时空和引力。', False))

    def test_hermes_reasoning_is_stripped(self):
        """Hermes 的 stdout 带英文推理框;不清洗会被 speak() 的中文占比过滤掉。"""
        sample = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'fixtures', 'hermes_stdout_sample.txt')
        with open(sample, encoding='utf-8') as fixture:
            raw = fixture.read()
        answer = va.extract_hermes_answer(raw)
        self.assertIn('72.54', answer)
        self.assertNotIn('Reasoning', answer)
        self.assertNotIn('session_id', answer)
        # 修复前这段输出被过滤成"回复内容暂时无法播报",现在必须能直接播
        self.assertGreater(va._cjk_ratio(answer[:120]), 0.3)

    def test_execute_tool_stock_and_unknown(self):
        payload = self._tencent('移远通信', '72.54', '73.66', '-1.12', '-1.52', '20260918161500')
        with patch.object(va.urllib.request, 'urlopen', return_value=self._resp(payload)):
            self.assertEqual(va.execute_tool('get_stock_quote', {'name': '移远通信'}),
                             '移远通信今天收盘72.54元，跌1.12元，跌幅百分之1.52。')
        with patch.object(va.urllib.request, 'urlopen', side_effect=AssertionError('must not look up')):
            self.assertEqual(va.execute_tool('get_stock_quote', {'name': '什么'}), va.QUOTE_NOTFOUND_TEXT)


if __name__ == '__main__':
    unittest.main()
