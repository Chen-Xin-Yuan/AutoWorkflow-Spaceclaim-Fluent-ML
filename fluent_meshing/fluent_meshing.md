# Fluent Meshing 工作流配置与历史记录

文档同步日期：2026-10-22。历史参数提取日期：2026-09-20。

本文保留原 `missile.msh.h5` 的配置提取记录，并补充当前自动化脚本如何使用这些参数。下文第 1–7 节及附录 A
按历史工作台截图顺序记录保存值；“当前脚本与运行约定”及各节的当前实现说明以现有源码为依据。
本次仅整理既有配置位置和说明，未重新读取历史网格、计算质量或执行 Fluent；历史任务状态、警告、数量和耗时不作为新运行的验收结果。

命名约定：历史 `TaskObject14` 的控制名 `curvature_1` 在本文和当前脚本中统一为 `missile_nose_tip`，
附录中的对应控制名写为 `missile_nose_tip_faces`。源网格文件未修改；当前工作流比较工具会归一此别名。

## 当前脚本与运行约定

当前仅支持专用的 `recorded` 工作流。`mesh_missile.py` 定义参数和任务，`mesh_runner.py` 管理执行与导出，
`mesh_validation.py` 和 `workspace_runtime.py` 完成会话内检查、独立文件核验与运行记录。
完整命令行和环境配置见 [README.md](README.md)。

输入输出文件名、目录来源、覆盖顺序及可调默认值现在集中于各脚本前部的“输入 / 输出 / 参数配置”区。
`mesh_missile.py` 前部的 `P_*` 表仍是工作流参数唯一来源；进程数和运行超时等 CLI 默认值位于
`mesh_runner.py`，运行记录与独立核验路径位于 `workspace_runtime.py`，校验报告名与原有阈值位于
`mesh_validation.py`。本次没有改变下面记录的参数数值、必填模型输入或动态运行目录规则。

以下命令在本仓库目录中使用 PowerShell 执行，`run-xxxx` 替换为实际几何生成记录目录：

```powershell
.\run_meshing.cmd --geometry ..\data_lib\runs\run-xxxx\spaceclaim\missile.scdoc -n 16
```

| 项目 | 当前行为 |
| --- | --- |
| 输入几何 | `--geometry` 必填，没有默认模型；导入时设置 `LengthUnit=mm`、`MaxFacetLength=0`，部件数量由输入决定 |
| 参数基线 | 按下文的尺寸、面网格、共享拓扑、区域更新、边界层和体网格设置执行；历史底层字段不一定由脚本显式设置 |
| 必需标签 | `missile_body`、`missile_nose`、`missile_nose_tip`、`missile_fin`、`missile-boi`；标签查询可用时检查是否存在，查询不可用时记录警告 |
| 区域名称 | 优先读取任务的 `RegionNameList`，其次 `OldRegionNameList`；两者为空时尝试历史参考名 `zone----enclosure----`，之后核对实际生成的流体区域 |
| 边界层范围 | `BLRegionList` 使用区域更新结果，`RegionScope` 保留 `missile`；Fluent 返回 `CompleteBLZoneList` 时逐项匹配配置的面区域，缺失或多义报错；未返回时保留配置并在导出阶段检查边界组 |
| 体网格范围 | 尺寸列表保留 `missile` 的 25 mm 设置，将历史 enclosure 条目替换为区域更新结果的第一个区域名并使用 500 mm；生成后必须报告唯一非空流体区域，且与更新结果一致 |
| 运行限制 | 默认 16 进程、启动超时 300 秒、工作进程时限 900 秒；独立文件核验另有 600 秒时限 |
| 数据位置 | 读取父目录的 `workspace.json`；默认运行目录为 `data_lib/runs/<run-id>/fluent_meshing/`，失败记录及产物归 `feedback/fluent_meshing/<run-id>/`；路径均相对工作区根目录 |
| 输出约束 | 默认网格为本次运行目录下的 `missile_mesh.msh.h5`；`--run-dir` 必须为新目录，`--out` 文件必须不存在；运行数据保存在代码仓库外 |
| 环境隔离 | PyFluent 使用 `_deps/`；导出核验使用独立解释器与 `.mesh_inspect_deps_313/`；旧 `.mesh_inspect_deps/` 保留，三者不合并、不混用 Python/NumPy 环境 |

当前执行沿用历史编号 1–7，其中第 4 步含共享拓扑和创建区域两个子步骤；随后执行网格检查、
原生正交质量读取、导出和独立 HDF5 文件核验。当前检查要求最小单元体积为正、最小正交质量大于 0.01，
导出为本次生成的非空三维毫米网格，包含 `inlet`、`outlet`、`wall`、非空 missile 壁面和唯一非空流体单元区域，
并与原生质量报告的单元数交叉核对。这些检查不等同于逐处测量边界层层数或完成求解器边界条件设置。

结果以新运行的 `run.json` 为准：`PASSED` 表示完整流程及独立核验通过；`STOPPED` 表示按 `--stop-after`
主动停止，即使退出码为 0 也未完成验收；`FAILED` 保留失败证据。质量证据见 `validation.trn`、`quality-native.json`、
`quality.json`，独立文件读取结果见 `mesh-inspection.json` 和 `verification.exportedMesh`。
完整、主动停止及失败时分别尝试保存 `completed_workflow.wft`、`stopped_workflow.wft` 和 `failed_workflow.wft`。

当前参数比较工具 `tools/compare_with_reference.py` 默认比较历史归档；比较新运行时需显式传入新的 `.wft`。
工具归一数值表示和控制别名，只忽略按任务列明的记录字段；`DrawSizeControl` 等未列为忽略项的参数仍参与比较。

## 历史数据来源与读取说明

以下归档路径相对工作区根目录，替代迁移前的机器绝对路径；文件大小、时间和版本信息保留历史提取结果。

| 项目 | 记录 |
| --- | --- |
| 主文件（当前归档） | `data_lib/legacy/fluent_meshing/reference/missile.msh.h5` |
| 文件大小 | 516,167,042 字节 |
| 文件最后修改时间（历史记录） | 2026-09-20 11:27:39（原记录本机时间） |
| 写出软件 | ANSYS FLUENT MESHING 2024 R1 |
| 版本字段 | `24.1` |
| 工作流 | `Watertight Geometry` |
| 维度 | 三维 |
| 长度 / 面积 / 体积单位 | `mm` / `mm^2` / `mm^3` |
| 参数主来源 | HDF5 的 `/settings/TGrid Variables` → `workflow/state` → 各任务的 `Arguments` |
| 辅助工作流文件（当前归档） | `data_lib/legacy/fluent_meshing/reference/missile_workflow_files/missile.wft` |
| 辅助运行日志（当前归档） | `data_lib/legacy/fluent_meshing/reference/fluent-20260920-105954-19336.trn` |

工作台截图用于核对步骤名称和顺序，具体数值来自文件。本文以最终网格内嵌的工作流状态为准；辅助 `.wft` 中任务状态为 `Out-of-date`，不能据此覆盖最终网格内的 `Up-to-date` 状态。尺寸数值均按保存单位 mm 展示，角度按度展示，增长率无量纲。浮点存储值 `0.07000000000000001` 在正文简写为 `0.07`。

文件中未显式保存的界面项标为“未显式保存”，不补成默认值。任务中保存的字段可能包含界面保留值；保存了某个值，不等于该值在所选方法下实际参与计算。历史提取为只读操作，未重新执行工作流或修改源文件。

## 历史工作流顺序

```text
Watertight Geometry
├─ 1. 导入几何模型
├─ 2. 添加局部尺寸
│  ├─ 2.1 missile_body
│  ├─ 2.2 missile_nose
│  ├─ 2.3 missile_nose_tip
│  ├─ 2.4 missile_fin
│  └─ 2.5 boi_1
├─ 3. 生成面网格
├─ 4. 描述几何结构
│  ├─ 4.1 使用共享拓扑
│  └─ 4.2 创建区域
├─ 5. 更新区域类型设定
├─ 6. 添加边界层
│  └─ last-ratio_1
└─ 7. 生成体网格
```

历史提取记录中，以上活动任务在最终 `.msh.h5` 中均为 `Up-to-date`，其 `Errors` 列表均为空。共享拓扑和创建区域任务各有一条保存的警告，见对应章节；新运行的任务状态须由新记录确认。

## 1. 导入几何模型（Import Geometry）

任务：`TaskObject1`；命令：`ImportGeometry`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 原始几何文件（历史路径仅保留文件名） | `missile.scdoc` | `FileName` |
| 长度单位 | mm | `LengthUnit` |
| 部件数量字段 | 3 | `NumParts` |
| 最大离散面片长度字段 | 0 | `ImportCadPreferences.MaxFacetLength` |

`MaxFacetLength = 0` 是文件中的原始设置值，不表示实际生成了零长度网格。

当前脚本将 `FileName` 设置为显式传入的 `--geometry` 绝对路径，不沿用历史输入位置，也不固定 `NumParts=3`。
下表的底层导入选项保留历史记录，当前脚本只显式设置上表的长度单位、最大面片长度和输入文件，不保证其余选项逐项复现。

底层 `cad/import-options`、`cad/adv-options` 还保存了以下导入记录，与上述工作流字段分开列示：

| 配置项 | 保存值 |
| --- | --- |
| 中间几何文件（历史路径简写） | `missile_workflow_files/missile.scdoc.pmdb` |
| 特征角 `feature-angle` | 40 |
| 缩放系数 `scale-factor` | 1 |
| `one-zone-per` | `program-controlled` |
| `one-face-zone-per` | `body` |
| 导入曲率 `import-curvature?` | true |
| 提取特征 `extract-feature?` | true |
| 导入命名选择 `import-ns?` | true |
| 导入命名选择标签 `import-named-selection-labels?` | true |
| 导入体名称 `import-body-names?` | true |
| 导入部件名称 `import-part-names?` | false |

## 2. 添加局部尺寸（Add Local Sizing）

父任务：`TaskObject2`；命令：`AddLocalSizingWTM`；`AddChild = yes`。

共有 5 个子任务，顺序与截图一致。下面记录每个子任务保存的全部尺寸类参数。

### 2.1 missile_body

任务：`TaskObject12`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 控制名称 | `missile_body` | `BOIControlName` |
| 尺寸方法 | Curvature（曲率） | `BOIExecution` |
| 作用标签 | `missile_body` | `BOIFaceLabelList` |
| 最小尺寸 | 3 mm | `BOIMinSize` |
| 最大尺寸 | 30 mm | `BOIMaxSize` |
| 增长率 | 1.18 | `BOIGrowthRate` |
| 曲率法向角 | 18° | `BOICurvatureNormalAngle` |
| 每间隙单元数 | 1 | `BOICellsPerGap` |
| 另存的尺寸字段 | 18 mm | `BOISize` |

### 2.2 missile_nose

任务：`TaskObject13`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 控制名称 | `missile_nose` | `BOIControlName` |
| 尺寸方法 | Curvature（曲率） | `BOIExecution` |
| 作用标签 | `missile_nose` | `BOIFaceLabelList` |
| 最小尺寸 | 2 mm | `BOIMinSize` |
| 最大尺寸 | 18 mm | `BOIMaxSize` |
| 增长率 | 1.18 | `BOIGrowthRate` |
| 曲率法向角 | 16° | `BOICurvatureNormalAngle` |
| 每间隙单元数 | 1 | `BOICellsPerGap` |
| 另存的尺寸字段 | 9 mm | `BOISize` |

### 2.3 missile_nose_tip

历史任务：`TaskObject14`，原控制名为 `curvature_1`。本文和当前脚本使用 `missile_nose_tip`，作用标签仍为 `missile_nose_tip`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 控制名称（文档及当前脚本） | `missile_nose_tip`（历史名 `curvature_1`） | `BOIControlName` |
| 尺寸方法 | Curvature（曲率） | `BOIExecution` |
| 作用标签 | `missile_nose_tip` | `BOIFaceLabelList` |
| 最小尺寸 | 1.6 mm | `BOIMinSize` |
| 最大尺寸 | 10 mm | `BOIMaxSize` |
| 增长率 | 1.15 | `BOIGrowthRate` |
| 曲率法向角 | 18° | `BOICurvatureNormalAngle` |
| 每间隙单元数 | 1 | `BOICellsPerGap` |
| 另存的尺寸字段 | 6 mm | `BOISize` |

### 2.4 missile_fin

任务：`TaskObject15`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 控制名称 | `missile_fin` | `BOIControlName` |
| 尺寸方法 | Curvature（曲率） | `BOIExecution` |
| 作用标签 | `missile_fin` | `BOIFaceLabelList` |
| 最小尺寸 | 3 mm | `BOIMinSize` |
| 最大尺寸 | 40 mm | `BOIMaxSize` |
| 增长率 | 1.2 | `BOIGrowthRate` |
| 曲率法向角 | 18° | `BOICurvatureNormalAngle` |
| 每间隙单元数 | 1 | `BOICellsPerGap` |
| 另存的尺寸字段 | 未显式保存 | `BOISize` |

前 4 项选择的是曲率方法；其中 `BOISize` 是另行保留的字段，不能将其替代最小、最大尺寸，或解释为已核实生效的统一面尺寸。

### 2.5 boi_1

任务：`TaskObject16`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 控制名称 | `boi_1` | `BOIControlName` |
| 尺寸方法 | Body Of Influence（影响体） | `BOIExecution` |
| 作用标签 | `missile-boi` | `BOIFaceLabelList` |
| 目标尺寸 | 30 mm | `BOISize` |
| 增长率 | 1.2 | `BOIGrowthRate` |
| 另存的最小尺寸字段 | 15 mm | `BOIMinSize` |
| 另存的最大尺寸字段 | 100 mm | `BOIMaxSize` |
| 另存的曲率法向角字段 | 16° | `BOICurvatureNormalAngle` |
| 另存的每间隙单元数字段 | 1 | `BOICellsPerGap` |

日志第 219 行明确记录了对 `missile-boi` 添加目标尺寸为 30 的 Body of Influence。其余保留字段按原样列出，不据此认定全部参与 BOI 计算。

### 2.6 五项共同保存的选项

| 配置项 | 保存值 |
| --- | --- |
| `AddChild` | `yes` |
| 选择方式 `BOIZoneorLabel` | `label` |
| 显示尺寸控制 `DrawSizeControl` | true |
| `CompleteFaceLabelList` | 与各自的 `BOIFaceLabelList` 相同 |
| 任务状态 | `Up-to-date` |
| `Errors` / `Warnings` | 均为空 |

以上为历史共同字段。当前脚本显式使用标签选择并在创建子任务时传入 `AddChild=yes`；
`DrawSizeControl` 默认 `true`，可由 `--draw-size-control false` 关闭。`CompleteFaceLabelList`、任务状态和错误列表由新会话返回，不直接从历史记录复制。

## 3. 生成面网格（Generate the Surface Mesh）

任务：`TaskObject3`；命令：`GenerateTheSurfaceMeshWTM`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 全局最小尺寸 | 15 mm | `CFDSurfaceMeshControls.MinSize` |
| 全局最大尺寸 | 300 mm | `CFDSurfaceMeshControls.MaxSize` |
| 增长率 | 1.2 | `CFDSurfaceMeshControls.GrowthRate` |
| 每间隙单元数 | 2 | `CFDSurfaceMeshControls.CellsPerGap` |
| 邻近作用范围 | faces（面） | `CFDSurfaceMeshControls.ScopeProximityTo` |
| 本步骤执行共享拓扑 | No | `ExecuteShareTopology` |
| 曲率法向角 | 本任务参数块未显式保存 | `CFDSurfaceMeshControls.CurvatureNormalAngle` |

`OriginalZones` 保存的列表为：`outlet`、`wall`、`inlet`、`missile_nose`、`missile_fin`、`missile_nose_tip`、`missile_body`。

该列表是历史会话记录，当前脚本不显式设置 `OriginalZones`，新会话的实际区域以本次导入和后续检查为准。

底层 `size-controls` 中另有 `import_curvature_0` 和 `import_proximity_0`，两者均保存数值序列 `15, 300, 1.2, 18, 2, 12`，完整原文见附录。其中的 `18` 作为底层记录保留，不补写成该任务中显式保存的字段。当前参数块也未显式保存完整的尺寸函数模式选项。

这里的 `ExecuteShareTopology = No` 属于面网格步骤；后面的“描述几何结构”保存了 `InvokeShareTopology = Yes`，并单独执行共享拓扑任务。

## 4. 描述几何结构（Describe Geometry）

任务：`TaskObject4`；命令：`GeometrySetup`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 是否需要封盖 | No | `CappingRequired` |
| 是否调用共享拓扑 | Yes | `InvokeShareTopology` |
| 非共形选项 | No | `NonConformal` |

活动子任务为 `TaskObject5`（使用共享拓扑）和 `TaskObject8`（创建区域）。

`TaskObject6`（Enclose Fluid Regions / Capping）与 `TaskObject7`（Update Boundaries）在 `InactiveTaskList` 内，两者参数为空、状态为 `Out-of-date`，不属于截图中的活动执行步骤。几何类别问题的完整界面选择未在此任务的 `Arguments` 中显式保存。

### 4.1 使用共享拓扑（Apply Share Topology）

任务：`TaskObject5`；命令：`ShareTopology`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 间隙距离 | 3 mm | `GapDistance` |
| 接口选择方式 | Manual（手动） | `InterfaceSelect` |

未显式保存具体手选接口列表。任务保存的警告原文为：

```text
The model only have one object. Share Topology might not be needed
```

含义：模型只有一个对象，共享拓扑可能不是必需的。日志第 409–410 行也记录了该提示和 `0 pair(s) found.`；随后仍记录了共享拓扑处理完成。

### 4.2 创建区域（Create Regions）

任务：`TaskObject8`；命令：`CreateRegions`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 流动体积数量设置 | 1 | `NumberOfFlowVolumes` |

任务保存的警告原文为：

```text
One or more fluid regions could not be detected. Verify the names and types of your regions using the Update Region task.
```

含义：未能检测到一个或多个流体区域，软件提示在更新区域步骤核对区域名称及类型。日志第 459–463 行记录了区域识别未成功，随后执行区域更新。该提示属于当时的工作流记录。

## 5. 更新区域类型设定（Update Regions）

任务：`TaskObject9`；命令：`UpdateRegions`。

| 区域 | 原类型 | 更新后类型 |
| --- | --- | --- |
| `zone----enclosure----` | solid（固体） | fluid（流体） |

原字段对应关系：

- `OldRegionNameList = ["zone----enclosure----"]`
- `OldRegionTypeList = ["solid"]`
- `RegionNameList = ["zone----enclosure----"]`
- `RegionTypeList = ["fluid"]`

区域名称未改变，保存记录中的类型由 solid 改为 fluid。最终文件的 `/settings/Thread Variables` 也将体区域 `zone----enclosure----` 记录为 `fluid`。

当前脚本按任务报告的区域列表更新类型；只有未报告名称时才尝试上述历史参考名。
新网格的唯一流体区域必须与区域更新结果一致，历史区域名及下表的区域 ID、面数不作为新模型的固定验收值。

### 最终保存的面区域类型

以下是最终网格区域记录，不是截图中另一个已执行的“更新边界”任务。

| 面区域名称 | ID | 保存类型 | 面数 |
| --- | ---: | --- | ---: |
| `inlet` | 37 | wall | 346 |
| `outlet` | 35 | wall | 346 |
| `wall` | 36 | wall | 5,276 |
| `missile-zone----enclosure----_43` | 38 | wall | 35,204 |
| `interior--zone----enclosure----` | 1963 | interior | 15,478,761 |

名称为 `inlet`、`outlet` 的两个面在此文件中仍保存为 `wall`，名称本身不能证明已经设置为求解器中的入口或出口边界。面数由 `/meshes/1/faces/zoneTopology` 的各区域 ID 范围计算得到。

## 6. 添加边界层（Add Boundary Layers）

父任务：`TaskObject10`，其 `Arguments` 为空；唯一子任务为 `TaskObject17`，名称 `last-ratio_1`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 控制名称 | `last-ratio_1` | `BLControlName` |
| 偏移方法 | `last-ratio` | `OffsetMethodType` |
| 首层高度 | 0.07 mm | `FirstHeight` |
| 层数 | 12 | `NumberOfLayers` |
| 连续性选项 | Stair Step（阶梯式） | `LocalPrismPreferences.Continuous` |
| 区域类型范围 | fluid-regions | `FaceScope.RegionsType` |
| 边界层区域列表 | `zone----enclosure----` | `BLRegionList` |
| 区域范围字段 | `missile` | `RegionScope` |
| 面区域列表 | `missile-zone----enclosure----_43`、`inlet`、`wall`、`outlet` | `BLZoneList` |

`CompleteBLRegionList`、`CompleteBLZoneList`、`CompleteRegionScope` 分别与对应列表相同。`RegionScope` 与 `BLRegionList` 是两个不同字段，本文保留两者，不将它们合并成一个区域名。

上述对应关系来自历史保存状态。当前脚本保留 `RegionScope=["missile"]`，用区域更新结果设置 `BLRegionList`；
若 Fluent 提供完整面区域列表，则按精确匹配优先、唯一子串匹配其次的规则核对 `BLZoneList`，多义或缺失报错。
若未提供该列表，则保留配置并检查最终导出边界组，不能据此认定已逐面验证边界层作用范围。

底层相关记录为：

```scheme
(prism-controls (("last-ratio_1" "last-ratio" 0.07000000000000001 12 27.2 1.2 "zone----enclosure----" "fluid-regions" "*" "only-walls" "*" #t)))
(prism/last-aspect-ratio 27.2)
(auto/create-prisms? #t)
(auto/grow-prisms "scoped")
```

工作流 `Arguments` 未显式保存增长率或最后一层比例的命名字段。底层具名变量 `prism/last-aspect-ratio` 为 `27.2`，控制元组中还保存了 `1.2` 和 `only-walls`。由于元组位置本身没有字段名，这里保留原记录，不将 `1.2` 当成已核实的界面增长率字段。

当前脚本也未显式设置 `prism/last-aspect-ratio=27.2` 或复写上述底层元组，不能仅凭历史记录断言新会话使用了这些底层值。

上面的 12 层是设置值，不是对最终所有位置实际层数的测量；面区域列表也不等同于对各处实际生成边界层的逐面验证。

## 7. 生成体网格（Generate the Volume Mesh）

任务：`TaskObject11`；命令：`GenerateTheVolumeMeshWTM`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 填充方法 | polyhedra（多面体） | `VolumeFill` |
| 尺寸设置方式 | Region-based（按区域） | `SizingMethod` |
| 是否划分固体区域网格 | false | `MeshSolidRegions` |
| 显示固体/流体网格字段 | true | `ShowSolidFluidMeshed` |
| 全局控制块中保存的增长率 | 1.2 | `VolumeFillControls.GrowthRate` |
| 全局控制块中保存的最大单元长度 | 80 mm | `VolumeFillControls.TetPolyMaxCellLength` |

### 7.1 按区域保存的尺寸

以下按 `RegionTetNameList`、`RegionTetMaxCellLengthList`、`RegionTetGrowthRateList` 的同一索引配对：

| 历史区域名称 | 保存的最大单元长度（mm） | 保存的增长率 | Old 字段保存的最大单元长度（mm） | Old 字段保存的增长率 |
| --- | ---: | ---: | ---: | ---: |
| `missile` | 25 | 1.2 | 15.68 | 1.2 |
| `zone----enclosure----` | 500 | 1.2 | 393.28 | 1.2 |

历史保存的方法和当前脚本均为 `Region-based`，因此不能只摘录全局控制块中的 `80 mm` 来代表全部体网格设置。
`OldRegionTetMaxCellLengthList`、`OldRegionTetGrowthRateList` 是历史文件中单独保存的旧值字段，当前脚本不将它们作为尺寸输入。

`missile` 出现在区域尺寸列表中，但 `MeshSolidRegions = false`，最终文件仅有一个 fluid 体单元区域 `zone----enclosure----`；不能据尺寸表中的两行认定生成了两个体单元区域。

### 7.2 历史最终网格规模

来源：`/meshes/1` 属性和区域拓扑。

| 项目 | 保存结果 |
| --- | ---: |
| 体单元数 | 2,518,996 |
| 面数 | 15,519,933 |
| 节点数 | 11,896,643 |
| 体单元区域数 | 1 |
| 面区域数 | 5（含 interior） |
| 体区域 ID | 1964 |
| 体区域名称 | `zone----enclosure----` |
| 体区域类型 | fluid |

### 7.3 历史运行日志中的质量记录

历史日志包含多次生成过程。下表引用其中最后一次生成并写出源文件的记录：日志第 508–524 行的体区域 ID `1964`、单元数 `2,518,996` 与历史最终文件相同；第 749–758 行明确写出了该历史源文件。

| 项目 | 日志记录 |
| --- | --- |
| 最小正交质量 | 0.057117063 |
| 最大正交质量 | 0.99999999 |
| 平均正交质量 | 0.9367215 |
| 正交质量低于 0.05 的单元数 | 0 |
| 体网格生成耗时 | 4.63 分钟 |

最小、最大、平均值来自历史日志第 724–725 行；生成摘要中的 `0.06` 是四舍五入后的显示值。以上为已有日志结果，未作为本次文档更新的新质量验收。

日志第 482 行还保存了 `region dead0 cannot be deleted as it is completely enclosed by other regions` 的提示，含义是 `dead0` 区域因被其他区域完全包围而无法删除。最终网格中仍只记录一个体单元区域。体网格任务自身的 `Warnings` 列表为空，与日志中存在运行过程提示是不同记录层级。

## 附录 A：历史底层局部尺寸记录（控制名已统一）

来源：`/settings/TGrid Variables` → `size-controls`。以下元组除文首说明的控制名称调整外，其余内容及数值顺序保持不变，不将无字段名的位置值自动替换为界面字段。

当前脚本通过工作流参数设置尺寸，不直接复写这些底层元组；尤其不能据此认定新会话的 `import_curvature_0`、`import_proximity_0` 与历史记录逐项一致。

```scheme
(size-controls (
  ("boi_1-0" "boi" 1.5 30. 1.2 16. 1. 12 "Face Zone" "origin-missile-boi" #t #t)
  ("missile_body_faces" "curvature" 3. 30. 1.18 18. 1. 12 "Face Zone Labels" "zone----enclosure---- :: missile_body" #f #t)
  ("missile_nose_faces" "curvature" 2. 18. 1.18 16. 1. 12 "Face Zone Labels" "zone----enclosure---- :: missile_nose" #f #t)
  ("missile_nose_tip_faces" "curvature" 1.6 10. 1.15 18. 1. 12 "Face Zone Labels" "zone----enclosure---- :: missile_nose_tip" #f #t)
  ("missile_fin_faces" "curvature" 3. 40. 1.2 18. 1. 12 "Face Zone Labels" "zone----enclosure---- :: missile_fin" #f #t)
  ("import_curvature_0" "curvature" 15. 300. 1.2 18. 2. 12 "Object Faces and Edges" "*" #f #t)
  ("import_proximity_0" "proximity" 15. 300. 1.2 18. 2. 12 "Object Faces" "*" #f #t)
))
```

BOI 的工作流另存 `BOIMinSize = 15`，底层 BOI 元组中的首个数值为 `1.5`；两者原样保留，不视为同一个已确认字段。四项曲率控制的底层前两个数值与其工作流 Min/Max 记录一致。

## 附录 B：读取边界

- 第 1–7 节覆盖历史截图中的全部活动步骤及其保存参数，同时保留了明确的旧值、底层记录和任务警告；底层记录不等同于当前脚本显式传参。
- 历史参数来源为现归档在 `data_lib/legacy/fluent_meshing/reference/` 的 `missile.msh.h5` 及辅助文件；本次新增的实现说明来源于当前脚本，未用其他网格替换历史证据。
- 缺失的界面选项、未保存的手工选择和完整点击历史无法仅凭这些参数逐项还原；本文不将缺失项补成默认值。
- 本文未计算边界层总厚度、实际逐处层数，也未重新测量网格质量或执行求解器设置。
- 新生成模型的标签、区域、网格数量、质量和任务状态以对应新运行证据为准；历史 `Up-to-date`、空错误列表和质量数值不能替代完整运行的 `PASSED` 核验。
