# 多智能体协作（第 1 课）

三个进程：Geometry Agent、Condition Agent，以及一个命令行 host。host 不是第四个服务。本课只读取 `cases.csv`，不启动 ANSYS 或 DATCOM，也不写总表、不跑 `scripts/run.ps1`。

几何尺寸来自 `workspace.json` 的 `inputRoot` 所指向的 `cases.csv`（当前是 `data_lib/inputs/cases.csv`），只读 11 个主尺寸，单位是米。工况编号 C01–C30 的马赫数和攻角用仓库根 README 的来流网格：马赫数 `{2.00 … 2.20}` 为外层，攻角 `{4.0 … 6.0}` 为内层。总表只用来说明这一行在不在。

## 直接下级

| 下级 | 类型 | 作用 |
|---|---|---|
| `geometry_agent/` | 目录 | Geometry Agent。`__main__.py` 是进程入口，`agent_executor.py` 按 `geometry_id` 返回 11 个主尺寸 |
| `condition_agent/` | 目录 | Condition Agent。`__main__.py` 是进程入口，`agent_executor.py` 换算 C01–C30 并核对总表 |
| `host/` | 目录 | `host.py`：发现两张 Agent Card，按句子提问 |
| `requirements.txt` | 文件 | 依赖声明，固定 `a2a-sdk==1.1.0`，另有 uvicorn、httpx、sse-starlette |
| `.venv/` | 依赖 | 本课隔离虚拟环境，**不纳入版本管理，不要手工编辑**。根 `.gitignore` 已忽略 `.venv/` |

## 端口

- Geometry Agent：`127.0.0.1:10011`，Agent Card 名称 `Geometry Agent`
- Condition Agent：`127.0.0.1:10012`，Agent Card 名称 `Condition Agent`

只绑定 `127.0.0.1`。

## 虚拟环境

使用 Python 3.10 及以上，不要用 `fluent_meshing/_deps` 或网格检查环境。在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 三条命令

先启动两个 Agent，再运行 host。工作目录是本目录：

```powershell
.\.venv\Scripts\python.exe .\geometry_agent\__main__.py
.\.venv\Scripts\python.exe .\condition_agent\__main__.py
.\.venv\Scripts\python.exe .\host\host.py
```

不带参数时，host 使用这句演示问题：

`geometry_id 1 的机身直径是多少，C07 的马赭数和攻角是多少？`

也可以把同一句放在参数里。句子里同时有 `geometry_id` 和 `C07` 时，host 会问两个 Agent。一个 Agent 没启动时，host 报告它 is down，并继续问另一个。
