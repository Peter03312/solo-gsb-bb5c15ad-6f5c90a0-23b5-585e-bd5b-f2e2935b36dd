# 周期平铺织物组织设计器

设计师在网页上录入周期平铺的织物组织（交织格），系统按环向浮长规则校验原稿、
标出超限段，并求出**翻转未锁定格数量最少**的可行修补；当存在多个同样最优的修补时，
一次预览返回按行展开格值字典序排列的**前 3 个互不相同的候选网格**（不足 3 个按实际
数量返回），设计师可在网页上逐个查看、比较后选择其中一个确认。确认预览后才覆盖原稿。

- 后端：Python + Flask + SQLite（求解器基于 PySAT 的 MaxSAT/ SAT 编码）
- 前端：React（Vite 构建，由后端统一托管）
- 部署：Dockerfile + Docker Compose 一键启动，含依赖安装与数据库初始化

## 规则说明

- 格值：`0` 表示经线在上，`1` 表示纬线在上。
- 尺寸：1~16 行、1~8 列的非空矩形。
- 浮长：每行、每列的相同值连续段按**环向**计（左右、上下拼接边界算作相连），
  长度不得超过正整数 K；**全同的一行或一列视为无限浮长**（恒超限）。
- 修补：只能翻转未锁定格；目标为翻转数最少。存在多个全局最少翻转的修补时，
  预览返回按行展开格值字典序升序的**前 3 个互不相同候选**（不足 3 个按实际数量），
  确认时只能选择该令牌预览中实际返回的候选；旧请求未指定候选时确认首个方案。
- 格值1总数区间（可选）：设计师可为草稿指定修补稿中格值 `1` 的总格数区间
  `[下限, 上限]`；不填（两栏留空 / 请求省略或传 `null`）时沿用原有修补规则。
  填写时**两端都必填**且满足 `0 ≤ 下限 ≤ 上限 ≤ 行数×列数`；尺寸变化导致区间
  越界时只提示调整，**绝不悄然截断**。预览在环向浮长 K、锁定格与总数区间的
  共同约束下，只从总数达标的修补稿中选翻转最少者（并列仍取按行展开字典序最小）；
  总数不可达时按无可行修补处理（不发令牌）。
- **并列最优候选比选（本轮）**：先在环向浮长 K、锁定格与可选 `onesRange` 约束下
  求**全局最少翻转数**，再在「翻转数恰好等于该最小值」的可行网格中，按行展开格值
  字典序升序枚举前 3 个**互不相同**的网格返回；不足 3 个时按实际数量返回（唯一解时
  仅 1 个），无解仍不发放令牌。预览响应的 `repaired` / `flips` / `flipCount` /
  `repairedOnesCount` 等旧字段保留且**始终指向首个（字典序最小）方案**；新增
  `candidateCount` 与 `candidates`，每个候选含 `repaired`（网格）、`flips`
  （翻转坐标）、`onesCount`（格值 1 总数）。确认时用 `candidate` 序号（从 0 起）
  选择，只能选择该令牌预览中返回的方案；省略/`null`/`0` 均确认首个方案；序号越界、
  类型非法或伪造一律 **400 拒绝且不消费令牌、原稿不变**。
- 无可行修补时保留原稿并报无解；尺寸、格值、K 或区间不合法或编辑越界时拒绝操作。
- **无解自诊断（本轮，只读）**：预览报无可行修补时，可用 `POST /api/diagnose`
  对**同一份草稿**做只读自诊断，区分两类无解：
  - **锁定格阻碍**：保持尺寸、格值、K 及可选 `onesRange` 不变，求「使修补
    变为可行所需解除的锁定格」**全局最少数量**；并列时按**解锁坐标列表
    字典序**返回前 3 个**互异解锁集合**，每个集合附**一份**可行修补示例
    （该解锁集合下按行展开格值字典序最小；满足环向浮长 K 与总数区间；
    **未解锁的锁定格一律保持原值**）。
  - **结构性无解**：即使解除全部锁定格仍无解（如 1×1、单行/单列——环向
    整行/整列必全同恒为无限浮长，或 `onesRange` 在浮长规则下本身不可达），
    接口明确返回 `structural:true` 且**不给出任何解锁建议**，避免误导。
  - 诊断**只供查看**：不落库、不产生/消费任何预览令牌、不改变草稿、原稿、
    锁定、区间或版本；原有可解预览、候选顺序与预览/确认契约完全不变。
  - 诊断响应回显当时规范化草稿 `input`；网页在自诊断后若发现草稿（尺寸、
    格值、锁定、K 或区间）已变化，**拒绝沿用旧建议**并提示重新诊断。
    网页可把选中的解锁集合一键应用到当前草稿（仅解除这些锁定格，
    其余编辑不变），应用后同样需重新诊断/预览。
  - 草稿当前本可修补时诊断返回 `solvable:true,minUnlockCount:0` 与空解锁
    集合（示例即一份可行修补），无需解锁。
- 区间随确认结果**与原稿一并持久化**（`GET /api/state` 可读出）；预览与确认的
  一致性比对包含区间，区间变更沿用既有 409 `mismatch` 冲突语义。
- 预览在生成时绑定当时的**原稿版本**；确认在单条数据库事务中原子校验
  「令牌未消费 + 提交草稿与预览输入逐字节一致 + 原稿版本未变」，
  三者同时成立才覆盖原稿（版本号恰好 +1）并消费令牌；任一不满足即整体回滚、
  原稿不变并返回 409（预览已失效）。因此并发或重复确认最多生效一次，
  甲确认后乙持有的旧令牌无法覆盖甲的结果。
- 网页收到 409 后会清除旧预览、重新拉取原稿、保留当前未提交草稿，
  并提示重新预览后再确认。

## 冲突处理（预览令牌与版本）

- `POST /api/preview`：服务端读取当前原稿版本 `v`，把规范化后的草稿输入
  （尺寸、格值、锁定、K、**格值1总数区间**）与求解结果（含至多 3 个候选）、令牌、
  基础版本 `v` 一并落库；无解（含总数区间不可达）时返回 `solvable: false`，
  **不发放令牌**，不可确认。
- `POST /api/confirm`：请求字段在原有基础上增加可选的 `onesRange`
  （`null` 或省略均表示不限制）与 `candidate`（候选序号，省略/`null`/`0`
  表示首个方案）。服务端在 `BEGIN IMMEDIATE` 写事务内完成校验与覆盖：

  | 情况                       | 结果        | 原稿/版本          |
  | -------------------------- | ----------- | ------------------ |
  | 令牌未消费、草稿一致（含区间）、版本未变、候选序号存在 | 200，覆盖原稿 | 原稿与区间更新，版本 +1，令牌删除 |
  | `candidate` 类型非法或超出该预览候选范围（越界/伪造） | **400** `bad_candidate` | 不变（令牌保留，可重新选择） |
  | 令牌不存在或已被确认（含并发重复提交） | 409 `consumed` | 不变 |
  | 草稿与预览输入不一致（含区间被改动/清除） | 409 `mismatch` | 不变 |
  | 预览绑定的原稿版本已被他人确认推进 | 409 `stale` | 不变 |
  | 无解预览、非法尺寸/格值/K/区间     | 400（无解预览本身来自 preview 接口的 `solvable:false`） | 不变 |

  三种 409 响应体均为 `{"error": "…请重新预览"}`，原稿与版本号不发生任何变化；
  冲突后重新 `POST /api/preview` 取得新令牌即可正常确认。旧版本令牌
  （生成于本轮功能之前的预览）仅可不带 `candidate` 确认首个方案，指定其他序号按
  400 `bad_candidate` 拒绝；重新预览即可获得完整候选。

### 调用示例

```bash
# 1. 查看原稿（含版本号、格值1总数与区间）
curl -s http://localhost:8000/api/state
# {"rows":4,"cols":4,...,"version":1,"onesCount":8,"onesRange":null,"violations":[]}

# 2. 提交草稿预览，拿到令牌（无解时 solvable=false 且无 token）
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":2,"cols":2,"grid":[[0,1],[0,1]],"locks":[],"k":2}'
# {"solvable":true,"token":"689b…","repaired":[[0,1],[1,0]],
#  "onesCount":2,"repairedOnesCount":2,"onesRange":null, ...}

# 2b. 指定格值1总数区间 [下限, 上限]（两端必填，0≤下限≤上限≤行数×列数）
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":4,"cols":4,
       "grid":[[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],
       "locks":[],"k":2,"onesRange":[8,8]}'
# {"solvable":true,"token":"ab12…",
#  "onesCount":0,"repairedOnesCount":8,"onesRange":[8,8],
#  "repaired":[[0,0,1,1],[0,0,1,1],[1,1,0,0],[1,1,0,0]], ...}

# 2c. 区间不可达（含锁定冲突、全同列/行等）-> 无令牌
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":4,"cols":4,"grid":[...全0...],"locks":[],"k":2,"onesRange":[9,9]}'
# {"solvable":false,"onesRange":[9,9],"onesCount":0,...}  （无 token 字段）

# 2d. 区间越界/只填一端/下限>上限 -> 400（不会截断）
# {"error":"格值1总数区间上限 99 超出当前尺寸总格数 16（4×4），请调整区间或尺寸"}

# 3. 携带令牌与同一份草稿确认（区间必须与预览输入逐字节一致）
curl -s -X POST http://localhost:8000/api/confirm \
  -H 'Content-Type: application/json' \
  -d '{"token":"ab12…","rows":4,"cols":4,"grid":[...全0...],
       "locks":[],"k":2,"onesRange":[8,8]}'
# {"state":{...,"version":2,"onesRange":[8,8],"onesCount":8,"violations":[...]}}

# 3b. 并列最优候选比选：2x2 全0、K=2 恰有 2 个同样最优方案
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":2,"cols":2,"grid":[[0,0],[0,0]],"locks":[],"k":2}'
# {"solvable":true,"token":"cd34…","candidateCount":2,
#  "repaired":[[0,1],[1,0]],"flips":[[0,1],[1,0]],"flipCount":2,
#  "repairedOnesCount":2,
#  "candidates":[
#    {"repaired":[[0,1],[1,0]],"flips":[[0,1],[1,0]],"onesCount":2},
#    {"repaired":[[1,0],[0,1]],"flips":[[0,0],[1,1]],"onesCount":2}]}
# 旧字段 repaired/flips/flipCount 始终指向候选 0（字典序最小）

# 不指定 candidate（或 null/0）-> 确认首个方案
curl -s -X POST http://localhost:8000/api/confirm \
  -H 'Content-Type: application/json' \
  -d '{"token":"cd34…","candidate":0,"rows":2,"cols":2,
       "grid":[[0,0],[0,0]],"locks":[],"k":2}'

# 选择第 2 个候选（序号从 0 起）-> 覆盖为 [[1,0],[0,1]]
curl -s -X POST http://localhost:8000/api/confirm \
  -H 'Content-Type: application/json' \
  -d '{"token":"cd34…","candidate":1,"rows":2,"cols":2,
       "grid":[[0,0],[0,0]],"locks":[],"k":2}'
# {"state":{...,"grid":[[1,0],[0,1]],...}}

# 序号越界/伪造（预览只有 2 个候选）-> 400，令牌未消费、原稿不变
# {"error":"所选候选方案不在该预览返回的范围内，请从预览候选中选择"}
# 序号类型非法（字符串/负数/小数/布尔）-> 400
# {"error":"候选方案序号必须是非负整数"}

# 3c. 确认时不带区间（或传 [8,9]）而预览带了 [8,8] -> 409 mismatch，原稿不变
# 4. 同一令牌再次确认（或他人已抢先确认、草稿已变动）-> 409，原稿不变
# {"error":"预览令牌不存在或已被确认，请重新预览"}

# 5. 无可行修补时的只读自诊断（不落库、不发令牌、不改任何稿件）
# 5a. 锁定格阻碍：2x2 全0、K=2，首列两格锁 0（列全同无限浮长）-> 最少解 1 格
curl -s -X POST http://localhost:8000/api/diagnose \
  -H 'Content-Type: application/json' \
  -d '{"rows":2,"cols":2,"grid":[[0,0],[0,0]],"locks":[[0,0],[1,0]],"k":2}'
# {"solvable":false,"structural":false,"minUnlockCount":1,"unlockSetCount":2,
#  "solutions":[
#    {"unlocks":[[0,0]],
#     "repaired":[[1,0],[0,1]],"changes":[[0,0],[1,1]],"onesCount":2},
#    {"unlocks":[[1,0]],
#     "repaired":[[0,1],[1,0]],"changes":[[0,1],[1,0]],"onesCount":2}],
#  "onesCount":0,"onesRange":null,"input":{...},"violations":[...]}
# 解锁集合按坐标列表字典序；示例未解锁的锁定格保持原值，满足浮长与区间。
# 响应中没有 token 字段——诊断只读，不能用于 /api/confirm。

# 5b. 结构性无解：即使全部解锁仍无解（1x1 / 单行单列 / 区间本身不可达）
curl -s -X POST http://localhost:8000/api/diagnose \
  -H 'Content-Type: application/json' \
  -d '{"rows":1,"cols":1,"grid":[[0]],"locks":[[0,0]],"k":2}'
# {"solvable":false,"structural":true,"onesCount":0,"onesRange":null,
#  "input":{...},"violations":[...]}     （无 minUnlockCount / solutions / token）

# 5c. 带格值1总数区间的诊断（区间保持不变；示例总数须落在区间内）
curl -s -X POST http://localhost:8000/api/diagnose \
  -H 'Content-Type: application/json' \
  -d '{"rows":4,"cols":4,"grid":[[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],
       "locks":[[0,0],[0,1],[0,2],[0,3]],"k":2,"onesRange":[8,8]}'
# {"solvable":false,"structural":false,"minUnlockCount":2,"unlockSetCount":3,
#  "solutions":[{"unlocks":[[0,0],[0,1]],"onesCount":8,...}, ...]}

# 5d. 草稿当前本可修补：solvable=true、无需解锁（空集合 + 一份可行修补示例）
curl -s -X POST http://localhost:8000/api/diagnose \
  -H 'Content-Type: application/json' \
  -d '{"rows":2,"cols":2,"grid":[[0,1],[0,1]],"locks":[],"k":2}'
# {"solvable":true,"structural":false,"minUnlockCount":0,"unlockSetCount":1,
#  "solutions":[{"unlocks":[],"repaired":[[0,1],[1,0]],...}], ...}
# 注：这只是诊断视图；要覆盖原稿仍须走 /api/preview + /api/confirm。
```

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

启动后访问：

- 网页地址： <http://localhost:8000>
- 端口： `8000`（宿主机端口可在 `docker-compose.yml` 的 `ports` 中修改）

首次启动会自动初始化 SQLite 数据库（建表 + 写入一份默认原稿），
数据库文件保存在名为 `weave-data` 的卷中，容器重建后数据仍在。

停止：

```bash
docker compose down            # 保留数据卷
docker compose down -v         # 连同数据库一起删除
```

## 配置（环境变量）

| 变量      | 默认值            | 说明                       |
| --------- | ----------------- | -------------------------- |
| `PORT`    | `8000`            | 后端监听端口（容器内）     |
| `DB_PATH` | `/data/weave.db`  | SQLite 数据库文件路径      |

也可不用 Compose，直接构建镜像：

```bash
docker build -t weave-designer .
docker run -p 8000:8000 -v weave-data:/data weave-designer
```

## 本地开发（不用 Docker）

```bash
# 后端（Python 3.11+）
cd server
pip install -r requirements.txt
python app.py                 # 监听 http://localhost:8000

# 前端（Node 20+，另开终端；开发服务器代理 /api 到 8000 端口）
cd frontend
npm ci
npm run dev                   # http://localhost:5173

# 生产形态：构建前端后由 Flask 托管
cd frontend && npm run build
cp -r dist ../server/static
cd ../server && python app.py
```

后端调试模式：`FLASK_DEBUG=1 python app.py`（Flask 内置服务器，自动重载）。

## 运行测试

```bash
cd server
python test_solver.py
```

求解器测试内容：固定边界用例（全同、锁定冲突、环向跨界、K 极大等）、
60 组随机小网格与暴力枚举对照（翻转数最少 + 字典序最小）、
格值1总数区间固定/随机用例（与暴力对照，含区间不可达、锁定冲突、精确计数）、
前 3 个同样最优候选的固定/60 组随机暴力对照（候选数、字典序、互不相同、
同翻转数、锁定与区间达标、`limit=1` 与单候选封装一致）、
**无解诊断的固定/约 3000 份随机暴力对照（最少解锁数全局最优、解锁集合按
坐标列表字典序前 3、互异、示例行展开字典序最小且满足浮长/区间、未解锁
锁定格保持原值、结构性无解不给建议、诊断无副作用）**、
16×8 最大网格在各档 K、区间、前 3 候选枚举与全锁无解诊断下的性能。

```bash
cd server
python test_confirm_race.py
```

确认竞争测试内容：成功确认版本恰好 +1、令牌消费后重复确认 409、
草稿不匹配 409、甲确认后乙的同原稿旧令牌 409、同一令牌 5 线程并发仅一次生效、
无解预览无令牌、非法请求 400、成功响应结构保持不变；
以及格值1总数区间的非法输入 400、区间不可达无令牌、确认体缺/改区间 409、
区间随原稿持久化、无字段/`null` 请求与旧响应的兼容性；
以及候选比选：2/3 个并列候选的预览结构、旧字段恒指首候选、越界/伪造/类型非法
候选 400 且不消费令牌、省略/`null`/`0` 确认首候选、选择其他候选覆盖对应网格、
合法候选叠加草稿改动仍 409、同一令牌携带不同候选并发仅一次生效、
旧版预览（无 `candidates`）只能确认首个方案。

```bash
cd server
python test_diagnose.py
```

无解诊断接口端到端测试内容：结构性无解（1×1、单行、区间本身不可达）
返回 `structural:true` 且无解锁建议与令牌；锁定阻碍时 `minUnlockCount`
全局最少、至多 3 个互异集合按坐标列表字典序、示例满足浮长/区间且未解锁
锁定格不变、`changes`/`onesCount` 自洽；诊断只读（版本、原稿、区间不变，
伪造确认 409）；本可修补草稿返回零解锁；非法输入 400；诊断无状态可重复；
以及与既有预览/确认契约并存（令牌、候选顺序、确认 200/409 不受影响）、
应用解锁集合后重新预览可解的完整闭环。

## API 简介

| 方法 | 路径           | 说明                                                         |
| ---- | -------------- | ------------------------------------------------------------ |
| GET  | `/api/state`   | 返回当前原稿（尺寸、格值、锁定格、K、**格值1总数区间**、版本号、**格值1总数 `onesCount`**）及其超限段 |
| POST | `/api/preview` | 提交草稿（可选 `onesRange:[下限,上限]`），返回超限段与至多 3 个同样最优修补候选（双方格值1总数、区间、翻转位置、令牌）；非法输入 400，无解（含区间不可达）时 `solvable=false` 且无令牌 |
| POST | `/api/diagnose` | **只读**自诊断：提交与预览同构的草稿（含可选 `onesRange`），区分锁定格阻碍（返回全局最少解锁数与按坐标列表字典序前 3 个互异解锁集合及可行修补示例）与结构性无解（`structural:true`，无解锁建议）；不落库、不发令牌、不改变草稿/原稿/版本；非法输入 400 |
| POST | `/api/confirm` | 携带预览令牌、当前草稿（含一致的 `onesRange`）与可选 `candidate` 序号；原子校验令牌未消费、草稿与预览输入一致（含区间）、原稿版本未变、候选存在，成功才覆盖原稿与区间（版本 +1）并消费令牌；候选越界/伪造返回 400，重复/过期/草稿或区间不匹配返回 409 且原稿不变 |

### 候选比选字段约定（`candidates` / `candidate`）

- `POST /api/preview` 可解时返回：
  - `candidateCount`：实际返回的候选数（1~3；并列方案不足 3 个时按实际数量）。
  - `candidates`：按行展开格值字典序升序、互不相同的同样最优（翻转数并列全局最少）
    网格数组，每项为 `{"repaired": [[…]], "flips": [[r, c], …], "onesCount": n}`。
  - `repaired`、`flips`、`flipCount`、`repairedOnesCount` 保留，且恒等于
    `candidates[0]` 的对应内容（首个即字典序最小方案）。
- `POST /api/confirm` 的可选字段 `candidate`：
  - 省略、`null` 或 `0`：确认首个方案（与旧版本请求完全兼容）；
  - 必须是非负整数且 `candidate < candidateCount`；类型非法（字符串/小数/负数/
    布尔/数组）返回 400；序号超出该令牌预览实际返回范围（越界或伪造）也返回 400，
    **不消费令牌、原稿不变**，可重新带合法序号确认。
- 每个候选都在同一预览约束（K、锁定、`onesRange`）下可行，且翻转数相同；
  确认仅能选择同一令牌预览中返回的候选。

### `onesRange` 字段约定

- 省略或为 `null`：不限制格值1总数，修补规则与旧版本完全一致。
- 否则必须是长度 2 的整数数组 `[下限, 上限]`，
  且 `0 ≤ 下限 ≤ 上限 ≤ rows×cols`，否则返回 400（不做任何截断）。
- `GET /api/state` 恒返回 `onesRange`（无区间为 `null`）与 `onesCount`；
  `POST /api/preview` 返回原稿 `onesCount`、候选列表 `candidates`（每项含
  `onesCount`）、首候选的 `repairedOnesCount` 与回显的 `onesRange`
  （无解时不返回候选、`repairedOnesCount` 与 `token`）。
- 未携带 `onesRange` 的旧请求/响应继续可用；未携带 `candidate` 的确认
  一律确认首个方案。

### 无解自诊断字段约定（`POST /api/diagnose`，只读）

请求体与 `/api/preview` 完全同构（`rows`/`cols`/`grid`/`locks`/`k` 及可选
`onesRange`，同样的 400 校验），但诊断**不写库、不发放令牌、不消费令牌、
不改草稿/原稿/锁定/区间/版本**，响应也没有 `token`，无法用于确认。

- 当前草稿可修补：`{"solvable": true, "structural": false,
  "minUnlockCount": 0, "unlockSetCount": 1,
  "solutions": [{"unlocks": [], "repaired": …, "changes": …, "onesCount": n}], …}`。
- 锁定格阻碍（当前锁定下无解，但解除部分锁定可修补）：
  - `solvable:false`、`structural:false`；
  - `minUnlockCount`：使修补可行所需解除锁定格的**全局最少数量**；
  - `unlockSetCount` / `solutions`：按**解锁坐标列表字典序**升序的前
    3 个（不足按实际）**互异**最少解锁集合，每个集合解锁数都等于
    `minUnlockCount`，每项为
    `{"unlocks": [[r,c],…], "repaired": [[…]], "changes": [[r,c],…],
    "onesCount": n}`；`repaired` 是该解锁集合下按行展开格值字典序最小的
    一份可行修补示例，`changes` 是示例相对草稿改变的全部格（含被解除并
    翻转的锁定格），`onesCount` 是示例格值 1 总数；
  - 示例恒满足环向浮长 K 与 `onesRange`，且**未出现在 `unlocks` 中的
    锁定格保持草稿原值不变**。
- 结构性无解（即使解除全部锁定仍无解）：
  `{"solvable": false, "structural": true, …}`，**不返回**
  `minUnlockCount`/`unlockSetCount`/`solutions`，也无 `token`；
  典型为 1×1、单行或单列（环向整行/整列必全同）或总数区间本身不可达，
  此时解锁锁定格无济于事，需调整尺寸、K 或区间。
- 三种情况都回显 `input`（规范化草稿，含 `onesRange`）、`onesCount`、
  `onesRange` 与 `violations`。网页据此判断「自诊断后草稿是否已变化」：
  当前草稿与 `input` 不一致即视为旧建议失效，拒绝沿用、提示重新诊断；
  「应用解锁集合到当前草稿」只从草稿 `locks` 中移除所选坐标
  （尺寸、格值、K、区间不变），应用后草稿改变，同样需要重新诊断/预览。

## 目录结构

```
├── Dockerfile              # 多阶段：前端构建 + 后端运行
├── docker-compose.yml      # 一键启动
├── server/                 # Python 后端
│   ├── app.py              # Flask 路由（state / preview / diagnose / confirm）
│   ├── solver.py           # SAT/MaxSAT 最优修补求解 + 无解最少解锁诊断
│   ├── weave.py            # 环向超限段检测
│   ├── validate.py         # 尺寸/格值/锁定/K 校验
│   ├── db.py               # SQLite 持久化与初始化（预览版本绑定 + 原子确认事务）
│   ├── test_solver.py      # 求解器测试（含暴力对照、无解诊断对照）
│   ├── test_confirm_race.py # 确认竞争端到端测试（含并发、冲突拒绝）
│   ├── test_diagnose.py    # 无解诊断接口端到端测试（只读/结构无解/最少解锁）
│   └── requirements.txt
└── frontend/               # React 前端（Vite）
    └── src/
        ├── App.jsx         # 编辑器、预览、确认、无解诊断与解锁应用交互
        ├── weave.js        # 前端实时超限段检测
        └── api.js
```
