"""无可行修补时「最少解锁自诊断」接口的端到端验证（Flask test client）。

覆盖：
- 诊断为只读：不落预览令牌、不改原稿/版本/区间，不发确认令牌；
- 锁定格阻碍（非结构）：minUnlockCount 为全局最少、返回至多 3 个互异
  解锁集合（坐标列表字典序）、各附一份满足浮长/区间/未解锁锁定保持的示例；
- 即使全解锁仍无解 -> structural=true，且不含解锁建议字段；
- 当前草稿本可修补 -> solvable=true、minUnlockCount=0、空解锁集合；
- 非法输入 -> 400；诊断不产生令牌（无法被 confirm 使用）；
- 草稿自诊断后变化，前端依据 input 一致性自行判定旧建议失效（接口本身
  无状态，不持久化，故重复诊断互不影响）；
- 与 preview/confirm 契约并存：可解预览、候选顺序、确认流程保持不变。
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


def draft(grid, locks=None, k=2, ones_range=None):
    rows = len(grid)
    cols = len(grid[0])
    body = {"rows": rows, "cols": cols, "grid": grid, "locks": locks or [], "k": k}
    if ones_range is not None:
        body["onesRange"] = ones_range
    return body


def ones(grid):
    return sum(v for row in grid for v in row)


st0 = client.get("/api/state").get_json()
version0 = st0["version"]

# ---- 结构性无解：1x1（环向整行整列必全同），全锁仍无解 ----
r = client.post("/api/diagnose", json=draft([[0]], [[0, 0]], k=2))
b = r.get_json()
check("1x1 诊断 200 且 structural", r.status_code == 200
      and b.get("solvable") is False and b.get("structural") is True,
      json.dumps(b)[:200])
check("结构无解不提供解锁建议",
      "minUnlockCount" not in b and "solutions" not in b and "token" not in b)
check("结构无解回显输入/区间/总数",
      b.get("onesRange") is None and b.get("onesCount") == 0
      and b.get("input", {}).get("locks") == [[0, 0]])

# 1 行多列同样结构性（行全同恒超限）
b = client.post("/api/diagnose",
                json=draft([[0, 1, 0]], [[0, 0], [0, 1], [0, 2]], k=1)).get_json()
check("1x3 全锁 structural", b["structural"] is True and b["solvable"] is False)

# ---- 锁定格阻碍：2x2 全0 K=2，首列两格锁 0 -> 解其一即可 ----
d = draft([[0, 0], [0, 0]], [[0, 0], [1, 0]], k=2)
# 先确认常规预览确实无解
pv = client.post("/api/preview", json=d).get_json()
check("前置：该草稿预览无解无令牌",
      pv["solvable"] is False and "token" not in pv)
r = client.post("/api/diagnose", json=d)
b = r.get_json()
check("非结构诊断 solvable=false structural=false",
      r.status_code == 200 and b["solvable"] is False and b["structural"] is False)
check("最少解锁 1 格", b["minUnlockCount"] == 1)
check("返回 2 个互异集合（恰两个选择）",
      b["unlockSetCount"] == 2 and len(b["solutions"]) == 2)
unlock_lists = [tuple(map(tuple, s["unlocks"])) for s in b["solutions"]]
check("两个集合互异且各只解 1 格",
      len(set(unlock_lists)) == 2 and all(len(u) == 1 for u in unlock_lists))
# 坐标列表字典序：[[0,0]] 在 [[1,0]] 之前
check("按坐标列表字典序升序",
      unlock_lists == sorted(unlock_lists) and unlock_lists[0] == ((0, 0),),
      str(unlock_lists))
for s in b["solutions"]:
    check("示例满足浮长且无超限", not find_violations(s["repaired"], 2, 2, 2))
    check("示例 onesCount 与网格一致", s["onesCount"] == ones(s["repaired"]))
    us = set(map(tuple, s["unlocks"]))
    keep = {(0, 0), (1, 0)} - us
    check("未解锁锁定格保持原值", all(s["repaired"][rr][cc] == 0 for (rr, cc) in keep))
    # changes 必须与 repaired 相对草稿一致
    ch = [(rr, cc) for rr in range(2) for cc in range(2)
          if s["repaired"][rr][cc] != d["grid"][rr][cc]]
    check("changes 与示例网格一致", list(map(tuple, s["changes"])) == ch)

# ---- 诊断只读：版本/原稿不变，且预览表无新增令牌可用 ----
st1 = client.get("/api/state").get_json()
check("诊断不改原稿版本与网格",
      st1["version"] == version0 and st1["grid"] == st0["grid"]
      and st1.get("onesRange") == st0.get("onesRange"))
# 诊断响应没有 token；即便伪造 confirm 也只能按 consumed 409（无预览落库）
for sol in b["solutions"]:
    pass
fake = client.post("/api/confirm", json={**d, "token": "diagnose-has-no-token"})
check("诊断不发令牌（伪造 confirm 409 且原稿不变）",
      fake.status_code == 409 and client.get("/api/state").get_json()["version"] == version0)

# ---- 本可修补的草稿：诊断 solvable=true、零解锁，示例即可行修补 ----
ok_d = draft([[0, 1], [0, 1]], k=2)
b = client.post("/api/diagnose", json=ok_d).get_json()
check("可修补草稿诊断 solvable=true",
      b["solvable"] is True and b["structural"] is False
      and b["minUnlockCount"] == 0 and b["unlockSetCount"] == 1)
check("零解锁集合 + 可行示例",
      b["solutions"][0]["unlocks"] == []
      and not find_violations(b["solutions"][0]["repaired"], 2, 2, 2))
check("可修补诊断同样无令牌", "token" not in b)

# ---- 区间：4x4 全0 K=2 首行全锁 0，区间 [8,8]；解 2 格，前三集合字典序 ----
g4 = [[0] * 4 for _ in range(4)]
locks_row0 = [[0, c] for c in range(4)]
d4 = draft(g4, locks_row0, k=2, ones_range=[8, 8])
pv = client.post("/api/preview", json=d4).get_json()
check("前置：4x4 首行锁+区间[8,8] 预览无解", pv["solvable"] is False)
b = client.post("/api/diagnose", json=d4).get_json()
check("4x4 区间诊断非结构、最少解 2 格",
      not b["structural"] and b["minUnlockCount"] == 2
      and b["unlockSetCount"] == 3, json.dumps(b)[:120])
uls = [tuple(map(tuple, s["unlocks"])) for s in b["solutions"]]
check("三集合互异、长度均为 2 且字典序",
      len(set(uls)) == 3 and uls == sorted(uls)
      and all(len(u) == 2 for u in uls), str(uls))
for s in b["solutions"]:
    check("示例满足浮长 + 总数恰为 8",
          not find_violations(s["repaired"], 4, 4, 2) and s["onesCount"] == 8)
    us = set(map(tuple, s["unlocks"]))
    check("未解锁首行锁定格保持 0",
          all(s["repaired"][0][cc] == 0 for cc in range(4) if (0, cc) not in us))

# ---- 区间本身在浮长规则下不可达（总数恒为 8）：[9,9] 全锁 -> 结构性 ----
b = client.post("/api/diagnose",
                json=draft(g4, [[rr, cc] for rr in range(4) for cc in range(4)],
                           k=2, ones_range=[9, 9])).get_json()
check("区间不可达 + 全锁 -> structural", b["structural"] is True)
check("结构性无解无误导建议", "solutions" not in b and "minUnlockCount" not in b)

# ---- 非法输入 400（且不影响任何状态） ----
for bad in [
    {"rows": 0, "cols": 2, "grid": [], "locks": [], "k": 1},
    {"rows": 1, "cols": 1, "grid": [[2]], "locks": [], "k": 1},
    {"rows": 2, "cols": 2, "grid": [[0, 0], [0, 0]], "locks": [], "k": 0},
    {"rows": 2, "cols": 2, "grid": [[0, 0], [0, 0]], "locks": [], "k": 1,
     "onesRange": [3, 2]},
]:
    rr = client.post("/api/diagnose", json=bad)
    check("非法诊断 400", rr.status_code == 400, json.dumps(bad)[:60])
check("非法诊断后版本不变", client.get("/api/state").get_json()["version"] == version0)

# ---- 诊断无状态：同一草稿两次诊断一致；草稿变化后旧 input 与新草稿不等 ----
b1 = client.post("/api/diagnose", json=d).get_json()
b2 = client.post("/api/diagnose", json=d).get_json()
check("诊断无状态、可重复且结果一致",
      b1["minUnlockCount"] == b2["minUnlockCount"]
      and b1["solutions"] == b2["solutions"])
changed = dict(d)
changed["grid"] = [[0, 0], [0, 1]]
# 前端 sameDesign 依据 input 判定失效：这里验证响应回显的 input 即规范化草稿
check("诊断回显 input 为当时草稿（变动后不相等）",
      b1["input"]["grid"] == d["grid"]
      and b1["input"]["grid"] != changed["grid"]
      and b1["input"]["onesRange"] is None)

# ---- 与既有契约并存：可解预览、候选顺序、确认流程不受影响 ----
tie = draft([[0, 0], [0, 0]], k=2)
p = client.post("/api/preview", json=tie).get_json()
check("可解预览仍发令牌且 2 候选",
      bool(p["token"]) and p["candidateCount"] == 2
      and p["repaired"] == p["candidates"][0]["repaired"])
before_v = client.get("/api/state").get_json()["version"]
rr = client.post("/api/confirm", json={**tie, "token": p["token"], "candidate": 1})
check("候选确认仍成功且版本 +1",
      rr.status_code == 200
      and rr.get_json()["state"]["version"] == before_v + 1
      and rr.get_json()["state"]["grid"] == p["candidates"][1]["repaired"])
rr = client.post("/api/confirm", json={**tie, "token": p["token"]})
check("令牌消费后重复确认仍 409", rr.status_code == 409)

# ---- 应用解锁集合后的真实闭环：解掉首列锁 -> 预览变为可解 ----
applied_locks = [[1, 0]]  # 应用字典序首个方案（解 (0,0)）后保留的锁定
unlocked_draft = draft([[0, 0], [0, 0]], applied_locks, k=2)
p2 = client.post("/api/preview", json=unlocked_draft).get_json()
check("应用解锁集合后预览可解（闭环）",
      p2["solvable"] is True and bool(p2["token"])
      and not find_violations(p2["repaired"], 2, 2, 2)
      and p2["repaired"][1][0] == 0)  # 仍锁定的 (1,0) 保持 0

print()
if FAILS:
    print(f"{len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("全部通过。")
