# 自动化工作区

几何参数与计算工况按以下约定设置。

## 参数化方式与来流条件

### 几何参数定义与范围

外形用 14 个几何参数描述：下面 11 个主尺寸，以及随后 3 个舵面翼型参数。11 个主尺寸的长度单位是米（m），参数名与建模输入 JSON 的字段一致，也是 `data_lib/inputs/cases.csv` 里的几何列。3 个翼型参数前舵和后舵共用，与鼻锥头型、舵面布局、基准几何一起写在 `DATCOM/scripts/missile_spec.py` 前部的配置区（该文件是唯一需要改参数的地方，DATCOM 批量计算与点云脚本共用同一份）。

**不在这 14 个参数里**的外形数据（不进总表，属设计固定项）也在同一个文件的配置区：鼻锥头型（`NOSE_SHAPE = "ogive"` 切线卵形 / `"cone"` 直线圆锥，对应 DATCOM 的 `TNOSE`）、锥顶倒角（只有点云用）、舵面布局（`LE_MODE` / `FIN_PHASE` / `CRUCIFORM_COUNT`）和基准几何。

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

舵面翼型参数一共 3 个，但具体是哪 3 个取决于截面类型。DATCOM 与点云的默认截面是**对称六边形**（弯度 0，中弧与弦线重合）；也可以切成对称 NACA 四位数。基准值就是 `DATCOM/scripts/missile_spec.py` 前部 1.2 配置区的默认值。控制卡写法见 [DATCOM 说明](DATCOM/README.md) 的「四、舵面翼型」。

鼻锥头型不属于这 14 个参数：默认是切线卵形（DATCOM `TNOSE = OGIVE`，也是不写 `TNOSE` 时的默认），可在同一个 `DATCOM/scripts/missile_spec.py` 配置区切成直线圆锥（`TNOSE = CONE`）。同一外形下两种头型的系数不同（Ma 0.8、α 4°：CL 2.229 / CD 0.430 / CM −5.712 对 CL 2.210 / CD 0.431 / CM −5.893），历史结果只在头型相同时可比。详见 [DATCOM 说明](DATCOM/README.md) 的「五、鼻锥头型」。

| 截面 | 设计变量 | 定义 | 允许值 | 基准 |
|---|---|---|---|---|
| 六边形（默认） | `thickness_percent` | 最大厚度，占弦长的百分数 | 大于 0、小于 40 | 4.46 |
| | `flat_percent` | 平顶段长度，占弦长的百分数；对称时最厚处从 `(1−flat)/2` 开始 | 大于等于 0、小于 100 | 61.0 |
| | `le_radius` | DATCOM 的 LER 卡片值（前缘圆角参数），只进 DATCOM，点云按尖前缘画 | 大于等于 0；0.5 起饱和 | 0.5 |
| NACA（可切回） | `thickness_percent` | 最大厚度，占弦长的百分数 | 大于 0、小于 40 | 4.48 |
| | `le_radius_index` | 前缘半径指数，6 是标准圆前缘 | 整数 0–9 | 6 |
| | `max_thickness_tenths` | 最大厚度距前缘的弦向位置，单位 1/10 弦长 | 2、3、4、5、6 | 3 |

SpaceClaim 默认使用 NACA 那一组（`fin_section=naca`）给前后舵放样，平面形状仍由根弦、梢弦和展长决定，并另外使用头部倒圆 `k_nose`、`k_tip`。翼型方程与 `DATCOM/scripts/missile_spec.py` 第 4 节相同。`fin_section=hexagon` 时改为尖前缘、尖后缘的对称六边形，厚度仍用 `thickness_percent`，等厚段由 `hex_flat_start`、`hex_flat_end` 决定；这一选择只存在于 SpaceClaim 建模，不改变上表的 14 个几何参数。**注意**：DATCOM/点云默认已是六边形，SpaceClaim 默认仍是 NACA，要让两边同源，需把 SpaceClaim 的 `fin_section` 设成 `hexagon` 并把三处数值对齐。

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

围绕上述几何参数和来流工况，本工作区按建模、网格、求解、低保真 DATCOM、数据、机器学习和异常反馈组织。高保真链路是 SpaceClaim → Fluent Meshing → Fluent Solution，低保真链路是 DATCOM。两条链路读同一张 `data_lib/inputs/cases.csv`，之后互不阻塞。

| 目录 | 用途 |
|---|---|
| spaceclaim | SpaceClaim 建模代码 |
| fluent_meshing | Fluent 网格代码及隔离依赖 |
| fluent_solution | 自动求解预留，目前没有已验证的求解流程 |
| DATCOM | 低保真气动力程序与模板。正式入口读总表，结果写到 `data_lib/runs/low/` |
| data_lib | 输入总表、阶段队列、高低保真运行、点云与来源索引 |
| ML_training | 机器学习预留 |
| multiagents_workbench | 第 1 课多智能体协作：只读 cases.csv 查询几何与工况，不启动 ANSYS 或 DATCOM |
| feedback | 异常日志、参数快照和失败产物，大输入通过校验值引用 |
| docx | 文献、汇报、提示词、通用 skill 和资料导航 |
| experiments/autoCFD | 保留的独立研究项目 |
| scripts | 统一启动、验收、索引和回退入口 |
| archive | 完整备份及迁移清单，保留用于回退 |

统一配置位于 `workspace.json`，只记录相对数据路径。安装选择仍使用 ANSYS 环境变量或 SpaceClaim 的 `npm run setup`。

## 目录组成

下面按顶层目录列出**直接下级**，只展开一级；更深的层级见各目录自己的 README。`DATCOM/` 随本仓库保存，这里说明它的入口、系数和归档。

类型含义：

- `目录` / `文件`：源码、文档或数据，由根仓库统一管理；
- `依赖` / `生成`：安装或运行产生的目录，**不纳入版本管理，不要手工编辑**，由 `.gitignore` 排除；
- `预留`：只建立目录与数据约定，尚无已验证实现。

工作区根目录本身就是一个 Git 仓库（分支 `main`）。`spaceclaim`、`fluent_meshing`、`experiments/autoCFD` 已不再是独立仓库，版本控制集中在根目录，排除规则见 `.gitignore`；迁移前的仓库 bundle 仍保留在 `archive/snapshot-pre-recovery-20261015T084726Z/`。下面每个目录都有自己的 `README.md` 说明其直接下级的作用；目录结构与 README 必须同步更新。

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
| `inputs/` | 目录 | 参数与原始输入：`cases.csv` 的几何列是 11 个主尺寸（当前 4 行试算，`geometry_id` 暂为 `1`）。翼型三项在 `DATCOM/scripts/missile_spec.py` 前部配置区，几何参数共 14 个。另有 `missile_params_example.json`、`params_mine.json` |
| `queues/` | 目录 | 阶段队列与训练轮次：`spaceclaim.json`、`fluent_meshing.json`、`fluent_solution.json`、`datcom.json`、`ml.json`。记录仍为空 |
| `datasets/` | 目录 | 数据集。直接下级 `point_clouds/` 接收 `04_pointnet_demo.py` 的点云，外形由 14 个几何参数决定，点数和最终格式仍待定 |
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

### `DATCOM/` — 低保真气动力

`DATCOM/` 是本仓库里的低保真程序和模板，`.gitignore` 不排除这个目录。程序本体是 `DATCOM/datcom_bin/datcom.exe`。算例结果写到 `data_lib/runs/low/<geometry_id>/`，那些运行产物不入库。队列在 `data_lib/queues/datcom.json`，失败包在 `feedback/datcom/`。这条链路可以在几何、网格、求解尚未结束时单独运行。

正式计算不带参数。`DATCOM/scripts/01_batch_datcom.py` 自己读取 `data_lib/inputs/cases.csv`。同一个 `geometry_id` 加同一个 `altitude_m` 是一次调用，覆盖该几何、该高度在总表里的全部马赫数和攻角。`--params`、`--mach`、`--alpha`、`--alt` 以及脚本顶部的几何和来流列表只在 `--demo` 时使用。

```powershell
python DATCOM/scripts/01_batch_datcom.py
```

一次调用生成输入卡片并运行 `datcom.exe`。运行当下的协议文件放在 `data_lib/runs/low/.work/`，文件名是 `for005.dat`、`for006.dat`、`for022.dat`，算完后删除。标准输出和标准错误写入 `data_lib/runs/low/<geometry_id>/<geometry_id>_datcom.log`。

三个系数与后续高保真使用同一套参考量：升力系数 `CL`、阻力系数 `CD`、俯仰力矩系数 `CM`。参考面积是机身横截面积 `π(D/2)²`，参考长度是机身直径 `D`，力矩参考点在 `L/2`。舵面相位 `+`、后缘对齐和这个力矩参考点是本次计算的冻结约定，记在 `{geometry_id}_case_params.json`，不进入 `geometry_id`。

一次调用通过验收，要同时满足这三条：几何核对报告里的自检都已通过；系数表行数等于这次计算的工况数；每一行的马赫数、攻角和高度都能对上总表。通过后，该几何、该高度下这些工况在 `datcom.json` 里改为已完成。子智能体按队列领取仍待接入，脚本现在可以按总表直接运行。

同一几何再跑时，按 `geometry_id` 命名的归档由这次调用覆盖。系数总表里其它高度的行保留，本次工况的行被替换。一个几何只有这一张系数表，各高度、各工况都在里面。

| 归档文件 | 作用 |
|---|---|
| `{geometry_id}_for005.dat` | 输入卡片 |
| `{geometry_id}_for006.dat` | 原始输出 |
| `{geometry_id}_for022.dat` | 外形 |
| `{geometry_id}_aero_coefficients.csv` | 该几何唯一的系数表：工况编号、来流、11 个主尺寸、舵面翼型（NACA 编号与三个参数）和系数，各高度都合并在这一张表里 |
| `{geometry_id}_geometry_check.txt` | 几何核对 |
| `{geometry_id}_geometry_check.png` | 几何核对图 |
| `{geometry_id}_case_params.json` | 参数快照，含冻结的舵面相位、后缘对齐和力矩参考点 |
| `{geometry_id}_datcom.log` | 该次调用的标准输出和标准错误 |

点云由 `DATCOM/scripts/04_pointnet_demo.py` 生成。`--geometry-id` 从总表读取 11 个主尺寸，`--params` 读取一份同名字段的 JSON，翼型三项与头型、锥顶倒角都取 `missile_spec.py` 前部配置区（与 DATCOM 共用）。这 14 个几何参数加头型一起决定外形。默认写到 `data_lib/datasets/point_clouds/`。带几何标识时主文件是 `<geometry_id>_pointcloud.ply`。点数和最终格式仍待定。闭环训练放在 `ML_training/` 这一预留模块里。

当前总表有 4 行试算：`geometry_id` 暂为 `1`，高度 `0`，工况 C01、C02、C06、C07，归档在 `data_lib/runs/low/1/`。早先写到 `data_lib/datcom_data/` 的结果保留作历史记录。

### `ML_training/` — 机器学习（预留）

| 下级 | 类型 | 作用 |
|---|---|---|
| `README.md` | 预留 | 仅建立目录与约定，尚未实现训练流程 |

### `multiagents_workbench/` — 多智能体协作（第 1 课）

只读 `cases.csv` 查询几何和工况。不启动 SpaceClaim、Fluent 或 DATCOM，也不写总表。启动方式见该目录的 `README.md`。

| 下级 | 类型 | 作用 |
|---|---|---|
| `geometry_agent/` | 目录 | Geometry Agent，绑定 `127.0.0.1:10011`，按 `geometry_id` 返回 11 个主尺寸（米） |
| `condition_agent/` | 目录 | Condition Agent，绑定 `127.0.0.1:10012`，按本文件的 C01–C30 网格回报马赫数与攻角，并核对总表是否有该行 |
| `host/` | 目录 | 命令行客户端，按句子把问题发给上面两个 Agent |
| `requirements.txt` | 文件 | Python 依赖，固定 `a2a-sdk==1.1.0` |
| `README.md` | 文件 | 虚拟环境与三条启动命令 |
| `.venv/` | 依赖 | 本课隔离虚拟环境，**不纳入版本管理，不要手工编辑**，由根 `.gitignore` 的 `.venv/` 排除 |

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
（`L_N` `L` `D` `L_W` `C_RW` `C_TW` `B_W` `L_F` `C_RF` `C_TF` `B_F`）。翼型使用同一节里的三个基准值。
其余几何与区域配置沿用旧版默认值，仍可逐项覆盖。仅生成脚本的运行记为 `GENERATED`，`runtimeVerified` 恒为 false。

## 数据与回退

高保真默认写到 `data_lib/runs/high/<run-id>/<阶段>/`，`<run-id>` 形如 `run-<时间戳>-<短ID>`。低保真的调用、系数和归档文件见上面的 [DATCOM](#datcom--低保真气动力)。标识符编码、置信度、补点范围、点云最终格式、求解器和上报后的调整权仍待定。

建模和网格每次运行生成独立 `<run-id>` 和 `run.json`。真实验证通过的记录标记 `PASSED`；失败为 `FAILED`，主动分步停止为 `STOPPED`；仅生成脚本为 `GENERATED`。文件存在或进程退出成功不能单独作为验收依据。

历史文件保持原内容，旧路径由 `data_lib/legacy/migration-manifest.json` 和备份中的迁移清单解释。可从 `data_lib/index.json` 查找来源、验证状态和反馈。

需要恢复原布局时，将备份目录的绝对路径传给 `scripts/rollback.ps1 -Snapshot`。脚本先验证备份，再保留当前目录并恢复旧目录；新数据和反馈不会被删除。备份不自动清理。

清理或恢复前，可用 `scripts/snapshot.ps1` 新建完整快照：两个仓库（含 Git、未跟踪文件、本地配置与依赖）、根入口和说明
一并复制，并写出文件清单与 SHA-256，复制后会逐文件复核。`DATCOM`、`archive`、`data_lib`、`feedback` 不参与复制。
