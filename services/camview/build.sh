#!/bin/bash
# 交叉编译 camview (aarch64 musl)。复用 8/31 YOLO 部署已验证的工具链与静态库。
set -e

STAGING_DIR="/home/jedyying/Documents/基于SDR的语义通信实验平台/AI_CPE_Demo/.build/ncnn-cpu/openwrt-sdk-23.05.0-armsr-armv8_gcc-12.3.0_musl.Linux-x86_64/staging_dir"
export STAGING_DIR
TCBIN="$STAGING_DIR/toolchain-aarch64_generic_gcc-12.3.0_musl/bin"
CXX="$TCBIN/aarch64-openwrt-linux-musl-g++"

DEP="/home/jedyying/Agent工作区/rg660mk_yolo_deploy_20260831_144149"
UVC_INC="$DEP/build/libuvc-musl/src/include"
UVC_INC2="$DEP/build/libuvc-musl/include"
UVC_LIB="$DEP/build/libuvc-musl/libuvc.a"
USB_LIB="$DEP/build/libusb-musl/install/lib/libusb-1.0.a"

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/build/camview"

echo "== 编译器: $CXX =="
"$CXX" --version | head -1

"$CXX" -O2 -mcpu=cortex-a55 -std=c++17 \
  -I"$UVC_INC" -I"$UVC_INC2" \
  "$HERE/camview.cpp" \
  "$UVC_LIB" "$USB_LIB" \
  -static -lpthread -lgcc_eh \
  -o "$OUT"

echo "== 产物 =="
ls -lh "$OUT"
file "$OUT" 2>/dev/null || true
"$TCBIN/aarch64-openwrt-linux-musl-readelf" -d "$OUT" 2>/dev/null | grep -iE "NEEDED|statically" || echo "(无动态依赖,静态链接)"
