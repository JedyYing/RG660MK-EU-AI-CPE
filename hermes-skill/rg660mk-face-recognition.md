# RG660MK 人脸识别（本地 NCNN）

## 概述

RG660MK 上运行本地人脸识别，使用 NCNN 推理引擎：
- **检测**: RetinaFace (mnet.25) 人脸检测 + 5 点 landmark
- **特征提取**: MobileFaceNet 128 维 embedding
- **运行方式**: aarch64 musl 静态编译 binary

## 文件位置

```
/data/ai_cpe/demo/ai_runtime/face/
├── face_runner              # NCNN 推理 binary (aarch64 musl static)
├── mnet.25-opt.param        # RetinaFace 检测模型
├── mnet.25-opt.bin
├── mobilefacenet.param      # MobileFaceNet 特征模型
└── mobilefacenet.bin

/data/ai_cpe/demo/bin/
└── vision_runner_wrapper    # 分发 wrapper (face -> face_runner, detect/pose -> vision_runner)
```

## 使用方式

### 直接调用 face_runner

```bash
echo '{"version":1,"operation":"face","input":{"path":"/tmp/RG660MK_C270.jpg"},"models":{"face_detect":{"path":"/data/ai_cpe/demo/ai_runtime/face/mnet.25-opt.param"},"face_embed":{"path":"/data/ai_cpe/demo/ai_runtime/face/mobilefacenet.param"}}}' | /data/ai_cpe/demo/ai_runtime/face/face_runner
```

返回 JSON:
```json
{
  "ok": true,
  "result": {
    "faces": [
      {
        "bbox": [x0, y0, x1, y1],
        "score": 0.99,
        "embedding": [0.123, -0.456, ...]  // 128 维
      }
    ],
    "count": 1
  }
}
```

### 通过 ai_service HTTP API

```bash
curl http://127.0.0.1:8080/vision/face \
  -H "Content-Type: application/json" \
  -d '{"input":{"path":"/tmp/RG660MK_C270.jpg"}}'
```

ai_service 会自动调用 wrapper -> face_runner，并进行人脸身份匹配（如果 gallery 中有注册的人脸）。

## 编译方法

在 Ubuntu x86_64 主机上交叉编译：

```bash
# 1. 下载 musl 工具链
wget https://musl.cc/aarch64-linux-musl-cross.tgz
tar xf aarch64-linux-musl-cross.tgz

# 2. 克隆 ncnn 并编译 (aarch64)
git clone --depth 1 https://github.com/Tencent/ncnn.git
cd ncnn && mkdir build && cd build
cmake -DCMAKE_TOOLCHAIN_FILE=../../toolchain.cmake \
      -DNCNN_BUILD_EXAMPLES=OFF -DNCNN_BUILD_TOOLS=OFF \
      -DNCNN_BUILD_BENCHMARK=OFF -DNCNN_VULKAN=OFF \
      -DNCNN_OPENMP=OFF -DNCNN_BUILD_SHARED_LIBS=OFF \
      -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)

# 3. 编译 face_runner
aarch64-linux-musl-g++ -O2 -std=c++11 -static \
  -I ncnn/src -I ncnn/build/src \
  face_runner.cpp -o face_runner \
  ncnn/build/src/libncnn.a -lpthread
```

## 已知问题

- 检测阈值 0.8 较高，可能漏检小人脸
- 输入需要 mean subtraction [104, 117, 123]
- 空 vector 时 qsort_desc 会 segfault（已修复）

## 与 Immich 方案的对比

| 方案 | 优点 | 缺点 |
|------|------|------|
| Immich (云端) | 无需本地模型，buffalo_s 精度高 | 依赖网络，延迟高 |
| NCNN (本地) | 离线可用，延迟低 | 需要编译部署，精度略低 |

当前两种方案都可用，通过不同 API 调用。
