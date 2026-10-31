# 点云

每个 `geometry_id` 一个子目录，**目录名就是几何标识**。外形来自**数据库的 16 个参数**：`DATCOM/scripts/04_pointnet_demo.py --geometry-id <id>` 从 `data_lib/datasets/SQLite/missile.db` 读 11 个主尺寸、翼型种类与三参数、鼻锥头型（库里查不到该几何时才回落到 `cases.csv`，那只有 11 个主尺寸）。`*_meta.json` 现在记 `main_dimensions`（11 个主尺寸）与头型；**翼型三参数尚未写进 meta**，所以审核判据 6 目前能核 12/16。点云永久保留，`gc` 不删。点数默认 16384（`DEFAULT_N_POINTS`，或 `-n` 临时覆盖），最终格式仍待定。

默认根目录就是本目录（`--outdir` 可改成别的根目录）。带几何标识时输出到 `<根目录>/<geometry_id>/`；不带几何标识是纯演示、不建子目录，`missile_<N>.*` 直接落在根目录下。点数和最终格式仍待定。

**点数**：默认 16384。改 `DATCOM/scripts/04_pointnet_demo.py` 前部的 `DEFAULT_N_POINTS` 改默认值，或临时用 `-n / --n-points` 覆盖（`--seed` 同理，默认 0）。点数只影响采样密度，不影响外形，也不改几何校验的结论。

| 下级 | 类型 | 作用 |
|---|---|---|
| `1/` | 目录 | **旧体系**（`geometry_id=1`）的历史点云，保留作证据，不迁入库 |
| `g692bcb5d5e50/` | 目录 | 新体系下的第一个几何（16 参数、`hex` 翼型、`cone` 头型），由 `--geometry-id` 从数据库生成 |
| `g48ee1449b30c/` | 目录 | 当前在跑的几何（`flat_percent` 64.6，平顶终点 0.823），由 `--geometry-id` 从数据库生成 |
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
