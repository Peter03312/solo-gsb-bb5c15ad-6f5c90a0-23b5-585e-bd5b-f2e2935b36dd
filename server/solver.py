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


def build_float_clauses(rows, cols, k):
    """环向浮长硬约束子句（不含锁定格固定）。

    - 行约束：对每行、每个环向起点，长度 L = min(K+1, cols) 的窗口
      不允许全 0 或全 1。L == cols 时窗口即整行，恰好表达
      "全同行视为无限浮长"。
    - 列约束：同理，窗口长度 L = min(K+1, rows)。
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

    return clauses


def build_hard_clauses(rows, cols, k, grid, locks):
    """构造硬约束子句：环向浮长 + 锁定格固定为原稿值。"""
    clauses = build_float_clauses(rows, cols, k)
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


# 无可行修补时的解锁诊断：每个方案最多给出的互异解锁集合数
MAX_UNLOCK_SETS = 3


def diagnose_unlocks(rows, cols, k, grid, locks, ones_range=None,
                     limit=MAX_UNLOCK_SETS):
    """无可行修补时的只读诊断：求「使修补变为可行所需解除的锁定格」全局最少方案。

    保持尺寸、格值、K、可选 onesRange 不变；允许每个锁定格被「解除」
    （解除后即可像普通格一样翻转），目标是解除数量全局最少。在最少
    解除数下，按「解锁坐标列表（坐标按行展开序排列）字典序」升序枚举
    前 limit 个互不相同的解锁集合，每个集合附一份可行修补示例
    （在该解锁集合下按行展开格值字典序最小的可行网格；未解锁的锁定格
    保持原值；满足环向浮长 K 与 onesRange）。

    返回 None 表示即使解除全部锁定仍无可行修补（结构性无解，
    例如 1×1、单行或单列，或 onesRange 本身在浮长规则下不可达）；
    否则返回
    {
        "unlock_count": 全局最少解除锁定格数,
        "solutions": [
            {"unlocks": [[r, c], ...],      # 需解除的锁定格（行展开序）
             "repaired": 二维格值,           # 一份可行修补示例（行展开字典序最小）
             "changes": [[r, c], ...],      # 示例相对草稿改变的格（行展开序）
             "onesCount": 格值 1 总数},
            ...   # 长度 1..limit，解锁集合互不相同
        ],
    }

    注意：本函数只做诊断，不修改任何稿件或锁定。
    """
    lock_cells = sorted(set(map(tuple, locks)))
    locked_set = set(lock_cells)

    # 浮长与 onesRange 为硬约束（与具体解除哪些锁定无关）
    base = build_float_clauses(rows, cols, k)
    range_clauses, next_free = _ones_range_clauses(
        rows, cols, ones_range, rows * cols + 1)
    base.extend(range_clauses)

    # 每个锁定格 i 配一个解除指示变量 y_i：为真表示该格被解除
    y_var = {cell: next_free + i for i, cell in enumerate(lock_cells)}
    free0 = next_free + len(lock_cells)

    # 未解除（y_i 为假）的锁定格必须保持原稿值：
    # 原值 1:  ¬y_i ∨  x_i；原值 0:  ¬y_i ∨ ¬x_i
    clauses = [cl[:] for cl in base]
    for (r, c) in lock_cells:
        x = _var(r, c, cols)
        y = y_var[(r, c)]
        clauses.append([y, x] if grid[r][c] == 1 else [y, -x])

    # 第一阶段：MaxSAT 求全局最少解除数。
    # 软约束倾向于不解除（¬y_i）；浮长、onesRange 与锁定保持为硬约束。
    wcnf = WCNF()
    for cl in clauses:
        wcnf.append(cl)
    for cell in lock_cells:
        wcnf.append([-y_var[cell]], weight=1)

    with RC2(wcnf, solver="g3", adapt=True, exhaust=True) as rc2:
        if rc2.compute() is None:
            return None  # 即使全部解除仍无解：结构性无解
        best = rc2.cost

    # 第二阶段：在解除数 <= best 的约束下枚举最少解除集合。
    enum_clauses = [cl[:] for cl in clauses]
    enum_clauses, _ = _at_most(
        enum_clauses, [y_var[cell] for cell in lock_cells], best, free0)

    def model_repaired(model):
        return [[1 if _var(r, c, cols) in model else 0
                 for c in range(cols)] for r in range(rows)]

    solutions = []

    def lex_repair(y_prefix):
        """在给定解锁决策（y_prefix 完整覆盖所有锁定格）下，
        按行展开格值字典序求最小可行网格。可变格 = 原本未锁定格
        ∪ 本次解锁格（未解锁锁定格仍被 y 约束固定，故跳过）；
        仅用 assumptions 探测，不往求解器添加子句。
        """
        unlocked = {cell for lit, cell in zip(y_prefix, lock_cells) if lit > 0}
        variable = [(r, c) for r in range(rows) for c in range(cols)
                    if (r, c) not in locked_set or (r, c) in unlocked]
        prefix = list(y_prefix)

        def probe(extra):
            return solver.solve(assumptions=prefix + extra)

        for (r, c) in variable:
            v = _var(r, c, cols)
            if probe([-v]):
                prefix.append(-v)
            elif probe([v]):
                prefix.append(v)
            else:
                return None
        if probe([]):
            return set(solver.get_model())
        return None

    with Solver(name="g3", bootstrap_with=enum_clauses) as solver:
        # 外层对锁定格（按行展开序）做字典序深度优先：每格优先「解除」
        # （y 为正文字），使输出的解锁坐标列表字典序最小。
        # 每个完整解除决策求一份行展开字典序最小的可行修补示例，
        # 随后用「完整 y 赋值的否定」永久阻塞该解锁集合，
        # 继续回溯枚举下一个互异集合，直至 limit 个。
        y_lits = []

        def extend_y():
            """沿「优先解除」方向扩展到下一个完整可行的解除决策。"""
            while len(y_lits) < len(lock_cells):
                cell = lock_cells[len(y_lits)]
                y = y_var[cell]
                if solver.solve(assumptions=y_lits + [y]):
                    y_lits.append(y)
                elif solver.solve(assumptions=y_lits + [-y]):
                    y_lits.append(-y)
                else:
                    return False
            return solver.solve(assumptions=list(y_lits))

        while True:
            if extend_y():
                model = lex_repair(y_lits)
                if model is None:
                    # 完整前缀确认本可行却求不出修补：不应发生，
                    # 以阻塞该决策兜底继续枚举
                    pass
                else:
                    repaired = model_repaired(model)
                    unlocks = [[r, c] for lit, (r, c) in zip(y_lits, lock_cells)
                               if lit > 0]
                    assert len(unlocks) == best, "诊断方案必须同为全局最少解除数"
                    changes = [[r, c] for r in range(rows) for c in range(cols)
                               if repaired[r][c] != grid[r][c]]
                    # 未解锁的锁定格不得改变
                    unlocked_set = set(map(tuple, unlocks))
                    assert all(repaired[cr][cc] == grid[cr][cc]
                               for (cr, cc) in lock_cells
                               if (cr, cc) not in unlocked_set)
                    solutions.append({
                        "unlocks": unlocks,
                        "repaired": repaired,
                        "changes": changes,
                        "onesCount": sum(v for row in repaired for v in row),
                    })
                    if len(solutions) >= limit:
                        break
                # 永久阻塞该完整解除决策（只作用于 y 变量），
                # 不影响内层曾用 assumptions 做的修补求解
                solver.add_clause([-lit for lit in y_lits])
            # 回溯到最近的「首选分支（解除）」并改走「次选分支（不解除）」。
            # 与 _enumerate_lex 同一套深度优先回溯，同时覆盖「整叶枚举完」
            # 与「中途两分支皆不可行」：末端为次选（-y）时逐层弹出，
            # 顶端首选（+y）翻转为 -y；其 +y 子树此前已完整枚举/确认不可行。
            while y_lits and y_lits[-1] < 0:
                y_lits.pop()
            if not y_lits:
                break  # 全部解除决策枚举完毕
            flip_index = len(y_lits) - 1
            y_lits[flip_index] = -y_var[lock_cells[flip_index]]

    assert solutions, "存在最少解除方案时枚举应至少返回一个"
    return {"unlock_count": best, "solutions": solutions}
