# 运行目录

高保真和低保真分开存放。运行产物不纳入版本管理，本目录和两个子目录的说明文件纳入版本管理。

| 下级 | 类型 | 作用 |
|---|---|---|
| `high/` | 目录 | 高保真。路径是 `high/<stage>/<run_id>/`，`<stage>` ∈ `spaceclaim` / `fluent_meshing` / `fluent_solution` |
| `low/` | 目录 | 低保真 DATCOM。路径是 `low/<geometry_id>/` |
| `README.md` | 文件 | 本文件 |

## `run_id` 的形状

```text
<geometry_id>__<condition_id>__<时间戳>
g692bcb5d5e50__shared__20261101T101500Z          ← 几何级产物（建模、网格、DATCOM 批次）
g692bcb5d5e50__cc9633c86a360__20261101T110500Z   ← 按工况（求解）
```

- 三段用 `__` 分隔，从右往左读：时间戳、`condition_id`、`geometry_id`。
- `stage` **不进** `run_id`：它已经是 `runs/high/<stage>/` 的父目录名。
- 时间戳是 UTC 的 `YYYYMMDDTHHMMSSZ`。`run_id` 里唯一无法由身份推出的信息就是"这是第几次跑"，
  所以重跑会自然得到新目录，不会覆盖上一次的产物。
- 手工运行没给 `--run-id` 时，SpaceClaim 退回旧的自生成形式 `run-<时间戳>-<随机短串>`；
  数据库管下的运行一律使用三段式。

## 目录顺序

旧版是 `<run-id>/<阶段>/`，本版倒过来成 `<阶段>/<run_id>/`：同一阶段的所有运行挨在一起，
清理失败产物、按阶段统计都只看一个目录。
