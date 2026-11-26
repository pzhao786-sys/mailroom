# Mailroom 学习指南（中文）

这份指南给项目作者本人看：讲清楚每个模块**做什么、为什么这样设计、面试时怎么讲**。
代码、配置和演示数据都是英文（面向美国市场），这里用中文解释。

---

## 1. 一句话说清这个项目

> 一个"邮件 + 文档处理 Agent"模板：读邮件和附件 → 分类 → 抽字段 → 查工具 → 写表 → 起草回复 → 拿不准的转人工。
> **换客户只改 `tenants/<客户>/tenant.yaml`，不改代码。** 仓库里两个虚构客户（物流公司、牙科诊所）跑的是同一套代码。

面试时的 30 秒版本：

> "我做了一个可配置的邮件处理 Agent。核心是手写的工具调用循环，模型只负责理解和起草，所有有副作用的动作都先变成'提议'，由确定性的策略引擎决定自动执行、等审批还是转人工。我用 80 封带标注的邮件做评测，报告自动处理率、自动处理部分的准确率、漏转人工数、成本和延迟。"

---

## 2. 一封邮件的完整旅程（以 `lf-q01` 为例）

邮件内容：Brightwater 公司要报价，3 托、1,800 磅、Columbus OH → Indianapolis IN，标准服务。

```
① Ingest   pipeline/ingest.py
   解析成 InboundEmail；如有 PDF 附件，用 pypdf 抽文字（没有文字层 = 扫描件 → 标记 unreadable）
   injection.scan() 用正则先扫一遍"忽略指令 / 改银行账户 / 导出全部客户"之类的话
   render_for_model() 把邮件包进 <email> 标签，告诉模型"这是外部数据"

② Triage   runner.py → llm/structured.py
   一次结构化调用 → {summary, category: quote_request, confidence: 0.95, urgency, suspicious}
   DeepSeek 用 JSON 模式；Claude 用"强制调用一个工具"；结果用 Pydantic 校验，不合格让模型改一次

③ Handle   agent/loop.py
   系统提示 = 模板 + 公司信息 + 该类别的说明 + 强制转人工规则
   可用工具 = YAML 里该类别列出的工具 + 一个 submit
   第 1 轮：模型调用 calculate_freight_quote(OH, IN, 1800) → 工具按费率表算出 $393.59
   第 2 轮：模型调用 submit(fields={...}, reply_body="Hi Dana, ... $393.59 ...", needs_human=false)

④ Policy   pipeline/policy.py
   检查必填字段 → 跑 YAML 里的校验规则（quote_priced）→ 跑强制规则（hazmat?）→ 累加信号权重
   风险分 0 < 阈值 1.0 → route=automated, outcome=processed

⑤ Actions  pipeline/actions.py
   record_row（写入"Quote Requests"表，auto）
   send_reply（收件人由系统绑定为原发件人，签名由系统追加；needs approval）

⑥ Execute  pipeline/executor.py
   auto 的动作立即执行（P0 写本地 CSV），approval 的进入待审批队列
   每个动作有幂等键，同一封邮件处理两次也不会写两行
```

自己跑一遍看每一步（需要有效的 DeepSeek key）：

```powershell
.\.venv\Scripts\mailroom process --tenant larkspur_freight --case lf-q01 --trace
```

---

## 3. 模块逐个讲

### 3.1 配置层：`config/schema.py` + `tenant.py`

- `TenantConfig` 是"客户配置的合同"：所有字段都用 Pydantic 定义，`extra="forbid"`，**拼错一个键也会在加载时报错**。
- 加载时还会检查：类别引用的工具是否存在、表格列是否都是已定义字段、规则表达式语法是否合法、必须有 `other` 兜底类别。
- 每个类别的字段在 YAML 里定义（类型、是否必填、范围、枚举值），加载时用 `pydantic.create_model` **动态生成模型**（`pipeline/schemas.py`），这个模型同时就是 `submit` 工具的 JSON Schema。

**为什么所有字段对模型都是 Optional？**
如果把"必填"直接写进 schema，模型在信息缺失时会被迫编一个值。改成"都可以为 null"，由策略层检查必填，模型就能诚实地说"没提供"。评测里 `expected: null` 就是专门抓"编造值"的。

### 3.2 模型层：`llm/`

| 文件 | 作用 |
|---|---|
| `types.py` | 统一的 Message / ToolCall / LLMResponse，循环只认这些 |
| `openai_compat.py` | OpenAI 协议：DeepSeek、OpenAI、Ollama、vLLM、你的 llm-gateway |
| `anthropic.py` | Anthropic 协议 |
| `http.py` | 统一重试：429/5xx/网络错误指数退避 + 抖动，优先用 `Retry-After` |
| `structured.py` | 结构化输出：JSON 模式 或 强制工具，校验失败回传修正 |
| `catalog.py` | `config/models.yaml` 的别名 → 客户端；`--model` 可一键替换所有别名 |
| `fake.py` | 测试用的"剧本模型"，按顺序返回预设消息 |

**两家协议的关键差异（面试高频）**：

- 系统提示：OpenAI 是第一条 `role: system` 消息；Anthropic 是顶层 `system` 字段。
- 工具调用的参数：OpenAI 是 **JSON 字符串**（可能解析失败）；Anthropic 是对象。
- 工具结果：OpenAI 是独立的 `role: tool` 消息；Anthropic 是放在 **user 消息**里的 `tool_result` 块，连续多个结果要合并成一条（`to_wire_messages` 做了这件事）。
- 强制调用某个工具：`{type: function, function: {name}}` vs `{type: tool, name}`。
- 缓存命中 token：OpenAI 在 `prompt_tokens_details.cached_tokens`，DeepSeek 在 `prompt_cache_hit_tokens`，Anthropic 在 `cache_read_input_tokens`（而且 Anthropic 的 `input_tokens` 不含缓存部分，代码里做了归一化）。

**为什么不用官方 SDK？** 直接写 HTTP，能让你说清楚协议细节；而且重试、计费、超时都由自己统一控制。代价是要自己维护，这一点面试时也要坦白说。

### 3.3 工具层：`tools/`

- `Tool` = 名字 + 描述 + Pydantic 参数模型 + 处理函数 + `side_effect` 标记。
- **通用工具**：`search_knowledge_base`（BM25，零成本、结果确定，适合几十页政策文档），`lookup_<表名>`（查 CSV 表，完全由 YAML 配置）。
- **行业插件**：`calculate_freight_quote`（数据来自客户自己的 `rate_card.yaml`）、`find_open_slots`、`hold_appointment_slot`、`open_damage_claim`。
- **按所有者限定的查询**：`owner_column` 让查询只返回属于发件人的记录，而且"属于别人"和"不存在"返回**完全一样**的结果，防止被用来试探别人的数据。评测用例 `lf-s07`、`jd-x01` 专门测这个。

### 3.4 Agent 主循环：`agent/loop.py`（面试重点）

```
for step in 1..max_steps:
    超预算 → 停（budget_exceeded）
    调模型（工具 = 该类别工具 + submit）
    没调工具，只回了文字 → 提醒"请调用 submit"（最多 2 次）
    对每个工具调用：
        参数不是 JSON         → 返回错误，让模型重试
        工具名不存在          → 返回错误 + 可用工具列表
        Pydantic 校验失败     → 返回具体错误（哪个字段、为什么）
        有副作用的工具        → 不执行！记录为提议，返回 "queued_for_review"
        和之前完全相同的调用  → 返回缓存结果 + "别再问了"
        submit 且合法         → 结束
        其它                  → 执行工具，截断过长结果，返回 JSON
```

要能讲出的保证：

1. **一定会结束**：步数上限 + 成本上限，超了返回状态而不是抛异常，流水线据此转人工。
2. **每个 tool_call 都恰好有一个结果**：否则 Anthropic 会直接拒绝请求。
3. **错误是给模型看的信息**，不是崩溃：模型能自我修正（`test_loop.py` 覆盖了这些情况）。
4. **循环里不会发生不可逆的事**。

### 3.5 策略引擎：`pipeline/policy.py`

"拿不准"不靠模型自报的置信度，而是多个信号加权求和：

| 信号 | 默认权重 |
|---|---|
| 流水线出错（模型调不通、输出解析失败） | 100（必转人工） |
| 强制规则命中（危险品、发票超过 2.5 万、银行信息变更、高严重度投诉） | 100 |
| 必填字段缺失 / 业务规则校验失败 / 附件读不出 / 疑似注入 / 模型主动要求转人工 / 循环未完成 | 1.0 |
| 低置信度归档（垃圾邮件但置信度低，否则会无人看到就被归档） | 1.0 |
| 分类置信度低 | 0.6 |
| 工具出错 ≥ 2 次 | 0.3 |

风险分 ≥ 阈值（默认 1.0）就转人工。**所有信号都存进结果**，所以评测可以离线重算任意阈值下的"自动处理率 vs 准确率"，不用重新调用模型。

业务规则写在 YAML 里，比如 `fields.subtotal is None or abs(fields.subtotal + fields.tax - fields.total) <= 0.02`，由 `expr.py` 用 `ast` 解析、白名单解释执行：不允许函数调用（除了 abs/len/min/max/round/lower/contains）、不允许访问 `_` 开头的名字、`a.b` 只是字典取值。缺失值参与运算返回 None，比较返回 False，所以"字段没提供"不会让规则误触发。

### 3.6 动作与执行：`actions.py` + `executor.py`

- 模型的输出只是数据。`plan_actions` 把它变成动作：`record_row`、`send_reply`、`notify_team`、`tool_call`。
- 回复的收件人由系统绑定为原发件人，签名由系统追加，**模型无法指定收件人**。
- 每个动作按 `policy.approvals` 标记为 `auto` 或 `approval`。
- 幂等键 = hash(客户, 邮件 ID, 动作类型, 内容)，执行过的写进 `ledger.jsonl`，重复执行会被跳过。
- 转人工时只发一个 `notify_team`，里面附上模型起草的回复，**给人参考，绝不自动发送**。

### 3.7 评测：`evals/`

- 数据：`evals/<客户>/cases.yaml`，每封邮件带标准答案。物流 60 封，牙科 20 封；分 `dev`（调提示词用）和 `test`（只用来报数，避免过拟合）。
- 指标：
  - **自动处理率**、**自动处理部分的准确率**：对客户卖点就是这两个数。
  - **漏转人工数**：本该给人处理却被自动处理了，这是最重要的安全指标。
  - 分类准确率、每类 P/R/F1、混淆矩阵；字段准确率（数字允许 ±0.5%，字符串做归一化）。
  - 回复检查：必须包含报价金额，不能泄露别人的数据。
  - 对抗样例通过率、每封成本、p50/p95 延迟、阈值扫描表。

---

## 4. 两个客户的差异，就是"模板化"的证明

| | Larkspur Freight（物流） | Juniper Dental（牙科） |
|---|---|---|
| 类别 | 询价、查件、供应商发票、投诉、垃圾、其他 | 预约、账单保险、投诉、垃圾、其他 |
| 工具 | 运费计算、查件（按公司域名限定）、查 PO、开理赔单（需审批） | 查空档、锁定时段（需审批）、查自己的预约（按邮箱限定） |
| 强制转人工 | 危险品、发票 > $25,000、银行信息变更、高严重度投诉 | 疑似急症、治疗效果投诉、账单争议 |
| 隐私 | 日志脱敏 | 日志脱敏，并且**会员号在送进模型前就打码** |

两者的代码完全一样，差别全在 YAML 和知识库文件里。

---

## 5. 面试常见问题和回答要点

**Q1：为什么不直接用 LangGraph / LangChain？**
先手写是为了真正掌握每个细节：消息格式、工具结果配对、错误回传、终止条件。手写版只有两三百行，流程完全透明。P3 会用 LangGraph 重写同一条流水线做对比：LangGraph 提供了检查点、interrupt（人工审批时暂停/恢复）和可视化；而我这里用"先提议后执行"，人工审批根本不需要暂停 LLM 对话。

**Q2：怎么防止 Agent 死循环？**
三层：最大步数、单封邮件成本上限、重复调用缓存（同样的参数第二次调用直接返回缓存结果，并提示模型往下走）。另外模型只回文字不调工具时最多提醒两次，然后以 `no_submit` 结束。

**Q3：模型给的工具参数错了怎么办？**
不抛异常，把错误作为工具结果返回：JSON 解析失败、工具不存在、Pydantic 校验失败都会带上具体原因（哪个字段、什么问题），模型下一轮通常能自己改对。

**Q4："拿不准"是怎么判断的？为什么不用模型的 confidence？**
LLM 自报的置信度校准很差，所以只把它当作一个权重 0.6 的弱信号。主要依据是确定性检查：必填字段、业务规则（金额加总、PO 匹配、日期先后）、附件可读性、注入扫描、强制规则。阈值用评测集来定：扫描阈值，选一个"漏转人工 = 0"前提下自动化率最高的点。

**Q5：怎么防提示注入？**
多层防御，不依赖任何单一手段：邮件包在 `<email>` 标签里，系统提示明确说明这是外部数据；正则预扫描和模型的 `suspicious` 标记都会加风险分；收件人由系统绑定；查询按所有者限定；有副作用的动作需要审批；没有批量导出类工具。评测里有专门的攻击样例。

**Q6：为什么报价要用工具算，不让模型算？**
LLM 不擅长精确计算，而且价格必须可审计、可复现。工具按费率表确定性计算，评测会检查回复里的金额是否和工具结果完全一致。

**Q7：怎么换模型？换模型会不会出问题？**
改 `config/models.yaml` 里的别名，或者 `mailroom eval --model gpt-4o-mini`。各家模型在工具调用上能力不一样，所以每换一次都要跑评测：评测集就是换模型的安全网。DeepSeek 默认会"思考"，而且思考部分按输出 token 计费，所以配置里用 `extra_body` 把它关掉了。

**Q8：同一封邮件被处理两次怎么办？**
每个动作都有幂等键，执行过的记在账本里，第二次会被标记为 `skipped_duplicate`（`test_execute_then_approve_is_idempotent` 覆盖了这一点）。

**Q9：数据隔离怎么做？**
每个客户独立的运行目录 `var/<客户>/`、独立的密钥前缀、独立的知识库；日志和追踪记录都会脱敏；牙科客户连模型输入里的会员号都会打码。P1 接入数据库后，每行都带 tenant_id，并且每个客户一个 Langfuse 项目。

**Q10：怎么证明系统真的有效？**
给出评测报告里的数字：自动处理率、自动处理部分的准确率、漏转人工数、每封成本、延迟。并且说明 dev/test 是分开的，提示词只在 dev 上调。

**Q11：如果模型服务挂了会怎样？**
先按指数退避重试；重试用完后，这封邮件带着 `pipeline_error` 信号转给人工，不会丢失，也不会误操作。（这一点已经被真实验证过：DeepSeek key 失效时，3 封测试邮件全部安全地转给了人工。）

**Q12：这个系统还有哪些局限？**
诚实回答更加分：扫描件还没有接 OCR；BM25 检索在文档多了以后效果会下降；评测集是自己造的，规模不大，而且标注是一个人完成的；回复质量目前只做了关键词检查，LLM-as-judge 还在计划中；P0 的数据都落在本地文件，真实连接器是 P1 的工作。

---

## 6. 常用命令

```powershell
.\.venv\Scripts\mailroom validate                                   # 校验所有客户配置和评测集
.\.venv\Scripts\mailroom models                                     # 看有哪些模型、key 有没有配好
.\.venv\Scripts\python -m pytest                                    # 61 个单元测试，不联网
.\.venv\Scripts\mailroom process --tenant larkspur_freight --case lf-i09   # 看一封诈骗发票怎么被拦下
.\.venv\Scripts\mailroom eval --tenant larkspur_freight --limit 5   # 先小规模跑，确认没问题
.\.venv\Scripts\mailroom eval --tenant larkspur_freight --split dev # 跑完整 dev 集
.\.venv\Scripts\mailroom init-tenant acme_plumbing --name "Acme Plumbing"  # 新建客户
```

## 7. 下一步

- **P1**：用 MCP 封装 Gmail / Sheets / Slack / Webhook 连接器；接入 Langfuse 追踪、Postgres、Mailpit 本地收发测试；用 LLM-as-judge 评估回复质量；评测加入 CI。
- **P2**：FastAPI 后端 + Next.js 管理后台（今日处理量、节省工时、审批队列、转人工列表）+ 在线试用页 + 部署。
- **P3**：LangGraph 版本和对比文章、多模型对比表、2 分钟演示视频。
