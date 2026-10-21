# Missile Project — 项目目录结构说明

项目根目录：`missile_project/`。用 VS Code / Jupyter 打开此目录即可。

## 一、核心工作目录（必要）

### `datcom_bin/`

DATCOM 程序本体目录，存放 `datcom.exe` 与历史输入模板 `for005.dat`。

`for005.dat` 是旧版手工算例模板，现在只作为翼型剖面（HEX）参数的参考，
`01_batch_datcom.py` 不再改写它，而是按几何/来流参数整份生成 `for005.dat`。

不要把算例结果放在这里，程序输出请放到 `runs/` 或 `outputs/` 下。

### `scripts/`

所有 Python 源码（`.py` 文件全部放这里）。

| 文件 | 用途 |
| --- | --- |
| `01_batch_datcom.py` | DATCOM 批量计算主脚本：输入 README 的 11 个主尺寸与来流条件，自动生成 `for005.dat`、调用 `datcom.exe`、解析气动系数 |
| `02_ml_train.py` | MLP 神经网络训练 |
| `03_plot_result.py` | 画图、误差分析、对比 DATCOM/Fluent |
| `04_pointnet_demo.py` | 导弹外形点云数据集生成（PointNet 演示，自带几何校验和三视图预览） |
| `datcom_parser.py` | 解析 `for006.dat` 的工具函数 |

### `notebook/`

Jupyter 交互式调试笔记本（`.ipynb`）。

- `demo.ipynb`：单组工况调试、测试解析函数。
- 其他 `.ipynb` 可按任务自由添加。

### `outputs/`

程序自动生成的产物目录（可定期清理，建议通过 `.gitignore` 忽略）。

| 子目录 | 用途 |
| --- | --- |
| `inp_files/` | Python 自动生成的 `*.inp` 输入卡片 |
| `datcom_out/` | DATCOM 输出 `for006.dat` / `datcom.out` 等 |
| `dataset/` | 训练数据集（`datcom_dataset.csv`、`fluent_validate.csv`） |
| `figures/` | 输出的图片：预测对比图、误差图 |
| `pointcloud/` | `04_pointnet_demo.py` 生成的导弹点云数据集 |

`pointcloud/` 包含 `missile_8192.ply/xyz`、部件标签、meta、参考网格 obj，以及预览图 `preview_missile.png`；体积小、可复现，已在 `.gitignore` 中单独放行入库。

### `runs/`

每个 DATCOM 算例的独立子文件夹。

子文件夹内自动生成：

| 文件 | 内容 |
| --- | --- |
| `for005.dat` | 本次实际喂给 DATCOM 的输入卡片（可复查、可直接复现） |
| `for006.dat` | DATCOM 原始输出 |
| `aero_coefficients.csv` | 解析后的气动系数：`MACH`、`ALPHA` + `CN`/`CM`/`CA`/`CL`/`CD`/`CNA`/`CMA`/`CNQ`/… 各表合并成一行一个工况 |
| `case_params.json` | 本算例的几何、来流、参考量与派生量快照 |

建议命名方式：`readme_baseline` / `longer_nose` 等，便于区分（算例名由脚本配置决定）。

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
- 因此整个 `missile_project` 文件夹可整体移动 / 改名到任意位置，无需修改任何路径配置；直接运行 `scripts/01_batch_datcom.py` 即可。
- 新增代码务必遵守同一约定：不要出现 `C:\Users\...` 这类写死的路径。

## 三、使用建议

### 1. 气动计算工作流

`01_batch_datcom.py` 的入口参数就是工作区 README「参数化方式与来流条件」里的那套量：

1. 几何：脚本顶部 `MISSILE_GEOMETRY` 的 11 个主尺寸（m，字段名与建模程序一致）：
   `L_N` `L` `D` `L_W` `C_RW` `C_TW` `B_W` `L_F` `C_RF` `C_TF` `B_F`。
2. 来流：`MACH_LIST` / `ALPHA_LIST` / `ALTITUDE_M`，默认即 README 的 6 个马赫 ×
   5 个攻角 = 30 个工况（高度按英尺写入 DATCOM）。
3. 运行 `python scripts/01_batch_datcom.py`，脚本自动生成 `for005.dat`、
   调用 `datcom_bin/datcom.exe`，把结果写到 `runs/<算例名>/` 并解析成
   `aero_coefficients.csv`。

常用命令：

```powershell
python scripts/01_batch_datcom.py --list          # 只看算例与工况组合
python scripts/01_batch_datcom.py --dry-run       # 只生成 for005.dat，不调用 DATCOM
python scripts/01_batch_datcom.py                 # 跑顶部 CASES 的算例
python scripts/01_batch_datcom.py --mach 2.0 2.2 --alpha 4.0 6.0 --alt 5000
python scripts/01_batch_datcom.py --params D:\...\missile_params_example.json
```

`--params` 吃的是 11 个主尺寸的 JSON（单位 m，必须齐全），每个文件跑成一个算例。

### 2. 批量扩展

- 多组几何对比：在 `CASES` 里加一项，`"geometry"` 只写要改的参数，其余沿用基准值；
- 不同来流：算例里可用 `"mach"` / `"alpha"` / `"alt"` 单独覆盖全局来流条件；
- 一个算例 = 一次 DATCOM 调用 = 一个 `runs/<算例名>/` 目录，互不干扰。

### 3. Git 管理

建议根目录加入 `.gitignore`，忽略以下内容：

```gitignore
outputs/*
runs/*
__pycache__/
.ipynb_checkpoints/
.playwright-mcp/
*.pyc
```

这样只有源码、模板、文档纳入版本控制，产物不入库。

---

最后更新：2026-10-21
