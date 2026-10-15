# 当前工作区入口与数据位置

本项目已迁移为工作区的 fluent_meshing 项目根。以下入口和数据约定用于当前版本，后续原文中的历史案例设置保留作参考。

- 从任意目录调用根 `scripts/run.ps1 verify-meshing -Geometry <新生成的 .scdoc>`；入口必须显式给出几何，没有默认模型。
- run_meshing.cmd 保留原命令行选项，另有 `--geometry`、`--run-dir`、`--out`、`--start-timeout`、`--timeout`、`--stop-after`。
- 工作流只有原来的专用 7 步 recorded 流程：局部尺寸（4 个曲率控制 + BOI）→ 面网格 → 描述几何 → 共享拓扑 → 创建区域 → 更新区域 → 边界层 → 体网格。
- 导入后核对必需标签（`missile_body` / `missile_nose` / `missile_nose_tip` / `missile_fin` / `missile-boi`）；区域与边界层区域必须唯一匹配，缺失或多义都直接报错，不再回退到猜测的名字。
- 新运行记录与合格产物归 data_lib/runs；失败日志、参数和实际失败产物归 feedback/fluent_meshing。
- `--out` 指定文件须不存在；`--run-dir` 须使用新目录。主动 `--stop-after` 记录 STOPPED，不作为完整验收通过。
- PyFluent 环境 `_deps` 与网格文件核验环境互相隔离：真实工作流只把 `_deps` 放入 PYTHONPATH；导出的 `.msh.h5` 由 `mesh_check.cmd`（解释器记录在 `.mesh-inspect-python`，依赖在 `.mesh_inspect_deps_313`）独立核验，supervisor 在同一记录里写入 `exportedMesh`。
- 历史参考及生成结果分别归 data_lib/legacy/fluent_meshing/reference 和 generated；比较工具默认使用这两处历史 workflow。
- 真实验收对象是新生成的 `.scdoc`：标签、区域、体网格区域、边界组都由本次导入的模型决定。

---
# missile.scdoc 自动网格划分脚本

本目录下的脚本用 **ANSYS Fluent Meshing 2024 R1 + PyFluent** 自动完成
`missile.scdoc` 的整套 Watertight Geometry 工作流，并把生成的网格导出到本目录。
所有参数取自本目录的 `fluent_meshing.md`（该文档是从原 `missile.msh.h5` 中读出的配置）。

## 1. 交付内容

| 文件 | 说明 |
| --- | --- |
| `mesh_missile.py` | 主脚本：启动 Fluent Meshing，按 7 个步骤自动布网格，最后写出 mesh |
| `run_meshing.cmd` | Windows 启动器：自动找到 Python 3.10/3.11 并设置 `PYTHONPATH=.\_deps` |
| `missile_mesh.msh.h5` | **生成的网格文件（导出到本目录）**，516,167,026 字节 |
| `missile_mesh_workflow_files\` | Fluent 随网格一起写出的工作流文件（`missile_mesh.wft`、中间网格等） |
| `geometry\missile.scdoc` | 输入几何（与用户提供的附件 SHA256 完全一致） |
| `logs\` | 最近一次运行的日志：`mesh_*.log`（脚本日志）、`mesh_*.trn`（Fluent 原始 transcript）、`missile_mesh.json`（网格内嵌工作流参数） |
| `runs\` | Fluent 运行目录（临时文件、失败时保存的 `failed_workflow.wft`） |
| `tools\compare_with_reference.py` | 校验工具：把生成的 `.wft` 与原始 `missile.wft` 逐参数对比 |
| `_deps\` | 本地安装的 PyFluent（ansys-fluent-core 0.19.2），脚本运行所需 |

## 2. 运行环境

* 已安装 ANSYS Fluent 2024 R1，环境变量 `AWP_ROOT241` 指向安装目录；
  许可证服务器可用（`ANSYSLMD_LICENSE_FILE=1055@localhost`）。
* Python 3.10 或 3.11（PyFluent 0.19.2 不支持 3.13）。
  本机使用 `D:\ProgarmFile\Python310\python.exe`，PyFluent 安装在 `.\_deps`。
* 无需额外安装 ANSYS 官方 Python 包，`run_meshing.cmd` 会设置好 `PYTHONPATH`。
* 网格文件核验使用**另一套**环境：`.mesh_inspect_deps_313`（h5py 3.16.0，配本机 CPython 3.13）+
  解释器记录文件 `.mesh-inspect-python`（被 Git 忽略）。原 `.mesh_inspect_deps` 是 CPython 3.12 的构建，
  与本机 3.13 解释器 ABI 不符，保留但不再使用；两套依赖不混用。重建方式：

```bat
python -m pip install --target .mesh_inspect_deps_313 "h5py==3.16.0" --no-deps --only-binary=:all:
```

## 3. 使用方法

```bat
:: 必需：给出新生成的几何（没有默认模型）
run_meshing.cmd --geometry D:\runs\run-xxxx\spaceclaim\missile.scdoc

:: 指定进程数 / 输出路径 / 保留 Fluent 进程
run_meshing.cmd --geometry ..\data_lib\runs\run-xxxx\spaceclaim\missile.scdoc -n 8
run_meshing.cmd --geometry ..\model.scdoc --out D:\somewhere\missile_mesh.msh.h5
run_meshing.cmd --geometry ..\model.scdoc --keep-fluent
run_meshing.cmd --geometry ..\model.scdoc --show-gui       :: 带界面运行（调试用）
run_meshing.cmd --geometry ..\model.scdoc --stop-after sizing
                                      :: 只跑到某一步（import|sizing|surface|describe|
                                      :: share|regions|update-regions|bl|volume）
```

也可以直接调用：

```bat
set PYTHONPATH=%CD%\_deps
D:\ProgarmFile\Python310\python.exe mesh_missile.py --geometry ..\model.scdoc -n 16
```

导出的网格由独立的核验环境检查（两套依赖不混用）：

```bat
mesh_check.cmd ..\data_lib\runs\run-xxxx\fluent_meshing\missile_mesh.msh.h5
```

本机 16 进程实测：**整流程约 7.5 分钟**（体网格 5.4 分钟）。

## 4. 脚本执行的步骤与参数（与 `fluent_meshing.md` 一致）

| 步骤 | 任务 | 关键参数 |
| --- | --- | --- |
| 1 导入几何 | Import Geometry | 单位 mm，`MaxFacetLength=0`，导入 3 个 parts/objects |
| 2 添加局部尺寸 | Add Local Sizing | `missile_body` 曲率 3–30 mm，增长率 1.18，法向角 18°，`BOISize=18`<br>`missile_nose` 曲率 2–18 mm，1.18，16°，`BOISize=9`<br>`missile_nose_tip` 曲率 1.6–10 mm，1.15，18°，`BOISize=6`<br>`missile_fin` 曲率 3–40 mm，1.2，18°<br>`boi_1` Body Of Influence 目标尺寸 30 mm，增长率 1.2，标签 `missile-boi` |
| 3 生成面网格 | Generate the Surface Mesh | 全局最小 15 mm、最大 300 mm、增长率 1.2、每间隙单元数 2、邻近作用范围 faces，本步不做共享拓扑 |
| 4 描述几何结构 | Describe Geometry | 封盖 No、调用共享拓扑 Yes、非共形 No |
| 4.1 使用共享拓扑 | Apply Share Topology | 间隙距离 3 mm、接口选择 Manual |
| 4.2 创建区域 | Create Regions | 流动体积数量 1 |
| 5 更新区域类型 | Update Regions | `zone----enclosure----`：solid → fluid |
| 6 添加边界层 | Add Boundary Layers → `last-ratio_1` | last-ratio，首层高度 0.07 mm，12 层，连续性 Stair Step，区域类型 fluid-regions，区域 `zone----enclosure----`，区域范围 `missile`，面区域 `missile-zone----enclosure----_43`、`inlet`、`wall`、`outlet` |
| 7 生成体网格 | Generate the Volume Mesh | polyhedra，Region-based，不划分固体区域；区域最大单元长度 `missile` 25 mm、`zone----enclosure----` 500 mm，增长率 1.2，全局控制块 1.2 / 80 mm |
| 8 校验 | — | check-mesh、列出 boundary zones 与 scoped sizing、复制 Fluent transcript、导出工作流参数 |
| 9 写出网格 | — | `file/write-mesh` → `missile_mesh.msh.h5` |

## 5. 验证结果

**(a) 网格规模、质量与区域拓扑（本次生成 vs 原 `missile.msh.h5`）**

直接读取两个 `.msh.h5` 的 `/meshes/1` 属性与 `/meshes/1/*/zoneTopology` 对比，结果**完全一致**：

| 项目 | 原文件 | 本次生成 |
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

```bat
python tools\compare_with_reference.py
```

结果：17 个任务中，除下面列出的 3 处非网格参数差异外，**其余任务参数完全一致**
（局部尺寸 5 个控制的 min/max/增长率/法向角/每间隙单元数/BOISize、面网格控制、
共享拓扑、区域类型、边界层控制、体网格区域尺寸等）。
已知差异（不影响网格）：

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
  脚本启动前会探测，不可写时自动把 `APPDATA` 重定向到 `.\_appdata`。
* **数值类型**：局部尺寸的数值（最小/最大尺寸、增长率、法向角等）必须传数字而不是字符串，
  否则 Fluent 报 `tg-draw-size-boxes: wta[2](float) ["3"]`。
* **添加子任务**：`AddChildAndUpdate()` 会立即更新工作流，所以子任务的必填参数
  （如 `BOIFaceLabelList`）必须在同一次调用里传入。
* 若在受限沙箱中运行：Intel MPI 的 hydra 无法创建进程、许可端口文件无法写入时，
  Fluent 不能启动，需要在不受限的会话中运行本脚本。
