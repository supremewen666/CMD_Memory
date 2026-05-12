# 原型简报：修复后评估与 Monitor 合约

## 分支

逻辑原型。问题在于：三值修复评估状态机和枚举锁定 monitor 拒绝路径在实现之前是否感觉正确。

## 一次性契约

- 此原型从第一天起即为一次性。
- 所有状态保留在内存中。
- 每次操作后展示完整状态。
- 状态模型验证后应删除或吸收。
- 不涉及生产数据、持久化、精美 UI 或宽泛的错误处理。

## 场景 1：三值修复后评估

### 问题

当 Post-Repair Context Replay 运行且证据恢复（evidence_score = 1.0）但答案仍然失败（answer_score = 0.0）时，`partial` 评估状态机是否为研究者诊断耦合失败提供了足够信息？

### 状态转换

```
RepairedContextBuilt
  -> PostRepairRetested(post_repair_answer_score, post_repair_evidence_score)
      -> repair_assessment = classify(post_repair_answer_score, post_repair_evidence_score)

classify(answer_score, evidence_score):
  answer_score == 1.0                               -> "recovered"
  answer_score < 1.0 AND evidence_score == 1.0      -> "partial"
  answer_score < 1.0 AND evidence_score < 1.0       -> "failed"
```

### 场景卡片

#### 卡片 A：完全恢复
- 修复前：answer_score=0.0, evidence_score=0.0, label=retrieval_error
- 修复：修正检索路由，用正确记忆重建上下文
- 修复后：answer_score=1.0, evidence_score=1.0
- 预期评估：`recovered`
- 解读：单因失败，修复端到端起效。

#### 卡片 B：Partial — 暴露耦合失败
- 修复前：answer_score=0.0, evidence_score=0.0, label=retrieval_error
- 修复：修正检索路由，用正确记忆重建上下文
- 修复后：answer_score=0.0, evidence_score=1.0
- 预期评估：`partial`
- 解读：检索有问题，且模型无法基于证据推理。retrieval_error 掩盖了 reasoning_error。这是诊断深度的正面信号，而非修复失败。
- 后续操作：重新运行 Evidence-Given Reasoning replay 以确认 reasoning_error。

#### 卡片 C：Failed — 修复未命中根因
- 修复前：answer_score=0.0, evidence_score=0.0, label=retrieval_error
- 修复：修正检索路由，用正确记忆重建上下文
- 修复后：answer_score=0.0, evidence_score=0.0
- 预期评估：`failed`
- 解读：检索不是根因。修复针对了错误的操作。
- 后续操作：检查 top-2 标签，用不同 baseline 重新归因。

#### 卡片 D：Partial — 注入修复，推理仍失败
- 修复前：answer_score=0.0, evidence_score=0.0, label=injection_error
- 修复：修正注入格式，用干净证据块重建上下文
- 修复后：answer_score=0.0, evidence_score=1.0
- 预期评估：`partial`
- 解读：注入格式有问题，且基于干净证据的推理也失败。耦合的 injection_error + reasoning_error。

### 需展示的状态

Post-Repair Context Replay 后展示：
- `case_id`
- `perturbation_label`
- `predicted_label`
- `pre_repair_answer_score`
- `pre_repair_evidence_score`
- `post_repair_answer_score`
- `post_repair_evidence_score`
- `repair_assessment`
- `repair_action_taken`
- `regression_risk`（是否有其他指标退化？）
- `token_cost`

### 判定占位

三值分类是否使耦合失败的诊断路径变得显然？或者 `partial` 需要 `suspected_coupled_label` 提示？

---

## 场景 2：Monitor 枚举锁定合约拒绝

### 问题

当 Subagent Judge Monitor 尝试发出自由文本 `anomaly_reason` 或含内容的 evidence pointer 时，拒绝路径是否为调用者提供了足够信息以理解输出为何被拒？

### 允许的输出 Schema

```text
MonitorOutput:
  trigger_replay: bool
  anomaly_reason: "answer_vs_evidence_mismatch"
                 | "retrieved_context_incomplete"
                 | "evidence_recall_low"
                 | "confidence_anomaly"
  confidence: float (0.0-1.0)
  evidence_pointers: list[opaque_id: str]  // 仅 ID，无文本
```

### 拒绝规则

| 违规 | 检测方式 | 拒绝行为 |
|------|---------|---------|
| `anomaly_reason` 不在枚举中 | 枚举成员检查 | 拒绝整个输出，返回 `MonitorRejection(reason="invalid_anomaly_reason", detail=raw_value)` |
| `anomaly_reason` 中有自由文本 | 与枚举值做字符串匹配 | 同上——枚举检查即可捕获 |
| Evidence pointer 包含内容文本 | pointer 值包含空白字符或超过 ID 长度限制 | 拒绝，返回 `MonitorRejection(reason="evidence_pointer_not_opaque", detail=offending_pointer)` |
| 出现最终归因标签 | 字段名检查 `label`、`attribution`、`diagnosis` | 拒绝，返回 `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| 出现 ECS 记录 | 字段名检查 `ecs`、`error_cause_solution`、`repair` | 拒绝，返回 `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| 出现 gold answer | 字段名检查 `gold_answer`、`correct_answer` | 拒绝，返回 `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| 出现完整失败轨迹 | 字段名检查 `trace`、`full_context`、`failed_output` | 拒绝，返回 `MonitorRejection(reason="forbidden_field", detail=field_name)` |
| 出现记忆写入 payload | 字段名检查 `memory_write`、`user_memory`、`failure_memory` | 拒绝，返回 `MonitorRejection(reason="forbidden_field", detail=field_name)` |

### 场景卡片

#### 卡片 A：合法触发
- Monitor 输入：baseline 答案与检索证据矛盾
- Monitor 输出：`{trigger_replay: true, anomaly_reason: "answer_vs_evidence_mismatch", confidence: 0.87, evidence_pointers: ["mem_003"]}`
- 预期：接受，触发 replay。
- 需展示状态：trigger_replay、anomaly_reason 标签、confidence、pointer 数量。

#### 卡片 B：自由文本 Reason 被拒
- Monitor 输出：`{trigger_replay: true, anomaly_reason: "答案与存储的用户位置信息相比看起来有问题", confidence: 0.9}`
- 预期：拒绝，`MonitorRejection(reason="invalid_anomaly_reason", detail="答案与存储的用户位置信息相比看起来有问题")`。
- 需展示状态：拒绝原因、原始值、建议使用枚举值。

#### 卡片 C：含内容 Pointer 被拒
- Monitor 输出：`{trigger_replay: true, anomaly_reason: "evidence_recall_low", evidence_pointers: ["mem_003: 用户住在柏林"]}`
- 预期：拒绝，`MonitorRejection(reason="evidence_pointer_not_opaque", detail="mem_003: 用户住在柏林")`。
- 需展示状态：拒绝原因、违规 pointer、建议仅使用 opaque ID。

#### 卡片 D：禁止字段被拒
- Monitor 输出包含 `{"diagnosis": "retrieval_error"}` 或 `{"gold_answer": "柏林"}`
- 预期：拒绝，`MonitorRejection(reason="forbidden_field", detail=field_name)`。
- 需展示状态：检测到哪个禁止字段。

#### 卡片 E：全枚举穷举
- 测试所有四个枚举值均被接受：`answer_vs_evidence_mismatch`、`retrieved_context_incomplete`、`evidence_recall_low`、`confidence_anomaly`。
- 测试每个值在拼写错误或同义改写时被拒绝。
- 需展示状态：每个枚举值的接受数 vs 拒绝数。

### 状态模型

```
MonitorCalled(trace, baseline_state)
  -> MonitorOutputSubmitted(raw_output)
      -> validate_monitor_output(raw_output)
          -> | Accepted: MonitorResult(trigger, reason_enum, confidence, opaque_ids)
          -> | Rejected: MonitorRejection(reason, detail)
```

### 判定占位

枚举穷举是否覆盖了所有合理的 monitor 触发场景？是否有无法归入四个枚举值的合法异常信号？
