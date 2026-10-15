# 麦当劳 MCP 集成说明

本文件说明「麦门军师」在何种场景下、以何种参数、按什么顺序调用麦当劳 MCP Server 的哪些 Tool，以及对应的业务价值。

## 1. MCP Server 信息

| 项 | 值 |
|---|---|
| 服务方 | 麦当劳中国（面向中国大陆地区，不含港澳台） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（POST，JSON-RPC 2.0） |
| 鉴权 | 请求头 `Authorization: Bearer <MCP Token>` |
| 会话 | `initialize` 握手后由响应头 `Mcp-Session-Id` 维持 |
| 响应格式 | `application/json` 或 `text/event-stream`（SSE）两种，客户端均兼容 |
| 限流 | 每个 Token 每分钟最多 600 次请求，超出返回 `429` |
| 常见错误 | `401` Token 无效/过期；`429` 限流；MCP 结果 `isError=true` 表示业务错误 |
| Token 申请 | <https://open.mcd.cn/mcp>（手机号验证登录 → 控制台 → 激活） |

客户端实现见 `src/mcd_strategist/mcp_client.py`：

- `initialize()` 握手后自动发送 `notifications/initialized`；
- `tools/list` 带缓存；`tools/call` 自动归一化返回（优先 `structuredContent`，否则解析 `content[0].text` 中的 JSON）；
- 429 尊重 `Retry-After` 并退避重试；5xx 指数退避；401 立即抛出 `UnauthorizedError`；
- 客户端令牌桶默认 8 QPS（官方 10 QPS 上限的 80%），防止把配额打满。

## 2. 使用的 Tool 清单（共 10 个）

| Tool | 用途 | 调用场景 | 关键入参 |
|---|---|---|---|
| `query-meals` | 获取当前门店可售餐品（分类、编码、价格） | 雷达、点餐的公共第一步 | `storeId`（可选） |
| `available-coupons` | 麦麦省可领取优惠券列表 | 雷达选券、日报 | — |
| `query-store-coupons` | 当前门店可用优惠券列表 | 雷达（传入 `--store-id` 时补充查询） | `storeId` |
| `auto-bind-coupons` | 一键领取全部可领券 | `mcd radar --bind` | — |
| `calculate-price` | 按餐品列表（可含券）计算金额 | 雷达比价的核心、点餐计价 | `meals: [{code, quantity}]`、`couponIds`、`storeId` |
| `list-nutrition-foods` | 餐品营养成分（能量/蛋白质/脂肪/碳水/钠/钙） | 雷达每元性价比、营养把关 | — |
| `query-nearby-stores` | 附近可用门店 | 点餐时自动选店 | — |
| `create-order` | 创建订单（堂食/外带/外送） | `mcd order --confirm`（唯一写订单的工具） | `meals`、`storeId`、`eatType` |
| `cancel-order` | 取消订单 | `mcd cancel <订单号>` | `orderId` |
| `campaign-calendar` | 当月营销活动日历 | 麦门日报 | — |
| `now-time-info` | 当前时间 | 麦门日报（让 LLM/用户对齐时间） | — |
| `query-my-account` | 积分账户（可用/累计/将过期） | 麦门日报 | — |
| `query-lottery-info` | 积分抽奖活动信息 | 麦门日报 | — |

> 注：麦当劳 MCP 目前共开放 33 个 Tool（完整清单可执行 `mcd --mock tools` 查看，mock 清单与官方公布一一对应）。上表 13 个即本项目真实调用的全部 Tool；其余 20 个（商城兑换、主题活动、抽奖执行等）作为能力的自然延伸保留在 mock 清单与文档中，欢迎在 Issues 里提名下一个要接入的。

## 3. 三条核心调用链

### 3.1 省钱雷达（`mcd radar --budget 30`）

```
query-meals ──────────────► 门店菜单（Meal 列表）
available-coupons ────────► 麦麦省可领券（Coupon 列表）
[--store-id] query-store-coupons ► 门店可用券（补充）
本地：generate_candidates ─► 48 个候选组合（套餐/主餐/+饮料/+小食）
循环：calculate-price(候选) ─► 原价
       按券面估算价值取 Top6 ─► calculate-price(候选, couponId) ─► 逐个试算
       取应付最低者
排名：预算内按应付升序（同价比热量），超预算方案垫底
输出：Top N + 券名 + 原价/优惠/实付 + 每元性价比
```

设计取舍：

- **候选生成用原价粗过滤**（预算 ×1.3 余量），避免对必然超预算的组合浪费 `calculate-price` 配额；
- **券用估算排序 + 截断试算**（每候选最多 6 张），理论上最多 48 × (1 次原价 + 6 次试券) = 336 次 `calculate-price`；雷达另有菜单/券/营养查询，仍低于官方 600 次/分钟限制；mock 菜单实测约 77 次 `calculate-price`；
- 相同 (餐品签名, 券集合) 的比价结果有缓存，不重复请求。

### 3.2 一句话点餐（`mcd order "来一份巨无霸套餐加薯条"`）

```
query-meals ──────────────► 菜单
本地 parse_text ──────────► 消歧解析（全名命中 > 别名；同名多规格取最低价）
query-nearby-stores ──────► 自动选店（或 --store-id 指定）
calculate-price ──────────► dry-run 报价
[--confirm] create-order ─► 返回订单号与支付链接
```

安全边界：`create-order` 只在显式 `--confirm` 后调用；未识别到餐品时拒绝下单。

### 3.3 麦门日报（`mcd brief`）

```
now-time-info ────────────► 时间锚点
campaign-calendar ────────► 本月活动（进行中/往期）
available-coupons ────────► 可领券 Top 8
query-my-account ─────────► 积分（可用/累计/将过期）
query-lottery-info ───────► 积分抽奖活动与奖品
```

每个板块独立容错：单个 Tool 失败只丢失该板块，不影响整份日报。

## 4. 错误处理策略

| 情况 | 行为 |
|---|---|
| 401 Token 无效 | 立即抛出，提示检查 `MCD_MCP_TOKEN` |
| 429 限流 | 读 `Retry-After`，退避后重试（默认最多 4 次） |
| 5xx / 网络错误 | 指数退避重试 |
| MCP `isError=true` | 抛 `ToolCallError`，原文透传给用户 |
| 某板块工具失败 | 日报/雷达降级继续，note 里说明 |
| 无 Token | CLI 提示并切换到内置 mock（`mcd demo`），绝不在无凭证情况下调用真实接口 |

## 5. 离线模拟 MCP

`src/mcd_strategist/mockserver.py` 实现了与 `McpClient` 同接口的 `MockMcpClient`：

- 工具清单与官方公布的 33 个 Tool 一一对应；
- `calculate-price` 实现了确定性的满减/折扣/单品券计费逻辑；
- 餐品、券、门店、活动、积分均为**虚构演示数据**，仅用于测试与 `mcd demo`，与麦当劳真实数据无关；
- 全部 77 条 pytest 用例依赖它，CI 不需要任何 Token。
