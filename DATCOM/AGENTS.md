# AGENTS.md — DATCOM 协作规范

给所有 AI 编码代理（Claude Code / DSH 等）修改本项目代码时的行为准则，动代码前先通读。

## 布局速览
- `scripts/`：Python 代码。主脚本 `01_batch_datcom.py`（DATCOM 批量运行），另有 02/03/datcom_parser 占位模块。
- `datcom_bin/`（datcom.exe、for005.dat 模板）、`runs/`（DATCOM 运行结果，每算例一子目录）、`outputs/` `docs/` `fluent_case/` `notebook/` 等：数据/产物，**不入 git**。
- 版本管理：当前 `DATCOM/` 目录没有独立 Git 仓库；需要提交时，先返回上一级目录，使用上一级 Git 仓库。源码/配置改动按上一级仓库的跟踪范围提交；产物目录不提交。

## 路径约定：禁止写死绝对路径（项目文件夹可整体移动）
- 项目根目录一律由代码自行推导，任何代码（脚本、notebook）都不得硬编码
  绝对路径（如 `C:\Users\...`）；整个 DATCOM 文件夹可整体移动 /
  改名到任意位置，移动后无需改任何路径即可运行。
- `scripts/` 下的脚本用
  `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` 得到项目根
  （脚本在 scripts/ 内，其上一级即项目根）。
- `notebook/` 等无 `__file__` 的运行环境，用"从当前目录向上查找含
  datcom_bin 的目录"推导项目根。
- 改代码 / 新增脚本时同样遵守本约定，不要重新引入写死的路径。

## 铁律 1：改码必自测（通过才算完成）
- `python -m py_compile <文件>.py` 无报错；
- 实际运行入口冒烟/回归：改 `01_batch_datcom.py` 须真跑通 ≥1 算例（datcom.exe 正常调用、系数记录数符合预期、产物落 runs\<算例名>\）；
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
