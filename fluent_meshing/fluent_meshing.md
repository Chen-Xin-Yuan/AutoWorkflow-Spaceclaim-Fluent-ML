# Fluent Meshing 工作流配置记录

读取日期：2026-09-20。本文按所附工作台截图的步骤顺序，整理 `missile.msh.h5` 中已经保存的配置，不包含新参数或优化建议。

文档命名调整：`TaskObject14` 的控制名称统一写为 `missile_nose_tip`，附录中的对应控制名同步写为 `missile_nose_tip_faces`。此调整仅用于本文，源网格文件中的名称未修改。

## 数据来源与读取说明

| 项目 | 记录 |
| --- | --- |
| 主文件 | `D:\2_my_sci_train\fluent\mesh\missile.msh.h5` |
| 文件大小 | 516,167,042 字节 |
| 文件最后修改时间 | 2026-09-20 11:27:39（本机时间） |
| 写出软件 | ANSYS FLUENT MESHING 2024 R1 |
| 版本字段 | `24.1` |
| 工作流 | `Watertight Geometry` |
| 维度 | 三维 |
| 长度 / 面积 / 体积单位 | `mm` / `mm^2` / `mm^3` |
| 参数主来源 | HDF5 的 `/settings/TGrid Variables` → `workflow/state` → 各任务的 `Arguments` |
| 辅助工作流文件 | `D:\2_my_sci_train\fluent\mesh\missile_workflow_files\missile.wft` |
| 辅助运行日志 | `D:\2_my_sci_train\fluent\mesh\fluent-20260920-105954-19336.trn` |

工作台截图用于核对步骤名称和顺序，具体数值来自文件。本文以最终网格内嵌的工作流状态为准；辅助 `.wft` 中任务状态为 `Out-of-date`，不能据此覆盖最终网格内的 `Up-to-date` 状态。尺寸数值均按保存单位 mm 展示，角度按度展示，增长率无量纲。浮点存储值 `0.07000000000000001` 在正文简写为 `0.07`。

文件中未显式保存的界面项标为“未显式保存”，不补成默认值。任务中保存的字段可能包含界面保留值；保存了某个值，不等于该值在所选方法下实际参与计算。本次为只读提取，未重新执行工作流或修改源文件。

## 工作流顺序

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

以上活动任务在最终 `.msh.h5` 中均为 `Up-to-date`，其 `Errors` 列表均为空。共享拓扑和创建区域任务各有一条保存的警告，见对应章节。

## 1. 导入几何模型（Import Geometry）

任务：`TaskObject1`；命令：`ImportGeometry`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 原始几何文件 | `D:/2_my_sci_train/scdoc_file/missile.scdoc` | `FileName` |
| 长度单位 | mm | `LengthUnit` |
| 部件数量字段 | 3 | `NumParts` |
| 最大离散面片长度字段 | 0 | `ImportCadPreferences.MaxFacetLength` |

`MaxFacetLength = 0` 是文件中的原始设置值，不表示实际生成了零长度网格。

底层 `cad/import-options`、`cad/adv-options` 还保存了以下导入记录，与上述工作流字段分开列示：

| 配置项 | 保存值 |
| --- | --- |
| 中间几何文件 | `D:/2_my_sci_train/fluent/mesh/missile_workflow_files\missile.scdoc.pmdb` |
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

任务：`TaskObject14`。文档中的控制名称为 `missile_nose_tip`，实际选择的标签为 `missile_nose_tip`。

| 配置项 | 保存值 | 原字段 |
| --- | --- | --- |
| 控制名称 | `missile_nose_tip` | `BOIControlName` |
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

底层相关记录为：

```scheme
(prism-controls (("last-ratio_1" "last-ratio" 0.07000000000000001 12 27.2 1.2 "zone----enclosure----" "fluid-regions" "*" "only-walls" "*" #t)))
(prism/last-aspect-ratio 27.2)
(auto/create-prisms? #t)
(auto/grow-prisms "scoped")
```

工作流 `Arguments` 未显式保存增长率或最后一层比例的命名字段。底层具名变量 `prism/last-aspect-ratio` 为 `27.2`，控制元组中还保存了 `1.2` 和 `only-walls`。由于元组位置本身没有字段名，这里保留原记录，不将 `1.2` 当成已核实的界面增长率字段。

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

| 区域名称 | 当前最大单元长度（mm） | 当前增长率 | Old 字段保存的最大单元长度（mm） | Old 字段保存的增长率 |
| --- | ---: | ---: | ---: | ---: |
| `missile` | 25 | 1.2 | 15.68 | 1.2 |
| `zone----enclosure----` | 500 | 1.2 | 393.28 | 1.2 |

当前方法明确为 `Region-based`，因此不能只摘录全局控制块中的 `80 mm` 来代表全部体网格设置。`OldRegionTetMaxCellLengthList`、`OldRegionTetGrowthRateList` 是文件中单独保存的旧值字段。

`missile` 出现在区域尺寸列表中，但 `MeshSolidRegions = false`，最终文件仅有一个 fluid 体单元区域 `zone----enclosure----`；不能据尺寸表中的两行认定生成了两个体单元区域。

### 7.2 最终网格规模

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

### 7.3 对应运行日志中的质量记录

日志包含多次生成过程。下表引用最后一次生成并写出本文件的记录：日志第 508–524 行的体区域 ID `1964`、单元数 `2,518,996` 与最终文件相同；第 749–758 行明确写出了本次源文件。

| 项目 | 日志记录 |
| --- | --- |
| 最小正交质量 | 0.057117063 |
| 最大正交质量 | 0.99999999 |
| 平均正交质量 | 0.9367215 |
| 正交质量低于 0.05 的单元数 | 0 |
| 体网格生成耗时 | 4.63 分钟 |

最小、最大、平均值来自日志第 724–725 行；生成摘要中的 `0.06` 是四舍五入后的显示值。以上为已有日志结果，本次没有重新计算质量。

日志第 482 行还保存了 `region dead0 cannot be deleted as it is completely enclosed by other regions` 的提示，含义是 `dead0` 区域因被其他区域完全包围而无法删除。最终网格中仍只记录一个体单元区域。体网格任务自身的 `Warnings` 列表为空，与日志中存在运行过程提示是不同记录层级。

## 附录 A：底层局部尺寸记录原文

来源：`/settings/TGrid Variables` → `size-controls`。以下元组除文首说明的控制名称调整外，其余内容及数值顺序保持不变，不将无字段名的位置值自动替换为界面字段。

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

- 本文覆盖截图中的全部活动步骤及其保存参数，同时保留了明确的旧值、底层记录和任务警告。
- 参数来源是本次提供的 `missile.msh.h5` 及其同目录辅助文件；当前文件夹已有的其他网格说明未作为本次参数来源。
- 缺失的界面选项、未保存的手工选择和完整点击历史无法仅凭这些参数逐项还原；本文不将缺失项补成默认值。
- 本文未计算边界层总厚度、实际逐处层数，也未重新测量网格质量或执行求解器设置。
