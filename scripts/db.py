# -*- coding: utf-8 -*-
"""SQLite 唯一真相源：建表、参数规范化、身份生成，以及之后所有库操作的唯一入口。

任何脚本、子智能体、主智能体都通过 `python scripts/db.py <命令>` 改库，**不直接开
sqlite3 连接写**，避免 `database is locked` 与"写了一半"的事务。

本文件提供 `init`、`check`、`identify`、`import`、`export`、`centroid`、`record-review`、
`report`、`control`、`claim`、`gc` 等命令。`watch.py` / `control.py` / `dispatch.py` 不自己
写 SQL，而是 import 本模块第 10 节的函数，所以"所有写操作只经 db.py"在这一层成立。

状态机只有三态（`TASK_STATUSES`）：`pending`（待处理，含待审核）、`repairing`（审核
不通过，修理中）、`queued`（审核通过，可被下一流程领取）。"谁正在跑"不靠状态词，靠
`claimed_at` + `task_process` 的 pid 登记：领取是 `UPDATE ... WHERE claimed_at IS NULL`
的原子写，`rowcount` 判成功，所以同一个任务不会被两个进程同时拿走。网格按
`(geometry_id, condition_id)` 建行（`mesh_task`），同一几何不同工况各画各的网格。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    库路径      命令行 `--database` > 环境变量 `MISSILE_DB` >
                `workspace.json` 的 `database` 字段 > `DEFAULT_DATABASE_REL`。
    工作区根    环境变量 `MISSILE_WORKSPACE` > 本文件所在目录的上一级。
                所有相对路径都以工作区根为基准；本文件不出现任何机器绝对路径。
    几何校验    `DATCOM/scripts/datcom_spec.normalize_geometry`：11 个主尺寸的自洽
                校验与梢弦钳位。**钳位必须发生在哈希之前**，否则 0.0005 和 0.001 会
                变成两个身份、却是同一个外形。
    翼型/头型   调用方必须显式给出（数据库是唯一真相）。缺列时由 CSV 导入层回落到
                `DATCOM/scripts/missile_spec.py` 的默认值，本模块不维护第二份默认值。
    实测质心    SpaceClaim 建模过审时由 `review.py` 给出（模型坐标减 `nose_tip_x`
                换算到弹尖基准），经 `record_review` → `set_geometry_centroid` 写入
                `geometry.cg_x_m` / `cg_y_m` / `cg_z_m`。这三列是派生量，
                **不参与身份哈希**，可以为空。

输出
    `data_lib/datasets/SQLite/missile.db`，WAL 附属文件 `-wal` / `-shm` 不入库。
    `init` 只建表、跑已知迁移并写入 `meta.schema_version`；`check` 只读，不改库。

参数配置（要改数值、字段顺序或路径只改这一节）
    库文件名、表结构与索引、schema 版本与迁移、规范化字段顺序、枚举与大写规则、
    数值范围、实测质心的校验容差、时间戳格式、浮点位数、ID 长度与前缀。

覆盖优先级与时间口径
    命令行 > 环境变量 > `workspace.json` > 本节默认值。
    库内所有时间字段与 `run_id` 的时间戳一律 UTC、文本格式 `YYYYMMDDTHHMMSSZ`，
    不用本地时区、不存 epoch、不存毫秒。
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# 1. 参数配置区
# =============================================================================
# 脚本根路径必须先定义，后面的相对路径全部挂在它下面。
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = Path(os.environ.get("MISSILE_WORKSPACE") or SCRIPT_DIR.parent).resolve()

WORKSPACE_FILE_NAME = "workspace.json"
DATABASE_ENV = "MISSILE_DB"
DEFAULT_DATABASE_REL = "data_lib/datasets/SQLite/missile.db"

# 几何校验与钳位复用 DATCOM 那一份纯函数模块（无第三方依赖、无文件读写）。
DATCOM_SCRIPTS_DIR = WORKSPACE_ROOT / "DATCOM" / "scripts"

# ---- 库连接参数 ----
SCHEMA_VERSION = "4"
JOURNAL_MODE = "WAL"          # 允许读写并发，`-wal` / `-shm` 不入版本库
BUSY_TIMEOUT_MS = 5000        # 等锁上限，超时即报错，不重试到天荒地老
FOREIGN_KEYS = True
SYNCHRONOUS = "NORMAL"        # WAL 下的推荐值，掉电最多丢最后一个事务

# ---- 时间 ----
TIMEZONE = timezone.utc
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
SQL_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# ---- 身份串 ----
FLOAT_FORMAT = "%.6f"
DECIMALS = 6                  # 规范化后的存库精度 = 哈希精度
ID_HEX_LEN = 12               # 48 bit；1 万个几何的碰撞概率约 1e-8
ID_PREFIX = {"geometry": "g", "condition": "c", "review": "r", "report": "p"}
SHARED_CONDITION_ID = "shared"  # 几何级产物（建模/网格/DATCOM 批次）的 run_id 中段
RUN_ID_SEPARATOR = "__"

# ---- 几何：16 个参数，顺序即规范串顺序，不可调换 ----
GEOMETRY_LENGTH_KEYS = (
    "L_N", "L", "D",
    "L_W", "C_RW", "C_TW", "B_W",
    "L_F", "C_RF", "C_TF", "B_F",
)
FIN_SECTION_KEY = "fin_section_type"
NOSE_SHAPE_KEY = "nose_shape"
FIN_SECTION_TYPES = ("hex", "naca")
# 库内用短名（hex / naca）；建模程序（SpaceClaim）把六边形叫 hexagon。
# 这个映射只在这里定义一次，dispatch.py 传参和 review.py 比对都引用它。
SECTION_TO_TOOL = {"hex": "hexagon", "naca": "naca"}
NOSE_SHAPES = ("ogive", "cone")

# ---- 实测质心（SpaceClaim 建模过审时写回；派生量，不进规范串、不参与身份哈希）----
# 坐标口径与 DATCOM 的 XCG、点云的 x 一致：x 从弹尖（x = 0）向尾部量，单位 m；y / z 是侧向分量。
GEOMETRY_CENTROID_KEYS = ("cg_x_m", "cg_y_m", "cg_z_m")
# 侧向分量容差（m）：十字舵关于弹轴对称，实测 |y|、|z| 应远小于 1 mm，超了就是模型摆错了。
CENTROID_SYMMETRY_TOLERANCE_M = 1e-3

# 翼型参数按类型二选一；整数参数单独列出，规范串里按整数写（不带小数点）
FIN_PARAM_KEYS = {
    "hex": ("thickness_percent", "flat_percent", "le_radius"),
    "naca": ("thickness_percent", "le_radius_index", "max_thickness_tenths"),
}
FIN_INT_PARAM_KEYS = ("le_radius_index", "max_thickness_tenths")
FIN_PARAM_BOUNDS = {          # (下限, 上限)；上限 None = 不设上界
    "thickness_percent": (0.0, 40.0),
    "flat_percent": (0.0, 100.0),
    "le_radius": (0.0, None),
    "le_radius_index": (0, 9),
    "max_thickness_tenths": None,
}
NACA_MAX_THICKNESS_TENTHS_CHOICES = (2, 3, 4, 5, 6)

# ---- 工况：3 个参数 ----
CONDITION_KEYS = ("mach", "alpha_deg", "altitude_m")

# ---- 两份输入表（人工接口：只在初始化导入时读，之后只改库）----
# 表头固定 23 列，按下面的顺序；`geometry_id` / `condition_id` 不存在于输入表，
# 它们由参数算出来。Excel 友好：写入用 BOM + CRLF，读入按 utf-8-sig 自动吃 BOM。
INPUT_TABLES = {
    "high": "data_lib/inputs/high_fidelity.csv",
    "low": "data_lib/inputs/low_fidelity.csv",
}
INPUT_TABLE_TASK = {"high": "cfd_task", "low": "datcom_task"}
CSV_FIN_COLUMNS = (
    "fin_section_type", "thickness_percent", "flat_percent", "le_radius",
    "le_radius_index", "max_thickness_tenths",
)
CSV_COLUMNS = (  # 输入表表头（23 列）
    *GEOMETRY_LENGTH_KEYS,
    *CSV_FIN_COLUMNS,
    NOSE_SHAPE_KEY,
    *CONDITION_KEYS,
    "origin",
    "label",
)
CSV_ENCODING = "utf-8-sig"
CSV_NEWLINE = "\r\n"
ORIGIN_CHOICES = ("human", "active_learning")
DEFAULT_ORIGIN = "human"

# `db export --format cases-csv` 的表头：比输入表多两个只读标识列，按 DATCOM 批量调用
# （同一 geometry_id + 同一 altitude_m 算一次）的顺序排列。
# 来流列名用 DATCOM 认的 `Ma`，这样导出文件可以直接喂给 `01_batch_datcom.py --input`。
EXPORT_CASES_HEADER = (
    "geometry_id",
    *GEOMETRY_LENGTH_KEYS,
    *CSV_FIN_COLUMNS,
    NOSE_SHAPE_KEY,
    "Ma", "alpha_deg", "altitude_m",
    "condition_id",
    "label",
)

# ---- 启停目标 ----
WORKFLOWS = ("high", "low")
STAGES = ("spaceclaim", "fluent_meshing", "fluent_solution", "datcom")
CONTROL_STATES = ("running", "paused", "stopped")

# ---- 审核与重试 ----
# 自修重试预算：同一任务连续第 3 次仍不过就写上报单并停手（预算按"每次上报前"计）。
SELF_REPAIR_BUDGET = 2
REVIEW_KINDS = ("model", "mesh", "solution", "datcom")
# 通过的阶段写到哪个队列目录（指针文件，不是产物本体）
QUEUE_FOR_REVIEW_KIND = {"model": "scdoc_for_fluent", "mesh": "meshing_for_solution"}
QUEUE_POINTER_SCHEMA_VERSION = 1

# ---- 三态状态词表（四阶段共用，库内存英文码，中文只出现在文档里）----
# pending    待处理，含"脚本跑完等审核"；审核不通过 → repairing
# repairing  审核不通过，修理中（子智能体修完再送审）；复审通过 → queued
# queued     审核通过，可被下一流程领取；领取靠 claimed_at + task_process
TASK_STATUSES = ("pending", "repairing", "queued")
# "谁在跑"不再单独设状态词：claimed_at 非空 = 已被某个进程领走。
CLAIMED_COLUMNS = ("claimed_at", "run_id")

# ---- 修理超时（watch.py 用）----
# repairing 行超过这个秒数还没进入 queued，watch 写一条 open 的 report 上报主智能体。
REPAIR_TIMEOUT_SECONDS = 1800
# 领取标记的孤儿超时：`claimed_at` 挂着、但 `task_process` 里没有对应 pid 登记，
# 且已经过了这么久 → 视作"进程在登记 pid 之前就崩了"，回收成 repairing。
# 正常路径下 spawn 到登记 pid 之间只有一两秒，300 秒足够宽松。
CLAIM_ORPHAN_SECONDS = 300

# ---- 网格任务（mesh_task，按几何 + 工况）----
# 网格必须按工况分开：边界层首层高度与层数由来流马赫数、高度决定，同几何不同工况
# 的 y+ 目标算出的首层高度不同，共用一个网格就有一个工况落在 y+ 之外。
MESH_TASK_TABLE = "mesh_task"
# 2→3 迁移复制过来的网格行**一律 `pending`**（见 `_migrate_2_to_3` 与
# `repair_mesh_migration`）。这里刻意不再提供"迁移写哪个状态"的配置项：旧网格是按旧
# 规则（固定 0.07 mm / 12 层）画的几何级产物，一份网格服务多个工况，从来没按工况
# 验证过，所以它只能是 `pending`（待按工况重画），不可能是 `queued`（审核通过）。
# 旧路径也不复制进新表——留着就会被下游当交付物（队列指针、求解、gc 都只看
# `status='queued' AND mesh_path IS NOT NULL`），只作为历史证据留在
# `data_lib/runs/high/fluent_meshing/<几何级 run_id>/` 下。

# ---- 修理计时（repairing 从哪一刻算起）----
# `updated_at` 是"这一行最后一次被写"的时间，不是"开始修理"的时间：迁移时旧 rejected
# 行被映射成 repairing，`updated_at` 还是它当年被否决的时刻，拿它判超时会把历史证据
# 误读成当前故障。所以另设一列记进入 repairing 的时刻，NULL = 不是真在修（历史遗留），
# 不参与超时判断。四张任务表都有这一列（见 `REPAIR_STARTED_TABLE`）。
REPAIR_STARTED_COLUMN = "repair_started_at"
REPAIR_STARTED_TABLES = ("geometry_state", "mesh_task", "cfd_task", "datcom_task")

# ---- `check` 的固定样例：与 myplan.md §3.3 的规范串逐字一致 ----
# 这几个 ID 是身份契约的一部分。改动配置区导致它们变化时 `check` 会立刻报错，
# 提醒这是"换了身份算法"，而不是可以随手接受的漂移。
GOLDEN_GEOMETRY = {
    "L_N": 0.49, "L": 3.69, "D": 0.18,
    "L_W": 1.72, "C_RW": 0.23, "C_TW": 0.06, "B_W": 0.23,
    "L_F": 3.20, "C_RF": 0.38, "C_TF": 0.19, "B_F": 0.22,
    "fin_section_type": "hex",
    "thickness_percent": 4.462, "flat_percent": 60.995, "le_radius": 0.05,
    "nose_shape": "cone",
}
GOLDEN_GEOMETRY_CANONICAL = (
    "L_N=0.490000|L=3.690000|D=0.180000|L_W=1.720000|C_RW=0.230000|C_TW=0.060000|"
    "B_W=0.230000|L_F=3.200000|C_RF=0.380000|C_TF=0.190000|B_F=0.220000|"
    "fin_section_type=hex|thickness_percent=4.462000|flat_percent=60.995000|"
    "le_radius=0.050000|nose_shape=cone"
)
GOLDEN_GEOMETRY_ID = "g692bcb5d5e50"

GOLDEN_CONDITION = {"mach": 2.0, "alpha_deg": 4.0, "altitude_m": 0.0}
GOLDEN_CONDITION_CANONICAL = "mach=2.000000|alpha_deg=4.000000|altitude_m=0.000000"
GOLDEN_CONDITION_ID = "cc9633c86a360"

# =============================================================================
# 2. 表结构与索引（单一库、12 张表；改字段必须同时升 SCHEMA_VERSION）
# =============================================================================
# 三态 CHECK 的正文只在这里拼一次：schema 与 2→3 迁移都用它，避免两处写法漂移。
STATUS_ENUM_SQL = ", ".join(f"'{status}'" for status in TASK_STATUSES)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geometry (
  geometry_id          TEXT PRIMARY KEY,          -- 'g' + 12 位小写十六进制
  L_N REAL NOT NULL, L REAL NOT NULL, D REAL NOT NULL,
  L_W REAL NOT NULL, C_RW REAL NOT NULL, C_TW REAL NOT NULL, B_W REAL NOT NULL,
  L_F REAL NOT NULL, C_RF REAL NOT NULL, C_TF REAL NOT NULL, B_F REAL NOT NULL,
  fin_section_type     TEXT NOT NULL CHECK (fin_section_type IN ('hex','naca')),
  thickness_percent    REAL NOT NULL,
  flat_percent         REAL,                      -- 仅 hex
  le_radius            REAL,                      -- 仅 hex
  le_radius_index      INTEGER,                   -- 仅 naca
  max_thickness_tenths INTEGER,                   -- 仅 naca
  nose_shape           TEXT NOT NULL CHECK (nose_shape IN ('ogive','cone')),
  canonical            TEXT NOT NULL UNIQUE,
  label                TEXT,                      -- 人工可读名，不参与身份
  created_at           TEXT NOT NULL,
  -- 实测质心（SpaceClaim 过审时写回）：x 从弹尖起算，y / z 是侧向分量。
  -- 派生量，不进规范串；没有过审的模型时保持 NULL。
  cg_x_m REAL, cg_y_m REAL, cg_z_m REAL,
  CHECK ( (fin_section_type='hex'
           AND flat_percent IS NOT NULL AND le_radius IS NOT NULL
           AND le_radius_index IS NULL AND max_thickness_tenths IS NULL)
       OR (fin_section_type='naca'
           AND le_radius_index IS NOT NULL AND max_thickness_tenths IS NOT NULL
           AND flat_percent IS NULL AND le_radius IS NULL) )
);

-- 建模是几何级的（一个几何只建一次模型、生成一份点云）；
-- 网格不是：它按 (geometry_id, condition_id) 放在 mesh_task 里，见下面的表。
CREATE TABLE IF NOT EXISTS geometry_state (
  geometry_id  TEXT PRIMARY KEY REFERENCES geometry(geometry_id),
  model_status TEXT NOT NULL DEFAULT 'pending' CHECK (model_status IN ({STATUS_ENUM_SQL})),
  model_path   TEXT, cloud_path TEXT,
  model_run_id TEXT, cloud_run_id TEXT,
  -- claimed_at 非空 = 这条建模任务已被某个进程领走；结束时清空。
  -- 它和 task_process 的 pid 一起承担"防重复领取"，所以不需要 running 这类状态词。
  claimed_at   TEXT,
  -- 进入 repairing 的时刻（UTC）；NULL = 不是真在修（迁移遗留），不参与超时判断。
  repair_started_at TEXT,
  deletable    INTEGER NOT NULL DEFAULT 0 CHECK (deletable IN (0,1)),
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS condition (
  condition_id TEXT PRIMARY KEY,                  -- 'c' + 12 位小写十六进制
  mach REAL NOT NULL, alpha_deg REAL NOT NULL, altitude_m REAL NOT NULL,
  canonical    TEXT NOT NULL UNIQUE,
  label        TEXT,                              -- 'C01'..'C30'，可为空，不参与身份
  created_at   TEXT NOT NULL,
  UNIQUE (mach, alpha_deg, altitude_m)
);

-- 网格按工况：同一几何不同来流马赫数的边界层首层高度/层数不同（见
-- fluent_meshing/boundary_layer.py），共用一个网格就会有工况落在 y+ 之外。
CREATE TABLE IF NOT EXISTS mesh_task (
  geometry_id      TEXT NOT NULL REFERENCES geometry(geometry_id),
  condition_id     TEXT NOT NULL REFERENCES condition(condition_id),
  status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ({STATUS_ENUM_SQL})),
  run_id           TEXT, mesh_path TEXT,
  -- 本次网格用的边界层设计值（跑之前算好写进来，review 按同一套公式复核）
  first_height_mm  REAL, n_layers INTEGER, growth_rate REAL,
  -- 可追溯证据：设计时的 y+ 目标与算出来的中间量
  y_plus_target    REAL, re REAL, cf REAL, u_tau REAL, delta_m REAL,
  attempts         INTEGER NOT NULL DEFAULT 0,
  claimed_at       TEXT,
  repair_started_at TEXT,
  review_id        TEXT REFERENCES review(review_id),
  created_at       TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (geometry_id, condition_id)
);

CREATE TABLE IF NOT EXISTS cfd_task (
  geometry_id  TEXT NOT NULL REFERENCES geometry(geometry_id),
  condition_id TEXT NOT NULL REFERENCES condition(condition_id),
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ({STATUS_ENUM_SQL})),
  attempts     INTEGER NOT NULL DEFAULT 0,
  CL REAL, CD REAL, CM REAL,
  converged    INTEGER,              -- 0/1，求解阶段填
  residual     REAL,                 -- 连续性残差
  run_id       TEXT, result_dir TEXT, log_path TEXT,
  claimed_at   TEXT,
  repair_started_at TEXT,
  review_id    TEXT REFERENCES review(review_id),
  origin       TEXT NOT NULL DEFAULT 'human' CHECK (origin IN ('human','active_learning')),
  round_id     TEXT REFERENCES training_round(round_id),
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (geometry_id, condition_id)
);

CREATE TABLE IF NOT EXISTS datcom_task (
  geometry_id  TEXT NOT NULL REFERENCES geometry(geometry_id),
  condition_id TEXT NOT NULL REFERENCES condition(condition_id),
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ({STATUS_ENUM_SQL})),
  attempts     INTEGER NOT NULL DEFAULT 0,
  CL REAL, CD REAL, CM REAL,
  run_id       TEXT, result_dir TEXT, log_path TEXT,
  claimed_at   TEXT,
  repair_started_at TEXT,
  review_id    TEXT REFERENCES review(review_id),
  origin       TEXT NOT NULL DEFAULT 'human' CHECK (origin IN ('human','active_learning')),
  round_id     TEXT REFERENCES training_round(round_id),
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (geometry_id, condition_id)
);

CREATE TABLE IF NOT EXISTS review (
  review_id       TEXT PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('model','mesh','solution','datcom')),
  geometry_id     TEXT NOT NULL,
  condition_id    TEXT,            -- model 为 NULL；mesh / solution / datcom 必须给
  run_id          TEXT NOT NULL,
  reviewer        TEXT NOT NULL,   -- 'script:review.py' 或子智能体标识
  verdict         TEXT NOT NULL CHECK (verdict IN ('passed','rejected')),
  checks_json     TEXT NOT NULL,   -- 每条判据的 name / ok / detail
  artifact_sha256 TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report (
  report_id    TEXT PRIMARY KEY,
  stage        TEXT NOT NULL CHECK (stage IN
               ('spaceclaim','fluent_meshing','fluent_solution','datcom')),
  geometry_id  TEXT NOT NULL,
  condition_id TEXT,
  attempts     INTEGER NOT NULL,
  last_error   TEXT,
  review_json  TEXT,
  feedback_dir TEXT,
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
  resolution   TEXT,
  created_at   TEXT NOT NULL, resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS control (
  target     TEXT PRIMARY KEY,     -- 'workflow:high' | 'stage:spaceclaim' | 'task:<run_id>'
  state      TEXT NOT NULL CHECK (state IN ('running','paused','stopped')),
  reason     TEXT,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL
);

-- run_id 三段式不含阶段名，所以同几何同工况的不同阶段理论上可能落在同一秒。
-- make_run_id() 撞库时把时间戳加 1 秒重试，保证这里的主键唯一。
CREATE TABLE IF NOT EXISTS task_process (
  run_id     TEXT PRIMARY KEY,
  pid        INTEGER NOT NULL,
  started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_round (
  round_id      TEXT PRIMARY KEY,
  status        TEXT NOT NULL CHECK (status IN ('running','passed','failed','cancelled')),
  data_version  TEXT,              -- 参与训练的样本集指纹
  model_path    TEXT,
  metrics_json  TEXT,
  proposed_rows INTEGER NOT NULL DEFAULT 0,
  log_path      TEXT,
  created_at    TEXT NOT NULL, finished_at TEXT
);
"""

INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_cfd_task_status      ON cfd_task(status);
CREATE INDEX IF NOT EXISTS idx_datcom_task_status   ON datcom_task(status);
CREATE INDEX IF NOT EXISTS idx_mesh_task_status     ON mesh_task(status);
CREATE INDEX IF NOT EXISTS idx_geometry_state_model ON geometry_state(model_status);
CREATE INDEX IF NOT EXISTS idx_report_status        ON report(status);
CREATE INDEX IF NOT EXISTS idx_review_kind_geometry ON review(kind, geometry_id);
"""

# 迁移：键是"库内当前版本"，值是一个收连接的函数，把它补齐到**下一版**。
# 只有这里登记过的旧版本能被 `init` 自动升级；其它版本一律拒绝，绝不猜着改结构。
# 每个迁移函数只改结构/数据，**不自己写 `meta.schema_version`**——版本号由 `cmd_init`
# 在每一步之后显式落库（见 `_set_schema_version`），这样"结构改到哪一版"和
# "库里记的是哪一版"永远同一步推进，不会出现"列已补、版本还是旧值"的半迁移库。
#   "1" → "2"：几何表加实测质心三列（派生量，不参与身份哈希）。
#   "2" → "3"：状态词七态 → 三态（重建表换掉 CHECK）、网格从几何级改成按工况。
#   "3" → "4"：四张任务表补 `repair_started_at`，并修正 2→3 留下的旧网格行。
# 旧词映射：passed→queued、rejected→repairing、reviewing→pending、running→pending
# （claimed_at 一并清空）、cancelled→pending。
# 旧的 queued 只代表"watcher 已认领、等 dispatcher 启动"，不是审核通过，所以也回
# pending——按新语义 queued 的含义是"审核通过、可被下一流程领取"。
MIGRATION_STATUS_MAP = {
    "pending": "pending",
    "queued": "pending",
    "running": "pending",
    "reviewing": "pending",
    "rejected": "repairing",
    "passed": "queued",
    "cancelled": "pending",
}
# 迁移前后必须逐表核对行数的表（新增 mesh_task 由复制而来，单独核）
MIGRATION_ROW_TABLES = (
    "geometry", "condition", "geometry_state", "cfd_task", "datcom_task", "review", "report",
)


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _status_case(column: str) -> str:
    """把旧状态词映射成三态；未知值一律落 `pending`（宁可重跑，不可假装通过）。"""
    branches = " ".join(
        f"WHEN '{old}' THEN '{new}'" for old, new in MIGRATION_STATUS_MAP.items())
    return f"CASE {column} {branches} ELSE 'pending' END"


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    for key in GEOMETRY_CENTROID_KEYS:
        conn.execute(f"ALTER TABLE geometry ADD COLUMN {key} REAL")
    conn.commit()


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """2→3：三态状态词 + 网格按 (geometry_id, condition_id)。

    SQLite 改不了 CHECK 约束，所以两张任务表与 `geometry_state` 都是"建新表 → 拷数据
    → 删旧表 → 改名"。迁移前后逐表数行数，必须完全一致；否则整体回滚并报错——迁移
    不许悄悄丢行。

    旧 `geometry_state.mesh_status/mesh_path/mesh_run_id` 在 `mesh_task` 里按该几何每个
    condition 建行，状态**写死 `pending`**（不设可配置项：旧网格没按工况验证过，
    没有"迁移即通过"这种可能），而且
    **`mesh_path` / `mesh_run_id` 一律写 NULL**：那份网格是按旧规则（固定 0.07 mm /
    12 层）画的几何级产物，一份网格服务多个工况，从来没按工况验证过。留着路径就会被
    下游当交付物（队列指针、求解、gc 都只看 `status='queued' AND mesh_path IS NOT NULL`），
    所以旧路径只作为历史证据留在 `runs/high/fluent_meshing/<旧 run_id>/` 下，不进新表。
    这几列从此废弃：几何级只剩建模与点云，网格一律按工况重画。

    `rejected → repairing` 的旧行没有"开始修理"的时刻，`repair_started_at` 写 NULL
    （见 `REPAIR_STARTED_COLUMN`），因此不会被 `scan_repair_timeouts` 当成当前故障。
    """
    before = {name: _row_count(conn, name) for name in MIGRATION_ROW_TABLES}
    # 旧网格路径不复制进新表，这里读出来只为在标准输出里说明"旧网格还在哪"。
    old_mesh = [tuple(row) for row in conn.execute(
        "SELECT geometry_id, mesh_path, mesh_run_id FROM geometry_state")]
    stamp = utc_sql()
    conn.commit()
    # 重建期间不查外键：`review` 等表对旧表没有引用，但临时表名会让引用短暂悬空。
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # ---- geometry_state：只留建模 + 点云 ----
        conn.execute(f"""
            CREATE TABLE geometry_state_v3 (
              geometry_id  TEXT PRIMARY KEY REFERENCES geometry(geometry_id),
              model_status TEXT NOT NULL DEFAULT 'pending'
                           CHECK (model_status IN ({STATUS_ENUM_SQL})),
              model_path   TEXT, cloud_path TEXT,
              model_run_id TEXT, cloud_run_id TEXT,
              claimed_at   TEXT,
              repair_started_at TEXT,
              deletable    INTEGER NOT NULL DEFAULT 0 CHECK (deletable IN (0,1)),
              updated_at   TEXT NOT NULL
            )
        """)
        # 旧状态词为 rejected 的行映射成 repairing，但没有开始修理的时刻 → NULL
        conn.execute(f"""
            INSERT INTO geometry_state_v3(geometry_id, model_status, model_path, cloud_path,
                                          model_run_id, cloud_run_id, claimed_at,
                                          repair_started_at, deletable, updated_at)
            SELECT geometry_id, {_status_case('model_status')}, model_path, cloud_path,
                   model_run_id, cloud_run_id, NULL, NULL, deletable, updated_at
            FROM geometry_state
        """)

        # ---- cfd_task / datcom_task：换 CHECK，加 claimed_at / repair_started_at ----
        for table, extra_column in (("cfd_task", "converged, residual,"),
                                    ("datcom_task", "")):
            conn.execute(f"""
                CREATE TABLE {table}_v3 (
                  geometry_id  TEXT NOT NULL REFERENCES geometry(geometry_id),
                  condition_id TEXT NOT NULL REFERENCES condition(condition_id),
                  status       TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ({STATUS_ENUM_SQL})),
                  attempts     INTEGER NOT NULL DEFAULT 0,
                  CL REAL, CD REAL, CM REAL, {extra_column}
                  run_id       TEXT, result_dir TEXT, log_path TEXT,
                  claimed_at   TEXT,
                  repair_started_at TEXT,
                  review_id    TEXT REFERENCES review(review_id),
                  origin       TEXT NOT NULL DEFAULT 'human'
                               CHECK (origin IN ('human','active_learning')),
                  round_id     TEXT REFERENCES training_round(round_id),
                  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL,
                  PRIMARY KEY (geometry_id, condition_id)
                )
            """)
            columns = ("geometry_id, condition_id, status, attempts, CL, CD, CM, "
                       f"{extra_column} run_id, result_dir, log_path, review_id, origin, "
                       "round_id, created_at, updated_at")
            mapped = columns.replace("status,", f"{_status_case('status')},", 1)
            conn.execute(f"INSERT INTO {table}_v3({columns}, claimed_at, repair_started_at)"
                         f" SELECT {mapped}, NULL, NULL FROM {table}")

        # ---- 新表 mesh_task（结构必须和 SCHEMA_SQL 里那份一致）----
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS mesh_task (
              geometry_id      TEXT NOT NULL REFERENCES geometry(geometry_id),
              condition_id     TEXT NOT NULL REFERENCES condition(condition_id),
              status           TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ({STATUS_ENUM_SQL})),
              run_id           TEXT, mesh_path TEXT,
              first_height_mm  REAL, n_layers INTEGER, growth_rate REAL,
              y_plus_target    REAL, re REAL, cf REAL, u_tau REAL, delta_m REAL,
              attempts         INTEGER NOT NULL DEFAULT 0,
              claimed_at       TEXT,
              repair_started_at TEXT,
              review_id        TEXT REFERENCES review(review_id),
              created_at       TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY (geometry_id, condition_id)
            )
        """)

        # ---- 为每个 (几何, 工况) 建一行 pending 网格任务；旧网格路径不入表 ----
        # 状态写死 'pending'：旧网格没按工况验证过，迁移不允许产生"已通过"的网格行。
        all_conditions = [row[0] for row in conn.execute(
            "SELECT condition_id FROM condition ORDER BY condition_id")]
        for geometry_id, _mesh_path, _mesh_run_id in old_mesh:
            condition_ids = [row[0] for row in conn.execute(
                "SELECT DISTINCT condition_id FROM cfd_task WHERE geometry_id=?"
                " ORDER BY condition_id", (geometry_id,))] or all_conditions
            for condition_id in condition_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO mesh_task(geometry_id, condition_id, status,"
                    " run_id, mesh_path, created_at, updated_at)"
                    " VALUES(?,?,'pending',NULL,NULL,?,?)",
                    (geometry_id, condition_id, stamp, stamp))

        conn.execute("DROP TABLE geometry_state")
        conn.execute("ALTER TABLE geometry_state_v3 RENAME TO geometry_state")
        for table in ("cfd_task", "datcom_task"):
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_v3 RENAME TO {table}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    # ---- 迁移后自检：行数一致 + 没有旧状态词残留 ----
    after = {name: _row_count(conn, name) for name in MIGRATION_ROW_TABLES}
    lost = {name: f"{before[name]} → {after[name]}"
            for name in before if before[name] != after[name]}
    if lost:
        raise SystemExit(f"迁移 2→3 前后行数不一致：{lost}")
    for table, column in (("geometry_state", "model_status"), ("cfd_task", "status"),
                          ("datcom_task", "status"), ("mesh_task", "status")):
        stale = conn.execute(f"SELECT COUNT(*) FROM {table}"
                             f" WHERE {column} NOT IN ({STATUS_ENUM_SQL})").fetchone()[0]
        if stale:
            raise SystemExit(f"迁移 2→3 后 {table}.{column} 仍有 {stale} 行不是三态词")
    conn.commit()


def _shared_run_id_pattern(column: str = "run_id") -> str:
    """`run_id` 是几何级（中段是 `shared`）的 SQL 匹配式。"""
    return f"{column} LIKE '%{RUN_ID_SEPARATOR}{SHARED_CONDITION_ID}{RUN_ID_SEPARATOR}%'"


# "旧的几何级网格被当成按工况过审"的唯一判据：**只看两件事**
#   1. `first_height_mm IS NULL`——没按该工况算过边界层设计值，就不可能是按工况审的；
#   2. 这一行还挂着几何级 `run_id`（中段 `shared`）。
# 刻意**不看** `status` / `mesh_path`：判据一旦依赖"当前状态"，行被下游改成别的值就
# 检测不到了，修完也可能留下 `mesh_path` 非空却 `pending` 的怪行。两列一并清空才是
# 真正的"不留成有效产物"。
# 默认还加 `claimed_at IS NULL` 作为安全阀：正在被进程领取的行走这条路说明有人刚领走
# 它，这时改 `run_id` 会让 `resolve_run` 失去线索。要清这类行必须显式指名
# （`repair-mesh-migration --release-claim`），由调用方承担"这个进程确实不该再跑"。
MESH_MIGRATION_CONDITION = (
    "first_height_mm IS NULL"
    f" AND {_shared_run_id_pattern()}"
    " AND claimed_at IS NULL"
)
# 不含领取安全阀的版本：只给 `--release-claim` 显式指名时用。
MESH_MIGRATION_CONDITION_ANY_CLAIM = MESH_MIGRATION_CONDITION.replace(
    " AND claimed_at IS NULL", "")


def _mesh_reset_sql() -> str:
    """把"旧的几何级网格被当成按工况过审"的行改回 `pending`，并清掉旧路径/运行号。

    判据见 `MESH_MIGRATION_CONDITION`。清 `run_id` / `mesh_path` 是必须的：这两列非空
    就会被队列指针 / 求解 / gc 当成有效产物。旧网格本身留在
    `runs/high/fluent_meshing/<旧 run_id>/` 下作为历史证据，不进新表。
    """
    return (
        "UPDATE mesh_task SET status='pending', run_id=NULL, mesh_path=NULL,"
        f" {REPAIR_STARTED_COLUMN}=NULL, updated_at=?"
        f" WHERE {MESH_MIGRATION_CONDITION}"
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_repair_started_column(conn: sqlite3.Connection) -> list[str]:
    """给四张任务表补 `repair_started_at`（已有则跳过），返回本次实际补的表。

    幂等，所以对"已经迁到 v4"的库是空操作，对停在 v3 的库就是那一步补列。3→4 迁移与
    `repair-mesh-migration` 共用它：数据修正要写这一列，不能假设列已经存在。
    """
    added: list[str] = []
    for table in REPAIR_STARTED_TABLES:
        if table not in table_names(conn):
            continue
        if REPAIR_STARTED_COLUMN in _table_columns(conn, table):
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {REPAIR_STARTED_COLUMN} TEXT")
        added.append(table)
    if added:
        conn.commit()
    return added


def _migrate_3_to_4(conn: sqlite3.Connection) -> None:
    """3→4：四张任务表补 `repair_started_at`，并把"旧网格被当成按工况过审"的行修正。

    两件事，顺序不能颠倒：

    1. **补列**：`geometry_state` / `mesh_task` / `cfd_task` / `datcom_task` 各加一列
       `repair_started_at TEXT`（可空，见 `ensure_repair_started_column`）。补列不改任何
       现有值——历史遗留的 `repairing` 行（含 2→3 里 `rejected → repairing` 的旧行）
       因此天然是 NULL，不参与修理超时判断（见 `scan_repair_timeouts`）。
    2. **修正 2→3 的网格迁移结果**：最早那版 2→3 把旧几何级网格复制进 `mesh_task`
       时置的是 `queued` 且带着同一个 `mesh_path` / `run_id`，四个工况共用一份没按
       工况验证过的网格。`repair_mesh_migration` 按 `MESH_MIGRATION_CONDITION` 把它们
       改成 `pending` 并清空路径——它要写 `repair_started_at`，所以必须在上一步之后。

    队列目录由 `cmd_init` 在迁移循环结束后统一重写（`export_queue`），不在这里做：
    迁移函数只碰库，不碰文件。
    """
    conn.commit()
    ensure_repair_started_column(conn)
    repair_mesh_migration(conn)


def repair_mesh_migration(conn: sqlite3.Connection,
                          *, reset_claim: list[str] | None = None) -> dict:
    """把"旧几何级网格被当成按工况过审"的行改成 `pending`；返回改动清单。

    幂等：没有这样的行时返回空清单。对已经修好的库重复调用不会造成任何变化。
    `db.py repair-mesh-migration` 是它的命令行入口，`_migrate_3_to_4` 也会调用它，
    所以手工补过列的老库和新迁上来的库走的是同一段逻辑，不存在两套规则。

    这是**数据修正**，不升 `schema_version`：3→4 迁移本身（补列）与"旧网格行改回
    pending"是两件独立的事，已经迁到 v4 的库再跑一次只会得到空清单。

    `reset_claim` 是要连领取一起放弃的 `run_id` 列表：这些行的 `claimed_at` 非空，被
    `MESH_MIGRATION_CONDITION` 的安全阀排除在批量修正之外，必须显式指名。清领取走
    `abandon_claim`（拒绝活着的进程、注销 `task_process`、任务回 pending 并开单），
    不在这里另写一套。
    """
    columns = _table_columns(conn, MESH_TASK_TABLE)
    if not {"status", "mesh_path", "run_id"} <= columns:
        raise WriteError(f"{MESH_TASK_TABLE} 不存在或结构不对，先跑 python scripts/db.py init")
    # 修正要用到 `repair_started_at`；停在 v3 的库还没这一列，就地补上（与 3→4 同一个
    # 函数，不会重复补，也不改任何现有值）。
    ensure_repair_started_column(conn)
    rows = conn.execute(
        "SELECT geometry_id, condition_id, status, run_id, mesh_path FROM mesh_task"
        f" WHERE {MESH_MIGRATION_CONDITION}"
        " ORDER BY geometry_id, condition_id").fetchall()
    if rows:
        conn.execute(_mesh_reset_sql(), (utc_sql(),))
        conn.commit()
    released: list[dict] = []
    for run_id in reset_claim or ():
        released.append(abandon_claim(conn, run_id))
    return {"reset": [dict(row) for row in rows], "released_claims": released}


SCHEMA_MIGRATIONS = {
    "1": _migrate_1_to_2,
    "2": _migrate_2_to_3,
    "3": _migrate_3_to_4,
}
# 每一步迁移把库补到哪一版：`cmd_init` 跑完 `SCHEMA_MIGRATIONS[旧版]` 后写这个值。
# 它必须与 `SCHEMA_MIGRATIONS` 的键一一对上（`check` 会校验），否则链条会断在半路。
SCHEMA_MIGRATION_TARGETS = {
    "1": "2",
    "2": "3",
    "3": "4",
}


def _set_schema_version(conn: sqlite3.Connection, version: str) -> None:
    """写 `meta.schema_version` 并提交：迁移的每一步都必须当场落盘版本号。

    迁移函数只改结构/数据，版本号统一在这里写——这是"半迁移库"（列已加、数据已改、
    但 `meta` 还停在旧版）唯一的防线。写完之后 `while` 循环才会前进，不会重复跑同一
    步迁移、也不会在下一步找不到登记时误报"链条缺环"。
    """
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (version,),
    )
    conn.commit()

# 2→3 迁移会删掉的三张表里的旧网格列；`init` 打印"本次废弃"用。
RETIRED_COLUMNS = {
    "geometry_state": ("mesh_status", "mesh_path", "mesh_run_id"),
}

EXPECTED_TABLES = (
    "meta", "geometry", "geometry_state", "condition", "mesh_task", "cfd_task",
    "datcom_task", "review", "report", "control", "task_process", "training_round",
)

EXPECTED_INDEXES = (
    "idx_cfd_task_status", "idx_datcom_task_status", "idx_mesh_task_status",
    "idx_geometry_state_model", "idx_report_status", "idx_review_kind_geometry",
)


# =============================================================================
# 3. 路径解析
# =============================================================================
def load_workspace() -> dict:
    """读 `workspace.json`；不存在时返回空字典（全部走本节默认值）。"""
    path = WORKSPACE_ROOT / WORKSPACE_FILE_NAME
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_database(cli_value: str | None = None) -> Path:
    """库路径：命令行 > 环境变量 > workspace.json > 默认相对路径。"""
    if cli_value:
        candidate = Path(cli_value)
    elif os.environ.get(DATABASE_ENV):
        candidate = Path(os.environ[DATABASE_ENV])
    else:
        workspace = load_workspace()
        candidate = Path(workspace.get("database") or DEFAULT_DATABASE_REL)
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    return candidate.resolve()


def workspace_rel(path: Path | str) -> str:
    """库内一律存工作区相对路径（正斜杠），换机器、挪目录都不失效。"""
    target = Path(path).resolve()
    return target.relative_to(WORKSPACE_ROOT).as_posix()


# =============================================================================
# 4. 时间
# =============================================================================
def utc_stamp(moment: datetime | None = None) -> str:
    """`run_id` 里的时间戳：UTC、秒级、`YYYYMMDDTHHMMSSZ`。"""
    return (moment or datetime.now(TIMEZONE)).astimezone(TIMEZONE).strftime(TIMESTAMP_FORMAT)


def utc_sql(moment: datetime | None = None) -> str:
    """库内时间字段：`YYYY-MM-DDTHH:MM:SSZ`，可直接字符串排序。"""
    return (moment or datetime.now(TIMEZONE)).astimezone(TIMEZONE).strftime(SQL_TIMESTAMP_FORMAT)


# =============================================================================
# 5. 规范化：先钳位、再量化、再拼串、最后哈希
# =============================================================================
@lru_cache(maxsize=1)
def _datcom_spec():
    """取 `DATCOM/scripts/datcom_spec.py`（纯常量 + 参数校验，无第三方依赖）。"""
    if not (DATCOM_SCRIPTS_DIR / "datcom_spec.py").exists():
        raise RuntimeError(
            f"找不到几何校验模块：{DATCOM_SCRIPTS_DIR / 'datcom_spec.py'}。"
            "几何身份必须与 DATCOM 共用同一套校验，不能在这里另写一份。"
        )
    if str(DATCOM_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(DATCOM_SCRIPTS_DIR))
    import datcom_spec  # noqa: PLC0415  （延迟导入：db.py 的其余命令不依赖 DATCOM）

    return datcom_spec


def _quantize(value: float) -> float:
    return round(float(value), DECIMALS)


def _fmt_float(value: float) -> str:
    """`%.6f`、禁止科学计数法；`-0.0` 一律写 `0.000000`，避免同一外形两个身份。"""
    number = _quantize(value)
    if number == 0.0:
        number = 0.0
    return FLOAT_FORMAT % number


def _fmt_int(value) -> str:
    return "%d" % int(value)


def _as_number(raw_value, key: str, errors: list[str], *, required: bool = True):
    """取数值；缺失/空/非数值/非有限都记进 errors，返回 None。"""
    if raw_value is None or raw_value == "":
        if required:
            errors.append(f"缺少参数 {key}。")
        return None
    if isinstance(raw_value, bool):
        errors.append(f"参数 {key} 必须是数值，收到布尔值 {raw_value!r}。")
        return None
    try:
        number = float(raw_value)
    except (TypeError, ValueError):
        errors.append(f"参数 {key} 必须是数值，收到 {raw_value!r}。")
        return None
    if not math.isfinite(number):
        errors.append(f"参数 {key} 必须是有限值，收到 {raw_value!r}。")
        return None
    return number


def normalize_geometry(raw: dict) -> tuple[dict, list[str]]:
    """几何 → (16 参数字典, 提示列表)。钳位在这里完成，哈希使用返回的字典。

    顺序固定：① `datcom_spec.normalize_geometry` 校验并钳位 11 个主尺寸
    ② 校验翼型种类与对应三个参数、鼻锥头型 ③ 量化到 6 位小数。
    """
    spec = _datcom_spec()
    errors: list[str] = []
    # ① 主尺寸：缺参、非数值、几何不自洽都由 DATCOM 那一份校验统一给出中文说明
    try:
        lengths, notes = spec.normalize_geometry(
            {key: raw.get(key) for key in GEOMETRY_LENGTH_KEYS}
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    params: dict = {key: _quantize(lengths[key]) for key in GEOMETRY_LENGTH_KEYS}

    # ② 翼型种类与鼻锥头型
    fin_type = str(raw.get(FIN_SECTION_KEY) or "").strip().lower()
    if not fin_type:
        errors.append(f"缺少参数 {FIN_SECTION_KEY}（只能是 {' / '.join(FIN_SECTION_TYPES)}）。")
    elif fin_type not in FIN_SECTION_TYPES:
        errors.append(f"{FIN_SECTION_KEY} = {fin_type!r} 不合法，只能是 {FIN_SECTION_TYPES}。")
    nose = str(raw.get(NOSE_SHAPE_KEY) or "").strip().lower()
    if not nose:
        errors.append(f"缺少参数 {NOSE_SHAPE_KEY}（只能是 {' / '.join(NOSE_SHAPES)}）。")
    elif nose not in NOSE_SHAPES:
        errors.append(f"{NOSE_SHAPE_KEY} = {nose!r} 不合法，只能是 {NOSE_SHAPES}。")
    params[FIN_SECTION_KEY] = fin_type
    params[NOSE_SHAPE_KEY] = nose
    if errors:
        raise ValueError("\n".join(errors))

    # ③ 翼型三个参数：整数项必须是整数，其余按范围校验
    for key in FIN_PARAM_KEYS[fin_type]:
        if key in FIN_INT_PARAM_KEYS:
            value = _as_number(raw.get(key), key, errors)
            if value is None:
                continue
            if value != int(value):
                errors.append(f"参数 {key} 必须是整数，收到 {raw.get(key)!r}。")
                continue
            params[key] = int(value)
        else:
            value = _as_number(raw.get(key), key, errors)
            if value is not None:
                params[key] = _quantize(value)
    if errors:
        raise ValueError("\n".join(errors))

    # ④ 范围
    for key in FIN_PARAM_KEYS[fin_type]:
        bounds = FIN_PARAM_BOUNDS.get(key)
        if bounds is None:                      # 枚举型
            if params[key] not in NACA_MAX_THICKNESS_TENTHS_CHOICES:
                errors.append(
                    f"参数 {key} 只能取 {NACA_MAX_THICKNESS_TENTHS_CHOICES}，收到 {params[key]!r}。"
                )
            continue
        low, high = bounds
        if params[key] < low or (high is not None and params[key] > high):
            upper = "∞" if high is None else f"{high:g}"
            errors.append(f"参数 {key} 必须在 [{low:g}, {upper}] 内，收到 {params[key]!r}。")
    if errors:
        raise ValueError("\n".join(errors))
    return params, notes


def normalize_condition(raw: dict) -> tuple[dict, list[str]]:
    """工况 → ({mach, alpha_deg, altitude_m}, 提示列表)。三条都量化到 6 位小数。"""
    errors: list[str] = []
    params: dict = {}
    for key in CONDITION_KEYS:
        value = _as_number(raw.get(key), key, errors)
        if value is not None:
            params[key] = _quantize(value)
    if not errors and params["mach"] < 0:
        errors.append(f"马赫数 mach 不能为负，收到 {params['mach']!r}。")
    if errors:
        raise ValueError("\n".join(errors))
    return params, ["数值按 %.6f 量化后参与身份。"]


def canonical_geometry(params: dict) -> str:
    """几何规范串：11 主尺寸 → 翼型种类 → 翼型三参数 → 头型，`|` 分隔。"""
    parts = [f"{key}={_fmt_float(params[key])}" for key in GEOMETRY_LENGTH_KEYS]
    fin_type = params[FIN_SECTION_KEY]
    parts.append(f"{FIN_SECTION_KEY}={fin_type}")
    for key in FIN_PARAM_KEYS[fin_type]:
        text = _fmt_int(params[key]) if key in FIN_INT_PARAM_KEYS else _fmt_float(params[key])
        parts.append(f"{key}={text}")
    parts.append(f"{NOSE_SHAPE_KEY}={params[NOSE_SHAPE_KEY]}")
    return "|".join(parts)


def canonical_condition(params: dict) -> str:
    """工况规范串：`mach` → `alpha_deg` → `altitude_m`。"""
    return "|".join(f"{key}={_fmt_float(params[key])}" for key in CONDITION_KEYS)


def hash_id(kind: str, canonical: str) -> str:
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:ID_HEX_LEN]
    return f"{ID_PREFIX[kind]}{digest}"


def identify_geometry(raw: dict) -> dict:
    """一步到位：规范化 → 规范串 → `geometry_id`。"""
    params, notes = normalize_geometry(raw)
    canonical = canonical_geometry(params)
    return {
        "kind": "geometry",
        "geometry_id": hash_id("geometry", canonical),
        "canonical": canonical,
        "params": params,
        "notes": notes,
    }


def identify_condition(raw: dict) -> dict:
    params, notes = normalize_condition(raw)
    canonical = canonical_condition(params)
    return {
        "kind": "condition",
        "condition_id": hash_id("condition", canonical),
        "canonical": canonical,
        "params": params,
        "notes": notes,
    }


def normalize(raw: dict) -> dict:
    """`normalize()` 总入口：`{"geometry": {...}, "condition": {...}}` → 身份结果。"""
    result: dict = {}
    if "geometry" in raw:
        result.update(identify_geometry(raw["geometry"]))
    if "condition" in raw:
        condition = identify_condition(raw["condition"])
        condition.pop("kind", None)
        result["condition"] = condition
    if not result:
        raise ValueError("没有可规范化的输入：需要 geometry 和/或 condition 两段。")
    return result


def make_run_id(geometry_id: str, condition_id: str, existing: set[str] | None = None) -> str:
    """`<geometry_id>__<condition_id>__<时间戳>`；几何级产物中段写 `shared`。

    撞车（同一秒、同几何、同工况）时把时间戳加 1 秒重试，最多试 60 次；
    `run_id` 里唯一无法由身份推出的信息就是"这是第几次跑"。
    """
    stamp = datetime.now(TIMEZONE)
    taken = existing or set()
    for _ in range(60):
        run_id = RUN_ID_SEPARATOR.join(
            (geometry_id, condition_id or SHARED_CONDITION_ID, utc_stamp(stamp))
        )
        if run_id not in taken:
            return run_id
        stamp = stamp.replace(microsecond=0)
        stamp = datetime.fromtimestamp(stamp.timestamp() + 1, TIMEZONE)
    raise RuntimeError(f"同一秒内已经排满 60 个 run_id：{geometry_id} / {condition_id}")


def split_run_id(run_id: str) -> tuple[str, str, str]:
    """从右往左拆：时间戳 → `condition_id` → `geometry_id`。"""
    head, stamp = run_id.rsplit(RUN_ID_SEPARATOR, 1)
    geometry_id, condition_id = head.rsplit(RUN_ID_SEPARATOR, 1)
    return geometry_id, condition_id, stamp


# =============================================================================
# 6. 连接与建表
# =============================================================================
def connect(db_path: Path, *, must_exist: bool = True) -> sqlite3.Connection:
    """打开库并按配置区设置 PRAGMA。写操作请一律经过本模块的命令。"""
    if must_exist and not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}\n先运行：python scripts/db.py init")
    if not must_exist:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA foreign_keys = {'ON' if FOREIGN_KEYS else 'OFF'}")
    if not must_exist:
        conn.execute(f"PRAGMA journal_mode = {JOURNAL_MODE}")
        conn.execute(f"PRAGMA synchronous = {SYNCHRONOUS}")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executescript(INDEX_SQL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE value <> excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return {row["name"] for row in rows}


def index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )
    return {row["name"] for row in rows}


# =============================================================================
# 7. 命令：init
# =============================================================================
def cmd_init(args) -> int:
    db_path = resolve_database(args.database)
    existed = db_path.exists()
    conn = connect(db_path, must_exist=False)
    migrated_from = None
    reset_meshes: list[dict] = []
    try:
        before = table_names(conn)
        if "meta" in before:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            current = row["value"] if row else None
            migrated_from = current if current != SCHEMA_VERSION else None
            # 逐版本往上走，不一步跳到底：每一步都只认 `SCHEMA_MIGRATIONS` 里登记过的
            # 上一版，链条缺一环就拒绝，绝不猜着改结构。
            while current != SCHEMA_VERSION:
                step = SCHEMA_MIGRATIONS.get(current or "")
                if step is None:
                    raise SystemExit(
                        f"库内 schema_version={current!r} 与本文件 {SCHEMA_VERSION!r} 不一致，"
                        "而且没有登记从该版本到当前版本的迁移。\n"
                        "改表结构必须升版本号并在 SCHEMA_MIGRATIONS 里补迁移，不能直接覆盖。"
                    )
                step(conn)
                # 迁移函数只改结构与数据；版本号必须在这里推进，否则循环会反复跑同一步
                # （重复补列会报 duplicate column，3→4 的网格修正也会被反复执行），
                # 库里还会留下"列已是新版、meta 仍是旧版"的半迁移状态。
                _set_schema_version(conn, SCHEMA_MIGRATION_TARGETS[current])
                current = conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'").fetchone()
                current = current["value"] if current else None
        create_schema(conn)
        # 走过迁移才重写队列目录：它是数据库的只读视图，3→4 的网格修正确实会让某些
        # 指针不该存在了。新建库（没有任何迁移）不重写——那会把目录整片清空，
        # 而空库本来就没有"该有的指针"可言。
        queue_rewrites: list[tuple[str, list[str]]] = []
        if migrated_from is not None:
            for queue in sorted(set(QUEUE_FOR_REVIEW_KIND.values())):
                queue_rewrites.append((queue, export_queue(conn, queue)))
        after = table_names(conn)
    finally:
        conn.close()

    print(f"数据库：{db_path}（{'新建' if not existed else '已存在'}）")
    print(f"表 {len(after)} 张：{', '.join(sorted(after))}")
    if migrated_from is not None:
        print(f"已迁移 schema_version：{migrated_from} → {SCHEMA_VERSION}")
        for table, columns in RETIRED_COLUMNS.items():
            print(f"废弃列（{table}）：{', '.join(columns)}"
                  "（旧值只留在 runs/ 下作历史证据，不进 mesh_task）")
    for queue, written in queue_rewrites:
        print(f"队列指针已按库重写：{queue} → {len(written)} 个"
              f"（{workspace_rel(queue_directory(queue))}）")
    print(f"schema_version = {SCHEMA_VERSION}；journal_mode = {JOURNAL_MODE}")
    if existed:
        added = sorted(after - before)
        print(f"本次新建：{', '.join(added) if added else '无（已是最新）'}")
    return 0


# =============================================================================
# 8. 命令：check
# =============================================================================
def _one_line(text: str) -> str:
    """CHECK / FK 的报错带换行，逐项打印会破坏对齐，只留第一行。"""
    return str(text).strip().splitlines()[0] if str(text).strip() else ""


def _expect_error(conn: sqlite3.Connection, sql: str, params: tuple, label: str) -> tuple[bool, str]:
    """期望这条写入被库拒绝（CHECK / FK / 主键）。"""
    try:
        conn.execute(sql, params)
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        return True, f"{label} 被拒绝：{_one_line(exc)}"
    conn.rollback()
    return False, f"{label} 竟然写进去了"


def _expect_ok(conn: sqlite3.Connection, sql: str, params: tuple, label: str) -> tuple[bool, str]:
    """期望这条写入成功。**必须提交**：随后回滚会把这行删掉，依赖它的外键就会假失败。"""
    try:
        conn.execute(sql, params)
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        return False, f"{label} 应当成功却被拒绝：{_one_line(exc)}"
    conn.commit()
    return True, label


def _insert_geometry_sql() -> str:
    columns = (
        "geometry_id", *GEOMETRY_LENGTH_KEYS,
        "fin_section_type", "thickness_percent", "flat_percent", "le_radius",
        "le_radius_index", "max_thickness_tenths", "nose_shape",
        "canonical", "label", "created_at",
    )
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT INTO geometry({', '.join(columns)}) VALUES({placeholders})"


def _geometry_row(identity: dict, **overrides) -> tuple:
    params = identity["params"]
    values = {
        "geometry_id": identity["geometry_id"],
        **{key: params[key] for key in GEOMETRY_LENGTH_KEYS},
        "fin_section_type": params[FIN_SECTION_KEY],
        "thickness_percent": params["thickness_percent"],
        "flat_percent": params.get("flat_percent"),
        "le_radius": params.get("le_radius"),
        "le_radius_index": params.get("le_radius_index"),
        "max_thickness_tenths": params.get("max_thickness_tenths"),
        "nose_shape": params[NOSE_SHAPE_KEY],
        "canonical": identity["canonical"],
        "label": None,
        "created_at": utc_sql(),
    }
    values.update(overrides)
    order = (
        "geometry_id", *GEOMETRY_LENGTH_KEYS,
        "fin_section_type", "thickness_percent", "flat_percent", "le_radius",
        "le_radius_index", "max_thickness_tenths", "nose_shape",
        "canonical", "label", "created_at",
    )
    return tuple(values[key] for key in order)


def cmd_check(args) -> int:
    db_path = resolve_database(args.database)
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    # ---- 文件与连接 ----
    record("库文件存在", db_path.exists(), str(db_path))
    if not db_path.exists():
        _print_results(results)
        return 1

    conn = connect(db_path)
    try:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        record("journal_mode = WAL", str(journal).lower() == JOURNAL_MODE.lower(), f"实际 {journal}")
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        record("foreign_keys 已开启", bool(fk), f"实际 {fk}")
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        record(
            "busy_timeout 已设置",
            int(busy) == BUSY_TIMEOUT_MS,
            f"实际 {busy} ms（期望 {BUSY_TIMEOUT_MS}）",
        )
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        record("integrity_check = ok", integrity == "ok", str(integrity))

        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        record("foreign_key_check 无违规", not fk_rows, f"{len(fk_rows)} 条违规")

        # ---- 结构 ----
        tables = table_names(conn)
        missing = [name for name in EXPECTED_TABLES if name not in tables]
        extra = sorted(tables - set(EXPECTED_TABLES))
        record(
            f"{len(EXPECTED_TABLES)} 张表齐全",
            not missing,
            f"缺 {missing}" if missing else (f"多余 {extra}（不阻塞）" if extra else "全部就位"),
        )
        indexes = index_names(conn)
        missing_index = [name for name in EXPECTED_INDEXES if name not in indexes]
        record(f"{len(EXPECTED_INDEXES)} 个索引齐全", not missing_index,
               f"缺 {missing_index}" if missing_index else "全部就位")

        if "meta" in tables:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            version = row["value"] if row else None
            record("schema_version 一致", version == SCHEMA_VERSION, f"库内 {version}，代码 {SCHEMA_VERSION}")
        else:
            record("schema_version 一致", False, "meta 表不存在")

        if "geometry" in tables:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(geometry)")}
            missing_centroid = [key for key in GEOMETRY_CENTROID_KEYS if key not in columns]
            record("几何表有实测质心三列", not missing_centroid,
                   f"缺 {missing_centroid}（先跑 python scripts/db.py init 执行迁移）"
                   if missing_centroid else ", ".join(GEOMETRY_CENTROID_KEYS))
        else:
            record("几何表有实测质心三列", False, "geometry 表不存在")

        # 网格按工况：mesh_task 必须存在且带边界层设计值与领取标记
        if "mesh_task" in tables:
            mesh_columns = {row["name"] for row in conn.execute("PRAGMA table_info(mesh_task)")}
            missing_mesh = [key for key in ("geometry_id", "condition_id", "status", "run_id",
                                            "mesh_path", "first_height_mm", "n_layers",
                                            "growth_rate", "claimed_at")
                            if key not in mesh_columns]
            record("网格表按 (几何, 工况) 且带边界层设计值", not missing_mesh,
                   f"缺 {missing_mesh}" if missing_mesh else
                   f"{_row_count(conn, 'mesh_task')} 行")
        else:
            record("网格表按 (几何, 工况) 且带边界层设计值", False, "mesh_task 表不存在")

        # 三张状态表都必须有 claimed_at（防重复领取的唯一标记）
        unclaimed = [name for name in ("geometry_state", "mesh_task", "cfd_task", "datcom_task")
                     if name in tables
                     and "claimed_at" not in {row["name"] for row in
                                              conn.execute(f"PRAGMA table_info({name})")}]
        record("四张任务表都有 claimed_at", not unclaimed,
               f"缺 {unclaimed}" if unclaimed else "geometry_state / mesh_task / cfd_task / datcom_task")

        # 几何级网格三列必须已废弃（存在说明 2→3 迁移没跑）
        if "geometry_state" in tables:
            state_columns = {row["name"] for row in conn.execute(
                "PRAGMA table_info(geometry_state)")}
            leftover = [key for key in RETIRED_COLUMNS["geometry_state"] if key in state_columns]
            record("几何级网格三列已废弃", not leftover,
                   f"仍存在 {leftover}（先跑 python scripts/db.py init 执行 2→3 迁移）"
                   if leftover else "moved to mesh_task")

        # 状态词的 CHECK 必须真的是三态：真库里不该有旧词
        stale = {}
        for table, column in (("geometry_state", "model_status"), ("mesh_task", "status"),
                              ("cfd_task", "status"), ("datcom_task", "status")):
            if table not in tables:
                continue
            count = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} NOT IN ({STATUS_ENUM_SQL})"
            ).fetchone()[0])
            if count:
                stale[table] = count
        record("状态词只剩三态", not stale,
               f"旧词残留 {stale}" if stale else " / ".join(TASK_STATUSES))

        # 修理计时列：四张任务表都必须有，否则修理超时没有可靠的起点
        missing_started = [name for name in REPAIR_STARTED_TABLES
                           if name in tables
                           and REPAIR_STARTED_COLUMN not in {row["name"] for row in
                                                             conn.execute(
                                                                 f"PRAGMA table_info({name})")}]
        record(f"四张任务表都有 {REPAIR_STARTED_COLUMN}", not missing_started,
               f"缺 {missing_started}（先跑 python scripts/db.py init 执行 3→4 迁移）"
               if missing_started else " / ".join(REPAIR_STARTED_TABLES))

        # 迁移链完整性：登记迁移的每一步都必须有目标版本，且目标恰好是"下一版"，
        # 否则 `init` 的 `while` 循环会原地打转或跳过某一版。这是结构配置的内部一致性。
        orphan_steps = sorted(set(SCHEMA_MIGRATIONS) - set(SCHEMA_MIGRATION_TARGETS))
        bad_targets = sorted(f"{step}→{target}" for step, target in
                             SCHEMA_MIGRATION_TARGETS.items() if target != str(int(step) + 1))
        record("迁移链每一步都指向下一版", not orphan_steps and not bad_targets,
               f"缺目标版本 {orphan_steps}、目标不是下一版 {bad_targets}"
               if (orphan_steps or bad_targets)
               else " → ".join(f"{k}→{v}" for k, v in sorted(SCHEMA_MIGRATION_TARGETS.items())))

        # 队列目录是数据库的只读视图，只能由 `export_queue` 写。库里"不该有指针"的行
        # 一变，目录里就必须跟着消失；这里逐目录比对文件名，把残留的指针抓出来。
        for queue in sorted(set(QUEUE_FOR_REVIEW_KIND.values())):
            directory = queue_directory(queue)
            if not directory.is_dir():
                record(f"队列 {queue} 与库一致", True, "目录不存在（库为空）")
                continue
            _kind, entries = queue_entries(conn, queue)
            expected_names = {entry["name"] for entry in entries if "name" in entry}
            actual_names = {item.name for item in directory.glob("*.json")}
            stale_names = sorted(actual_names - expected_names)
            missing_names = sorted(expected_names - actual_names)
            detail = []
            if stale_names:
                detail.append(f"多出 {len(stale_names)} 个（{', '.join(stale_names[:3])}"
                              f"{'…' if len(stale_names) > 3 else ''}）")
            if missing_names:
                detail.append(f"缺 {len(missing_names)} 个（{', '.join(missing_names[:3])}"
                              f"{'…' if len(missing_names) > 3 else ''}）")
            record(f"队列 {queue} 与库一致", not stale_names and not missing_names,
                   "；".join(detail) + "，跑 python scripts/db.py export --format queue "
                   f"--queue {queue}" if detail else f"{len(expected_names)} 个指针")

        # repairing 行没有修理起点 = 迁移遗留（历史证据），不该被算成"正在修"：
        # 它们既不参与超时上报，也不该被误当成当前故障。这里只统计，不拦。
        if not missing_started:
            no_start = {}
            for table, column in (("geometry_state", "model_status"), ("mesh_task", "status"),
                                  ("cfd_task", "status"), ("datcom_task", "status")):
                if table not in tables:
                    continue
                count = int(conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}='repairing'"
                    f" AND {REPAIR_STARTED_COLUMN} IS NULL").fetchone()[0])
                if count:
                    no_start[table] = count
            record("repairing 行都有修理起点（除迁移遗留）", True,
                   f"无起点的 repairing 行（历史遗留，不参与超时）：{no_start}"
                   if no_start else "全部有起点")

        # 迁移修正的结果：不许再有"没按工况算过边界层、却带着几何级网格"的行。
        # 这种行会被队列指针 / 求解 / gc 当成已交付的网格。判据与
        # `repair_mesh_migration` 相同（含领取中的行——领取中的旧网格同样是缺陷，
        # 只是修它要显式 `--release-claim`）。
        if "mesh_task" in tables and REPAIR_STARTED_COLUMN in {
                row["name"] for row in conn.execute("PRAGMA table_info(mesh_task)")}:
            bad_mesh = int(conn.execute(
                f"SELECT COUNT(*) FROM mesh_task WHERE {MESH_MIGRATION_CONDITION_ANY_CLAIM}"
            ).fetchone()[0])
            record("无未按工况验证的旧网格被当成过审", bad_mesh == 0,
                   f"{bad_mesh} 行仍是几何级旧网格（跑 python scripts/db.py "
                   "repair-mesh-migration）" if bad_mesh else "0 行")
    finally:
        conn.close()

    # ---- 规范化与身份 ----
    try:
        geometry = identify_geometry(GOLDEN_GEOMETRY)
        ok = geometry["canonical"] == GOLDEN_GEOMETRY_CANONICAL
        record(
            "几何规范串与样例逐字一致",
            ok,
            "" if ok else f"实际 {geometry['canonical']}",
        )
        ok = geometry["geometry_id"] == GOLDEN_GEOMETRY_ID
        record(
            "geometry_id 与冻结值一致",
            ok,
            "" if ok else f"实际 {geometry['geometry_id']}，期望 {GOLDEN_GEOMETRY_ID}",
        )
        condition = identify_condition(GOLDEN_CONDITION)
        ok = (
            condition["canonical"] == GOLDEN_CONDITION_CANONICAL
            and condition["condition_id"] == GOLDEN_CONDITION_ID
        )
        record(
            "工况规范串与 condition_id 一致",
            ok,
            "" if ok else f"实际 {condition['condition_id']} / {condition['canonical']}",
        )

        # 幂等：再规范化一次结果不变（量化后重入不漂移）
        again = identify_geometry(geometry["params"])
        record(
            "规范化幂等",
            again["canonical"] == geometry["canonical"],
            "二次规范化结果一致" if again["canonical"] == geometry["canonical"] else again["canonical"],
        )

        # 钳位先于哈希：0.0005 与 0.001 必须是同一个 geometry_id
        clamped = dict(GOLDEN_GEOMETRY, C_TW=0.0005)
        tips = dict(GOLDEN_GEOMETRY, C_TW=0.001)
        left = identify_geometry(clamped)
        right = identify_geometry(tips)
        record(
            "梢弦钳位在哈希之前",
            left["geometry_id"] == right["geometry_id"],
            "" if left["geometry_id"] == right["geometry_id"]
            else f"{left['geometry_id']} != {right['geometry_id']}",
        )
        record(
            "钳位会给出提示",
            bool(left["notes"]),
            left["notes"][0] if left["notes"] else "没有任何提示，钳位可能没生效",
        )

        # -0.0 与 0.0 同一个身份
        negative_zero = identify_condition({"mach": 0.0, "alpha_deg": -0.0, "altitude_m": 0})
        record(
            "-0.0 与 0.0 同一身份",
            "alpha_deg=0.000000" in negative_zero["canonical"],
            negative_zero["canonical"],
        )

        # 几何级 run_id 与按工况 run_id 的拆解
        shared = make_run_id(geometry["geometry_id"], SHARED_CONDITION_ID)
        scoped = make_run_id(geometry["geometry_id"], condition["condition_id"], {shared})
        record("run_id 三段可反向拆解", split_run_id(scoped) == (
            geometry["geometry_id"], condition["condition_id"], scoped.rsplit(RUN_ID_SEPARATOR, 1)[1]
        ), scoped)
        record(
            "run_id 撞车时时间戳顺延",
            scoped != shared and split_run_id(shared)[1] == SHARED_CONDITION_ID,
            f"{shared} → {scoped}",
        )
    except (ValueError, RuntimeError) as exc:
        record("规范化可用", False, str(exc))

    # ---- 约束是否真的在拦人（在内存库里试写，不碰真库）----
    memory = sqlite3.connect(":memory:")
    memory.row_factory = sqlite3.Row
    memory.execute("PRAGMA foreign_keys = ON")
    try:
        create_schema(memory)
        insert_geometry = _insert_geometry_sql()
        identity = identify_geometry(GOLDEN_GEOMETRY)
        condition_identity = identify_condition(GOLDEN_CONDITION)

        ok, detail = _expect_ok(
            memory, insert_geometry, _geometry_row(identity), "合法六边形几何可写入"
        )
        record("合法几何可写入", ok, detail)

        mixed = _geometry_row(
            identity,
            geometry_id="gmixed00000000",
            canonical="mixed",
            fin_section_type="hex",
            le_radius_index=6,
            max_thickness_tenths=3,
        )
        ok, detail = _expect_error(memory, insert_geometry, mixed, "hex 混写 NACA 参数")
        record("翼型参数互斥由 CHECK 拦住", ok, detail)

        bad_nose = _geometry_row(identity, geometry_id="gbadnose00000", canonical="badnos", nose_shape="blunt")
        ok, detail = _expect_error(memory, insert_geometry, bad_nose, "非法头型")
        record("头型枚举由 CHECK 拦住", ok, detail)

        naca = dict(
            GOLDEN_GEOMETRY,
            fin_section_type="naca",
            le_radius_index=6,
            max_thickness_tenths=3,
        )
        naca.pop("flat_percent")
        naca.pop("le_radius")
        naca_identity = identify_geometry(naca)
        ok, detail = _expect_ok(memory, insert_geometry, _geometry_row(naca_identity), "合法 NACA 几何可写入")
        record("合法 NACA 几何可写入", ok, detail)

        # 实测质心是几何表的派生列：写得进去、读得回来，而且不改变身份。
        centroid_written, centroid_detail = False, ""
        try:
            centroid_written = set_geometry_centroid(
                memory, identity["geometry_id"],
                {"cg_x_m": 2.0, "cg_y_m": 0.0, "cg_z_m": 0.0},
            )
            stored = memory.execute(
                "SELECT cg_x_m, cg_y_m, cg_z_m, canonical FROM geometry WHERE geometry_id=?",
                (identity["geometry_id"],),
            ).fetchone()
            centroid_detail = (
                f"x={stored['cg_x_m']} y={stored['cg_y_m']} z={stored['cg_z_m']}"
                if stored is not None else "读不回来"
            )
            centroid_written = (centroid_written and stored is not None
                                and stored["cg_x_m"] == 2.0
                                and stored["canonical"] == identity["canonical"])
        except WriteError as exc:
            centroid_detail = str(exc)
        record("实测质心写进几何表", centroid_written, centroid_detail)

        bad_centroids = (
            ({"cg_x_m": 5.0, "cg_y_m": 0.0, "cg_z_m": 0.0}, "轴向超出 L"),
            ({"cg_x_m": 2.0, "cg_y_m": 0.02, "cg_z_m": 0.0}, "侧向偏离轴线"),
            ({"cg_x_m": 2.0}, "缺分量"),
            (None, "缺质心"),
        )
        missed = []
        for bad, label in bad_centroids:
            try:
                set_geometry_centroid(memory, identity["geometry_id"], bad)
            except WriteError:
                continue
            missed.append(label)
        record("越界或缺失的质心被拒绝", not missed,
               "四种都拒绝" if not missed else f"漏掉：{missed}")

        memory.execute(
            "INSERT INTO condition(condition_id, mach, alpha_deg, altitude_m, canonical, label, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                condition_identity["condition_id"], condition_identity["params"]["mach"],
                condition_identity["params"]["alpha_deg"], condition_identity["params"]["altitude_m"],
                condition_identity["canonical"], "C00", utc_sql(),
            ),
        )
        memory.commit()

        now = utc_sql()
        condition_id = condition_identity["condition_id"]
        ok, detail = _expect_error(
            memory,
            "INSERT INTO cfd_task(geometry_id, condition_id, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?)",
            (identity["geometry_id"], condition_id, "bogus", now, now),
            "非法任务状态",
        )
        record("任务状态只能取三态", ok, detail)

        # 已删除的旧词必须真被 CHECK 挡住，而不只是文档里删掉
        old_words = ("running", "reviewing", "rejected", "passed", "cancelled")
        accepted = []
        for word in old_words:
            ok, _ = _expect_error(
                memory,
                "INSERT INTO cfd_task(geometry_id, condition_id, status, created_at, updated_at)"
                " VALUES(?,?,?,?,?)",
                (identity["geometry_id"], condition_id, word, now, now),
                f"旧状态词 {word}",
            )
            if not ok:
                accepted.append(word)
        record("旧状态词全部被 CHECK 拒绝", not accepted,
               "五个旧词都插不进去" if not accepted else f"仍可写入：{accepted}")

        # 网格按工况：同一 (几何, 工况) 只能一行；几何级三列已不存在
        ok, detail = _expect_ok(
            memory,
            "INSERT INTO mesh_task(geometry_id, condition_id, status, first_height_mm,"
            " n_layers, growth_rate, created_at, updated_at) VALUES(?,?,'pending',?,?,?,?,?)",
            (identity["geometry_id"], condition_id, 0.0021, 18, 1.2, now, now),
            "网格任务按工况可写入",
        )
        record("网格任务按工况可写入", ok, detail)
        ok, detail = _expect_error(
            memory,
            "INSERT INTO mesh_task(geometry_id, condition_id, status, created_at, updated_at)"
            " VALUES(?,?,'pending',?,?)",
            (identity["geometry_id"], condition_id, now, now),
            "同一 (几何, 工况) 的第二行网格",
        )
        record("同一 (几何, 工况) 只能有一行网格", ok, detail)
        state_columns = {row["name"] for row in memory.execute("PRAGMA table_info(geometry_state)")}
        record("几何状态表不再有网格三列", not (state_columns & set(RETIRED_COLUMNS["geometry_state"])),
               "已废弃到 mesh_task" if not (state_columns & set(RETIRED_COLUMNS["geometry_state"]))
               else f"仍存在 {sorted(state_columns & set(RETIRED_COLUMNS['geometry_state']))}")

        ok, detail = _expect_error(
            memory,
            "INSERT INTO cfd_task(geometry_id, condition_id, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?)",
            ("gnotexist00000", condition_id, "pending", now, now),
            "不存在的 geometry_id",
        )
        record("外键拦住孤儿任务", ok, detail)

        ok, detail = _expect_error(
            memory,
            "INSERT INTO control(target, state, updated_at, updated_by) VALUES(?,?,?,?)",
            ("workflow:high", "resume", now, "check"),
            "非法启停状态",
        )
        record("启停状态只能取 running/paused/stopped", ok, detail)

        ok, detail = _expect_error(
            memory,
            "INSERT INTO review(review_id, kind, geometry_id, run_id, reviewer, verdict, checks_json, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            ("rbad000000000", "flight", "g1", "r1", "check", "passed", "[]", now),
            "非法审核种类",
        )
        record("审核种类只能取四种", ok, detail)

        ok, detail = _expect_ok(
            memory,
            "INSERT INTO cfd_task(geometry_id, condition_id, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?)",
            (identity["geometry_id"], condition_id, "pending", now, now),
            "合法任务可写入",
        )
        record("合法任务可写入", ok, detail)

        # 领取标记：同一行只能被领一次（UPDATE ... WHERE claimed_at IS NULL 的 rowcount）
        first = memory.execute(
            "UPDATE cfd_task SET claimed_at=? WHERE geometry_id=? AND condition_id=?"
            " AND claimed_at IS NULL", (now, identity["geometry_id"], condition_id))
        memory.commit()
        second = memory.execute(
            "UPDATE cfd_task SET claimed_at=? WHERE geometry_id=? AND condition_id=?"
            " AND claimed_at IS NULL", (now, identity["geometry_id"], condition_id))
        memory.rollback()
        record("claimed_at 原子领取只成功一次",
               first.rowcount == 1 and second.rowcount == 0,
               f"第一次 rowcount={first.rowcount}，第二次 rowcount={second.rowcount}")

        # 修理计时：置 repairing 写起点、离开 repairing 清起点。
        # 这是"修理超时按 repair_started_at 判断"的前提，必须在建表阶段就成立。
        run = run_target("fluent_solution", identity["geometry_id"], condition_id)
        mark_task(memory, run, "repairing")
        started = memory.execute(
            f"SELECT {REPAIR_STARTED_COLUMN} AS s FROM cfd_task"
            " WHERE geometry_id=? AND condition_id=?",
            (identity["geometry_id"], condition_id)).fetchone()
        started_ok = started is not None and bool(started["s"])
        mark_task(memory, run, "queued")
        cleared = memory.execute(
            f"SELECT {REPAIR_STARTED_COLUMN} AS s FROM cfd_task"
            " WHERE geometry_id=? AND condition_id=?",
            (identity["geometry_id"], condition_id)).fetchone()
        cleared_ok = cleared is not None and cleared["s"] is None
        record("置 repairing 记起点、离开 repairing 清起点",
               started_ok and cleared_ok,
               f"repairing 时 {started['s'] if started else None}，"
               f"queued 时 {cleared['s'] if cleared else None}")

        # 2→3 迁移修的正是"一份几何级旧网格被当成四个工况都过审"：
        # 这里造一条几何级 run_id + 空边界层设计值的行，确认修复会把它改成 pending
        # 且清空 run_id / mesh_path（留着任一个，下游都会当成交付物）。
        geometry_id = identity["geometry_id"]
        other_condition = "c0000000000ff"
        memory.execute(
            "INSERT INTO condition(condition_id, mach, alpha_deg, altitude_m, canonical, label,"
            " created_at) VALUES(?,?,?,?,?,?,?)",
            (other_condition, 0.8, 0.0, 5000.0, "mach=0.800000|alpha_deg=0.000000|"
                                              "altitude_m=5000.000000", "C01", now))
        legacy_run = f"{geometry_id}{RUN_ID_SEPARATOR}{SHARED_CONDITION_ID}{RUN_ID_SEPARATOR}20261031T111141Z"
        memory.execute(
            "INSERT INTO mesh_task(geometry_id, condition_id, status, run_id, mesh_path,"
            " created_at, updated_at) VALUES(?,?,'queued',?,?,?,?)",
            (geometry_id, other_condition, legacy_run,
             "data_lib/runs/high/fluent_meshing/legacy/missile_mesh.msh.h5", now, now))
        memory.commit()
        fixed = repair_mesh_migration(memory)
        after = memory.execute(
            "SELECT status, run_id, mesh_path FROM mesh_task"
            " WHERE geometry_id=? AND condition_id=?",
            (geometry_id, other_condition)).fetchone()
        again = repair_mesh_migration(memory)
        legacy_ok = (
            len(fixed["reset"]) == 1
            and after is not None and after["status"] == "pending"
            and after["run_id"] is None and after["mesh_path"] is None
            # 幂等：第二次跑不应该再有可修的行
            and not again["reset"]
        )
        record("旧几何级网格被改回 pending 且清空产物列", legacy_ok,
               f"修复 {len(fixed['reset'])} 行、二次运行 {len(again['reset'])} 行；"
               f"结果 {after['status'] if after else '行不存在'} / "
               f"run_id {after['run_id'] if after else '—'} / "
               f"mesh_path {after['mesh_path'] if after else '—'}")

        # 放弃领取：清 claimed_at / run_id 并注销 pid 登记，任务回 pending
        memory.execute(
            "UPDATE mesh_task SET claimed_at=?, run_id=? WHERE geometry_id=?"
            " AND condition_id=?", (now, legacy_run, geometry_id, other_condition))
        memory.commit()
        register_task_process(memory, legacy_run, 999999)     # 不存在的 pid：一定判死
        released = abandon_claim(memory, legacy_run)
        row = memory.execute(
            "SELECT status, claimed_at, run_id FROM mesh_task"
            " WHERE geometry_id=? AND condition_id=?",
            (geometry_id, other_condition)).fetchone()
        left = memory.execute(
            "SELECT COUNT(*) FROM task_process WHERE run_id=?", (legacy_run,)).fetchone()[0]
        abandon_ok = (
            row is not None and row["status"] == "pending"
            and row["claimed_at"] is None and row["run_id"] is None and left == 0
            and bool(released["report_id"])
        )
        record("放弃领取清 claimed_at / run_id 并注销 pid", abandon_ok,
               f"结果 {row['status'] if row else '行不存在'} / "
               f"claimed_at {row['claimed_at'] if row else '—'} / "
               f"task_process 残留 {left} 行" if not abandon_ok
               else f"回 pending，上报 {released['report_id']}")
    finally:
        memory.close()

    return _print_results(results)


def _print_results(results: list[tuple[str, bool, str]]) -> int:
    width = max(len(name) for name, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        mark = "[ ok ]" if ok else "[FAIL]"
        if not ok:
            failed += 1
        line = f"{mark} {name.ljust(width)}"
        if detail:
            line = f"{line}  {detail}"
        print(line)
    print()
    if failed:
        print(f"共 {len(results)} 项，{failed} 项不通过。")
        return 1
    print(f"共 {len(results)} 项，全部通过。")
    return 0


# =============================================================================
# 9. 命令：identify（调试用，只读）
# =============================================================================
def _load_json_arg(inline: str | None, path: str | None, label: str) -> dict | None:
    """读 JSON 参数。Windows 的命令行会把内联 JSON 里的双引号吃掉，所以也支持从文件读。"""
    if inline and path:
        raise SystemExit(f"{label}：--{label}-json 与 --{label}-file 只能给一个。")
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return json.loads(inline) if inline else None


def cmd_identify(args) -> int:
    payload: dict = {}
    geometry = _load_json_arg(args.geometry_json, args.geometry_file, "geometry")
    condition = _load_json_arg(args.condition_json, args.condition_file, "condition")
    if geometry is not None:
        payload["geometry"] = geometry
    if condition is not None:
        payload["condition"] = condition
    if not payload:
        raise SystemExit(
            "需要 --geometry-json / --geometry-file，或 --condition-json / --condition-file。\n"
            "16 个几何参数示例见 data_lib/datasets/SQLite/README.md。"
        )
    result = normalize(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


# =============================================================================
# 9b. 命令：centroid（只读，打印 SpaceClaim 实测质心）
# =============================================================================
def cmd_centroid(args) -> int:
    """读实测质心：过审的建模运行经 `record_review` 把它写进 `geometry` 的派生三列。

    坐标口径与 DATCOM 的 XCG、点云的 x 一致：x 从弹尖（x = 0）向尾部量，单位 m；
    y / z 是侧向分量。三列不参与 `geometry_id`，所以空着（`measured=false`）不影响身份。
    """
    conn = connect(resolve_database(args.database))
    try:
        if args.geometry_id:
            rows = conn.execute(
                "SELECT geometry_id, label, L, cg_x_m, cg_y_m, cg_z_m FROM geometry"
                " WHERE geometry_id=?", (args.geometry_id,)).fetchall()
            if not rows:
                raise SystemExit(f"库里没有几何 {args.geometry_id}")
        else:
            rows = conn.execute(
                "SELECT geometry_id, label, L, cg_x_m, cg_y_m, cg_z_m FROM geometry"
                " ORDER BY geometry_id").fetchall()
    finally:
        conn.close()
    payload = [
        {
            "geometry_id": row["geometry_id"],
            "label": row["label"],
            "L_m": row["L"],
            "cg_x_m": row["cg_x_m"],
            "cg_y_m": row["cg_y_m"],
            "cg_z_m": row["cg_z_m"],
            "measured": row["cg_x_m"] is not None,
        }
        for row in rows
    ]
    print(json.dumps(payload[0] if args.geometry_id else payload,
                     ensure_ascii=False, indent=2))
    return 0


# =============================================================================
# 10. 写库：登记、领取、上报、启停（watch.py / control.py / dispatch.py 都调用这里，
#     所以"所有写操作只经 db.py"在这一层成立，而不只是"只经 db.py 的命令行"）
# =============================================================================
class WriteError(RuntimeError):
    """写入被业务规则拒绝（不是 sqlite 报错）。"""


def _geometry_columns() -> tuple[str, ...]:
    return (
        "geometry_id", *GEOMETRY_LENGTH_KEYS,
        "fin_section_type", "thickness_percent", "flat_percent", "le_radius",
        "le_radius_index", "max_thickness_tenths", "nose_shape",
        "canonical", "label", "created_at",
    )


GEOMETRY_INSERT_SQL = (
    f"INSERT INTO geometry({', '.join(_geometry_columns())}) "
    f"VALUES({', '.join('?' for _ in _geometry_columns())})"
)


def register_geometry(conn: sqlite3.Connection, identity: dict, label: str | None = None) -> bool:
    """登记一个几何身份。已存在则核对规范串，返回是否新增。

    规范串不一致 = 同一个 `geometry_id` 对应两个外形，说明规范化或位数出了 bug，
    属于硬错误，绝不静默覆盖。
    """
    row = conn.execute(
        "SELECT canonical, label FROM geometry WHERE geometry_id = ?",
        (identity["geometry_id"],),
    ).fetchone()
    if row is not None:
        if row["canonical"] != identity["canonical"]:
            raise WriteError(
                f"geometry_id 碰撞：{identity['geometry_id']} 已存在，但规范串不同。\n"
                f"  库内：{row['canonical']}\n  本次：{identity['canonical']}\n"
                "这属于硬错误，需要人工确认后加长 ID 位数，不会自动覆盖。"
            )
        if label and not row["label"]:
            conn.execute(
                "UPDATE geometry SET label = ? WHERE geometry_id = ?",
                (label, identity["geometry_id"]),
            )
        return False
    params = identity["params"]
    values = {
        "geometry_id": identity["geometry_id"],
        **{key: params[key] for key in GEOMETRY_LENGTH_KEYS},
        "fin_section_type": params[FIN_SECTION_KEY],
        "thickness_percent": params["thickness_percent"],
        "flat_percent": params.get("flat_percent"),
        "le_radius": params.get("le_radius"),
        "le_radius_index": params.get("le_radius_index"),
        "max_thickness_tenths": params.get("max_thickness_tenths"),
        "nose_shape": params[NOSE_SHAPE_KEY],
        "canonical": identity["canonical"],
        "label": label,
        "created_at": utc_sql(),
    }
    conn.execute(GEOMETRY_INSERT_SQL, tuple(values[key] for key in _geometry_columns()))
    return True


def register_condition(conn: sqlite3.Connection, identity: dict, label: str | None = None) -> bool:
    """登记一个工况身份。`label`（`C01`–`C30`）只是可读标签，不参与身份。"""
    row = conn.execute(
        "SELECT canonical, label FROM condition WHERE condition_id = ?",
        (identity["condition_id"],),
    ).fetchone()
    if row is not None:
        if row["canonical"] != identity["canonical"]:
            raise WriteError(
                f"condition_id 碰撞：{identity['condition_id']} 已存在，但规范串不同。\n"
                f"  库内：{row['canonical']}\n  本次：{identity['canonical']}"
            )
        if label and not row["label"]:
            conn.execute(
                "UPDATE condition SET label = ? WHERE condition_id = ?",
                (label, identity["condition_id"]),
            )
        return False
    params = identity["params"]
    conn.execute(
        "INSERT INTO condition(condition_id, mach, alpha_deg, altitude_m, canonical, label, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (
            identity["condition_id"], params["mach"], params["alpha_deg"],
            params["altitude_m"], identity["canonical"], label, utc_sql(),
        ),
    )
    return True


def set_geometry_centroid(conn: sqlite3.Connection, geometry_id: str,
                          centroid: dict | None) -> bool:
    """把 SpaceClaim 实测质心写进 `geometry` 的派生三列（不进规范串、不参与身份哈希）。

    `centroid` 是模型坐标换算到弹尖基准后的 `{cg_x_m, cg_y_m, cg_z_m}`（米）：
    x 从弹尖（x = 0）向尾部量，必须落在 (0, L) 内；y / z 是侧向分量，十字对称外形的
    实测值应落在轴上，超过 `CENTROID_SYMMETRY_TOLERANCE_M` 就拒绝写入。
    """
    row = conn.execute("SELECT L FROM geometry WHERE geometry_id=?", (geometry_id,)).fetchone()
    if row is None:
        raise WriteError(f"库里没有几何 {geometry_id}，无法写入实测质心")
    if not isinstance(centroid, dict):
        raise WriteError(
            "缺少实测质心（{}）：建模审核通过时必须带上 SpaceClaim 实测的整弹质心".format(
                ", ".join(GEOMETRY_CENTROID_KEYS)))
    values: dict[str, float] = {}
    for key in GEOMETRY_CENTROID_KEYS:
        raw = centroid.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise WriteError(f"实测质心 {key} 必须是数值，收到 {raw!r}")
        number = float(raw)
        if not math.isfinite(number):
            raise WriteError(f"实测质心 {key} 必须是有限值，收到 {raw!r}")
        values[key] = number
    length = float(row["L"])
    if not 0.0 < values["cg_x_m"] < length:
        raise WriteError(
            f"实测质心轴向位置 {values['cg_x_m']:.6f} m 不在 (0, L={length:.6f}) 内")
    for key in ("cg_y_m", "cg_z_m"):
        if abs(values[key]) > CENTROID_SYMMETRY_TOLERANCE_M:
            raise WriteError(
                f"实测质心 {key} = {values[key]:.6f} m 偏离弹轴超过 "
                f"{CENTROID_SYMMETRY_TOLERANCE_M:g} m：十字对称外形的质心应落在轴上")
    conn.execute(
        "UPDATE geometry SET cg_x_m=?, cg_y_m=?, cg_z_m=? WHERE geometry_id=?",
        (values["cg_x_m"], values["cg_y_m"], values["cg_z_m"], geometry_id),
    )
    return True


def ensure_geometry_state(conn: sqlite3.Connection, geometry_id: str) -> bool:
    """路径状态行随几何行一起建；已存在不动（状态推进由 watch/dispatch 负责）。"""
    exists = conn.execute(
        "SELECT 1 FROM geometry_state WHERE geometry_id = ?", (geometry_id,)
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO geometry_state(geometry_id, updated_at) VALUES(?,?)",
        (geometry_id, utc_sql()),
    )
    return True


def register_task(
    conn: sqlite3.Connection,
    table: str,
    geometry_id: str,
    condition_id: str,
    origin: str = DEFAULT_ORIGIN,
    round_id: str | None = None,
) -> bool:
    """登记一条任务行（`cfd_task` / `datcom_task`）。已存在则不动，返回是否新增。

    追加型：同一个 `(geometry_id, condition_id)` 不会被第二次导入覆盖，
    所以重复导入同一份输入表行数不变。
    """
    if table not in ("cfd_task", "datcom_task"):
        raise WriteError(f"未知任务表：{table}")
    if origin not in ORIGIN_CHOICES:
        raise WriteError(f"origin 只能是 {ORIGIN_CHOICES}，收到 {origin!r}")
    exists = conn.execute(
        f"SELECT 1 FROM {table} WHERE geometry_id = ? AND condition_id = ?",
        (geometry_id, condition_id),
    ).fetchone()
    if exists:
        return False
    stamp = utc_sql()
    conn.execute(
        f"INSERT INTO {table}(geometry_id, condition_id, status, origin, round_id, created_at, updated_at)"
        " VALUES(?,?,'pending',?,?,?,?)",
        (geometry_id, condition_id, origin, round_id, stamp, stamp),
    )
    return True


def register_task_for_stages(conn: sqlite3.Connection, geometry_id: str, condition_id: str,
                             origin: str = DEFAULT_ORIGIN, round_id: str | None = None) -> list[str]:
    """新建的几何/工况两个阶段都要跑：高保真一条 `cfd_task`，低保真一条 `datcom_task`。"""
    created = [
        table for table in ("cfd_task", "datcom_task")
        if register_task(conn, table, geometry_id, condition_id, origin, round_id)
    ]
    return created


# ---- 网格任务：按工况建行（watch.py 在建模过审后调用）----
def ensure_mesh_tasks(conn: sqlite3.Connection, geometry_id: str) -> list[str]:
    """为一个几何的每个高保真工况建 `mesh_task` 行（`pending`），返回新建的 condition_id。

    网格按 `(geometry_id, condition_id)` 一行：同一个几何在不同来流下需要的边界层
    首层高度不同（见 `fluent_meshing/boundary_layer.py`），所以不能共用一份网格。

    已存在的行不动——重复调用幂等，watch 每轮都能安全地调一次。
    """
    condition_ids = [row["condition_id"] for row in conn.execute(
        "SELECT DISTINCT condition_id FROM cfd_task WHERE geometry_id=?"
        " ORDER BY condition_id", (geometry_id,))]
    created: list[str] = []
    stamp = utc_sql()
    for condition_id in condition_ids:
        exists = conn.execute(
            "SELECT 1 FROM mesh_task WHERE geometry_id=? AND condition_id=?",
            (geometry_id, condition_id)).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO mesh_task(geometry_id, condition_id, status, created_at, updated_at)"
            " VALUES(?,?,'pending',?,?)", (geometry_id, condition_id, stamp, stamp))
        created.append(condition_id)
    if created:
        conn.commit()
    return created


def geometries_ready_for_mesh(conn: sqlite3.Connection) -> list[str]:
    """建模已审核通过（`model_status='queued'` 且有 `model_path`）、且有工况的几何。"""
    return [row["geometry_id"] for row in conn.execute(
        "SELECT s.geometry_id FROM geometry_state s"
        " WHERE s.model_status='queued' AND s.model_path IS NOT NULL"
        "   AND EXISTS (SELECT 1 FROM cfd_task t WHERE t.geometry_id = s.geometry_id)"
        " ORDER BY s.geometry_id")]


def set_mesh_design(conn: sqlite3.Connection, geometry_id: str, condition_id: str,
                    design: dict) -> None:
    """把边界层设计值写进 `mesh_task` 行（跑网格之前写，review 按同一套值复核）。

    `design` 是 `boundary_layer.design_boundary_layer(...).as_dict()`（或它的子集），
    键名与列名一一对应。没给的列保持原值。
    """
    mapping = {
        "first_height_mm": design.get("first_height_mm"),
        "n_layers": design.get("n_layers"),
        "growth_rate": design.get("growth_rate"),
        "y_plus_target": design.get("y_plus_target"),
        "re": design.get("re"),
        "cf": design.get("cf"),
        "u_tau": design.get("u_tau_m_s", design.get("u_tau")),
        "delta_m": design.get("delta_m"),
    }
    sets = ", ".join(f"{key}=?" for key in mapping)
    conn.execute(
        f"UPDATE mesh_task SET {sets}, updated_at=? WHERE geometry_id=? AND condition_id=?",
        (*mapping.values(), utc_sql(), geometry_id, condition_id))
    conn.commit()


def mesh_design(conn: sqlite3.Connection, geometry_id: str,
                condition_id: str) -> dict | None:
    """读回 `mesh_task` 行里记录的边界层设计值。"""
    row = conn.execute(
        "SELECT first_height_mm, n_layers, growth_rate, y_plus_target, re, cf, u_tau, delta_m"
        " FROM mesh_task WHERE geometry_id=? AND condition_id=?",
        (geometry_id, condition_id)).fetchone()
    return dict(row) if row is not None else None


# ---- 阶段 → 行定位 ----
# 每个阶段的"任务行"落在哪张表、状态列叫什么、是否按工况。
# 建模是几何级的（`geometry_state`）；网格/求解/低保真都是按 `(几何, 工况)` 一行。
STAGE_TARGET = {
    "spaceclaim": {"table": "geometry_state", "status": "model_status", "scoped": False},
    "fluent_meshing": {"table": "mesh_task", "status": "status", "scoped": True},
    "fluent_solution": {"table": "cfd_task", "status": "status", "scoped": True},
    "datcom": {"table": "datcom_task", "status": "status", "scoped": True},
}
# 可被领取执行的状态：`pending` 是"待处理"（含新建、修理完、脚本跑完等审核）。
# `queued` 已经在审核通过之后，不再重跑；`repairing` 由子智能体修，不由脚本领。
CLAIMABLE_STATUSES = ("pending",)
# 领取前的前置闸门（SQL 片段，作用在表的别名 t 上）。
# 网格必须等建模过审：没有过审的模型就没有可喂给 Fluent 的 .scdoc。
CLAIM_GATES = {
    "fluent_meshing": (
        "EXISTS (SELECT 1 FROM geometry_state s WHERE s.geometry_id = t.geometry_id"
        " AND s.model_status='queued' AND s.model_path IS NOT NULL)"
    ),
}


def _stage_of_table(table: str) -> str:
    for stage, spec in STAGE_TARGET.items():
        if spec["table"] == table:
            return stage
    raise WriteError(f"未知任务表：{table}")


def run_target(stage: str, geometry_id: str, condition_id: str | None = None) -> dict:
    """把 `(阶段, 几何, 工况)` 变成一条可交给 `mark_task` 的行描述。"""
    spec = STAGE_TARGET[stage]
    if spec["scoped"] and not condition_id:
        raise WriteError(f"{stage} 是按工况的任务，必须给 condition_id")
    return {"stage": stage, "table": spec["table"], "field": spec["status"],
            "geometry_id": geometry_id,
            "condition_id": condition_id if spec["scoped"] else None}


def _run_column(table: str) -> str:
    """建模的运行号列叫 `model_run_id`，其余三张表都叫 `run_id`。"""
    return "model_run_id" if table == "geometry_state" else "run_id"


def _claim_rows(conn: sqlite3.Connection, stage: str, limit: int,
                statuses: tuple[str, ...] = CLAIMABLE_STATUSES) -> list[dict]:
    """原子领取：`UPDATE ... WHERE claimed_at IS NULL` + `rowcount` 判成功。

    两条 watch / dispatch 进程同时看到同一条任务时，只有一个的 `rowcount` 是 1，
    另一个是 0 —— 这就是"不重复启动第二个进程"的唯一保证，不需要 running 状态词。
    领取时同时写入本次的 `run_id`，所以 `claimed_at + run_id` 一起代表"谁在跑哪一次"。
    """
    spec = STAGE_TARGET[stage]
    table, status_column = spec["table"], spec["status"]
    run_column = _run_column(table)
    placeholders = ", ".join("?" for _ in statuses)
    key_columns = "t.geometry_id, t.condition_id" if spec["scoped"] else "t.geometry_id"
    gate = CLAIM_GATES.get(stage)
    gate_sql = f" AND {gate}" if gate else ""
    rows = conn.execute(
        f"SELECT t.rowid AS rid, {key_columns} FROM {table} t"
        f" WHERE t.{status_column} IN ({placeholders}) AND t.claimed_at IS NULL{gate_sql}"
        f" ORDER BY {key_columns} LIMIT ?",
        (*statuses, max(1, int(limit))),
    ).fetchall()
    claimed: list[dict] = []
    stamp = utc_sql()
    for row in rows:
        condition_id = row["condition_id"] if spec["scoped"] else None
        run_id = make_run_id(row["geometry_id"], condition_id or SHARED_CONDITION_ID,
                             taken_run_ids(conn) | {item["run_id"] for item in claimed})
        cursor = conn.execute(
            f"UPDATE {table} SET claimed_at=?, {run_column}=?, updated_at=?"
            f" WHERE rowid=? AND {status_column} IN ({placeholders}) AND claimed_at IS NULL",
            (stamp, run_id, stamp, row["rid"], *statuses),
        )
        if cursor.rowcount != 1:                # 被别的进程抢先了，跳过
            continue
        claimed.append({"stage": stage, "table": table, "field": status_column,
                        "geometry_id": row["geometry_id"], "condition_id": condition_id,
                        "run_id": run_id})
    conn.commit()
    return claimed


def claim_model(conn: sqlite3.Connection, limit: int = 1) -> list[dict]:
    """领一条建模任务（`geometry_state`，几何级）。"""
    return _claim_rows(conn, "spaceclaim", limit)


def claim_mesh(conn: sqlite3.Connection, limit: int = 1) -> list[dict]:
    """领一条网格任务（`mesh_task`，按 `(几何, 工况)`）。"""
    return _claim_rows(conn, "fluent_meshing", limit)


def claim_solution(conn: sqlite3.Connection, limit: int = 1) -> list[dict]:
    """领一条求解任务。求解脚本未实现，`dispatch.py` 在领取之前就拒绝启动。"""
    return _claim_rows(conn, "fluent_solution", limit)


def claim_datcom_tasks(conn: sqlite3.Connection, limit: int = 1) -> list[dict]:
    """领一条低保真任务（按行；整批领取见 `claim_datcom_batch`）。"""
    return _claim_rows(conn, "datcom", limit)


def clear_claim(conn: sqlite3.Connection, table: str, geometry_id: str,
                condition_id: str | None = None, *, commit: bool = True) -> None:
    """任务结束时清 `claimed_at`（`run_id` 保留：它是"上一次跑的是哪一次"的证据）。"""
    if table not in {spec["table"] for spec in STAGE_TARGET.values()}:
        raise WriteError(f"未知任务表：{table}")
    if condition_id is None:
        conn.execute(f"UPDATE {table} SET claimed_at=NULL, updated_at=? WHERE geometry_id=?",
                     (utc_sql(), geometry_id))
    else:
        conn.execute(
            f"UPDATE {table} SET claimed_at=NULL, updated_at=?"
            " WHERE geometry_id=? AND condition_id=?",
            (utc_sql(), geometry_id, condition_id))
    if commit:
        conn.commit()


def abandon_claim(conn: sqlite3.Connection, run_id: str) -> dict:
    """放弃一次"领了却没跑完"的领取：清 `claimed_at`、注销 pid 登记、任务回 `pending`。

    与 `clear_claim` 的区别：`clear_claim` 是"跑完了，正常收尾"，产物与 `run_id` 都要
    留下；`abandon_claim` 是"这次运行作废"，所以连 `run_id` 也不留（它记的是作废的那一次）。
    产物文件不动，仍在 `runs/` 下作为证据。

    会**拒绝**仍然活着的进程：那说明任务真的在跑，清掉领取等于让它变成孤儿，
    而且同一个任务可能被第二次启动。要停它先用 `control.py stop --target task:<run_id>`。

    用 `--reset-report` 之外的场景：`repair-mesh-migration --release-claim` 也走它。
    返回 `{run_id, stage, geometry_id, condition_id, pid, report_id}`。
    """
    row = conn.execute("SELECT run_id, pid FROM task_process WHERE run_id=?", (run_id,)).fetchone()
    pid = int(row["pid"]) if row is not None else None
    if pid is not None and pid_alive(pid):
        raise WriteError(
            f"{run_id} 的进程 pid {pid} 还活着：先 control.py stop --target task:{run_id} 收掉它")
    run = resolve_run(conn, run_id)
    if run is None:
        # 库里已经没有对应任务（行被删/被 gc 清掉了）：只清 pid 登记，别假装改了状态
        conn.execute("DELETE FROM task_process WHERE run_id=?", (run_id,))
        conn.commit()
        return {"run_id": run_id, "stage": None, "geometry_id": None, "condition_id": None,
                "pid": pid, "report_id": None}

    table, condition_id = run["table"], run["condition_id"]
    stamp = utc_sql()
    where = "geometry_id=?" if condition_id is None else "geometry_id=? AND condition_id=?"
    params = (run["geometry_id"],) if condition_id is None else (run["geometry_id"], condition_id)
    run_column = _run_column(table)
    status_column = run["field"]
    if run_column not in _task_columns(conn, table):
        run_column = None
    sets = [f"{status_column}='pending'", "claimed_at=NULL", "updated_at=?"]
    values: list = [stamp]
    if run_column:
        sets.insert(0, f"{run_column}=NULL")
    if REPAIR_STARTED_COLUMN in _task_columns(conn, table):
        sets.append(f"{REPAIR_STARTED_COLUMN}=NULL")
    conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE {where}", (*values, *params))
    conn.execute("DELETE FROM task_process WHERE run_id=?", (run_id,))
    conn.commit()

    # 领取被放弃 = 这次运行没跑成，跟"被 stop 杀掉"一样需要人知道，所以开一张单；
    # 已有同任务 open 的单就复用，不会每轮开一张新的。
    report_id = record_report(
        conn, stage=run["stage"], geometry_id=run["geometry_id"], run_id=run_id,
        attempts=_attempts_of(conn, run), condition_id=condition_id, dedupe=True,
        last_error=f"领取 {run_id} 被放弃（pid {pid or '未登记'}），任务回 pending 待重跑",
    )
    return {"run_id": run_id, "stage": run["stage"], "geometry_id": run["geometry_id"],
            "condition_id": condition_id, "pid": pid, "report_id": report_id}


def is_claimed(conn: sqlite3.Connection, table: str, geometry_id: str,
               condition_id: str | None = None) -> bool:
    spec = STAGE_TARGET[_stage_of_table(table)]
    where = "geometry_id=?" if not spec["scoped"] else "geometry_id=? AND condition_id=?"
    params = (geometry_id,) if not spec["scoped"] else (geometry_id, condition_id)
    row = conn.execute(f"SELECT claimed_at FROM {table} WHERE {where}", params).fetchone()
    return bool(row and row["claimed_at"])


def taken_run_ids(conn: sqlite3.Connection) -> set[str]:
    """已经用掉的 run_id（进程登记 + 四个阶段的运行号），新的不能与它们重复。"""
    taken = {row["run_id"] for row in conn.execute("SELECT run_id FROM task_process")}
    taken |= {row[0] for row in conn.execute(
        "SELECT model_run_id FROM geometry_state WHERE model_run_id IS NOT NULL"
        " UNION SELECT run_id FROM mesh_task WHERE run_id IS NOT NULL"
        " UNION SELECT run_id FROM cfd_task WHERE run_id IS NOT NULL"
        " UNION SELECT run_id FROM datcom_task WHERE run_id IS NOT NULL")}
    return taken


def claim_datcom_batch(conn: sqlite3.Connection) -> dict | None:
    """领一批低保真任务：同一个 `geometry_id` + 同一个 `altitude_m` 用一次 DATCOM 调用算完。

    返回 `{"geometry_id", "altitude_m", "condition_ids", "run_id"}`。批里的每一行都走
    和单行相同的原子领取（`claimed_at IS NULL` + `rowcount`），所以两个 dispatch 不会
    领到同一行；任一行被别人抢先领走，就只领到自己拿到的那几条。
    """
    row = conn.execute(
        "SELECT t.geometry_id AS geometry_id, c.altitude_m AS altitude_m FROM datcom_task t"
        " JOIN condition c ON c.condition_id = t.condition_id"
        " WHERE t.status=? AND t.claimed_at IS NULL"
        " GROUP BY t.geometry_id, c.altitude_m"
        " ORDER BY t.geometry_id, c.altitude_m LIMIT 1",
        (CLAIMABLE_STATUSES[0],),
    ).fetchone()
    if row is None:
        return None
    geometry_id, altitude_m = row["geometry_id"], row["altitude_m"]
    candidates = [item["condition_id"] for item in conn.execute(
        "SELECT t.condition_id FROM datcom_task t"
        " JOIN condition c ON c.condition_id = t.condition_id"
        " WHERE t.status=? AND t.claimed_at IS NULL AND t.geometry_id=? AND c.altitude_m=?"
        " ORDER BY t.condition_id",
        (CLAIMABLE_STATUSES[0], geometry_id, altitude_m),
    )]
    run_id = make_run_id(geometry_id, SHARED_CONDITION_ID, taken_run_ids(conn))
    stamp = utc_sql()
    claimed: list[str] = []
    for condition_id in candidates:
        cursor = conn.execute(
            "UPDATE datcom_task SET claimed_at=?, run_id=?, updated_at=?"
            " WHERE geometry_id=? AND condition_id=? AND status=? AND claimed_at IS NULL",
            (stamp, run_id, stamp, geometry_id, condition_id, CLAIMABLE_STATUSES[0]),
        )
        if cursor.rowcount == 1:
            claimed.append(condition_id)
    conn.commit()
    if not claimed:
        return None
    return {"geometry_id": geometry_id, "altitude_m": altitude_m,
            "condition_ids": claimed, "run_id": run_id}


def export_batch_input(conn: sqlite3.Connection, geometry_id: str,
                       condition_ids: list[str], out_path: Path) -> Path:
    """给一次 DATCOM 批次写入口表（23+2 列，直接喂 `01_batch_datcom.py --input`）。"""
    placeholders = ", ".join("?" for _ in condition_ids)
    rows = conn.execute(
        f"SELECT t.geometry_id, t.condition_id, g.*, c.mach, c.alpha_deg, c.altitude_m,"
        f" c.label AS condition_label FROM datcom_task t"
        " JOIN geometry g ON g.geometry_id = t.geometry_id"
        " JOIN condition c ON c.condition_id = t.condition_id"
        f" WHERE t.geometry_id=? AND t.condition_id IN ({placeholders})"
        " ORDER BY c.mach, c.alpha_deg",
        (geometry_id, *condition_ids),
    ).fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding=CSV_ENCODING, newline="") as handle:
        writer = csv.writer(handle, lineterminator=CSV_NEWLINE)
        writer.writerow(EXPORT_CASES_HEADER)
        for row in rows:
            writer.writerow(
                [row["geometry_id"]]
                + [row[key] for key in (*GEOMETRY_LENGTH_KEYS, *CSV_FIN_COLUMNS, NOSE_SHAPE_KEY)]
                + [row["mach"], row["alpha_deg"], row["altitude_m"]]
                + [row["condition_id"], row["condition_label"]]
            )
    return out_path


# ---- 运行记录与僵死回收 ----
def pid_alive(pid: int) -> bool:
    """进程是否还活着。

    Windows 上不能用 `os.kill(pid, 0)`——它会真的调 TerminateProcess 杀进程。
    这里先走 `OpenProcess` + `GetExitCodeProcess`（不需要列进程权限，也不经过管道），
    拿不到再用 `tasklist` 兜底。**判断不明时一律返回 True**：宁可把僵死任务留给人处理，
    也不能把还在跑的算例判死、重复启动第二次。
    """
    pid = int(pid)
    if pid <= 0:
        return False
    if os.name == "nt":
        verdict = _pid_alive_via_kernel(pid)
        if verdict is not None:
            return verdict
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if completed.returncode != 0:
        return True                         # 查不动就当活着
    return str(pid) in (completed.stdout or "")


def _pid_alive_via_kernel(pid: int) -> bool | None:
    """`OpenProcess` 判定；无法判定（缺权限、API 不可用）时返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:                     # 非 Windows
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_query_limited_information = 0x1000
        still_active = 259
        error_invalid_parameter = 87
        error_access_denied = 5
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return False                # 这个 pid 确实不存在
            if error == error_access_denied:
                return True                 # 存在，只是不给查
            return None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return None


def resolve_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """由 `run_id` 反查它属于哪个阶段、哪个任务（`run_id` 本身不含阶段名）。

    顺序按阶段排：先几何级建模，再按工况的网格 / 求解 / 低保真。同一个 run_id 不会
    同时出现在两处（`make_run_id` 会撞库顺延）。
    """
    row = conn.execute(
        "SELECT geometry_id FROM geometry_state WHERE model_run_id = ?", (run_id,)).fetchone()
    if row is not None:
        return {"stage": "spaceclaim", "geometry_id": row["geometry_id"], "condition_id": None,
                "table": "geometry_state", "field": "model_status"}
    for stage, table in (("fluent_meshing", "mesh_task"),
                         ("fluent_solution", "cfd_task"),
                         ("datcom", "datcom_task")):
        row = conn.execute(
            f"SELECT geometry_id, condition_id FROM {table} WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is not None:
            return {"stage": stage, "geometry_id": row["geometry_id"],
                    "condition_id": row["condition_id"], "table": table, "field": "status"}
    return None


def _task_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def mark_task(
    conn: sqlite3.Connection,
    run: dict,
    status: str,
    *,
    bump_attempts: bool = False,
    run_id: str | None = None,
    result_dir: str | None = None,
    log_path: str | None = None,
    keep_claim: bool = False,
    repair_started_at: str | None = None,
) -> None:
    """把一条任务/几何阶段推到指定状态，并清掉领取标记（除非 `keep_claim`）。

    状态词表由 CHECK 兜底，这里只挡拼写错误。三态的含义是"排队位置"，不是"进程
    在不在跑"：所以 `pending` 既可以是新建待跑，也可以是脚本跑完等审核。
    `claimed_at` 的清理与状态推进写在同一事务里——否则会留下"状态已变、还挂着领取
    标记"的行，watch 的僵死回收会把它误判成进程崩了。

    `result_dir` / `log_path` 只在表里真有这两列时才写：`mesh_task` 的运行目录由
    `run_id` 唯一确定，不重复存列（存了就会和 `run_id` 漂移）。自己提交：状态推进
    必须当场落地，不能靠调用方后续 commit。

    `repair_started_at` 与状态同一个 UPDATE 写：置 `repairing` 时记下此刻（修理超时
    从这里算起，不是从 `updated_at`——那可能是几周前的历史时刻），离开 `repairing`
    就清成 NULL。重复置 `repairing` 会刷新时刻：每次重新开始修都该重新计时。
    显式传 `repair_started_at` 可以写一个指定的起点（验收"真的修理超时能报"时用，
    生产路径不传，一律取当前时刻）。
    """
    if status not in TASK_STATUSES:
        raise WriteError(f"未知状态：{status}（只能是 {TASK_STATUSES}）")
    table, field = run["table"], run["field"]
    columns = _task_columns(conn, table)
    stamp = utc_sql()
    claim_sql = "" if keep_claim else ", claimed_at=NULL"
    repair_value = (repair_started_at or stamp) if status == "repairing" else None
    repair_sql = (f", {REPAIR_STARTED_COLUMN}=?" if REPAIR_STARTED_COLUMN in columns else "")
    if table == "geometry_state":
        conn.execute(
            f"UPDATE geometry_state SET {field}=?, updated_at=?{claim_sql}{repair_sql}"
            " WHERE geometry_id=?",
            (status, stamp, *([repair_value] if repair_sql else []), run["geometry_id"]),
        )
        # 几何级建模用 model_run_id 反查是哪一次运行（run_id 本身不含阶段名）
        if run_id is not None:
            conn.execute(
                "UPDATE geometry_state SET model_run_id=? WHERE geometry_id=?",
                (run_id, run["geometry_id"]),
            )
        conn.commit()
        return
    where = "geometry_id=? AND condition_id=?"
    key = (run["geometry_id"], run["condition_id"])
    conn.execute(f"UPDATE {table} SET status=?, updated_at=?{claim_sql}{repair_sql} WHERE {where}",
                 (status, stamp, *([repair_value] if repair_sql else []), *key))
    if bump_attempts and "attempts" in columns:
        conn.execute(f"UPDATE {table} SET attempts=attempts+1 WHERE {where}", key)
    if run_id is not None:
        conn.execute(f"UPDATE {table} SET run_id=? WHERE {where}", (run_id, *key))
    if result_dir is not None and "result_dir" in columns:
        conn.execute(f"UPDATE {table} SET result_dir=? WHERE {where}", (result_dir, *key))
    if log_path is not None and "log_path" in columns:
        conn.execute(f"UPDATE {table} SET log_path=? WHERE {where}", (log_path, *key))
    conn.commit()


def record_report(
    conn: sqlite3.Connection,
    *,
    stage: str,
    geometry_id: str,
    run_id: str,
    attempts: int,
    last_error: str | None = None,
    condition_id: str | None = None,
    review_json: str | None = None,
    feedback_dir: str | None = None,
    dedupe: bool = False,
) -> str:
    """写一条上报单。`report_id = "p" + sha256(stage+run_id+created_at)[:12]`。

    `dedupe=True` 时先按 `stage + geometry_id + condition_id` 查一条还开着的单，
    有就直接返回它——`watch.py` 的修理超时扫描每轮都会发现同一行，不能每轮开一张新单。
    """
    if stage not in STAGES:
        raise WriteError(f"未知阶段：{stage}")
    if dedupe:
        existing = conn.execute(
            "SELECT report_id FROM report WHERE stage=? AND geometry_id=?"
            " AND condition_id IS ? AND status='open'",
            (stage, geometry_id, condition_id),
        ).fetchone()
        if existing is not None:
            return existing["report_id"]
    created = utc_sql()
    report_id = _unique_id(conn, "report", "report_id", ID_PREFIX["report"],
                           f"{stage}{run_id}{created}")
    conn.execute(
        "INSERT INTO report(report_id, stage, geometry_id, condition_id, attempts, last_error,"
        " review_json, feedback_dir, status, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,'open',?)",
        (report_id, stage, geometry_id, condition_id, attempts, last_error,
         review_json, feedback_dir, created),
    )
    conn.commit()
    return report_id


REQUEUE_STAGE_KIND = {
    "spaceclaim": "model",
    "fluent_meshing": "mesh",
    "fluent_solution": "solution",
    "datcom": "datcom",
}
# 能被人为重排的状态：`queued` 是审核通过，重排等于让它重跑，允许（人要重算）；
# `repairing` 是子智能体正在修，重排就是替它决定"照原参数再跑一次"，也允许。
REQUEUE_ALLOWED_STATUSES = TASK_STATUSES


def _requeue_target(conn: sqlite3.Connection, stage: str,
                    geometry_id: str, condition_id: str | None):
    """定位要重排的那一行，返回 `((table, column), status, run_id, claimed_at)`。

    建模读 `geometry_state`；网格 / 求解 / 低保真读各自的任务表。找不到就报错——
    不要静默返回"没有可重排的任务"。
    """
    spec = STAGE_TARGET[stage]
    table, column = spec["table"], spec["status"]
    if spec["scoped"]:
        if not condition_id:
            raise WriteError(f"{stage} 是按工况的任务，必须给 --condition-id")
        row = conn.execute(
            f"SELECT {column} AS status, run_id, claimed_at FROM {table}"
            " WHERE geometry_id=? AND condition_id=?", (geometry_id, condition_id)).fetchone()
        if row is None:
            raise WriteError(f"库里没有 {stage} 任务：{geometry_id} / {condition_id}")
    else:
        row = conn.execute(
            f"SELECT {column} AS status, model_run_id AS run_id, claimed_at FROM {table}"
            " WHERE geometry_id=?", (geometry_id,)).fetchone()
        if row is None:
            raise WriteError(f"库里没有几何 {geometry_id}")
    return (table, column), row["status"], row["run_id"], row["claimed_at"]


def requeue_task(
    conn: sqlite3.Connection,
    *,
    stage: str,
    geometry_id: str,
    condition_id: str | None = None,
    resolve_reports: bool = False,
    reason: str | None = None,
) -> dict:
    """把一条任务放回 `pending`，让 `watch.py` / `dispatch.py` 重新领它。

    只动"排队位置"和领取标记，**不改产物、不删审核历史、不重置重试次数**：重试次数
    是按 `review` 表里的拒绝记录算出来的，清它等于篡改历史。
    """
    if stage not in STAGES:
        raise WriteError(f"未知阶段：{stage}（只能是 {STAGES}）")
    (table, column), status, run_id, claimed_at = _requeue_target(
        conn, stage, geometry_id, condition_id)

    if claimed_at:
        pid_row = conn.execute("SELECT pid FROM task_process WHERE run_id=?",
                               (run_id,)).fetchone() if run_id else None
        if pid_row is not None and pid_alive(int(pid_row["pid"])):
            raise WriteError(
                f"{stage} / {geometry_id} 已被领取（pid {pid_row['pid']} 还活着）："
                "先 `control.py stop` 收掉进程再重排")
        # 领取标记还在但进程已死：清掉，否则新任务领不到
        conn.execute(f"UPDATE {table} SET claimed_at=NULL WHERE "
                     + ("geometry_id=?" if column == "model_status"
                        else "geometry_id=? AND condition_id=?"),
                     (geometry_id,) if column == "model_status" else (geometry_id, condition_id))

    kind = REQUEUE_STAGE_KIND[stage]
    rejections = _rejections_since_last_pass(conn, kind, geometry_id, condition_id)
    stamp = utc_sql()
    # run_id 保留：它记的是"哪一次被否了"，是证据；领取时会生成新的 run_id 覆盖它
    # repair_started_at 清成 NULL：重排后它已经不在 repairing，"这段修理"结束了。
    if column == "model_status":
        conn.execute(
            f"UPDATE geometry_state SET {column}='pending', {REPAIR_STARTED_COLUMN}=NULL,"
            " updated_at=? WHERE geometry_id=?",
            (stamp, geometry_id))
    else:
        conn.execute(
            f"UPDATE {table} SET status='pending', {REPAIR_STARTED_COLUMN}=NULL,"
            " updated_at=? WHERE geometry_id=? AND condition_id=?",
            (stamp, geometry_id, condition_id))

    resolved: list[str] = []
    if resolve_reports:
        where = "stage=? AND geometry_id=? AND status='open'"
        params: list = [stage, geometry_id]
        if condition_id:
            where += " AND condition_id=?"
            params.append(condition_id)
        for row in conn.execute(f"SELECT report_id FROM report WHERE {where}", params).fetchall():
            conn.execute(
                "UPDATE report SET status='resolved', resolution=?, resolved_at=?"
                " WHERE report_id=?", (reason or "人工重排", stamp, row["report_id"]))
            resolved.append(row["report_id"])
    conn.commit()
    return {
        "stage": stage, "geometry_id": geometry_id, "condition_id": condition_id,
        "previous_status": status, "rejections": rejections,
        "budget_left": max(0, SELF_REPAIR_BUDGET + 1 - rejections),
        "resolved_reports": resolved,
    }


def set_cloud_path(conn: sqlite3.Connection, geometry_id: str, rel_path: str | None) -> None:
    """回写点云产物路径。审核核对通过后才写；`NULL` 表示没有可用点云。"""
    conn.execute("UPDATE geometry_state SET cloud_path=?, updated_at=? WHERE geometry_id=?",
                 (rel_path, utc_sql(), geometry_id))
    conn.commit()


def register_task_process(conn: sqlite3.Connection, run_id: str, pid: int) -> None:
    """登记"这个 run_id 正由哪个 pid 在跑"。

    `task_process` 是 `control.py stop` 与 `watch.py` 僵死回收的唯一线索：
    没有这行记录，stop 杀不到任何进程，僵死回收也永远不会触发。
    """
    conn.execute(
        "INSERT OR REPLACE INTO task_process(run_id, pid, started_at) VALUES(?,?,?)",
        (run_id, int(pid), utc_sql()))
    conn.commit()


def clear_task_process(conn: sqlite3.Connection, run_id: str) -> None:
    """子进程结束后注销登记。失败也要清：残留记录会被僵死回收判成假失败。"""
    conn.execute("DELETE FROM task_process WHERE run_id=?", (run_id,))
    conn.commit()


def _age_seconds(stamp: str | None) -> float:
    """`YYYY-MM-DDTHH:MM:SSZ` 距今多少秒；解析不了返回 `inf`（当作从未更新）。"""
    if not stamp:
        return float("inf")
    try:
        moment = datetime.strptime(stamp, SQL_TIMESTAMP_FORMAT).replace(tzinfo=TIMEZONE)
    except (TypeError, ValueError):
        return float("inf")
    return datetime.now(TIMEZONE).timestamp() - moment.timestamp()


def reclaim_zombies(conn: sqlite3.Connection) -> list[dict]:
    """僵死回收：`claimed_at` 还挂着，但进程已经没了。

    两种来源：
      1. `task_process` 里登记过 pid，而该 pid 已不存在；
      2. **领取后没来得及登记 pid 就崩了**（子进程起不来、依赖缺失等）：
         `claimed_at` 挂着但 `task_process` 里没有这一行，且已超过 `CLAIM_ORPHAN_SECONDS`。

    处置：置 `repairing` + `attempts+1` + 清 `claimed_at` + 写一条去重的上报单。
    置 `repairing` 而不是 `pending` 是刻意的——进程非正常消失属于"需要人看"的失败，
    自动重排队会让坏环境反复烧机器；`repairing` 由子智能体接手，超时由
    `scan_repair_timeouts()` 再报一次。
    """
    reclaimed: list[dict] = []
    live_run_ids = {row["run_id"] for row in conn.execute("SELECT run_id FROM task_process")}
    # ---- 1. 登记过 pid、但进程已消失 ----
    for row in conn.execute("SELECT run_id, pid FROM task_process").fetchall():
        run_id, pid = row["run_id"], int(row["pid"])
        if pid_alive(pid):
            continue
        run = resolve_run(conn, run_id)
        conn.execute("DELETE FROM task_process WHERE run_id=?", (run_id,))
        conn.commit()
        live_run_ids.discard(run_id)
        if run is None:
            reclaimed.append({"run_id": run_id, "pid": pid, "stage": None})
            continue
        claimed = is_claimed(conn, run["table"], run["geometry_id"], run["condition_id"])
        if not claimed:
            # 进程正常结束、登记也已清：这条 pid 记录是上一次运行的残留，不用管。
            continue
        attempts = _attempts_of(conn, run)
        mark_task(conn, run, "repairing", bump_attempts=True)
        report_id = record_report(
            conn, stage=run["stage"], geometry_id=run["geometry_id"], run_id=run_id,
            attempts=attempts + 1, condition_id=run["condition_id"], dedupe=True,
            last_error=f"进程 {pid} 已不存在但领取标记未清（僵死回收）→ repairing",
        )
        reclaimed.append({"run_id": run_id, "pid": pid, "report_id": report_id, **run})

    # ---- 2. 领取了却从没登记 pid（子进程在启动前就崩了）----
    for stage, spec in STAGE_TARGET.items():
        table, column = spec["table"], spec["status"]
        run_column = "model_run_id" if table == "geometry_state" else "run_id"
        rows = conn.execute(
            f"SELECT *, {run_column} AS current_run_id FROM {table}"
            " WHERE claimed_at IS NOT NULL").fetchall()
        for row in rows:
            run_id = row["current_run_id"]
            if run_id and run_id in live_run_ids:
                continue
            if _age_seconds(row["claimed_at"]) < CLAIM_ORPHAN_SECONDS:
                continue
            condition_id = row["condition_id"] if spec["scoped"] else None
            run = run_target(stage, row["geometry_id"], condition_id)
            attempts = _attempts_of(conn, run)
            mark_task(conn, run, "repairing", bump_attempts=True)
            report_id = record_report(
                conn, stage=stage, geometry_id=row["geometry_id"],
                run_id=run_id or make_run_id(row["geometry_id"],
                                             condition_id or SHARED_CONDITION_ID,
                                             taken_run_ids(conn)),
                attempts=attempts + 1, condition_id=condition_id, dedupe=True,
                last_error="领取后从未登记 pid（子进程启动前就退出了）→ repairing",
            )
            reclaimed.append({"run_id": run_id, "pid": None, "report_id": report_id, **run})
    return reclaimed


def scan_repair_timeouts(conn: sqlite3.Connection,
                         timeout_seconds: float | None = None) -> list[dict]:
    """修理超时：`repairing` 超过 `REPAIR_TIMEOUT_SECONDS` 还没解决 → 上报主智能体。

    **计时起点是 `repair_started_at`，不是 `updated_at`**。`updated_at` 只是"这一行最后
    一次被写"的时刻：迁移时旧 rejected 行被映射成 repairing，它的 `updated_at` 还是当年
    被否决的时间（可能是几周前），拿它判超时等于把历史证据误读成当前故障，每轮都开一张
    假单。`repair_started_at` 由 `mark_task` 在置 repairing 时写入。

    `repair_started_at IS NULL` 的行**不参与判断**：那是迁移/历史遗留的 repairing，没有
    "开始修理"这个事件。真要修它，走 `db.py requeue` 或审核侧重新置 repairing。

    按 `stage + geometry_id + condition_id` 去重，所以同一条任务每轮扫到也只开一张单；
    上报单 `open` 着就说明还没人处理，watcher 会继续在输出里提示。
    """
    limit = REPAIR_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    created: list[dict] = []
    for stage, spec in STAGE_TARGET.items():
        table, column = spec["table"], spec["status"]
        run_column = _run_column(table)
        if REPAIR_STARTED_COLUMN not in _table_columns(conn, table):
            continue                     # 老库还没补列：没有计时起点，不猜
        rows = conn.execute(
            f"SELECT *, {run_column} AS current_run_id FROM {table}"
            f" WHERE {column}='repairing'"
            f"   AND {REPAIR_STARTED_COLUMN} IS NOT NULL").fetchall()
        for row in rows:
            stamp = row[REPAIR_STARTED_COLUMN]
            if _age_seconds(stamp) <= limit:
                continue
            condition_id = row["condition_id"] if spec["scoped"] else None
            run_id = row["current_run_id"] or make_run_id(
                row["geometry_id"], condition_id or SHARED_CONDITION_ID, taken_run_ids(conn))
            minutes = limit / 60
            report_id = record_report(
                conn, stage=stage, geometry_id=row["geometry_id"], run_id=run_id,
                attempts=_attempts_of(conn, {"table": table,
                                             "geometry_id": row["geometry_id"],
                                             "condition_id": condition_id}),
                condition_id=condition_id, dedupe=True,
                last_error=f"修理超过 {minutes:g} 分钟仍未完成"
                           f"（{REPAIR_STARTED_COLUMN} {stamp}）",
            )
            created.append({"stage": stage, "geometry_id": row["geometry_id"],
                            "condition_id": condition_id, "report_id": report_id,
                            "repair_started_at": stamp})
    conn.commit()
    return created


def _attempts_of(conn: sqlite3.Connection, run: dict) -> int:
    if run["table"] == "geometry_state":
        return 0                      # 建模没有 attempts 字段，用 report 计数
    row = conn.execute(
        f"SELECT attempts FROM {run['table']} WHERE geometry_id=? AND condition_id=?",
        (run["geometry_id"], run["condition_id"]),
    ).fetchone()
    return int(row["attempts"]) if row else 0


def revive_deleted_artifacts(conn: sqlite3.Connection) -> list[str]:
    """产物不在（路径为 NULL）但状态是 `queued` → 回落 `pending`，等重新产出。

    典型来源有两个：`gc` 删掉了产物但状态没回退；或 2→3 迁移时把几何级网格值复制到
    各工况，而那一行本来就没有 `mesh_path`。两种情况都说明"这条记录声称已通过，
    却没有可交付的文件"，必须重跑。

    只回退**已经通过审核**（`queued`）且路径为 NULL 的行：
    - `pending` 本来就在等重跑；
    - `repairing` 的重跑要走重试预算，不能由 watcher 悄悄重置；
    - 领取中的行（`claimed_at` 非空）不能动，那会打断正在跑的进程。

    网格按工况检查：任意一行是 `queued` 但 `mesh_path` 为 NULL 就回落 `pending`——
    同一几何只有一个网格产物的时代已经结束。
    """
    stamp = utc_sql()
    revived: list[str] = []
    # ---- 建模：几何级 ----
    rows = conn.execute(
        "SELECT s.geometry_id FROM geometry_state s"
        " WHERE s.model_status='queued' AND s.model_path IS NULL AND s.claimed_at IS NULL"
        "   AND EXISTS (SELECT 1 FROM cfd_task t"
        "               WHERE t.geometry_id = s.geometry_id AND t.status='pending')"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE geometry_state SET model_status='pending', updated_at=?"
            " WHERE geometry_id=?", (stamp, row["geometry_id"]))
        revived.append(row["geometry_id"])
    # ---- 网格：按工况 ----
    # 只复活"建模已过审"的几何：没有模型就没有可重画的东西，留着 pending 只会污染列表。
    mesh_rows = conn.execute(
        "SELECT m.geometry_id, m.condition_id FROM mesh_task m"
        " WHERE m.status='queued' AND m.mesh_path IS NULL AND m.claimed_at IS NULL"
        "   AND EXISTS (SELECT 1 FROM geometry_state s WHERE s.geometry_id = m.geometry_id"
        "               AND s.model_status='queued' AND s.model_path IS NOT NULL)"
    ).fetchall()
    for row in mesh_rows:
        conn.execute(
            "UPDATE mesh_task SET status='pending', updated_at=?"
            " WHERE geometry_id=? AND condition_id=?",
            (stamp, row["geometry_id"], row["condition_id"]))
        revived.append(f"{row['geometry_id']}/{row['condition_id']}")
    conn.commit()
    return revived


# ---- 启停 ----
def parse_target(target: str) -> tuple[str, str]:
    kind, _, name = target.partition(":")
    name = name.strip()
    if kind == "workflow" and name in WORKFLOWS:
        return kind, name
    if kind == "stage" and name in STAGES:
        return kind, name
    if kind == "task" and name:
        return kind, name
    raise WriteError(
        f"目标不合法：{target!r}。只能是 workflow:{'|'.join(WORKFLOWS)}、"
        f"stage:{'|'.join(STAGES)} 或 task:<run_id>。"
    )


def set_control(conn: sqlite3.Connection, target: str, state: str, *,
                reason: str | None = None, updated_by: str = "main-agent") -> None:
    if state not in CONTROL_STATES:
        raise WriteError(f"启停状态只能是 {CONTROL_STATES}，收到 {state!r}")
    parse_target(target)
    conn.execute(
        "INSERT INTO control(target, state, reason, updated_at, updated_by) VALUES(?,?,?,?,?)"
        " ON CONFLICT(target) DO UPDATE SET state=excluded.state, reason=excluded.reason,"
        " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (target, state, reason, utc_sql(), updated_by),
    )
    conn.commit()


def effective_control(conn: sqlite3.Connection, *, workflow: str | None = None,
                      stage: str | None = None, run_id: str | None = None) -> tuple[str, str | None]:
    """生效状态：`task` > `stage` > `workflow`；都没有则 `running`。"""
    for target in (
        f"task:{run_id}" if run_id else None,
        f"stage:{stage}" if stage else None,
        f"workflow:{workflow}" if workflow else None,
    ):
        if not target:
            continue
        row = conn.execute("SELECT state FROM control WHERE target=?", (target,)).fetchone()
        if row is not None:
            return row["state"], target
    return "running", None


# =============================================================================
# 11. 命令：import / export
# =============================================================================
def read_input_table(path: Path) -> list[dict]:
    """读 23 列输入表；表头必须齐全，未知列忽略（但要提示）。"""
    if not path.exists():
        raise SystemExit(f"输入表不存在：{path}")
    with path.open("r", encoding=CSV_ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [(name or "").strip() for name in (reader.fieldnames or [])]
        missing = [column for column in CSV_COLUMNS if column not in headers]
        if missing:
            raise SystemExit(
                f"{path.name} 缺少列：{', '.join(missing)}\n"
                f"表头应为 23 列：{', '.join(CSV_COLUMNS)}"
            )
        unknown = [name for name in headers if name and name not in CSV_COLUMNS]
        if unknown:
            print(f"提示：忽略未知列 {', '.join(unknown)}")
        rows = []
        for line_number, raw in enumerate(reader, start=2):
            row = {(key or "").strip(): (value.strip() if isinstance(value, str) else value)
                   for key, value in raw.items() if key is not None}
            if not any(value not in (None, "") for value in row.values()):
                continue                    # 空行
            row["__line__"] = line_number
            rows.append(row)
    return rows


def cmd_import(args) -> int:
    if args.table not in INPUT_TABLES:
        raise SystemExit(f"--table 只能是 {list(INPUT_TABLES)}")
    path = Path(args.file) if args.file else WORKSPACE_ROOT / INPUT_TABLES[args.table]
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    rows = read_input_table(path)
    task_table = INPUT_TABLE_TASK[args.table]

    db_path = resolve_database(args.database)
    conn = connect(db_path, must_exist=not args.dry_run)
    stats = {"geometry": 0, "condition": 0, "task": 0, "existing": 0}
    errors: list[str] = []
    # 试运行不写库，用集合去重，统计口径与真实导入一致（同一几何重复出现只算一次）
    seen_geometry: set[str] = set()
    seen_condition: set[str] = set()
    seen_task: set[tuple[str, str]] = set()
    try:
        for row in rows:
            line = row.pop("__line__")
            try:
                geometry = identify_geometry(row)
                condition = identify_condition(row)
                origin = (row.get("origin") or DEFAULT_ORIGIN).strip() or DEFAULT_ORIGIN
                if origin not in ORIGIN_CHOICES:
                    raise ValueError(f"origin 只能是 {ORIGIN_CHOICES}，收到 {origin!r}")
                label = (row.get("label") or "").strip() or None
            except ValueError as exc:
                errors.append(f"第 {line} 行：{exc}")
                continue
            if args.dry_run:
                if geometry["geometry_id"] not in seen_geometry:
                    seen_geometry.add(geometry["geometry_id"])
                    stats["geometry"] += 1
                if condition["condition_id"] not in seen_condition:
                    seen_condition.add(condition["condition_id"])
                    stats["condition"] += 1
                key = (geometry["geometry_id"], condition["condition_id"])
                if key not in seen_task:
                    seen_task.add(key)
                    stats["task"] += 1
                continue
            if register_geometry(conn, geometry):
                stats["geometry"] += 1
            ensure_geometry_state(conn, geometry["geometry_id"])
            if register_condition(conn, condition, label):
                stats["condition"] += 1
            if register_task(conn, task_table, geometry["geometry_id"],
                             condition["condition_id"], origin):
                stats["task"] += 1
            else:
                stats["existing"] += 1
        # 有一行不合格就整份不写：宁可让人改表，也不要半份数据悄悄进库
        if errors or args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    except WriteError as exc:
        conn.rollback()
        raise SystemExit(str(exc))
    finally:
        conn.close()

    if errors:
        mode = "未写入（有错行）"
    elif args.dry_run:
        mode = "试运行（未写库）"
    else:
        mode = "已写入"
    print(f"{mode}：{path}")
    print(f"  读入 {len(rows)} 行 → {task_table}")
    print(f"  新增几何 {stats['geometry']}、新增工况 {stats['condition']}、"
          f"新增任务 {stats['task']}、已存在跳过 {stats['existing']}")
    if errors:
        print(f"  {len(errors)} 行有错，整份文件未写入：")
        for message in errors:
            print(f"    {message}")
        return 2
    return 0


def _export_cases_rows(conn: sqlite3.Connection, source: str, status: str) -> list[tuple]:
    """导出 DATCOM 批量入口表。列名逐个写死，不用 `g.*`：`geometry.label` 与
    `condition.label` 同名，`g.*` 会让后者被前者（NULL）遮住。"""
    table = "datcom_task" if source == "datcom" else "cfd_task"
    geometry_columns = ", ".join(f"g.{key}" for key in (
        *GEOMETRY_LENGTH_KEYS, *CSV_FIN_COLUMNS, NOSE_SHAPE_KEY))
    where, params = "", ()
    if status != "all":
        where, params = " WHERE t.status = ?", (status,)
    rows = conn.execute(
        f"SELECT t.geometry_id, {geometry_columns}, c.mach, c.alpha_deg, c.altitude_m,"
        f" t.condition_id, c.label AS condition_label FROM {table} t"
        " JOIN geometry g ON g.geometry_id = t.geometry_id"
        " JOIN condition c ON c.condition_id = t.condition_id"
        f"{where} ORDER BY t.geometry_id, c.altitude_m, c.mach, c.alpha_deg",
        params,
    ).fetchall()
    return [
        tuple([row["geometry_id"]] + [row[key] for key in (
            *GEOMETRY_LENGTH_KEYS, *CSV_FIN_COLUMNS, NOSE_SHAPE_KEY)]
            + [row["mach"], row["alpha_deg"], row["altitude_m"]]
            + [row["condition_id"], row["condition_label"]])
        for row in rows
    ]


def cmd_record_review(args) -> int:
    """把 review.py 写出的 JSON 记进库：唯一的入库凭证。"""
    path = Path(args.file)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    conn = connect(resolve_database(args.database))
    try:
        result = record_review(conn, payload)
    except WriteError as exc:
        raise SystemExit(str(exc))
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _index_view(conn: sqlite3.Connection) -> dict:
    """只读索引视图：从库里生成，取代原来扫盘生成的 `data_lib/index.json`。"""
    counts = {}
    for table in ("geometry", "condition", "review", "report"):
        counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    for table in ("mesh_task", "cfd_task", "datcom_task"):
        counts[table] = {row["status"]: row["n"] for row in conn.execute(
            f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status")}
    geometry_rows = []
    for row in conn.execute(
        "SELECT g.geometry_id, g.label, g.fin_section_type, g.nose_shape, g.L, g.D,"
        " g.cg_x_m, g.cg_y_m, g.cg_z_m,"
        " s.model_status, s.model_path, s.cloud_path, s.deletable, s.updated_at"
        " FROM geometry g LEFT JOIN geometry_state s ON s.geometry_id = g.geometry_id"
        " ORDER BY g.geometry_id"
    ):
        entry = dict(row)
        entry["deletable"] = bool(entry.get("deletable"))
        entry["mesh_tasks"] = [dict(item) for item in conn.execute(
            "SELECT condition_id, status, mesh_path, first_height_mm, n_layers, claimed_at"
            " FROM mesh_task WHERE geometry_id=? ORDER BY condition_id",
            (row["geometry_id"],))]
        entry["cfd_tasks"] = {item["status"]: item["n"] for item in conn.execute(
            "SELECT status, COUNT(*) AS n FROM cfd_task WHERE geometry_id=? GROUP BY status",
            (row["geometry_id"],))}
        entry["datcom_tasks"] = {item["status"]: item["n"] for item in conn.execute(
            "SELECT status, COUNT(*) AS n FROM datcom_task WHERE geometry_id=? GROUP BY status",
            (row["geometry_id"],))}
        geometry_rows.append(entry)
    reports = [dict(row) for row in conn.execute(
        "SELECT report_id, stage, geometry_id, condition_id, status, attempts, last_error,"
        " created_at FROM report WHERE status='open' ORDER BY created_at")]
    return {
        "schemaVersion": 1,
        "generatedAt": utc_sql(),
        "source": "python scripts/db.py export --format index",
        "note": "只读视图：内容全部来自 SQLite，随时可重建；不要手工编辑。",
        "counts": counts,
        "geometry": geometry_rows,
        "openReports": reports,
    }


def cmd_export(args) -> int:
    db_path = resolve_database(args.database)
    conn = connect(db_path)
    try:
        if args.format == "index":
            view = _index_view(conn)
            out = Path(args.out) if args.out else Path("data_lib/index.json")
            if not out.is_absolute():
                out = WORKSPACE_ROOT / out
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"已导出索引视图 → {workspace_rel(out)}"
                  f"（{view['counts']['geometry']} 个几何，"
                  f"{len(view['openReports'])} 条未解决上报）")
            return 0
        if args.format == "queue":
            if not args.queue:
                raise SystemExit("--format queue 需要 --queue（scdoc_for_fluent / meshing_for_solution）")
            written = export_queue(conn, args.queue, prune=not args.no_prune)
            print(f"已写出 {len(written)} 个队列指针 → {workspace_rel(queue_directory(args.queue))}")
            for item in written:
                print(f"  {item}")
            return 0
        if args.format == "cases-csv":
            rows = _export_cases_rows(conn, args.source, args.status)
            out = Path(args.out)
            if not out.is_absolute():
                out = WORKSPACE_ROOT / out
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding=CSV_ENCODING, newline="") as handle:
                writer = csv.writer(handle, lineterminator=CSV_NEWLINE)
                writer.writerow(EXPORT_CASES_HEADER)
                writer.writerows(rows)
            print(f"已导出 {len(rows)} 行 → {out}")
            return 0
        raise SystemExit(f"未知导出格式：{args.format}（当前支持 cases-csv）")
    finally:
        conn.close()


# =============================================================================
# 12. 命令：report / control
# =============================================================================
def cmd_report(args) -> int:
    db_path = resolve_database(args.database)
    conn = connect(db_path)
    try:
        report_id = record_report(
            conn, stage=args.stage, geometry_id=args.geometry_id, run_id=args.run_id,
            attempts=args.attempts, condition_id=args.condition_id,
            last_error=args.error, review_json=args.review_json,
            feedback_dir=args.feedback_dir,
        )
        print(f"已上报：{report_id}（{args.stage} / {args.geometry_id}）")
        return 0
    finally:
        conn.close()


def resolve_report(conn: sqlite3.Connection, report_id: str, resolution: str,
                   *, status: str = "resolved") -> dict:
    """把一条 `open` 上报单结掉，并把结论写进 `resolution`。

    `status='dismissed'` 用于"判定为非缺陷、不再处理"。两种都会离开 `open`，
    但保留原记录——上报单是事后追责的证据，不删行。
    """
    if status not in ("resolved", "dismissed"):
        raise WriteError(f"结单状态只能是 resolved / dismissed，收到 {status!r}")
    row = conn.execute(
        "SELECT report_id, status FROM report WHERE report_id=?", (report_id,)).fetchone()
    if row is None:
        raise WriteError(f"没有这条上报单：{report_id}")
    if row["status"] != "open":
        raise WriteError(f"上报单 {report_id} 已经是 {row['status']}，不能重复结单")
    conn.execute(
        "UPDATE report SET status=?, resolution=?, resolved_at=? WHERE report_id=?",
        (status, resolution, utc_sql(), report_id))
    conn.commit()
    return {"report_id": report_id, "status": status, "resolution": resolution}


def cmd_resolve_report(args) -> int:
    db_path = resolve_database(args.database)
    conn = connect(db_path)
    try:
        result = resolve_report(conn, args.report_id, args.resolution, status=args.status)
        print(f"{result['report_id']} → {result['status']}：{result['resolution']}")
        return 0
    finally:
        conn.close()


def cmd_requeue(args) -> int:
    db_path = resolve_database(args.database)
    conn = connect(db_path)
    try:
        result = requeue_task(
            conn, stage=args.stage, geometry_id=args.geometry_id,
            condition_id=args.condition_id, resolve_reports=args.resolve_reports,
            reason=args.reason,
        )
        target = result["geometry_id"] + (f" / {result['condition_id']}"
                                          if result["condition_id"] else "")
        print(f"{result['stage']} {target}：{result['previous_status']} → pending")
        if result["resolved_reports"]:
            print("已结掉上报单：" + "、".join(result["resolved_reports"]))
        print(f"重试额度剩余 {result['budget_left']} 次"
              f"（已拒 {result['rejections']} 次，预算 {SELF_REPAIR_BUDGET} 次自修）")
        if result["budget_left"] == 0:
            print("  额度已用尽：再被拒一次就会直接写上报单、不再自修。", file=sys.stderr)
        print("下一步：`python scripts\\watch.py --once` 排进队列，再 `dispatch.py --stage "
              f"{result['stage']} --once` 领取。")
        return 0
    finally:
        conn.close()


def _print_control_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("control 表为空：所有工作流与阶段都是默认的 running。")
        return
    print(f"{'目标'.ljust(28)} {'状态'.ljust(9)} 更新人 / 时间 / 原因")
    for row in rows:
        reason = f" / {row['reason']}" if row["reason"] else ""
        print(f"{row['target'].ljust(28)} {row['state'].ljust(9)} "
              f"{row['updated_by']} / {row['updated_at']}{reason}")


def cmd_control(args) -> int:
    db_path = resolve_database(args.database)
    conn = connect(db_path)
    try:
        if args.action == "set":
            if not args.target or not args.state:
                raise SystemExit("control set 需要 --target 与 --state。")
            set_control(conn, args.target, args.state, reason=args.reason,
                        updated_by=args.by)
            print(f"{args.target} → {args.state}")
            return 0
        if args.action == "show":
            rows = conn.execute(
                "SELECT * FROM control ORDER BY target"
            ).fetchall() if not args.target else conn.execute(
                "SELECT * FROM control WHERE target=?", (args.target,)
            ).fetchall()
            _print_control_rows(rows)
            return 0
        if args.action == "effective":
            state, source = effective_control(
                conn, workflow=args.workflow, stage=args.stage, run_id=args.run_id
            )
            print(f"{state}" + (f"（来自 {source}）" if source else "（无开关，默认）"))
            return 0
        raise SystemExit(f"未知 control 动作：{args.action}")
    finally:
        conn.close()


# =============================================================================
# 13. 审核入库与队列指针（唯一入库闸门）
# =============================================================================
def file_fingerprint(path: Path) -> dict:
    """`{rel_path, size, sha256}`；库内与指针文件里一律存工作区相对路径。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "rel_path": workspace_rel(path),
        "size": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def review_id_for(kind: str, run_id: str, verdict: str, created_at: str) -> str:
    """`review_id = "r" + sha256(kind+run_id+verdict+created_at)[:12]`（见下方去重）。"""
    digest = hashlib.sha256(f"{kind}{run_id}{verdict}{created_at}".encode("utf-8"))
    return ID_PREFIX["review"] + digest.hexdigest()[:ID_HEX_LEN]


def _unique_id(conn: sqlite3.Connection, table: str, column: str, prefix: str, seed: str) -> str:
    """同一秒内重复审核/上报会算出同一个 ID。撞了就在哈希输入后加 `#n` 重算，
    保证主键唯一，同时让"第一次"仍然等于文档里那条公式。"""
    for index in range(1000):
        salted = seed if index == 0 else f"{seed}#{index}"
        candidate = prefix + hashlib.sha256(salted.encode("utf-8")).hexdigest()[:ID_HEX_LEN]
        if conn.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (candidate,)).fetchone() is None:
            return candidate
    raise WriteError(f"{table}.{column} 在同一秒内已排满 1000 个，检查是否有循环写入")


def _rejections_since_last_pass(conn: sqlite3.Connection, kind: str,
                                geometry_id: str, condition_id: str | None) -> int:
    """同一 `(kind, 几何, 工况)` 上"最后一次通过之后"的拒绝次数，就是当前的重试次数。"""
    last_pass = conn.execute(
        "SELECT created_at FROM review WHERE kind=? AND geometry_id=? AND condition_id IS ?"
        " AND verdict='passed' ORDER BY created_at DESC LIMIT 1",
        (kind, geometry_id, condition_id),
    ).fetchone()
    if last_pass is None:
        return int(conn.execute(
            "SELECT COUNT(*) FROM review WHERE kind=? AND geometry_id=? AND condition_id IS ?"
            " AND verdict='rejected'",
            (kind, geometry_id, condition_id),
        ).fetchone()[0])
    return int(conn.execute(
        "SELECT COUNT(*) FROM review WHERE kind=? AND geometry_id=? AND condition_id IS ?"
        " AND verdict='rejected' AND created_at > ?",
        (kind, geometry_id, condition_id, last_pass["created_at"]),
    ).fetchone()[0])


def _check_artifact_path(rel_path: str, geometry_id: str, stage: str) -> Path:
    """产物路径安全检查：工作区内、在规定的运行根下、文件名以 `geometry_id` 开头。

    高保真三个阶段在 `data_lib/runs/high/<stage>/`；低保真的归档按几何放在
    `data_lib/runs/low/<geometry_id>/`（DATCOM 的归档名不带 run_id）。

    **网格阶段例外**：Fluent 工作流的默认产物名是 `missile_mesh.msh.h5`（由
    `fluent_meshing/mesh_missile.py` 的 `MESH_NAME` 决定），不以 `geometry_id` 开头。
    它落在 `runs/high/fluent_meshing/<run_id>/` 这个由库内 run_id 唯一确定的目录里，
    归属已经由目录和 run_id 保证，所以这里只要求文件名以 `.msh.h5` 结尾。
    `dispatch.py` 现在还会显式传 `--out <geometry_id>_mesh.msh.h5`，新运行仍然带前缀。
    """
    path = (WORKSPACE_ROOT / rel_path).resolve()
    try:
        local = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise WriteError(f"产物路径不在工作区内：{rel_path}")
    if stage == "datcom":
        expected = ("data_lib", "runs", "low", geometry_id)
    else:
        expected = ("data_lib", "runs", "high", stage)
    if local.parts[:len(expected)] != expected:
        raise WriteError(
            f"产物路径必须在 {'/'.join(expected)}/ 下：{rel_path}")
    if stage == "fluent_meshing":
        if not path.name.endswith(".msh.h5"):
            raise WriteError(f"网格产物必须是 .msh.h5：{path.name}")
    elif not path.name.startswith(geometry_id):
        raise WriteError(f"产物文件名必须以 {geometry_id} 开头：{path.name}")
    if not path.exists():
        raise WriteError(f"产物不存在：{rel_path}")
    return path


def record_review(conn: sqlite3.Connection, payload: dict) -> dict:
    """把一次审核写进 `review` 表，并按结论推进任务状态。

    **这是唯一的入库凭证**：产物路径、气动系数、`queued` 状态都只在 `verdict='passed'`
    时写入；`verdict='rejected'` 把任务置 `repairing`，交给子智能体修。
    连续 `SELF_REPAIR_BUDGET + 1` 次不过就写一条 `open` 上报单并停手。

    状态映射（三态）：`passed → queued`（审核通过、可被下一流程领取）、
    `rejected → repairing`（审核不通过、修理中）。领取标记在两条分支里都清掉：
    审核结束时进程已经退出了。
    """
    kind = payload.get("kind")
    verdict = payload.get("verdict")
    if kind not in REVIEW_KINDS:
        raise WriteError(f"审核种类只能是 {REVIEW_KINDS}，收到 {kind!r}")
    if verdict not in ("passed", "rejected"):
        raise WriteError(f"审核结论只能是 passed / rejected，收到 {verdict!r}")
    geometry_id = payload.get("geometry_id")
    if not geometry_id:
        raise WriteError("审核记录缺少 geometry_id")
    condition_id = payload.get("condition_id") or None
    run_id = payload.get("run_id")
    if not run_id:
        raise WriteError("审核记录缺少 run_id")
    if kind == "model" and condition_id:
        raise WriteError("model 是按几何审核的，condition_id 必须为空")
    if kind in ("mesh", "solution", "datcom") and not condition_id:
        raise WriteError(f"{kind} 是按工况审核的，必须给出 condition_id"
                         "（网格按 (geometry_id, condition_id) 一行，见 mesh_task）")
    reviewer = payload.get("reviewer") or "script:review.py"
    checks = payload.get("checks") or []
    if not isinstance(checks, list) or not checks:
        raise WriteError("审核记录必须带 checks（每条判据的 name / ok / detail）")
    blocking_failed = [c for c in checks if c.get("blocking", True) and not c.get("ok")]
    if verdict == "passed" and blocking_failed:
        raise WriteError(
            "结论自相矛盾：有 {} 条否决性判据未通过，不能记 passed：{}".format(
                len(blocking_failed), ", ".join(str(c.get("name")) for c in blocking_failed))
        )

    created = utc_sql()
    review_id = _unique_id(conn, "review", "review_id", ID_PREFIX["review"],
                           f"{kind}{run_id}{verdict}{created}")
    rejections = _rejections_since_last_pass(conn, kind, geometry_id, condition_id)
    attempts = rejections + (1 if verdict == "rejected" else 0)
    target_status = "queued" if verdict == "passed" else "repairing"

    artifact = payload.get("artifact") or {}
    rel_path = artifact.get("rel_path")
    stage_for_path = {"model": "spaceclaim", "mesh": "fluent_meshing",
                      "solution": "fluent_solution", "datcom": "datcom"}[kind]
    if rel_path:
        path = _check_artifact_path(rel_path, geometry_id, stage_for_path)
        actual = file_fingerprint(path)
        if artifact.get("sha256") and artifact["sha256"] != actual["sha256"]:
            raise WriteError(
                f"产物 sha256 与审核记录不一致：{rel_path}\n"
                f"  记录 {artifact['sha256']}\n  磁盘 {actual['sha256']}"
            )
        artifact = {**artifact, **actual}
        rel_path = actual["rel_path"]

    # 实测质心：过审的建模必须带整弹质心，先校验再写任何记录（同一条事务）。
    # 质心不进规范串，所以它缺失或越界不会改变身份，只会让这次审核入不了库。
    if kind == "model" and verdict == "passed":
        set_geometry_centroid(conn, geometry_id, payload.get("centroid"))

    conn.execute(
        "INSERT INTO review(review_id, kind, geometry_id, condition_id, run_id, reviewer,"
        " verdict, checks_json, artifact_sha256, attempts, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (review_id, kind, geometry_id, condition_id, run_id, reviewer, verdict,
         json.dumps(checks, ensure_ascii=False), artifact.get("sha256"), attempts, created),
    )

    stamp = utc_sql()
    report_id = None
    queue_files: list[str] = []
    # 审核结论也要维护修理计时：置 repairing 的那一支记下此刻，通过的那一支清空。
    # 与 `mark_task` 同一口径——`repair_started_at` 只表示"当前这段修理从何时开始"。
    repair_value = stamp if target_status == "repairing" else None
    if kind == "model":
        if verdict == "passed" and not rel_path:
            raise WriteError("模型审核通过必须给出产物路径")
        conn.execute(
            "UPDATE geometry_state SET model_status=?, model_path=?, model_run_id=?,"
            f" claimed_at=NULL, {REPAIR_STARTED_COLUMN}=?, updated_at=? WHERE geometry_id=?",
            (target_status,
             rel_path if verdict == "passed" else None,
             # run_id 一律记下：被否决的运行也要能反查是哪一次、重跑时也不用猜
             run_id, repair_value, stamp, geometry_id),
        )
    elif kind == "mesh":
        if verdict == "passed" and not rel_path:
            raise WriteError("网格审核通过必须给出产物路径")
        exists = conn.execute(
            "SELECT 1 FROM mesh_task WHERE geometry_id=? AND condition_id=?",
            (geometry_id, condition_id)).fetchone()
        if not exists:
            raise WriteError(f"mesh_task 里没有 {geometry_id} / {condition_id} 这条网格任务"
                             "（网格按工况建行，先让 watch.py 建出这一行）")
        conn.execute(
            "UPDATE mesh_task SET status=?, mesh_path=?, run_id=?, review_id=?,"
            f" claimed_at=NULL, {REPAIR_STARTED_COLUMN}=?, updated_at=?"
            " WHERE geometry_id=? AND condition_id=?",
            (target_status, rel_path if verdict == "passed" else None,
             run_id, review_id, repair_value, stamp, geometry_id, condition_id),
        )
    else:
        table = "cfd_task" if kind == "solution" else "datcom_task"
        exists = conn.execute(
            f"SELECT 1 FROM {table} WHERE geometry_id=? AND condition_id=?",
            (geometry_id, condition_id),
        ).fetchone()
        if not exists:
            raise WriteError(f"{table} 里没有 {geometry_id} / {condition_id} 这条任务")
        if verdict == "passed":
            conn.execute(
                f"UPDATE {table} SET status=?, CL=?, CD=?, CM=?, run_id=?,"
                " result_dir=COALESCE(?, result_dir), log_path=COALESCE(?, log_path),"
                f" review_id=?, claimed_at=NULL, {REPAIR_STARTED_COLUMN}=NULL, updated_at=?"
                " WHERE geometry_id=? AND condition_id=?",
                (target_status, payload.get("CL"), payload.get("CD"), payload.get("CM"),
                 run_id, payload.get("result_dir"), payload.get("log_path"),
                 review_id, stamp, geometry_id, condition_id),
            )
            if kind == "solution":
                conn.execute(
                    "UPDATE cfd_task SET converged=?, residual=?, updated_at=?"
                    " WHERE geometry_id=? AND condition_id=?",
                    (1 if payload.get("converged") else 0, payload.get("residual"),
                     stamp, geometry_id, condition_id),
                )
        else:
            conn.execute(
                f"UPDATE {table} SET status=?, run_id=?, claimed_at=NULL,"
                f" {REPAIR_STARTED_COLUMN}=?, updated_at=?"
                " WHERE geometry_id=? AND condition_id=?",
                (target_status, run_id, repair_value, stamp, geometry_id, condition_id),
            )

    # 超出预算就上报，且只报一次
    if verdict == "rejected" and attempts > SELF_REPAIR_BUDGET:
        already = conn.execute(
            "SELECT report_id FROM report WHERE stage=? AND geometry_id=? AND condition_id IS ?"
            " AND status='open'",
            (stage_for_path, geometry_id, condition_id),
        ).fetchone()
        if already is None:
            failed = [str(c.get("name")) for c in blocking_failed][:5]
            report_id = record_report(
                conn, stage=stage_for_path, geometry_id=geometry_id, run_id=run_id,
                attempts=attempts, condition_id=condition_id,
                last_error="自修重跑 {} 次仍不过：{}".format(SELF_REPAIR_BUDGET, "; ".join(failed)),
                review_json=json.dumps(checks, ensure_ascii=False),
            )

    conn.commit()

    # 通过的模型/网格进入队列视图（指针文件，可随时重建）
    queue = QUEUE_FOR_REVIEW_KIND.get(kind)
    if verdict == "passed" and queue:
        queue_files = export_queue(conn, queue)

    return {
        "review_id": review_id,
        "verdict": verdict,
        "attempts": attempts,
        "report_id": report_id,
        "queue_files": queue_files,
    }


def queue_directory(queue: str) -> Path:
    if queue not in QUEUE_FOR_REVIEW_KIND.values():
        raise WriteError(f"队列只能是 {sorted(set(QUEUE_FOR_REVIEW_KIND.values()))}，收到 {queue!r}")
    workspace = load_workspace()
    configured = (workspace.get("queues") or {}).get(queue)
    relative = configured or f"data_lib/queues/{queue}"
    return (WORKSPACE_ROOT / relative).resolve()


def _queue_status_query(queue: str) -> tuple[str, str]:
    """`(kind, SQL)`：队列对应的审核种类与"该有指针"的行。

    网格队列按 `(几何, 工况)` 一行，**只认 `status='queued'` 且有 `mesh_path`**——这就
    是"审核通过且产物已定位"的定义。迁移遗留的旧网格行在 `repair_mesh_migration` 之后
    是 `pending` 且 `mesh_path IS NULL`，所以天然进不了这个视图。
    """
    if queue == "scdoc_for_fluent":
        # 建模是几何级的，没有工况列；补一个 NULL 让两条队列的返回列一致。
        return "model", (
            "SELECT geometry_id, NULL AS condition_id, model_path AS path,"
            "       model_run_id AS run_id FROM geometry_state"
            " WHERE model_status='queued' AND model_run_id IS NOT NULL"
            "   AND model_path IS NOT NULL ORDER BY geometry_id")
    return "mesh", (
        "SELECT geometry_id, condition_id, mesh_path AS path, run_id FROM mesh_task"
        " WHERE status='queued' AND run_id IS NOT NULL AND mesh_path IS NOT NULL"
        " ORDER BY geometry_id, condition_id")


def queue_entries(conn: sqlite3.Connection, queue: str) -> tuple[str, list[dict]]:
    """算出队列目录**应该**有哪些指针文件，以及每条被跳过的原因（不写盘、不算哈希）。

    返回值 `(kind, entries)`，每条 entry 含 `name` / `pointer` 以外的行字段与 `path`。
    跳过规则（指向不存在的文件比没有指针更坏）：
      * 产物已不在磁盘上；
      * 库里没有对应的 `verdict='passed'` 审核记录。

    文件名默认 `<run_id>.json`。2→3 迁移遗留的行曾共享同一个几何级 `run_id`，直接拼
    会互相覆盖，所以撞名时补上 `condition_id`——修复后这种行已不存在，逻辑保留是为了
    对老库仍然安全。
    """
    kind, sql = _queue_status_query(queue)
    entries: list[dict] = []
    taken: set[str] = set()
    for row in conn.execute(sql).fetchall():
        artifact_path = (WORKSPACE_ROOT / row["path"]).resolve()
        if not artifact_path.exists():
            entries.append({"row": row, "skip": f"产物已不在磁盘上（{row['path']}）"})
            continue
        review = conn.execute(
            "SELECT review_id, created_at FROM review WHERE kind=? AND run_id=?"
            " AND verdict='passed' ORDER BY created_at DESC LIMIT 1",
            (kind, row["run_id"]),
        ).fetchone()
        if review is None:
            entries.append({"row": row, "skip": "库里没有对应的通过记录"})
            continue
        name = f"{row['run_id']}.json"
        if name in taken:
            name = f"{row['run_id']}__{row['condition_id']}.json"
        taken.add(name)
        entries.append({"row": row, "review": review, "artifact": artifact_path, "name": name})
    return kind, entries


def export_queue(conn: sqlite3.Connection, queue: str, *, prune: bool = True) -> list[str]:
    """把队列目录重写成数据库的只读视图：一个已通过审核的产物一个指针 JSON。

    下游领任务只看数据库；这个目录整目录删掉也能重建。该有哪些指针由 `queue_entries`
    决定（产物缺失或没有通过记录的行跳过），库一变就必须重写，否则目录会残留指向
    未验证产物的指针。
    """
    _kind, entries = queue_entries(conn, queue)
    directory = queue_directory(queue)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    expected: set[str] = set()
    for entry in entries:
        row = entry["row"]
        if "skip" in entry:
            print(f"跳过 {row['run_id']}：{entry['skip']}", file=sys.stderr)
            continue
        pointer = {
            "schemaVersion": QUEUE_POINTER_SCHEMA_VERSION,
            "queue": queue,
            "run_id": row["run_id"],
            "geometry_id": row["geometry_id"],
            "condition_id": row["condition_id"] or SHARED_CONDITION_ID,
            "artifact": file_fingerprint(entry["artifact"]),
            "review_id": entry["review"]["review_id"],
            "approved_at": entry["review"]["created_at"],
        }
        target = directory / entry["name"]
        target.write_text(json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(workspace_rel(target))
        expected.add(target.name)
    if prune:
        for stale in directory.glob("*.json"):
            if stale.name not in expected:
                stale.unlink()
    return written


# =============================================================================
# 13b. 清理：deletable 判定与 gc
# =============================================================================
def refresh_deletable(conn: sqlite3.Connection) -> list[str]:
    """自动判定 `deletable`：该几何至少有一行 `cfd_task`，且所有 `cfd_task` 都已审核通过。

    "审核通过"在三态里是 `queued`（`passed` 这个词已从任务表里删除；`review.verdict`
    仍是 `passed/rejected`，那是审核结论，不是排队状态）。网格同理：该几何的每一行
    `mesh_task` 都必须是 `queued`。

    人工可以把某一行的 `deletable` 强制置 1（`UPDATE`），这里只负责把自动判定的
    结果写回，不会把人工置的 1 改回 0——只升不降，否则人工决定会被自动规则覆盖。
    """
    marked: list[str] = []
    rows = conn.execute(
        "SELECT s.geometry_id, s.deletable,"
        "       (SELECT COUNT(*) FROM cfd_task t WHERE t.geometry_id = s.geometry_id) AS total,"
        "       (SELECT COUNT(*) FROM cfd_task t WHERE t.geometry_id = s.geometry_id"
        "          AND t.status = 'queued') AS done,"
        "       (SELECT COUNT(*) FROM mesh_task m WHERE m.geometry_id = s.geometry_id) AS mesh_total,"
        "       (SELECT COUNT(*) FROM mesh_task m WHERE m.geometry_id = s.geometry_id"
        "          AND m.status = 'queued' AND m.mesh_path IS NOT NULL) AS mesh_done"
        " FROM geometry_state s"
    ).fetchall()
    stamp = utc_sql()
    for row in rows:
        if row["deletable"]:
            continue
        tasks_ready = row["total"] > 0 and row["total"] == row["done"]
        meshes_ready = row["mesh_total"] == 0 or row["mesh_total"] == row["mesh_done"]
        if tasks_ready and meshes_ready:
            conn.execute(
                "UPDATE geometry_state SET deletable=1, updated_at=? WHERE geometry_id=?",
                (stamp, row["geometry_id"]),
            )
            marked.append(row["geometry_id"])
    conn.commit()
    return marked


def _gc_guard(path: Path, geometry_id: str, stage: str) -> str | None:
    """返回拒绝原因；None 表示这个路径可以删。"""
    try:
        resolved = path.resolve()
        local = resolved.relative_to(WORKSPACE_ROOT)
    except (OSError, ValueError):
        return "不在工作区内"
    expected = ("data_lib", "runs", "high", stage)
    if local.parts[:len(expected)] != expected:
        return f"不在 {'/'.join(expected)}/ 下"
    stage_dir = WORKSPACE_ROOT.joinpath(*expected).resolve()
    # 产物在 runs/high/<stage>/<run_id>/<file>；手工摆进去的文件也可能直接在
    # runs/high/<stage>/<file>。两种都接受，再深一层就不接受。
    if resolved.parent != stage_dir and resolved.parent.parent != stage_dir:
        return "必须直接位于 runs/high/<stage>/ 或 runs/high/<stage>/<run_id>/ 下"
    if not resolved.name.startswith(geometry_id):
        return "文件名不以该 geometry_id 开头"
    return None


def gc_candidates(conn: sqlite3.Connection) -> list[dict]:
    """列出 `deletable=1` 的几何要删哪些文件，以及跳过原因。

    网格按工况有多份（`mesh_task.mesh_path`），所以先按几何汇总所有网格路径，
    每条路径单独过安全校验。点云不在这个范围内，永远不碰。
    """
    candidates: list[dict] = []
    rows = conn.execute(
        "SELECT gs.geometry_id, gs.model_path,"
        "       (SELECT GROUP_CONCAT(mesh_path, '|') FROM mesh_task m"
        "        WHERE m.geometry_id = gs.geometry_id AND m.mesh_path IS NOT NULL) AS mesh_paths"
        " FROM geometry_state gs WHERE gs.deletable=1 ORDER BY gs.geometry_id"
    ).fetchall()
    for row in rows:
        geometry_id = row["geometry_id"]
        entry = {"geometry_id": geometry_id, "delete": [], "skipped": []}
        targets = [("spaceclaim", row["model_path"])]
        targets += [("fluent_meshing", item)
                    for item in (row["mesh_paths"] or "").split("|") if item]
        for stage, rel in targets:
            if not rel:
                continue
            path = (WORKSPACE_ROOT / rel).resolve()
            reason = _gc_guard(path, geometry_id, stage)
            if reason:
                entry["skipped"].append(f"{rel}：{reason}")
                continue
            if path not in entry["delete"]:
                entry["delete"].append(path)
            # 同目录里跟这个几何同名的日志一并删；点云不在这个目录，永远不碰
            for sibling in path.parent.glob(f"{geometry_id}_*.log"):
                if sibling not in entry["delete"]:
                    entry["delete"].append(sibling)
        candidates.append(entry)
    return candidates


def cmd_gc(args) -> int:
    conn = connect(resolve_database(args.database))
    try:
        marked = refresh_deletable(conn)
        if marked:
            print(f"新标记可删除：{', '.join(marked)}")
        candidates = gc_candidates(conn)
        if not candidates:
            print("没有 `deletable=1` 的几何。")
            return 0
        total = 0
        for entry in candidates:
            print(f"几何 {entry['geometry_id']}")
            for path in entry["delete"]:
                size = path.stat().st_size if path.exists() else 0
                print(f"  删除 {workspace_rel(path)}（{size} 字节）")
                total += 1
            for reason in entry["skipped"]:
                print(f"  跳过 {reason}")
            if not args.apply:
                continue
            for path in entry["delete"]:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    print(f"  删除失败 {workspace_rel(path)}：{exc}", file=sys.stderr)
            # 产物没了，但"曾经审过"这件事保留：状态不回退，路径与运行号清空。
            # 网格路径按工况逐行清空（同一几何可能有多份网格）。
            conn.execute(
                "UPDATE geometry_state SET model_path=NULL, model_run_id=NULL, deletable=0,"
                " updated_at=? WHERE geometry_id=?",
                (utc_sql(), entry["geometry_id"]),
            )
            conn.execute(
                "UPDATE mesh_task SET mesh_path=NULL, run_id=NULL, updated_at=?"
                " WHERE geometry_id=? AND mesh_path IS NOT NULL",
                (utc_sql(), entry["geometry_id"]),
            )
        conn.commit()
        if args.apply:
            print(f"\n已删除 {total} 个文件。点云永久保留；feedback/ 不在清理范围内。")
        else:
            print(f"\n试运行：将删除 {total} 个文件。加 --apply 才真删。")
        return 0
    finally:
        conn.close()


# =============================================================================
# 13c. 训练轮次与补点（train.py 用）
# =============================================================================
def start_training_round(conn: sqlite3.Connection, *, data_version: str,
                         model_path: str | None = None,
                         metrics_json: str | None = None) -> str:
    """开一轮训练记录（`status='running'`），返回 `round_id`。"""
    stamp = utc_stamp()
    round_id = f"t{stamp}"
    index = 1
    while conn.execute("SELECT 1 FROM training_round WHERE round_id=?", (round_id,)).fetchone():
        index += 1
        round_id = f"t{stamp}-{index}"
    conn.execute(
        "INSERT INTO training_round(round_id, status, data_version, model_path, metrics_json,"
        " proposed_rows, created_at) VALUES(?, 'running', ?, ?, ?, 0, ?)",
        (round_id, data_version, model_path, metrics_json, utc_sql()),
    )
    conn.commit()
    return round_id


def finish_training_round(conn: sqlite3.Connection, round_id: str, *,
                          status: str, metrics_json: str | None = None,
                          proposed_rows: int = 0, log_path: str | None = None) -> None:
    if status not in ("passed", "failed", "cancelled"):
        raise WriteError(f"训练轮次状态只能是 passed/failed/cancelled，收到 {status!r}")
    conn.execute(
        "UPDATE training_round SET status=?, metrics_json=COALESCE(?, metrics_json),"
        " proposed_rows=?, log_path=COALESCE(?, log_path), finished_at=? WHERE round_id=?",
        (status, metrics_json, proposed_rows, log_path, utc_sql(), round_id),
    )
    conn.commit()


def propose_task(conn: sqlite3.Connection, geometry_identity: dict,
                 condition_identity: dict, round_id: str) -> bool:
    """把一条"置信度不足"的 (16 参数, 3 工况) 写进库，状态 `pending`。

    与初始化导入走同一条路：登记几何/工况 → 建状态行 → 插 `cfd_task`（`origin`
    `=active_learning`、`round_id` 指回本轮）。已存在就返回 False，不重复插入。
    """
    created_geometry = register_geometry(conn, geometry_identity)
    ensure_geometry_state(conn, geometry_identity["geometry_id"])
    register_condition(conn, condition_identity, None)
    if not register_task(conn, "cfd_task", geometry_identity["geometry_id"],
                         condition_identity["condition_id"],
                         origin="active_learning", round_id=round_id):
        conn.commit()
        return False
    conn.execute(
        "UPDATE cfd_task SET round_id=? WHERE geometry_id=? AND condition_id=?",
        (round_id, geometry_identity["geometry_id"], condition_identity["condition_id"]),
    )
    conn.commit()
    return True


# =============================================================================
# 14. 命令：claim / repair-timeout（dispatch.py / watch.py 的调试入口）
# =============================================================================
CLAIMERS = {
    "spaceclaim": claim_model,
    "fluent_meshing": claim_mesh,
    "fluent_solution": claim_solution,
    "datcom": claim_datcom_tasks,
}


def cmd_claim(args) -> int:
    """原子领取一条待处理行，打印含 `run_id` 的 JSON。

    领取会写上 `claimed_at + run_id`，所以并发的两次 `claim` 里只有一个能拿到这条任务。
    """
    db_path = resolve_database(args.database)
    conn = connect(db_path)
    try:
        claimed = CLAIMERS[args.stage](conn, args.limit)
        print(json.dumps(claimed, ensure_ascii=False))
        return 0
    finally:
        conn.close()


def cmd_repair_timeout(args) -> int:
    """扫一遍修理超时的行并开上报单（`watch.py` 每轮调用同一个函数）。"""
    conn = connect(resolve_database(args.database))
    try:
        found = scan_repair_timeouts(conn, args.timeout)
        if not found:
            print("没有修理超时的任务。")
            return 0
        for item in found:
            target = item["geometry_id"] + (f" / {item['condition_id']}"
                                            if item["condition_id"] else "")
            print(f"{item['stage']} {target}：修理超时，已上报 {item['report_id']}"
                  f"（repair_started_at {item['repair_started_at']}）")
        return 0
    finally:
        conn.close()


def cmd_release_claim(args) -> int:
    """放弃一次作废的领取（清 claimed_at + 注销 pid + 任务回 pending）。"""
    conn = connect(resolve_database(args.database))
    try:
        result = abandon_claim(conn, args.run_id)
        if result["stage"] is None:
            print(f"{result['run_id']}：库里已无对应任务，只清掉了 pid 登记"
                  f"（pid {result['pid']}）")
            return 0
        target = result["geometry_id"] + (f" / {result['condition_id']}"
                                          if result["condition_id"] else "")
        print(f"已放弃领取 {result['run_id']}（pid {result['pid'] or '未登记'}）："
              f"{result['stage']} {target} → pending，run_id / claimed_at 已清")
        print(f"已开上报单 {result['report_id']}（按阶段+几何+工况去重）")
        return 0
    finally:
        conn.close()


def cmd_repair_mesh_migration(args) -> int:
    """一次性修数据：把 2→3 迁移留下的"旧几何级网格 = 按工况过审"改回 pending。"""
    conn = connect(resolve_database(args.database))
    try:
        release: list[str] = []
        if args.release_claim:
            # 领取中的行由 `MESH_MIGRATION_CONDITION` 的安全阀排除在批量修正之外，
            # 必须显式列出才动——否则等于替一个可能正在跑的进程改它的 run_id。
            wanted = set(args.run_id)
            for row in conn.execute(
                "SELECT run_id FROM mesh_task"
                f" WHERE {MESH_MIGRATION_CONDITION_ANY_CLAIM}"
                " AND claimed_at IS NOT NULL ORDER BY geometry_id, condition_id").fetchall():
                if row["run_id"] and (not wanted or row["run_id"] in wanted):
                    release.append(row["run_id"])
        result = repair_mesh_migration(conn, reset_claim=release)
        reset = result["reset"]
        released = result["released_claims"]
        if not reset:
            print("没有需要修正的网格行（已是 pending / 无旧几何级网格）。")
        else:
            print(f"已修正 {len(reset)} 行 mesh_task → pending（清空 run_id / mesh_path）：")
            for row in reset:
                print(f"  {row['geometry_id']} / {row['condition_id']}"
                      f"（原 {row['status']}，run_id {row['run_id'] or '—'}）")
        for row in released:
            print(f"已放弃领取：{row['run_id']}（pid {row['pid'] or '未登记'}）"
                  f" → {row['stage']} {row['geometry_id']}"
                  f"{' / ' + row['condition_id'] if row['condition_id'] else ''} pending，"
                  f"上报 {row['report_id']}")
        if args.release_claim and not released:
            print("没有挂着 claimed_at 的旧网格行可放弃。")
        # 队列目录是库的只读视图，库一变就必须重写，否则里面还留着指向未验证网格的指针
        for queue in sorted(set(QUEUE_FOR_REVIEW_KIND.values())):
            written = export_queue(conn, queue)
            print(f"已按库重写 {queue}：{len(written)} 个指针"
                  f"（{workspace_rel(queue_directory(queue))}）")
        return 0
    finally:
        conn.close()


# =============================================================================
# 15. 命令行
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="db.py",
        description="SQLite 唯一真相源：建表、规范化、身份生成。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "库路径优先级：--database > 环境变量 MISSILE_DB > workspace.json 的 database 字段。\n"
            "工作区根：环境变量 MISSILE_WORKSPACE > scripts/ 的上一级。"
        ),
    )
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json 的 database 字段）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="建表（幂等）")
    init.set_defaults(func=cmd_init)

    check = subparsers.add_parser("check", help="自检：结构、PRAGMA、规范化、约束")
    check.set_defaults(func=cmd_check)

    identify = subparsers.add_parser("identify", help="规范化并打印 geometry_id / condition_id")
    identify.add_argument("--geometry-json", help="16 个几何参数的 JSON 对象")
    identify.add_argument("--geometry-file", help="16 个几何参数的 JSON 文件（Windows 命令行会吃掉内联 JSON 的引号）")
    identify.add_argument("--condition-json", help="mach / alpha_deg / altitude_m 的 JSON 对象")
    identify.add_argument("--condition-file", help="同上，从 JSON 文件读")
    identify.set_defaults(func=cmd_identify)

    centroid = subparsers.add_parser(
        "centroid", help="读 SpaceClaim 实测质心坐标（x 从弹尖起算，单位 m）")
    centroid.add_argument("--geometry-id", help="只读这一个几何；不给就列出全部")
    centroid.set_defaults(func=cmd_centroid)

    importer = subparsers.add_parser("import", help="把 23 列输入表导入（追加型、幂等）")
    importer.add_argument("--table", required=True, choices=sorted(INPUT_TABLES),
                          help="high = 高保真（写 cfd_task），low = 低保真（写 datcom_task）")
    importer.add_argument("--file", help="输入表路径；默认取配置区的 INPUT_TABLES")
    importer.add_argument("--dry-run", action="store_true", help="只校验不写库")
    importer.set_defaults(func=cmd_import)

    exporter = subparsers.add_parser("export", help="从库导出只读视图")
    exporter.add_argument("--format", required=True, choices=["cases-csv", "queue", "index"],
                          help="cases-csv = DATCOM 批量入口表；queue = 队列目录指针文件；"
                               "index = data_lib/index.json 只读索引")
    exporter.add_argument("--queue", choices=sorted(set(QUEUE_FOR_REVIEW_KIND.values())),
                          help="--format queue 时要写哪个队列目录")
    exporter.add_argument("--no-prune", action="store_true",
                          help="不删除队列目录里已经不该存在的指针文件")
    exporter.add_argument("--source", default="datcom", choices=["datcom", "cfd"],
                          help="cases-csv 的来源任务表（默认 datcom）")
    exporter.add_argument("--status", default="all",
                          choices=["all", *TASK_STATUSES], help="只导出某一状态的行")
    exporter.add_argument("--out", help="输出文件（相对工作区根或绝对路径；cases-csv 必填）")
    exporter.set_defaults(func=cmd_export)

    reviewer = subparsers.add_parser("record-review", help="记入一次审核结论（唯一入库凭证）")
    reviewer.add_argument("--file", required=True, help="review.py 写出的审核 JSON")
    reviewer.set_defaults(func=cmd_record_review)

    reporting = subparsers.add_parser("report", help="写一条上报主智能体的问题单")
    reporting.add_argument("--stage", required=True, choices=list(STAGES))
    reporting.add_argument("--geometry-id", required=True)
    reporting.add_argument("--run-id", required=True)
    reporting.add_argument("--condition-id")
    reporting.add_argument("--attempts", type=int, default=1)
    reporting.add_argument("--error", help="最后一次失败原因")
    reporting.add_argument("--review-json", help="审核判据明细（JSON 文本）")
    reporting.add_argument("--feedback-dir", help="失败包目录（工作区相对路径）")
    reporting.set_defaults(func=cmd_report)

    resolve = subparsers.add_parser(
        "resolve-report", help="结掉一条 open 上报单，把结论写进 report.resolution")
    resolve.add_argument("--report-id", required=True)
    resolve.add_argument("--resolution", required=True, help="结论，写进 report.resolution")
    resolve.add_argument("--status", default="resolved", choices=["resolved", "dismissed"],
                         help="resolved=已解决；dismissed=判定为非缺陷")
    resolve.set_defaults(func=cmd_resolve_report)

    requeue = subparsers.add_parser(
        "requeue", help="把一条任务放回 pending，等 watch.py / dispatch.py 重新领取")
    requeue.add_argument("--stage", required=True, choices=list(STAGES))
    requeue.add_argument("--geometry-id", required=True)
    requeue.add_argument("--condition-id", help="网格 / 求解 / 低保真按工况，必须给")
    requeue.add_argument("--resolve-reports", action="store_true",
                         help="同时把这个任务 open 的上报单结掉")
    requeue.add_argument("--reason", help="结上报单时写进 report.resolution")
    requeue.set_defaults(func=cmd_requeue)

    control = subparsers.add_parser("control", help="启停开关的低层读写")
    control.add_argument("action", choices=["set", "show", "effective"])
    control.add_argument("--target", help="workflow:high | stage:spaceclaim | task:<run_id>")
    control.add_argument("--state", choices=list(CONTROL_STATES))
    control.add_argument("--reason")
    control.add_argument("--by", default="main-agent", help="谁改的，写进 updated_by")
    control.add_argument("--workflow", choices=list(WORKFLOWS), help="effective 用")
    control.add_argument("--stage", choices=list(STAGES), help="effective 用")
    control.add_argument("--run-id", help="effective 用")
    control.set_defaults(func=cmd_control)

    claim = subparsers.add_parser(
        "claim", help="原子领取一条待处理行（claimed_at + run_id 挡住重复领取）")
    claim.add_argument("--stage", required=True, choices=list(STAGES))
    claim.add_argument("--limit", type=int, default=1)
    claim.set_defaults(func=cmd_claim)

    repair = subparsers.add_parser(
        "repair-timeout", help="扫 repairing 超时的行并写去重的上报单")
    repair.add_argument("--timeout", type=float, default=REPAIR_TIMEOUT_SECONDS,
                        help=f"超时秒数（默认 {REPAIR_TIMEOUT_SECONDS}）")
    repair.set_defaults(func=cmd_repair_timeout)

    fix_mesh = subparsers.add_parser(
        "repair-mesh-migration",
        help="一次性修数据：把 2→3 迁移动过的旧几何级网格改回 pending 并清空路径/运行号",
        description="修 2→3 迁移留下的『一份几何级旧网格 = 四个工况都审核通过』。"
                    "判据：first_height_mm IS NULL 且 run_id 是几何级（中段 shared）。"
                    "置 pending 并清空 run_id / mesh_path / repair_started_at；"
                    "旧网格文件留在 runs/high/fluent_meshing/<旧 run_id>/ 作历史证据。"
                    "跑完自动按库重写两个队列目录。")
    fix_mesh.add_argument("--release-claim", action="store_true",
                          help="连『还挂着 claimed_at』的旧网格行一起清（同时注销 task_process）")
    fix_mesh.add_argument("--run-id", action="append", default=[],
                          help="配合 --release-claim：只释放这个 run_id 的领取登记，可重复")
    fix_mesh.set_defaults(func=cmd_repair_mesh_migration)

    release = subparsers.add_parser(
        "release-claim",
        help="放弃一次作废的领取：清 claimed_at + 注销 pid + 任务回 pending",
        description="用于『进程已经不在、但 claimed_at 还挂着』的作废运行。"
                    "拒绝仍然活着的进程（先用 control.py stop 收掉它）；产物文件不动。")
    release.add_argument("--run-id", required=True, help="要放弃的 run_id")
    release.set_defaults(func=cmd_release_claim)

    gc = subparsers.add_parser("gc", help="清理已通过审核的模型/网格（默认试运行）")
    gc.add_argument("--apply", action="store_true", help="真删；不加只列出将要删什么")
    gc.set_defaults(func=cmd_gc)

    return parser


def _configure_output() -> None:
    """管道输出统一成 UTF-8；交互终端保持系统代码页，中文在两边都能看清。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None or stream.isatty():
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            continue


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
