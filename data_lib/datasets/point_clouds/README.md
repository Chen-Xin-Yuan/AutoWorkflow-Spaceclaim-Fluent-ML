# 点云

每个 `geometry_id` 对应一份点云。`DATCOM/scripts/04_pointnet_demo.py` 用 `--geometry-id` 从 `data_lib/inputs/cases.csv` 读取 11 个主尺寸，或用 `--params` 读一份同名字段的 JSON，默认写到本目录。带几何标识时主文件是 `<geometry_id>_pointcloud.ply`。点数和最终格式仍待定。本目录目前没有点云文件。

| 下级 | 类型 | 作用 |
|---|---|---|
| `README.md` | 文件 | 本文件 |

以后生成的点云文件放在本目录，不纳入版本管理。说明文件保留在仓库里。
