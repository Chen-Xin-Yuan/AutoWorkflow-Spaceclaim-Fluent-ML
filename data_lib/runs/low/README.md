# 低保真运行

`DATCOM/scripts/01_batch_datcom.py` 读取 `data_lib/inputs/cases.csv`。同一个 `geometry_id` 加同一个 `altitude_m` 是一次调用。归档写进 `<geometry_id>/`。`datcom.exe` 运行当下使用 `.work`，里面是 `for005.dat`、`for006.dat`、`for022.dat`，算完后删除。

`--demo` 的演示算例也写在这里，但目录名不是总表里的 `geometry_id`，`data_lib/runs/low/README.md` 的目录表不会列出它，验证完请手动删除。

当前总表有 4 行试算：`geometry_id` 暂为 `1`，高度 `0`，工况 C01、C02、C06、C07。对应归档是 `1/`。

| 下级 | 类型 | 作用 |
|---|---|---|
| `1/` | 生成 | 当前总表这 4 行的归档，不入库，不要手工编辑 |
| `README.md` | 文件 | 本文件 |

`<geometry_id>/` 和 `.work` 由运行创建，不纳入版本管理。同一几何换一个高度再跑时，按几何标识命名的 `for006` 等归档会被这次调用覆盖；系数总表里其它高度的行会保留，本次工况的行被替换。一个几何只保留一张系数表 `<geometry_id>_aero_coefficients.csv`（旧版本按工况拆出的 `<run_id>_datcom.csv` 已不再生成，脚本也不再自动清理历史遗留的那类文件）。表里一行一个工况，列是工况编号、来流、11 个主尺寸、舵面翼型（截面编号 + 三个参数，六边形与 NACA 两种口径的列名不同，缺的格子留空）和 3 个气动系数。
