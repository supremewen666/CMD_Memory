# Probe Case 构建准则（全标签覆盖）

本准则定义 CMD-Audit probe case 的构建标准，覆盖项目全部诊断出口：**5 个
pipeline 动作**（Tier 3 MCTS）、**5 个 item 标签**（Tier 2 item gate）、以及
**Fill 分支**（不打标签的 formation 失败）。所有字段约束、判定阈值、反事实机制
均来自源码核验，标注了出处。

> **评分语义**：paper-grade 判定走 G-Eval 连续分——`GoldEvidence.text` 经
> `_continuous_verify`（`scoring/llm.py:720`）读 rubric digit 的 logprob 期望，
> 得 `E[score] ∈ [0,4]`。`required_phrases` 是**无 logprob 端点时的确定性
> 0/1 fallback**（`scoring/phrase.py:39`），仅用于 mechanics-validation 快照，
> **不是** paper 路径。构建 evidence 时优先写可被 SVO 判断的自然语言原子命题，
> 而非堆砌 `required_phrases`。

---

## 0. 通用契约（所有 case 必须满足）

加载器 `ProbeCase.from_mapping` → `validate()`（`core/models.py:191,*`）强制：

| 约束 | 来源 |
|---|---|
| `case_id` / `query` / `gold_answer` 非空字符串 | `_required_str` |
| `raw_events` 非空 | `validate():` |
| `extracted_memory` 非空 | 同上 |
| `gold_evidence` 非空 | 同上 |
| `baseline_outputs` 非空 | 同上 |
| 每个 `GoldEvidence.source_memory_id` 必须指向存在的 `MemoryItem.memory_id` | 同上 |
| 每个 `GoldEvidence.source_event_id`（若设）必须指向存在的 `RawEvent.event_id` | 同上 |

`perturbation_label` 的取值经 `_optional_label_v0`（`core/models.py`）规整：

- `write_error` / `compression_error` / `premature_extraction_error` /
  `ingestion_error` / `reasoning_error` → **吸收为 `None`**（Fill 分支或经
  MCTS back-prop 浮现，**不作为 gold 标签**）
- `route_error` → **重写为 `retrieval_error`**（route 并入 retrieval）
- 仅 5 pipeline 动作 + 5 item 标签是合法 live 标签，其余报 `ProbeCaseError`

**baseline 必须真失败**：`BaselineOutput.answer_score = 0.0`，
`injected_context` = baseline 实际拿到的（通常是有缺陷的）上下文。否则没有可
归因的失败。

**合格判据（每个 gold case 都要过）**：用对应路径评估，baseline 节点连续分
≈0，单点反事实干预后 `credit ≥ 0.8`（MCTS 早停阈，`mcts/search.py:28`）或
散度过阈（item gate，`0.5`）。归因唯一——其余 9 个出口都不该被误触发。

---

## 1. Pipeline 动作（Tier 3 MCTS，5 个）

判定原语：`credit(h) = Qmax(prefix + h:best) − Qmax(prefix + h:identity)`，
`primary_label = argmax credit`。value `V_scalar = (k/N)·(E[answer]/4)`，
`k = #{atom: rubric_B ≥ 2.0}`（`mcts/value.py:107-118`）。反事实 transform 在
`mcts/actions.py:91`。

### 1.1 `retrieval_error`（恒合法）

- **机制**：该检索的没检索对。反事实 `_repair_retrieval_context`
  （`actions.py:138`）把 best 候选拼回 context。
- **构建**：gold evidence 的 source item **在 store 里但未进 baseline 的
  `injected_context`** / `retrieved_memory_ids`。baseline 召回错 item 或漏召回。
- **边界**：证据确实存在于 `extracted_memory`（否则是 Fill）；内容正确（否则是
  item_*）；非粒度/图/安全所致。

### 1.2 `injection_error`（恒合法）

- **机制**：注入格式/顺序错，或上下文管理把已注入证据挤出窗口。反事实
  `_repair_injection_context`（`actions.py:155`）归一化为显式有序 memory block。
- **构建**：gold item 已被召回（在 `retrieved_memory_ids` 内），但
  `injected_context` 里顺序错乱/被无关内容淹没/被截断，导致 baseline 用不上。
  反事实把召回集规整重排后恢复。
- **边界**：item 召回成功且内容正确，区别于 retrieval（没召回）和 item_*（内容错）。

### 1.3 `granularity_error`（恒合法）⭐ 参考实例

- **机制**：粒度遮蔽证据。证据存在于细粒度层，但 baseline 处在粗层被过滤。
  反事实 `_repair_granularity_context`（`actions.py:170`）拼回细粒度 text。
- **载体字段**：
  - `current_granularity`：baseline 所处层（粗，如 `"session"`）
  - `GoldEvidence.granularity_level`：证据可召回层（细，如 `"event"`），**≠**
    current
  - 细层 source item 的 `text` 含完整 gold 事实
  - 粗层 item 的 `text` 把多事件压成摘要、抹掉决定性属性
- **判据**：baseline(current 层)连续分 < 2.0（k=0）；反事实(细层)≥ 2.0（k=1）；
  `is_graph_expanded=false` 且 `passed_safety_filter=false`（排除门控动作）。
- **已验证**：参考实例 §6，V_scalar `0.000 → 0.969`，credit `+0.969`。

### 1.4 `graph_error`（门控 `is_graph_expanded`）

- **机制**：图扩展引入干扰，遮蔽正确证据。反事实 `_repair_graph_context`
  （`actions.py`）滤掉 `is_graph_expanded=True` 的 item，保留直连证据。
- **门控**：`get_legal_actions`（`actions.py:74`）只在召回集**存在
  `is_graph_expanded=True` 的 item** 时才把 graph_error 加入动作空间。
- **构建**：召回集含一个 `is_graph_expanded=true` 的**干扰** item（无关/误导），
  和一个 `is_graph_expanded=false` 的 gold source item。baseline 被图扩展干扰项带偏。
  gold evidence 指向那个 `false` 的直连 item（`_recover_without_graph_expansion`
  `interventions.py:568` 只收非图扩展 item 的证据）。
- **边界**：缺了 `is_graph_expanded=true` 的 item，graph_error 根本不进动作空间。

### 1.5 `safety_error`（门控 `passed_safety_filter`）

- **机制**：安全层误杀证据。反事实 `_repair_safety_context`（`actions.py`）恢复
  被过滤的安全候选。
- **门控**：只在召回集存在 `passed_safety_filter=True` 的 item 时才合法
  （`actions.py:81`）。
- **构建**：gold source item 标 `passed_safety_filter=true`（本应通过却被
  baseline 的过滤逻辑误拦），`safety_filter_blocked=true`（ProbeCase 级，
  `models.py:189`）。baseline 因安全过滤丢了关键证据，反事实恢复后答对。
- **边界**：缺 `passed_safety_filter=true` 的 item，safety_error 不进动作空间。

---

## 2. Item 标签（Tier 2 item gate，5 个，d=1 无树）

判定原语：reference-contrast 有向蕴含散度，经 `compute_directed_divergence`
（`item_gate/divergence.py:53`）复用 `_continuous_verify`，**非 KL**。散度阈
`0.5`。item gate 在 Tier 3 之前独立触发，item-wrong → 跳过 Tier 3。

### 2.1 `item_stale`（② 召回集对撞 + 时间方向）

- **机制**：库内兄弟矛盾 + 一方更新（`collision.py:51`）。
- **载体字段**：`MemoryItem.store` **当 ISO 时间戳占位符**
  （`collision.py:162`，`_analyze_timestamp_direction`）。必须是
  `"2026-01-15T00:00:00Z"` 这种**带 `Z` 的 ISO 串**，否则判 `no_reliable_timestamp`。
- **构建**：召回集含两个同主题但矛盾的 item，`store` 分别为旧/新 ISO 时间戳，
  跨度 > `timestamp_tolerance_days`（默认 7 天）。散度大 + 一方明显更新 → stale。
- **gold evidence**：指向**新**的那条（旧条应被推翻）。

### 2.2 `item_conflict`（② 召回集对撞 + 同期）

- **机制**：库内兄弟矛盾 + 同期/无可靠时序（`collision.py:52`）。
- **构建**：同 stale，但两个 item 的 `store` 时间戳在 `tolerance_days` 内
  （`same_period`），或非 ISO 格式（`no_reliable_timestamp`）。散度大 +
  无方向 → conflict（需人/规则裁）。

### 2.3 `item_wrong`（③ LOO 重构 + 正向散度）

- **机制**：LOO 重构 `m̂_i = Reconstruct(store\{m_i}, query)`，**正向散度大**
  → 重构比原 item 更具体/正确（`loo.py:225,240`，`is_forward_dominant`）。
- **构建**：gold source item 的 `text` 含**事实错误**（与 query 真值矛盾），但
  store 里其余 item 足以重构出正确版本。② 召回集对撞 PASS（无库内矛盾兄弟），
  才落到 ③。
- **边界**：必须 ② 不触发（否则先判 stale/conflict）；store 里要有重构素材。

### 2.4 `item_compression_distorted`（③ LOO 重构 + 反向散度）

- **机制**：**反向散度大** → 原 item 比重构更压缩/失真（`loo.py:226,242`，
  `is_reverse_dominant`）。
- **构建**：gold source item 的 `text` 是**过度压缩**的版本（丢了关键限定/数值），
  重构能还原更完整的事实。原 item 蕴含重构（反向强）但重构不蕴含原（正向弱）。

### 2.5 `item_poisoned`（末端 HITL）

- **机制**：无源不可检（信息论地板，`DISCUSSION.md:302`）。
- **构建**：**不作为自动归因 gold**。这类 case 只能 HITL 标注，留作 item gate
  的 HITL 通道测试，不进自动 attribution 准确率分母。

---

## 3. Fill 分支（不打标签的 formation 失败）

- **触发**：hook confidence < `FILL_FIX_THRESHOLD=0.5`（`hook/constants.py:31`,
  `post_retrieve_hook.py`），证据缺失 → Fill → 先 generate + 异步补记忆，**无诊断
  无标签**。
- **对应原始标签**：`write_error` / `compression_error` /
  `premature_extraction_error` / `ingestion_error`（被 `_optional_label_v0`
  吸收为 `None`）。
- **构建**：gold evidence 的 source item **根本不在 `extracted_memory`**（写入/
  萃取阶段就丢了），或召回集全低分。`perturbation_label` 设为上述原始标签之一
  （加载器会归零），用于验证 Fill 分支正确路由、不产生错误的 pipeline 标签。
- **`reasoning_error`**：同样归零；推理faults 经 MCTS back-prop 体现为"任何干预
  都救不回 → 所有 credit ≈ 0"，不单列标签。

---

## 4. 标签判定路线图（连续分语义）

```
recall_set
  │
  ▼ HOOK 6因子 → confidence ∈ [0,1]   (+experience_bonus)
  │   < 0.5 ─────────────► FILL 分支 (formation 失败, 无标签)
  │   ≥ 0.5 → FIX 分支
  ▼
  TIER 2 Item Gate (散度阈 0.5, 复用 _continuous_verify)
  │  ② 召回集对撞:  散度大+一方新(store ISO) → item_stale
  │                 散度大+同期/无时序        → item_conflict
  │                 散度小                     → PASS ↓
  │  ③ LOO 重构:    正向散度大 → item_wrong
  │                 反向散度大 → item_compression_distorted
  │  末端 HITL:      边缘 + item_poisoned
  │  item WRONG ──► 跳过 Tier 3
  │  item CORRECT ─┐
  ▼               ▼
  TIER 3 Pipeline MCTS (credit≥0.8 早停, value 阈 τ·4=2.0)
  │  动作 = retrieval/injection/granularity(恒合法)
  │       + graph(门控 is_graph_expanded) + safety(门控 passed_safety_filter)
  │       + IDENTITY
  │  primary_label = argmax credit
```

---

## 5. 字段载体速查表

| 出口 | 关键字段 | 取值要求 | 源码 |
|---|---|---|---|
| retrieval_error | `retrieved_memory_ids` / `injected_context` | gold item 在 store 但未召回/未注入 | actions.py:138 |
| injection_error | `injected_context` | 召回了但顺序/格式坏 | actions.py:155 |
| granularity_error | `current_granularity` + `GoldEvidence.granularity_level` | current(粗) ≠ evidence层(细) | actions.py:170 |
| graph_error | `MemoryItem.is_graph_expanded` | 召回集含 `true` 干扰项 + `false` gold | actions.py:74 |
| safety_error | `passed_safety_filter` + `safety_filter_blocked` | gold item `passed=true`，被误拦 | actions.py:81 |
| item_stale | `MemoryItem.store`（ISO+Z） | 两兄弟矛盾，跨度>7天 | collision.py:162 |
| item_conflict | `MemoryItem.store`（ISO+Z） | 两兄弟矛盾，同期/无时序 | collision.py:184 |
| item_wrong | item `text` 含错 + store 可重构 | 正向散度大 | loo.py:240 |
| item_compression_distorted | item `text` 过度压缩 | 反向散度大 | loo.py:242 |
| item_poisoned | — | HITL only, 不自动归因 | DISCUSSION:302 |
| Fill (formation) | `extracted_memory` 不含 gold source | 写/萃取阶段丢失 | post_retrieve_hook.py |

---

## 6. 参考实例：granularity_error（已实跑验证）

取材真实 memoryarena raw case `bundled_shopping-0-0`（购物 agent 选蛋糕底，
gold = `B00TUDFEW2`）。连续分链路实测：session 层 atom 0.125（k=0,
V_scalar=0.000）→ event 层 atom 3.875（k=1, V_scalar=0.969），
credit `+0.969 ≥ 0.8` → granularity_error。

```json
{
  "case_id": "memoryarena-bundled_shopping-0-0-granularity",
  "query": "Select Cake Base. Goal: buy the highest-priced compatible option. Which product (ASIN) should be purchased for Product 1?",
  "gold_answer": "B00TUDFEW2",
  "perturbation_label": "granularity_error",
  "current_granularity": "session",
  "granularity_levels": ["raw", "event", "session", "persona", "procedure", "graph"],
  "raw_events": [
    {"event_id": "e1", "text": "Saw Duncan Hines Strawberry Supreme cake mix, 16.5 oz."},
    {"event_id": "e2", "text": "Saw Sweet 'N Low lemon cake mix, sugar-free."},
    {"event_id": "e3", "text": "Saw Simple Mills Almond Flour Baking Mix, gluten free vanilla, muffin-pan ready, ASIN B00TUDFEW2, $9.99, highest rated."}
  ],
  "extracted_memory": [
    {
      "memory_id": "m_session",
      "text": "Product 1 cake-base options: three baking mixes were shown (strawberry, lemon, and a vanilla almond-flour mix).",
      "store": "episodic",
      "source_event_ids": ["e1", "e2", "e3"],
      "is_graph_expanded": false,
      "passed_safety_filter": false
    },
    {
      "memory_id": "m_event_gold",
      "text": "Simple Mills Almond Flour Baking Mix, Gluten Free Vanilla Cake Mix, muffin-pan ready, ASIN B00TUDFEW2, $9.99 highest-rated compatible option",
      "store": "episodic",
      "source_event_ids": ["e3"],
      "is_graph_expanded": false,
      "passed_safety_filter": false
    }
  ],
  "gold_evidence": [
    {
      "evidence_id": "ev_asin",
      "text": "Simple Mills Almond Flour Gluten Free cake mix, ASIN B00TUDFEW2",
      "source_memory_id": "m_event_gold",
      "granularity_level": "event"
    }
  ],
  "baseline_outputs": [
    {
      "baseline_name": "primary",
      "answer": "I am not sure which cake mix to buy.",
      "retrieved_memory_ids": ["m_session"],
      "answer_score": 0.0,
      "evidence_score": 0.0,
      "injected_context": "Product 1 cake-base options: three baking mixes were shown (strawberry, lemon, and a vanilla almond-flour mix)."
    }
  ]
}
```

每个新 gold case 套用 §0 合格判据自检：baseline 节点连续分 ≈0、单点反事实
credit ≥ 0.8（pipeline）或散度过阈（item），且其余出口不被误触发。
