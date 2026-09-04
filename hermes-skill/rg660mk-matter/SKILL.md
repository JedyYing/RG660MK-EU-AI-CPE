---
name: rg660mk-matter
description: Commission reachable on-network Matter devices and control allowlisted On/Off cluster functions through the RG660MK-EU local Matter controller.
---
# RG660MK-EU Matter Controller
Use this skill for Matter device status, on-network commissioning, On/Off control, state reads, and explicit device removal.

## Hard boundaries
- Call only `/data/ai_cpe/demo/matter/services/hermes_matter_tool.py` with `execute_code` and `subprocess.run`; never construct shell commands.
- Never modify WAN, LAN, DHCP, DNS, NAT, firewall, Wi-Fi, USB role, boot, firmware, or Thread datasets.
- This deployment has no BLE or IEEE 802.15.4 radio. It can commission only a Matter device already reachable on `br-lan` and advertising an open commissioning window.
- A Thread device requires a separately installed Thread Border Router. A new Wi-Fi Matter device normally needs a BLE-capable phone/controller for its initial network provisioning.
- Treat setup PINs and QR onboarding payloads as secrets. Never repeat, log, or store them in conversation history beyond the immediate tool call.
- Never operate a door lock, garage door, cooker, heater, or other safety/security-sensitive endpoint through the generic On/Off action.
- Confirm success only when returned JSON has `ok: true`; a recognized intent or process launch is not success.

## Calling pattern
Run a health check before the first command:

```python
import json, subprocess
cmd = [
    "/data/ai_cpe/hermes/venv/bin/python",
    "/data/ai_cpe/demo/matter/services/hermes_matter_tool.py",
    "status",
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
print(json.dumps(json.loads(r.stdout), ensure_ascii=False))
```

For actions, append `--payload` and one JSON object. Keep the subprocess timeout bounded and slightly longer than the controller timeout.

## Intent mapping
- Controller health → `status` with `{}`
- Add an already-networked device → `commission_onnetwork` with `node_id`, `setup_pin`, and optional `discriminator`
- Turn a light/socket on → `on` with `node_id` and optional `endpoint`
- Turn a light/socket off → `off` with `node_id` and optional `endpoint`
- Toggle a light/socket → `toggle` with `node_id` and optional `endpoint`
- Read On/Off state → `read_onoff` with `node_id` and optional `endpoint`
- Remove a fabric node → `unpair` with `node_id`, only after explicit user confirmation

## Response rules
- `ok: true`: report the confirmed operation and relevant Matter response.
- `MATTER_BINARY_UNAVAILABLE`: controller binary is not deployed; do not retry.
- `MATTER_TIMEOUT`: perform at most one `status` call, then stop.
- `MATTER_COMMAND_FAILED`: summarize the returned diagnostic without claiming the furniture changed state.
- `INVALID_ARGUMENT`: ask only for the missing node ID, endpoint, setup PIN, or discriminator.
