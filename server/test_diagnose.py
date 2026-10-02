"""无解草稿诊断（最少解锁）的测试，含暴力枚举对照。"""

import itertools
import random
import sys
import time

from solver import diagnose_unlocks, solve_repair_top
from weave import find_violations


def brute_diagnose(rows, cols, k, grid, locks, ones_range=None, limit=3):
    """暴力求最少解锁集合（按坐标列表短字典序前 limit 个）及各自最优修补。

    返回 None 表示结构性无解（全部解锁仍无解）；否则返回
    [(unlocks, flips, flat_repaired), ...]。
    """
    locked = sorted(map(tuple, locks))

    def best_repair(unlockset):
        remaining = set(locked) - set(unlockset)
        best = None
        for bits in itertools.product((0, 1), repeat=rows * cols):
            g = [list(bits[r * cols:(r + 1) * cols]) for r in range(rows)]
            if any(g[r][c] != grid[r][c] for (r, c) in remaining):
                continue
            if find_violations(g, rows, cols, k):
                continue
            ones = sum(bits)
            if ones_range is not None and not (
                    ones_range[0] <= ones <= ones_range[1]):
                continue
            flips = tuple((r, c) for r in range(rows)
                          for c in range(cols) if g[r][c] != grid[r][c])
            if best is None or (len(flips), bits) < (
                    len(best[0]), best[1]):
                best = (flips, bits)
        return best

    feasible_sets = []
    for size in range(len(locked) + 1):
        for comb in itertools.combinations(range(len(locked)), size):
            unlockset = [locked[i] for i in comb]
            sol = best_repair(unlockset)
            if sol is not None:
                feasible_sets.append((unlockset, sol))
        if feasible_sets:
            break
    if not feasible_sets:
        return None
    # 坐标列表短字典序：先比长度，再逐项按行展开坐标比较
    feasible_sets.sort(key=lambda x: ([list(p) for p in x[0]]))
    return [(s, len(sol[0]), sol[1]) for s, sol in feasible_sets[:limit]]


def check_diagnose(rows, cols, k, grid, locks, ones_range, label):
    got = diagnose_unlocks(rows, cols, k, grid, locks, ones_range, limit=3)
    expected = brute_diagnose(rows, cols, k, grid, locks, ones_range)
    locked = set(map(tuple, locks))
    # 该草稿在现有锁定下是否本身就无解（无解才需真正“解除锁定”）
    originally_unsolvable = solve_repair_top(
        rows, cols, k, grid, locks, ones_range, limit=1) is None

    if expected is None:
        assert got["structural"] is True, f"{label}: 应结构性无解"
        assert "options" not in got, f"{label}: 结构性无解不得给解锁建议"
        # 全部解锁确实无解
        assert solve_repair_top(rows, cols, k, grid, [],
                                ones_range, limit=1) is None
        print(f"  [OK] {label}: 结构性无解（不给建议，与暴力一致）")
        return

    assert got.get("structural") is False, f"{label}: 不应报结构性无解"
    assert got["unlock_count"] == len(expected[0][0]), (
        f"{label}: 最少解锁数 {got['unlock_count']} != {len(expected[0][0])}")
    assert len(got["options"]) == len(expected), (
        f"{label}: 选项数 {len(got['options'])} != {len(expected)}")
    if not originally_unsolvable:
        assert got["unlock_count"] == 0, (
            f"{label}: 草稿本可解时最少解锁数应为 0")

    seen = set()
    for opt, (exp_unlocks, exp_flip_count, exp_seq) in zip(
            got["options"], expected):
        unlocks = [tuple(p) for p in opt["unlocks"]]
        assert unlocks == exp_unlocks, (
            f"{label}: 解锁集合字典序错误 {unlocks} != {exp_unlocks}")
        assert len(unlocks) == got["unlock_count"], f"{label}: 解锁数不一致"
        ex = opt["example"]
        seq = tuple(v for row in ex["repaired"] for v in row)
        assert seq == exp_seq, f"{label}: 示例修补 {seq} != {exp_seq}"
        assert len(ex["flips"]) == exp_flip_count, (
            f"{label}: 示例翻转数 {len(ex['flips'])} != {exp_flip_count}")
        assert ex["onesCount"] == sum(seq)
        # 示例合法
        assert not find_violations(ex["repaired"], rows, cols, k)
        if ones_range is not None:
            assert ones_range[0] <= ex["onesCount"] <= ones_range[1]
        # 未解锁的锁定格不得改变
        unlockset = set(unlocks)
        for (r, c) in locked - unlockset:
            assert ex["repaired"][r][c] == grid[r][c], (
                f"{label}: 未解锁格 ({r},{c}) 被改变")
        # 对“原本无解”的草稿，最小解锁集合必非空，且至少一个被解除的格
        # 必须实际翻转（否则原锁定配置下即可行）
        if originally_unsolvable:
            assert unlockset, f"{label}: 无解草稿的建议必须解除至少 1 格"
            flipped = set(map(tuple, ex["flips"]))
            assert any(p in flipped for p in unlocks), (
                f"{label}: 建议中无实际翻转的解锁格")
        seen.add(tuple(unlocks))
    assert len(seen) == len(expected), f"{label}: 解锁集合必须互异"
    print(f"  [OK] {label}: 最少解锁 {got['unlock_count']} 格，"
          f"{len(expected)} 个集合（与暴力一致）")


def main():
    print("== 固定诊断用例 ==")
    # 2x2 全0 K=1，锁整行：任一解都要求该 row 有 1，解除 1 格即可
    check_diagnose(2, 2, 1, [[0, 0], [0, 0]], [[0, 0], [0, 1]],
                   None, "2x2 锁整行 需解1")
    # 1x1：结构性无解（行与列恒全同）
    check_diagnose(1, 1, 1, [[0]], [[0, 0]], None, "1x1 结构性无解")
    check_diagnose(1, 1, 1, [[0]], [], None, "1x1 无锁 结构性无解")
    # 1x3：列全同恒超限，结构性无解
    check_diagnose(1, 3, 1, [[0, 1, 0]], [], None, "1x3 结构性无解")
    # 3x3 全0 K=2 全锁：需解除 3 格，前 3 集合字典序
    check_diagnose(3, 3, 2, [[0] * 3 for _ in range(3)],
                   [[r, c] for r in range(3) for c in range(3)],
                   None, "3x3 全锁 需解3")
    # 区间结构性不可达：3x3 K=2 合法网格最多 6 个 1（2 个 1/行），
    # 要求 >=7 个时即使全部解锁也无解
    check_diagnose(3, 3, 2, [[0] * 3 for _ in range(3)],
                   [[r, c] for r in range(3) for c in range(3)],
                   (7, 9), "3x3 区间[7,9] 结构性无解")
    # 区间因锁定不可达、解除后可达：锁死全部为 0 又要求 3 个 1
    check_diagnose(3, 3, 2, [[0] * 3 for _ in range(3)],
                   [[r, c] for r in range(3) for c in range(3)],
                   (3, 3), "3x3 区间[3,3] 需解3")
    # 2x2 全0 K=2，仅锁对角：草稿本就可解（翻转该对角），best=0
    check_diagnose(2, 2, 2, [[0, 0], [0, 0]],
                   [[0, 0], [1, 1]], None, "2x2 对角锁 本可解 best=0")
    # 2x2 全0 K=2，四格全锁：无解，需解除 2 格
    check_diagnose(2, 2, 2, [[0, 0], [0, 0]],
                   [[0, 0], [0, 1], [1, 0], [1, 1]], None,
                   "2x2 全锁 需解2")
    # 区间结构性：2x2 合法恒 2 个 1
    check_diagnose(2, 2, 2, [[0, 0], [0, 0]],
                   [[0, 0], [1, 1]], (3, 4), "2x2 区间[3,4] 结构性无解")

    print("== 随机小网格对照暴力 ==")
    rng = random.Random(6061)
    for i in range(80):
        rows = rng.randint(2, 4)
        cols = rng.randint(2, 4)
        k = rng.randint(1, 4)
        grid = [[rng.randint(0, 1) for _ in range(cols)] for _ in range(rows)]
        # 多锁一些，更容易出现“锁定阻碍”的无解
        locks = [[r, c] for r in range(rows) for c in range(cols)
                 if rng.random() < 0.45]
        if rng.random() < 0.5:
            lo = rng.randint(0, rows * cols)
            orng = (lo, rng.randint(lo, rows * cols))
        else:
            orng = None
        # 只对真正无解（按现有锁定）的草稿做诊断才有“建议”语义；
        # 但诊断对可解草稿也应安全（best=0），故全量对照
        check_diagnose(rows, cols, k, grid, locks, orng,
                       f"随机#{i} {rows}x{cols} K={k} 锁{len(locks)}"
                       f"{' 区间' + str(orng) if orng else ''}")

    print("== 大网格性能（16x8） ==")
    prng = random.Random(812)
    for k in (1, 2, 3):
        grid = [[prng.randint(0, 1) for _ in range(8)] for _ in range(16)]
        # 高锁定比例制造无解
        locks = [[r, c] for r in range(16) for c in range(8)
                 if prng.random() < 0.7]
        t0 = time.time()
        got = diagnose_unlocks(16, 8, k, grid, locks, None, limit=3)
        dt = time.time() - t0
        if got["structural"]:
            print(f"  [OK] 16x8 K={k} 锁{len(locks)}: 结构性无解, {dt:.2f}s")
        else:
            assert 1 <= len(got["options"]) <= 3
            for opt in got["options"]:
                assert not find_violations(opt["example"]["repaired"],
                                           16, 8, k)
                assert len(opt["unlocks"]) == got["unlock_count"]
            print(f"  [OK] 16x8 K={k} 锁{len(locks)}: 最少解锁 "
                  f"{got['unlock_count']} 格, {len(got['options'])} 选项, "
                  f"{dt:.2f}s")
        assert dt < 30, "诊断超时"

    # 结构性无解的大网格应快速返回（仅一次无锁求解）
    grid = [[0] * 8 for _ in range(16)]
    t0 = time.time()
    got = diagnose_unlocks(16, 8, 2, grid, [], (0, 0))
    dt = time.time() - t0
    assert got["structural"]
    print(f"  [OK] 16x8 区间[0,0] 结构性无解, {dt:.2f}s")

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
