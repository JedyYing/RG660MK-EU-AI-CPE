#!/bin/sh
# RG660MKEU 蓝牙部署恢复脚本 (2026-09-08)
# 用法: sh /data/restore-bt.sh
# 前提: 备份文件 /data/bt-deploy-backup-20260908.tar.gz 存在
BACKUP=/data/bt-deploy-backup-20260908.tar.gz
KVER=5.15.134

echo "=============================================="
echo " RG660MKEU 蓝牙部署恢复"
echo "=============================================="

[ -f "$BACKUP" ] || { echo "[FAIL] 找不到备份 $BACKUP"; exit 1; }

echo "[1/6] 校验内核版本 ..."
CURVER=$(uname -r)
echo "  当前内核: $CURVER / 备份内核: $KVER"
if [ "$CURVER" != "$KVER" ]; then
  echo "  [WARN] 内核版本不一致! .ko 可能加载失败, 需重新编译"
fi

echo "[2/6] 解包部署文件到 / ..."
tar xzf "$BACKUP" -C / && echo "  [OK] 解包完成"

echo "[3/6] 加载内核模块 ..."
for m in bluetooth btintel btrtl btusb hci_uart rfcomm bnep hidp; do
  insmod /lib/modules/$CURVER/$m.ko 2>/dev/null && echo "  [OK] $m" || echo "  [skip] $m (已加载或失败)"
done
sleep 1

echo "[4/6] 拉起 hci0 ..."
hciconfig hci0 up 2>/dev/null && echo "  [OK] hci0 up" || echo "  [WARN] hci0 up 失败"

echo "[5/6] 启动 bluetoothd + session bus + obexd ..."
/etc/init.d/bluetoothd enable 2>/dev/null
/etc/init.d/bluetoothd restart 2>/dev/null
sleep 1
dbus-daemon --session --address=unix:path=/var/run/dbus/session_bus_socket --fork 2>/dev/null
sleep 1
killall obexd 2>/dev/null
DBUS_SESSION_BUS_ADDRESS="unix:path=/var/run/dbus/session_bus_socket" nohup /usr/bin/obexd -n -a -r /data/obex-incoming >/tmp/obexd.log 2>&1 &
sleep 1

echo "[6/6] 启动文件守护 + 打开可发现/可配对 ..."
mkdir -p /data/obex-incoming /data/obex-saved
killall obex-guard.sh 2>/dev/null
nohup /data/obex-guard.sh >/tmp/obex-guard.log 2>&1 &
sleep 1
echo -e "discoverable on\npairable on\n" | bluetoothctl >/dev/null 2>&1

echo "=============================================="
echo " 恢复完成, 验证结果:"
echo "=============================================="
echo "--- hci0 ---"
hciconfig -a 2>/dev/null | grep -E "hci0|UP RUNNING|Name" || echo "  (无 hci0)"
echo "--- bluetoothd ---"
ps w | grep "bluetoothd -n" | grep -v grep | awk "{print \"  PID\", \$1}" || echo "  (未运行)"
echo "--- obexd ---"
ps w | grep "obexd -n" | grep -v grep | awk "{print \"  PID\", \$1}" || echo "  (未运行)"
echo "--- 可发现状态 ---"
bluetoothctl show 2>&1 | grep -E "Powered|Discoverable|Pairable" || echo "  (无)"
echo ""
echo "若上面都正常, 手机即可搜到 RG660MK-BT 并传文件。"
echo "接收文件目录: /data/obex-saved/"
