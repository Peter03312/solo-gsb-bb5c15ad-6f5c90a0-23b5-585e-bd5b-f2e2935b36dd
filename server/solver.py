"""织物组织修补求解器。

把"每行/每列环向相同值连续段长度 <= K，且全同行/列视为无限浮长"
编码为 SAT 硬约束，用 MaxSAT(RC2) 求翻转未锁定格数最少的修补，
再以"按行展开字典序"为序逐格假设求解，枚举前 limit 个互不相同的
最优网格（翻转数同为全局最少，按行展开字典序升序）。

格值约定：0 = 经线在上，1 = 纬线在上。
"""

from pysat.solvers import Solver
from pysat.formula import WCNF
from pysat.examples.rc2 import RC2
from pysat.card import CardEnc, EncType


def _var(r, c, cols):
    """单元 (r, c) 对应的 SAT 变量编号（从 1 开始）。"""
    return r * cols + c + 1


def build_hard_clauses(rows, cols, k, grid, locks):
    """构造硬约束子句。

    - 行约束：对每行、每个环向起点，长度 L = min(K+1, cols) 的窗口
      不允许全 0 或全 1。L == cols 时窗口即整行，恰好表达
      "全同行视为无限浮长"。
    - 列约束：同理，窗口长度 L = min(K+1, rows)。
    - 锁定格：固定为原稿值。
    """
    clauses = []

    row_len = min(k + 1, cols)
    for r in range(rows):
        seen = set()
        for c0 in range(cols):
            cells = tuple(sorted((c0 + i) % cols for i in range(row_len)))
            if cells in seen:
                continue
            seen.add(cells)
            lits = [_var(r, c, cols) for c in cells]
            clauses.append(lits[:])             # 禁止全 0
            clauses.append([-v for v in lits])  # 禁止全 1

    col_len = min(k + 1, rows)
    for c in range(cols):
        seen = set()
        for r0 in range(rows):
            cells = tuple(sorted((r0 + i) % rows for i in range(col_len)))
            if cells in seen:
                continue
            seen.add(cells)
            lits = [_var(r, c, cols) for r in cells]
            clauses.append(lits[:])             # 禁止全 0
            clauses.append([-v for v in lits])  # 禁止全 1

    for (r, c) in locks:
        v = _var(r, c, cols)
        clauses.append([v if grid[r][c] == 1 else -v])

    return clauses


def _flip_lits(rows, cols, grid, locks):
    """每个未锁定格的"翻转指示"文字：为真表示该格被翻转。"""
    locked = set(map(tuple, locks))
    lits = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) in locked:
                continue
            v = _var(r, c, cols)
            lits.append(v if grid[r][c] == 0 else -v)
    return lits


def _at_most(clauses, lits, bound, next_var):
    """Sinz 顺序计数器编码：sum(lits) <= bound，返回 (clauses, 下一空闲变量)。

    next_var 为第一个空闲变量编号（须大于所有已用变量）。
    """
    n = len(lits)
    if bound >= n:
        return clauses, next_var
    if bound == 0:
        for lit in lits:
            clauses.append([-lit])
        return clauses, next_var

    # s[(i, j)]：前 i 个文字中至少 j 个为真（1 <= i <= n, 1 <= j <= bound）
    def s(i, j):
        return next_var + (i - 1) * bound + (j - 1)

    clauses.append([-lits[0], s(1, 1)])
    for j in range(2, bound + 1):
        clauses.append([-s(1, j)])  # 前 1 个不可能 >= j（j>=2）

    for i in range(2, n + 1):
        li = lits[i - 1]
        clauses.append([-li, s(i, 1)])
        clauses.append([-s(i - 1, 1), s(i, 1)])
        for j in range(2, bound + 1):
            clauses.append([-li, -s(i - 1, j - 1), s(i, j)])
            clauses.append([-s(i - 1, j), s(i, j)])
        clauses.append([-li, -s(i - 1, bound)])

    return clauses, next_var + n * bound


def _ones_range_clauses(rows, cols, ones_range, next_var):
    """格值 1 总数区间的硬约束子句：下限 <= sum(格值为1) <= 上限。

    变量取真即该格格值为 1（见 _var）。区间为 None 时不增加约束。
    计数采用 totalizer 基数编码（在 MaxSAT 迭代求解下比顺序计数器更稳，
    16×8 大多数区间亚秒级，个别精确计数硬实例约数秒）。
    两次编码使用互不重叠的辅助变量区间。
    返回 (clauses, 下一空闲变量号 = 已用最大变量 + 1)。
    """
    if ones_range is None:
        return [], next_var
    lower, upper = ones_range
    all_vars = [_var(r, c, cols) for r in range(rows) for c in range(cols)]
    clauses = []
    free = next_var
    # 上限：1 的数量 <= upper（upper == n 时恒成立，无需编码）
    if upper < len(all_vars):
        c1 = CardEnc.atmost(lits=all_vars, bound=upper, top_id=free - 1,
                            encoding=EncType.totalizer)
        clauses.extend(c1.clauses)
        free = c1.nv + 1
    # 下限：1 的数量 >= lower（lower == 0 时恒成立）
    if lower > 0:
        c2 = CardEnc.atleast(lits=all_vars, bound=lower, top_id=free - 1,
                             encoding=EncType.totalizer)
        clauses.extend(c2.clauses)
        free = c2.nv + 1
    return clauses, free


def _enumerate_lex(clauses, rows, cols, locked, limit):
    """在同一增量 SAT 实例上按行展开字典序枚举至多 limit 个网格模型。

    对未锁定格做深度优先的字典序遍历：每个格优先尝试取 0（负文字），
    不可行再取 1（正文字）；当前前缀通过 assumptions 携带（回溯即撤销）。
    每枚举出一个完整模型，就永久加入它的否定作为阻塞子句，
    保证后续网格与已返回的所有网格互不相同。
    """
    free_cells = [(r, c) for r in range(rows) for c in range(cols)
                  if (r, c) not in locked]
    results = []

    with Solver(name="g3", bootstrap_with=clauses) as solver:
        # prefix_lits[j]：第 j 个未锁定格在当前路径上的取值文字（-v=0，v=1）
        prefix_lits = []

        def extend():
            """从当前前缀末尾逐格扩展。

            探测某格取 0/1 可行后，只在到达完整前缀时用“同一前缀”做一次
            确认求解：增量求解器对不同长度 assumptions 的快速应答在极少数
            实例上并不自洽，以最终完整前缀的确认结果为准；完整前缀不可行
            （含中途两分支皆不可行）时返回 False，交由上层回溯。
            """
            while len(prefix_lits) < len(free_cells):
                r, c = free_cells[len(prefix_lits)]
                v = _var(r, c, cols)
                if solver.solve(assumptions=prefix_lits + [-v]):
                    prefix_lits.append(-v)
                elif solver.solve(assumptions=prefix_lits + [v]):
                    prefix_lits.append(v)
                else:
                    return False
            return solver.solve(assumptions=list(prefix_lits))

        while True:
            if extend():
                results.append(set(solver.get_model()))
                # 永久阻塞该完整模型，之后的遍历只能给出不同网格
                solver.add_clause([-lit for lit in prefix_lits])
                if len(results) >= limit:
                    break
            # 回溯：逐层弹出两子树均已枚举完的 1 分支，
            # 找到最近的 0 分支点并改走 1 分支
            while prefix_lits and prefix_lits[-1] > 0:
                prefix_lits.pop()
            if not prefix_lits:
                break  # 字典序遍历完成
            prefix_lits[-1] = -prefix_lits[-1]
    return results


def solve_repair_top(rows, cols, k, grid, locks, ones_range=None, limit=3):
    """求全局最少翻转的前 limit 个并列最优修补（按行展开字典序升序）。

    ones_range 给出修补稿格值 1 总数区间 (下限, 上限)，None 表示不限制。
    返回 None 表示无可行修补；否则返回
    {
        "flip_count": 全局最少翻转数,
        "candidates": [
            {"repaired": 二维格值, "flips": [[r, c], ...],
             "onesCount": 格值 1 总数},
            ...   # 长度 1..limit，网格互不相同
        ],
    }
    candidates[0] 即旧版 solve_repair 的结果（字典序最小）。
    """
    hard = build_hard_clauses(rows, cols, k, grid, locks)
    # 格值 1 总数区间与浮长、锁定同为硬约束；辅助变量从格变量之后开始编号
    range_clauses, next_free = _ones_range_clauses(
        rows, cols, ones_range, rows * cols + 1)
    hard.extend(range_clauses)
    locked = set(map(tuple, locks))

    # 第一阶段：MaxSAT 求最少翻转数
    wcnf = WCNF()
    for cl in hard:
        wcnf.append(cl)
    for r in range(rows):
        for c in range(cols):
            if (r, c) in locked:
                continue
            v = _var(r, c, cols)
            # 软约束：倾向于不翻转（保持原值）
            wcnf.append([-v] if grid[r][c] == 0 else [v], weight=1)

    with RC2(wcnf, solver="g3", adapt=True, exhaust=True) as rc2:
        if rc2.compute() is None:
            return None
        best = rc2.cost

    # 第二阶段：在翻转数 <= best 的约束下，按行展开字典序枚举前 limit 个网格。
    # 最优翻转数唯一（best），所有候选翻转数相同（同为最优）。
    clauses = [cl[:] for cl in hard]
    clauses, _ = _at_most(clauses, _flip_lits(rows, cols, grid, locks),
                          best, next_free)

    models = _enumerate_lex(clauses, rows, cols, locked, limit)
    assert models, "翻转数最优约束下应恒有解"

    candidates = []
    for model in models:
        repaired = [[1 if _var(r, c, cols) in model else 0
                     for c in range(cols)] for r in range(rows)]
        flips = [[r, c] for r in range(rows) for c in range(cols)
                 if repaired[r][c] != grid[r][c]]
        assert len(flips) == best, "候选必须同为全局最少翻转数"
        candidates.append({
            "repaired": repaired,
            "flips": flips,
            "onesCount": sum(v for row in repaired for v in row),
        })
    return {"flip_count": best, "candidates": candidates}


def _enumerate_unlock_sets(clauses, unlock_vars, best, limit, next_free):
    """在同一增量 SAT 实例上按坐标列表字典序枚举至多 limit 个解锁集合。

    对每个锁定格（unlock_vars 按行展开序排列）依次决定是否解除锁定：
    先尝试纳入（正文字），再尝试保留锁定（负文字），因此叶子出现的顺序
    恰为集合的短字典序（先按列表长度，再逐项按行展开坐标比较）。
    用 |集合| <= best 与 |集合| >= best 剪枝（best 为全局最少解锁数）。
    与 _enumerate_lex 同理，以完整前缀上的确认求解为准。
    next_free 为 clauses 已用辅助变量之后的第一个空闲变量号。
    """
    results = []
    # |已选| <= best：best >= n 时恒成立，无需编码
    clauses = [cl[:] for cl in clauses]
    clauses, free = _at_most(clauses, unlock_vars, best, next_free)
    # |已选| >= best：best == 0 时恒成立
    if best > 0:
        enc = CardEnc.atleast(lits=unlock_vars, bound=best, top_id=free - 1,
                              encoding=EncType.totalizer)
        clauses.extend(enc.clauses)

    with Solver(name="g3", bootstrap_with=clauses) as solver:
        prefix_lits = []

        def extend():
            while len(prefix_lits) < len(unlock_vars):
                v = unlock_vars[len(prefix_lits)]
                if solver.solve(assumptions=prefix_lits + [v]):
                    prefix_lits.append(v)
                elif solver.solve(assumptions=prefix_lits + [-v]):
                    prefix_lits.append(-v)
                else:
                    return False
            return solver.solve(assumptions=list(prefix_lits))

        while True:
            if extend():
                chosen = {lit for lit in prefix_lits if lit > 0}
                results.append(chosen)
                # 永久阻塞该完整取舍，保证互异
                solver.add_clause([-lit for lit in prefix_lits])
                if len(results) >= limit:
                    break
            # 回溯：逐层弹出两个子树均已枚举完的“保留锁定”分支，
            # 找到最近的“解除锁定”分支点并改走“保留锁定”分支
            while prefix_lits and prefix_lits[-1] < 0:
                prefix_lits.pop()
            if not prefix_lits:
                break
            prefix_lits[-1] = -prefix_lits[-1]
    return results


def diagnose_unlocks(rows, cols, k, grid, locks, ones_range=None, limit=3):
    """无解草稿诊断：求使修补可行所需解除的锁定格数量全局最少的方案。

    保持尺寸、格值、K 与可选 onesRange 不变，只允许解除部分锁定格。
    - 全部锁定格均解除仍无解（结构性无解）时返回
      {"structural": True}，不提供任何解锁建议；
    - 否则返回
      {"structural": False, "unlock_count": 全局最少解锁数,
       "options": [
         {"unlocks": [[r, c], ...],
          "example": {"repaired", "flips", "onesCount"}},
         ...   # 1..limit 个互异解锁集合，按坐标列表短字典序升序
       ]}。
    每份 example 是在“仅解除该集合、其余锁定格不变”的约束下
    全局翻转数最少（并列再按行展开字典序最小）的可行修补示例，
    因此未解锁的锁定格绝不改变，且示例满足环向浮长 K 与 onesRange。
    """
    locked = sorted(map(tuple, locks))

    # 快速路径：草稿在现有锁定下已可行 -> 无需解除任何锁定格
    current = solve_repair_top(rows, cols, k, grid, locks, ones_range,
                               limit=1)
    if current is not None:
        return {
            "structural": False,
            "unlock_count": 0,
            "options": [{
                "unlocks": [],
                "example": current["candidates"][0],
            }],
        }

    # 结构性检验：解除全部锁定后是否还存在可行网格
    if solve_repair_top(rows, cols, k, grid, [], ones_range, limit=1) is None:
        return {"structural": True}

    if not locked:
        # 没有锁定格：上面 current 可解与“全部解锁可解”一致，不会到达
        return {"structural": True}

    # 第一阶段：MaxSAT 求“最少需解除多少锁定格”。
    # 引入解锁指示 u_x：u_x 为真时该锁定格不再固定（单位子句被弱化）。
    cell_base = rows * cols
    unlock_vars = [cell_base + 1 + i for i in range(len(locked))]
    next_free = cell_base + len(locked) + 1
    hard = []
    hard.extend(build_hard_clauses(rows, cols, k, grid, []))
    range_clauses, next_free = _ones_range_clauses(
        rows, cols, ones_range, next_free)
    hard.extend(range_clauses)
    for (r, c), uv in zip(locked, unlock_vars):
        cell = _var(r, c, cols)
        lit = cell if grid[r][c] == 1 else -cell
        hard.append([uv, lit])

    wcnf = WCNF()
    for cl in hard:
        wcnf.append(cl)
    for uv in unlock_vars:
        wcnf.append([-uv], weight=1)  # 软约束：尽量不解除锁定

    with RC2(wcnf, solver="g3", adapt=True, exhaust=True) as rc2:
        model = rc2.compute()
        assert model is not None, "全部解锁可解时 MaxSAT 必须可解"
        best = rc2.cost

    # 第二阶段：在 |解锁| == best 下按坐标列表字典序枚举前 limit 个集合
    chosen_sets = _enumerate_unlock_sets(hard, unlock_vars, best, limit,
                                         next_free)
    assert chosen_sets, "最优解锁数约束下应恒有解"

    unlock_by_var = {uv: pos for pos, uv in zip(locked, unlock_vars)}
    options = []
    for chosen in chosen_sets:
        unlocks = sorted(unlock_by_var[lit] for lit in chosen)
        remaining = [pos for pos in locked if pos not in set(unlocks)]
        solved = solve_repair_top(rows, cols, k, grid, remaining,
                                  ones_range, limit=1)
        assert solved is not None, "枚举到的解锁集合必须使修补可行"
        first = solved["candidates"][0]
        options.append({
            "unlocks": [[r, c] for r, c in unlocks],
            "example": first,
        })
    return {"structural": False, "unlock_count": best, "options": options}


def solve_repair(rows, cols, k, grid, locks, ones_range=None):
    """求字典序最小的最优修补（solve_repair_top 的单候选便捷封装）。

    ones_range 给出修补稿格值 1 总数区间 (下限, 上限)，None 表示不限制。
    返回 None 表示无可行修补；否则返回 dict：
    {
        "repaired": 修补后的二维格值,
        "flips":    [[r, c], ...] 被翻转的格（按行展开序）,
    }
    """
    solved = solve_repair_top(rows, cols, k, grid, locks, ones_range, limit=1)
    if solved is None:
        return None
    first = solved["candidates"][0]
    return {"repaired": first["repaired"], "flips": first["flips"]}
