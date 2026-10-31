# 统一启动、验收、索引与回退入口

脚本从工作区根目录执行，也可以用绝对路径从任意目录执行。对外统一入口是 `run.ps1`。

| 下级 | 类型 | 作用 |
|---|---|---|
| `run.ps1` | 文件 | 唯一入口，模式为 `spaceclaim`、`fluent_meshing`、`verify-spaceclaim`、`verify-meshing`、`spaceclaim-mcp`、`index`、`db`、`watch`、`control`、`dispatch`、`review` |
| `db.py` | 文件 | **唯一允许写库的程序**：建表、参数规范化与身份生成、导入导出、审核入库、上报单、启停开关、重排、`gc`。命令：`init` / `check` / `identify` / `import` / `export` / `record-review` / `report` / `requeue` / `control` / `claim` / `gc` |
| `watch.py` | 文件 | watcher：`pending → queued`、僵死回收、被删产物复活。**不启动任何进程** |
| `dispatch.py` | 文件 | 派发：从 `queued` 领一条、建目录、拉起本阶段脚本、按退出情况推进状态；不做审核 |
| `review.py` | 文件 | 确定性审核判据，产出 `checks` 清单与审核 JSON；`--record` 时经 `db.py` 入库 |
| `docs.py` | 文件 | 把参数值从代码同步进 README 的生成块：`--check` 查漂移、`--write` 重写。参数表只由它写，人只改块外的散文 |
| `train.py` | 文件 | 训练闭环：读 `passed` 的高保真样本、训最近邻代理、把不确定的 (16 参数, 3 工况) 以 `origin='active_learning'` 写回 `cfd_task`（`pending`），并记 `training_round` |
| `control.py` | 文件 | 启停：`start` / `pause` / `stop` / `status`，`stop` 用 Win32 `TerminateProcess` 连子进程一起终止整棵进程树（等价 `taskkill /T /F`，但不受 `taskkill` 被拒影响） |
| `README.md` | 文件 | 本文件 |
| `snapshot.ps1` | 文件 | 新建完整快照（含两个仓库的 Git、未跟踪文件、本地配置与依赖），写出文件清单与 SHA-256 并在复制后逐文件复核 |
| `rollback.ps1` | 文件 | 从指定快照恢复旧布局；先验证备份，保留当前的新数据与反馈 |
| `migrate.ps1` | 文件 | 目录布局迁移 |
| `retire-old.ps1` | 文件 | 旧文件下线归档 |
| `find-directory-owner.ps1` | 文件 | 排查占用某个目录的进程 |
| `compare-models.mjs` | 文件 | 模型对比 |
| `README.md` | 文件 | 本文件 |

## 注意

- `db.py` 是唯一允许写 `data_lib/datasets/SQLite/missile.db` 的程序。库路径优先级：`--database` > 环境变量 `MISSILE_DB` > `workspace.json` 的 `database` 字段。`check` 会把几何/工况的身份串与冻结值逐字比对，改名或改精度会立刻报错。
- 入库只有一个闸门：`python scripts/db.py record-review --file <审核 JSON>`（`review.py --record` 是它的便捷写法）。`review` 表里没有通过记录，产物就永远不算数。
- 三个队列相关的日常命令：
  - `python scripts/db.py import --table high|low`：两份输入表导入（追加型、整份失败即整份不写）。
  - `python scripts/db.py export --format queue --queue scdoc_for_fluent`：重建队列指针目录（`record_review` 通过时会自动重建）。
  - `python scripts/control.py status`：看各阶段七态计数与在跑的进程数。
- `rejected` / `cancelled` 不是终点：`python scripts/db.py requeue --stage spaceclaim --geometry-id <id> --resolve-reports` 把它放回 `pending`，再 `watch.py --once` 排进 `queued`，然后照常派发。重排只动排队状态，不删审核历史、不重置重试额度；任务还在 `running` 时先 `control.py stop`。
- 派发器一次只跑一条：`python scripts/dispatch.py --stage spaceclaim --once`；`--loop` 常驻。
  `datcom` 阶段按"同一几何 + 同一高度"整批领取，跑完由 `review.py --kind datcom --record` 自检并逐工况入库。
  `fluent_meshing` 已可派发（要求 `model_path` 非空、即模型已过审）；`fluent_solution` 仍未实现，会被明确拒绝并上报，不会静默跳过。
  - 建模通过后派发器**自动调用 `DATCOM/scripts/04_pointnet_demo.py --geometry-id`** 生成点云：点云是模型审核的否决项，不再靠人工补跑。失败只打警告，模型仍进 `reviewing`，由审核给出明确否决理由。
  - 网格用 `--out <run_dir>/<geometry_id>_mesh.msh.h5` 显式指定产物名：入库闸门要求产物名以 `geometry_id` 开头，而 `fluent_meshing` 的默认名是 `missile_mesh.msh.h5`（历史运行仍是这个名字，审核与 `db.py` 对网格阶段按 `.msh.h5` 结尾放行）。
  - 喂给 Fluent 的是 `data_lib/runtime/model_stage/<run_id>/missile.scdoc` 这份**副本**，不是过审的产物本身：`.scdoc` 一旦被重新保存（界面打开后保存、读取器写回加密片缓存），字节就变，`run.json` 的 sha256 证据链会断。副本必须叫 `missile.scdoc`——Fluent 用文件名主干加部件名生成 face zone label，过审产物名是 `<geometry_id>_spaceclaim.scdoc`，直接喂会得到 `<geometry_id>_spaceclaim-boi`，而工作流硬性要求 `missile-boi`。
  - 子进程一律以 `Popen` 启动并把 pid 登记进 `task_process`，退出后注销；超时用整棵进程树终止（ANSYS 会再叉子进程）。这是 `control.py stop` 能真正杀到进程、`watch.py` 僵死回收能生效的前提。
- 审核有防回归守卫：对已经 `passed` 的任务再跑 `review.py --record` 会被拒绝（重审会把状态打回 `rejected` 并清空产物路径）。确需重审要显式 `--force --force-reason "<理由>"`；`--force` 只放宽"产物 sha256 与 `run.json` 记录一致"这一条，其余否决性判据照旧。
- `watch.py`、`dispatch.py`、`control.py`、`review.py` 都不自己写 SQL，而是 import `db.py` 的函数；写入路径仍然只有一条。
- `run.ps1` 的位置参数会并入 `-WorkflowArgs`，所以 `run.ps1 db check` 与 `run.ps1 db -WorkflowArgs @('check')` 等价；`--once` 这类双横线开关现在也能直接跟在模式后面。
- `run.ps1` 保持纯 ASCII。Windows PowerShell 5.1 把无 BOM 的 `.ps1` 当 ANSI 读，中文注释会变成乱码字节并可能让参数绑定直接失败；要写中文就把文件存成 UTF-8 with BOM。
- `run.ps1 index` 现在是 `python scripts/db.py export --format index` 的别名：索引从库里生成，不再扫盘。原来的 `index.mjs` 已退役（它扫出来的 24 条里 14 条指向已删目录）。`run.ps1` 也不再在其他模式结束时自动刷新索引。
- `index` 在 `data_lib/runs/` 和 `feedback/` 里查找 `run.json`。高保真运行在 `runs/high/<run-id>/<阶段>/`，低保真 DATCOM 归档在 `runs/low/<geometry_id>/`。DATCOM 归档目前没有 `run.json`，所以不会进入这份索引。
- `spaceclaim-mcp` 模式下标准输出保留给 JSON-RPC，不打印任何提示，也不接受工作流参数。
- `verify-meshing` 必须显式传入 `-Geometry <新生成的 .scdoc>`，没有默认模型，不会退回历史几何。
- 需要数组开关（例如 `-WorkflowArgs @('-n','8')`）时，改用 `-Command "& '<绝对路径>\scripts\run.ps1' ..."` 调用。
- 本机执行策略禁止运行脚本时，用 `powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 ...`。
