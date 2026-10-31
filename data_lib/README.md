# 文件数据仓库

- `inputs/`：参数与原始输入。`cases.csv` 是工作流输入总表，一行一个工况，几何列是 11 个主尺寸，并包含 `altitude_m`。几何参数一共 14 个，多出来的 3 个是翼型参数（`thickness_percent`、`flat_percent`、`le_radius` 或 NACA 那一组）。所有不进总表的外形数据——翼型三项、鼻锥头型（`NOSE_SHAPE` = ogive / cone）、锥顶倒角、舵面布局、基准几何——都集中在 `DATCOM/scripts/missile_spec.py` 前部的配置区，DATCOM 批量计算与点云共用。当前有 4 行试算：`geometry_id` 暂为 `1`，高度 `0`，工况 C01、C02、C06、C07。标识符编码仍待定。目前 `inputs/` 下只有 `cases.csv`，末列 `origin` 记录来源（当前都是 `human`），DATCOM 脚本不读取这一列。示例参数 JSON 不再随仓库保存，避免与总表、`DATCOM/scripts/missile_spec.py` 形成第三份参数。
- `queues/`：五份队列。四份阶段队列按工况记录状态，`ml.json` 按训练轮次记录。字段见 `queues/README.md`。这 4 行试算还没有写入队列。
- `datasets/`：数据集。`point_clouds/` 接收 `DATCOM/scripts/04_pointnet_demo.py` 的点云，一个几何一个子目录，目录名就是几何标识。脚本用 `--geometry-id` 或 `--params` 读取 11 个主尺寸，并套用 `missile_spec.py` 的 3 个翼型参数与头型。点数和最终格式仍待定。
- `runs/`：运行目录。高保真在 `high/<run-id>/<阶段>/`，`<run-id>` 是 `run-<时间戳>-<短ID>`。低保真在 `low/<geometry_id>/`。说明见 `runs/README.md`。
- `datcom_data/`：早先 DATCOM 脚本写出的算例结果，保留作历史记录。当前 `01_batch_datcom.py` 改写到 `runs/low/<geometry_id>/`。
- `legacy/`：历史模型、网格、日志和 workflow，保留原文件内容；`migration-manifest.json` 解释旧路径与来源。
- `runtime/`：运行期支撑数据；`test-support/` 供测试使用，当前为空。
- `index.json`：由 `scripts/run.ps1 index` 更新的统一索引。
- `README.md`：本文件。

合格记录需同时满足 `status=PASSED` 与 `verification.runtimeVerified=true`；仅生成脚本的记录状态为 `GENERATED`。
历史 PASS 字样不能替代新运行验收；失败包通过索引关联到 `feedback`。
