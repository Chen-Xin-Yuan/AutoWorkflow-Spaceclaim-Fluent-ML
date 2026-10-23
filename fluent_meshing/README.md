# Fluent Meshing 自动网格划分

本仓库使用 **ANSYS Fluent Meshing 2024 R1 + PyFluent**，对显式提供的 `.scdoc`
执行 Watertight Geometry 工作流。当前只支持从原 `missile.msh.h5` 记录中复现的专用
`recorded` 流程；参数来源见 [fluent_meshing.md](fluent_meshing.md)。输入模型必须提供下文要求的标签和区域。

运行模型、网格和日志保存在仓库外的工作区数据目录。以下说明对应当前脚本；末尾的数值和对比结果是历史记录。

各自有源码和启动器的文件前部均说明输入、输出、路径基准及覆盖来源，并集中原有默认文件名、路径模板和可调参数；修改位置如下：

| 文件前部的配置区 | 修改内容 |
| --- | --- |
| `mesh_missile.py` | 默认导出网格名 `MESH_NAME`、辅助日志/工作流路径模板，以及原有 `P_*` 工作流参数表；旧 `GEOMETRY_FILE` 等仅供直接调用函数兼容，正式 CLI 仍要求 `--geometry` |
| `mesh_runner.py` | 进程数、启动/运行超时、显示默认值，以及本次运行的日志、请求记录、完成/停止/失败工作流文件名 |
| `mesh_validation.py` | 校验报告名、既有校验阈值及等待/文件时间容差参数；网格文件与运行目录仍由调用方传入 |
| `workspace_runtime.py` | `workspace.json` 文件名、缺少配置时的回退路径、运行记录和独立核验报告名、解释器本地配置名、独立核验超时及既有重试参数 |
| `run_meshing.cmd` / `mesh_check.cmd` | 脚本相对的入口、解释器配置和依赖路径；解释器环境变量的原有优先级保持不变 |
| `tools/compare_with_reference.py` | 历史比较输入的默认路径，位置参数可覆盖；结果仅打印到标准输出 |
| `tools/inspect_mesh_file.py` | 必填网格位置参数和 JSON 标准输出说明，无默认输入或输出文件 |
| `tools/probe_regions.py` | 必填几何输入说明、诊断进程数和显示长度参数；不主动导出网格或报告 |

显式 CLI 路径仍按调用时工作目录解析，工作区数据根目录仍由 `workspace.json` 决定，自动运行目录仍包含实时生成的 `run-id`。`run.json` 等协议文件名及 Fluent/HDF5 固定标识需要与消费者同步，不能当作可任意更改的普通参数。此次只整理配置位置与说明，保留原有默认数值、算法和运行行为，未执行 Fluent。

## 1. 当前目录与数据位置

| 文件或目录 | 用途 |
| --- | --- |
| `mesh_missile.py` | 工作流参数、任务实现和命令行入口 |
| `mesh_runner.py` | 参数解析、Fluent 启动、工作流执行和导出检查 |
| `mesh_validation.py` | Fluent 网格检查、正交质量解析和导出文件拓扑核验 |
| `workspace_runtime.py` | 读取工作区配置、管理运行记录、超时处理和独立文件核验 |
| `run_meshing.cmd` | 使用独立 `_deps` 环境启动划分流程 |
| `mesh_check.cmd` | 使用独立 h5py 环境读取导出的 `.msh.h5`，输出维度、单位、网格数量和区域信息 |
| `fluent_meshing.md` | 从历史网格提取的工作流配置参考 |
| `tools/` | `compare_with_reference.py` 比较工作流参数；`inspect_mesh_file.py` 读取网格文件；`probe_regions.py` 诊断模型标签和区域；`__pycache__/` 为生成缓存，不纳入版本控制，不手工编辑 |
| `tests/` | `test_workflows.py` 验证质量解析、名称匹配、导出核验和运行记录等逻辑；`__pycache__/` 为生成缓存，不纳入版本控制，不手工编辑 |
| `.vscode/` | 本地编辑器配置，当前包含 `settings.json` |
| `_deps/` | 本地 PyFluent 依赖（ansys-fluent-core 0.19.2）；不纳入版本控制，不手工编辑 |
| `.mesh_inspect_deps_313/` | 当前 CPython 3.13 网格读取依赖（h5py 3.16.0 及配套 NumPy）；不纳入版本控制，不手工编辑 |
| `.mesh_inspect_deps/` | 保留的旧 CPython 3.12 网格读取依赖，当前启动器不使用；不纳入版本控制，不手工编辑 |
| `__pycache__/` | Python 自动生成的缓存；不纳入版本控制，不手工编辑 |
| `.python-path` / `.mesh-inspect-python` | 本机解释器路径配置；不纳入版本控制 |

工作区根目录是本仓库的父目录，运行数据位置由其 `workspace.json` 配置。默认约定如下（均相对工作区根目录）：

| 位置 | 用途 |
| --- | --- |
| `data_lib/runs/<run-id>/fluent_meshing/` | 新运行记录、工作文件、日志和默认导出的 `missile_mesh.msh.h5` |
| `feedback/fluent_meshing/<run-id>/` | 失败运行的记录、日志、参数和实际产生的失败产物 |
| `data_lib/legacy/fluent_meshing/reference/` | 历史参考网格和工作流 |
| `data_lib/legacy/fluent_meshing/generated/` | 历史生成网格和工作流 |

每次运行使用新目录。常见记录包括 `run.json`、`request.json`、`pipeline.log`、`supervisor.log`、
`validation.trn`、`quality-native.json`、`quality.json` 和 `mesh-inspection.json`；工作流按状态保存为
`completed_workflow.wft`、`stopped_workflow.wft` 或 `failed_workflow.wft`。文件是否产生取决于实际执行阶段。

## 2. 运行环境

- ANSYS Fluent 2024 R1 和可用许可证；通过 `AWP_ROOT241` 等受支持的环境配置发现安装，不在源码或文档中固定安装路径和许可连接信息。
- 划分流程使用 Python 3.10 或 3.11，以及 `_deps/` 中的 PyFluent 0.19.2。
  `run_meshing.cmd` 优先使用 `FLUENT_MESH_PYTHON`，其次读取 `.python-path`，再查找本机 3.10/3.11 解释器；`PYTHONPATH` 由启动器按脚本所在目录设置。
- 导出文件核验使用独立的 CPython 3.13 环境和 `.mesh_inspect_deps_313/`。
  解释器通过 `MESH_INSPECT_PYTHON` 或 `.mesh-inspect-python` 配置。
- `_deps/`、`.mesh_inspect_deps/` 和 `.mesh_inspect_deps_313/` 的 Python/NumPy 环境保持隔离，保留原有版本，不合并依赖目录。

## 3. 使用方法与验收

以下示例在本仓库目录中用 PowerShell 执行，`run-xxxx` 应替换为实际的几何生成记录目录。必须显式提供新生成的 `.scdoc`，没有默认输入模型。

```powershell
# 工作区统一入口
..\scripts\run.ps1 verify-meshing -Geometry ..\data_lib\runs\run-xxxx\spaceclaim\missile.scdoc

# 也可直接使用本仓库启动器
.\run_meshing.cmd --geometry ..\data_lib\runs\run-xxxx\spaceclaim\missile.scdoc -n 8

# 调试：只执行到局部尺寸设置完成
.\run_meshing.cmd --geometry ..\data_lib\runs\run-xxxx\spaceclaim\missile.scdoc --stop-after sizing
```

| 选项 | 说明 |
| --- | --- |
| `--geometry` | 必填，输入 `.scdoc` 路径 |
| `-n` / `--processors` | Fluent 进程数，默认 16，必须为正数 |
| `--run-dir` | 指定新的运行目录，目录须不存在；应放在 `data_lib/runs/` 下 |
| `--out` | 指定新的网格文件路径，文件须不存在；应放在仓库外的运行数据目录中 |
| `--start-timeout` | Fluent 启动超时秒数，默认 300，必须为正数 |
| `--timeout` | 工作进程运行时限秒数，默认 900，必须为正数；随后独立文件核验另有 600 秒时限 |
| `--keep-fluent` | 运行结束后保留 Fluent 进程 |
| `--show-gui` | 显示 Fluent 界面 |
| `--draw-size-control true\|false` | 是否绘制局部尺寸控制，默认 `true` |
| `--stop-after` | 在 `import`、`sizing`、`surface`、`describe`、`share`、`regions`、`update-regions`、`bl` 或 `volume` 后主动停止 |

导入后核对必需标签：`missile_body`、`missile_nose`、`missile_nose_tip`、`missile_fin`、`missile-boi`。
区域与边界层作用范围必须能够唯一匹配；缺失或多义会报错。体网格检查还会确认实际流体区域与工作流设置一致。

完整运行先在 Fluent 内执行网格检查和正交质量检查，再由 supervisor 使用独立环境读取导出的 `.msh.h5`。
当前检查要求最小单元体积为正、最小正交质量大于 0.01；导出文件须为本次生成的非空三维毫米网格，
包含 `inlet`、`outlet`、`wall`、非空的 missile 壁面和唯一的非空流体单元区域，并与质量报告中的单元数交叉核对。
核验结果写入 `run.json` 的 `verification`，独立读取结果写入 `verification.exportedMesh`。

- `PASSED`：完整流程及独立导出文件核验通过。
- `STOPPED`：通过 `--stop-after` 主动停止，`runtimeVerified=false`，不作为完整验收通过；命令退出码仍可为 0。
- `FAILED`：执行或核验失败，记录和已有产物保留在 `feedback/fluent_meshing/`。

也可单独查看导出文件的结构信息；这条命令不代替完整流程中的质量检查和边界组交叉核验：

```powershell
.\mesh_check.cmd ..\data_lib\runs\run-xxxx\fluent_meshing\missile_mesh.msh.h5
```

## 4. 脚本执行的步骤与参数（与 `fluent_meshing.md` 一致）

| 步骤 | 任务 | 关键参数 |
| --- | --- | --- |
| 1 导入几何 | Import Geometry | 单位 mm，`MaxFacetLength=0`；导入后的标签必须通过检查，parts/objects 数量由输入模型决定 |
| 2 添加局部尺寸 | Add Local Sizing | `missile_body` 曲率 3–30 mm，增长率 1.18，法向角 18°，`BOISize=18`<br>`missile_nose` 曲率 2–18 mm，1.18，16°，`BOISize=9`<br>`missile_nose_tip` 曲率 1.6–10 mm，1.15，18°，`BOISize=6`<br>`missile_fin` 曲率 3–40 mm，1.2，18°<br>`boi_1` Body Of Influence 目标尺寸 30 mm，增长率 1.2，标签 `missile-boi` |
| 3 生成面网格 | Generate the Surface Mesh | 全局最小 15 mm、最大 300 mm、增长率 1.2、每间隙单元数 2、邻近作用范围 faces，本步不做共享拓扑 |
| 4 描述几何结构 | Describe Geometry | 封盖 No、调用共享拓扑 Yes、非共形 No |
| 4.1 使用共享拓扑 | Apply Share Topology | 间隙距离 3 mm、接口选择 Manual |
| 4.2 创建区域 | Create Regions | 流动体积数量 1 |
| 5 更新区域类型 | Update Regions | 将实际检测并匹配的区域设为 fluid；历史参考名为 `zone----enclosure----` |
| 6 添加边界层 | Add Boundary Layers → `last-ratio_1` | last-ratio，首层高度 0.07 mm，12 层，连续性 Stair Step，区域类型 fluid-regions；按实际模型匹配区域和面区域，历史配置为 `zone----enclosure----`、`missile` 及 `missile-zone----enclosure----_43`、`inlet`、`wall`、`outlet` |
| 7 生成体网格 | Generate the Volume Mesh | polyhedra，Region-based，不划分固体区域；匹配 `missile` 与 enclosure 区域，最大单元长度分别为 25 / 500 mm，增长率 1.2，全局控制块 1.2 / 80 mm |
| 8 会话内校验 | — | 执行 check-mesh，读取原生正交质量、单元区域与边界组，保存检查报告 |
| 9 写出与独立核验 | — | 写出 `missile_mesh.msh.h5`，由独立环境读取文件并交叉核验；默认输出到本次运行目录 |

上述编号沿用历史参数文档，其中描述几何包含 4.1、4.2 子步骤；当前实际执行顺序为：
导入与标签检查 → 局部尺寸 → 面网格 → 描述几何 → 共享拓扑 → 创建区域 → 更新区域 → 边界层 → 体网格 → 校验与导出。

## 5. 历史验证记录（仅供参考）

以下数据保留自迁移前的记录，本次 README 更新未重新运行 Fluent，也未重新验证这些产物。
历史参考及生成文件位于 `data_lib/legacy/fluent_meshing/`；这些数值不代表新模型的验收结果。
历史记录中，16 进程整流程约 7.5 分钟（体网格约 5.4 分钟），实际耗时随模型和环境变化。

**(a) 网格规模、质量与区域拓扑（历史生成 vs 原 `missile.msh.h5`）**

历史记录中的 `/meshes/1` 属性、区域拓扑和质量对比如下（网格统计与质量一致，文件大小略有差异）：

| 项目 | 原文件 | 历史生成 |
| --- | ---: | ---: |
| 体单元数 cellCount | 2,518,996 | 2,518,996 |
| 面数 faceCount | 15,519,933 | 15,519,933 |
| 节点数 nodeCount | 11,896,643 | 11,896,643 |
| 边数 edgeCount | 1,473 | 1,473 |
| 单位 / 维度 | mm / 3 | mm / 3 |
| 最小正交质量 | 0.057117063 | 0.057117063 |
| 正交质量 < 0.05 的单元数 | 0 | 0 |
| 节点区域 / 面区域 / 体区域数 | 4 / 5 / 1 | 4 / 5 / 1 |
| 文件大小 | 516,167,042 字节 | 516,167,026 字节 |

面区域逐个对比（名称、ID、类型、面数全部相同）：

| 面区域 | ID | 面数 |
| --- | ---: | ---: |
| `inlet` | 37 | 346 |
| `outlet` | 35 | 346 |
| `wall` | 36 | 5,276 |
| `missile-zone----enclosure----_43` | 38 | 35,204 |
| `interior--zone----enclosure----` | 1963 | 15,478,761 |

体区域 `zone----enclosure----`（ID 1964，fluid）单元数 2,518,996，与原文件一致。

面网格阶段同样复现了原记录里的提示：`1 disconnected regions have been detected`、
面网格最大偏斜度 0.42–0.48；共享拓扑步骤复现了原文保存的警告
`The model only have one object. Share Topology might not be needed`；
创建区域步骤复现了 `One or more fluid regions could not be detected...`，
随后在更新区域中把 `zone----enclosure----` 由 solid 改为 fluid。

**(b) 逐参数对比**

```powershell
python tools\compare_with_reference.py
```

不传参数时，比较工具读取 `data_lib/legacy/fluent_meshing/generated/` 和 `reference/` 中的历史工作流。
比较新的运行须显式指定文件，例如：

```powershell
python tools\compare_with_reference.py ..\data_lib\runs\run-xxxx\fluent_meshing\completed_workflow.wft
```

第二个位置参数可指定参考 `.wft`；省略时仍使用历史参考。当前工具只忽略明确列出的任务记录字段，
并统一 `curvature_1` 与 `missile_nose_tip` 的别名及数值表示；真实参数差异仍会报告并返回非零退出码。

原始历史记录：17 个任务中，除下面列出的 3 处记录或显示项差异外，**其余任务参数完全一致**
（局部尺寸 5 个控制的 min/max/增长率/法向角/每间隙单元数/BOISize、面网格控制、
共享拓扑、区域类型、边界层控制、体网格区域尺寸等）。
历史记录列出的差异如下；别名和 `AddChild` 等记录项已由当前比较工具处理，`DrawSizeControl` 仍参与比较：

1. `DrawSizeControl`：原文件为 `true`，脚本默认也是 `true`（headless 亦可用）；
   若加 `--draw-size-control false` 则关闭该显示项。
2. `missile_nose_tip` 的控制名：原 `.wft` 里是 `curvature_1`，`fluent_meshing.md`
   统一写作 `missile_nose_tip`，脚本按文档使用后者（仅名字不同）。
3. `Add Boundary Layers` 及其子任务多一个 `AddChild="yes"` 记录项，
   这是 PyFluent 创建子任务的记账字段，不参与网格计算。

## 6. 环境相关的注意点（脚本已处理）

* **PyFluent watchdog**：watchdog 用 `CREATE_BREAKAWAY_FROM_JOB` 启动子进程，
  在 Windows 作业对象（job object）中会失败，因此脚本固定 `start_watchdog=False`。
* **ANSYS 许可**：licensing 客户端要把端口文件写到 `%APPDATA%\Ansys`；
  该目录不可写时会报 `Cannot initialize ANSYS Licensing context`。
  脚本启动前会探测，不可写时自动把 `APPDATA` 重定向到本次运行目录下的 `_appdata/`。
* **数值类型**：局部尺寸的数值（最小/最大尺寸、增长率、法向角等）必须传数字而不是字符串，
  否则 Fluent 报 `tg-draw-size-boxes: wta[2](float) ["3"]`。
* **添加子任务**：`AddChildAndUpdate()` 会立即更新工作流，所以子任务的必填参数
  （如 `BOIFaceLabelList`）必须在同一次调用里传入。
* 若在受限沙箱中运行：Intel MPI 的 hydra 无法创建进程、许可端口文件无法写入时，
  Fluent 不能启动，需要在不受限的会话中运行本脚本。
