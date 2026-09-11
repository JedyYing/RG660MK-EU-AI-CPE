#!/bin/bash
# build.sh — 在 Ubuntu 上交叉编译 face_runner 到 aarch64 (musl, OpenWrt)
set -e
cd "$(dirname "$0")"

# 1) 工具链 (musl.cc 预编译)
TC="$HOME/musl-cross/aarch64-linux-musl-cross"
if [ ! -x "$TC/bin/aarch64-linux-musl-g++" ]; then
  echo "[1/4] 下载 aarch64 musl 工具链..."
  wget -q https://musl.cc/aarch64-linux-musl-cross.tgz -O /tmp/tc.tgz
  mkdir -p "$HOME/musl-cross"
  tar xzf /tmp/tc.tgz -C "$HOME/musl-cross"
fi
export PATH="$TC/bin:$PATH"

# 2) 工具链 cmake 文件
cat > toolchain.cmake <<'EOF'
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER aarch64-linux-musl-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-musl-g++)
set(CMAKE_FIND_ROOT_PATH $ENV{HOME}/musl-cross/aarch64-linux-musl-cross)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
EOF

# 3) ncnn
if [ ! -d ncnn ]; then
  echo "[2/4] 克隆 ncnn..."
  git clone --depth 1 https://github.com/Tencent/ncnn.git
fi
cd ncnn
mkdir -p build && cd build
echo "[3/4] 编译 ncnn (aarch64)..."
TOOLCHAIN_FILE="$(cd .. && cd .. && pwd)/toolchain.cmake"
cmake -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN_FILE" \
      -DNCNN_BUILD_EXAMPLES=OFF -DNCNN_BUILD_TOOLS=OFF -DNCNN_BUILD_BENCHMARK=OFF \
      -DNCNN_VULKAN=OFF -DNCNN_OPENMP=OFF -DNCNN_BUILD_SHARED_LIBS=OFF \
      -DCMAKE_BUILD_TYPE=Release .. >/dev/null
make -j"$(nproc)" >/dev/null
cd ../..

# 4) 编译 runner
echo "[4/4] 编译 face_runner..."
aarch64-linux-musl-g++ -O2 -std=c++11 -static \
  -I ncnn/src -I ncnn/build/src \
  face_runner.cpp -o face_runner \
  ncnn/build/src/libncnn.a -lpthread

echo "✅ 完成: $(pwd)/face_runner"
file face_runner 2>/dev/null || true
