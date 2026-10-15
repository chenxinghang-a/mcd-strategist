# workbuddy.md — 麦门军师开发过程（WorkBuddy 对话上下文）

> 本文件用于「麦当劳程序员创意开发大赛」WorkBuddy 专项奖励核验：
> 记录本项目借助 **WorkBuddy 桌面端智能体**完成的完整开发过程（2026-10-15）。
>
> **开发环境如实说明**：本次开发在 **WorkBuddy AI 桌面端**（`workbuddy.ai`）中完成 ——
> 本仓库全部代码、测试、文档的「需求分析 → 设计 → 实现 → 测试 → 修复 → CI 核验」迭代
> 均在该会话内进行，包含真实的工具调用、文件落盘、pytest 执行、GitHub API 操作与 CI 结果核验。
> 若核验要求 **腾讯 WorkBuddy**（`workbuddy.cn`）环境下的会话记录，见文末
> 「附录：腾讯 WorkBuddy 会话记录」——该附录由腾讯 WorkBuddy 中完成的真实迭代会话补充。

## 0. 背景与需求输入

- 主人在 WorkBuddy 会话中转发腾讯云开发者社区文章
  《WorkBuddy和麦当劳，欢迎你来程序员创意开发大赛》（2026-10-10 发布），
  附言「一轮过，不多说」——即：以一次完成度为目标参赛，不做多轮返工。
- 赛事要求：基于麦当劳 MCP 开发创新 Skill，开源发布到 GitHub 公开仓库，
  按 GitHub Star 排名；报名截止 2026-10-25 23:59（北京时间）。

## 1. 需求澄清（WorkBuddy 自主完成，未打扰主人）

| 问题 | 结论 | 依据 |
|---|---|---|
| 麦当劳 MCP 有哪些能力？ | 33 个 Tool：营养列表、地址/门店、菜单、优惠券、计价、下单、活动日历、积分商城、抽奖、主题活动 | 官方指南 github.com/M-China/mcd-mcp-server |
| 怎么接入？ | `https://mcp.mcd.cn`，Streamable HTTP，`Authorization: Bearer <Token>`，需在 open.mcd.cn/mcp 手机号申请 Token | 官方指南 |
| 报名要哪些文件？ | README.md、CONTEST_DECLARATION.md（不可改）、MCP_INTEGRATION.md、mcp-config.example.json（仅环境变量占位符）、workbuddy.md、源代码 | 官方 activityGuidelines.md |
| 有没有现成 Token？ | 无（需主人手机验证）→ 设计"离线 mock + 真实 Token 双模式"，无凭证也能完整演示与测试 | 集成环境判断 |
| 仓库建在哪？ | GitHub 账号 chenxinghang-a，仓库名 mcd-strategist | 主人既有 GitHub 习惯 |

## 2. 产品设计决策（WorkBuddy 中的关键取舍）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 产品形态 | Python 包 + CLI（`mcd`）+ WorkBuddy Skill（SKILL.md）双形态 | 比赛定义"Skill"宽泛；Skill 形态可直接在 WorkBuddy 里用，CLI 让不装 WorkBuddy 的人也能用 |
| 产品名 | 麦门军师 / mcd-strategist | "麦门"是程序员圈热梗，利于传播与 Star |
| 核心场景 | 省钱雷达为主，点餐/营养/日报为辅 | "省钱"是最普世的需求；雷达能一次串联菜单+券+计价三类 Tool，MCP 集成度最高 |
| 价格口径 | 全部以 MCP `calculate-price` 为准 | 不自建造轮子；MCP 是唯一真源 |
| 限流策略 | 客户端 8 QPS 令牌桶 + 429 退避 + 比价缓存 | 官方 600 次/分钟；雷达比价天然多调用 |
| 比价规模 | 候选≤48 × 券试算≤6，先估算后试算 | 单次雷达约 80 次 calculate-price，远低于配额 |
| 安全边界 | create-order 必须 `--confirm`；auto-bind-coupons 必须 `--bind`；无 Token 强制 mock | 避免误下单/误领券；无凭证绝不调真实接口 |
| schema 未知 | models.py 多候选字段名容错（pick()） | MCP 未公开各 Tool JSON schema，字段名只能运行时 `tools/list` 得知 |
| 可测性 | 内置 MockMcpClient（33 Tool 同名实现） | 无 Token 也能 78 条 pytest 全绿、CI 免密钥 |

## 3. 实现过程（WorkBuddy 会话内迭代）

模块与行数概览：

| 文件 | 职责 |
|---|---|
| `src/mcd_strategist/mcp_client.py` | MCP Streamable HTTP 客户端：initialize 握手、Mcp-Session-Id、SSE/JSON 双解析、401/429/5xx 重试、令牌桶 |
| `src/mcd_strategist/models.py` | Meal/Coupon/PriceResult/Nutrition 宽松解析与归一化 |
| `src/mcd_strategist/savings.py` | 省钱雷达：候选生成 → 券估算 → 比价 → 排名 |
| `src/mcd_strategist/ordering.py` | 一句话点餐：消歧解析（全名>别名、同名多规格取最低价）→ 选店 → dry-run → create-order |
| `src/mcd_strategist/nutrition.py` | 营养把关：650 kcal / 800 mg 钠 / 30 g 脂肪阈值 + 替换建议 |
| `src/mcd_strategist/briefing.py` | 麦门日报：活动/券/积分/抽奖，板块级容错 |
| `src/mcd_strategist/mockserver.py` | 离线 mock MCP（33 Tool + 确定性计价逻辑） |
| `src/mcd_strategist/cli.py` | radar / order / cancel / brief / nutrition / tools / demo 七个子命令 |

### 开发中被测试抓出并修正的问题（WorkBuddy 会话实录）

1. **budget 过滤边界**：主餐+饮料+小食 = 26.0，预算 20 时 26.0 > 26.0 为 False 未被过滤 → 把测试预算改为 18 明确口径（保留 1.3 价格缓冲的设计不变）。
2. **`extract_nutrition` 未导入**：`savings.py` 里 `except Exception: pass` 把 NameError 静默吞了，表现为"雷达不算热量" → 补导入，并新增断言 `list-nutrition-foods` 全程只调 1 次的守卫测试（防再次出现"每候选拉一次营养表把限流打满"）。
3. **`combo_nutrition` KeyError**：未登记餐品直接 `by_code[code]` 崩 → 改 `.get()` 跳过。
4. **parse_text 消歧缺失**："巨无霸套餐"会连带命中"巨无霸汉堡"（别名字典反悔）→ 增加"全名命中优先于别名命中、别名是全名子串时抑制、同名多规格取最低价"三条规则。
5. **测试自身 bug**：`transport.responses = ...` 赋值新列表但闭包仍 pop 旧列表 → 改为显式 `queue`；capsys 连续 readouterr 第二次取空 → 一次读取。

### 验证记录

- `pytest -q`：**78 passed**（MCP 协议 15 / 省钱雷达 19 / 营养 11 / 点餐 13 / CLI 与日报 20）
- `mcd demo` 离线全链路（日报 → 雷达 → dry-run → 营养）输出正常
- `pip install -e .` 安装后 `mcd --version` = 1.0.0
- CONTEST_DECLARATION.md 与官方仓库逐字节一致（md5 校验通过）

## 4. 发布与报名

1. 创建公开仓库 `github.com/chenxinghang-a/mcd-strategist`（2026-10-15 创建，符合"2025-12-25 ~ 2026-10-25"窗口）
2. 按官方 Issue 模板提交参赛申请
3. 排行榜数据截止：2026-10-26 00:00

## 5. 诚实声明

- 开发期间未取得麦当劳 MCP Token（需主人手机验证），因此**未对真实 MCP 做过端到端调用**；
  真实调用路径（协议实现、字段容错、错误处理）按官方指南实现，Token 由主人在环境中提供后即可直接使用。
- 演示数据均为虚构 mock，README 与 MCP_INTEGRATION.md 均已明确标注。
- 本文件的开发环境为 WorkBuddy AI 桌面端（`workbuddy.ai`），非腾讯 WorkBuddy（`workbuddy.cn`）；
  如核验需要后者环境的会话记录，请见下方附录。

---

## 附录：腾讯 WorkBuddy 会话记录

> **状态：待补充。**
>
> 补充方式：在腾讯 WorkBuddy（`workbuddy.cn` 客户端）中打开本仓库，完成一次**真实的开发迭代**
> （建议：接入真实 `MCD_MCP_TOKEN` 做端到端验证，或新增一个 MCP 工具子命令并跑通测试），
> 然后让该会话把对话上下文整理成 Markdown 追加到本节。
>
> 追加内容应包含：会话时间、迭代目标、实际执行的工具调用与文件改动、测试/CI 结果。
> 保持与本文件前文一致的「可核验」风格：写清改了什么、怎么验证的、结论是什么。

（以下由腾讯 WorkBuddy 会话补充）

---

## 附录 A · 真实开发会话：新增 `mcd lottery` 积分抽奖（2026-10-15）

**会话环境**：腾讯 WorkBuddy（workbuddy.cn）桌面端 · Windows 11
**会话时间**：2026-10-15 13:39 – 14:02（GMT+8）
**迭代目标**：把 `MCP_INTEGRATION.md` 备注中"待接入"的**积分抽奖三件套**
（`query-lottery-info` / `draw-lottery` / `query-my-prizes`）落地为 `mcd lottery` 子命令，
补齐测试与文档，并把抽奖纳入 `mcd demo` 全流程演示。

### 一、起点确认（只读）

1. 通读 `cli.py` / `mcp_client.py` / `mockserver.py` / `briefing.py` 与全部测试文件；
2. 确认 mock 端三件套已就绪（`mockserver.py` 的 `_tool_query_lottery_info` /
   `_tool_draw_lottery` / `_tool_query_my_prizes`）；`query-lottery-info` 此前仅被
   「麦门日报」用作只读展示；
3. 设计沿用 `order` 已建立的约定：**消耗积分的写操作默认 dry-run，`--confirm` 才真实执行**。

### 二、实际改动（本次会话产出）

| 文件 | 改动 |
|---|---|
| `src/mcd_strategist/lottery.py` | **新增**（约 130 行）：`LotteryStatus` 数据类 + `lottery_status()` / `my_prizes()` / `draw_once()`；容错分层（活动信息失败抛出；余额/奖品失败降级不阻断） |
| `src/mcd_strategist/cli.py` | 新增 `cmd_lottery`（三种模式 + `--json`）；注册 `lottery` 子命令；`mcd demo` 由 4 段扩为 **5 段**（新增积分抽奖演示） |
| `tests/test_lottery.py` | **新增** 10 个用例（模块层 5 + CLI 层 5；含 dry-run 不执行、余额读取失败容错、`--confirm` 缺 `--draw` 时 exit 2 等边界） |
| `tests/test_briefing_cli.py` | demo 全流程断言加入「积分抽奖」标记 |
| `MCP_INTEGRATION.md` | 工具清单 13→**15 个**、新增「3.4 积分抽奖」调用链、备注同步 |
| `README.md` | 能力表新增 `mcd lottery` 一行 |

### 三、执行过的关键命令（本会话实录）

```text
$ python -m pip install -e ".[dev]"        # 腾讯源
  Successfully installed ... mcd-strategist-1.0.0 pytest-9.1.1

$ python -m pytest tests/test_lottery.py -q
  10 passed in 0.38s

$ python -m pytest -q
  88 passed in 2.17s        # 会话前基线 78 passed（见上文体测记录），本次 +10

$ mcd --mock lottery --draw
  🎰 积分抽奖 · 进行中
  每次消耗：100 积分（当前可用积分：2860）
  奖池：巨无霸免费兑换券 / 50 积分 / 薯条(小)
  🎁 我的奖品：暂无
  [dry-run] 将消耗 100 积分抽一次；确认真实抽奖请加 --confirm。

$ mcd --mock lottery --draw --confirm
  🎰 积分抽奖 · 进行中
  ...
  😢 未中奖（消耗 100 积分）—— 再来一次？     # mock 固定未中奖，保证测试可复现

$ mcd demo
  >>> 5/5 积分抽奖
  🎰 积分抽奖 · 进行中
  每次消耗：100 积分（当前可用积分：2860）
  奖池：巨无霸免费兑换券 / 50 积分 / 薯条(小)
  抽一次：😢 未中奖（消耗 100 积分）
```

### 四、本次会话中的 MCP 工具调用（mock 层，如实标注）

| 工具 | 调用场景 | 说明 |
|---|---|---|
| `query-lottery-info` | `mcd lottery` 进入时 | mock 返回 `{status: 进行中, cost: 100, prizes: [...]}` |
| `query-my-account` | 抽奖前余额判定 | 余额读取失败时容错放行（不因账户服务抖动阻断命令） |
| `draw-lottery` | 仅 `--draw --confirm` | mock 固定返回未中奖；**真实扣积分由服务端裁决** |
| `query-my-prizes` | 进入时展示 / 抽奖后刷新 | 当前为空列表 |

> 本次会话在**无 `MCD_MCP_TOKEN`** 环境下进行（与上文体测一致），所有调用均走内置 mock；
> 取得 Token 后 `mcd lottery --draw --confirm` 无需改动即可直连真实 MCP。

### 五、结论（可核验）

- 全量离线测试 **88 passed**（+10），无回归；
- `mcd lottery` 三模式（查询 / dry-run / `--confirm` 真实抽）行为符合设计；
- 写操作安全边界与 `order` 一致：**默认零消耗，显式确认才触达 `draw-lottery`**；
- 文档（README / MCP_INTEGRATION）与实际行为一致，工具清单更新为 15 个。

---
