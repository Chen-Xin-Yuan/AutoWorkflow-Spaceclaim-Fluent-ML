# 点云

每个 `geometry_id` 对应一份点云。外形使用 14 个几何参数。`DATCOM/scripts/04_pointnet_demo.py` 用 `--geometry-id` 从 `data_lib/inputs/cases.csv` 读取 11 个主尺寸，或用 `--params` 读一份同名字段的 JSON；翼型三个参数、鼻锥头型（切线卵形 / 圆锥）与锥顶倒角都来自 `DATCOM/scripts/missile_spec.py` 前部配置区，与批量 DATCOM 共用同一份。`*_meta.json` 会记录头型（`nose_shape`），但**不记录翼型参数**（`fin_section` 与 `geometry.section` 都已剔除）。默认写到本目录。带几何标识时主文件是 `<geometry_id>_pointcloud.ply`。点数和最终格式仍待定。

本目录已有按 `geometry_id` 命名的 `1_pointcloud.*`（4096 点，带自绘预览图），以及不带几何标识的演示结果 `missile_8192.*`、`missile_16384.*` 和 `missile_surface.obj`。归档里的 `*_meta.json` 是生成当时的快照：早期版本还带 `fin_section`、`nose_tip_cap`、`include_nose_tip` 等键，现行脚本不再写这些字段，重新生成时即消失。

| 下级 | 类型 | 作用 |
|---|---|---|
| `1_pointcloud.ply`、`1_pointcloud.xyz` | 生成 | 用 `--geometry-id 1` 生成的点云（4096 点），不入库，不要手工编辑 |
| `1_pointcloud_labels.txt` | 生成 | 同一次的部件标签，一行一个整数 |
| `1_pointcloud_meta.json` | 生成 | 同一次的参数、抽样统计与自检结果快照 |
| `1_pointcloud.obj` | 生成 | 同一次的参考三角网格（与点云同一几何） |
| `1_pointcloud_preview.png` | 生成 | `--preview` 画的三视图，仅供速查 |
| `missile_8192.ply`、`missile_8192.xyz`、`missile_8192_labels.txt`、`missile_8192_meta.json` | 生成 | 不带几何标识的演示点云（8192 点） |
| `missile_16384.ply`、`missile_16384.xyz`、`missile_16384_labels.txt`、`missile_16384_meta.json` | 生成 | 不带几何标识的演示点云（16384 点） |
| `missile_surface.obj` | 生成 | 演示的参考三角网格 |
| `README.md` | 文件 | 本文件 |

以后生成的点云文件放在本目录，不纳入版本管理。说明文件保留在仓库里。
