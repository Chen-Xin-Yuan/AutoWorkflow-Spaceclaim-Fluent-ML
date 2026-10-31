# AGENTS.md — DATCOM 协作规范

给所有 AI 编码代理（Claude Code / DSH / Codex等）修改本项目代码时的行为准则，动代码前先通读。

## 布局速览
- `scripts/`：Python 代码。几何身份是 **16 个参数**：11 个主尺寸、翼型种类（`hex` / `naca`）、按种类二选一的三个翼型参数、鼻锥头型（`cone` / `ogive`）。数据库 `data_lib/datasets/SQLite/missile.db` 是唯一真相源；`db.py export --format cases-csv` 会导出成 DATCOM 认识的 25 列表，用 `01_batch_datcom.py --input <csv>` 喂进去。同一个 `geometry_id` 加同一个 `altitude_m` 调用一次 DATCOM，**一次调用里可以有六边形和 NACA 两种截面、圆锥和卵形两种头型混跑**。`04_pointnet_demo.py` 用 `--geometry-id` **从库里读全部 16 个参数**（11 个主尺寸 + 翼型种类与三项参数 + 头型，见 `main_dimensions_from_db()`），翼型与头型不再取 `missile_spec.py` 的默认值；不再有独立的 `fin_section.py`。`--params` 只给 11 个主尺寸时，翼型与头型才回落到该模块的默认值。点云 `*_meta.json` 仍只记录主尺寸与头型，不记翼型参数。
- `missile_spec.py` 是**默认值与解析式**的来源，不再是唯一配置区：前部配置区仍然集中头型、翼型两套参数、舵面布局与基准几何，`make_fin_section()` / `resolve_nose_shape()` 让调用方按行覆盖；`01` 与 `04` 共用这一份。不要再拆出第二个参数文件（原来的 `fin_section.py` 已并入）。
- 翼型与头型的**一手记录在 `run.json`**（每批一份，`../data_lib/runs/low/<geometry_id>/<run_id>/run.json`，含 `shape.fin_section` 与 `shape.nose_shape`），审核据此比对库内参数。系数表、`case_params.json`、`geometry_check.txt`、点云 `*_meta.json` **仍不记翼型**——要加回这四个既有产物，先跟用户确认。`datcom_spec.py` 只管 DATCOM 协议、列名与参数校验（含可选的 `SHAPE_COLUMNS`），不要往里塞外形数据。
- `datcom_bin/`（datcom.exe、for005.dat 历史模板、1_for005.dat）**入 git**，属于本仓库要保留的程序与模板；其中的 macOS 元数据 `._*` 不入库。DATCOM 算例结果写在项目外的 `../data_lib/runs/low/<geometry_id>/`（位置由脚本顶部 `RESULT_ROOT` 决定），点云默认写到 `../data_lib/datasets/point_clouds/<geometry_id>/`（一个几何一个子目录，目录名就是几何标识；不带标识的演示点云不建子目录，直接落在根目录下）。项目目录里不再保留旧结果目录。
- **参数以代码为准**：README 里的参数值永远服从当前代码（本仓库脚本前部的配置区 / schema 默认值 / 数据库里的行），不是反过来。文档与代码不一致时一律改文档，只有用户明确要求时才改参数值。优先级：数据库行 > 脚本配置区 / schema 默认值 > 文档文字。参数表由 `../scripts/docs.py --write` 生成，提交前 `--check` 必须通过；历史运行记录不回填。
- 版本管理：当前 `DATCOM/` 目录没有独立 Git 仓库；需要提交时，先返回上一级目录，使用上一级 Git 仓库。源码/配置改动按上一级仓库的跟踪范围提交；产物目录不提交。

## 路径约定：禁止写死绝对路径（项目文件夹可整体移动）
- 项目根目录一律由代码自行推导，任何代码（脚本、notebook）都不得硬编码
  绝对路径（如 `C:\Users\...`）；整个 DATCOM 文件夹可整体移动 /
  改名到任意位置，移动后无需改任何路径即可运行。
- `scripts/` 下的脚本用
  `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` 得到项目根
  （脚本在 scripts/ 内，其上一级即项目根）。
- 将来若加 `notebook/` 这类无 `__file__` 的运行环境，用"从当前目录向上查找含
  datcom_bin 的目录"推导项目根。
- 改代码 / 新增脚本时同样遵守本约定，不要重新引入写死的路径。

## 铁律 1：改码必自测（通过才算完成）
- `python -m py_compile <文件>.py` 无报错；
- 实际运行入口冒烟/回归：改 `01_batch_datcom.py` 须真跑通 ≥1 算例（datcom.exe 正常调用、系数记录数符合预期、产物落 `../data_lib/runs/low/<geometry_id>/`）；
- 环境无法验证时须明说"未验证"，不得声称完成。

## 铁律 2：改完必 commit
- 逻辑完整、自测通过后，先返回上一级目录，再提交到上一级 Git 仓库，一次一主题；
- 用 `git rev-parse --show-toplevel` 确认仓库根目录，使用该仓库的当前分支，不再假定为 `master`，不要在当前项目目录初始化独立仓库；
- message：`<type>: <中文简述>`，type ∈ feat/fix/refactor/docs/chore；
- 流程（从当前项目目录开始）：`cd ..` → `git status` → `git add -- <相对上一级仓库的文件路径>` → `git commit`；
- 上一级仓库的根 `.gitignore` 第十节整体放行 `DATCOM/`（`!/DATCOM/`、`!/DATCOM/**`），因此 `DATCOM/` 下的源码、文档、`datcom_bin/` 程序与模板默认都在跟踪范围内，**不需要** `git add -f`。放行之后又按路径重新排除的有：`__pycache__/`、`*.py[cod]`、`.pytest_cache/`、`.vscode/settings.json`（含本机绝对路径）、`._*`。这些文件即使被误提交也应当撤出跟踪；
- 只提交本次改动涉及的文件，保留已有暂存和未提交内容；
- 噪音（__pycache__/.vscode 等）与密钥（.claude 配置中的 token）严禁入库。

## 其它
- 保持中文注释与中文 commit；
- 本文件修改后，也须先返回上一级目录，按同样规范提交到上一级 Git 仓库（`docs: ...`）。
