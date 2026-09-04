---
name: rg660mk-local-ai
description: Use the RG660MK-EU local USB accelerator through the loopback AI Service for object detection, face identification, posture detection, ASR, keyword spotting, TTS, and runtime metrics. Preserve CPE networking and fall back cleanly when local AI is unavailable.
---
# RG660MK-EU Local AI
Use this skill only when a request needs local vision/audio inference or accelerator health.

## Hard boundaries
- Hermes remains the conversation, tool orchestration, Feishu, MQTT/Home Assistant, and cloud-model agent.
- Never load accelerator drivers or access USB devices directly from Hermes.
- Never change WAN, DHCP, DNS, NAT, firewall, Wi-Fi, USB role, boot, or firmware settings.
- Call only the loopback AI Service through `/data/ai_cpe/demo/services/hermes_ai_tool.py`.
- Run `/health` before the first inference request. If health fails, report that local AI is unavailable and continue ordinary cloud conversation where possible.
- Do not claim an action succeeded from process exit code alone; inspect the returned JSON field `ok` and the expected result fields.

## Calling the tool on constrained OpenWrt
Use `execute_code` with Python `subprocess.run`, because the terminal backend may be degraded:

```python
import json, subprocess
cmd = [
    "/data/ai_cpe/hermes/venv/bin/python",
    "/data/ai_cpe/demo/services/hermes_ai_tool.py",
    "health",
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
result = json.loads(r.stdout)
print(json.dumps(result, ensure_ascii=False))
```

For POST actions, append `--payload` and a JSON object. Always set a bounded timeout.

## Intent mapping
- Object detection or image scene request → `detect` → `/vision/detect`
- Face identification → `face` → `/vision/face`; report `subject_id=unknown` when `matched` is false
- Sitting/standing/posture request → `posture` → `/vision/posture`; preserve `unknown` when joints are occluded or outside calibrated angles
- Speech transcription → `asr` → `/audio/asr`
- Wake word or fixed keyword spotting → `kws` → `/audio/kws`
- Local speech synthesis → `tts` → `/tts`
- Accelerator/service status → `health` → `/health`
- Latency, FPS, memory, temperature, USB errors → `metrics` → `/metrics`

## Request and response rules
Every request must include or accept an auto-generated `request_id` and a finite `timeout_ms`. File inputs must be local absolute paths under an explicitly configured media directory. Do not pass secrets, arbitrary shell commands, or network configuration through payloads. Face enrollment/removal is intentionally unavailable over HTTP: an authorized operator must use the local `face_gallery.py` CLI. Never expose embeddings or claim an `unknown` face is a known person.

Treat these states distinctly:
- `ok: true`: use the returned structured result.
- `MODEL_UNAVAILABLE`: explain that the requested model is not loaded; do not loop.
- `AI_SERVICE_UNAVAILABLE`: keep Hermes/cloud functions alive and state that local acceleration is degraded.
- Timeout or USB/runtime error: perform at most one health check, then stop retrying and recommend the AI rollback script.

## Full demo flow
1. Health check.
2. Capture service provides a frame path; run `detect` or `face`.
3. Convert the structured result into the requested MQTT/Home Assistant action.
4. Confirm the action from its real response, not from intent alone.
5. Reply through the active Hermes channel; use `tts` only when a USB speaker is present and healthy.
6. Preserve about three seconds of end-to-end latency as the initial target, then optimize from measured metrics.
