# agent loop 设计评审

对照对象：`phonecli/`（原版）与 `phoneagent/core/agent.py`（Android 移植版）。
所有结论都标了代码位置或实测数据。

---

## 1. 现在一圈循环做什么

```
1. dump_ui()          取控件树               (agent.py:914-1000)
2. _marks(xml)        ≤30 个可点元素编号      (agent.py:991, config.max_marks=30)
3. screenshot()       1280x2772 PNG          （实测 2.0 MB）
4. _marked_png()      把编号画到图上          (agent.py:997)
5. _vlm_user_prompt() 拼观察块                (agent.py:1300)
6. llm.choose()       一次工具调用            (llm.py:98)
7. _execute_action()  执行                    (agent.py:1361)
8. _settle()          等界面停止变化           (agent.py:1347)
9. 循环检测 / 自检 / 计数
```

**关键事实：每一轮都是一次全新的两消息对话。**

```python
messages = [
    {"role": "system", "content": system},
    {"role": "user",   "content": user_content},   # llm.py:118-121
]
```

没有 assistant 历史、没有工具结果消息、没有对话累积。

状态只通过两条路传递：

| 通道 | 内容 | 位置 |
|---|---|---|
| 观察块 | 当前屏、编号列表、屏上文字 | `_vlm_user_prompt` |
| `Previous observations` | **最近 5 条**，每条一行 | `agent.py:1356`，`history_window=5` |

---

## 2. 与原版 PhoneCLI 对照

| 维度 | 原版 `phonecli/` | 移植版 | 评价 |
|---|---|---|---|
| 视觉调用方式 | 单次 VLM 调用 + `--history` 文本 | 同 | **一致** |
| 历史长度 | `state.history[-5:]`（agent.py:875） | `history_window=5` | **一致** |
| 历史内容 | 动作 + 少量结论 | 动作 | 移植版**更弱**（见 §3.2） |
| CoT | 要求 `State Assessment:` 再 `Action:`（prompts.py:151） | 工具模式明文禁止解释（prompts.py:444-448） | **移植版丢了 CoT** |
| 推理开关 | — | `disable_reasoning=True`（server.py:1300, run.py:60） | **模型内部思维链也关掉了** |
| 循环检测 | 有 | 有，且比较完整调用而非名字 | 移植版**更强** |
| 结果校验 | VLM 复核 | `_expectation` + `ANSWER_MISMATCH_NOTICE` | 移植版**更强** |
| 中途提示 | 少 | progress / empty / truncated / final / mismatch / see-it-first | 移植版**更强** |
| 地图 | 有 | 有（+SoM 编号、macro、落点自检） | 移植版**更强** |

**结论**：架构是忠实移植，而且在健壮性上更好；**唯一退步是 CoT**。

---

## 3. 问题清单（按影响排序）

### 3.1 完全没有思维链（影响最大）

三处叠加，导致模型**每轮从零开始看一张图就出动作**：

1. `disable_reasoning=True`（server.py:1300）——模型内部推理被关
2. `_TOOL_OUTPUT_FORMAT`："Do not explain what you are about to do; just call the function."（prompts.py:444-448）
3. 没有 assistant 历史——即使模型在文本里写了推理，下一轮也看不到

原版至少有 `State Assessment: <what you see, what changed, what to do next>`。

**影响**：任务一做长就"忘事"。小红书那次 15 轮里反复 `launch`，本质是
**模型不知道两轮前自己已经试过并失败**。

### 3.2 历史只记动作，不记结果

```python
self.history.append(f"{name}({', '.join(map(str, args))})")   # agent.py:1099
```

模型看到的是 `tap_index(7)`，**不知道它成功还是失败**。
`Step(round_no, "vlm", name, "ok" if ok else "failed")` 存在 `self.steps` 里，
但 `steps` 从不进入提示词。

对比：`macro` 失败会写 `macro X failed: ...`（agent.py:687），
说明作者知道结果重要——只是 VLM 动作这条路径漏了。

### 3.3 没有进度台账

- 25 轮的任务只能看到最近 5 条
- 早期的关键结论（"搜索页需要点右上角放大镜"）会滑出窗口
- `self.actions` 记录了完整调用序列，但只用于循环检测，**不给模型看**

### 3.4 每轮没有"第几步/还剩几步"

只有 50% 和 75% 两个检查点会提示（`PROGRESS_NOTICE`，agent.py:1246）。
其余 23 轮模型不知道自己还剩多少预算，无法做"要不要换策略"的判断。

### 3.5 图片成本

实测：每轮 2.0 MB PNG（1280x2772），base64 后约 2.7 MB。
按 28x28 patch 折算约 4.5k tokens/轮——**占一次调用 7-8k 输入 token 的六成**。

降到 720 宽（720x1560）约 1.1k tokens，**省 3.4k/轮，25 轮省 85k**。

### 3.6 观察块没有预算

`ui_text`（屏上文字）和 `marks`（30 个）都是全量塞入。
小红书信息流 42 个元素、设置页 155 个元素，都直接进提示词。
`max_marks=30` 有上限，但文字列表没有。

---

## 4. 改进建议

### P0｜历史记结果（半小时，风险低）

```python
# 现在
self.history.append(f"{name}({args})")
# 改成
result = "ok" if ok else "failed"
self.history.append(f"{name}({args}) -> {result}")
```

失败时再补一句原因（`no such mark` / `no text field` / `screen unchanged`）。

### P0｜进度台账（1 小时）

每轮在观察块里加 5-8 行**结论级**摘要，而不是动作流水：

```
## 进度
还剩 11 步。
已确认：小红书搜索要点击右上角放大镜（第 2 轮的宏做到了）。
已失败：launch 重复 3 次无效，不要再试。
```

实现：`actions` + `steps` 里挑关键事件（失败的宏、失败的 launch、识别到的屏）
渲染成短句，只保留最近 3 条失败和最近 3 条成功。

### P1｜CoT 二选一（需要 A/B 实测）

| 方案 | 做法 | 代价 |
|---|---|---|
| A | 打开 `disable_reasoning=False`（Qwen3-VL 支持 thinking） | token 增加，延迟增加 |
| B | 工具 schema 加可选 `why` 参数，要求填一句"为什么" | 输出 +20 字 |
| C | 回到原版文本模式 `State Assessment:` + `Action:` | 丢工具调用的稳定性 |

**建议先做 B**：改动最小，且 `why` 进入历史后天然形成 CoT 连续性。

### P1｜图片降采样（半小时）

`_marked_png()` 之后缩到 720x1560 再 base64。

### P2｜每轮显示剩余步数

`Choose ONE action. (step 7/25)` —— 一行字，让模型有预算意识。

### P2｜观察块字符预算

`ui_text` 超过 N 条就截断，并注明"还有 N 条未列出"。

---

## 5. 建议的验证顺序

1. P0 两条（历史记结果 + 进度台账）→ 跑小红书任务 5 次，看成功率
2. P1 的 `why` 字段 → 同样 5 次，对比
3. 图片降采样 → 只看成本变化，不看成功率

每步都有可量化指标：**成功率、平均轮数、单次任务 token**。
