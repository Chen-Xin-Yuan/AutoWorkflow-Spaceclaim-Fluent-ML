# DATCOM — 项目目录结构说明


## 一、核心工作目录（必要）

### `datcom_bin/`

DATCOM 程序本体目录，存放 `datcom.exe` 与历史输入模板 `for005.dat`。

`for005.dat` 是旧版手工算例模板，现在只作为翼型剖面（HEX）参数的参考，
`01_batch_datcom.py` 不再改写它，而是按几何/来流参数整份生成 `for005.dat`。

不要把算例结果放在这里。`01_batch_datcom.py` 把结果写到 `../data_lib/runs/low/<geometry_id>/`，运行中的协议文件放在 `../data_lib/runs/low/.work/`。

### `scripts/`

所有 Python 源码（`.py` 文件全部放这里）。

| 文件 | 用途 |
| --- | --- |
| `01_batch_datcom.py` | DATCOM 批量计算主脚本：输入 README 的 11 个主尺寸与来流条件，自动生成 `for005.dat`、调用 `datcom.exe`、解析气动系数；同时输出几何核对报告与三视图，便于确认建模是否正确 |
| `02_ml_train.py` | MLP 神经网络训练 |
| `03_plot_result.py` | 画图、误差分析、对比 DATCOM/Fluent |
| `04_pointnet_demo.py` | 导弹外形点云数据集生成（PointNet 演示，自带几何校验和三视图预览） |
| `datcom_parser.py` | 解析 `for006.dat` 的工具函数 |


### `outputs/`

程序自动生成的产物目录（可定期清理，建议通过 `.gitignore` 忽略）。

| 子目录 | 用途 |
| --- | --- |
| `inp_files/` | Python 自动生成的 `*.inp` 输入卡片 |
| `datcom_out/` | DATCOM 输出 `for006.dat` / `datcom.out` 等 |
| `dataset/` | 训练数据集（`datcom_dataset.csv`、`fluent_validate.csv`） |
| `figures/` | 输出的图片：预测对比图、误差图 |
| `pointcloud/` | 旧的点云演示目录。`04_pointnet_demo.py` 现在默认写到 `../data_lib/datasets/point_clouds/` |

`04_pointnet_demo.py` 默认把点云写到 `../data_lib/datasets/point_clouds/`。不带几何标识时文件名仍是 `missile_<N>.*`。用 `--geometry-id` 或 `--params` 读入总表的 11 个主尺寸后，主文件是 `<geometry_id>_pointcloud.ply`。点数和文件格式仍沿用脚本里的现有演示，工作流侧的最终格式还待定。

### `../data_lib/runs/low/`（运行结果，项目外）

每个 `geometry_id` 一个子文件夹，位置由脚本顶部 `RESULT_ROOT` 决定
（默认 = 工作区 `data_lib/runs/low`）。`datcom.exe` 运行时只用 `../data_lib/runs/low/.work/` 里的 `for005.dat`、`for006.dat`、`for022.dat`，算完后工作目录会删掉。

同一个 `geometry_id` 加同一个 `altitude_m` 单元格是一次调用。子文件夹内的归档名带几何标识：

| 文件 | 内容 |
| --- | --- |
| `<geometry_id>_for005.dat` | 本次实际喂给 DATCOM 的输入卡片 |
| `<geometry_id>_for006.dat` | DATCOM 原始输出 |
| `<geometry_id>_for022.dat` | DATCOM 写出的 Tecplot ASCII 几何外形（英尺） |
| `<geometry_id>_aero_coefficients.csv` | 该几何的系数总表。不同高度再次运行时保留已有高度的行 |
| `<run_id>_datcom.csv` | 从总表抽出的单工况一行。`run_id` 是 `<geometry_id>__<condition_id>__<altitude_m>` |
| `<geometry_id>_geometry_check.txt` | 几何模型核对报告 |
| `<geometry_id>_geometry_check.png` | 脚本自绘的三视图，仅供速查 |
| `<geometry_id>_case_params.json` | 本算例的几何、来流、参考量与派生量快照 |
| `<geometry_id>_datcom.log` | `datcom.exe` 的标准输出和标准错误 |

看外形的最直接方式：Tecplot 360 → *File → Load Data File(s)* → 选 `../data_lib/runs/low/<geometry_id>/<geometry_id>_for022.dat`，
会得到 9 个区（1 个 `FUSELAGE` + 前舵 4 片 `FINSET 1` + 后舵 4 片 `FINSET 2`）。
注意文件长度单位是**英尺**（DATCOM 几何输出固定用英尺，与输入卡片里的英寸无关）。

`aero_coefficients.csv` 只输出上面 3 个系数，需要别的系数（例如 `CNA`、`XCP`）时在脚本顶部
`OUTPUT_COEFFICIENTS` 里加一行即可。注意：CSV 若正被 Excel 打开，Windows 会拒绝覆盖写入，
脚本会给出警告并保留其余产物，关闭 Excel 后重跑即可。

归档目录名就是 `geometry_id`。当前工作区总表有 4 行试算，几何标识暂为 `1`，高度 `0`，工况 C01、C02、C06、C07，结果在 `../data_lib/runs/low/1/`。前一次试算的归档留在 `../data_lib/runs/low/sample/`。

### `docs/`

文档、笔记、参考资料。

| 文件或子目录 | 用途 |
| --- | --- |
| `task_note.md` | 任务进度记录 |
| `reference_papers/` | 下载的相关硕士论文 PDF |

### `.vscode/`

VS Code 编辑器配置文件（`settings.json` 等）。属于 IDE 工具配置，建议保留。

### `.claude/`

Claude Code 配置文件。属于 IDE 工具配置，建议保留。

### `AGENTS.md`

项目级 Agent 说明文档（如有使用 AI 编程助手）。

## 二、路径约定

整个项目文件夹可整体移动。

- 所有脚本 / 代码均不写死绝对路径，项目根目录由代码自动推导：
  - `scripts/` 下脚本用 `__file__` 推导（项目根 = `scripts/` 的上一级）。
  - `notebook/` 无 `__file__`，用“从当前目录向上查找含 `datcom_bin` 的目录”推导。
- 因此整个 `DATCOM` 文件夹可整体移动 / 改名到任意位置，无需修改任何路径配置；直接运行 `scripts/01_batch_datcom.py` 即可。
- 新增代码务必遵守同一约定：不要出现 `C:\Users\...` 这类写死的路径。

## 三、使用建议

### 1. 气动计算工作流

`01_batch_datcom.py` 的入口参数就是工作区 README「参数化方式与来流条件」里的那套量：

1. 正式输入是工作区的 `data_lib/inputs/cases.csv`。每一行是一个工况，列依次为
   `geometry_id`、`condition_id`、11 个主尺寸、`Ma`、`alpha_deg`、`altitude_m`。同一个
   `geometry_id` 的几何尺寸必须相同。同一个 `geometry_id` 和同一个 `altitude_m`
   单元格的来流必须是这些马赫数和攻角的完整组合。
2. 直接运行 `python scripts/01_batch_datcom.py` 就会读取这张表。脚本顶部的
   `MISSILE_GEOMETRY`、`MACH_LIST`、`ALPHA_LIST` 和 `CASES` 只在 `--demo` 时使用。
3. 飞行高度来自每一行的 `altitude_m`。不同高度分次调用。
4. 运行后把结果写到 `../data_lib/runs/low/<geometry_id>/`。归档名以 `geometry_id` 开头；
   每个工况另有 `<run_id>_datcom.csv`。

几条实测到的模型约定（改输入卡片前务必知道）：

- 本机 DATCOM 的 `$FINSET` **`SWEEP` 以后缘为基准**：梢部后缘 = 根部后缘 +
  展长 × tan(SWEEP)，梢弦长度不变。所以 README 的“后缘对齐”几何对应 `SWEEP = 0`，
  而不是 `atan((C_root - C_tip) / 展长)`；脚本已按此自动换算（前缘对齐 / 对称写负角度）。
- 数字卡片必须带小数点（`ALPHA = 4` 会报语法错）；参考长度变量名是 `LREF`（`CBAR` 会报未知变量）。
- 数字统一保留 5 位小数：本机 datcom.exe 对过长的数字串会在内部几何计算里报
  `sqrt DOMAIN` 且完全不输出结果。
- 第二个舵面组 `STA` 写 2.0 会让本机 datcom 直接终止，脚本固定写 1.0。

常用命令：

```powershell
python scripts/01_batch_datcom.py --list          # 只看输入总表里的算例与工况
python scripts/01_batch_datcom.py --dry-run       # 按总表生成 for005.dat 与几何核对图，不调用 DATCOM
python scripts/01_batch_datcom.py                 # 读取 data_lib/inputs/cases.csv 并计算
python scripts/01_batch_datcom.py --demo          # 用脚本顶部的演示几何和来流，不读总表
python scripts/01_batch_datcom.py --demo --mach 2.0 2.2 --alpha 4.0 6.0 --alt 5000
```

`--params`、`--mach`、`--alpha`、`--alt` 只在 `--demo` 下有效。正式计算改总表，不改命令行。

### 2. 批量扩展

- 多组几何对比：在 `data_lib/inputs/cases.csv` 里为新几何另起一个 `geometry_id`，并写全它的来流组合。
- 演示单组几何：加 `--demo`，此时才使用脚本顶部的 `CASES`。
- 一个 `geometry_id` 加一个 `altitude_m` = 一次 DATCOM 调用，结果落在 `../data_lib/runs/low/<geometry_id>/`。

### 3. Git 管理

结果现在写到项目外的 `../data_lib/runs/low/`，由上级工作区 `.gitignore` 忽略运行产物。点云写到 `../data_lib/datasets/point_clouds/`，点云文件同样不入库。

`outputs/`、`__pycache__/` 等产物同样不入库（由上级 `.gitignore` 统一覆盖）。

---

最后更新：2026-10-23
