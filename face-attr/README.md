# 人脸性别 / 年龄段识别（旁路部署，不影响 8099 演示）

RG660MK AI CPE 的附加能力：从现有摄像头画面识别人脸性别与年龄段，**独立进程、独立端口、按需调用**，与 `http://192.168.1.1:8099/` 的 AI CPE 演示完全隔离。

## 能力边界（实测结论，非估算）

| 能力 | 状态 | 实测依据 |
|---|---|---|
| **性别** | ✅ **交付** | InsightFace 13/13 + FairFace 10/10，置信度 0.93–0.999 |
| 年龄（含年龄段） | ❌ **已移除** | 两个模型在亚洲人脸上均无判别力：InsightFace 把 32 岁和 50 岁都输出 ~50-55；FairFace（85 MB，种族均衡训练）把两人都判 30-39。真实相差 18 岁却落同一档 |
| 情绪（7 类） | ❌ **已移除** | 1254 张公开标注数据实测，真实场景最好 36–40%（随机 14.3%），不具备交付条件 |

情绪移除的完整证据见 [`bench/results.md`](bench/results.md)，书面论证见
[`docs/RG660MK_人脸属性_实施方案_V2.pdf`](docs/RG660MK_人脸属性_实施方案_V2.pdf)。

> **阅读顺序**：先看 V2 PDF（结论 + 实测），再看 `bench/results.md`（原始数据）。
> V1 的两份文档保留作历史对照，其中情绪方案与耗时估算已被实测推翻。

## 架构

```
RG660MK camview:8090/snapshot   ← 只读 HTTP，不直接打开 USB 摄像头
        ↓ 局域网（约 40 KB/帧）
本机 attr_service:8093          ← YuNet 检测 + genderage 推理（只取性别头）
        ↓
JSON: {gender, gender_score}
```

**为什么推理在本机而不是设备上**：设备端 ncnn 的 Python 绑定在 musl 环境执行 `extract()` 必然段错误（实测 ncnn 1.0.20261020 / 20241226 / 20231027 三版，numpy 2.4.6 与 1.26.4，单线程 + 关 packing + 关 fp16 均崩）。C++ 路径可行但需交叉编译工具链。故采用本机推理，代价是依赖笔记本在线。

## 对 8099 的影响

**物理隔离**：推理 100% 在本机 CPU，设备侧只多一个 `GET /snapshot`（camview 本来就在持续产帧）。

三轮交替 A/B 实测（停服务/启服务各 8 次，交替 3 轮）：

| 轮次 | 服务停止 | 服务运行 |
|---|---|---|
| 1 | 23.7 ms | 25.3 ms |
| 2 | 24.9 ms | 17.4 ms |
| 3 | 18.2 ms | 24.9 ms |
| **均值** | **22.3 ms** | **22.5 ms** |

差 0.2 ms，方向来回跳 → 是设备自身 load 6.4 下的抖动，非服务引入。40 次连续推理压测同样无劣化（-5.1 ms，噪声方向）。全程 0 次失败。

## 部署

```bash
# 依赖：python3 + opencv + onnxruntime
pip3 install opencv-python onnxruntime numpy

# 模型（合计约 1.5 MB，不入库）
#   genderage.onnx  — InsightFace buffalo_l，https://hf-mirror.com/public-data/insightface
#   yunet.onnx      — OpenCV Zoo，face_detection_yunet_2023mar.onnx
# 放到 ~/models/face_attr/

# 启动
./attrctl.sh start        # 或 ~/attrctl start
./attrctl.sh status
./attrctl.sh bench 40     # 重跑 8099 影响压测
```

开机自启（用户级，无需 sudo）：
```bash
(crontab -l; echo "@reboot sleep 20 && $HOME/attrctl start >/dev/null 2>&1") | crontab -
```

## 实时预览页（推荐演示用）

```bash
~/attrctl start      # 同时起 API(8093) 和 预览页(8094)
```

浏览器打开 **http://127.0.0.1:8094/**（仅本机；首次访问弹出登录框，用户名任意，密码为笔记本服务令牌，见下节「令牌」）—— 摄像头画面 + 人脸框 + 性别/年龄段叠加，
多人时逐条列出，右上角显示实时状态。5–10 fps（受 snapshot 轮询限制，非推理瓶颈，单帧推理仅 12–32 ms）。

**为什么不直接改 8099**：8099 的页面内嵌在 `aicpe_demo.py` 的 `DASH_HTML` 字符串里，
改它必须重启 aicpe-board（2–5 秒中断且有起不来的风险）。演示主线不能赌，所以做独立页面。
8099 一个字节都没动。

## 令牌（必须配置，源码不含任何口令）

| 令牌 | 用途 | 读取顺序 |
|---|---|---|
| 笔记本服务令牌 | 保护本机 8093 / 8094 | 环境变量 `RG660MK_LOCAL_TOKEN`，否则 `~/.config/rg660mk/local_ops_token` |
| 设备令牌 | 访问设备 camview:8090、看板 8099 | 环境变量 `RG660MK_DEVICE_TOKEN`，否则 `~/.config/rg660mk/device_ops_token` |

```bash
mkdir -p ~/.config/rg660mk && umask 077
openssl rand -hex 24 > ~/.config/rg660mk/local_ops_token    # 笔记本服务令牌
# 设备令牌须与设备端 /data/ai_cpe/secrets/ops_token 一致（部署时由设备侧生成后拷入）
```

- 文件权限必须为 0600，否则启动失败。
- 默认监听 `127.0.0.1`，不再对局域网开放。
- `/healthz` 为公开存活探测，只返回 `{"ok": true}`，不含画面或属性数据。
- 其余接口需要 `Authorization: Bearer <令牌>`，或 Basic 认证（密码为令牌，用户名任意）。

## 接口

```bash
LTOK=$(cat ~/.config/rg660mk/local_ops_token)

# 抓设备当前画面并识别
curl -s -X POST http://127.0.0.1:8093/vision/attribute \
  -H "Authorization: Bearer $LTOK" -H "Content-Type: application/json" -d '{"snapshot":true}'

# 识别本地图片（只允许令牌持有者调用）
curl -s -X POST http://127.0.0.1:8093/vision/attribute \
  -H "Authorization: Bearer $LTOK" -H "Content-Type: application/json" -d '{"image":"/path/to.jpg"}'

# 存活探测（无需令牌）/ 详细状态（需要令牌）
curl -s http://127.0.0.1:8093/healthz
curl -s -H "Authorization: Bearer $LTOK" http://127.0.0.1:8093/health
```

响应：
```json
{"ok": true,
 "faces": [{"bbox": [193,191,131,170], "gender": "male",
            "gender_score": 0.988, "note": null}],
 "face_count": 1, "elapsed_ms": 11,
 "capability": "gender", "total_ms": 18}
```

## 模型预处理（踩过的坑，勿改）

- `genderage.onnx` **内置**归一化（图里有 Sub(127.5) → Mul(1/128)）→ 必须传**原始 0–255 的 RGB**。
  在外面再归一化会让所有输入（含随机噪声）输出同一结果。
- BGR 会把女性判成男性，**必须转 RGB**。
- `emotion-ferplus-8.onnx` 同样内置归一化（Sub 127.5 / Div 255）。
- YuNet 关键点顺序：右眼、左眼、鼻尖、右嘴角、左嘴角（与 ArcFace 模板顺序不同）。

## 已知限制

- 人脸小于 28 px 会跳过；侧脸超过约 45° 性别判定不稳
- 依赖笔记本在线；要纯端侧需交叉编译 C++ runner（未做）
- 年龄与情绪已移除：不是阈值或选型问题，两个方向不同的模型都无判别力，详见 bench/results.md §2.6

## 文件

```
attr_service_pc.py   服务主程序（v4，仅 gender + age_band）
attrctl.sh           启停/状态/压测
bench/               客观评测脚本与结果
docs/
  ├── RG660MK_人脸属性_实施方案_V2.pdf   ★ 当前有效（5 页，含全部实测数据）
  ├── 设计文档_V2.html                  V2 的 HTML 源，改完可直接重新渲染
  ├── RG660MK_人脸属性识别_设计文档_V1.pdf   历史：含已被否决的情绪方案
  ├── RG660MK_人脸属性_NCNN部署设计_V1.pdf   历史：耗时估算已被实测推翻
  └── RG660MK_人脸属性_思路图_V1.png          历史
```
