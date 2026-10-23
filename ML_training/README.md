# 机器学习模块预留

后续通过 `data_lib/index.json` 选择具有来源、单位和验证结果的数据，保存训练配置、数据版本及模型验证结果。高保真观测在 `data_lib/runs/high/`，低保真观测在 `data_lib/runs/low/<geometry_id>/`，几何特征在 `data_lib/datasets/point_clouds/`。失败运行和未重新验收的历史数据需要显式审查后才能用于训练。置信度、补点范围和点云最终格式仍待定。

本次建立目录和约定，尚未实现训练流程。

本目录的直接下级只有本文件（`README.md`）；实现训练流程前不新增其他文件，新增时必须同步更新本文件与工作区根目录的 `README.md`。
