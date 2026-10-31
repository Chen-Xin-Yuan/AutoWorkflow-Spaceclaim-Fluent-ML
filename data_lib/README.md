# 文件数据仓库

- `inputs/`：参数与原始输入。`high_fidelity.csv`（高保真）与 `low_fidelity.csv`（低保真）各 23 列：11 个主尺寸、6 个翼型列（`fin_section_type` ＋ 两套各三个参数）、`nose_shape`、`mach` / `alpha_deg` / `altitude_m`，外加 `origin` 与 `label`。**两份表不含 ID**，`geometry_id` / `condition_id` 由参数算出，所以同一行永远得到同一个身份。两份表只在初始化导入时读（`python scripts/db.py import --table high`），之后只改数据库。`cases.csv` 是旧输入总表，`geometry_id` 暂为 `1` 的那批算例保留作历史记录，不迁入新库。
- `queues/`：两个**只读视图**目录 `scdoc_for_fluent/`（已通过审核的模型指针）与 `meshing_for_solution/`（已通过审核的网格指针）。指针由 `python scripts/db.py export --format queue` 从库里导出，可整目录删掉重建；任务状态的真身在数据库。见 `queues/README.md`。
- `datasets/`：数据集。`SQLite/missile.db` 是全工作区的**唯一真相源**（几何、工况、任务状态、审核凭证都在库里），建库用 `python scripts/db.py init`，见 `datasets/SQLite/README.md`。`point_clouds/` 接收 `DATCOM/scripts/04_pointnet_demo.py` 的点云，一个几何一个子目录，目录名就是几何标识。脚本用 `--geometry-id` 或 `--params` 读取 11 个主尺寸，并套用 `missile_spec.py` 的 3 个翼型参数与头型。点数和最终格式仍待定。
- `runs/`：运行目录。高保真在 `high/<stage>/<run_id>/`，`run_id` 是 `<geometry_id>__<condition_id>__<时间戳>`；低保真在 `low/<geometry_id>/`。说明见 `runs/README.md`。
- `legacy/`：历史模型、网格、日志和 workflow，保留原文件内容；`migration-manifest.json` 解释旧路径与来源。
- `runtime/`：运行期支撑数据（不入库）：`logs/`（派发器抓的子进程输出）、`reviews/`（审核 JSON）、`training/`（训练明细）、`test-support/`（测试用）。
- `index.json`：**只读索引视图**，由 `python scripts/db.py export --format index` 从数据库生成（`run.ps1 index` 是别名）。内容全部来自库，随时可重建，不要手工编辑。
- `README.md`：本文件。

合格记录需同时满足 `status=PASSED` 与 `verification.runtimeVerified=true`；仅生成脚本的记录状态为 `GENERATED`。
历史 PASS 字样不能替代新运行验收；失败包通过索引关联到 `feedback`。
