# vendor/python311 — CPython 3.11.7 标准库补齐文件

设备（RG660MK-EU）固件自带 python3.11.7，但 stdlib 不全：缺 `decimal`（连锁 `fractions`/`statistics`
不可用）。这 4 个纯 Python 文件从 CPython 官方 v3.11.7 取出（PSF-2.0 License）：

- `decimal.py`（薄封装：`_decimal` C 扩展缺失时自动回退 `_pydecimal`）
- `_pydecimal.py` / `fractions.py` / `statistics.py`

来源：https://github.com/python/cpython （tag `v3.11.7`，`Lib/` 目录）。

部署：`deploy/install.sh` 拷贝到设备 `/data/ai_net/lib/python311`；工具调用时
`PYTHONPATH=/data/ai_net/lib/python311:...`。不动 `/usr`（overlay 空间紧张）。

设备端验证（2026-10-08）：

```
PYTHONPATH=/data/ai_net/lib/python311 python3 -c "import statistics; print(statistics.mean([1,2,3]))"  # → 2
```
