# 原型简报：CMD 探针逻辑

## 分支

逻辑原型。问题在于：CMD 状态模型和归因循环在实现之前是否感觉正确。

## 当前原型状态

第一条可执行 smoke 路径已验证了最简单的 `retrieval_error` 场景和 issue 0002 的 comparator/monitor 边界。保留此原型简报作为非代码状态模型，而非生产文档。

下一个原型问题是：同一状态模型是否能让 Verbatim Event Oracle 边界变得显然——原始事件能恢复证据，抽取后的记忆无法恢复，标签应为 `premature_extraction_error`。

Issue 0003 不应创建 UI 原型。相关原型仍为逻辑原型：一个微型终端/状态模拟，在每次操作后展示完整 case 状态、replay portfolio、恢复增益和归因结果。

## 原型问题

一个微型交互式探针能否让"失败记忆案例应标注为 write/compression/retrieval/premature extraction/injection/reasoning 中的哪一个"变得显然？

V0 仅模拟六个标签：`write_error`、`compression_error`、`premature_extraction_error`、`retrieval_error`、`injection_error` 和 `reasoning_error`。

边界规则：原型区分 CMD-Audit 与未来的 CMD-Skill Adapter，保持 subagent judge monitor 防泄漏，并将 bad memory item labels 排除在 V0 归因之外。

## 一次性契约

- 此原型从第一天起即为一次性。
- 所有状态保留在内存中。
- 每次操作后展示完整状态。
- 归因循环验证后应删除或吸收。
- 不涉及生产数据、持久化、精美 UI 或宽泛的错误处理。

## 未来的单命令形态

实现开始时，预期命令应为启动交互式终端运行的单一本地命令。具体命令应遵循最终的项目运行器。

原型应假定为独立研究 harness。可以展示 adapter 边界，但不应与现有记忆 agent 集成。

对于当前 harness 形态，预期的一次性 smoke 运行命令接近：

```text
python3 -m cmd_audit run --cases data/probe_cases/<v0_cases>.json --out artifacts/attribution_table.csv --metrics-out artifacts/comparison_metrics.csv
```

## 状态模型

1. `CaseLoaded`：query、原始事件、抽取记忆、gold evidence、gold answer、扰动标签。
2. `BaselineFailed`：记录 baseline 输出和初始分数。
3. `MonitorFlagged`：防泄漏 subagent judge monitor 附带高召回 replay 触发原因，但不发出最终标签、ECS、记忆写入、gold answer 或完整失败轨迹。
4. `ReplaysRun`：每个 replay 有输出、answer score、evidence score、恢复增益和成本。
5. `AttributionAssigned`：top-1、top-2 和歧义注释可见。
6. `ECSDrafted`：wrong memory、cause、corrected memory、repair action 和 guidance 可见。
7. `RepairedContextBuilt`：组装 corrected memory、repair guidance 和 repaired evidence block，不含 gold answer。
8. `PostRepairRetested`：用修复后上下文重新运行原始失败 query。
9. `RepairValidated` / `RepairFailed`：repair success、evidence score、token cost 和 regression risk 可见。
10. `RepairSimulated`：定向修复与通用困难样例更新对比。
11. `FutureCaseGuided`：未来相似任务只接收 corrected memory 和 repair guidance。

## 需模拟的操作

- 加载带标签的失败案例。
- 运行 baseline 记忆答案。
- 触发 subagent judge monitor。
- 按固定 V0 标签顺序构建 V0 Replay Portfolio。
- 运行 Oracle Write replay。
- 运行 Oracle Compression replay。
- 运行 Oracle Retrieval replay。
- 运行 Verbatim Event Oracle replay。
- 运行 Injection-Oracle replay。
- 运行 Evidence-Given Reasoning replay。
- 从恢复增益中分配归因。
- 起草 Error-Cause-Solution。
- 构建修复后上下文（不注入 gold answer）。
- 用修复后上下文重新运行原始失败 query。
- 模拟定向修复。
- 模拟未来 Failure Memory 检索。
- 展示面向未来记忆 agent 集成的 adapter-boundary payload。

## 场景卡片

### 看似检索实为过早抽取

原始事件包含所需证据，但抽取后的记忆不包含。Verbatim Event Oracle 恢复答案，而在抽取记忆上的 Oracle Retrieval 无法恢复。预期结果：不标注为 `retrieval_error`。
预期标签：`premature_extraction_error`。

需展示的状态细节：

- gold evidence 指向原始事件；
- gold evidence 不指向抽取后的 Memory Item；
- Oracle Retrieval 的 evidence score 保持低位；
- Verbatim Event Oracle 的 evidence score 恢复；
- top replay 映射到 `premature_extraction_error`。

### 正确记忆，错误检索

抽取后的记忆包含 gold evidence，但 baseline 检索未命中。Oracle Retrieval 恢复答案。预期结果：标签 `retrieval_error`。

### 正确证据，错误推理

Baseline 检索到正确证据但回答错误。Evidence-Given Reasoning 恢复答案。预期结果：标签 `reasoning_error`。

### 恢复增益接近

Oracle Compression 和 Oracle Retrieval 均恢复答案，且 delta 接近。预期结果：输出 top-2 归因并标记耦合失败。

### 防泄漏 Monitor

Subagent judge monitor 标记可疑轨迹。预期结果：触发 replay 但不发出最终标签、ECS、记忆写入、gold answer 或完整失败轨迹。

### 修复后复测

ECS 提供 corrected memory 和 repair guidance。预期结果：修复后上下文在不注入 gold answer 的情况下恢复原始失败 query。

## 判定占位

仅保留此原型的答案：状态模型是否在实现前为人类提供了足够信息以信任 CMD 归因。

下一判定待捕获：当仅原始事件 replay 能恢复所需证据时，`premature_extraction_error` 是否与 `retrieval_error` 有可见区别。
