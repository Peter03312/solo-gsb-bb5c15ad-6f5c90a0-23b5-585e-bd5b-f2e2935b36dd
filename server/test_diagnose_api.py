"""无解诊断接口 /api/diagnose 的端到端验证（Flask test client）。

覆盖：
- 锁定阻碍无解：返回全局最少解锁数、按坐标列表字典序的前 3 个互异集合，
  每个集合附一份满足环向浮长 K 与 onesRange 的可行修补示例，
  未解锁的锁定格在示例中不改变；
- 全部解锁仍无解（尺寸/格值/K/区间结构性不可达）：明确报结构性无解、
  不返回任何解锁建议（无 options/unlockCount）；
- 诊断只读：不发令牌、不写预览、原稿与版本不变；
- 非法输入 400；方法不允许 405；
- onesRange 回显与结构性区间不可达；
- 并列集合的坐标列表字典序、互异性与“应用后即可解”。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name

import app as server  # noqa: E402
from weave import find_violations  # noqa: E402

client = server.app.test_client()
FAILS = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}{(' :: ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def diagnose(body):
    return client.post("/api/diagnose", json=body)


def make_draft(grid, locks=None, k=2, ones_range=None):
    d = {"rows": len(grid), "cols": len(grid[0]), "grid": grid,
         "locks": locks or [], "k": k}
    if ones_range is not None:
        d["onesRange"] = ones_range
    return d


before = client.get("/api/state").get_json()
check("初始版本为 1", before["version"] == 1)

# --- 非法输入 400，原稿不变 ---
r = diagnose({"rows": 0, "cols": 2, "grid": [], "locks": [], "k": 1})
check("非法尺寸 -> 400", r.status_code == 400)
r = diagnose(make_draft([[0, 0]], k=0))
check("非法 K -> 400", r.status_code == 400)
r = diagnose(make_draft([[0, 0]], ones_range=[5, 6]))
check("非法区间 -> 400", r.status_code == 400)
check("非法诊断后版本不变",
      client.get("/api/state").get_json()["version"] == before["version"])

# GET 不是受支持的诊断调用：被静态兜底路由以 404 拒绝（与 /api/preview 一致），
# 绝不会执行诊断
check("GET /api/diagnose 被拒绝（不执行诊断）",
      client.get("/api/diagnose").status_code in (404, 405))

# --- 锁定阻碍：2x2 全0 K=1，锁定第 0 行两格 -> 必须解除 1 格 ---
body = make_draft([[0, 0], [0, 0]], locks=[[0, 0], [0, 1]], k=1)
r = diagnose(body)
b = r.get_json()
check("锁定阻碍诊断 200", r.status_code == 200, json.dumps(b)[:200])
check("非结构性无解", b["structurallyUnsolvable"] is False)
check("最少解锁 1 格", b["unlockCount"] == 1)
check("给出 2 个互异集合（恰有 2 个单格集合可行）",
      len(b["options"]) == 2
      and [o["unlocks"] for o in b["options"]] == [[[0, 0]], [[0, 1]]],
      json.dumps([o["unlocks"] for o in b["options"]]))
for o in b["options"]:
    ex = o["example"]
    check(f"示例合法（{o['unlocks']}）",
          not find_violations(ex["repaired"], 2, 2, 1))
    # 未解锁的锁定格不变：解锁 (0,0) 时 (0,1) 必须仍为 0，反之亦然
    kept = {(0, 0), (0, 1)} - {tuple(p) for p in o["unlocks"]}
    check(f"未解锁格保持原值（{o['unlocks']}）",
          all(ex["repaired"][rr][cc] == 0 for rr, cc in kept))
    check("示例含 flips/onesCount",
          isinstance(ex["flips"], list) and isinstance(ex["onesCount"], int))

# 只读：不发令牌、版本不变、无任何 confirm 可用
check("诊断响应无 token", "token" not in b)
after = client.get("/api/state").get_json()
check("诊断后原稿与版本不变",
      after["version"] == before["version"]
      and after["grid"] == before["grid"])
conf = client.post("/api/confirm",
                   json={"token": "anything", **body})
check("诊断不产生可确认令牌（伪造令牌 409）", conf.status_code == 409)

# 响应回显规范化输入与计数
check("诊断回显 input/onesCount/onesRange",
      b["input"]["rows"] == 2 and b["onesCount"] == 0
      and b["onesRange"] is None
      and b["input"]["locks"] == [[0, 0], [0, 1]])
check("诊断回显超限段",
      {s["kind"] for s in b["violations"]} == {"row", "col"})

# --- 应用任一解锁集合后 preview 即可解（端到端闭环）---
for o in b["options"]:
    unlocks = {tuple(p) for p in o["unlocks"]}
    applied = make_draft(
        body["grid"],
        locks=[p for p in body["locks"] if tuple(p) not in unlocks],
        k=1)
    pr = client.post("/api/preview", json=applied).get_json()
    check(f"应用 {o['unlocks']} 后预览可解",
          pr["solvable"] is True and pr.get("token"))
    # 且示例就是该锁定配置下的可行修补
    check("应用后首候选与诊断示例一致",
          pr["repaired"] == o["example"]["repaired"])

# --- 3x3 全0 K=2 全锁：需解除 3 格，前 3 集合按坐标列表字典序 ---
body3 = make_draft([[0] * 3 for _ in range(3)],
                   locks=[[r, c] for r in range(3) for c in range(3)], k=2)
b3 = diagnose(body3).get_json()
check("3x3 全锁最少解 3", b3["unlockCount"] == 3)
sets = [tuple(tuple(p) for p in o["unlocks"]) for o in b3["options"]]
check("3 个集合互异且按坐标列表字典序",
      len(set(sets)) == 3
      and [list(map(list, s)) for s in sets]
      == sorted((list(map(list, s)) for s in sets)),
      json.dumps([list(map(list, s)) for s in sets]))
check("首集合为字典序最小的对角线",
      b3["options"][0]["unlocks"] == [[0, 0], [1, 1], [2, 2]])
for o in b3["options"]:
    ex = o["example"]
    check("3x3 示例合法且仅 3 格翻转",
          not find_violations(ex["repaired"], 3, 3, 2)
          and len(ex["flips"]) == 3
          and ex["onesCount"] == 3)
    unlocks = {tuple(p) for p in o["unlocks"]}
    kept = {(r, c) for r in range(3) for c in range(3)} - unlocks
    check("3x3 未解锁格全部保持 0",
          all(ex["repaired"][rr][cc] == 0 for rr, cc in kept))

# --- 结构性无解：1x1（行/列恒全同），即使无锁 ---
b4 = diagnose(make_draft([[0]], locks=[[0, 0]], k=2)).get_json()
check("1x1 报结构性无解", b4["structurallyUnsolvable"] is True)
check("结构性无解不给解锁建议",
      "options" not in b4 and "unlockCount" not in b4
      and isinstance(b4.get("message"), str) and b4["message"])
b5 = diagnose(make_draft([[0]], locks=[], k=2)).get_json()
check("1x1 无锁同样结构性无解", b5["structurallyUnsolvable"] is True)

# 1x3：列全同恒超限
b6 = diagnose(make_draft([[0, 1, 0]], k=1)).get_json()
check("1x3 结构性无解", b6["structurallyUnsolvable"] is True)

# --- 区间因锁定不可达，解除后可达：3x3 全锁 + 区间[3,3] ---
b7 = diagnose(make_draft([[0] * 3 for _ in range(3)],
                         locks=[[r, c] for r in range(3) for c in range(3)],
                         k=2, ones_range=[3, 3])).get_json()
check("区间+锁定可经解锁修复",
      b7["structurallyUnsolvable"] is False and b7["unlockCount"] == 3)
check("区间回显 [3,3]", b7["onesRange"] == [3, 3])
for o in b7["options"]:
    check("示例总数达标", o["example"]["onesCount"] == 3)

# --- 区间结构性不可达：3x3 K=2 最多 6 个 1，要求 [7,9] ---
b8 = diagnose(make_draft([[0] * 3 for _ in range(3)],
                         locks=[[r, c] for r in range(3) for c in range(3)],
                         k=2, ones_range=[7, 9])).get_json()
check("区间结构性不可达 -> 结构性无解",
      b8["structurallyUnsolvable"] is True and "options" not in b8)

# --- 对本就可解的草稿调用诊断：最少解锁 0、空集合 + 一份最优修补示例 ---
solvable_draft = make_draft([[0, 0], [0, 0]], k=2)
b9 = diagnose(solvable_draft).get_json()
check("可解草稿诊断 unlockCount=0",
      b9["structurallyUnsolvable"] is False and b9["unlockCount"] == 0
      and len(b9["options"]) == 1
      and b9["options"][0]["unlocks"] == []
      and not find_violations(b9["options"][0]["example"]["repaired"], 2, 2, 2))

# --- 诊断绝不写库：直接查 preview 表，诊断前后行数不变 ---
db_path = os.environ["DB_PATH"]
import sqlite3
conn = sqlite3.connect(db_path)
n_previews_before = conn.execute("SELECT COUNT(*) FROM preview").fetchone()[0]
conn.close()
diagnose(body)
diagnose(make_draft([[0]], k=2))
conn = sqlite3.connect(db_path)
n_previews_after = conn.execute("SELECT COUNT(*) FROM preview").fetchone()[0]
conn.close()
check("诊断不新增任何预览行（无令牌持久化）",
      n_previews_before == n_previews_after,
      f"{n_previews_before} -> {n_previews_after}")
check("全程原稿版本始终为 1",
      client.get("/api/state").get_json()["version"] == 1)

# 原有的可解预览/确认契约不受影响：正常预览+确认仍成功
p = client.post("/api/preview", json=solvable_draft).get_json()
check("普通预览仍可解带令牌", p["solvable"] and p["token"]
      and p["candidateCount"] >= 1)
cr = client.post("/api/confirm",
                 json={"token": p["token"], **solvable_draft})
check("普通确认仍成功且版本 +1",
      cr.status_code == 200 and cr.get_json()["state"]["version"] == 2)

print()
if FAILS:
    print(f"{len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("全部通过。")
