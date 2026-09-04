# RG660MK ↔ 本机 DPI 对接说明(Hermes 拉取本机状态)

- 编制:QRIBuddy · 2026-08-31
- 架构:DPI 解析跑在本机 PC(设备装不下完整 DPI),RG660MK 通过 Hermes 拉取本机 DPI 状态。
- 本次范围:**本机坐实 + 备好只读接口**。设备侧仅给对接方法,未实际写入设备。

## 为什么 DPI 放本机

1. 设备无任何标准 DPI 组件(netifyd/nDPI/xt_ndpi 均未装,实测确认)。
2. 设备资源受限:约 1.49GB RAM、`/data` 仅约 2GB 可用,跑不动 NALU 级实时解析。
3. 真实 5G 视频为 WireGuard 加密流量,设备侧看不到明文包,明文 DPI 在其上不成立。

因此采用原方案的 fallback:**解析常驻本机 PC,设备只做数据源/查询方**。

## 本机侧现状(已部署并自测通过)

- 解析服务 `rg660mk-video-dpi.service`:active + enabled,持续写 `service_metrics.jsonl`。
- 状态接口 `rg660mk-dpi-status-api.service`:active + enabled,只读,绑 `192.168.1.244:8770`。
- 接口带 token 鉴权,token 存本机 `dpi_gateway/.status_token`(权限 600),不入公网。

### 接口契约

```
GET /health              # 接口自身存活
GET /dpi/status          # DPI 实时状态(需 header: X-DPI-Status-Token)
```

`/dpi/status` 返回示例:

```json
{
  "ok": true,
  "dpi_service": "RUNNING",          // RUNNING/STALE/STOPPED/UNKNOWN(解析服务本身)
  "stream_status": "NO_ACTIVE_STREAM", // ACTIVE_STREAM/NO_ACTIVE_STREAM(有无活跃视频流)
  "cum_rtp": 19782,                   // 累计 RTP 包
  "active_ssrcs": null,               // 活跃流数
  "metrics_age_sec": 6.3,             // 指标新鲜度(秒)
  "last_health_ts": "2026-08-31T18:14:21+0800"
}
```

## 设备侧对接(需你在设备执行,本步骤会写设备,尚未做)

### 第一步:验证连通(只读,不写设备)

登设备 `ssh root@192.168.1.1` 后:

```sh
curl -s -m 6 -H "X-DPI-Status-Token: <本机给的token>" \
  http://192.168.1.244:8770/dpi/status
```

能返回上面那段 JSON,即证明「设备 → 本机 DPI」链路通。设备无 curl 时用 `wget -qO- --header=...`。

### 第二步(可选,端到端):给 Hermes 加一个查询 skill

仅在你确认要做真联动时执行。会往设备 Hermes 配置写入,**执行前先备份**:

```sh
cp -r /data/ai_cpe/hermes/.hermes/skills /data/ai_cpe/hermes/.hermes/skills.bak
```

skill 目标路径:`/data/ai_cpe/hermes/.hermes/skills/embedded/rg660mk-dpi-status/SKILL.md`,
让 Hermes 用 `execute_code + subprocess.run` 调用上面的 curl,把 `stream_status` 与
`dpi_service` 回给用户。token 存设备 `/data/ai_cpe/demo/data/dpi-status-token`(0600),
不写进 skill 明文。

## 重要前提:业务真出数还需要明文视频源

当前接口能实时反映 DPI 状态,但只要没有明文视频流喂进解析服务,`stream_status` 会一直是
`NO_ACTIVE_STREAM`(真实 5G 是 WireGuard 加密)。要产出真实视频指标,需原方案的阶段 B:
一个可路由的明文 RTSP/RTP 源。这是方案层现实,不是本次对接的缺陷。

## 回滚

```sh
systemctl --user disable --now rg660mk-dpi-status-api.service   # 停本机状态接口
rm ~/.config/systemd/user/rg660mk-dpi-status-api.service        # 如需彻底移除
# 设备侧若做了第二步: 用 skills.bak 覆盖回去, 仅重启 Hermes gateway, 不动网络
```
