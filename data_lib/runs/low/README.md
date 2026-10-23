# 低保真运行

`DATCOM/scripts/01_batch_datcom.py` 读取 `data_lib/inputs/cases.csv`。同一个 `geometry_id` 加同一个 `altitude_m` 是一次调用。归档写进 `<geometry_id>/`。`datcom.exe` 运行当下使用 `.work`，里面是 `for005.dat`、`for006.dat`、`for022.dat`，算完后删除。

当前总表有 4 行试算：`geometry_id` 暂为 `1`，高度 `0`，工况 C01、C02、C06、C07。对应归档是 `1/`。前一次试算把几何标识写成 `sample`，归档仍留在 `sample/`。

| 下级 | 类型 | 作用 |
|---|---|---|
| `1/` | 生成 | 当前总表这 4 行的归档，不入库，不要手工编辑 |
| `sample/` | 生成 | 前一次试算的归档，不入库，不要手工编辑 |
| `README.md` | 文件 | 本文件 |

`<geometry_id>/` 和 `.work` 由运行创建，不纳入版本管理。同一几何换一个高度再跑时，按几何标识命名的 `for006` 等归档会被这次调用覆盖；`<run_id>_datcom.csv` 和系数总表里的其它高度会保留。`run_id` 是 `<geometry_id>__<condition_id>__<altitude_m>`。
