# 数据集

| 下级 | 类型 | 作用 |
|---|---|---|
| `point_clouds/` | 目录 | 点云：一个几何一个子目录，**目录名就是 `geometry_id`**（不带标识的演示点云直接放在根下）。外形由 14 个几何参数决定：`04_pointnet_demo.py` 用 `--geometry-id` 或 `--params` 读取 11 个主尺寸，翼型三个参数、鼻锥头型与倒角都来自 `DATCOM/scripts/missile_spec.py`（与 DATCOM 共用）。点数和最终格式仍待定 |
| `README.md` | 文件 | 本文件 |
