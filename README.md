# 自动化工作区

几何参数与计算工况按以下约定设置。

## 参数化方式与来流条件

### 几何参数定义与范围

本文使用下列 11 个主尺寸描述几何，长度单位统一为米（m）。参数名与建模输入 JSON 的字段一致。

| 设计变量 | 定义 | 下限 / m | 上限 / m | 基准 / m |
|---|---|---|---|---|
| `L_N` | 鼻锥长度 | — | — | 0.49 |
| `L` | 轴向长度 | — | — | 3.69 |
| `D` | 机身直径 | — | — | 0.18 |
| `L_W` | 前十字舵轴向位置 | 1.2 | 1.8 | 1.72 |
| `C_RW` | 前十字舵根弦长 | 0.1 | 0.4 | 0.23 |
| `C_TW` | 前十字舵梢弦长 | 0 | 0.09 | 0.06 |
| `B_W` | 前十字舵展长 | 0.1 | 0.3 | 0.23 |
| `L_F` | 后十字舵轴向位置 | 3.0 | 3.2 | 3.2 |
| `C_RF` | 后十字舵根弦长 | 0.1 | 0.4 | 0.38 |
| `C_TF` | 后十字舵梢弦长 | 0 | 0.25 | 0.19 |
| `B_F` | 后十字舵展长 | 0.1 | 0.3 | 0.22 |

`—` 表示原图未给出上下限；图中对 `L_N`、`L`、`D` 仅列出基准值，对其余 8 项给出设计范围。`L_W`、`L_F` 在当前建模程序中均指从弹尖到弹体表面处舵面可见前缘的轴向距离。

表中保留原图的设计范围。当前建模程序会将小于 0.001 m 的非负梢弦长自动修正为 0.001 m，具体建模约定见 [SpaceClaim 参数化建模说明](spaceclaim/README.md#参数化建模)。

### 来流参数与工况组合

来流条件由马赫数 `Ma` 和攻角 `α` 两个参数确定，采用以下离散取值的全部组合：

- 马赫数：`Ma = {2.00, 2.04, 2.08, 2.12, 2.16, 2.20}`，无量纲。
- 攻角：`α = {4.0°, 4.5°, 5.0°, 5.5°, 6.0°}`。

每组几何参数对应 `6 × 5 = 30` 个来流工况。下表每个单元格代表其所在行的马赫数与所在列的攻角组成的一个工况，按马赫数优先、攻角递增编号：

| 马赫数 Ma / 攻角 α | 4.0° | 4.5° | 5.0° | 5.5° | 6.0° |
|---|---|---|---|---|---|
| 2.00 | C01 | C02 | C03 | C04 | C05 |
| 2.04 | C06 | C07 | C08 | C09 | C10 |
| 2.08 | C11 | C12 | C13 | C14 | C15 |
| 2.12 | C16 | C17 | C18 | C19 | C20 |
| 2.16 | C21 | C22 | C23 | C24 | C25 |
| 2.20 | C26 | C27 | C28 | C29 | C30 |

例如，C01 为 `(Ma, α) = (2.00, 4.0°)`，C30 为 `(2.20, 6.0°)`。以上为后续计算采用的工况定义，自动求解模块仍处于预留状态。

## 工作区组织

围绕上述几何参数和来流工况，本工作区按建模、网格、求解、数据、机器学习和异常反馈组织。`DATCOM` 独立保留，不参与重构。

| 目录 | 用途 |
|---|---|
| spaceclaim | SpaceClaim 建模代码 |
| fluent_meshing | Fluent 网格代码及隔离依赖 |
| fluent_solution | 自动求解预留，目前没有已验证的求解流程 |
| data_lib | 输入总表、阶段队列、高低保真运行、点云与来源索引 |
| ML_training | 机器学习预留 |
| feedback | 异常日志、参数快照和失败产物，大输入通过校验值引用 |
| docx | 文献、汇报、提示词、通用 skill 和资料导航 |
| experiments/autoCFD | 保留的独立研究项目 |
| scripts | 统一启动、验收、索引和回退入口 |
| archive | 完整备份及迁移清单，保留用于回退 |

统一配置位于 `workspace.json`，只记录相对数据路径。安装选择仍使用 ANSYS 环境变量或 SpaceClaim 的 `npm run setup`。

## 目录组成

下面按顶层目录列出**直接下级**，只展开一级；更深的层级见各目录自己的 README。`DATCOM` 独立保留，不在此说明。

类型含义：

- `目录` / `文件`：源码、文档或数据，由根仓库统一管理；
- `依赖` / `生成`：安装或运行产生的目录，**不纳入版本管理，不要手工编辑**，由 `.gitignore` 排除；
- `预留`：只建立目录与数据约定，尚无已验证实现。

工作区根目录本身就是一个 Git 仓库（分支 `master`，尚无提交）。`spaceclaim`、`fluent_meshing`、`experiments/autoCFD` 已不再是独立仓库，版本控制集中在根目录，排除规则见 `.gitignore`；迁移前的仓库 bundle 仍保留在 `archive/snapshot-pre-recovery-20261015T084726Z/`。下面每个目录都有自己的 `README.md` 说明其直接下级的作用；目录结构与 README 必须同步更新。

### `spaceclaim/` — SpaceClaim 文档检索 MCP server 与参数化建模

| 下级 | 类型 | 作用 |
|---|---|---|
| `server.mjs` | 文件 | MCP stdio 服务入口；标准输出只输出 JSON-RPC |
| `lib/` | 目录 | 核心库：CHM 检索与提取、几何参数与 schema、模板、运行记录、启动器、安装配置 |
| `scripts/` | 目录 | 命令入口：`missile.mjs` 由参数生成 `.scdoc`，`verify-real.mjs` / `verify-geometry.mjs` 真实验收，`smoke.mjs`，`setup.mjs` |
| `tests/` | 目录 | `node --test` 单元测试（协议、CHM 提取、几何、模板、路径等） |
| `docs/` | 目录 | 文档配图与设计规划记录 |
| `.github/` | 目录 | GitHub Actions CI 工作流 |
| `node_modules/` | 依赖 | npm 安装产物 |
| `package.json`、`package-lock.json` | 文件 | 依赖声明与锁定版本 |
| `README.md`、`README.en.md`、`README.bilingual.md` | 文件 | 中文、英文、双语说明 |
| `AGENTS.md`、`CONTRIBUTING.md`、`SECURITY.md`、`CHANGELOG.md`、`LICENSE`、`.gitignore` | 文件 | 协作、安全、变更、许可与忽略规则 |

### `fluent_meshing/` — Fluent 网格生成与校验

| 下级 | 类型 | 作用 |
|---|---|---|
| `mesh_missile.py` | 文件 | 专用 7 步网格工作流主脚本 |
| `mesh_runner.py` | 文件 | 启动并编排 PyFluent 会话 |
| `mesh_validation.py` | 文件 | 网格质量与区域、边界组的验收判定 |
| `workspace_runtime.py` | 文件 | 与 `spaceclaim` 对齐的 Python 侧运行记录 |
| `run_meshing.cmd` | 文件 | Windows 主入口，先解析可用的 3.10 / 3.11 解释器 |
| `mesh_check.cmd` | 文件 | 网格文件检查入口 |
| `fluent_meshing.md` | 文件 | 该模块的详细说明 |
| `README.md` | 文件 | 模块说明入口 |
| `tests/` | 目录 | pytest 工作流测试 |
| `tools/` | 目录 | 诊断脚本：网格文件查看、区域探测、与参考网格对比 |
| `_deps/` | 依赖 | PyFluent 依赖（需 Python 3.10 / 3.11，3.13 不支持） |
| `.mesh_inspect_deps/`、`.mesh_inspect_deps_313/` | 依赖 | 网格检查工具各自的隔离依赖，**两者与环境不可混用** |
| `.mesh-inspect-python`、`.python-path` | 生成 | 本机解释器路径记录，属于本地配置 |
| `__pycache__/` | 生成 | Python 字节码缓存，可安全删除 |

### `scripts/` — 统一启动、验收、索引与回退

| 下级 | 类型 | 作用 |
|---|---|---|
| `run.ps1` | 文件 | 唯一入口：`spaceclaim`、`fluent_meshing`、`verify-spaceclaim`、`verify-meshing`、`spaceclaim-mcp`、`index` |
| `index.mjs` | 文件 | 扫描运行与反馈记录，生成 `data_lib/index.json` |
| `snapshot.ps1` | 文件 | 新建完整快照（含未跟踪文件、本地配置与依赖）并写出 SHA-256 清单 |
| `rollback.ps1` | 文件 | 从指定快照恢复旧布局，保留新数据与反馈 |
| `migrate.ps1` | 文件 | 目录布局迁移 |
| `retire-old.ps1` | 文件 | 旧文件下线归档 |
| `find-directory-owner.ps1` | 文件 | 排查占用某个目录的进程 |
| `compare-models.mjs` | 文件 | 模型对比 |
| `README.md` | 文件 | 各脚本的用途与调用注意 |

### `data_lib/` — 输入、队列、运行记录与历史产物

| 下级 | 类型 | 作用 |
|---|---|---|
| `inputs/` | 目录 | 参数与原始输入：`cases.csv`（当前 4 行试算，`geometry_id` 暂为 `1`）、`missile_params_example.json`、`params_mine.json` |
| `queues/` | 目录 | 阶段队列与训练轮次：`spaceclaim.json`、`fluent_meshing.json`、`fluent_solution.json`、`datcom.json`、`ml.json`。记录仍为空 |
| `datasets/` | 目录 | 数据集。直接下级 `point_clouds/` 接收 `04_pointnet_demo.py` 的点云，点数和最终格式仍待定 |
| `runs/` | 目录 | 高保真 `high/<run-id>/<阶段>/`，低保真 `low/<geometry_id>/`。运行产物不入库，说明文件入库 |
| `datcom_data/` | 生成 | 早先 DATCOM 算例结果，保留作历史记录，不入库 |
| `legacy/` | 目录 | 迁移前的历史产物（`spaceclaim/`、`fluent_meshing/`）与 `migration-manifest.json` 迁移清单 |
| `runtime/` | 目录 | 运行期支撑数据（`test-support/`，当前为空） |
| `index.json` | 生成 | 由 `scripts/run.ps1 index` 更新的统一索引 |
| `README.md` | 文件 | 数据仓库约定 |

### `feedback/` — 异常反馈与失败产物

| 下级 | 类型 | 作用 |
|---|---|---|
| `spaceclaim/` | 生成 | 该阶段按 `run-<时间戳>-<短ID>/` 保存的异常包 |
| `fluent_meshing/` | 生成 | 该阶段按 `run-<时间戳>-<短ID>/` 保存的异常包 |
| `fluent_solution/` | 生成 | 该阶段按 `run-<时间戳>-<短ID>/` 保存的异常包 |
| `datcom/` | 生成 | 该阶段按 `run-<时间戳>-<短ID>/` 保存的异常包 |
| `README.md` | 文件 | 异常包内容与 `STOPPED` / `FAILED` 的判定说明 |

### `docx/` — 文献、汇报、提示词与技能资料

| 下级 | 类型 | 作用 |
|---|---|---|
| `reference_papers/` | 目录 | 原文献目录，文件保留原样 |
| `skills/` | 目录 | 独立保留的 Fluent skill 项目 |
| `prompts/` | 目录 | 项目提示词 |
| `dependency-inventory.json` | 文件 | 依赖清单 |
| `task_note.md`、`workspace_restructure_report.md` | 文件 | 任务笔记与重构报告 |
| `汇报.pptx` | 文件 | 汇报材料 |
| `README.md` | 文件 | 本目录导航 |

### `experiments/` — 保留的独立研究项目

| 下级 | 类型 | 作用 |
|---|---|---|
| `autoCFD/` | 目录 | 保留的研究代码（含自身 Git、`src/`、`Benchmark/`）；本轮不调试，且**未验证为本工作区的求解器** |
| `README.md` | 文件 | 保留项目的范围与使用限制 |

### `fluent_solution/` — 自动求解（预留）

| 下级 | 类型 | 作用 |
|---|---|---|
| `README.md` | 预留 | 仅建立目录与数据约定，尚未实现或验证自动求解入口 |

### `ML_training/` — 机器学习（预留）

| 下级 | 类型 | 作用 |
|---|---|---|
| `README.md` | 预留 | 仅建立目录与约定，尚未实现训练流程 |

### `archive/` — 完整备份与迁移快照

| 下级 | 类型 | 作用 |
|---|---|---|
| `migration-20261014T084018Z/` | 生成 | 迁移前的完整备份 |
| `retired-originals-20261014T132640Z/` | 生成 | 旧版原始文件下线归档 |
| `snapshot-pre-recovery-20261015T084726Z/` | 生成 | 恢复操作前的快照 |
| `README.md` | 文件 | 备份的用途与回退方式 |

备份不自动清理，作为 `scripts/rollback.ps1 -Snapshot` 的回退来源。

### `.vscode/` — 工作区编辑器配置

| 下级 | 类型 | 作用 |
|---|---|---|
| `settings.json` | 文件 | 编辑器与终端设置 |
| `README.md` | 文件 | 本目录说明与绝对路径处理约定 |

### 根目录文件

| 下级 | 类型 | 作用 |
|---|---|---|
| `workspace.json` | 文件 | 统一配置，只记录相对数据路径 |
| `AGENTS.md` | 文件 | 本工作区的规则与边界 |
| `.gitignore` | 文件 | 根仓库的忽略规则：运行产物、依赖、备份与大体积数据不入库 |
| `README.md` | 文件 | 本文件 |
| `DATCOM/` | 目录 | 独立保留，不参与重构，不在此说明 |

## 启动与验收

从本目录执行；也可以用启动脚本的绝对路径从任意目录执行：

```powershell
.\scripts\run.ps1 verify-spaceclaim
.\scripts\run.ps1 verify-meshing -Geometry <新生成的 missile.scdoc>
.\scripts\run.ps1 index
```

如果本机执行策略禁止运行脚本，用 `powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 ...`；需要数组开关（例如
`-WorkflowArgs @('-n','8')`）时改用 `-Command "& '<绝对路径>\scripts\run.ps1' verify-meshing -Geometry <...> -WorkflowArgs @('-n','8')"`。

`verify-meshing` 不会退回历史模型：必须显式传入 `-Geometry`，并走原专用 7 步工作流（局部尺寸 → 面网格 →
描述几何 → 共享拓扑 → 创建区域 → 更新区域 → 边界层 → 体网格）。验收要求写入的网格非空、三维、
正体积、正交质量达标，且区域与边界组齐全。

原命令行开关可通过 `-WorkflowArgs` 数组传递，例如：

```powershell
.\scripts\run.ps1 fluent_meshing -WorkflowArgs @('--help')
.\scripts\run.ps1 spaceclaim -WorkflowArgs @('--help')
```

SpaceClaim MCP 可使用 `.\scripts\run.ps1 spaceclaim-mcp`，其标准输出保留给 JSON-RPC。本次不修改工作区外的 MCP 注册。

## 建模入口

给一组几何参数即可生成含旧版外流场外壳与 BOI 的 `.scdoc`：

```powershell
node .\spaceclaim\scripts\missile.mjs --list
node .\spaceclaim\scripts\missile.mjs --preset .\data_lib\inputs\missile_params_example.json
```

`data_lib/inputs/missile_params_example.json` 填写[几何参数表](#几何参数定义与范围)中的 11 个主尺寸基准值
（`L_N` `L` `D` `L_W` `C_RW` `C_TW` `B_W` `L_F` `C_RF` `C_TF` `B_F`），其余几何与区域配置沿用旧版默认值，
仍可逐项覆盖。仅生成脚本的运行记为 `GENERATED`，`runtimeVerified` 恒为 false。

## 数据与回退

高保真默认写到 `data_lib/runs/high/<run-id>/<阶段>/`，`<run-id>` 形如 `run-<时间戳>-<短ID>`。低保真由 `DATCOM/scripts/01_batch_datcom.py` 读取 `data_lib/inputs/cases.csv`：同一个 `geometry_id` 加同一个 `altitude_m` 是一次调用，归档在 `data_lib/runs/low/<geometry_id>/`。点云由 `DATCOM/scripts/04_pointnet_demo.py` 用 `--geometry-id` 或 `--params` 写入 `data_lib/datasets/point_clouds/`。标识符编码、置信度、补点范围、点云最终格式、求解器和上报后的调整权仍待定。

建模和网格每次运行生成独立 `<run-id>` 和 `run.json`。真实验证通过的记录标记 `PASSED`；失败为 `FAILED`，主动分步停止为 `STOPPED`；仅生成脚本为 `GENERATED`。文件存在或进程退出成功不能单独作为验收依据。

历史文件保持原内容，旧路径由 `data_lib/legacy/migration-manifest.json` 和备份中的迁移清单解释。可从 `data_lib/index.json` 查找来源、验证状态和反馈。

需要恢复原布局时，将备份目录的绝对路径传给 `scripts/rollback.ps1 -Snapshot`。脚本先验证备份，再保留当前目录并恢复旧目录；新数据和反馈不会被删除。备份不自动清理。

清理或恢复前，可用 `scripts/snapshot.ps1` 新建完整快照：两个仓库（含 Git、未跟踪文件、本地配置与依赖）、根入口和说明
一并复制，并写出文件清单与 SHA-256，复制后会逐文件复核。`DATCOM`、`archive`、`data_lib`、`feedback` 不参与复制。
