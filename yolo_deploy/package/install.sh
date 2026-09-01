#!/bin/sh
# install.sh — 在 RG660MK 上安装持久服务(仅在阶段A/B/C 全部通过后运行)
# 依据 df 自动选择有足够空间的持久目录;不把正式模型放 /tmp;不覆盖既有文件。
# 用法: sh install.sh [--enable]   (--enable 才开机自启;默认仅安装不 enable)
set -e
ENABLE=0
[ "$1" = "--enable" ] && ENABLE=1

# 本包解压后的当前目录(含 bin/ models/ config/ service/)
PKG="$(cd "$(dirname "$0")" && pwd)"

# 选持久目录:优先 /data,其次 /overlay,再 /root;要求可写且 >=64MB 空余
pick_dir() {
  for c in /data /overlay /root /mnt/sda1; do
    [ -d "$c" ] || continue
    # 可写测试
    ( echo x > "$c/.pose_wtest" 2>/dev/null && rm -f "$c/.pose_wtest" ) || continue
    avail=$(df -k "$c" 2>/dev/null | awk 'NR==2{print $4}')
    [ -n "$avail" ] && [ "$avail" -ge 65536 ] && { echo "$c"; return 0; }
  done
  return 1
}
PERSIST="$(pick_dir)" || { echo "[FAIL] 找不到 >=64MB 可写持久目录" >&2; exit 1; }
BASE="$PERSIST/pose"
echo "[install] 持久目录: $BASE"

mkdir -p "$BASE/bin" "$BASE/models" "$BASE/config" "$BASE/service" "$BASE/logs"
# 拷贝(不覆盖已存在的 config,以保留现场修改)
cp -f "$PKG/bin/pose_camera_arm64" "$BASE/bin/"
cp -f "$PKG/bin/pose_image_arm64"  "$BASE/bin/" 2>/dev/null || true
chmod +x "$BASE/bin/"*
cp -rf "$PKG/models/"* "$BASE/models/"
[ -f "$BASE/config/pose.conf" ] || cp -f "$PKG/config/pose.conf" "$BASE/config/"
cp -f "$PKG/service/pose_run.sh" "$BASE/service/"; chmod +x "$BASE/service/pose_run.sh"

# 生成 init 脚本(注入 BASE)
sed "s#__BASE__#$BASE#g" "$PKG/service/pose_detect.init" > /etc/init.d/pose_detect
chmod +x /etc/init.d/pose_detect

echo "[install] 已安装。手动测试: /etc/init.d/pose_detect start  然后  /etc/init.d/pose_detect stop"
if [ "$ENABLE" = 1 ]; then
  /etc/init.d/pose_detect enable && echo "[install] 已设为开机自启"
else
  echo "[install] 未 enable(默认)。验收通过后执行: /etc/init.d/pose_detect enable"
fi
echo "[install] 回滚: /etc/init.d/pose_detect disable; /etc/init.d/pose_detect stop; rm -f /etc/init.d/pose_detect; rm -rf $BASE"
