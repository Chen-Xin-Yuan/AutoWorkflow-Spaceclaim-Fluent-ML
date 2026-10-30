# AGENTS.md — DATCOM 协作规范

给所有 AI 编码代理（Claude Code / DSH / Codex等）修改本项目代码时的行为准则，动代码前先通读。

## 布局速览
- `scripts/`：Python 代码。几何参数一共 14 个：`data_lib/inputs/cases.csv` 里的 11 个主尺寸，加上前舵和后舵共用的 3 个翼型参数，翼型可以选择naca或者六边形，通过一个对象（包括一个类别和三个参数）来进行切换。`01_batch_datcom.py` 读取总表，同一个 `geometry_id` 加同一个 `altitude_m` 调用一次 DATCOM。`04_pointnet_demo.py` 用 `--geometry-id` 或 `--params` 读取 11 个主尺寸，翼型与批量计算共用这一份，并写点云。
- 不进输入总表的外形数据（鼻锥头型 `NOSE_SHAPE = "ogive" / "cone"` 即 DATCOM 的 `TNOSE`、锥顶倒角、舵面翼型 `SECTION_TYPE = "HEX" / "NACA"` 与两套三个参数、舵面布局 `LE_MODE` / `FIN_PHASE` / `CRUCIFORM_COUNT`、基准几何）以及鼻锥母线 / 舵面翼型的外形方程，全部在 `scripts/missile_spec.py`：前部是唯一配置区，后面是解析式。`01` 与 `04` 共用这一份，改一处两边同时生效；不要再拆出第二个参数文件（原来的 `fin_section.py` 已并入）。这些数据目前**也不写进产物**（系数表、`case_params.json`、`geometry_check.txt`、点云 `*_meta.json` 都不记翼型），只在运行时打到控制台；要加回产物先跟用户确认。`datcom_spec.py` 只管 DATCOM 协议与参数校验，不要往里塞外形数据。
- `datcom_bin/`（datcom.exe、for005.dat 模板）**不入 git**；DATCOM 算例结果写在项目外的 `../data_lib/runs/low/<geometry_id>/`（位置由脚本顶部 `RESULT_ROOT` 决定），点云默认写到 `../data_lib/datasets/point_clouds/`。项目目录里不再保留旧结果目录。
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
- 上一级仓库当前通过 `.gitignore` 忽略整个 `DATCOM/`。返回上一级目录不会自动解除忽略；只有明确纳入本次提交范围的具体文件才可用 `git add -f -- <文件路径>` 添加，禁止强制添加整个目录；
- 只提交本次改动涉及的文件，保留已有暂存和未提交内容；
- 噪音（__pycache__/.vscode 等）与密钥（.claude 配置中的 token）严禁入库。

## 其它
- 保持中文注释与中文 commit；
- 本文件修改后，也须先返回上一级目录，按同样规范提交到上一级 Git 仓库（`docs: ...`）。
