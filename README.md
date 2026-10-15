# 🍔 麦门军师 · mcd-strategist

<div align="center">

**你的麦当劳点餐决策助手 —— 全部跑在麦当劳官方 MCP 上**

领券 · 比价 · 点餐 · 把关 · 日报，一句话的事

[![CI](https://github.com/chenxinghang-a/mcd-strategist/actions/workflows/ci.yml/badge.svg)](https://github.com/chenxinghang-a/mcd-strategist/actions)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![tests](https://img.shields.io/badge/tests-77%20passed-brightgreen.svg)](#%E5%BC%80%E5%8F%91%E4%B8%8E%E6%B5%8B%E8%AF%95)
[![MCP](https://img.shields.io/badge/McDonald's-MCP-ffc0cb.svg)](MCP_INTEGRATION.md)

</div>

> "麦门永存。" —— 每一位在 1024 认真吃汉堡的程序员

## ✨ 它能做什么

| 能力 | 命令 | 说明 |
|---|---|---|
| 🎯 **省钱雷达** | `mcd radar --budget 30` | 菜单 + 麦麦省券 + 门店券，自动生成候选组合并逐个调 `calculate-price` 比价，给出预算内最优方案 |
| 🧾 **一句话点餐** | `mcd order "来份巨无霸套餐加薯条"` | 自然语言解析 → 选店 → 计价，**默认 dry-run**，`--confirm` 才真实 `create-order` |
| 🥗 **营养把关** | `mcd nutrition 麦辣鸡腿堡套餐` | 基于 `list-nutrition-foods` 的能量/钠/脂肪阈值评估 + 同类替换建议 |
| 📰 **麦门日报** | `mcd brief` | 活动日历 + 可领券 + 积分 + 积分抽奖，一站式早报 |
| 🎰 **积分抽奖** | `mcd lottery` | 活动状态 + 奖池 + 我的奖品；`--draw` 抽奖（默认 dry-run，`--confirm` 才真实消耗积分） |
| 🛠️ **连通自检** | `mcd tools` | 列出麦当劳 MCP 全部 33 个工具 |
| 🎬 **离线演示** | `mcd demo` | 没有 Token 也能预览全部能力（内置模拟 MCP） |

贴近生活的例子：

- *"预算 30，吃什么最划算？"* → radar 直接给出排序结果和券的用法
- *"巨无霸套餐加薯条，多少钱？帮我下单"* → dry-run 先看价，确认后才真下单
- *"汉堡套餐都超热量参考值吗？"* → nutrition 逐项把关，还给替换建议

## 🚀 快速开始

### 前置：申请麦当劳 MCP Token（约 1 分钟）

打开 [麦当劳 MCP 开放平台](https://open.mcd.cn/mcp) → 手机号验证登录 → 右上角「控制台」→ 点「激活」→ 复制 Token。

```bash
export MCD_MCP_TOKEN="你的Token"
```

### 方式 A：作为 WorkBuddy Skill 使用（推荐）

麦门军师本身也是一个 [WorkBuddy Skill](SKILL.md)。把本仓库克隆到本地后，在 WorkBuddy 的连接器配置里加入麦当劳 MCP（配置见 [mcp-config.example.json](mcp-config.example.json)），即可在对话里直接说：

> "用麦门军师看看 30 块钱怎么吃最划算"

### 方式 B：作为命令行工具使用

```bash
# 安装（唯一第三方依赖：requests）
pip install -e .
# 或直接跑：PYTHONPATH=src python -m mcd_strategist.cli

# 没有 Token 也能先看演示
mcd demo

# 有了 Token：开雷达！
mcd radar --budget 30
mcd radar --budget 50 --bind        # --bind 会调用 auto-bind-coupons 一键领券
mcd brief                           # 麦门日报
mcd order "来一份巨无霸套餐"         # dry-run
mcd order "来一份巨无霸套餐" --confirm   # 确认后真实下单
mcd nutrition 巨无霸套餐             # 营养把关
```

## 🎬 演示输出（离线 mock 数据）

```
$ mcd demo
>>> 2/4 省钱雷达：预算 ¥30
1. 麦香鱼｜券：麦麦省8.8折券｜原价 ¥11.50 → 实付 ¥10.12｜约 390 kcal
2. 板烧鸡腿堡｜券：麦麦省8.8折券｜原价 ¥13.00 → 实付 ¥11.44｜约 480 kcal
3. 麦辣鸡腿堡｜券：麦麦省8.8折券｜原价 ¥13.50 → 实付 ¥11.88｜约 590 kcal

>>> 3/4 一句话点餐（dry-run）
识别：巨无霸套餐×1、薯条(小)×1
原价 ¥34.50 → 实付 ¥34.50
· 门店由 query-nearby-stores 自动选取
· dry-run 完成：未创建订单。加 --confirm 才会真正下单。

>>> 4/4 营养把关
巨无霸套餐 + 薯条(大) + 可口可乐(中)：能量 1410 kcal | 蛋白质 35.0 g | 脂肪 60.0 g
| 碳水 175.0 g | 钠 1515 mg ⚠️ 能量 1410kcal 超出单餐参考值 650kcal；钠 1515mg 超出单餐参考值 800mg
🔁 可换：鲜煮咖啡（90 kcal）
```

## 🧠 设计要点

- **价格以 MCP 为单一真源**：所有"多少钱、省多少"都由麦当劳 `calculate-price` 算，本地只做候选生成和排序，不自己造轮子。
- **限流友好**：官方限额 600 次/分钟。客户端内置 8 QPS 限速 + 429 退避重试；雷达按券面估算价值先排序、每个组合最多试 6 张券，mock 菜单实测约 77 次 `calculate-price`（具体次数随真实菜单和券包变化）。
- **字段宽松解析**：MCP 未公开各 Tool 的 JSON schema，`models.py` 对字段名做多候选容错（如 `totalPrice`/`originalPrice`/`totalAmount`…），换字段名不崩。
- **默认安全**：`create-order` 必须显式 `--confirm`；`auto-bind-coupons` 必须显式 `--bind`；Token 只从环境变量读，仓库里只有占位符。
- **离线可测**：内置 mock MCP（测试替身），77 条 pytest 全离线运行，CI 无需任何密钥。

## 🏗️ 架构

```
mcd_strategist/
├── mcp_client.py   # Streamable HTTP MCP 客户端（握手/会话/SSE/重试/限流）
├── models.py       # Meal / Coupon / PriceResult / Nutrition 宽松解析
├── savings.py      # 省钱雷达：候选生成 → 券优选 → 比价 → 排名
├── ordering.py     # 一句话点餐：消歧解析 → 选店 → dry-run → 确认下单
├── nutrition.py    # 营养把关：阈值评估 + 替换建议
├── briefing.py     # 麦门日报：活动/券/积分/抽奖
├── mockserver.py   # 离线模拟 MCP（33 个 Tool，测试与 demo 用）
└── cli.py          # mcd 命令行
```

## 🔌 麦当劳 MCP 使用情况

本项目真实调用麦当劳 MCP（`https://mcp.mcd.cn`）的工具共 **15 个**，覆盖点餐全链路。每个工具的用途、参数与调用时机详见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。

| 场景 | 使用的 Tool |
|---|---|
| 雷达比价 | `query-meals` `available-coupons` `query-store-coupons` `calculate-price` `list-nutrition-foods` |
| 一键领券 | `auto-bind-coupons` |
| 一句话点餐 | `query-meals` `query-nearby-stores` `calculate-price` `create-order` `cancel-order` |
| 麦门日报 | `now-time-info` `campaign-calendar` `available-coupons` `query-my-account` `query-lottery-info` |
| 积分抽奖 | `query-lottery-info` `draw-lottery` `query-my-prizes` |

## 🛡️ 安全与免责

- 领券和下单都会改变你的麦当劳账户状态，因此分别要求 `--bind` / `--confirm` 显式开启。
- 仓库内不含任何真实 Token；`mcp-config.example.json` 只使用环境变量占位符 `${MCD_MCP_TOKEN}`。
- demo / mock 数据为演示用虚构数据，与麦当劳真实菜单、价格无关。
- 营养阈值参考《中国居民膳食指南》常用建议值，仅供参考，不构成医疗建议。
- 本项目为参赛作品，非麦当劳官方产品；麦当劳及相关商标归其权利人所有。

## 🧪 开发与测试

```bash
pip install -e ".[dev]"
pytest -q                 # 77 passed，全离线
```

测试覆盖：MCP 客户端协议（握手/会话/SSE/401/429/5xx 重试/结果归一化）、雷达比价与限流、点餐消歧与 dry-run 边界、营养阈值、CLI 全子命令。

## 🏆 关于本仓库

本仓库是 **麦当劳程序员创意开发大赛**（2026-10-09 ~ 2026-10-25）参赛作品，由 [chenxinghang-a](https://github.com/chenxinghang-a) 使用腾讯 WorkBuddy 开发。参赛声明见 [CONTEST_DECLARATION.md](CONTEST_DECLARATION.md)，MCP 集成说明见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)，开发过程对话见 [workbuddy.md](workbuddy.md)。

## 📄 License

[MIT](LICENSE)
