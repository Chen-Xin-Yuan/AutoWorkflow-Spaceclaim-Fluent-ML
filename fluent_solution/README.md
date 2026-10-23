# 自动求解模块预留

后续从 `data_lib/index.json` 选择已验收网格作为输入，按执行目录保存配置、求解结果和验证记录至 `data_lib/runs/high/<run-id>/fluent_solution/`。`<run-id>` 是 `run-<时间戳>-<短ID>`。失败数据归入 `feedback/fluent_solution/<run-id>/`。求解器仍待定。

本次建立目录和数据约定，尚未实现或验证自动求解入口。

本目录的直接下级只有本文件（`README.md`）；实现求解入口前不新增其他文件，新增时必须同步更新本文件与工作区根目录的 `README.md`。
