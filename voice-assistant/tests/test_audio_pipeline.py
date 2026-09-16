"""Offline regressions; fake PCM input exercises the actual recorder state machine."""
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

    def test_user_questions(self):
        for text, expected in [('今天上海什么天气？', 'WEATHER'), ('相对论是怎么回事？', 'LLM_OR_HERMES'), ('1+1等于几？', 'LOCAL_CALCULATOR'), ('给我把灯打开', 'DEVICE_CONTROL'), ('今天讲讲相对论', 'LLM_OR_HERMES'), ('时间为什么会变慢', 'LLM_OR_HERMES'), ('今天星期几？', 'LOCAL_DATE_TIME'), ('', 'EMPTY')]:
            with self.subTest(text=text):
                self.assertEqual(va.classify(text)[0], expected)
        self.assertEqual(va.route('1+1等于几？'), ('2', False))

    def test_observed_calc_transcript(self):
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


if __name__ == '__main__':
    unittest.main()
