# 低保真运行

`DATCOM/scripts/01_batch_datcom.py` 读输入表（派发器给的是数据库导出的 25 列表，用 `--input` 传入）。
同一个 `geometry_id` 加同一个 `altitude_m` 是一次调用，一次调用里可以混跑 `hex` / `naca` 两种翼型和
`cone` / `ogive` 两种头型。归档写进 `<geometry_id>/`。`datcom.exe` 运行当下使用 `.work`，算完后删除。

| 下级 | 类型 | 作用 |
|---|---|---|
| `<geometry_id>/` | 生成 | 一个几何一份归档，不入库，不要手工编辑；归档文件名以几何标识开头 |
| `<geometry_id>/<run_id>/run.json` | 生成 | 每批一份运行记录：`run_id`、每工况的 `CL/CD/CM`、卡片实际用的翼型/头型、几何自检结论、退出码、产物 sha256 |
| `1/` | 生成 | **旧体系**（`geometry_id=1`）的历史归档，保留作证据，不迁入库 |
| `.work/` | 生成 | `datcom.exe` 的协议工作目录（`for005.dat` / `for006.dat` / `for022.dat`），算完删除 |
| `README.md` | 文件 | 本文件 |

`run_id` 是 `<geometry_id>__shared__<时间戳>`（一次 DATCOM 调用覆盖多个工况，中段写 `shared`）。
数据库 `datcom_task.run_id` 用的是同一个值，审核与上报都靠它对齐。

## 归档规则

- 一个几何只保留一张系数表 `<geometry_id>_aero_coefficients.csv`，一行一个工况。
- 同一几何换一个高度再跑时，按几何标识命名的 `for006` 等归档会被这次调用覆盖；
  系数总表里其它高度的行保留，本次工况的行替换。
- 翼型与头型**不进系数表**，只进 `run.json`（`shape` 段）。
- `--demo` 的演示算例也写在这里，但目录名不是库里的 `geometry_id`，本文件的目录表不列出，
  验证完请手动删除。
- `<geometry_id>/` 与 `.work/` 由运行创建，不纳入版本管理。
