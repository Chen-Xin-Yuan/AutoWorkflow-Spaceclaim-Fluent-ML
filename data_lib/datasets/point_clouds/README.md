# 点云

每个 `geometry_id` 对应一份点云。外形使用 14 个几何参数。`DATCOM/scripts/04_pointnet_demo.py` 用 `--geometry-id` 从 `data_lib/inputs/cases.csv` 读取 11 个主尺寸，或用 `--params` 读一份同名字段的 JSON；翼型三个参数 `thickness_percent`、`le_radius_index`、`max_thickness_tenths` 来自 `DATCOM/scripts/fin_section.py`。默认写到本目录。带几何标识时主文件是 `<geometry_id>_pointcloud.ply`。点数和最终格式仍待定。

本目录已有不带几何标识的演示结果：`missile_8192.*`、`missile_16384.*` 和 `missile_surface.obj`。按 `geometry_id` 命名的点云尚未生成。

| 下级 | 类型 | 作用 |
|---|---|---|
| `missile_8192.*`、`missile_16384.*`、`missile_surface.obj` | 生成 | 不带几何标识的演示点云，不入库，不要手工编辑 |
| `README.md` | 文件 | 本文件 |

以后生成的点云文件放在本目录，不纳入版本管理。说明文件保留在仓库里。
