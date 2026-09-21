# 实验协议

本文件规定「跑一次生成评测」的标准动作，以及什么样的结果可以被拿来下结论。

配合 `docs/evaluation-contract.md` 阅读：那份说明判据，这份说明流程。

## 前提

- 检索侧已冻结：不修改 `src/retriever.py`、不重建快照、不动分块 / Embedding / Reranker / Chroma、不调 `RETRIEVER_*` 参数
- 每一次生成回放都是 `retrieval_calls = 0` 且 `dry_run = false`——读冻结快照，不重新检索。这是检索冻结可验证的前提

## 步骤

### 1. 固定实验身份

一次运行的身份由五个字段组成，只有它们全部相同的两次运行才可比较：

| 字段 | 来源 | 含义 |
|---|---|---|
| `snapshot_sha256` | 冻结快照文件 | 检索输入 |
| `evaluation_sha256` | 契约文件 | 期望的答案标准 |
| `prompt_version` | `src/generator_v2.py` | 提示词 |
| `audit_version` | `src/frozen_evidence.py` | 判定口径 |
| `generation_max_retries` | 运行参数 | 重试策略 |

聚合时会校验这五项与 `repetition_index` 的唯一性，任一不一致直接报错。

### 2. 运行

```powershell
# 单轮（重复序号默认 1）
.\scripts\Run-GenerationReplay.ps1 -OutputPath data\runs\run_r1.json -RepetitionIndex 1

# 重复运行，序号必须不同
.\scripts\Run-GenerationReplay.ps1 -OutputPath data\runs\run_r2.json -RepetitionIndex 2
```

或者一次跑满整个矩阵（`Run-P0GenerationMatrix.ps1` 会自动递增序号并聚合）：

```powershell
.\scripts\Run-P0GenerationMatrix.ps1 -Repetitions 3
```

结果落在 `data/runs/`。**重复序号必须不同**——两轮用同一个序号会被聚合拒绝，因为它们无法被当作独立重复。

### 3. 聚合与报告

```powershell
.\scripts\New-GenerationReport.ps1 `
    -RunPath data\runs\p1_full_v3.json,data\runs\p1_full_v3_r2.json `
    -ReportPath reports\report.md
```

或直接调用 Python 入口：

```bash
.venv/Scripts/python.exe aggregate_generation_replays.py \
    data/runs/run1.json data/runs/run2.json \
    --output .tmp/agg.json --report reports/report.md \
    --slices data/generation_eval_slices.v1.json \
    --evaluation data/generation_eval.v2.json
```

### 4. 读报告的顺序

**必须按依赖顺序读，不能跳。**

| 顺序 | 板块 | 前一块不成立时的后果 |
|---|---|---|
| 1 | 实验可信度 | 运行不可比 → 后续任何「变化」都不是趋势 |
| 2 | 系统可用性 | 超时率过高 → 质量数字只描述幸存样本 |
| 3 | 回答质量 | — |
| 4 | 安全行为 | — |
| 5 | 引用质量 | — |

报告本身会在版面里写明这些条件，不依赖读者记得。

## 可得出与不可得出的结论

**可以**：在两组运行的开头五项身份一致、且 `runs_comparable = true` 时，比较声明过的指标组。

**不可以**：

- 跨 `audit_version` 比较（口径变了，数字含义也变了）
- 把不同重试策略的运行平均（成功率分布不同）
- 在 `runs_comparable = false` 时报告任何趋势
- 用单个数字概括系统水平（检索冻结组、可用性、质量、安全四类问题的补救方式完全不同）
- 把未声明组的变动当作「意外收获」——它说明改动没被限定范围，按违规处理
- **用「运行条数不同」的两组做分组闸门对比**

### 基线必须覆盖同一组运行

这是实测踩到的陷阱。同一配置的三轮运行里，`citation_id_usage_ratio_mean` 分别是
**0.225 / 0.225 / 0.305**——跨度 0.08，在 0.10 容差内，但已经接近噪声量级。

于是拿「两轮子集」（0.225）去比「三轮基线」（0.252）时，闸门报出
`citation` 组违规（`out_of_scope`，delta=-0.027）。**这个违规是分母变化造成的，不是系统退化。**

比对时必须让两组覆盖同一批运行。若确实要比较不同轮数，先确认差异**大于**该指标自身的
跨轮跨度（上例中须大于 0.08），否则区分不了「子集效应」与「真实变化」。

自比自（同一组运行当自己的基线）应当 `group_gate_passed = true`——
这是验证闸门没被「卡在触发态」的最简检查。

## 稳定性不足时怎么办

如果 `runs_comparable = false`，说明噪声量级与分析目标相当。此时**先修稳定性，再谈改动效果**：

1. 看第二板块的超时率与单次调用延迟余量
   - 余量为负 → 有调用触及上限，这是**卡死**的特征，不是「慢但会完成」
   - 余量不足 5 s → 超时率对服务抖动会很敏感
2. **区分「抖动」与「天花板」**——这一步决定后面所有步骤是否有效：

   | 特征 | 抖动 | 天花板 |
   |---|---|---|
   | 超时题是否跨轮变化 | 每轮不同题 | 同一题反复超时 |
   | 失败行延迟 | 分散 | 精确卡在上限（±2 s） |
   | 提高重复次数 | 有帮助 | **无效** |

   判据：把各轮超时题号并排看。若某题 3/3 全超时，或失败延迟精确等于 `LLM_TIMEOUT_SECONDS`，就是天花板。
3. 天花板 → **提高 `LLM_TIMEOUT_SECONDS`**，不要加重复次数。重复次数不改变上限。
4. 抖动 → 提高重复次数（≥3），让运行间差异可被观察
5. 两者都处理完仍不可比 → 分析目标改为可用性本身，而不是回答质量

> **实测记录（2026-09-20）**：在 `LLM_TIMEOUT_SECONDS=60` 下跑 3 轮，超时率 33%–42%。超时题**不是随机分布**：`q08_hit`/`q10_hit` 3/3 全超时，`q04_hit`/`q05_hit`/`q09_hit` 各 2/3，失败行延迟精确为 60.0–62.5 s。证据包最大的 5 题（4.0k–4.5k 字符）与超时集高度重合。这是天花板而非抖动，所以第 4 步（加重复）在此场景下**完全无效**——瓶颈是上限本身。已把上限提到 180 s 后重跑。

在 12 题的规模上，只有一两轮重复得到的质量差异与噪声同量级，不能作为结论。

## 零破坏验证

改动判定口径或契约时，必须证明**只有目标指标在动**。

方法：**固定契约比较新旧实现**。契约升级本身会造成指标漂移，若同时改契约和实现，就无法区分「口径升级的预期变化」和「代码引入的回归」。

```bash
# 用同一份契约分别跑新旧实现，逐题比对
.venv/Scripts/python.exe .tmp/verify/compare.py
```

验收标准（P-1 修复时采用）：

- 12 题 × 11 个非行为类指标，**0 漂移**
- 目标行为指标的判定达成一致
- q17 的历史一致性从 3/7 升到 7/7

## 回归用例

`tests/fixtures/replay_fixtures.json` 保存真实运行的审计结果，供离线重放：

- 导出：`export_replay_fixtures.py`，**带 `audit_version` 闸门**——旧口径的 fixture 会被拒绝而不是混入。缺这道闸门时，早于预清洗层的旧数据会被当作当前结果，制造出假的指标漂移
- 用途：不调用模型即可验证判定逻辑未变
- 局限：只覆盖判定层，不覆盖生成质量

## 相关文件

- `scripts/Run-GenerationReplay.ps1`——跑一次回放
- `scripts/New-GenerationReport.ps1`——聚合与出报告
- `aggregate_generation_replays.py`——聚合 CLI
- `docs/evaluation-contract.md`——判定口径
