# 阶段队列（只读视图）

**这些目录不是第二份真相。** 数据库 `data_lib/datasets/SQLite/missile.db` 是唯一真相源；
队列目录里的文件是 `db.py` 从库里导出的**指针文件**，可以整目录删掉重建。

```powershell
python scripts/db.py export --format queue --queue scdoc_for_fluent
python scripts/db.py export --format queue --queue meshing_for_solution
```

`record_review` 在建模 / 网格审核通过时会自动重写对应的队列目录，所以正常情况下不需要手工执行。

| 下级 | 类型 | 作用 |
|---|---|---|
| `scdoc_for_fluent/` | 生成 | 已通过审核的模型（`.scdoc`）指针，下游是 `fluent_meshing` |
| `meshing_for_solution/` | 生成 | 已通过审核的网格（`.msh.h5`）指针，下游是 `fluent_solution` |
| `README.md` | 文件 | 本文件 |

## 指针文件

一个通过审核的产物一个文件，文件名就是 `run_id`：

```text
data_lib/queues/scdoc_for_fluent/<run_id>.json
```

```json
{
  "schemaVersion": 1,
  "queue": "scdoc_for_fluent",
  "run_id": "g692bcb5d5e50__shared__20261101T101500Z",
  "geometry_id": "g692bcb5d5e50",
  "condition_id": "shared",
  "artifact": {
    "rel_path": "data_lib/runs/high/spaceclaim/<run_id>/g692bcb5d5e50_spaceclaim.scdoc",
    "size": 4488574,
    "sha256": "…"
  },
  "review_id": "r0a9f2c81d3be",
  "approved_at": "2026-11-01T10:15:00Z"
}
```

- `condition_id` 恒为 `shared`：模型和网格是按几何做的，不分工况。
- `artifact.rel_path` 是工作区相对路径，换机器、整体挪目录都不失效。
- 指针里不放产物本体；产物在 `data_lib/runs/high/<stage>/<run_id>/`。
- 产物文件不在了（被 `gc` 删过）就不写指针——指向不存在文件的指针比没有指针更坏。

## 旧的五份 JSON 队列已删除

`spaceclaim.json`、`fluent_meshing.json`、`fluent_solution.json`、`datcom.json`、`ml.json`
记录的是"按工况逐行维护状态"的那套做法，已由数据库的状态机取代（见
`data_lib/datasets/SQLite/README.md`），不再恢复。任务状态查库：

```powershell
python scripts/control.py status
```
