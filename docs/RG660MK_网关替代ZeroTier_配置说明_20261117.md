# 用网关替代 ZeroTier（2026-11-17）

目标：拔掉网线后，板子仍能访问笔记本上的 Immich / CasaOS，演示第 3、7、8 项不依赖 ZeroTier。

## 拓扑（全程只走回环，不对公网开放任何端口）

```
笔记本 ──ssh -R 127.0.0.1:18082:127.0.0.1:2283──▶ 网关 47.116.191.31   (Immich)
笔记本 ──ssh -R 127.0.0.1:18083:127.0.0.1:80───▶ 网关 47.116.191.31   (CasaOS)
板子   ──ssh -L 127.0.0.1:2283:127.0.0.1:18082─▶ 网关                 → 板子本机 2283
板子   ──ssh -L 127.0.0.1:10080:127.0.0.1:18083▶ 网关                 → 板子本机 10080
```

- 网关上的 18082/18083 只绑 `127.0.0.1`，外部扫不到
- 板子已有 5G，出站到网关 22 端口即可
- 原 18099（板子演示页）与 18080 不受影响

## 网关侧改动（CentOS 7，sshd）

`/etc/ssh/sshd_config`，仅改 `Match User cpe-view` 块：

```
AllowTcpForwarding yes                     # 原 local（笔记本需要 -R）
PermitOpen 127.0.0.1:18099 127.0.0.1:18080 127.0.0.1:18081 127.0.0.1:18082 127.0.0.1:18083
```

- 备份：`/etc/ssh/sshd_config.bak_YYYYmmdd_HHMM`
- 生效：`sshd -t && systemctl reload sshd`（reload 不断已有连接）
- `GatewayPorts` 保持 `no`（不开放公网绑定）

## 笔记本侧（常驻）

`~/.config/systemd/user/gw-nas-tunnel.service`（仓库副本 `scripts/gw-nas-tunnel.service`）：

```bash
systemctl --user daemon-reload
systemctl --user enable --now gw-nas-tunnel.service
systemctl --user is-active gw-nas-tunnel.service     # active
```

验证（在网关上）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18082/   # 200 = Immich
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18083/   # 200 = CasaOS
```

## 板子侧（待装，脚本 `scripts/board_gw_tunnel.sh`）

1. 生成密钥：`ssh-keygen -t ed25519 -f /data/ai_cpe/.ssh/gw_board -N ""`
2. 公钥追加到网关 `root@47.116.191.31:/root/.ssh/authorized_keys`
3. 启动：`sh /data/ai_cpe/tools/gw_tunnel.sh`（pidfile 单实例 + 每分钟 cron 保活）
4. 验证：
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:2283/    # 200 = Immich
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:10080/   # 200 = CasaOS
   ```

## 演示代码接入（需确认后改）

`aicpe_demo.py` 的 `nas_host()` 目前只试 `192.168.1.212`（LAN）和 `172.25.25.240`（ZT）。
加第三个候选 `127.0.0.1`（Immich 由板子本地 2283 隧道承载），CasaOS 入口用 `127.0.0.1:10080`。
改动前备份 `aicpe_demo.py.bak_*`，改后跑一轮 10 项验收。

## 回滚

```bash
# 网关
cp /etc/ssh/sshd_config.bak_<时间> /etc/ssh/sshd_config && sshd -t && systemctl reload sshd
# 笔记本
systemctl --user disable --now gw-nas-tunnel.service
# 板子
crontab -l | grep -v gw_tunnel | crontab -   # 去掉保活
rm -f /var/run/gw_tunnel.pid; pkill -f gw_tunnel
```

## 已知限制

- 网关重启后，两侧 SSH 会自动重连（ServerAliveInterval=20 / Restart=always），最长约 1 分钟恢复
- 图片检索经公网多一跳，单次比局域网慢约 100–300 ms（实测待补）
- 若网关 sshd 策略被重置（如重装），需重新应用上面的 PermitOpen
