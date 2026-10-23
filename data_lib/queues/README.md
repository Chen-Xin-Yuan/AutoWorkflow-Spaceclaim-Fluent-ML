# 阶段队列

主智能体按 `data_lib/inputs/cases.csv` 维护这些文件。总表里已有 4 行试算，`geometry_id` 暂为 `1`，高度 `0`，工况 C01、C02、C06、C07。标识符编码仍待定，这 4 行还没有写入队列。参数值以总表该行为准，队列保存几何标识、工况编号和高度。

| 下级 | 类型 | 作用 |
|---|---|---|
| `spaceclaim.json` | 文件 | SpaceClaim 队列，`stage` 为 `spaceclaim` |
| `fluent_meshing.json` | 文件 | Fluent Meshing 队列，`stage` 为 `fluent_meshing` |
| `fluent_solution.json` | 文件 | Fluent Solution 队列，`stage` 为 `fluent_solution`。求解流程仍待定 |
| `datcom.json` | 文件 | DATCOM 队列，`stage` 为 `datcom` |
| `ml.json` | 文件 | 训练轮次，`stage` 为 `ml`。按轮记录，不按工况拆行 |
| `README.md` | 文件 | 本文件 |

四份阶段队列的形状：

```json
{
  "schemaVersion": 1,
  "stage": "spaceclaim",
  "records": []
}
```

`records` 以后的每一条包含 `geometry_id`、`condition_id`、`altitude_m`、`status`、`artifact`、`log`、`execution_dir`、`updated_at`。`altitude_m` 是字符串，与总表单元格相同。未完成时，路径和时间写空字符串。查找记录时对照这三个标识字段。

`ml.json` 的形状：

```json
{
  "schemaVersion": 1,
  "stage": "ml",
  "rounds": []
}
```

每一轮以后包含 `status`、`log`、`data_version`、`artifact`、`cases`、`updated_at`。`cases` 的每一项只有 `geometry_id`、`condition_id`、`altitude_m`。

`status` 只用这四个词：未开始、进行中、已完成、失败待上报。同一阶段同时处于进行中的任务数，以及瞬时故障重试次数，写在 `myplan.md`，不写入这些 JSON。
