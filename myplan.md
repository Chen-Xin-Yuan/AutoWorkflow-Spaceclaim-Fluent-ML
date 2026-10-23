# 工作流与数据闭环计划

本文是尚未实施的设计说明。它规定两条计算链路、队列、智能体职责和数据登记方式。当前目录、脚本和运行数据保持现状。

13 个参数指根目录说明里的 11 个几何尺寸，加上马赫数 `Ma` 和攻角 `α`。长度单位是米。`Ma` 与 `α` 使用根目录说明中的离散档：6 个马赫数乘 5 个攻角，共 30 个工况，编号 `C01`–`C30`。

## 计划参数与路径

路径都相对工作区根目录。`workspace.json` 里的 `dataRoot` 仍是 `data_lib`，`feedbackRoot` 仍是 `feedback`。下表是本计划要使用的位置和默认参数；实施时改这一处即可。

| 名称 | 值 | 作用 |
|---|---|---|
| 输入总表 | `data_lib/inputs/cases.csv` | 一行一个 13 元组。前两列是 `geometry_id` 和 `condition_id`，同一行后面就是参数 |
| 建模队列 | `data_lib/queues/spaceclaim.json` | SpaceClaim 的状态和产物路径 |
| 网格队列 | `data_lib/queues/fluent_meshing.json` | Fluent Meshing 的状态和产物路径 |
| 求解队列 | `data_lib/queues/fluent_solution.json` | Fluent Solution 的状态和产物路径 |
| DATCOM 队列 | `data_lib/queues/datcom.json` | 低保真计算的状态和产物路径 |
| DATCOM 程序 | `DATCOM/` | 只放程序和模板。入口是 `DATCOM/scripts/01_batch_datcom.py`，程序本体是 `DATCOM/datcom_bin/datcom.exe` |
| 训练轮次 | `data_lib/queues/ml.json` | 每一轮训练的状态，不按工况拆行 |
| 高保真数据 | `data_lib/fluent_CFD_data/` | 求解运行产物，按工况存放 |
| 低保真数据 | `data_lib/datcom_data/` | DATCOM 运行产物。每个 `geometry_id` 一个子目录，与高保真目录并列 |
| 点云 | `data_lib/datasets/point_clouds/` | 按几何标识存放的点云 |
| 运行产物 | `data_lib/runs/` | 一次执行一个 `run-<时间戳>-<短ID>/` 目录。目录名只区分执行，文件名另含可回总表的标识 |
| 失败包 | `feedback/<阶段>/<执行目录>/` | 沿用现有异常包。包内文件名与原产物相同，因此也能回总表 |
| 每阶段并发 | `1` | 同一阶段同时处于「进行中」的任务数 |
| 瞬时故障重试 | `2` | 启动失败、超时、进程中断的重试次数 |

现有单组几何示例仍是 `data_lib/inputs/missile_params_example.json`。它只含 11 个几何尺寸，继续作为建模示例。工作流的批量入口是上面的 CSV。

## 身份与共用

比较参数时使用固定字段顺序和固定小数写法，保证同一个数值只有一种写法。字段顺序是：

`L_N`、`L`、`D`、`L_W`、`C_RW`、`C_TW`、`B_W`、`L_F`、`C_RF`、`C_TF`、`B_F`、`Ma`、`alpha_deg`

- `geometry_id` 与前 11 个几何尺寸一一对应，放在第一列。同一几何的各个工况共用一个 `geometry_id`。
- `condition_id` 是 `C01`–`C30`，只由 `Ma` 和 `alpha_deg` 决定，放在第二列。每个编号只对应网格里的一对来流。
- 一行由 `geometry_id` 和 `condition_id` 唯一确定，这两个标识一起对应完整的 13 元组。总表不再另设 `case_id`。
- 在现有 30 个工况内，知道前两列就能确定 `Ma`、`alpha_deg` 和 11 个几何尺寸。若以后出现这 30 档以外的来流，先给它分配新的 `condition_id`，再写入总表。
- `geometry_id` 的字符串编码留到实施时锁定，并且必须由 11 个几何尺寸的规范写法稳定得出。编码里不使用双下划线 `__`。总表以前两列的组合保证唯一。队列和失败记录保存这两个标识，参数以总表该行为准。
- `run_id` 是 `{geometry_id}__{condition_id}`。它由前两列拼出，不另占一列，也不另外编号。文件名里看到它，就从最后一处 `__` 拆开，分别对总表第一列和第二列。

SpaceClaim、网格和点云都只由 11 个几何尺寸决定，因此每个 `geometry_id` 只生成一份模型、一份网格、一份点云。30 条工况记录指向这三份产物。求解按每一对 `geometry_id` 与 `condition_id` 各执行一次。DATCOM 按 `geometry_id` 调用一次，同一次调用覆盖该几何在总表里的全部工况。

几何参数变化会得到新的 `geometry_id`，来流变化会得到新的 `condition_id`。两种情况都追加新行，并得到新的 `run_id`。原行保留原来的前两列和原来的参数。已有行的参数不再改写，避免前两列还在、参数已经对不上。

## 产物文件名

中间产物的文件名本身要能回到总表，不依赖打开 `run.json` 才知道是哪一组参数。扩展名保持各程序原有的扩展名。

跟一个工况绑定的文件，以 `run_id` 开头：

| 文件 | 文件名 |
|---|---|
| 求解结果 | `{run_id}_fluent_solution.<扩展名>` |
| 求解日志 | `{run_id}_fluent_solution.log` |
| DATCOM 单工况系数 | `{run_id}_datcom.csv` |
| DATCOM 日志 | `{geometry_id}_datcom.log` |

模型、网格和点云由 30 个工况共用，只跟几何绑定，文件名以 `geometry_id` 开头。这个 `geometry_id` 就是相关 `run_id` 双下划线前面的部分。用第一列能列出这些工况，11 个几何参数在这些行上相同。

| 文件 | 文件名 |
|---|---|
| SpaceClaim 模型 | `{geometry_id}_spaceclaim.<扩展名>` |
| 建模日志 | `{geometry_id}_spaceclaim.log` |
| 网格 | `{geometry_id}_fluent_meshing.<扩展名>` |
| 网格日志 | `{geometry_id}_fluent_meshing.log` |
| 点云 | `{geometry_id}_pointcloud.<扩展名>` |
| DATCOM 输入卡片归档 | `{geometry_id}_for005.dat` |
| DATCOM 原始输出归档 | `{geometry_id}_for006.dat` |
| DATCOM 外形归档 | `{geometry_id}_for022.dat` |
| DATCOM 系数总表 | `{geometry_id}_aero_coefficients.csv` |
| DATCOM 几何核对 | `{geometry_id}_geometry_check.txt` |

一次执行仍放在 `data_lib/runs/run-<时间戳>-<短ID>/<阶段>/`。这个目录名只用来区分哪一次执行。目录里的模型、网格、日志、求解和 DATCOM 文件使用上表的文件名。失败包中的副本沿用同一文件名。重跑会新建执行目录，已经验收通过的文件名保持不变；同名新文件只有在该次验收通过后才替换队列里的产物路径。

`datcom.exe` 运行当下，工作目录里的协议文件名仍是 `for005.dat`、`for006.dat`、`for022.dat`。工作目录用 `data_lib/datcom_data/.work`。算例写完后只保留在 `data_lib/datcom_data/{geometry_id}/`，文件名改成上表里带 `geometry_id` 的名字。系数总表里每个工况再抽出一行，写成 `{run_id}_datcom.csv`，也放在这个子目录。`DATCOM/` 下面不保留算例结果。

## 两条链路

高保真链路：

`cases.csv` → SpaceClaim → Fluent Meshing → Fluent Solution → `data_lib/fluent_CFD_data/`

低保真链路：

`cases.csv` → DATCOM → `data_lib/datcom_data/`

两条链路从同一张总表分出，之后互不阻塞。DATCOM 可以在几何、网格、求解尚未结束时独立领取自己的未开始记录。网格只在对应几何已经完成时领取。求解只在对应网格已经完成时领取。

点云在新的 `geometry_id` 写入总表时生成，放在 `data_lib/datasets/point_clouds/`，不等待仿真。它是该几何的特征，供后续模型和检索使用。置信度与补点由 13 维参数上的多保真高斯过程负责。

## 逻辑图

### 总流程

```mermaid
flowchart TD
  main["主智能体<br/>维护总表、写入四份队列、处理上报"]
  csv["输入总表<br/>前两列 geometry_id 与 condition_id"]
  cloud["点云<br/>每个 geometry_id 一份"]

  qsc["spaceclaim.json"]
  qmesh["fluent_meshing.json"]
  qsol["fluent_solution.json"]
  qdat["datcom.json"]

  sc["SpaceClaim 子智能体"]
  mesh["Meshing 子智能体"]
  sol["Solution 子智能体"]
  dat["DATCOM 子智能体"]

  high["fluent_CFD_data<br/>CL、CD、CM"]
  low["datcom_data<br/>同名三个系数"]
  ml["机器学习子智能体<br/>多保真高斯过程"]
  report["失败待上报<br/>记录里带前两列标识"]

  main --> csv
  csv --> cloud
  csv --> qsc
  csv --> qmesh
  csv --> qsol
  csv --> qdat

  qsc --> sc
  sc --> qmesh
  qmesh --> mesh
  mesh --> qsol
  qsol --> sol
  qdat --> dat

  sol --> high
  dat --> low
  high --> ml
  low --> ml
  cloud --> ml
  ml -->|"置信度不足的新 13 元组"| csv

  sc --> report
  mesh --> report
  sol --> report
  dat --> report
  ml --> report
  report --> main
```

图中的队列文件就是缓冲区。子智能体完成任务后，把本阶段状态写成「已完成」，并写下产物相对路径。下一步从自己的队列里领取那些上游已完成、自身仍未开始的记录。阶段之间不互相调用。

### 一个几何如何摊到 30 个工况

```mermaid
flowchart TD
  geom["geometry_id<br/>11 个几何尺寸"]
  shared["各一份：scdoc、网格、点云"]
  cases["同一 geometry_id 配上 C01 到 C30"]
  hq["每一对标识求解一次"]
  lq["每个 geometry_id 调用一次 DATCOM<br/>再按工况拆开"]

  geom --> shared
  shared --> cases
  cases --> hq
  cases --> lq
  hq --> high["fluent_CFD_data"]
  lq --> low["datcom_data"]
```

建模队列和网格队列仍是每一对 `geometry_id` 与 `condition_id` 一行。执行时只领取该 `geometry_id` 的代表行。其余 29 行保持「未开始」，直到这一份产物验收通过，再一并写成「已完成」并填上同一路径。已有进行中或已完成的同几何记录时，子智能体不再另起一份模型或网格。DATCOM 队列同样每个工况一行，但一次调用按 `geometry_id` 把这些未开始的工况一起算完，再拆回各行。

### 状态流转

```mermaid
stateDiagram-v2
  [*] --> 未开始: 新的 13 元组写入本阶段队列
  未开始 --> 进行中: 上游已完成且本阶段领取
  进行中 --> 已完成: 验收通过
  进行中 --> 失败待上报: 瞬时重试后仍无法在本阶段内解决
  失败待上报 --> 进行中: 主智能体要求用原参数重跑
  已完成 --> [*]
```

四个阶段队列和训练轮次使用同一套状态词：未开始、进行中、已完成、失败待上报。

子智能体只读取状态为「进行中」的日志。工作流只把「未开始」且上游已满足的记录领进「进行中」。已完成的记录留在队列里供下游读取，不重复执行。

## 输入总表

`data_lib/inputs/cases.csv` 的每一行是一个工况。人直接改这张表。四份阶段 JSON 由主智能体根据总表维护。

列的顺序固定，标识符在前，参数紧跟其后：

| 顺序 | 列 | 来源 | 含义 |
|---|---|---|---|
| 1 | `geometry_id` | 由 11 个几何尺寸得出 | 几何标识。模型或网格出问题时，用它滤出共用该几何的各行 |
| 2 | `condition_id` | 由 `Ma` 与 `alpha_deg` 得出 | `C01`–`C30`。与第一列一起唯一确定这一行 |
| 3–13 | `L_N` `L` `D` `L_W` `C_RW` `C_TW` `B_W` `L_F` `C_RF` `C_TF` `B_F` | 人填写，或由学习回路追加 | 与建模 JSON 字段同名，单位米 |
| 14 | `Ma` | 人填写，或由学习回路追加 | 无量纲 |
| 15 | `alpha_deg` | 人填写，或由学习回路追加 | 攻角，单位度 |
| 16 | `origin` | 写入时确定 | `human` 或 `active_learning` |

表头就是：

```text
geometry_id,condition_id,L_N,L,D,L_W,C_RW,C_TW,B_W,L_F,C_RF,C_TF,B_F,Ma,alpha_deg,origin
```

出错时的查法：日志、队列里的失败记录、失败包和两个数据集都写出 `geometry_id` 和 `condition_id`。打开 `cases.csv`，用前两列找到这一行，从第 3 列到第 15 列就是当时的 13 个参数。只知道 `geometry_id` 时，用第一列可以列出共用这份模型或网格的全部工况。

主智能体发现总表里有新的规范 13 元组时，在四份阶段队列里各追加一行，状态为「未开始」。已存在的 `geometry_id` 与 `condition_id` 组合保持原状态。总表拒绝第二对相同的前两列。

## 阶段队列

每份阶段 JSON 都包含该阶段名称，以及按 `geometry_id` 与 `condition_id` 索引的记录。一条记录至少包含：

| 字段 | 含义 |
|---|---|
| `geometry_id` / `condition_id` | 与总表前两列一致，用它们回到总表取出参数 |
| `status` | 未开始、进行中、已完成、失败待上报 |
| `artifact` | 本阶段产物的相对路径；未完成时为空 |
| `log` | 进行中日志的相对路径；子智能体只读这个字段指向的日志 |
| `execution_dir` | 对应 `data_lib/runs/run-<时间戳>-<短ID>/`。它不是文件名里的 `run_id` |
| `updated_at` | 最近一次状态变化的时间 |

参数值放在总表该行，队列里保留前两列标识。失败包里再抄一份当时的 13 个参数，这样单个失败包也能读，不必先打开总表。两处不一致时，以总表里这对 `geometry_id` 与 `condition_id` 所在行为准，并在失败包中标明差异。

上游关系：

| 阶段 | 可以领取的条件 |
|---|---|
| SpaceClaim | 本行未开始；同一 `geometry_id` 没有进行中或已完成的模型 |
| Fluent Meshing | 本行未开始；对应 SpaceClaim 行已完成；同一 `geometry_id` 没有进行中或已完成的网格 |
| Fluent Solution | 本行未开始；对应网格行已完成 |
| DATCOM | 该 `geometry_id` 下还有未开始的工况。一次领取这个几何的全部未开始工况 |

领取后把该行改为「进行中」，写上 `log` 和 `execution_dir`，然后启动本阶段命令。日志文件名按「产物文件名」一节，以 `run_id` 或 `geometry_id` 开头。默认每个阶段同时只有一行处于进行中。几何共用的代表行占用这个名额，同几何的其余行继续留在未开始。

队列里的「已完成」表示对应运行记录为 `PASSED`，并且 `verification.runtimeVerified` 为 true。仅生成脚本产生的 `GENERATED` 记录仍不能交给下一阶段。`FAILED` 与 `STOPPED` 进入失败包，队列状态写成「失败待上报」。失败包和上报记录都把 `geometry_id` 与 `condition_id` 放在显眼位置。

`data_lib/queues/ml.json` 的一行是一轮训练，字段同样使用这四种状态，并记录训练日志、所用数据版本和模型产物路径。它不复制工况行。训练若引用了某些工况，记录这些 `geometry_id` 与 `condition_id`，以便回到总表前两列查看参数。

## 智能体

主智能体：

- 读取和校验 `cases.csv`。前两列的组合重复，或与该行 13 个参数对不上时，拒绝写入队列。
- 为新的 `geometry_id` 与 `condition_id` 组合写入四份阶段队列。
- 接收「失败待上报」。上报正文以前两列标识开头，再用它们到总表取出参数。
- 在确认原参数可以重跑时，把该行改回「进行中」。
- 几何、网格配方、求解设置或 DATCOM 设置需要改变时，保留原行，把新参数作为新的 13 元组追加到总表。新行的前两列按新参数生成。

四个阶段子智能体各自只写自己的 JSON。工具范围是：读本阶段队列、读进行中日志、重跑本阶段已有命令、更新本阶段状态、改为失败待上报。上报时必须带上 `geometry_id` 和 `condition_id`。

可以在本阶段内处理的情况，限于启动失败、超时和进程中断，次数默认 2 次。需要改科学设置或几何参数时，子智能体停止重试并上报。

SpaceClaim 现有 MCP 继续只承担文档检索，标准输出保持 JSON-RPC。本计划的流水线使用各阶段命令行入口。实施时建模走 `scripts/run.ps1 spaceclaim`，网格走 `scripts/run.ps1 fluent_meshing`。这次不注册新的外部 MCP。

机器学习子智能体读两个数据集、点云和总表。它训练、给出置信度，并把需要补充的 13 元组追加到 CSV，`origin` 填 `active_learning`。新行同样先写 `geometry_id` 和 `condition_id`。它不启动建模、网格、求解或 DATCOM。新行进入四份队列仍由主智能体完成。

## 数据登记

执行中的日志、请求和产物仍写入 `data_lib/runs/run-<时间戳>-<短ID>/<阶段>/`。失败包仍写入 `feedback/<阶段>/<执行目录>/`，内含 `run.json`、参数快照、诊断日志和已产生的文件。这些文件的文件名以 `run_id` 或 `geometry_id` 开头。`run.json` 与参数快照都记录 `geometry_id` 和 `condition_id`。大文件放在 `data_lib`，队列和失败包用相对路径、大小和 SHA-256 引用。

验收通过之后再登记系数：

| 数据集 | 写入时机 | 内容 |
|---|---|---|
| `data_lib/fluent_CFD_data/` | 求解行为已完成 | `geometry_id`、`condition_id`、`CL`、`CD`、`CM`、参考量、运行路径、验证记录 |
| `data_lib/datcom_data/{geometry_id}/` | 该几何的 DATCOM 调用已完成 | 原始输出、系数总表，以及每个工况的 `{run_id}_datcom.csv` |
| `data_lib/datasets/point_clouds/` | 新 `geometry_id` 进入总表 | 由 11 个几何尺寸确定生成的点云，以及引用它的 `condition_id` 列表 |

三个系数与现有 DATCOM 脚本一致：升力系数 `CL`、阻力系数 `CD`、俯仰力矩系数 `CM`。参考量也沿用该脚本：参考面积是机身横截面积 `π(D/2)²`，参考长度是机身直径 `D`，力矩参考点默认在 `L/2`。高低质量数据使用同名和这一套参考量。飞行高度不进入 13 个参数，作为整张总表共用的冻结值，由调用参数传入。训练只读取两侧数据集中已完成且带验证记录的条目。失败运行和未经重新验收的历史数据先经过明确审查，再决定能否进入训练集。

`scripts/run.ps1 index` 继续生成 `data_lib/index.json`，用于检索来源和验证状态。领取任务以四份阶段 JSON 为准。由索引或运行目录回到参数时，用 `geometry_id` 和 `condition_id` 查总表前两列。

## 学习闭环

预测交付的是高保真三个系数，以及每个系数的置信区间。DATCOM 结果作为低保真观测，CFD 结果作为高保真观测，输入向量是规范后的 13 个参数。点云按 `geometry_id` 存放，并在这一轮作为几何特征保存。

每一轮的顺序是：

1. 总表出现新的 13 元组。前两列写入对应的 `geometry_id` 和 `condition_id`。新几何立即生成点云。主智能体把四份队列中缺失的行补为未开始。
2. DATCOM 与 SpaceClaim 按各自队列领取。网格、求解按上游完成情况继续领取。
3. 求解和 DATCOM 验收通过后，系数分别进入 `data_lib/fluent_CFD_data` 和 `data_lib/datcom_data`。
4. 两侧都有已完成样本时，机器学习子智能体开始一轮训练，并在 `ml.json` 中把该轮标为进行中。
5. 训练完成后，把置信度不足的 13 元组追加到总表。这些新行有自己的 `geometry_id` 与 `condition_id`，四个阶段状态都是未开始。
6. 新行再次进入第 1 步。已完成的工况保持已完成，不会因为重新训练而重算。

本轮没有新的低置信度行要追加，并且四份阶段队列里都没有未开始或进行中的记录时，这一轮闭环结束。什么样的数值算置信度不足，留在下面的待定项里。

## 求解与 DATCOM 的落点

求解部分在本计划里写到子智能体、队列、输入和输出为止。输入是队列中已完成的网格路径。输出是 `CL`、`CD`、`CM`、日志和验证记录，参考量与 DATCOM 相同。求解文件名以 `{run_id}_fluent_solution` 开头，放在 `data_lib/fluent_CFD_data/`。失败包使用 `feedback/fluent_solution/<执行目录>/`，副本保持同一文件名。求解器程序本身留空。`experiments/autoCFD` 继续作为独立保留的研究代码。

低保真计算使用已经改名的 `DATCOM/`，不再另建模块，也不再把旧名 `missile_project` 当作隔离目录。入口是：

```text
python DATCOM/scripts/01_batch_datcom.py --params <该几何的 11 个主尺寸 JSON> --mach <总表中的马赫数> --alpha <总表中的攻角> --alt <冻结高度>
```

一次调用对应一个 `geometry_id`。脚本生成输入卡片并调用 `DATCOM/datcom_bin/datcom.exe`。子智能体把标准输出和标准错误写入 `data_lib/datcom_data/{geometry_id}/{geometry_id}_datcom.log`，只盯这份进行中的日志。

算例目录使用 `data_lib/datcom_data/{geometry_id}/`。这与高保真产物放在 `data_lib/fluent_CFD_data/` 是同一种安排：程序留在自己的代码目录，结果留在 `data_lib`。脚本顶部的 `RESULT_ROOT` 已经指向 `data_lib/datcom_data`。验收通过要同时满足：几何核对报告没有未通过的自检，系数表的行数等于这次领取的工况数，每一行的马赫数和攻角都能对上一个 `condition_id`。通过后，该几何下这些工况的队列行改为已完成。每个工况的 `{run_id}_datcom.csv` 与 `{geometry_id}_for006.dat` 都留在这个子目录里。

调用时显式传入总表里的马赫数和攻角。脚本配置区里的默认马赫列表目前是 `0.80` 到 `1.00`，工作区根说明里的工况网格是 `2.00` 到 `2.20`。工作流以总表为准，不用那份默认列表代替总表。

飞行高度、舵面相位 `+`、后缘对齐和力矩参考点 `L/2` 是这次计算的冻结约定，写在算例的 `case_params.json` 快照里。它们不参与 `geometry_id`。相位或高度一旦要改，就作为新的一批几何重新编号，不覆盖已有行。

`DATCOM/scripts/04_pointnet_demo.py` 能生成点云，但它的参数名还不是总表这 11 个主尺寸。工作流点云仍按 `geometry_id` 登记到 `data_lib/datasets/point_clouds/`。把这个演示脚本接进来之前，先让它读取同一套主尺寸。

机器学习的后续实现放在已有的 `ML_training/`。`DATCOM/scripts/` 里已有的训练和画图脚本不作为这条闭环的入口。开始放训练代码时，同步更新 `ML_training` 的目录说明。

工作区里仍有脚本和忽略规则写着旧目录名 `missile_project`，包括快照、迁移、回退和 `.gitignore`。接入 DATCOM 队列之前，把这些保护路径改到 `DATCOM/`，并继续排除 `datcom_bin/`、`runs/`、`outputs/`。`datcom.exe` 不写入计划、源码或报告。

## 建议的实施顺序

1. 落地 CSV、四份阶段 JSON 和 `ml.json` 的字段，先用假数据检查去重、共用几何和状态流转。CSV 检查包括前两列的组合唯一，以及用这两列能取回同一行的 13 个参数。
2. 把 SpaceClaim 与 Fluent Meshing 的现有命令接到各自子智能体，验收标准沿用现有 `PASSED` 与 `runtimeVerified`。
3. 把 `DATCOM/scripts/01_batch_datcom.py` 接到 DATCOM 子智能体，按几何一次计算，再把系数拆成带 `run_id` 的单工况文件。
4. 求解器选定后再接通 `fluent_solution` 队列。
5. 在高低质量数据都有已完成样本后，接通多保真高斯过程和总表回写。

## 待定项

以下各项会改变实施细节。正文里的链路、队列和职责不依赖它们的具体取值。工况由总表前两列确定，不再单设 `case_id`。

| 待定项 | 需要决定的内容 |
|---|---|
| 置信度阈值 | 三个系数的不确定性超过多少时追加新行，以及每一轮最多追加多少行 |
| 补点范围 | 新的 `Ma` 与 `alpha_deg` 是否只取现有 30 个离散工况；`L_N`、`L`、`D` 的允许范围 |
| 标识符编码 | 规范小数的位数，以及 `geometry_id` 的具体字符串。位置已定：总表第 1、2 列 |
| 系数参考量 | DATCOM 侧已定为 `CL`、`CD`、`CM`，参考面积 `π(D/2)²`，参考长度 `D`，力矩点 `L/2`。求解侧要产出同一套量 |
| 飞行高度 | 整表冻结值。脚本配置区当前是 `0` m，`DATCOM/README.md` 的说明文字写 `5000` m |
| 点云接入 | `04_pointnet_demo.py` 与 11 个主尺寸对齐之后再作为点云入口；在此之前点数和文件格式仍待定 |
| 求解器 | `fluent_solution` 的可执行流程，以及它如何给出与 DATCOM 同名的 `CL`、`CD`、`CM` |
| 上报后的调整权 | 主智能体在失败待上报之后，哪些设置允许改，哪些必须请人决定 |
