# 异常反馈

失败产物按 `feedback/<stage>/<run_id>/` 存放，永久保留，不参与 `gc`。

| 下级 | 类型 | 作用 |
|---|---|---|
| `spaceclaim/` | 生成 | 建模阶段的失败包 |
| `fluent_meshing/` | 生成 | 网格阶段的失败包 |
| `fluent_solution/` | 生成 | 求解阶段的失败包（求解脚本未实现，当前没有） |
| `datcom/` | 生成 | 低保真阶段的失败包（P4 接入后开始产生） |
| `README.md` | 文件 | 本文件 |

## 包内是什么

一次运行的完整副本：`run.json`、`params.json`、`model.py`、诊断与日志、已经产生的产物。
`run.json` 里的 `pathMapping` 记录它原本在 `data_lib/runs/…` 的哪个位置。

- 建模 / 网格 / 求解失败时，由 SpaceClaim / Fluent 运行器把整个运行目录**移动**到这里。
- 派发器（`dispatch.py`）另外把命令行输出写成 `<geometry_id>_<stage>.log` 一并留在包里。
- 目录名就是 `run_id`（`<geometry_id>__<condition_id>__<时间戳>`），与数据库里的
  `review.run_id` / `report.last_error` 对得上。

## 与 `report` 表的关系

**这个目录本身不是上报。** 每一份失败包在库里都对应一条 `report` 记录（`status='open'`），
主智能体看库决定"原参数重跑 / 改设置新建行 / 请人决定"，结论写回 `report.resolution`。

```powershell
python scripts/control.py status                 # 各阶段七态计数
python scripts/db.py report --stage spaceclaim --geometry-id <id> --run-id <run_id> --error "…"
```

## 保留策略

- 失败包**永不自动清理**：它是复盘唯一的一手证据。
- 大输入不复制，用工作区相对路径 + 大小 + SHA-256 引用 `data_lib` 里的原件。
- 主动分步停止（`stop`）记录为 `cancelled`，产物留在 `data_lib/runs/` 里，**不进**这个目录。
