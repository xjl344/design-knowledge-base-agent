# 多跳能力实验（新链路）：证据上限 G1 vs G2

**日期**：2026-09-21
**对象**：新链路（`eval_generation_replay.py` → `src/generator_v2.py`），冻结证据包上生成
**判定口径**：`soft-audit-behaviour-v5`
**LangSmith**：dataset `design-kb-multihop-6`，experiments `…evidence5` / `…evidence-all`

---

## 一、要回答的问题

现有 12 道评测题里 **11 题是单跳事实抽取**，没有任何多跳题 —— 所以「多跳能力」既没实现，也没被测量。
而检索侧已冻结，新题无法跑检索取证据。

**待验证的假设**：多跳答不好，是因为**证据根本没进上下文**，还是**模型抓不住多个证据项**？

已核实的事实：每题 `documents` 有 4~10 个 chunk，但 `build_evidence_pack(max_items=5)` **只取 5 个**
→ **38 个已冻结 chunk 一直被丢弃**。

这个假设决定要不要改生成器：如果是前者，改配置即可；如果是后者，才需要改生成逻辑。

---

## 二、实验设计

### 2.1 复合题：从已冻结 chunk 池化，不跑检索

`scripts/build_multihop_snapshot.py` 把来源题的 chunk 按顺序池化成复合题，全程无检索（`retrieval_calls=0`）。

**防伪造断言**：每跳的 `expected_span` 与每组 `required_terms` 必须能在**声明的那个 chunk 原文**里找到
（归一化空白后比对），否则脚本 `SystemExit`。

> 为什么必须有这道闸：chunk 是**为原问题**召回的，未必含新问题需要的事实。
> 若放行，就会造出「证据里根本没有答案」的假题，把**检索缺失**误诊成**生成能力不足**。

6 道题覆盖 4 个主题：

| id | 主题 | 需跨的证据 |
|---|---|---|
| mh01 | 跨标准综合设计 | 3326 配合高差 + 26158 坐高 + 26158 适用范围 |
| mh02 | 标准间对比 | 3326 座高 vs 26158 坐高 |
| mh03 | 人群差异对比 | 10000 成年人 vs 26158 未成年人 |
| mh04 | 百分位推算 | 26158 坐高百分位 + 3326 座高 |
| mh05 | 跨标准 | 3326 扶手椅尺寸 + 26158 适用范围 |
| mh06 | 设计任务综合 | 3326 座高/座深/背倾角 + 26158 坐高 |

### 2.2 让截断真的咬住

池化后按「来源题顺序 → 各题内 retrieval_rank」排列，于是**越晚出现的来源题，其证据在上下文里越靠后**。
设计结果：**每题恰好有一跳落在第 5 位之后**（即被默认截断丢掉）。

| 题 | 被截断的跳 | 位置 | 池化 chunk 数 |
|---|---|---|---|
| mh01 | h3 | 8 | 17 |
| mh02 | h3 | 8 | 17 |
| mh03 | h2 | 11 | 20 |
| mh04 | h3 | 8 | 17 |
| mh05 | h2 | 8 | 17 |
| mh06 | h2 | 8 | 9 |

没有这个设计，两臂结果必然相同，对照就是空的。

### 2.3 新增指标 `hop_recall`

`soft_audit(..., required_hops=())` 逐跳判定：该跳的**全部必需词组都命中**，且声明的片段也命中，才算该跳覆盖。

两条刻意的约束：
- **每跳独立判定**，不共用词池。否则一跳的词会满足另一跳的检查，任何答案都会看起来完整。
- **只测「事实是否被带出」，不测「推理是否正确」**。用词表测推理会重演 v3 之前
  `ambiguity_safety` 的缺陷 —— 同一语义行为因措辞不同被判反。

---

## 三、结果

### 3.1 两臂总览

| | G1（证据上限 5） | G2（用满全部） |
|---|---|---|
| 生成成功率 | **1.000** | 0.833 |
| 超时率 | **0.000** | **0.167** |
| `hop_recall` | 0.472 (n=6) | 0.767 (n=5) |
| `answer_span_recall` | 0.528 | 0.933 |
| 延迟 p50 / max | 53s / 55s | 77s / **182s** |
| 墙钟 | 262s | 524s |

### 3.2 配对比较（关键）

⚠️ **G2 有一题超时，所以两臂的分母不同（n=6 vs n=5）**，直接比 0.472 与 0.767 是不公平的。
在**共同完成的 5 题**上做配对比较：

| 口径 | G1 | G2 |
|---|---|---|
| `hop_recall`（共同 5 题） | **0.433** | **0.767** |

### 3.3 逐跳翻转

| 题 | 跳 | 结果 |
|---|---|---|
| mh02 | h3 | F → T ✓（该跳在 G1 被截断） |
| mh03 | h1 | F → T ✓ |
| mh04 | h3 | F → T ✓（该跳在 G1 被截断） |
| mh05 | h2 | F → T ✓（该跳在 G1 被截断） |
| mh06 | h2 | F → T ✓（该跳在 G1 被截断） |
| mh06 | h3 | T → F ✗（噪声） |

**5 跳 F→T，1 跳 T→F。其中 4 跳正是被截断的那一跳。**

---

## 四、结论

### 4.1 主结论：瓶颈是「证据没进上下文」

被截断的跳在放开上限后**几乎全部转为覆盖**（4/5）。这说明多跳答不好**主要不是模型抓不住**，
而是**它根本没看到那部分证据**。

**因此不需要改生成器**（不需要两阶段生成、不需要本地模型）。改证据装配即可。

### 4.2 但代价是可用性：天花板再次被撞

G2 的 mh01（17 chunk / 12831 字符）**卡在 182 s 超时**，超时率 16.7% > 10% 阈值。
延迟 p50 从 53 s 升到 77 s，墙钟翻倍。

> 这与 v4 那次「60 s 天花板」是同一类问题：**放宽上下文会推高延迟，而上限是硬的**。
> 上一轮把上限从 60 s 提到 180 s；本轮 182 s 又刚好越过。

### 4.3 分组闸门抓到 4 处，全部有效

| 指标组 | 判定 | 指标 | 说明 |
|---|---|---|---|
| usability | **违规**（out_of_scope） | `provider_timeout_rate` 0 → 0.167 | 可用性回归未被声明 |
| quality | **违规**（beyond_tolerance） | `answer_span_recall_mean` 0.528 → 0.933（+0.405） | 声明了会动，但没声明幅度 |
| multi_hop | **违规**（beyond_tolerance） | `hop_recall_mean` 0.472 → 0.767（+0.295） | 同上 |
| citation | **违规**（out_of_scope） | `citation_id_usage_ratio_mean` 0.767 → 0.307（−0.46） | 见下方警告 |

**可用性闸门也独立触发**（`provider_stability_gate_passed=false`）。

这同时完成了此前挂着的「真实改动验证」缺口：**闸门确实能识别真实变化**，
而且能把「可用性回归」与「质量变化」分开报，不会把超时平均进一个总分里。

### 4.4 ⚠️ 一处指标设计警告：`citation_id_usage_ratio` 不可跨证据量比较

`citation_id_usage_ratio = 用到的引用数 / 允许的引用数`。G2 的允许引用从 ~5 涨到 9~20，
**分母机械地变大**，比值必然下降。

所以上表里那个 −0.46 的「违规」**主要是分母效应，不是引用质量退化**。
这条指标只在**证据条数相同**的两臂之间可比。

这与之前记录的「基线必须覆盖同一批运行」是同一类陷阱：**分母变了，比值就不可比**。

---

## 五、下一步建议（按性价比排序）

1. **不要直接采用 G2 的配置**——它会撞延迟上限。
2. 真正该做的是**在受控上下文预算下提高证据利用率**：
   - 提高上限的同时**压缩每条证据**（去掉与问题无关的段落），而不是整段塞入；
   - 或按相关性**筛选**要放进上下文的 chunk（这不是检索改动，是上下文装配）；
   - 或继续提高 `LLM_TIMEOUT_SECONDS`，但要接受墙钟翻倍。
3. 若目标只是「多跳能答」，**先把证据装配改对**，两阶段生成/本地模型都不必上。
4. `citation_id_usage_ratio` 需要重新设计（改为「每条答案句是否都有引用」这类不随证据量漂移的口径），
   否则它在不同配置间永远会报假违规。

---

## 六、复现方式

```bash
# 1. 生成复合题（含防伪造断言；--check 可校验已生成文件未过期）
python scripts/build_multihop_snapshot.py --positions

# 2. 两臂各跑一轮
python eval_generation_replay.py --snapshot data/frozen_multihop_cases.jsonl \
  --evaluation data/generation_eval.multihop.v1.json \
  --max-evidence 5  --output data/runs/mh_g1_evidence5.json --repetition-index 1
python eval_generation_replay.py --snapshot data/frozen_multihop_cases.jsonl \
  --evaluation data/generation_eval.multihop.v1.json \
  --max-evidence 99 --output data/runs/mh_g2_evidenceall.json --repetition-index 1

# 3. 分组闸门对比（G1 作基线）
python aggregate_generation_replays.py data/runs/mh_g2_evidenceall.json \
  --slices data/generation_eval_slices.multihop.v1.json \
  --evaluation data/generation_eval.multihop.v1.json \
  --baseline <G1 的聚合结果> --declared-changes multi_hop,quality \
  --report reports/generation-report-multihop-g1-vs-g2.md

# 4. 上传 LangSmith（两次上传复用同一 dataset）
python upload_generation_replay_langsmith.py \
  --result data/runs/mh_g1_evidence5.json \
  --evaluation data/generation_eval.multihop.v1.json --arm evidence5
```
