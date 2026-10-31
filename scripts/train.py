# -*- coding: utf-8 -*-
"""训练闭环：用已通过审核的高保真样本训一个代理模型，再把"不确定"的点补回数据库。

第一版刻意做小、做诚实：

- 样本 = `cfd_task.status='passed'` 的行（**只用高保真**；DATCOM 留作后续多保真升级）。
- 模型 = **最近邻代理**：把 16 个几何参数 + 3 个工况参数归一化后，按最近的 k 个样本
  取加权平均。它没有可训练的权重，所以 `model_path` 记的是本轮快照而不是权重文件。
- 不确定度 = 候选点到最近样本的归一化距离。距离越大越"不知道"。
- 补点：把候选格点上"离已有样本足够远"的 (几何, 工况) 直接插进 `cfd_task`，
  `origin='active_learning'`、`round_id` 指回本轮，状态 `pending`。**不另设建议表**。

阈值（`UNCERTAINTY_THRESHOLD` / `MAX_PROPOSALS` / 候选格点）都是 `myplan.md` §12 的
待定项，先给保守默认值：宁可少补，也不要拿一堆重复点把库填满。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    数据库      命令行 `--database` > 环境变量 `MISSILE_DB` > `workspace.json`（与 db.py 共用）。
    样本        `cfd_task` 里 `status='passed'` 且 `CL/CD/CM` 齐全的行。
输出
    `training_round` 一行（`round_id` / `data_version` / `metrics_json` / `proposed_rows`）
    `data_lib/runtime/training/<round_id>/report.json`（本轮明细，可复现）
    `cfd_task` 追加 `origin='active_learning'` 的 `pending` 行（`watch.py` 下一轮自动入队）
参数配置
    K_NEIGHBOURS / UNCERTAINTY_THRESHOLD / MAX_PROPOSALS / CANDIDATE_* 格点
覆盖优先级
    命令行 > 本节默认值。时间戳一律 UTC（由 db.py 统一生成）。
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import db  # noqa: E402

# =============================================================================
# 1. 参数配置区（§12 待定项先给保守默认值）
# =============================================================================
K_NEIGHBOURS = 3
UNCERTAINTY_THRESHOLD = 0.15      # 归一化距离；大于它就算"不知道"
MAX_PROPOSALS = 20                # 每轮最多补多少行
REPORT_DIR_REL = "data_lib/runtime/training"
# 候选格点：只补这些工况组合（几何仍取库里已有的 16 元组）。
# 几何参数本身怎么补（新的 L/D/L_N）还没定，所以默认不为新几何补点。
CANDIDATE_MACH = (0.6, 0.8, 1.0, 1.2, 2.0)
CANDIDATE_ALPHA = (0.0, 2.0, 4.0, 6.0, 8.0)
CANDIDATE_ALTITUDE = (0.0, 5000.0, 10000.0)
COEFFICIENTS = ("CL", "CD", "CM")


# =============================================================================
# 2. 样本与归一化
# =============================================================================
GEOMETRY_KEYS = db.GEOMETRY_LENGTH_KEYS + ("thickness_percent",)
# 归一化尺度下限，顺序与 _feature_vector 一致：11 个长度、厚度、mach、攻角、高度。
FALLBACK_SCALES = (0.1,) * len(db.GEOMETRY_LENGTH_KEYS) + (1.0, 0.1, 5.0, 5000.0)


def load_samples(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT g.*, t.geometry_id AS geometry_id, t.condition_id AS condition_id,"
        " c.mach, c.alpha_deg, c.altitude_m, t.CL, t.CD, t.CM"
        " FROM cfd_task t"
        " JOIN geometry g ON g.geometry_id = t.geometry_id"
        " JOIN condition c ON c.condition_id = t.condition_id"
        " WHERE t.status='passed' AND t.CL IS NOT NULL AND t.CD IS NOT NULL AND t.CM IS NOT NULL"
        " ORDER BY g.geometry_id, c.mach, c.alpha_deg, c.altitude_m"
    ).fetchall()
    return [dict(row) for row in rows]


def _feature_vector(row) -> list[float]:
    values = [float(row[key]) for key in GEOMETRY_KEYS]
    values += [float(row["mach"]), float(row["alpha_deg"]), float(row["altitude_m"])]
    return values


def _scales(rows: list[dict]) -> list[float]:
    """每一维的归一化尺度 = max(样本标准差, 该维的物理下限)。

    样本标准差在"所有样本都在同一高度"这类情况下是 0，会把那一维的距离放大成天文数字，
    相对排序也就没意义了。所以给每维一个物理意义上的下限：长度 0.1 m、厚度 1%、
    Mach 0.1、攻角 5°、高度 5000 m。这样"没见过的高度差 10000 m"读作距离 2.0，
    仍然远超阈值，但和别的维可比。
    """
    columns = list(zip(*[_feature_vector(row) for row in rows]))
    scales = []
    for index, column in enumerate(columns):
        mean = sum(column) / len(column)
        variance = sum((value - mean) ** 2 for value in column) / len(column)
        deviation = math.sqrt(variance)
        scales.append(max(deviation, FALLBACK_SCALES[index]))
    return scales


def _distance(left: list[float], right: list[float], scales: list[float]) -> float:
    return math.sqrt(sum(((a - b) / s) ** 2 for a, b, s in zip(left, right, scales)))


def predict(samples: list[dict], features: list[float], scales: list[float]) -> dict:
    """k 近邻加权平均；不确定度 = 到最近邻居的归一化距离。"""
    ranked = sorted(
        ((_distance(features, _feature_vector(row), scales), row) for row in samples),
        key=lambda item: item[0],
    )
    neighbours = ranked[:K_NEIGHBOURS]
    weights = [1.0 / (distance + 1e-9) for distance, _ in neighbours]
    total = sum(weights)
    prediction = {
        key: sum(weight * float(row[key]) for weight, (_, row) in zip(weights, neighbours)) / total
        for key in COEFFICIENTS
    }
    prediction["uncertainty"] = ranked[0][0]
    prediction["nearest"] = {
        "geometry_id": ranked[0][1]["geometry_id"],
        "condition_id": ranked[0][1]["condition_id"],
    }
    return prediction


def leave_one_out(samples: list[dict], scales: list[float]) -> dict:
    """留一误差：每个样本用其余样本预测一次，报三个系数的相对误差。"""
    errors = {key: [] for key in COEFFICIENTS}
    for index, row in enumerate(samples):
        others = samples[:index] + samples[index + 1:]
        if not others:
            continue
        predicted = predict(others, _feature_vector(row), scales)
        for key in COEFFICIENTS:
            actual = float(row[key])
            if abs(actual) > 1e-9:
                errors[key].append(abs(predicted[key] - actual) / abs(actual))
    summary = {}
    for key, values in errors.items():
        values.sort()
        summary[key] = {
            "count": len(values),
            "medianRel": values[len(values) // 2] if values else None,
            "p90Rel": values[int(len(values) * 0.9)] if values else None,
            "maxRel": values[-1] if values else None,
        }
    return summary


# =============================================================================
# 3. 主流程
# =============================================================================
def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="train.py",
                                     description="训练一轮代理模型并把补点写回数据库。")
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json）")
    parser.add_argument("--max-proposals", type=int, default=MAX_PROPOSALS)
    parser.add_argument("--threshold", type=float, default=UNCERTAINTY_THRESHOLD)
    parser.add_argument("--dry-run", action="store_true", help="只算不写库")
    args = parser.parse_args(argv)

    conn = db.connect(db.resolve_database(args.database))
    try:
        samples = load_samples(conn)
        if len(samples) < 2:
            print(f"样本不足（{len(samples)} 条）：先让高保真链路跑出至少 2 条 "
                  "`cfd_task.status='passed'` 的样本，再训练。")
            return 1

        scales = _scales(samples)
        metrics = leave_one_out(samples, scales)
        data_version = f"{len(samples)}samples:{samples[0]['geometry_id']}..{samples[-1]['geometry_id']}"
        print(f"样本 {len(samples)} 条；留一误差（中位数）："
              + ", ".join(f"{key} {metrics[key]['medianRel']:.2%}"
                          for key in COEFFICIENTS if metrics[key]["medianRel"] is not None))

        # 候选：库里已有的每个几何 × 候选工况格点，已存在的跳过
        geometries = conn.execute(
            "SELECT * FROM geometry ORDER BY geometry_id").fetchall()
        existing = {(row["geometry_id"], row["condition_id"]) for row in conn.execute(
            "SELECT geometry_id, condition_id FROM cfd_task")}
        proposals = []
        for geometry in geometries:
            for mach in CANDIDATE_MACH:
                for alpha in CANDIDATE_ALPHA:
                    for altitude in CANDIDATE_ALTITUDE:
                        condition = db.identify_condition(
                            {"mach": mach, "alpha_deg": alpha, "altitude_m": altitude})
                        if (geometry["geometry_id"], condition["condition_id"]) in existing:
                            continue
                        features = [float(geometry[key]) for key in GEOMETRY_KEYS]
                        features += [float(mach), float(alpha), float(altitude)]
                        predicted = predict(samples, features, scales)
                        if predicted["uncertainty"] < args.threshold:
                            continue
                        proposals.append({
                            "geometry_id": geometry["geometry_id"],
                            "mach": float(mach),
                            "alpha_deg": float(alpha),
                            "altitude_m": float(altitude),
                            "uncertainty": predicted["uncertainty"],
                            "predicted": {key: predicted[key] for key in COEFFICIENTS},
                            "nearest": predicted["nearest"],
                        })
        proposals.sort(key=lambda item: -item["uncertainty"])
        proposals = proposals[:args.max_proposals]

        print(f"候选补点 {len(proposals)} 条（阈值 {args.threshold}，上限 {args.max_proposals}）")
        for item in proposals[:5]:
            print(f"  {item['geometry_id']} / Ma={item['mach']:g} α={item['alpha_deg']:g}° "
                  f"h={item['altitude_m']:g}m  不确定度 {item['uncertainty']:.3f}")

        if args.dry_run:
            print("试运行：未写库。")
            return 0

        round_id = db.start_training_round(conn, data_version=data_version,
                                           metrics_json=json.dumps(metrics, ensure_ascii=False))
        written = 0
        for item in proposals:
            geometry_row = conn.execute("SELECT * FROM geometry WHERE geometry_id=?",
                                        (item["geometry_id"],)).fetchone()
            params = {key: geometry_row[key] for key in db.GEOMETRY_LENGTH_KEYS}
            params.update({
                db.FIN_SECTION_KEY: geometry_row["fin_section_type"],
                "thickness_percent": geometry_row["thickness_percent"],
                "flat_percent": geometry_row["flat_percent"],
                "le_radius": geometry_row["le_radius"],
                "le_radius_index": geometry_row["le_radius_index"],
                "max_thickness_tenths": geometry_row["max_thickness_tenths"],
                db.NOSE_SHAPE_KEY: geometry_row["nose_shape"],
            })
            geometry_identity = db.identify_geometry(params)
            condition_identity = db.identify_condition({
                "mach": item["mach"], "alpha_deg": item["alpha_deg"],
                "altitude_m": item["altitude_m"],
            })
            if db.propose_task(conn, geometry_identity, condition_identity, round_id):
                written += 1
        db.finish_training_round(conn, round_id, status="passed",
                                 metrics_json=json.dumps(metrics, ensure_ascii=False),
                                 proposed_rows=written)

        report_dir = db.WORKSPACE_ROOT / REPORT_DIR_REL / round_id
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "report.json").write_text(json.dumps({
            "round_id": round_id, "data_version": data_version, "samples": len(samples),
            "metrics": metrics, "proposals": proposals, "written": written,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"训练轮次 {round_id}：新增 {written} 条 `pending` 任务 → "
              f"{db.workspace_rel(report_dir / 'report.json')}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    db._configure_output()
    raise SystemExit(run())
