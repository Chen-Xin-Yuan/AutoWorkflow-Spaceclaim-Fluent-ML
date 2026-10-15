# 文件数据仓库

- `inputs/`：参数与原始输入；`missile_params_example.json` 是只填写图中 11 个主尺寸的参数示例，`params_mine.json` 是配套提示词使用的自有参数，`fluent_meshing/geometry/` 保存网格阶段使用的几何。
- `legacy/`：历史模型、网格、日志和 workflow，保留原文件内容；`migration-manifest.json` 解释旧路径与来源。
- `runs/`：每次运行一个 `run-<时间戳>-<短ID>/<stage>/` 目录，以 `run.json` 记录状态和验证依据，`request.json` 记录本次参数。
- `runtime/`：运行期支撑数据；`test-support/` 供测试使用，当前为空。
- `index.json`：由 `scripts/run.ps1 index` 更新的统一索引。
- `README.md`：本文件。

合格记录需同时满足 `status=PASSED` 与 `verification.runtimeVerified=true`；仅生成脚本的记录状态为 `GENERATED`。
历史 PASS 字样不能替代新运行验收；失败包通过索引关联到 `feedback`。
