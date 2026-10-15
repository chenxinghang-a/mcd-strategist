# workbuddy.md — 麦门军师开发过程（WorkBuddy 对话上下文）

> 本文件用于「麦当劳程序员创意开发大赛」WorkBuddy 专项奖励核验：
> 记录本项目在腾讯 WorkBuddy 智能体协助下的完整开发过程（2026-10-15）。
> 开发工具：腾讯 WorkBuddy（WorkBuddy AI，本仓库的全部代码、测试与文档均在其中
> 完成需求分析、设计、实现、验证的迭代）。

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
