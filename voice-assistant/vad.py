#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""RG660MK 语音端点检测 (VAD) —— 纯标准库，无第三方依赖。

真正的端点检测（Voice Activity Detection）：
  - 流式从 arecord 读 PCM（16-bit / mono），按 30ms 帧切分；
  - 每帧同时算 短时能量(RMS) 与 过零率(ZCR)，双特征判定"有话/静音"；
  - 状态机：静音中检测到连续 N 帧语音 -> 进入 SPEAKING；
             说话中检测到连续 M 帧静音 -> 判定"说完了"，立即截断返回；
  - 带前置 padding（把触发前的一小段也保留，避免吃掉首字）；
  - 带最长录音上限与"起始静音超时"（一直没人说话就返回空）。

相比旧版"死录固定秒数 + 整段 RMS 门限"，这里是逐帧、自适应、说完即停的真 VAD。

CLI:
  vad.py record <out.wav> [--rate R] [--dev D] [--max S] [--startwait S]
      录到一段语音就写文件；有语音返回 exit 0，纯静音超时返回 exit 2。
  vad.py selftest
      对合成信号跑一遍状态机，验证逻辑正确（离线，不碰麦克风）。
"""
import os, sys, math, wave, struct, subprocess, collections

RATE_DEFAULT = 48000
FRAME_MS = 30            # 每帧时长
PAD_MS = 300            # 语音前保留的 padding
START_SPEECH_FRAMES = 5 # 连续多少帧"有话"才算开始说话（去抖，避免噪声误触发）
END_SIL_MS = 700        # 说话后连续这么久静音，判定说完
MIN_SPEECH_MS = 180     # 语音时长下限，短于这个当噪声丢弃
MAX_UTTER_S = 12        # 单次录音最长
START_WAIT_S = 8        # 起始静音超时（一直没人说话就放弃）

# 双门限：能量与过零率。能量为主，过零率辅助（清辅音/擦音能量低但 ZCR 高）。
RMS_ON = float(os.environ.get("VOICE_VAD_ON", "450"))    # 高于此判定"可能有话"；VOICE_VAD_ON 可覆盖（换麦克风后按实测标定）
RMS_OFF = float(os.environ.get("VOICE_VAD_OFF", "280"))  # 低于此判定"静音"（滞回）；VOICE_VAD_OFF 可覆盖
ZCR_MIN = 0.02          # 过零率下限（纯直流/极低频噪声排除）


def frame_features(frame_bytes):
    """返回 (rms, zcr)。frame_bytes 为 16-bit LE PCM。"""
    n = len(frame_bytes) // 2
    if n == 0:
        return 0.0, 0.0
    a = struct.unpack("<%dh" % n, frame_bytes[:n * 2])
    # 短时能量
    s = 0
    for x in a:
        s += x * x
    rms = math.sqrt(s / n)
    # 过零率
    z = 0
    prev = a[0]
    for x in a[1:]:
        if (x >= 0) != (prev >= 0):
            z += 1
        prev = x
    zcr = z / n
    return rms, zcr


def is_speech(rms, zcr, prev_state):
    """滞回双门限。prev_state: True=上一帧判为语音。"""
    if prev_state:
        # 已在语音中，用较低的 OFF 门限维持，避免字间短停被切断
        return rms >= RMS_OFF
    # 静音中，需较高 ON 门限或（中等能量 + 足够过零率）才触发
    if rms >= RMS_ON:
        return True
    if rms >= RMS_OFF and zcr >= ZCR_MIN * 3:
        return True
    return False


def record_utterance(out_path, rate=RATE_DEFAULT, dev="plughw:2,0",
                     max_s=MAX_UTTER_S, start_wait_s=START_WAIT_S,
                     end_sil_ms=END_SIL_MS, gain=4.0):
    """流式 VAD 录一句话，写 wav。返回 True=录到语音, False=起始静音超时。

    end_sil_ms: 语音结束后连续静音多久判定"说完"（唤醒词阶段可调大，
    避免"你好…小皮"中间的自然停顿被切断）。
    """
    if os.path.exists(out_path):
        os.unlink(out_path)
    frame_bytes = int(rate * FRAME_MS / 1000) * 2   # 每帧字节数(16bit mono)
    pad_frames = int(PAD_MS / FRAME_MS)
    end_sil_frames = int(end_sil_ms / FRAME_MS)
    max_frames = int(max_s * 1000 / FRAME_MS)
    start_wait_frames = int(start_wait_s * 1000 / FRAME_MS)

    # arecord 原始 PCM 流式输出到 stdout
    proc = subprocess.Popen(
        ["arecord", "-D", dev, "-f", "S16_LE", "-r", str(rate),
         "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    ring = collections.deque(maxlen=pad_frames)   # 语音前的 padding 缓存
    voiced = []                                   # 已确认的语音帧
    triggered = False
    speech_run = 0        # 连续语音帧计数（用于起始去抖）
    sil_run = 0           # 语音开始后的连续静音帧
    speech_frames = 0
    total = 0
    prev_state = False
    try:
        while True:
            chunk = proc.stdout.read(frame_bytes)
            if len(chunk) < frame_bytes:
                break
            total += 1
            rms, zcr = frame_features(chunk)
            sp = is_speech(rms * gain, zcr, prev_state)
            prev_state = sp

            if not triggered:
                ring.append(chunk)
                speech_run = speech_run + 1 if sp else 0
                if speech_run >= START_SPEECH_FRAMES:
                    speech_frames = speech_run
                    triggered = True
                    voiced.extend(ring)   # 把 padding 也带上，保住首字
                    ring.clear()
                    sil_run = 0
                elif total >= start_wait_frames:
                    proc.terminate()
                    return False          # 一直没人说话
            else:
                voiced.append(chunk)
                if sp:
                    speech_frames += 1
                    sil_run = 0
                else:
                    sil_run += 1
                    if sil_run >= end_sil_frames:
                        # 说完了：裁掉尾部静音，只留语音。否则"短语音+长静音"
                        # 会被 whisper 判为低置信度（输出带括号的猜测），ASR 失败。
                        keep_tail = min(sil_run, 150 // FRAME_MS)
                        if sil_run > keep_tail:
                            del voiced[-(sil_run - keep_tail):]
                        break
                if len(voiced) >= max_frames:
                    break                 # 到最长上限
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        finally:
            proc.stdout.close()

    # 写 wav
    min_voiced_frames = int(MIN_SPEECH_MS / FRAME_MS)
    if speech_frames < min_voiced_frames:
        # 太短，很可能是噪声误触发，丢弃
        return False
    w = wave.open(out_path, "w")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(b"".join(voiced))
    w.close()
    return len(voiced) > 0


def selftest():
    """离线验证状态机：合成 [静音 - 语音 - 静音]，确认能正确切出语音段。"""
    rate = 16000
    frame_n = int(rate * FRAME_MS / 1000)

    def sil_frame():
        return struct.pack("<%dh" % frame_n, *([0] * frame_n))

    def tone_frame(k):
        buf = []
        for i in range(frame_n):
            # 400Hz 正弦，幅度足够越过 RMS_ON
            buf.append(int(8000 * math.sin(2 * math.pi * 400 * (k * frame_n + i) / rate)))
        return struct.pack("<%dh" % frame_n, *buf)

    seq = []
    for _ in range(20):
        seq.append(("sil", sil_frame()))
    for k in range(40):
        seq.append(("voice", tone_frame(k)))
    for _ in range(40):
        seq.append(("sil", sil_frame()))

    triggered = False; prev = False; speech_run = 0; sil_run = 0
    end_sil_frames = int(END_SIL_MS / FRAME_MS)
    start_idx = end_idx = None
    for idx, (label, fb) in enumerate(seq):
        rms, zcr = frame_features(fb)
        sp = is_speech(rms, zcr, prev); prev = sp
        if not triggered:
            speech_run = speech_run + 1 if sp else 0
            if speech_run >= START_SPEECH_FRAMES:
                triggered = True; start_idx = idx; sil_run = 0
        else:
            if sp:
                sil_run = 0
            else:
                sil_run += 1
                if sil_run >= end_sil_frames:
                    end_idx = idx; break

    ok = (start_idx is not None and end_idx is not None
          and 20 <= start_idx <= 26         # 语音在第 20 帧开始，允许 START_SPEECH_FRAMES=5 去抖延迟
          and 60 <= end_idx <= 85)          # 语音在第 60 帧结束 + 静音收尾
    print("selftest: start_frame=%s end_frame=%s -> %s"
          % (start_idx, end_idx, "PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    cmd = sys.argv[1]
    if cmd == "selftest":
        return selftest()
    if cmd == "record":
        out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/voice_rec.wav"
        rate = RATE_DEFAULT; dev = os.environ.get("REC_DEV", "plughw:2,0")
        max_s = MAX_UTTER_S; start_wait = START_WAIT_S
        args = sys.argv[3:]
        for i, a in enumerate(args):
            if a == "--rate" and i + 1 < len(args): rate = int(args[i + 1])
            elif a == "--dev" and i + 1 < len(args): dev = args[i + 1]
            elif a == "--max" and i + 1 < len(args): max_s = float(args[i + 1])
            elif a == "--startwait" and i + 1 < len(args): start_wait = float(args[i + 1])
        got = record_utterance(out, rate=rate, dev=dev, max_s=max_s, start_wait_s=start_wait)
        print("VAD: %s -> %s" % ("speech" if got else "timeout(silence)", out))
        return 0 if got else 2
    print("unknown cmd:", cmd); return 1


if __name__ == "__main__":
    sys.exit(main())
