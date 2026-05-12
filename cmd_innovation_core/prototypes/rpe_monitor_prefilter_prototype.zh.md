# 原型简报：RPE 预过滤器（面向 Subagent Judge Monitor）

## 分支

逻辑原型。问题在于：D-MEM 风格的 Reward Prediction Error（RPE）预过滤器能否在不降低归因召回的前提下减少 Subagent Judge Monitor 的 replay 触发成本。

## 来源

- D-MEM (arXiv:2603.14597)：Critic Router 用 Surprise 和 Utility 评分门控刺激；低 RPE 输入绕过昂贵的 O(N) 重构（80% token 节省）。
- CMD hyp-011：RPE 作为 Subagent Judge Monitor 的廉价异常预过滤器。

## 一次性契约

- 此原型从第一天起即为一次性。
- 所有状态保留在内存中。
- 每次操作后展示完整状态。
- 预过滤器决策验证后应删除或吸收。
- 不涉及生产数据、持久化、精美 UI 或宽泛的错误处理。

## 问题

当 Subagent Judge Monitor 检测到异常时，是否应始终触发完整的 V0 Replay Portfolio（6 个 replay），还是可以由轻量级 RPE 预过滤器对证据-惊奇差距评分并跳过低惊奇案例？

## RPE 预过滤器设计

### 输入

- `baseline_answer`：失败的 agent 输出
- `retrieved_memory_items`：agent 上下文中的内容
- `gold_evidence`：已知 gold（仅用于评估；生产中使用 evidence_recall_from_text 作为代理）
- `confidence_threshold`：低于此阈值时始终触发 replay

### 评分

```
surprise_score = 1.0 - max(evidence_recall_from_text(gold_evidence_unit, item.text) for item in retrieved_memory_items)
utility_score = answer_score(baseline_answer)  // 任务失败程度
rpe = surprise_score * utility_score
```

### 门控决策

```
if rpe > REPLAY_THRESHOLD:
    trigger_full_replay_portfolio()
elif confidence < CONFIDENCE_THRESHOLD:
    trigger_full_replay_portfolio()  // 不确定性案例的安全网
else:
    skip_replay()  // 低惊奇、高置信度：记忆流水线大概率无过错
```

## 场景卡片

### 卡片 A：高惊奇、高效用 → 触发
- 记忆条目不含 gold evidence（surprise=1.0）
- Answer score 为 0.0（utility=1.0）
- RPE = 1.0 → 触发完整 replay
- 预期：replay portfolio 运行，识别流水线错误。

### 卡片 B：低惊奇、低效用 → 跳过
- 记忆条目包含 gold evidence（surprise=0.1）
- Answer score 为 0.9（utility=0.1，接近正确）
- RPE = 0.01 → 跳过 replay
- 预期：replay 跳过；接近正确的答案且有证据存在，表明记忆流水线无过错。

### 卡片 C：低惊奇、高效用 → 触发（效用主导）
- 记忆条目包含 gold evidence（surprise=0.0）
- Answer score 为 0.0（utility=1.0，完全失败）
- RPE = 0.0 但效用很高
- 预期：这是 `reasoning_error` 领域。预过滤器仍应触发，因为 utility=1.0 且有证据存在暗示推理失败。

### 卡片 D：高惊奇、低置信度 → 触发（安全网）
- RPE 中等（0.4）
- Monitor 置信度低（0.3）
- 预期：无论 RPE 多少都触发；低置信度是安全覆盖。

### 卡片 E：批量跳过率
- 在 100 个已知扰动标签的探针案例上运行预过滤器。
- 测量：跳过率、假跳过率（跳过了实际有流水线错误的案例）、预过滤后的归因召回。
- 目标：<5% 假跳过率，>30% replay 成本降低。

## 需展示的状态

预过滤器评估后展示：
- `case_id`
- `surprise_score`
- `utility_score`
- `rpe`
- `monitor_confidence`
- `gate_decision`（trigger / skip）
- `actual_perturbation_label`（用于评估）
- `false_skip`（若跳过但实际有流水线错误则为 true）

## 与 CMD-Audit 的关系

RPE 预过滤器位于 Subagent Judge Monitor 与 V0 Replay Portfolio 之间：

```
Failed Task
  -> Subagent Judge Monitor（防泄漏异常检测）
    -> RPE Pre-Filter（本原型）
      -> [trigger] V0 Replay Portfolio
      -> [skip] 仅返回 monitor 结果
```

预过滤器不改变归因或 ECS 输出。它仅门控 replay 是否运行。

## 判定占位

RPE 评分（surprise * utility）是否捕获了正确的 replay 案例？是否存在 RPE 低但记忆流水线错误确实存在的失败模式？置信度安全网是否捕获了这些边缘案例？
