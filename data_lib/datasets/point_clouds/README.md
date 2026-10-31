# 点云

每个 `geometry_id` 一个子目录，**目录名就是几何标识**。外形使用 14 个几何参数：`DATCOM/scripts/04_pointnet_demo.py` 用 `--geometry-id` 从 `data_lib/inputs/cases.csv` 读取 11 个主尺寸，或用 `--params` 读一份同名字段的 JSON；翼型三个参数、鼻锥头型（切线卵形 / 圆锥）与锥顶倒角都来自 `DATCOM/scripts/missile_spec.py` 前部配置区，与批量 DATCOM 共用同一份。`*_meta.json` 会记录头型（`nose_shape`），但**不记录翼型参数**（`fin_section` 与 `geometry.section` 都已剔除）。

默认根目录就是本目录（`--outdir` 可改成别的根目录）。带几何标识时输出到 `<根目录>/<geometry_id>/`；不带几何标识是纯演示、不建子目录，`missile_<N>.*` 直接落在根目录下。点数和最终格式仍待定。

**点数**：默认 16384。改 `DATCOM/scripts/04_pointnet_demo.py` 前部的 `DEFAULT_N_POINTS` 改默认值，或临时用 `-n / --n-points` 覆盖（`--seed` 同理，默认 0）。点数只影响采样密度，不影响外形，也不改几何校验的结论。

| 下级 | 类型 | 作用 |
|---|---|---|
| `1/` | 目录 | `--geometry-id 1` 生成的点云（16384 点），目录名就是几何标识 |
| `README.md` | 文件 | 本文件 |

`<geometry_id>/` 里的文件（下表的 `<id>` 即该目录名，例如 `1/1_pointcloud.ply`）：

| 文件 | 作用 |
|---|---|
| `<id>_pointcloud.ply`、`<id>_pointcloud.xyz` | 同一次的点云：PLY 为二进制（带法向），xyz 为纯文本坐标 |
| `<id>_pointcloud_labels.txt` | 同一次的部件标签，一行一个整数（0 头锥 / 1 圆柱弹身 / 2 尾端面 / 3 前组舵面 / 4 后组舵面） |
| `<id>_pointcloud_meta.json` | 同一次的参数、抽样统计与自检结果快照 |
| `<id>_pointcloud.obj` | 同一次的参考三角网格（与点云同一几何） |
| `<id>_pointcloud_preview.png` | `--preview` 画的三视图，仅供速查 |

同一个几何再跑一次会覆盖该目录里的同名文件（脚本不清理目录里其它文件）。归档里的 `*_meta.json` 是生成当时的快照：`1/` 里这份由当前脚本生成，含 `nose_shape`（头型与锥顶倒角）但不含翼型参数；更早版本的归档还可能带 `fin_section`、`nose_tip_cap`、`include_nose_tip` 等键，现行脚本不再写。

重新生成某个几何的点云（在 `DATCOM/` 下执行，默认就是 16384 点）：

```
python scripts/04_pointnet_demo.py --geometry-id 1
python scripts/04_pointnet_demo.py --geometry-id 1 -n 32768        # 换点数
python scripts/04_pointnet_demo.py --geometry-id 1 --preview       # 顺便画三视图
```

点云文件不纳入版本管理（`.gitignore` 忽略 `point_clouds/*`，只保留本文件），说明文件保留在仓库里。
