# 多跳能力实验（新链路）：三臂对照

**日期**：2026-09-21
**对象**：新链路（`eval_generation_replay.py` → `src/generator_v2.py`），冻结证据包上生成
**判定口径**：`soft-audit-behaviour-v5`
**LangSmith**：dataset `design-kb-multihop-6` + **3 个 experiment** 绑定其上

---

## 一、要回答的问题

现有 12 道评测题里 **11 题是单跳事实抽取**，没有任何多跳题 —— 「多跳能力」既没实现，也没被测量。
而检索侧已冻结，新题无法跑检索取证据。

**待验证的假设**：多跳答不好，是因为**证据根本没进上下文**，还是**模型抓不住多个证据项**？

已核实的事实：每题 `documents` 有 4~10 个 chunk，但 `build_evidence_pack(max_items=5)` **只取 5 个**
→ **38 个已冻结 chunk 一直被丢弃**。

---

## 二、三臂设计

| 臂 | 证据条数 | 每条字符 | 意图 |
|---|---|---|---|
| **G1** | 5（默认） | 不截断 | 现状 |
| **G2** | 全部（9~20） | 不截断 | 只加证据 → 覆盖率升，但上下文膨胀 |
| **G3** | 全部（9~20） | **300** | 加证据同时压缩 → 目标：拿到 G2 的覆盖率、G1 的延迟 |

### 2.1 复合题：从已冻结 chunk 池化，不跑检索

`scripts/build_multihop_snapshot.py` 把来源题的 chunk 按顺序池化成复合题，全程无检索（`retrieval_calls=0`）。

**防伪造断言**：每跳的 `expected_span` 与每组 `required_terms` 必须能在**声明的那个 chunk 原文**里找到
（归一化空白后比对），否则脚本 `SystemExit`。

> 为什么必须有这道闸：chunk 是**为原问题**召回的，未必含新问题需要的事实。
> 若放行，就会造出「证据里根本没有答案」的假题，把**检索缺失**误诊成**生成能力不足**。

6 道题覆盖 4 个主题：跨标准综合设计 / 标准间对比 / 人群差异对比 / 百分位推算。

### 2.2 让截断真的咬住

池化后按「来源题顺序 → 各题内 retrieval_rank」排列，于是**越晚出现的来源题，其证据在上下文里越靠后**。
设计结果：**每题恰好有一跳落在第 5 位之后**（被默认截断丢掉）。

没有这个设计，G1 与 G2 结果必然相同，对照就是空的。

### 2.3 压缩预检：先测量再动手

按字符预算截断后，**必需事实是否还在**：

| 预算 | 上下文 | 相对不截断 | 丢失的跳 |
|---|---|---|---|
| 200 | 30859 | 41% | **0** |
| **300** | **40162** | **54%** | **0** |
| 400 | 48512 | 65% | 0 |
| 不截断 | 74688 | 100% | 0 |

选 300：砍掉 46% 上下文，零事实损失，且给事实周围留下可解读的上下文。

> **截断发生在证据本身（`page_content`），不是在渲染提示词时。**
> 审计用 `item.page_content` 判「无出处数字」；若只截断渲染文本，
> 审计会把**模型从未看到**的尾部当作有出处 → 把模型不可能知道的数字判成有依据。

### 2.4 新增指标 `hop_recall`

逐跳判定：该跳的**全部必需词组都命中**，且声明的片段也命中，才算该跳覆盖。

- **每跳独立判定**，不共用词池（否则一跳的词会满足另一跳的检查，指标恒为 1.0）
- **只测「事实是否被带出」，不测「推理是否正确」**（用词表测推理会重演 v3 之前 `ambiguity_safety` 的缺陷）

---

## 三、结果

### 3.1 三臂总览

| | G1 | G2 | **G3** |
|---|---|---|---|
| 生成成功率 | 1.000 | 0.833 | **1.000** |
| 超时率 | 0.000 | **0.167** | **0.000** |
| `hop_recall` | 0.472 (n=6) | 0.767 (n=5) | **0.778 (n=6)** |
| `answer_span_recall` | 0.528 | 0.933 | **0.945** |
| 延迟 mean / max | 44s / 55s | 87s / **182s** | **46s / 75s** |
| 墙钟 | 262s | 524s | **275s** |
| 可用性闸门 | 通过 | **失败** | **通过** |

**G3 拿到了 G2 的覆盖率，同时恢复了 G1 的延迟，且不再超时。**

注意 G2 的 `n=5`：它有一题超时，分母与另两臂不同。**G3 六题全部完成，n=6**，
所以 G1↔G3 是可比的同分母比较，不需要配对修正。

### 3.2 逐题逐跳

| 题 | G1 | G2 | G3 |
|---|---|---|---|
| mh01 | T T **F** | 超时 | T **F** F |
| mh02 | T T **F** | T T T | T T **F** |
| mh03 | F F | T F | **T T** |
| mh04 | F T **F** | F T T | F T **T** |
| mh05 | T **F** | T T | **T T** |
| mh06 | T **F** T | T T F | **T T T** |

（**粗体** = 该题在 G1 中被截断的那一跳）

**G3 vs G1**：4 跳改善（mh03 两跳、mh04 h3、mh05 h2、mh06 h2），1 跳回退（mh01 h2）。

被截断的跳在 G3 中大多转为覆盖：mh04 h3、mh05 h2、mh06 h2 均 F→T。

### 3.3 分组闸门：G3 vs G1（声明 `multi_hop,quality`）

| 指标组 | 判定 | 指标 | 变化 |
|---|---|---|---|
| usability | **通过** | provider_timeout_rate | 0 → 0（**无可用性回归**）|
| quality | 违规（beyond_tolerance） | answer_span_recall_mean | +0.417 |
| multi_hop | 违规（beyond_tolerance） | hop_recall_mean | +0.306 |
| citation | 违规（out_of_scope） | citation_id_usage_ratio_mean | −0.368（见 §4.2）|

**对比 G2 vs G1**：G2 时 usability 组因超时率 0 → 0.167 判违规、可用性闸门失败；
**G3 把这两项都修好了。**

quality 与 multi_hop 报「超出容差」而不是「通过」，是因为**声明只说了「这两组会动」，
没说会动多少**，而实际涨幅远超 5%/10% 的容差。这是闸门的正确行为：
声明不完整就该被拦下，而不是被放行。

---

## 四、两个必须记录的发现

### 4.1 ⚠️ 配置的超时根本没兜住调用（严重）

G3 首跑出现**单次尝试耗时 9458.9 秒（2.6 小时）**，而 `request_timeout=180`、
`max_retries=0` 都正确传到了客户端。

**根因**：HTTP 层超时**只在 socket 静默时触发**。中继只要持续滴流字节就会不断重置它。

**后果**：一行可以挂几小时，**整轮延迟数据作废**（首跑 elapsed=9648s）。
这也意味着此前任何基于延迟的结论都可能是脆的。

**修法**：调用外层加 `asyncio.wait_for(..., timeout=llm_timeout + 30s)`——
事件循环强制生效，与传输层行为无关；+30s 余量让客户端自身的错误（带 provider 原文）通常先返回。

修复后 G3 重跑：**4 分 47 秒**完成（原 2 小时 41 分）。

### 4.2 ⚠️ `citation_id_usage_ratio` 不可跨证据量比较

`= 用到的引用 / 允许的引用`。G3 的允许引用有 9~20 个，**分母机械变大**，比值必然下降。

所以 §3.3 里那个 −0.368 的「违规」**主要是分母效应，不是引用质量退化**。
该指标只在**证据条数相同**的两臂之间可比，**需要重新设计口径**。

---

## 五、结论

1. **瓶颈是证据可见性，不是模型能力。** 被截断的跳在放开上限后几乎全部转为覆盖。
2. **但「只加证据」不可取**：G2 换来 16.7% 超时率。
3. **正解是「加证据 + 压缩」**：G3 以 54% 的上下文拿到同等甚至更高的覆盖率，
   延迟与 G1 持平，可用性零回归。
4. **不需要改生成器**（不必上两阶段生成、不必上本地模型）。改证据装配即可。

---

## 六、复现方式

```bash
# 1. 生成复合题（含防伪造断言；--positions 打印每跳在上下文中的位置）
python scripts/build_multihop_snapshot.py --positions

# 2. 三臂各跑一轮
python eval_generation_replay.py --snapshot data/frozen_multihop_cases.jsonl \
  --evaluation data/generation_eval.multihop.v1.json \
  --max-evidence 5  --output data/runs/mh_g1_evidence5.json --repetition-index 1
python eval_generation_replay.py --snapshot data/frozen_multihop_cases.jsonl \
  --evaluation data/generation_eval.multihop.v1.json \
  --max-evidence 99 --output data/runs/mh_g2_evidenceall.json --repetition-index 1
python eval_generation_replay.py --snapshot data/frozen_multihop_cases.jsonl \
  --evaluation data/generation_eval.multihop.v1.json \
  --max-evidence 99 --max-chars-per-item 300 \
  --output data/runs/mh_g3_evidenceall_compress300_r2.json --repetition-index 1

# 3. 分组闸门（G1 作基线）
python aggregate_generation_replays.py data/runs/mh_g1_evidence5.json \
  --slices data/generation_eval_slices.multihop.v1.json \
  --evaluation data/generation_eval.multihop.v1.json --output <G1 聚合>
python aggregate_generation_replays.py data/runs/mh_g3_evidenceall_compress300_r2.json \
  --slices data/generation_eval_slices.multihop.v1.json \
  --evaluation data/generation_eval.multihop.v1.json \
  --baseline <G1 聚合> --declared-changes multi_hop,quality

# 4. 上传 LangSmith（三次上传复用同一 dataset）
python upload_generation_replay_langsmith.py --result <任一运行> \
  --evaluation data/generation_eval.multihop.v1.json --arm <臂名>
```
