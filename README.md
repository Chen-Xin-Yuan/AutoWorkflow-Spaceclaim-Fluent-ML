# 单阶段手动运行接口

本文件只讲一件事：**绕过 dispatcher，单独跑某一个阶段的脚本**，用什么命令、传什么参数、产物落在哪里、怎么判断跑通了。
管线调度、数据库入库、审核流程不在本文范围；下列参数值以各脚本前部「输入 / 输出 / 参数配置」区为准（参数以代码为准，本文只做索引）。

## 0. 三个阶段共用的约定

| 项目 | 约定 |
| --- | --- |
| 运行根 | `data_lib/runs/high/<stage>/<run-id>/`（`workspace.json` 的 `dataRoot`） |
| run-id | 库里是 `<geometry_id>__<condition_id>__<时间戳>`（UTC）；手工不传时脚本自生成 `run-<UTC 时间戳>-<8 位十六进制>` |
| 运行目录 | 必须**不存在**。脚本是独占创建，目录已存在直接失败 |
| 失败留证 | 整个运行目录被移到 `feedback/<stage>/<run-id>/`，去那里找日志和证据 |
| 验收 | 脚本自己打印逐条判据；只有退出码 0 才算通过，进程退出码本身不被当作几何/网格正确的证明 |
| 时间戳 | 一律 UTC，由脚本自己生成 |

环境：Windows；ANSYS 安装位置从 `AWP_ROOT<数字>` / `AWP_ROOT` / 本机 setup 配置解析，不要把安装路径写进命令行或文档。

**转发壳**（可选）：`scripts\run.ps1` 只是下面三节入口的薄转发，参数原样透传，适合不想记长路径时用。

```powershell
.\scripts\run.ps1 spaceclaim --list                  # == node spaceclaim\scripts\missile.mjs --list
.\scripts\run.ps1 spaceclaim --L 3.69 --out D:\tmp\try
.\scripts\run.ps1 fluent_meshing --geometry D:\stage\missile.scdoc --stop-after surface
.\scripts\run.ps1 verify-spaceclaim --out D:\tmp\vsc             # == verify-geometry.mjs
.\scripts\run.ps1 verify-meshing -Geometry D:\stage\missile.scdoc # 必须先给 -Geometry，没有默认模型
```

若报 `running scripts is disabled on this system`（执行策略为 Restricted），用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 <mode> ...`，或者直接用第 1、2 节里的原始命令。

---

## 1. SpaceClaim 建模

**入口**：`spaceclaim\scripts\missile.mjs`（在 `spaceclaim` 目录内等价于 `npm run missile`）

```powershell
# 看全部开关、默认值和优化区间（JSON，不起 SpaceClaim）
node spaceclaim\scripts\missile.mjs --list

# 全默认值跑一次建模
node spaceclaim\scripts\missile.mjs

# 改几个几何参数再跑
node spaceclaim\scripts\missile.mjs --L 3.69 --D 0.18 --thickness_percent 4.462 --out D:\tmp\try

# 用上一次运行留下的 params.json 复盘（--preset 优先级低于命令行开关）
node spaceclaim\scripts\missile.mjs --preset data_lib\runs\high\spaceclaim\<run-id>\params.json

# 只生成 params.json + model.py，不启动 SpaceClaim（最快的迭代方式）
node spaceclaim\scripts\missile.mjs --generate-only --out D:\tmp\gen
```

**选项**

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--<几何参数>` | 见 `--list` | 一次一个开关，省略取默认值；命令行 > `--preset` |
| `--preset <file.json>` | 空 | 预设文件；接受裸参数对象或上次生成的 `params.json` |
| `--out <dir>` | `data_lib/runs/high/spaceclaim` | 产物根，最终目录是 `<out>/<run-id>` |
| `--run-id <id>` | 自生成 | 想和库里/审核对齐时显式传三段式 id |
| `--name <file.scdoc>` | `missile.scdoc` | 只允许叶子文件名 |
| `--generate-only` | 关 | 只写 `params.json` / `model.py` 和运行清单，不启动 SpaceClaim |
| `--timeout <ms>` | 300000 | SpaceClaim 单次执行超时 |
| `--json` | 关 | 机读输出 |
| `--list` | — | 打印默认值、优化区间、几何字段名后退出 |

**开关来自哪里**：`spaceclaim\lib\missile-schema.mjs`，加参数只需要改那里，CLI / MCP / `params.json` 同步生效。
16 个几何参数：`L_N` `L` `D` `L_W` `C_RW` `C_TW` `B_W` `L_F` `C_RF` `C_TF` `B_F`（11 个长度）+ `fin_section` + `thickness_percent` + 截面参数（`hexagon` 用 `flat_percent`；`naca` 用 `le_radius_index`、`max_thickness_tenths`）+ `nose_shape`。
此外，建模实现参数（`k_nose` `k_tip` `fin_phase` `root_overlap` `le_mode`）和流场域参数（`enclosure_*` `boi_*` `model_placement` `model_offset`）也从这个入口传入——网格阶段直接用它们生成的命名选择，所以调试网格时先在这里改。
当前实现范围：`nose_shape` 只有 `cone`；`le_mode` 只有 `trailing_aligned`；`fin_section` 支持 `naca` 和 `hexagon`。

**前置**：`SpaceClaim.exe` 按 `SCDM_EXECUTABLE` > `npm run setup` 存下的本机配置 > `AWP_ROOT<版本>` > `AWP_ROOT` 顺序解析；都没有时报 `SpaceClaim.exe not found`。首次配置跑 `npm run setup`（在 `spaceclaim` 目录内），页面里选 AWP 或手填 `SpaceClaim.exe`。API CHM 同理，顺序是 `SCDM_CHM` > setup 配置 > `AWP_ROOT<版本>`。

**产物**（`<run-id>/` 内）：`params.json`、`model.py`、`<name>.scdoc`、`run.json`、`result.json`，脚本报错时还有 `diagnostic.txt`；`request.json`、预览图、`error.txt` 不保留。`run.json` 里是 `status` + `verification.runtimeVerified` + 产物清单（含 sha256），`result.json` 里是实测体积/面积/整弹质心 `measurements.centerOfMassM`（弹尖起算）。

**验收**：控制台逐条打印 `通过/失败  判据  :: 详情`，最后一行给出产物目录。退出码 `0` 通过、`1` 验收未通过（产物保留在运行目录）、`2` 参数非法或调用错误。

**旁路工具**

| 命令 | 用途 |
| --- | --- |
| `node spaceclaim\scripts\verify-geometry.mjs [--out <dir>] [--timeout <ms>]` | 圆周阵列 fixture 端到端验证 SpaceClaim 通路（不是整弹几何） |
| `npm run verify:real` | 只验证 CHM 检索链路，不启动 SpaceClaim |
| `npm run smoke` | MCP 协议 smoke（需要 CHM 环境） |

---

## 2. Fluent Meshing 网格

**入口**：`fluent_meshing\run_meshing.cmd`（批处理，必须经 cmd 启动；PowerShell 里用 `cmd /c` 或 `& .\fluent_meshing\run_meshing.cmd ...`）

```powershell
# 最小调用（--geometry 必填，没有默认模型）
cmd /c fluent_meshing\run_meshing.cmd --geometry D:\stage\missile.scdoc

# 断点调试：画到局部尺寸就停，保留会话上手看
cmd /c fluent_meshing\run_meshing.cmd --geometry D:\stage\missile.scdoc --stop-after sizing --show-gui --keep-fluent

# 与库里对齐的运行目录和产物名
cmd /c fluent_meshing\run_meshing.cmd --geometry D:\stage\missile.scdoc --run-id <run_id> `
    --out data_lib\runs\high\fluent_meshing\<run_id>\<geometry_id>_mesh.msh.h5
```

**参数**

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--geometry <file>` | 必填 | 导入的 `.scdoc`；相对路径按调用时工作目录解析 |
| `-n` / `--processors <n>` | 12 | Fluent 进程数（不是线程数，一进程一物理核） |
| `--run-dir <dir>` | 自生成 | 显式运行目录，必须不存在；优先级高于 `--run-id` |
| `--run-id <id>` | 自生成 | 三段式 id，运行目录落 `data_lib/runs/high/fluent_meshing/<id>/` |
| `--out <file>` | `<run-dir>/missile_mesh.msh.h5` | 网格产物路径，必须不存在 |
| `--start-timeout <s>` | 300 | 等 Fluent 启动就绪 |
| `--timeout <s>` | 900 | 整个网格流程总时限（超时杀整棵进程树） |
| `--keep-fluent` | 关 | 结束时不关 Fluent 会话（配合 `--show-gui` 手查） |
| `--show-gui` | 关 | 显示 Fluent 界面 |
| `--draw-size-control <true\|false>` | true | 局部尺寸控制是否画尺寸框 |
| `--stop-after <step>` | 不设 | 画到指定步骤就停：`import` / `sizing` / `surface` / `describe` / `share` / `regions` / `update-regions` / `bl` / `volume` |

**两条硬约束**

1. **模型文件名主干必须是 `missile`**：Fluent 用文件名主干拼 face zone label，`Add Local Sizing` 要的 label 是写死的 `missile_body`、`missile_nose`、`missile_nose_tip`、`missile_fin`、`missile-boi`。把已审模型复制成 `<某个目录>\missile.scdoc` 再喂进去；直接喂 `<geometry_id>_spaceclaim.scdoc` 会因为 label 变成 `<geometry_id>_spaceclaim-missile_body` 之类而报 Unknown exception。
2. **`--out` 指向的文件必须不存在**，脚本不会覆盖（新运行目录天然满足）。

**解释器**：`FLUENT_MESH_PYTHON` > `fluent_meshing\.python-path` 里写的路径 > `%LOCALAPPDATA%\Programs\Python\Python310|311` > `py -0p` 找到的 3.10/3.11。启动器把 `PYTHONPATH` 指向 `fluent_meshing\_deps`（PyFluent 装在这里，不能用 3.13）。Fluent 版本固定 2024 R1，需要 `AWP_ROOT241`。

**产物**（运行目录内）：`request.json`、`pipeline.log`、`quality.json`、`quality-native.json`、`validation.trn`、`completed_workflow.wft`（或 `stopped_workflow.wft` / `failed_workflow.wft`）、`supervisor.log`、`run.json`、`mesh-inspection.json`、网格文件。失败时整目录移到 `feedback/fluent_meshing/<run-id>/`。

**验收与输出**：stdout 最后一行是 JSON：`{"status","runId","runDir","verification"}`。`PASSED` 需要两道独立检查都过：
1. worker 内检查：正交质量下限（`mesh_validation.MINIMUM_ORTHOGONAL_QUALITY`）、边界 zone `inlet`/`outlet`/`wall`、唯一的非空流体 cell zone、导弹壁面 zone 非空；
2. 主管进程用**另一个隔离环境**（`.mesh_inspect_deps_313` + `.mesh-inspect-python` 或 `MESH_INSPECT_PYTHON`）把写出的 `.msh.h5` 重新读一遍，核对单元数和 zone 名。

第 2 道缺解释器会直接判 `FAILED`（"isolated mesh-inspection interpreter is not configured"）。退出码：`0` 为 `PASSED`/`STOPPED`，`1` 失败（含超时、验收不过），`2` 运行目录创建失败。

**单文件复核工具**

| 命令 | 用途 |
| --- | --- |
| `cmd /c fluent_meshing\mesh_check.cmd <mesh.msh.h5>` | 用隔离解释器读网格，打印维度/单元数/区域 JSON，不启动 Fluent |
| `python fluent_meshing\tools\probe_regions.py --geometry <file.scdoc> [-n N]` | 起会话打印导入后的 label / region / zone 名，排查命名问题 |
| `python fluent_meshing\tools\compare_with_reference.py <new.wft> [<ref.wft>]` | 把生成的 workflow 文件和历史参考逐任务对比 |

---

## 3. Fluent Solution 求解

**现状：还没有可运行脚本。** `fluent_solution\` 目录为空，`scripts\dispatch.py` 的 `UNIMPLEMENTED_STAGES` 里写着「求解脚本未实现（计划 P5 只建骨架）」，`--stage fluent_solution` 领到任务会直接标 `rejected` 并写上报单，不会假装成功。

在脚本补齐之前，求解只能在 Fluent 里手工算。已经定下来、写脚本时要遵守的接口：

| 接口 | 位置 / 形状 |
| --- | --- |
| 上游交接 | `data_lib/queues/meshing_for_solution/<run_id>.json`：`schemaVersion`、`queue`、`run_id`、`geometry_id`、`condition_id`、`artifact{rel_path,size,sha256}`、`review_id`、`approved_at` |
| 网格文件 | 上面 JSON 的 `artifact.rel_path`（相对工作区），目前指 `data_lib/runs/high/fluent_meshing/<run_id>/<geometry_id>_mesh.msh.h5` |
| 工况 | 库表 `condition(mach, alpha_deg, altitude_m)`，`run_id` 中段就是 `condition_id` |
| 产物目录 | `data_lib/runs/high/fluent_solution/<run_id>/`，含 `run.json`（`status` + `verification.runtimeVerified`）与 `result.json` |
| 结果 | 只能经审核入库，写进 `cfd_task` 的 `CL` / `CD` / `CM` / `converged` / `residual` |
| 审核 | `python scripts/review.py --kind solution --run-id <run_id>`（库里必须已有该 `run_id`） |

---

## 4. 手工跑时容易踩的点

- **手工运行不推进数据库状态**：状态推进只由 `scripts\dispatch.py` 做，写库只有 `scripts\db.py` 一个入口（`review.py --record` 也只是转调它）。手工跑出来的目录是证据，不是入库结果。
- **`review.py` 靠 `run_id` 找目录**（`data_lib/runs/high/<stage>/<run-id>/`），并要求库里已有该任务。手工自生成的 `run-...` id 天然对不上，会直接报「库里没有 run_id ... 对应的任务」；想走审核就用 `--run-id <库里那个 id>` 显式指定。
- **目录和文件都是独占创建**：`--run-dir`、`--out`、运行目录三者只要已存在就失败，重跑就换个新 id。
- **失败先看 `feedback\<stage>\<run-id>\`**：`run.json` 的 `error`、`supervisor.log`、`pipeline.log`（网格）或 `diagnostic.txt`（建模）里的第一条异常才是根因。
- **不要按旧文档改参数**：参数以 `workspace.json`、各脚本前部配置区、schema 默认值和数据库里的行为准；本文不含参数表，需要列默认值时跑 `node spaceclaim\scripts\missile.mjs --list` 或看 `fluent_meshing\mesh_runner.py` 前部。
