#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唤醒自测（声学回放）：TTS "你好小皮" → CM564 播放 → C270 实录 → 分析
输出：每档音量的 首字是否保留 / 前 300ms 能量 / ASR 文本 / is_wake
用法：先停 voice（C270 被占用），跑完再起
"""
import os, sys, time, wave, array, math, subprocess

sys.path.insert(0, "/data/ai_cpe")
import vad, voice_assistant as va

SRC = "/tmp/wake_test.wav"          # 由 local_tts 合成
OUT = "/tmp/wake_selftest"
os.makedirs(OUT, exist_ok=True)


def rec(dev, path, secs):
    subprocess.run(["arecord", "-D", dev, "-f", "S16_LE", "-r", "48000",
                    "-c", "1", "-d", str(secs), path], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stats(path):
    w = wave.open(path)
    d = w.readframes(w.getnframes())
    rate = w.getframerate() or 48000
    a = array.array("h"); a.frombytes(d[: len(d) // 2 * 2])
    fr = int(rate * 0.03)
    rms = []
    for i in range(0, len(a) - fr, fr):
        seg = a[i:i + fr]
        rms.append(int(math.sqrt(sum(x * x for x in seg) / fr)))
    first = next((i for i, r in enumerate(rms) if r >= 450), -1)
    lead_ms = first * 30 if first >= 0 else -1
    head = rms[:10]
    return {
        "dur_s": round(len(a) / rate, 2),
        "lead_ms": lead_ms,
        "head300": head,
        "max_rms": max(rms) if rms else 0,
    }


def main():
    if not os.path.exists(SRC):
        print("缺少 /tmp/wake_test.wav（先跑 local_tts.synthesize）")
        return 2
    rows = []
    for vol in (70, 85, 100):
        subprocess.run(["amixer", "-c", "2", "sset", "PCM", "%d%%" % vol],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        path = os.path.join(OUT, "rec_%d.wav" % vol)
        p = subprocess.Popen(["arecord", "-D", "plughw:CARD=WEBCAM,DEV=0",
                              "-f", "S16_LE", "-r", "48000", "-c", "1",
                              "-d", "5", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        subprocess.run(["aplay", "-D", "plughw:2,0", SRC],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.wait()
        st = stats(path)
        text = va.transcribe(path, use_prompt=False)
        rows.append((vol, st, text, va.is_wake(text)))
    print("%-4s %-6s %-8s %-6s %-22s %-6s" % ("vol", "dur_s", "lead_ms", "maxRMS", "asr", "wake"))
    for vol, st, text, wk in rows:
        print("%-4d %-6s %-8s %-6s %-22s %-6s" % (vol, st["dur_s"], st["lead_ms"],
              st["max_rms"], (text or "")[:20], wk))
        print("      head300ms:", st["head300"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
