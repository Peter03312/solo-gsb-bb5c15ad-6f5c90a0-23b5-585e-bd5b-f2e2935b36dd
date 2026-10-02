"""Flask 后端：原稿查询、修补预览、预览确认。"""

import json
import os
import uuid

from flask import Flask, jsonify, request, send_from_directory

from db import Database
from solver import diagnose_unlocks, solve_repair_top
from validate import ValidationError, canonical, validate_design
from weave import find_violations

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "weave.db"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 修补预览最多并列枚举的同样最优候选数
MAX_CANDIDATES = 3
# 无解诊断最多返回的互异最少解锁集合数
MAX_UNLOCK_SETS = 3

db = Database(DB_PATH)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


def _design_payload(design):
    """原稿及其超限段，供页面展示。"""
    payload = dict(design)
    payload["violations"] = find_violations(
        design["grid"], design["rows"], design["cols"], design["k"])
    payload["onesCount"] = sum(v for row in design["grid"] for v in row)
    return payload


def _canonical_text(obj):
    """规范化输入的逐字节序列化，用于草稿与预览输入的一致性比对。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@app.get("/api/state")
def get_state():
    return jsonify(_design_payload(db.get_design()))


@app.post("/api/preview")
def preview():
    try:
        rows, cols, grid, locks, k, ones_range = validate_design(
            request.get_json(silent=True))
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    input_obj = canonical(rows, cols, grid, locks, k, ones_range)
    input_text = _canonical_text(input_obj)
    violations = find_violations(grid, rows, cols, k)
    solved = solve_repair_top(rows, cols, k, grid, locks, ones_range,
                              limit=MAX_CANDIDATES)

    if solved is None:
        # 无可行修补（含格值1总数区间不可达）：保留原稿，仅报无解（不发放令牌）
        return jsonify({
            "solvable": False,
            "violations": violations,
            "input": input_obj,
            "onesCount": sum(v for row in grid for v in row),
            "onesRange": input_obj["onesRange"],
        })

    candidates = solved["candidates"]
    first = candidates[0]
    # repaired/flips/flipCount 始终指向首个（字典序最小）方案，旧字段保持不变
    result = {
        "repaired": first["repaired"],
        "flips": first["flips"],
        "flipCount": len(first["flips"]),
        "candidates": candidates,
    }
    token = uuid.uuid4().hex
    # 预览绑定生成时的原稿版本；确认时若该版本已变则拒绝
    base_version = db.get_design()["version"]
    db.save_preview(token, input_text, result, base_version)
    return jsonify({
        "solvable": True,
        "token": token,
        "violations": violations,
        "input": input_obj,
        "onesCount": sum(v for row in grid for v in row),
        "repairedOnesCount": first["onesCount"],
        "onesRange": input_obj["onesRange"],
        # 前 3 个同样最优、互不相同的候选（不足 3 个按实际数量返回），
        # 按行展开格值字典序升序；确认时用 candidate 序号选择
        "candidateCount": len(candidates),
        **result,
    })


@app.post("/api/confirm")
def confirm():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400

    token = body.get("token")
    if not isinstance(token, str) or not token:
        return jsonify({"error": "缺少预览令牌"}), 400

    # 选择预览中的哪个并列最优候选：省略/null 表示首个（与旧请求一致）。
    # 必须是非负整数；实际越界（超出该令牌返回的候选数量）在确认事务内
    # 判定，按 400 拒绝且不消费令牌。
    raw_candidate = body.get("candidate", 0)
    if raw_candidate is None:
        candidate_index = 0
    elif isinstance(raw_candidate, int) and not isinstance(raw_candidate, bool) \
            and raw_candidate >= 0:
        candidate_index = raw_candidate
    else:
        return jsonify({"error": "候选方案序号必须是非负整数"}), 400

    try:
        rows, cols, grid, locks, k, ones_range = validate_design(body)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    # 原子确认：令牌未消费、草稿与预览输入一致（含格值1区间）、预览绑定的
    # 原稿版本未变、候选序号属于该预览，四者同时成立才覆盖原稿（版本仅 +1、
    # 区间随原稿持久化）并消费令牌；否则整体回滚。
    input_text = _canonical_text(canonical(rows, cols, grid, locks, k, ones_range))
    design, reason = db.commit_preview(token, input_text, candidate_index)
    if design is None:
        message = {
            "consumed": "预览令牌不存在或已被确认，请重新预览",
            "mismatch": "预览后稿件已变动，旧预览已失效，请重新预览",
            "stale": "原稿已被其他确认更新，旧预览已失效，请重新预览",
            "bad_candidate": "所选候选方案不在该预览返回的范围内，请从预览候选中选择",
        }[reason]
        # 越界/伪造的候选序号是客户端错误（400），且令牌未被消费
        status = 400 if reason == "bad_candidate" else 409
        return jsonify({"error": message}), status

    return jsonify({"state": _design_payload(design)})


@app.post("/api/diagnose")
def diagnose():
    """无可行修补草稿的只读自诊断（不落库、不发令牌、不改任何稿件）。

    保持尺寸、格值、K、可选 onesRange 不变，求使修补变为可行所需解除的
    锁定格「全局最少数量」，并按解锁坐标列表字典序返回前 3 个互异解锁
    集合及各自一份可行修补示例（满足环向浮长与总数区间，未解锁锁定格
    保持原值）。即使全部解锁仍无解时明确报告结构性无解，不给出解锁建议。
    """
    try:
        rows, cols, grid, locks, k, ones_range = validate_design(
            request.get_json(silent=True))
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    input_obj = canonical(rows, cols, grid, locks, k, ones_range)
    violations = find_violations(grid, rows, cols, k)
    ones_count = sum(v for row in grid for v in row)

    # 先按当前锁定做常规求解：已可修补时无需解锁（minUnlockCount=0）
    solved = solve_repair_top(rows, cols, k, grid, locks, ones_range, limit=1)
    if solved is not None:
        first = solved["candidates"][0]
        return jsonify({
            "solvable": True,
            "structural": False,
            # 诊断仅供查看：不发令牌、不落库
            "minUnlockCount": 0,
            "unlockSetCount": 1,
            "solutions": [{
                "unlocks": [],
                "repaired": first["repaired"],
                "changes": first["flips"],
                "onesCount": first["onesCount"],
            }],
            "violations": violations,
            "input": input_obj,
            "onesCount": ones_count,
            "onesRange": input_obj["onesRange"],
        })

    # 当前锁定下无可行修补：求最少解锁集合
    diag = diagnose_unlocks(rows, cols, k, grid, locks, ones_range,
                            limit=MAX_UNLOCK_SETS)
    if diag is None:
        # 即使解除全部锁定仍无解：结构性无解（如 1×1、单行/单列，
        # 或 onesRange 在浮长规则下本身不可达），不给出误导性解锁建议
        return jsonify({
            "solvable": False,
            "structural": True,
            "violations": violations,
            "input": input_obj,
            "onesCount": ones_count,
            "onesRange": input_obj["onesRange"],
        })

    return jsonify({
        "solvable": False,
        "structural": False,
        "minUnlockCount": diag["unlock_count"],
        "unlockSetCount": len(diag["solutions"]),
        "solutions": diag["solutions"],
        "violations": violations,
        "input": input_obj,
        "onesCount": ones_count,
        "onesRange": input_obj["onesRange"],
    })


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "接口不存在"}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "方法不允许"}), 405


@app.errorhandler(500)
def internal_error(exc):
    return jsonify({"error": "服务器内部错误"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    debug = os.environ.get("FLASK_DEBUG", "") == "1"
    if debug:
        app.run(host="0.0.0.0", port=port, debug=True)
    else:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
