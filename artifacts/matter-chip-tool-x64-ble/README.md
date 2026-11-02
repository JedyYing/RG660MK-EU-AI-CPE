# 带 BLE 的 chip-tool（x86_64，笔记本侧）——构建与使用

## 为什么需要它

板载 `/data/ai_cpe/matter/bin/chip-tool` 是 **BLE 关闭**构建
（`scripts/build_matter_chip_tool.sh` 里 `chip_config_network_layer_ble=false`，
`artifacts/matter-v1.6.0.0-rg660/BUILD_INFO.txt` 可查）。即使 CSM8510 蓝牙适配器工作正常，
`pairing ble-wifi` 也会报：

```
[IN] SendMessage() to BLE failed: Tuple.h: Chip Error 0x0000000C: No message handler
```

要做 BLE 配网，只能在有蓝牙的笔记本上跑 x86_64 版 chip-tool。

## 产物

- 笔记本：`/home/jedyying/chip-tool-ble-x64`（带调试符号 169MB）
  `chip-tool-ble-x64.stripped`（`strip --strip-debug` 后 12MB，功能相同）
- 仓库备份：`artifacts/matter-chip-tool-x64-ble/chip-tool-ble-x64.stripped`
- 构建树：`/home/jedyying/chip-tool-build/connectedhomeip`（含 `.environment` 的 pigweed 工具包）

## 构建步骤（2026-11-02 实测通过）

1) 准备镜像（一次，约 5 分钟）：

```sh
docker run --name cbtmp ubuntu:22.04 bash -lc 'export DEBIAN_FRONTEND=noninteractive;
  apt-get update -qq;
  apt-get install -y -qq git unzip xz-utils file pkg-config cmake make python3 python3-dev \
    python3-pip gcc g++ libglib2.0-dev libdbus-1-dev libavahi-client-dev libssl-dev \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    libgtk-3-0 libx11-xcb1 libxss1'
docker commit cbtmp chiptool-build:22.04 && docker rm cbtmp
```

`libnss3…libxss1` 是 ZAP（Electron 版代码生成器）需要的，缺了会在 zapgen 阶段失败。

2) 补缺失 submodule：

```sh
git -C /home/jedyying/chip-tool-build/connectedhomeip submodule update --init --depth 1 third_party/libwebsockets/repo
```

3) Python 3.10 兼容补丁（SDK 用了 3.11 才有的 `logging.getLevelNamesMapping`）：
把下面 5 个文件里的 `__LOG_LEVELS__ = logging.getLevelNamesMapping()`
换成 `__LOG_LEVELS__ = getattr(logging, "getLevelNamesMapping", lambda: {logging.getLevelName(v): v for v in range(1, 60)})()`

- scripts/codepregen.py
- scripts/codegen.py
- scripts/run-clang-tidy-on-compile-commands.py
- scripts/py_matter_idl/matter/idl/lint/__init__.py
- scripts/py_matter_idl/matter/idl/backwards_compatibility.py

4) 构建（**不要** source scripts/activate.sh：会触发 pigweed CIPD bootstrap，国内拉 fuchsia 包会卡死）：

```sh
docker run --rm --entrypoint /bin/bash \
  -v /home/jedyying/chip-tool-build/connectedhomeip:/sdk -w /sdk chiptool-build:22.04 -c '
set -e
git config --global --add safe.directory "*"
ENV_ROOT=/sdk/.environment
export _PW_ACTUAL_ENVIRONMENT_ROOT=$ENV_ROOT
export PATH=$ENV_ROOT/cipd/packages/zap:$ENV_ROOT/cipd/packages/pigweed/bin:$ENV_ROOT/cipd/packages/pigweed:$ENV_ROOT/pigweed-venv/bin:$PATH
export ELECTRON_DISABLE_SANDBOX=1
gn gen out/linux-ble --args="chip_config_network_layer_ble=true is_debug=false chip_crypto=\"mbedtls\" chip_mdns=\"platform\""
ninja -C out/linux-ble chip-tool'
```

5) 拷出产物（`out/linux-ble` 是 root 0700，宿主机读不到）：

```sh
docker run --rm -v /home/jedyying/chip-tool-build/connectedhomeip:/sdk -v $HOME:/host alpine sh -c \
  'cp /sdk/out/linux-ble/chip-tool /host/chip-tool-ble-x64 && chown 1000:1000 /host/chip-tool-ble-x64 && chmod 755 /host/chip-tool-ble-x64'
strip --strip-debug ~/chip-tool-ble-x64   # 可选，169MB → 12MB
```

6) 验证：

```sh
strings ~/chip-tool-ble-x64 | grep -ic bluez     # > 0（实测 3533）
~/chip-tool-ble-x64 pairing ble-wifi --help      # 用法应为 5 个位置参数
bluetoothctl show | head -4                      # Powered: yes
```

## 用法（BLE 配网）

```sh
PIN=$(~/chip-tool-ble-x64 payload parse-setup-payload "<二维码/手动码>" 2>/dev/null \
      | grep -oE 'Passcode:[[:space:]]*[0-9]+' | grep -oE '[0-9]+$')
mkdir -p /tmp/chipkvs-pc
~/chip-tool-ble-x64 pairing ble-wifi <node-id> <ssid> <password> "$PIN" <discriminator> \
   --paa-trust-store-path ~/matter-paa --storage-directory /tmp/chipkvs-pc
```

- `--storage-directory` 目录**必须已存在**，否则报 `Init Storage failure ... 0x000000AF`。
- 扫描期会打印大量 `Device xx does not look like a CHIP device`（正常过滤），
  只有设备处于 commissioning window 时才会出现匹配项。
- PAA 证书目录从板子拷：`rsync -az root@<cpe>:/data/ai_cpe/matter/paa/ ~/matter-paa/`
  （实测里面是 `tuya_paa_vid0x125D.der`，VendorID 4701）。

## 坑

- 镜像 commit 后 ENTRYPOINT 可能变成 bash → `docker run` 必须加 `--entrypoint /bin/bash`，
  否则报 `/usr/bin/bash: cannot execute binary file`。
- 不要 `source scripts/activate.sh`；直接复用 `.environment/cipd/packages/{pigweed,zap}`。
- 板载 aarch64 chip-tool 永远不会有 BLE（编译期关闭），别在板子上反复试。
- 若笔记本完成 BLE 配网，fabric 在笔记本侧；要让板子控制，需要
  `pairing open-commissioning-window` 再让板子用 `pairing already-discovered`（IP 直连）加入第二管理员。

## 现状备注

2026-11-02 最终没有走 BLE：Moes 灯已被手机 App 连到模组 AP，设备端用
`pairing code 10 <11位码>` 做 **on-network** 配网成功（node 10）。此工具留作备用
（例如设备只开 BLE 窗口、或需要在笔记本侧取证时）。
