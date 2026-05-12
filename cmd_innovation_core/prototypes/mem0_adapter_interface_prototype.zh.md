# 原型简报：mem0 Adapter 接口

## 分支

逻辑原型。问题在于：CMD-Audit 能否以最小表面积拦截 mem0 的 `add()` 和 `search()` 操作，同时保持与 standalone harness 一致的归因准确率。

## 来源

- mem0ai/mem0（55,320 GitHub stars，YC S24）：通用记忆层，v3 算法（2026 年 4 月）使用单次 ADD-only 提取 + 多信号检索（语义 + BM25 + 实体匹配）。
- CMD open_decisions Decision 14：选择 mem0 作为第一个 CMD-Skill Adapter 目标。
- V0 Cycle 9（Adapter Boundary）：standalone harness 已预留 adapter 接口合约。本原型将其从抽象边界扩展到具体的 mem0 拦截。

## 一次性契约

- 此原型从第一天起即为一次性。
- 所有状态保留在内存中。
- 每次操作后展示完整状态。
- 拦截合约验证后应删除或吸收。
- 不涉及生产数据、持久化、精美 UI 或宽泛的错误处理。
- 不需要运行中的 mem0 实例；拦截基于录制的 mem0 操作轨迹进行模拟。

## 问题

当 CMD-Audit 对 mem0 支持的 agent 运行反事实 replay 时，adapter 能否在无需了解 mem0 内部实现的前提下，在明确定义的切点拦截 `add()` 和 `search()`，并且归因准确率是否匹配 standalone harness？

## Adapter 切点

mem0 的 v3 流水线有两个天然的拦截面：

```text
mem0 流水线：
  原始对话/事件
    -> [切点 A: add()]      ← 在此拦截写侧 replay
    -> 记忆存储（facts）
    -> [切点 B: search()]   ← 在此拦截检索侧 replay
    -> 检索到的 Facts
    -> Agent 上下文
    -> Agent 答案
```

### 切点 A：`add()` 拦截

| Replay | 拦截行为 |
|--------|---------|
| Oracle Write | 将 `add()` 的输入替换为应写入的 gold evidence facts |
| Oracle Compression | 将 `add()` 的输入替换为未压缩/完整版的 facts |
| Verbatim Event Oracle | 完全绕过 `add()`；直接将原始事件作为上下文输入 |
| Injection-Oracle | 将 `add()` 的输入替换为格式正确的 evidence block |

### 切点 B：`search()` 拦截

| Replay | 拦截行为 |
|--------|---------|
| Oracle Retrieval | 将 `search()` 的结果直接替换为 gold evidence facts |
| Evidence-Given Reasoning | 保持 `search()` 结果不变；在检索后将 gold evidence 追加到 agent 上下文 |

### 不拦截（直通）

- Entity linking 正常运行（不拦截——CMD 不修改 mem0 的实体图）。
- 多信号融合正常运行（不拦截——CMD 评估检索结果，而非检索内部机制）。

## 场景卡片

### 卡片 A：mem0 上的 Write Error

- 探针案例的 gold evidence 从未传递给 `add()`。
- Baseline：`search()` 返回错误/干扰 facts。答案错误。
- 拦截：Oracle Write 将 `add()` 输入替换为 gold evidence facts。`search()` 正常运行。
- 预期：`search()` 现在返回正确 facts。答案恢复。
- 归因：`write_error` → V1 中，若证据从未到达 `add()`，则为 `ingestion_error`。
- 需展示状态：原始 `add()` 输入、oracle `add()` 输入、拦截前后 `search()` 结果、answer scores。

### 卡片 B：mem0 上的 Retrieval Error

- 探针案例的 gold evidence 已通过 `add()` 正确存储。
- Baseline：`search()` 未能返回（BM25 未命中、语义不匹配）。
- 拦截：Oracle Retrieval 将 `search()` 结果替换为 gold evidence facts。
- 预期：上下文中出现正确 facts。答案恢复。
- 归因：`retrieval_error`。
- 需展示状态：已存储 facts、`search()` query、原始 `search()` 结果、oracle `search()` 结果、answer scores。

### 卡片 C：mem0 上的过早抽取

- 原始对话包含 gold evidence（如"用户对青霉素过敏"）。
- `add()` 提取了丢失细节的 fact（"用户有医疗状况"）。
- Baseline：`search()` 返回丢失细节的 fact。答案错误。
- 拦截：Verbatim Event Oracle 完全绕过 `add()`/`search()`；直接输入原始对话。
- 预期：原始对话提供具体证据。答案恢复。
- 归因：`premature_extraction_error`。
- 需展示状态：原始对话文本、`add()` 提取的 facts、绕过标记。

### 卡片 D：mem0 上的注入错误

- Gold evidence 已正确存储且 `search()` 正确返回。
- 但 mem0 以混乱格式注入 agent 上下文（顺序错误、缺少边界标记）。
- 拦截：Injection-Oracle 将上下文注入块替换为格式清晰的 evidence。
- 预期：相同 facts，更清晰的呈现。答案恢复。
- 归因：`injection_error`。
- 需展示状态：原始上下文注入块、oracle 注入块、answer scores。

### 卡片 E：归因标签与 Standalone 一致

- 同一探针案例分别通过 (a) standalone CMD-Audit harness 和 (b) mem0 adapter 路径运行。
- 预期：归因标签一致。6-label smoke suite 上的 macro F1 不变。
- 需展示状态：standalone 标签、adapter 标签、匹配标记、恢复增益的任何差异。

### 卡片 F：Adapter 不修改 mem0 状态

- 所有 replay 在沙箱中运行：拦截的 `add()` 和 `search()` 产生临时内存变体。
- 原始 mem0 存储永不写入。
- 预期：replay 后 mem0 存储状态与 replay 前完全一致。
- 需展示状态：存储校验和（前/后）。

## 需展示的状态

mem0 adapter replay 后展示：

- `case_id`
- `perturbation_label`
- `interception_points_used`（使用了哪些切点）
- `add_input_original` / `add_input_oracle`
- `search_results_original` / `search_results_oracle`
- `replay_scores`（每个 replay：answer_score、evidence_score、recovery_gain）
- `predicted_label`（adapter 路径）
- `predicted_label`（standalone 路径，用于对比）
- `label_match`（bool）
- `store_mutated`（必须为 false）

## Adapter 接口合约

```text
Mem0Adapter:
  # 拦截
  intercept_add(case_id: str, original_facts: list[str], replay: ReplayName) -> list[str]
  intercept_search(case_id: str, original_query: str, original_results: list[MemoryItem], replay: ReplayName) -> list[MemoryItem]

  # 只读访问
  get_store_snapshot() -> StoreChecksum

  # 无写入方法——所有变更是内存中的沙箱变体

ReplayName:
  "oracle_write"
  | "oracle_compression"
  | "verbatim_event_oracle"
  | "oracle_retrieval"
  | "injection_oracle"
  | "evidence_given_reasoning"
```

## 与 Standalone Harness 的关系

```text
Standalone Harness (V0):
  ProbeCase -> ReplayEngine -> Attribution -> ECS

mem0 Adapter (V1):
  ProbeCase -> Mem0Adapter(intercept_add, intercept_search) -> ReplayEngine -> Attribution -> ECS
                   |
            mem0 Store（只读，沙箱化）
```

ReplayEngine、Attribution 和 ECS 层不变。仅输入来源变化：从 fixture 控制的记忆操作变为拦截的 mem0 操作。

## 判定占位

两个切点拦截（add + search）是否覆盖了全部六个 V0 replay 而无需了解 mem0 内部实现？是否存在 mem0 特有的失败模式（实体链接错误、多信号融合 bug）是 adapter 无法拦截因而无法诊断的？
