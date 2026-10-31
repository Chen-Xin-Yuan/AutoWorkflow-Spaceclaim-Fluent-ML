# -*- coding: utf-8 -*-
"""SQLite 唯一真相源：建表、参数规范化、身份生成，以及之后所有库操作的唯一入口。

任何脚本、子智能体、主智能体都通过 `python scripts/db.py <命令>` 改库，**不直接开
sqlite3 连接写**，避免 `database is locked` 与"写了一半"的事务。

本文件提供 `init`、`check`、`identify`、`import`、`export`、`report`、`control`、`claim`
八个命令；`record-review`（P2）、`gc`（P6）在后续阶段加进来，配置区与规范化函数届时
不需要改动。`watch.py` / `control.py` / `dispatch.py` 不自己写 SQL，而是 import 本模块
第 10 节的函数，所以"所有写操作只经 db.py"在这一层成立。

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

输出
    `data_lib/datasets/SQLite/missile.db`，WAL 附属文件 `-wal` / `-shm` 不入库。
    `init` 只建表与写入 `meta.schema_version`；`check` 只读，不改库。

参数配置（要改数值、字段顺序或路径只改这一节）
    库文件名、表结构与索引、规范化字段顺序、枚举与大写规则、数值范围、
    时间戳格式、浮点位数、ID 长度与前缀。

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
SCHEMA_VERSION = "1"
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

# ---- 七态状态词表（四阶段共用，库内存英文码，中文只出现在文档里）----
TASK_STATUSES = (
    "pending", "queued", "running", "reviewing", "rejected", "passed", "cancelled",
)

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
# 2. 表结构与索引（单一库、11 张表；改字段必须同时升 SCHEMA_VERSION）
# =============================================================================
SCHEMA_SQL = """
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
  CHECK ( (fin_section_type='hex'
           AND flat_percent IS NOT NULL AND le_radius IS NOT NULL
           AND le_radius_index IS NULL AND max_thickness_tenths IS NULL)
       OR (fin_section_type='naca'
           AND le_radius_index IS NOT NULL AND max_thickness_tenths IS NOT NULL
           AND flat_percent IS NULL AND le_radius IS NULL) )
);

CREATE TABLE IF NOT EXISTS geometry_state (
  geometry_id  TEXT PRIMARY KEY REFERENCES geometry(geometry_id),
  model_status TEXT NOT NULL DEFAULT 'pending' CHECK (model_status IN
               ('pending','queued','running','reviewing','rejected','passed','cancelled')),
  mesh_status  TEXT NOT NULL DEFAULT 'pending' CHECK (mesh_status IN
               ('pending','queued','running','reviewing','rejected','passed','cancelled')),
  model_path   TEXT, mesh_path TEXT, cloud_path TEXT,
  model_run_id TEXT, mesh_run_id TEXT, cloud_run_id TEXT,
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

CREATE TABLE IF NOT EXISTS cfd_task (
  geometry_id  TEXT NOT NULL REFERENCES geometry(geometry_id),
  condition_id TEXT NOT NULL REFERENCES condition(condition_id),
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
               ('pending','queued','running','reviewing','rejected','passed','cancelled')),
  attempts     INTEGER NOT NULL DEFAULT 0,
  CL REAL, CD REAL, CM REAL,
  converged    INTEGER,              -- 0/1，求解阶段填
  residual     REAL,                 -- 连续性残差
  run_id       TEXT, result_dir TEXT, log_path TEXT,
  review_id    TEXT REFERENCES review(review_id),
  origin       TEXT NOT NULL DEFAULT 'human' CHECK (origin IN ('human','active_learning')),
  round_id     TEXT REFERENCES training_round(round_id),
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (geometry_id, condition_id)
);

CREATE TABLE IF NOT EXISTS datcom_task (
  geometry_id  TEXT NOT NULL REFERENCES geometry(geometry_id),
  condition_id TEXT NOT NULL REFERENCES condition(condition_id),
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
               ('pending','queued','running','reviewing','rejected','passed','cancelled')),
  attempts     INTEGER NOT NULL DEFAULT 0,
  CL REAL, CD REAL, CM REAL,
  run_id       TEXT, result_dir TEXT, log_path TEXT,
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
  condition_id    TEXT,            -- model / mesh 为 NULL
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

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_cfd_task_status      ON cfd_task(status);
CREATE INDEX IF NOT EXISTS idx_datcom_task_status   ON datcom_task(status);
CREATE INDEX IF NOT EXISTS idx_geometry_state_model ON geometry_state(model_status);
CREATE INDEX IF NOT EXISTS idx_geometry_state_mesh  ON geometry_state(mesh_status);
CREATE INDEX IF NOT EXISTS idx_report_status        ON report(status);
CREATE INDEX IF NOT EXISTS idx_review_kind_geometry ON review(kind, geometry_id);
"""

EXPECTED_TABLES = (
    "meta", "geometry", "geometry_state", "condition", "cfd_task", "datcom_task",
    "review", "report", "control", "task_process", "training_round",
)

EXPECTED_INDEXES = (
    "idx_cfd_task_status", "idx_datcom_task_status",
    "idx_geometry_state_model", "idx_geometry_state_mesh",
    "idx_report_status", "idx_review_kind_geometry",
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
    try:
        before = table_names(conn)
        if "meta" in before:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            current = row["value"] if row else None
            if current != SCHEMA_VERSION:
                raise SystemExit(
                    f"库内 schema_version={current!r} 与本文件 {SCHEMA_VERSION!r} 不一致。\n"
                    "改表结构必须升版本号并写迁移脚本，不能直接覆盖。"
                )
        create_schema(conn)
        after = table_names(conn)
    finally:
        conn.close()

    print(f"数据库：{db_path}（{'新建' if not existed else '已存在'}）")
    print(f"表 {len(after)} 张：{', '.join(sorted(after))}")
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
            f"11 张表齐全",
            not missing,
            f"缺 {missing}" if missing else (f"多余 {extra}（不阻塞）" if extra else "全部就位"),
        )
        indexes = index_names(conn)
        missing_index = [name for name in EXPECTED_INDEXES if name not in indexes]
        record("6 个索引齐全", not missing_index, f"缺 {missing_index}" if missing_index else "全部就位")

        if "meta" in tables:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            version = row["value"] if row else None
            record("schema_version 一致", version == SCHEMA_VERSION, f"库内 {version}，代码 {SCHEMA_VERSION}")
        else:
            record("schema_version 一致", False, "meta 表不存在")
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
        ok, detail = _expect_error(
            memory,
            "INSERT INTO cfd_task(geometry_id, condition_id, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?)",
            (identity["geometry_id"], condition_identity["condition_id"], "bogus", now, now),
            "非法任务状态",
        )
        record("任务状态只能取七态", ok, detail)

        ok, detail = _expect_error(
            memory,
            "INSERT INTO cfd_task(geometry_id, condition_id, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?)",
            ("gnotexist00000", condition_identity["condition_id"], "pending", now, now),
            "不存在的 geometry_id",
        )
        record("外键拦住孤儿任务", ok, detail)

        ok, detail = _expect_error(
            memory,
            "INSERT INTO control(target, state, updated_at, updated_by) VALUES(?,?,?,?)",
            ("workflow:high", "resume", now, "check"),
            "非法启停状态",
        )
        record("启停状态只能取三态", ok, detail)

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
            (identity["geometry_id"], condition_identity["condition_id"], "pending", now, now),
            "合法任务可写入",
        )
        record("合法任务可写入", ok, detail)
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


# ---- 领取：pending → queued（只是状态推进，不启动进程）----
STAGE_STATUS_FIELD = {"spaceclaim": "model_status", "fluent_meshing": "mesh_status"}


def claim_geometry_stage(conn: sqlite3.Connection, stage: str, limit: int = 1,
                         *, source_status: str = "pending",
                         target_status: str = "queued") -> list[str]:
    """领取建模/网格。`watch.py` 用默认值做 `pending → queued`；`dispatch.py` 用
    `source_status='queued'` 把它推进到 `running`。网格要多一道建模已通过的闸门。"""
    if stage not in STAGE_STATUS_FIELD:
        raise WriteError(f"该阶段不按几何领取：{stage}")
    for status in (source_status, target_status):
        if status not in TASK_STATUSES:
            raise WriteError(f"未知状态：{status}（只能是 {TASK_STATUSES}）")
    field = STAGE_STATUS_FIELD[stage]
    gate = ""
    if stage == "fluent_meshing":
        gate = " AND model_status='passed' AND model_path IS NOT NULL"
    rows = conn.execute(
        f"SELECT geometry_id FROM geometry_state WHERE {field}=?{gate}"
        " ORDER BY geometry_id LIMIT ?",
        (source_status, limit),
    ).fetchall()
    claimed = [row["geometry_id"] for row in rows]
    for geometry_id in claimed:
        conn.execute(
            f"UPDATE geometry_state SET {field}=?, updated_at=? WHERE geometry_id=?",
            (target_status, utc_sql(), geometry_id),
        )
    conn.commit()
    return claimed


def claim_cfd_tasks(conn: sqlite3.Connection, limit: int = 1, *,
                    source_status: str = "pending",
                    target_status: str = "queued") -> list[tuple[str, str]]:
    """领取求解任务：网格已通过、产物还在。"""
    rows = conn.execute(
        "SELECT t.geometry_id, t.condition_id FROM cfd_task t"
        " JOIN geometry_state s ON s.geometry_id = t.geometry_id"
        " WHERE t.status=? AND s.mesh_status='passed' AND s.mesh_path IS NOT NULL"
        " ORDER BY t.geometry_id, t.condition_id LIMIT ?",
        (source_status, limit),
    ).fetchall()
    claimed = [(row["geometry_id"], row["condition_id"]) for row in rows]
    for geometry_id, condition_id in claimed:
        conn.execute(
            "UPDATE cfd_task SET status=?, updated_at=? WHERE geometry_id=? AND condition_id=?",
            (target_status, utc_sql(), geometry_id, condition_id),
        )
    conn.commit()
    return claimed


def claim_datcom_tasks(conn: sqlite3.Connection, limit: int = 1, *,
                       source_status: str = "pending",
                       target_status: str = "queued") -> list[tuple[str, str]]:
    """领取低保真任务：无前置条件（按行领取，批量版见 `claim_datcom_batch`）。"""
    rows = conn.execute(
        "SELECT geometry_id, condition_id FROM datcom_task WHERE status=?"
        " ORDER BY geometry_id, condition_id LIMIT ?",
        (source_status, limit),
    ).fetchall()
    claimed = [(row["geometry_id"], row["condition_id"]) for row in rows]
    for geometry_id, condition_id in claimed:
        conn.execute(
            "UPDATE datcom_task SET status=?, updated_at=? WHERE geometry_id=? AND condition_id=?",
            (target_status, utc_sql(), geometry_id, condition_id),
        )
    conn.commit()
    return claimed


def taken_run_ids(conn: sqlite3.Connection) -> set[str]:
    """已经用掉的 run_id（进程登记 + 四个阶段的运行号），新的不能与它们重复。"""
    taken = {row["run_id"] for row in conn.execute("SELECT run_id FROM task_process")}
    taken |= {row[0] for row in conn.execute(
        "SELECT model_run_id FROM geometry_state WHERE model_run_id IS NOT NULL"
        " UNION SELECT mesh_run_id FROM geometry_state WHERE mesh_run_id IS NOT NULL"
        " UNION SELECT run_id FROM cfd_task WHERE run_id IS NOT NULL"
        " UNION SELECT run_id FROM datcom_task WHERE run_id IS NOT NULL")}
    return taken


def claim_datcom_batch(conn: sqlite3.Connection) -> dict | None:
    """领一批低保真任务：同一个 `geometry_id` + 同一个 `altitude_m` 用一次 DATCOM 调用算完。

    返回 `{"geometry_id", "altitude_m", "condition_ids", "run_id"}`；这批行全部置
    `running` 并记下同一个 `run_id`（批次级产物，`condition_id` 写 `shared`）。
    """
    row = conn.execute(
        "SELECT t.geometry_id AS geometry_id, c.altitude_m AS altitude_m FROM datcom_task t"
        " JOIN condition c ON c.condition_id = t.condition_id"
        " WHERE t.status='queued'"
        " GROUP BY t.geometry_id, c.altitude_m"
        " ORDER BY t.geometry_id, c.altitude_m LIMIT 1",
    ).fetchone()
    if row is None:
        return None
    geometry_id, altitude_m = row["geometry_id"], row["altitude_m"]
    condition_ids = [item["condition_id"] for item in conn.execute(
        "SELECT t.condition_id FROM datcom_task t"
        " JOIN condition c ON c.condition_id = t.condition_id"
        " WHERE t.status='queued' AND t.geometry_id=? AND c.altitude_m=?"
        " ORDER BY t.condition_id",
        (geometry_id, altitude_m),
    )]
    run_id = make_run_id(geometry_id, SHARED_CONDITION_ID, taken_run_ids(conn))
    stamp = utc_sql()
    for condition_id in condition_ids:
        conn.execute(
            "UPDATE datcom_task SET status='running', run_id=?, updated_at=?"
            " WHERE geometry_id=? AND condition_id=?",
            (run_id, stamp, geometry_id, condition_id),
        )
    conn.commit()
    return {"geometry_id": geometry_id, "altitude_m": altitude_m,
            "condition_ids": condition_ids, "run_id": run_id}


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
    """由 `run_id` 反查它属于哪个阶段、哪个任务（`run_id` 本身不含阶段名）。"""
    row = conn.execute(
        "SELECT geometry_id, model_run_id, mesh_run_id FROM geometry_state"
        " WHERE model_run_id = ? OR mesh_run_id = ?",
        (run_id, run_id),
    ).fetchone()
    if row is not None:
        stage = "spaceclaim" if row["model_run_id"] == run_id else "fluent_meshing"
        return {"stage": stage, "geometry_id": row["geometry_id"], "condition_id": None,
                "table": "geometry_state", "field": STAGE_STATUS_FIELD[stage]}
    row = conn.execute(
        "SELECT geometry_id, condition_id FROM cfd_task WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is not None:
        return {"stage": "fluent_solution", "geometry_id": row["geometry_id"],
                "condition_id": row["condition_id"], "table": "cfd_task", "field": "status"}
    row = conn.execute(
        "SELECT geometry_id, condition_id FROM datcom_task WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is not None:
        return {"stage": "datcom", "geometry_id": row["geometry_id"],
                "condition_id": row["condition_id"], "table": "datcom_task", "field": "status"}
    return None


def mark_task(
    conn: sqlite3.Connection,
    run: dict,
    status: str,
    *,
    bump_attempts: bool = False,
    run_id: str | None = None,
    result_dir: str | None = None,
    log_path: str | None = None,
) -> None:
    """把一条任务/几何阶段推到指定状态。状态词表由 CHECK 兜底，这里只挡拼写错误。

    自己提交：状态推进必须当场落地。早期依赖调用方后续 commit 时，失败路径被
    `record_report()` 的 commit 顺手带上，成功路径（`running → reviewing`）却什么都没写，
    表现为 dispatch 打印了 `→ reviewing` 而库里仍是 `running`、`model_run_id` 为空。
    """
    if status not in TASK_STATUSES:
        raise WriteError(f"未知状态：{status}（只能是 {TASK_STATUSES}）")
    table, field = run["table"], run["field"]
    stamp = utc_sql()
    if table == "geometry_state":
        conn.execute(
            f"UPDATE geometry_state SET {field}=?, updated_at=? WHERE geometry_id=?",
            (status, stamp, run["geometry_id"]),
        )
        # 几何级阶段用 model_run_id / mesh_run_id 反查是哪一次运行（run_id 本身不含阶段名）
        run_column = "model_run_id" if field == "model_status" else "mesh_run_id"
        if run_id is not None:
            conn.execute(
                f"UPDATE geometry_state SET {run_column}=? WHERE geometry_id=?",
                (run_id, run["geometry_id"]),
            )
        conn.commit()
        return
    where = "geometry_id=? AND condition_id=?"
    key = (run["geometry_id"], run["condition_id"])
    conn.execute(f"UPDATE {table} SET status=?, updated_at=? WHERE {where}", (status, stamp, *key))
    if bump_attempts:
        conn.execute(f"UPDATE {table} SET attempts=attempts+1 WHERE {where}", key)
    if run_id is not None:
        conn.execute(f"UPDATE {table} SET run_id=? WHERE {where}", (run_id, *key))
    if result_dir is not None:
        conn.execute(f"UPDATE {table} SET result_dir=? WHERE {where}", (result_dir, *key))
    if log_path is not None:
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
) -> str:
    """写一条上报单。`report_id = "p" + sha256(stage+run_id+created_at)[:12]`。"""
    if stage not in STAGES:
        raise WriteError(f"未知阶段：{stage}")
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
REQUEUE_ALLOWED_STATUSES = ("rejected", "cancelled", "pending")


def _requeue_target(conn: sqlite3.Connection, stage: str,
                    geometry_id: str, condition_id: str | None):
    """定位要重排的那一行，返回 `(status, updates, run_id)`。

    几何级阶段（建模/网格）读 `geometry_state`；按工况的阶段读 `cfd_task` / `datcom_task`。
    找不到就报错——不要静默返回"没有可重排的任务"。
    """
    if stage in ("spaceclaim", "fluent_meshing"):
        column = "model_status" if stage == "spaceclaim" else "mesh_status"
        run_column = "model_run_id" if stage == "spaceclaim" else "mesh_run_id"
        row = conn.execute(
            f"SELECT {column} AS status, {run_column} AS run_id FROM geometry_state"
            " WHERE geometry_id=?", (geometry_id,)).fetchone()
        if row is None:
            raise WriteError(f"库里没有几何 {geometry_id}")
        return ("geometry_state", column), row["status"], row["run_id"]

    table = "cfd_task" if stage == "fluent_solution" else "datcom_task"
    if not condition_id:
        raise WriteError(f"{stage} 是按工况的任务，必须给 --condition-id")
    row = conn.execute(
        f"SELECT status, run_id FROM {table} WHERE geometry_id=? AND condition_id=?",
        (geometry_id, condition_id)).fetchone()
    if row is None:
        raise WriteError(f"库里没有 {stage} 任务：{geometry_id} / {condition_id}")
    return (table, "status"), row["status"], row["run_id"]


def requeue_task(
    conn: sqlite3.Connection,
    *,
    stage: str,
    geometry_id: str,
    condition_id: str | None = None,
    resolve_reports: bool = False,
    reason: str | None = None,
) -> dict:
    """把一条 `rejected` / `cancelled` 的任务放回 `pending`，等 `watch.py` 重排进队列。

    只动"排队状态"，**不改产物、不删审核历史、不重置重试次数**：重试次数是按 `review`
    表里的拒绝记录算出来的，清它等于篡改历史。真跑过预算就先按预算停手，
    由人来判断是环境问题还是模型问题。
    """
    if stage not in STAGES:
        raise WriteError(f"未知阶段：{stage}（只能是 {STAGES}）")
    (table, column), status, run_id = _requeue_target(conn, stage, geometry_id, condition_id)

    if status == "running":
        raise WriteError(f"任务还在 running（run_id {run_id}）：先 `control.py stop` 收掉进程再重排")
    if status not in REQUEUE_ALLOWED_STATUSES:
        raise WriteError(
            f"{stage} / {geometry_id} 当前是 {status}，只有 {REQUEUE_ALLOWED_STATUSES} 能重排")

    # 遗留的 pid 登记必须先清掉：它还占着 run_id，会让新任务领不到号。
    if run_id:
        stale = conn.execute("SELECT pid FROM task_process WHERE run_id=?", (run_id,)).fetchone()
        if stale is not None:
            if pid_alive(int(stale["pid"])):
                raise WriteError(
                    f"run_id {run_id} 的进程 {stale['pid']} 还活着：先 `control.py stop` 再重排")
            conn.execute("DELETE FROM task_process WHERE run_id=?", (run_id,))

    kind = REQUEUE_STAGE_KIND[stage]
    rejections = _rejections_since_last_pass(conn, kind, geometry_id, condition_id)
    stamp = utc_sql()
    if table == "geometry_state":
        # run_id 保留：它记的是"哪一次被否了"，是证据；领取时会生成新的 run_id 覆盖它
        conn.execute(
            f"UPDATE geometry_state SET {column}='pending', updated_at=? WHERE geometry_id=?",
            (stamp, geometry_id))
    else:
        conn.execute(
            f"UPDATE {table} SET status='pending', updated_at=? WHERE geometry_id=? AND condition_id=?",
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


def reclaim_zombies(conn: sqlite3.Connection) -> list[dict]:
    """僵死回收：`task_process` 里 pid 已不存在 → `rejected` + `attempts+1` + 上报单。"""
    reclaimed: list[dict] = []
    for row in conn.execute("SELECT run_id, pid FROM task_process").fetchall():
        run_id, pid = row["run_id"], int(row["pid"])
        if pid_alive(pid):
            continue
        run = resolve_run(conn, run_id)
        conn.execute("DELETE FROM task_process WHERE run_id=?", (run_id,))
        if run is None:
            conn.commit()
            reclaimed.append({"run_id": run_id, "pid": pid, "stage": None})
            continue
        attempts = _attempts_of(conn, run)
        mark_task(conn, run, "rejected", bump_attempts=True)
        record_report(
            conn, stage=run["stage"], geometry_id=run["geometry_id"], run_id=run_id,
            attempts=attempts + 1, condition_id=run["condition_id"],
            last_error=f"进程 {pid} 已不存在，任务停在 running（僵死回收）",
        )
        reclaimed.append({"run_id": run_id, "pid": pid, **run})
    return reclaimed


def _attempts_of(conn: sqlite3.Connection, run: dict) -> int:
    if run["table"] == "geometry_state":
        return 0                      # 几何阶段没有 attempts 字段，用 report 计数
    row = conn.execute(
        f"SELECT attempts FROM {run['table']} WHERE geometry_id=? AND condition_id=?",
        (run["geometry_id"], run["condition_id"]),
    ).fetchone()
    return int(row["attempts"]) if row else 0


def revive_deleted_artifacts(conn: sqlite3.Connection) -> list[str]:
    """产物被 `gc` 删过（路径为 NULL）但又有 `pending` 工况 → 建模/网格回落 `pending`。

    只回退**曾经通过**的阶段（`passed` 且路径为 NULL）。判据故意很窄：
    - `pending` / `queued` 本来就是等重跑，不用动；
    - `running` 正在跑，动它会打断；
    - `rejected` 重跑要走重试预算，不能由 watcher 悄悄重置；
    - `cancelled` 按约定**永不自动恢复**，必须显式 `start`。
    """
    rows = conn.execute(
        "SELECT s.geometry_id, s.model_status, s.mesh_status, s.model_path, s.mesh_path"
        " FROM geometry_state s"
        " WHERE EXISTS (SELECT 1 FROM cfd_task t"
        "               WHERE t.geometry_id = s.geometry_id AND t.status = 'pending')"
        "   AND (s.model_path IS NULL OR s.mesh_path IS NULL)"
    ).fetchall()
    revived: list[str] = []
    stamp = utc_sql()
    for row in rows:
        geometry_id = row["geometry_id"]
        changed = False
        if row["model_path"] is None and row["model_status"] == "passed":
            conn.execute(
                "UPDATE geometry_state SET model_status='pending', updated_at=? WHERE geometry_id=?",
                (stamp, geometry_id),
            )
            changed = True
        if row["mesh_path"] is None and row["mesh_status"] == "passed":
            conn.execute(
                "UPDATE geometry_state SET mesh_status='pending', updated_at=? WHERE geometry_id=?",
                (stamp, geometry_id),
            )
            changed = True
        if changed:
            revived.append(geometry_id)
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
    for table in ("cfd_task", "datcom_task"):
        counts[table] = {row["status"]: row["n"] for row in conn.execute(
            f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status")}
    geometry_rows = []
    for row in conn.execute(
        "SELECT g.geometry_id, g.label, g.fin_section_type, g.nose_shape, g.L, g.D,"
        " s.model_status, s.mesh_status, s.model_path, s.mesh_path, s.deletable, s.updated_at"
        " FROM geometry g LEFT JOIN geometry_state s ON s.geometry_id = g.geometry_id"
        " ORDER BY g.geometry_id"
    ):
        entry = dict(row)
        entry["deletable"] = bool(entry.get("deletable"))
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

    **这是唯一的入库凭证**：产物路径、气动系数、`passed` 状态都只在 `verdict='passed'`
    时写入。连续 `SELF_REPAIR_BUDGET + 1` 次不过就写一条 `open` 上报单并停手。
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
    if kind in ("solution", "datcom") and not condition_id:
        raise WriteError(f"{kind} 审核必须给出 condition_id")
    if kind in ("model", "mesh") and condition_id:
        raise WriteError(f"{kind} 是按几何审核的，condition_id 必须为空")
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
    if kind == "model":
        if verdict == "passed" and not rel_path:
            raise WriteError("模型审核通过必须给出产物路径")
        conn.execute(
            "UPDATE geometry_state SET model_status=?, model_path=?, model_run_id=?, updated_at=?"
            " WHERE geometry_id=?",
            (("passed" if verdict == "passed" else "rejected"),
             rel_path if verdict == "passed" else None,
             # run_id 一律记下：被否决的运行也要能反查是哪一次、重跑时也不用猜
             run_id, stamp, geometry_id),
        )
    elif kind == "mesh":
        if verdict == "passed" and not rel_path:
            raise WriteError("网格审核通过必须给出产物路径")
        conn.execute(
            "UPDATE geometry_state SET mesh_status=?, mesh_path=?, mesh_run_id=?, updated_at=?"
            " WHERE geometry_id=?",
            (("passed" if verdict == "passed" else "rejected"),
             rel_path if verdict == "passed" else None,
             run_id, stamp, geometry_id),
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
                f"UPDATE {table} SET status='passed', CL=?, CD=?, CM=?, run_id=?,"
                " result_dir=COALESCE(?, result_dir), log_path=COALESCE(?, log_path),"
                " review_id=?, updated_at=? WHERE geometry_id=? AND condition_id=?",
                (payload.get("CL"), payload.get("CD"), payload.get("CM"), run_id,
                 payload.get("result_dir"), payload.get("log_path"),
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
                f"UPDATE {table} SET status='rejected', run_id=?, updated_at=?"
                " WHERE geometry_id=? AND condition_id=?",
                (run_id, stamp, geometry_id, condition_id),
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


def export_queue(conn: sqlite3.Connection, queue: str, *, prune: bool = True) -> list[str]:
    """把队列目录重写成数据库的只读视图：一个通过的产物一个指针 JSON。

    下游领任务只看数据库；这个目录整目录删掉也能重建。产物文件缺失的行直接跳过——
    指向不存在的文件比没有指针更坏。
    """
    kind = "model" if queue == "scdoc_for_fluent" else "mesh"
    column, run_column = (
        ("model_path", "model_run_id") if kind == "model" else ("mesh_path", "mesh_run_id")
    )
    directory = queue_directory(queue)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    expected: set[str] = set()
    rows = conn.execute(
        f"SELECT geometry_id, {column} AS path, {run_column} AS run_id FROM geometry_state"
        f" WHERE {run_column} IS NOT NULL AND {column} IS NOT NULL"
        " ORDER BY geometry_id"
    ).fetchall()
    for row in rows:
        artifact_path = (WORKSPACE_ROOT / row["path"]).resolve()
        if not artifact_path.exists():
            print(f"跳过 {row['run_id']}：产物已不在磁盘上（{row['path']}）", file=sys.stderr)
            continue
        review = conn.execute(
            "SELECT review_id, created_at FROM review WHERE kind=? AND run_id=? AND verdict='passed'"
            " ORDER BY created_at DESC LIMIT 1",
            (kind, row["run_id"]),
        ).fetchone()
        if review is None:
            print(f"跳过 {row['run_id']}：库里没有对应的通过记录", file=sys.stderr)
            continue
        fingerprint = file_fingerprint(artifact_path)
        pointer = {
            "schemaVersion": QUEUE_POINTER_SCHEMA_VERSION,
            "queue": queue,
            "run_id": row["run_id"],
            "geometry_id": row["geometry_id"],
            "condition_id": SHARED_CONDITION_ID,
            "artifact": fingerprint,
            "review_id": review["review_id"],
            "approved_at": review["created_at"],
        }
        target = directory / f"{row['run_id']}.json"
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
    """自动判定 `deletable`：该几何至少有一行 `cfd_task`，且所有 `cfd_task` 都 `passed`。

    人工可以把某一行的 `deletable` 强制置 1（`UPDATE`），这里只负责把自动判定的
    结果写回，不会把人工置的 1 改回 0——只升不降，否则人工决定会被自动规则覆盖。
    """
    marked: list[str] = []
    rows = conn.execute(
        "SELECT s.geometry_id, s.deletable,"
        "       (SELECT COUNT(*) FROM cfd_task t WHERE t.geometry_id = s.geometry_id) AS total,"
        "       (SELECT COUNT(*) FROM cfd_task t WHERE t.geometry_id = s.geometry_id"
        "          AND t.status = 'passed') AS passed"
        " FROM geometry_state s"
    ).fetchall()
    stamp = utc_sql()
    for row in rows:
        if row["deletable"]:
            continue
        if row["total"] > 0 and row["total"] == row["passed"]:
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
    """列出 `deletable=1` 的几何要删哪些文件，以及跳过原因。"""
    candidates: list[dict] = []
    rows = conn.execute(
        "SELECT geometry_id, model_path, mesh_path FROM geometry_state"
        " WHERE deletable=1 ORDER BY geometry_id"
    ).fetchall()
    for row in rows:
        geometry_id = row["geometry_id"]
        entry = {"geometry_id": geometry_id, "delete": [], "skipped": []}
        for column, stage in (("model_path", "spaceclaim"), ("mesh_path", "fluent_meshing")):
            rel = row[column]
            if not rel:
                continue
            path = (WORKSPACE_ROOT / rel).resolve()
            reason = _gc_guard(path, geometry_id, stage)
            if reason:
                entry["skipped"].append(f"{rel}：{reason}")
                continue
            entry["delete"].append(path)
            # 同目录里跟这个几何同名的日志一并删；点云不在这个目录，永远不碰
            for sibling in path.parent.glob(f"{geometry_id}_*.log"):
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
            # 产物没了，但"曾经验过"这件事保留：状态仍是 passed，路径与运行号清空
            conn.execute(
                "UPDATE geometry_state SET model_path=NULL, mesh_path=NULL, model_run_id=NULL,"
                " mesh_run_id=NULL, deletable=0, updated_at=? WHERE geometry_id=?",
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
# 14. 命令：claim（dispatch.py 用）
# =============================================================================
def cmd_claim(args) -> int:
    db_path = resolve_database(args.database)
    conn = connect(db_path)
    try:
        if args.stage in STAGE_STATUS_FIELD:
            claimed = [
                {"stage": args.stage, "geometry_id": geometry_id, "condition_id": None}
                for geometry_id in claim_geometry_stage(conn, args.stage, args.limit)
            ]
        elif args.stage == "fluent_solution":
            claimed = [
                {"stage": args.stage, "geometry_id": gid, "condition_id": cid}
                for gid, cid in claim_cfd_tasks(conn, args.limit)
            ]
        elif args.stage == "datcom":
            claimed = [
                {"stage": args.stage, "geometry_id": gid, "condition_id": cid}
                for gid, cid in claim_datcom_tasks(conn, args.limit)
            ]
        else:
            raise SystemExit(f"未知阶段：{args.stage}（只能是 {STAGES}）")
        print(json.dumps(claimed, ensure_ascii=False))
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
        "requeue", help="把 rejected / cancelled 的任务放回 pending，等 watch.py 重排")
    requeue.add_argument("--stage", required=True, choices=list(STAGES))
    requeue.add_argument("--geometry-id", required=True)
    requeue.add_argument("--condition-id", help="求解 / 低保真按工况，必须给")
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

    claim = subparsers.add_parser("claim", help="领取 queued 之前的那一步：pending → queued")
    claim.add_argument("--stage", required=True, choices=list(STAGES))
    claim.add_argument("--limit", type=int, default=1)
    claim.set_defaults(func=cmd_claim)

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
