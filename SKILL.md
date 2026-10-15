---
name: mcd-strategist
description: 麦门军师：基于麦当劳 MCP 的点餐决策助手。当用户想省钱吃麦当劳、预算内搭配套餐、查询餐品营养/热量、查询或领取麦当劳优惠券与活动、查看积分与抽奖，或让 AI 帮忙点麦当劳（堂食/外带/外送）时使用。能力：省钱雷达（菜单+麦麦省券+门店券比价）、一句话点餐（默认 dry-run，确认后才 create-order）、营养把关（能量/钠/脂肪阈值+替换建议）、麦门日报（活动日历+可领券+积分+抽奖）。需要麦当劳 MCP Token（https://open.mcd.cn/mcp 申请）。
---

# 麦门军师 · mcd-strategist

把麦当劳 MCP（`https://mcp.mcd.cn`）的 13 个 Tool 组合成四个开箱即用的点餐决策能力。
所有价格/优惠金额都以 MCP 的 `calculate-price` 返回为准；订单创建与领券都需要用户显式确认。

## 前置条件

1. 用户已在 <https://open.mcd.cn/mcp> 用手机号申请到 MCP Token；
2. WorkBuddy 已配置麦当劳 MCP 连接器（自定义连接器 → 配置 MCP，内容见仓库
   `mcp-config.example.json`，把 `${MCD_MCP_TOKEN}` 替换为真实 Token 并启用）；
3. 本 Skill 依赖的 CLI 已安装：`pip install -e <本仓库路径>`。

未配置 Token 时，**不要**调用真实接口，改用内置 mock 演示：`mcd demo`。

## 工作流

### 1. 省钱雷达（最高频）

用户说"预算 N 怎么吃最划算 / 有什么券 / 怎么点便宜"时：

```bash
mcd radar --budget 30          # 预算内最优组合排行
mcd radar --budget 50 --bind   # 额外调用 auto-bind-coupons 一键领券（需用户先同意）
mcd radar --budget 30 --store-id <门店ID>   # 指定门店时额外查询 query-store-coupons
```

输出含：排名、券名、原价/优惠/实付、每元性价比（kcal/元）。
向用户转述时保留"券名 + 实付 + 超预算标记"三个关键信息。

### 2. 一句话点餐

用户说"来份巨无霸套餐 / 帮我点麦当劳"时：

```bash
mcd order "来一份巨无霸套餐加薯条"              # dry-run：只报价，不创建订单
mcd order "来一份巨无霸套餐" --confirm          # 用户明确确认后才真实 create-order
mcd order "巨无霸套餐" --confirm --delivery     # 外送（默认堂食取餐）
mcd cancel <订单号>                            # 取消订单
```

**铁律：没有用户明确说"下单/确认"，只允许 dry-run。** 报价后必须把实付金额复述给用户，
得到肯定答复后才加 `--confirm`。

### 3. 营养把关

用户问"热量高吗 / 会胖吗 / 哪个更健康"时：

```bash
mcd nutrition 巨无霸套餐
mcd nutrition 麦辣鸡腿堡套餐 薯条(大) 可口可乐(中)
```

输出能量/蛋白质/脂肪/碳水/钠，超过单餐参考值（650 kcal / 800 mg 钠 / 30 g 脂肪）会标 ⚠️，
并给同类更低卡的替换建议。声明：仅供参考，不构成医疗建议。

### 4. 麦门日报

用户说"最近有什么活动 / 我的积分 / 能不能抽奖"时：

```bash
mcd brief
```

汇总 `campaign-calendar`、`available-coupons`、`query-my-account`、`query-lottery-info`。

## 边界与故障

| 情况 | 处理 |
|---|---|
| 401 | Token 无效/过期 → 引导用户去 open.mcd.cn/mcp 重新激活，命令输出已含此提示 |
| 429 | 客户端已内置退避重试；仍失败就让用户稍后再试 |
| 预算内无方案 | 会明确告知"预算内没有可行组合"，此时**不要**擅自提高预算，询问用户 |
| 解析不到餐品 | 用更明确的说法重试（参考菜单原名），不要瞎猜餐品编码 |
| 没有 Token | 一律 `mcd demo` 离线演示，禁止伪造真实价格回答用户 |

## 参考

- 仓库 README / MCP_INTEGRATION.md：完整设计与调用链
- 麦当劳 MCP 官方指南：https://github.com/M-China/mcd-mcp-server
- 大赛规则：https://github.com/M-China-Official/mcd-developer-innovation-challenge
