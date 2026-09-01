---
name: rg660mk-video-diagnose
description: >
  诊断 5G CPE 视频播放卡顿。当用户问「视频卡不卡 / 为什么卡 / 播放怎么样 /
  视频流正常吗 / 帮我看下卡顿 / 诊断一下播放」等与视频播放质量、卡顿、
  流状态相关的问题时使用。本 skill 通过本机 DPI 只读状态接口取数,
  把 DPI 字段翻译成用户能看懂的分级结论、原因和下一步建议。
  仅只读查询,绝不改动设备、网络、DPI 服务或任何配置。
---

# RG660MK 视频播放卡顿诊断（Hermes 执行）

## 你要做什么

用户问「视频卡不卡 / 为什么卡 / 播放正常吗」时，你**不要凭空回答**，
必须实时调用本机 DPI 状态接口取数，再按下面的规则把原始字段翻成
分级结论 + 原因 + 下一步建议，给用户一句话看懂的答复。

数据源是本机 PC 上常驻的 DPI 解析服务（设备本身装不下完整 DPI），
接口只读、带 token 鉴权。你只做「查询 + 翻译」，不做任何写操作。

## 第一步：调用状态接口（固定动作，每次诊断都要跑）

用 `execute_code` 执行下面的 Python，**不要**把 token 明文写进代码或回答里，
token 从设备本地文件读取：

```python
import json, subprocess

TOKEN_FILE = "/data/ai_cpe/demo/data/dpi-status-token"   # 0600, 不入 skill 明文
DPI_URL    = "http://192.168.1.244:8770/dpi/status"       # 本机 DPI 只读接口

def read_token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()

def call_dpi():
    try:
        token = read_token()
    except OSError as e:
        return {"_error": f"读不到 token 文件: {e}"}
    # 设备有 curl 用 curl，无 curl 时 fallback 到 wget
    cmd_curl = ["curl", "-s", "-m", "6",
                "-H", f"X-DPI-Status-Token: {token}", DPI_URL]
    try:
        p = subprocess.run(cmd_curl, capture_output=True, text=True, timeout=8)
        out = p.stdout.strip()
        if not out:
            wcmd = ["wget", "-qO-", "-T", "6",
                    f"--header=X-DPI-Status-Token: {token}", DPI_URL]
            out = subprocess.run(wcmd, capture_output=True, text=True, timeout=8).stdout.strip()
        return json.loads(out) if out else {"_error": "接口无返回（curl/wget 均为空）"}
    except subprocess.TimeoutExpired:
        return {"_error": "接口超时（>8s），本机 DPI 接口可能未启动或网络不通"}
    except json.JSONDecodeError:
        return {"_error": f"返回非 JSON，可能是鉴权失败(401)或路径错误: {out[:200]}"}
    except Exception as e:
        return {"_error": f"调用异常: {e}"}

result = call_dpi()
print(json.dumps(result, ensure_ascii=False))
```

## 第二步：把返回翻译成分级诊断（判定规则）

拿到 JSON 后，**按下表从上往下匹配，命中第一条即为结论**，不要跳级：

| 判定条件（按顺序） | 分级结论 | 给用户的原因 | 下一步建议 |
|---|---|---|---|
| 有 `_error` 字段 | ⚠️ 诊断不可用 | 取数链路本身出了问题（见 _error） | 让用户确认本机 DPI 接口是否在跑、设备到本机网络是否通 |
| `dpi_service` = `STOPPED` | ⚠️ 无法诊断 | 本机 DPI 解析服务已停 | 需在本机重启解析服务后再诊断 |
| `dpi_service` = `STALE`（或 `metrics_age_sec` > 30） | ⚠️ 数据可能过时 | 指标已 N 秒未更新，解析服务疑似卡住 | 结论仅供参考，建议本机侧排查解析服务 |
| `dpi_service` = `UNKNOWN` / `stream_status` = `NO_DATA` | ❓ 暂无数据 | 解析服务未写出指标 | 确认解析服务已启动且有流量喂入 |
| `stream_status` = `NO_ACTIVE_STREAM` | ⏸ 当前无视频流 | 没有活跃视频流在解析（真实 5G 是 WireGuard 加密，明文侧看不到） | 若确在播放，说明是加密流，需接明文 RTSP/RTP 源才能出真实指标 |
| `stream_status` = `ACTIVE_STREAM` 且 `last_window.recv_to_metric_ms` 缺失或 ≤ 200 | ✅ 播放流畅 | 有活跃视频流，解析处理及时 | 无需处理 |
| `stream_status` = `ACTIVE_STREAM` 且 `last_window.recv_to_metric_ms` 在 200–500 | 🟡 轻微卡顿 | 有活跃流，但收包到出指标延迟偏高 | 观察是否持续，短暂抖动可忽略 |
| `stream_status` = `ACTIVE_STREAM` 且 `last_window.recv_to_metric_ms` > 500 | 🔴 明显卡顿 | 有活跃流，但处理延迟明显偏高 | 建议排查本机负载 / 网络抖动 |

判定要点：
- **先看 `dpi_service` 再看 `stream_status`**：服务本身不健康时，流状态没有意义。
- `recv_to_metric_ms` 的阈值（200 / 500ms）是初始经验值，可按现场调整。
- 只有 `ACTIVE_STREAM` 才谈流畅/卡顿；没有活跃流不等于「不卡」，要如实说是「当前无流」。

## 第三步：回给用户（措辞要求）

- **一句话结论打头**：先给分级结论（如「✅ 当前播放流畅」），再补一句原因。
- 用大白话，不要甩 `dpi_service` `recv_to_metric_ms` 这类字段名给用户。
- 附一行取数时间（用返回里的 `last_health_ts` 或 `ts`），让用户知道是实时的。
- 结论若来自 `STALE` / 过时数据，必须明确提示「数据可能过时，仅供参考」，不要假装确定。

回复示例（ACTIVE_STREAM 且延迟正常）：
> ✅ 当前视频播放流畅。检测到活跃视频流，解析处理及时（延迟约 30ms），无卡顿迹象。
> 数据时间：2026-08-31 18:14。

回复示例（NO_ACTIVE_STREAM）：
> ⏸ 当前没有检测到活跃视频流。若你正在播放，说明走的是 5G 加密流量（WireGuard），
> DPI 在明文侧看不到，需要接一个明文 RTSP/RTP 源才能出真实卡顿指标。
> 数据时间：2026-08-31 18:14。

## 硬边界（不可违反）

- **只读**：本 skill 只查询状态，绝不重启/修改 DPI 服务、抓包、改网络或设备配置。
- **token 不外泄**：token 只从 `/data/ai_cpe/demo/data/dpi-status-token` 读取，
  不写进 skill 明文、不回显给用户、不入日志。
- **不编造**：接口取不到数就如实报「诊断不可用」，绝不凭记忆或猜测给卡顿结论。
- **加密流真相**：真实 5G 视频是加密流量，`NO_ACTIVE_STREAM` 是方案现实（需明文源），
  不是接口 bug，别把它误报成「一切正常」。
