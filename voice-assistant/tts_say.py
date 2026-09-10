#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""RG660MK 智能音箱 TTS 播放脚本
用法:
  tts_say.py <文本>            # 生成语音并在绿联音箱播放
  echo "文本" | tts_say.py      # 从 stdin 读文本
环境变量:
  SPEAKER_DEV   ALSA 播放设备, 默认 plughw:2,0 (绿联 CM564)
  VOICE         edge-tts 音色, 默认 zh-CN-XiaoxiaoNeural
"""
import asyncio, subprocess, sys, os

os.environ.setdefault("HOME", "/data/hermes/home")
VOICE = os.environ.get("VOICE", "zh-CN-XiaoxiaoNeural")
MPG123 = "/data/ai_cpe/bin/mpg123"
PLAY_DEV = os.environ.get("SPEAKER_DEV", "plughw:2,0")
MP3_PATH = "/tmp/tts_say.mp3"


def synth(text):
    import edge_tts

    async def _run():
        c = edge_tts.Communicate(text, VOICE)
        await c.save(MP3_PATH)

    asyncio.run(_run())


def play():
    p1 = subprocess.Popen(
        [MPG123, "-q", "-s", "--rate", "48000", MP3_PATH],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    p2 = subprocess.Popen(
        ["aplay", "-D", PLAY_DEV, "-f", "S16_LE", "-r", "48000", "-c", "1", "-q"],
        stdin=p1.stdout, stderr=subprocess.DEVNULL,
    )
    p1.stdout.close()
    p2.communicate()
    return p2.returncode


def main():
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = sys.stdin.read().strip()
    if not text:
        print("ERROR: no text", file=sys.stderr)
        sys.exit(2)
    try:
        synth(text)
        rc = play()
        print("OK spoken %d chars rc=%d" % (len(text), rc))
        sys.exit(0 if rc == 0 else 3)
    except Exception as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
