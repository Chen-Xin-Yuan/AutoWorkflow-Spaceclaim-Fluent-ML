# SQLite 数据库

全工作区的**唯一真相源**。几何、工况、任务状态、审核凭证、上报单、启停开关、训练轮次
全部存在这里；`data_lib/queues/` 下的两个队列目录只是本库的导出视图，可以随时整目录删掉
重建。

| 下级 | 类型 | 作用 |
|---|---|---|
| `missile.db` | 生成 | 库文件，入库。11 张表，见下 |
| `missile.db-wal` | 生成 | WAL 日志。运行期临时物，**不入库**，不要手工删除 |
| `missile.db-shm` | 生成 | WAL 共享内存索引。同上 |
| `README.md` | 文件 | 本文件 |

## 建库与自检

```powershell
python scripts/db.py init      # 建表（幂等），schema_version 写进 meta 表
python scripts/db.py check     # 自检：PRAGMA、表、索引、规范化、CHECK 约束
```

**所有写操作只经 `scripts/db.py`**。脚本、子智能体、主智能体都不要直接开 `sqlite3`
连接写库——会撞 `database is locked`，也会留下写了一半的事务。

## 表

| 表 | 作用 |
|---|---|
| `meta` | 键值对，当前只存 `schema_version` |
| `geometry` | 16 个几何参数（11 主尺寸 ＋ 翼型种类 ＋ 翼型三参数 ＋ 头型）、规范串、标签 |
| `geometry_state` | 每几何的建模/网格状态、三条产物路径、`deletable` 标志 |
| `condition` | `mach` / `alpha_deg` / `altitude_m`、规范串、`C01`–`C30` 标签 |
| `cfd_task` | 高保真任务（几何 × 工况）：状态、重试次数、三个气动系数 |
| `datcom_task` | 低保真任务（几何 × 工况）：同上 |
| `review` | 每次审核的判据明细与结论，**唯一的入库凭证** |
| `report` | 上报主智能体的问题单 |
| `control` | 启停开关（`running` / `paused` / `stopped`） |
| `task_process` | 运行中任务的 `pid`，供叫停时终止进程树 |
| `training_round` | 训练轮次、数据版本、模型路径、提出了哪些新行 |

## 约定

- 库内状态一律用英文码：`pending` `queued` `running` `reviewing` `rejected` `passed`
  `cancelled`；中文只出现在文档里。
- 库内时间一律 UTC、文本 `YYYY-MM-DDTHH:MM:SSZ`，可直接字符串排序。
- 库内路径一律工作区相对路径（正斜杠），换机器、整体挪目录都不失效。
- `geometry_id` / `condition_id` 是内容哈希，**不是**自增序号；改参数就换身份，
  所以任何"改几何"都是追加新行，不改旧行。
- `schema_version` 变化必须配迁移脚本；`scripts/db.py init` 见到版本不一致会直接拒绝。

## 身份示例

16 个参数按固定顺序拼成规范串，再取 `sha256` 前 12 位（前缀 `g` / `c`）。下面是
`scripts/db.py check` 逐字比对的冻结样例：

```json
{"L_N":0.49,"L":3.69,"D":0.18,"L_W":1.72,"C_RW":0.23,"C_TW":0.06,"B_W":0.23,
 "L_F":3.2,"C_RF":0.38,"C_TF":0.19,"B_F":0.22,
 "fin_section_type":"hex","thickness_percent":4.462,"flat_percent":60.995,"le_radius":0.05,
 "nose_shape":"cone"}
```

```text
geometry_id = g692bcb5d5e50
canonical   = L_N=0.490000|L=3.690000|…|fin_section_type=hex|thickness_percent=4.462000|flat_percent=60.995000|le_radius=0.050000|nose_shape=cone
```

工况 `{"mach":2.0,"alpha_deg":4.0,"altitude_m":0.0}` → `cc9633c86a360`。

Windows 的命令行会吃掉内联 JSON 里的双引号，所以调试时优先从文件读：

```powershell
python scripts/db.py identify --geometry-file params.json
```

## 备份

库是单文件，干净备份用 `VACUUM INTO`（会自动合并 WAL，不用停服务）：

```powershell
python -c "import sqlite3;sqlite3.connect('data_lib/datasets/SQLite/missile.db').execute(\"VACUUM INTO 'data_lib/datasets/SQLite/missile.backup.db'\")"
```

直接复制 `missile.db` 会漏掉 `-wal` 里已提交的内容，只在库确认没有写者时才安全。
