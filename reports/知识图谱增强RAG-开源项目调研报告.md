# 知识图谱 × RAG 开源项目调研报告

**调研主题**：同时结合知识图谱（Knowledge Graph）与检索增强生成（RAG）的开源项目
**数据来源**：GitHub REST API（`/search/repositories`、`/repos/{owner}/{repo}`）、PyPI 下载统计（`pypistats.org`）与各仓库 README
**数据截止**：2026-09-30（主报告）／2026-10-02（§7 补充复核）
**报告整理**：小嘟

> **📌 阅读提示**：第 7 节是 2026-10-02 的补充复核，用 **PyPI 真实下载量**重新评估了"哪些项目真的成熟"。
> 它**修正了主报告按 star 排序带来的误导**——建议先看 §7 再回头看前面的项目卡片。

---

## 0. 先说方法与局限

这一节建议先看，它决定了后面哪些结论可以信、哪些只能当参考。

**怎么找的。** 用四组检索口径交叉覆盖，避免只依赖单一关键词：

| 检索口径 | 命中仓库总数 |
|---|---|
| `graphrag in:name,description,readme` | 26,186 |
| `graph + rag in:name,description` | 12,048 |
| `knowledge graph retrieval augmented generation in:name,description` | 494 |
| `topic:knowledge-graph topic:rag` | 1,905 |

四路结果合并去重后，剔除两类：① 只做图数据库、与 LLM 检索无关的（如 NebulaGraph）；② 只做纯向量 RAG、图谱只是营销词的。最终保留 **26 个** 进入正文。

**三点局限，请务必知道：**

1. **star 数是"当下快照"，不是质量证明。** 本次抓取时 `graphify` 已有 12.2 万星，但它 2026 年 4 月才创建。热度可以反映关注度，不能反映工程成熟度——判断成熟度请看「最近更新时间 + Issue 积压 + 是否归档」这三个指标。
2. **"优缺点"一栏分两类来源。** 项目自身文档明确写了的（如"索引很贵""仅维护模式"），我按事实陈述；我基于架构和活跃度做的推断，会标注「（推断）」。不要把推断当结论。
3. **有一个项目在调研期间消失了。** `aws-samples/graphrag-toolkit`（AWS 官方 GraphRAG 工具包，曾 2,408 星相关生态）在抓取时返回 **404 Not Found**，仓库已不可访问。这是本领域"官方项目也会下架"的直接证据，选型时值得引以为戒。

---

## 1. 一张图看懂：知识图谱到底插在 RAG 的哪一步

这是整份报告最重要的一节。**所有项目的差异，本质上都是"图谱插在哪一步"的差异。**

```
                 ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
  原始文档  ───►  │ ① 索引/建图 │──► │ ② 检索      │──► │ ③ 生成      │──► 答案
                 └─────────────┘    └─────────────┘    └─────────────┘
                        ▲                  ▲
                  范式 A / B / C        范式 D / E
```

| 范式 | 图谱的角色 | 典型代表 | 解决的问题 |
|---|---|---|---|
| **A. 建图即索引** | LLM 从文本抽实体关系，图本身就是索引，替代/补充向量库 | GraphRAG、LightRAG、fast-graphrag | 跨文档、跨段落的**全局性**问题（"这批材料的主线是什么"） |
| **B. 图谱做社区摘要** | 建图后用 Leiden 聚类成社区，为每个社区生成摘要，检索时命中摘要 | microsoft/graphrag 的 global search | 语料整体归纳，成本换质量 |
| **C. 图谱做本体约束** | 先定 Schema/本体，抽取结果必须符合本体，图谱可被符号推理 | OpenSPG/KAG、cognee | 专业领域的**逻辑严谨性**与可解释性 |
| **D. 图上做多跳传播** | 建图后在图上跑 Personalized PageRank 等传播算法，激活关联节点 | HippoRAG、fast-graphrag、PathRAG | 多跳问答（A→B→C 型推理） |
| **E. 图谱做时序记忆** | 图谱带时间戳，事实变更即失效而非删除 | Graphiti、cognee | Agent 长期记忆、可回溯"某时刻的真相" |

**一句话总结**：向量检索擅长"找到和问题长得像的段落"，知识图谱擅长"把散落各处的片段连成一条推理链"。两者是互补关系，不是替代关系——这也是为什么几乎所有严肃项目最后都走向了**混合检索**。

---

## 2. 项目速查表

按类别与 star 数排序。⭐ 标记的是本报告重点推荐阅读的。

### 2.1 通用 GraphRAG 框架 / 引擎

| 项目 | Stars | Forks | 语言 | 最近推送 | 创建 | 许可 | 状态 |
|---|---|---|---|---|---|---|---|
| ⭐ [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) | 122,559 | 11,811 | Python | 2026-09-29 | 2026-04-03 | Apache-2.0 | 活跃 |
| ⭐ [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) | 39,928 | 5,642 | Python | 2026-09-28 | 2024-10-02 | MIT | 活跃 |
| ⭐ [microsoft/graphrag](https://github.com/microsoft/graphrag) | 36,169 | 3,817 | Python | 2026-09-28 | 2024-03-27 | MIT | **维护模式** |
| ⭐ [OpenSPG/KAG](https://github.com/OpenSPG/KAG) | 9,079 | 721 | Python | 2026-01-28 | 2024-09-21 | Apache-2.0 | 低频维护 |
| ⭐ [OSU-NLP-Group/HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG) | 4,033 | 434 | Python | 2026-09-29 | 2024-05-23 | MIT | 活跃 |
| [gusye1234/nano-graphrag](https://github.com/gusye1234/nano-graphrag) | 3,992 | 429 | Python | 2026-01-27 | 2024-07-25 | MIT | 低频维护 |
| ⭐ [circlemind-ai/fast-graphrag](https://github.com/circlemind-ai/fast-graphrag) | 3,955 | 260 | Python | 2025-11-01 | 2024-10-30 | MIT | 停滞近 1 年 |
| [microsoft/PIKE-RAG](https://github.com/microsoft/PIKE-RAG) | 2,481 | 236 | Python | 2025-09-10 | 2024-09-26 | MIT | **已归档** |
| ⭐ [neo4j/neo4j-graphrag-python](https://github.com/neo4j/neo4j-graphrag-python) | 1,295 | 247 | Python | 2026-09-29 | 2024-02-27 | 自定义 | 活跃 |
| [FalkorDB/GraphRAG-SDK](https://github.com/FalkorDB/GraphRAG-SDK) | 1,003 | 141 | Python | 2026-09-28 | 2024-01-25 | Apache-2.0 | 活跃 |
| [automataIA/graphrag-rs](https://github.com/automataIA/graphrag-rs) | 528 | 50 | Rust | 2026-06-02 | 2025-09-21 | MIT | 活跃 |

### 2.2 时序知识图谱 / Agent 记忆

| 项目 | Stars | Forks | 语言 | 最近推送 | 创建 | 许可 | 状态 |
|---|---|---|---|---|---|---|---|
| ⭐ [getzep/graphiti](https://github.com/getzep/graphiti) | 31,317 | 3,213 | Python | 2026-09-29 | 2024-08-08 | Apache-2.0 | 活跃 |
| ⭐ [topoteretes/cognee](https://github.com/topoteretes/cognee) | 31,231 | 3,137 | Python | 2026-09-30 | 2023-08-16 | Apache-2.0 | 活跃 |
| [GreatScottyMac/context-portal](https://github.com/GreatScottyMac/context-portal) | 768 | 78 | Python | 2026-01-27 | 2025-05-05 | Apache-2.0 | 低频维护 |

### 2.3 平台 / 应用（含图谱能力）

| 项目 | Stars | Forks | 语言 | 最近推送 | 创建 | 许可 | 状态 |
|---|---|---|---|---|---|---|---|
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 91,522 | 10,864 | Go | 2026-09-30 | 2023-12-12 | Apache-2.0 | 活跃 |
| ⭐ [xerrors/Yuxi](https://github.com/xerrors/Yuxi) | 7,241 | 1,151 | Python | 2026-09-29 | 2024-07-05 | MIT | 活跃 |
| ⭐ [neo4j-labs/llm-graph-builder](https://github.com/neo4j-labs/llm-graph-builder) | 5,272 | 879 | Jupyter | 2026-09-16 | 2024-01-11 | Apache-2.0 | 活跃 |
| [pingcap/autoflow](https://github.com/pingcap/autoflow) | 2,975 | 192 | TypeScript | 2026-04-27 | 2024-02-05 | Apache-2.0 | 低频维护 |
| [Azure-Samples/graphrag-accelerator](https://github.com/Azure-Samples/graphrag-accelerator) | 2,408 | — | Python | 2025-05-27 | — | MIT | **已归档** |
| [1517005260/graph-rag-agent](https://github.com/1517005260/graph-rag-agent) | 2,341 | 320 | Python | 2025-11-05 | 2025-02-12 | MIT | 停滞近 1 年 |
| [severian42/GraphRAG-Local-UI](https://github.com/severian42/GraphRAG-Local-UI) | 2,321 | 292 | Python | 2024-11-09 | 2024-07-14 | MIT | **停滞近 2 年** |

### 2.4 代码知识图谱（与 RAG 相邻）

| 项目 | Stars | Forks | 语言 | 最近推送 | 创建 | 许可 | 状态 |
|---|---|---|---|---|---|---|---|
| [vitali87/code-graph-rag](https://github.com/vitali87/code-graph-rag) | 5,189 | 688 | Python | 2026-09-30 | 2025-06-16 | MIT | 活跃 |
| [yifanfeng97/Hyper-Extract](https://github.com/yifanfeng97/Hyper-Extract) | 4,051 | 464 | Python | 2026-09-29 | 2026-01-07 | 自定义 | 活跃 |

### 2.5 学术实现（附论文）

| 项目 | Stars | 语言 | 最近推送 | 会议 | 许可 |
|---|---|---|---|---|---|
| [LHRLAB/HyperGraphRAG](https://github.com/LHRLAB/HyperGraphRAG) | 453 | Python | 2026-05-12 | NeurIPS 2025 | MIT |
| [BUPT-GAMMA/PathRAG](https://github.com/BUPT-GAMMA/PathRAG) | 377 | Python | 2025-12-17 | — | 无 |
| [Graph-COM/SubgraphRAG](https://github.com/Graph-COM/SubgraphRAG) | 186 | Python | 2025-01-27 | ICLR 2025 | MIT |
| [nju-websoft/KG2RAG](https://github.com/nju-websoft/KG2RAG) | 143 | Python | 2025-02-26 | NAACL 2025 | GPL-3.0 |
| [YZ-Cai/SimGRAG](https://github.com/YZ-Cai/SimGRAG) | 142 | Python | 2025-07-25 | ACL 2025 | 无 |

### 2.6 垂直领域

| 项目 | Stars | 领域 | 最近推送 | 许可 |
|---|---|---|---|---|
| [BaranziniLab/KG_RAG](https://github.com/BaranziniLab/KG_RAG) | 947 | 生物医学 | 2024-11-09 | Apache-2.0 |
| [Ais1on/CTI-RAG](https://github.com/Ais1on/CTI-RAG) | 243 | 网络威胁情报 | 2026-05-12 | GPL-3.0 |
| [iqvianlp/ontologyRAG](https://github.com/iqvianlp/ontologyRAG) | 93 | 生物医学本体映射 | 2025-02-27 | GPL-3.0 |

### 2.7 资源清单（找论文/基准从这里入手）

| 项目 | Stars | 最近推送 | 说明 |
|---|---|---|---|
| [DEEP-PolyU/Awesome-GraphRAG](https://github.com/DEEP-PolyU/Awesome-GraphRAG) | 2,663 | 2026-06-02 | 综述、论文、基准、开源项目的策展清单 |

---

## 3. 重点项目详解

每个项目统一按六个维度展开：**核心思路 / 技术方案 / 主要功能 / 适用场景 / 活跃度 / 优缺点**。

---

### 3.1 microsoft/graphrag —— 定义了"GraphRAG"这个词的项目

> **⚠️ 先说最重要的：本项目已进入维护模式。** README 顶部明确写着：自 2024 年 7 月首次发布以来，前沿模型能力已发生巨大变化，项目组的研究组合已多元化；**本项目基本处于维护模式，不再接受新 PR、不再实现新功能**，只做 bug 修复与依赖更新（尤其是 CVE 安全修复）。

**核心思路**
把"全局性问题"从向量检索的盲区里救出来。传统 RAG 只能回答"哪几段文字和问题最像"，但回答不了"这 500 份文档讲的主线是什么"——因为答案不在任何单个段落里。GraphRAG 的做法是：先用 LLM 把语料抽成实体关系图，再用图聚类把图切成"社区"，为每个社区预先写好摘要。检索时命中摘要而非原始段落，于是"全局问题"变成了"读几个摘要"。

**技术方案**
- **索引阶段**：文本切块 → LLM 抽取实体/关系/声明（claims）→ 构建知识图谱 → Leiden 算法做层次化社区聚类 → 为每个社区生成摘要报告
- **检索阶段**：三种模式
  - `local search`：从实体出发，沿图邻居扩展，适合具体事实问题
  - `global search`：在社区摘要层做 map-reduce 汇总，适合全局归纳问题
  - `drift search`：在社区摘要上继续展开，兼顾全局与细节
- **工程形态**：Python 包 + CLI，配置与提示词均可调；官方强烈建议针对自己的语料做提示词微调，开箱效果未必好

**主要功能**
模块化数据管道；可替换的 LLM / 嵌入 / 存储后端；支持增量索引；提供 Prompt Tuning 指南；配套 Azure 一键部署方案（已归档）。

**适用场景**
叙事型私有语料的全局理解——报告集、新闻档案、研究文献、企业内部文档。不适合对延迟敏感、对成本敏感的实时问答。

**活跃度**
36,169★ / 3,817 fork；创建于 2024-03-27，最近推送 2026-09-28；开放 Issue 仅 47 个（说明维护者清理积极，但也说明不再接新功能）；PR 策略已收紧为 `collaborators_only`。

**优缺点**
- ✅ **范式定义者**：几乎后来所有项目都以它为对照系，读它的文档等于读这个领域的"通用语"
- ✅ 文档质量与理论阐述最完整，`RAI_TRANSPARENCY.md` 里连"局限性与最小化方法"都写了
- ✅ global search 在全局归纳类任务上至今仍是强基线
- ❌ **索引成本高**：官方自己在 README 里加警告框——"GraphRAG 索引是昂贵操作，请先读懂文档、从小规模开始"
- ❌ **已停止演进**：不接受新功能 PR。选它意味着接受"不会再变好"
- ❌ 版本升级需重跑 `graphrag init --force`，跨大版本要跑迁移 notebook 或重建索引

---

### 3.2 HKUDS/LightRAG —— 目前综合性价比最高的通用选择

**核心思路**
针对 GraphRAG"慢且贵"的痛点做工程优化，同时保留图谱带来的全局理解能力。核心是**双层检索**：把知识图谱显式拆成"低层实体"和"高层关系"两个层次，分别索引、分别召回，再与向量检索融合。低层负责精确事实，高层负责主题与全局，一次查询两条路并行。

**技术方案**
- **双层索引结构**：同时维护知识图谱与向量嵌入两套表示
- **存储高度可插拔**：
  - 图存储：NetworkX（默认，零依赖）/ Neo4j / Memgraph
  - 向量存储：NanoVectorDB（默认）/ Milvus / Qdrant
  - KV 与文档状态：PostgreSQL / MongoDB / OpenSearch
- **文档解析**：支持 MinerU、Docling、原生解析器；四种分块策略（Fix / Recursive / Vector / Paragraph）
- **LLM 角色分离**：把模型调用拆成 EXTRACT（抽取）、QUERY（查询）、KEYWORD（关键词）、VLM（视觉）四个独立角色，可分别配不同模型——这个设计对成本控制很实用
- **论文**：EMNLP 2025

**主要功能**
五种查询模式（`local` / `global` / `hybrid` / `naive` / `mix`）；增量更新与选择性删除（不必重建全图）；多模态处理；Rerank 重排序；内置 RAGAS 评估；WebUI 与 REST API。

**适用场景**
法律、金融等需要全局理解与跨文档推理的垂直领域；中等规模团队希望自托管、又要能上生产的场景。README 称 30B 级别的开源模型即可跑出可接受质量，这对无法用 GPT-4 级 API 的团队很关键。

**活跃度**
39,928★ / 5,642 fork；创建 2024-10-02，最近推送 2026-09-28；star 数在同类中最高（不含代码图谱类项目）。

**优缺点**
- ✅ **工程完成度最高**：五种查询模式 + 可插拔存储 + 增量更新 + WebUI，基本是"拿来就能用"
- ✅ **成本友好**：相对 GraphRAG 显著更省，且能跑在本地开源模型上
- ✅ **更新活跃**，社区大，遇到问题容易搜到答案
- ✅ 有 EMNLP 2025 论文背书，学术上站得住
- ❌ 可插拔带来的代价是**配置项多**，新手容易被存储/模型/分块/查询模式的组合淹没
- ❌ 多后端兼容意味着**没有一条被充分压测的"黄金路径"**，某些组合的坑要靠自己踩
- ❌ 默认的 NanoVectorDB + NetworkX 只适合小规模，上量必须换 Milvus/Neo4j

---

### 3.3 OpenSPG/KAG —— 蚂蚁集团出品，走"逻辑推理"而非"相似度"路线

**核心思路**
直指传统 RAG 的两个硬伤：**知识推理相关性**与**逻辑敏感性**。向量相似度本质上是个模糊匹配器，它无法保证"检索到的证据在逻辑上真的支撑结论"。KAG 的做法是用**逻辑形式（logical form）引导推理**——把问题先转成可符号化的逻辑形式，再在知识图谱上做有约束的检索与推理，让"为什么是这个答案"变得可追溯。

**技术方案**
- **底层引擎**：OpenSPG（语义增强的可编程图，Semantic-enhanced Programmable Graph）
- **核心机制**：**互索引（mutual indexing）** —— 原文分块与知识图谱之间双向索引，既能让图节点定位回原文，也能让原文块关联到图结构
- **推理层**：混合推理引擎，符号推理（逻辑规则、图查询）与 LLM 推理协同
- **抽取层**：基于 Schema/本体的受约束抽取，而非自由抽取——这是保证专业领域严谨性的关键
- 论文：《KAG: Boosting LLMs in Professional Domains via Knowledge Augmented Generation》

**主要功能**
逻辑形式引导的检索；多跳问答；专业知识库构建；对推理链的可解释输出；面向"事实问答 + 逻辑推理"两类任务。

**适用场景**
专业领域知识库——医疗、金融、政务、能源等**不能容忍"看起来对但逻辑错"**的场景。需要先投入本体设计成本，收益是长期可维护、可审计的知识资产。

**活跃度**
9,079★ / 721 fork；创建 2024-09-21，最近推送 **2026-01-28**（约 8 个月前）；开放 Issue 173 个。

**优缺点**
- ✅ **思路最独特**：在"向量相似度"之外提供了一条符号推理路线，学术与实践价值都高
- ✅ 蚂蚁集团 + 浙江大学背景，有真实业务场景验证
- ✅ 有完整论文，方法论讲得清楚
- ❌ **最近推送距今约 8 个月，且 Issue 积压 173 个**，活跃度明显弱于 LightRAG / Graphiti 一档
- ❌ **上手门槛最高**：要先理解 SPG 建模、逻辑形式、Schema 设计，不是"pip install 就能跑"
- ❌ 强依赖本体设计质量，本体没设计好，效果可能不如朴素 RAG

---

### 3.4 getzep/graphiti —— 时序知识图谱，Agent 记忆的正确打开方式

**核心思路**
传统知识图谱有个根本缺陷：**它假设事实是静态的**。"张三住在北京"这条边一旦写入，就永远在那里——哪怕张三三个月前搬去了上海。Graphiti 的做法是给每条事实**加上有效期**：事实变更不是删除旧边，而是让旧边失效，新边生效。于是你既能查"现在张三住哪"，也能查"去年三月时系统认为张三住哪"。

**技术方案**
- **双时序模型（bi-temporal）**：同时追踪"事实在现实世界中生效的时间"和"系统录入该事实的时间"，两者分离——这是能回溯历史的关键
- **数据模型**：实体（entity）+ 关系（relation）+ 事实（fact）构成带有效期的时间图；每个事实都溯源到原始 episode（对话轮次/文档片段），保证可审计
- **本体支持**：用 Pydantic 定义自定义实体类型，约束抽取结果
- **检索**：混合检索——语义嵌入 + BM25 关键词 + 图遍历，三者融合排序
- **存储后端**：Neo4j、FalkorDB、Amazon Neptune 等

**主要功能**
增量数据整合（新信息自动与已有事实做冲突消解）；自动事实失效；亚秒级查询延迟；MCP Server 集成；面向 Agent 的实时记忆 API。

**适用场景**
需要实时交互、且需要精确历史查询的动态演化场景——客服 Agent 的客户画像、个人助理的长期记忆、需要"合规审计某时刻系统认知"的业务系统。

**活跃度**
31,317★ / 3,213 fork；创建 2024-08-08，最近推送 2026-09-29（几乎每天有提交）。⚠️ 开放 Issue **443 个**——这个数字相对其规模偏高，说明功能迭代快于 Issue 清理。

**优缺点**
- ✅ **解决了真问题**：事实时效性是被大多数 KG 项目忽视的硬需求
- ✅ **与 GraphRAG 的定位区分清晰**（README 里直接列了对比表）：GraphRAG 是批处理静态文档 + LLM 顺序总结；Graphiti 是增量更新 + 双时序追踪 + 自动失效 + 亚秒级查询
- ✅ 由 Zep 商业公司维护，有产品化压力，迭代快
- ❌ **443 个开放 Issue** 需要留意，可能意味着部分问题响应不及时
- ❌ **强绑定图数据库**，部署复杂度高于纯 Python 方案（至少要跑一个 Neo4j/FalkorDB）
- ❌ 面向 Agent 记忆设计，做"静态文档集问答"反而不如 LightRAG 直接

---

### 3.5 topoteretes/cognee —— 把图谱包装成"AI 记忆"的完整平台

**核心思路**
不把自己叫 RAG 框架，而叫**AI 记忆平台**。出发点是把"文档、代码、对话"三种异构输入统一摄入，构建成"实体 + 关系 + 可搜索分块"的知识图谱，查询时按图/向量/代码三条路自动路由召回。用四个动词定义整个生命周期：`remember` / `recall` / `improve` / `forget`。

**技术方案**
- **四操作 API**：`remember`（摄入并建图）、`recall`（检索）、`improve`（反馈优化）、`forget`（遗忘）
- **零 API Key 可跑**：无 LLM 配置时用本地 GLiNER 抽取器 + 本地嵌入模型；配置 Key 后默认走 OpenAI
- **单库部署选项**：1.0 起可把整个记忆层跑在**单个 Postgres** 上（含 PGVector 与图状态，官方标注为 demo 用途）
- **本体**：支持自定义本体与数据模型
- **生态**：多语言 SDK；可迁移 Mem0 / Zep 等既有记忆方案；MCP / Claude Code / Codex 插件

**主要功能**
跨会话持久记忆；Company Brain（企业知识大脑）；CLI 与 UI；Docker 部署；代码图与会话蒸馏。

**适用场景**
智能体长期记忆、企业知识库、代码与工单问答。特别适合已经在用 Claude Code / Cursor 这类编码 Agent、想给它们加"项目记忆"的团队。

**活跃度**
31,231★ / 3,137 fork；创建 2023-08-16，最近推送 **2026-09-30**（当天有提交，是本次调研中最活跃的项目之一）。⚠️ 开放 Issue **444 个**。

**优缺点**
- ✅ **开箱门槛最低之一**：没 API Key 也能跑，本地模型兜底，这点对评估阶段很友好
- ✅ 覆盖"摄入—检索—优化—遗忘"完整闭环，概念模型比纯检索框架更完整
- ✅ 单 Postgres 部署选项大幅降低了试用成本
- ✅ 迭代极快，且明确欢迎贡献（topics 里有 `good-first-issue`）
- ❌ **概念包装较重**："记忆平台""Company Brain"这类说法需要穿透看实质，实际能力仍是"图谱 + 向量混合检索"
- ❌ 444 个开放 Issue，功能扩张快于稳定化
- ❌ 单 Postgres 方案官方自己标注为 demo，生产环境仍需正经图库

---

### 3.6 Graphify-Labs/graphify —— star 最高，但它解决的是另一个问题

**必须说清楚**：它是本次调研中 star 最高的项目（122,559★），**但它不是文档 RAG 工具**。它把**代码库**（含文档、SQL schema、配置、PDF）转成可查询的知识图谱，形态是给 Claude Code / Cursor / Codex / Gemini CLI 用的 `/graphify` skill。

**核心思路**
**"不用向量库"是它的核心主张。** 主流代码检索靠向量相似度，但代码的结构关系（谁调用谁、谁继承谁、哪个配置影响哪个模块）是**确定的、可静态推导的**，用概率性的向量去近似它，既浪费又不可靠。所以 graphify 用本地确定性 AST 解析建图，**每一条边都能解释它是怎么来的**（"every edge explained"）。

**技术方案**
- **解析层**：本地确定性 AST 解析（tree-sitter 生态），非 LLM 抽取
- **聚类**：Leiden 算法（与 microsoft/graphrag 同源思路，但用于代码结构）
- **检索**：图查询，**无向量存储**（no vector store）
- **交付形态**：MCP + Skill，直接嵌入现有 AI 编码工具，不要求用户另起一套系统
- 默认分支为 `v8`，说明已迭代 8 个大版本

**主要功能**
代码库结构化索引；文档/SQL/配置/PDF 一并入图；边级可解释性；面向编码 Agent 的上下文供给。

**适用场景**
大型代码库的 AI 辅助开发与代码审查——让 AI 只读"真正相关"的部分，而不是把整个仓库塞进上下文。

**活跃度**
122,559★ / 11,811 fork；创建 **2026-04-03**，最近推送 2026-09-29。⚠️ 开放 Issue **1,502 个**。

**优缺点**
- ✅ **热度断层第一**，且增长极快（半年内从 0 到 12 万星）
- ✅ **技术主张清晰且反潮流**："代码关系是确定的，不该用概率方法近似"——这个论点在代码领域很有说服力
- ✅ 交付形态聪明：做成 skill/MCP，寄生在已有工作流里，没有迁移成本
- ✅ 每条边可解释，对代码审查场景是刚需
- ❌ **1,502 个开放 Issue**，是本次调研中积压最多的，需要警惕
- ❌ **与文档 RAG 不是同一赛道**：如果你的需求是"从一堆 PDF 里问答"，它帮不上忙
- ❌ 项目仅半年历史，长期维护能力尚未被验证
- ❌ 高度绑定 Claude Code / Cursor 等工具生态，工具链变化会直接影响可用性

---

### 3.7 neo4j/neo4j-graphrag-python —— 图数据库厂商的官方 SDK

**核心思路**
Neo4j 官方的 Python 包，把 GraphRAG 拆成两个可独立使用的部分：**知识图谱构建管道（KG Builder）** 和 **一组检索器（Retrievers）**。不试图提供端到端"魔法"，而是给你标准化的零件。

**技术方案**
- **检索器族**：`VectorRetriever`（纯向量）、`VectorCypherRetriever`（向量召回后用 Cypher 沿图扩展——这是最实用的一个）、`HybridRetriever`（向量 + 全文）、`Text2CypherRetriever`（自然语言转 Cypher 查询）
- **KG Builder**：从非结构化文本抽取实体关系写入 Neo4j
- **图存储抽象**：`SimpleKGPipeline` / `Neo4jGraph` 等

**主要功能**
标准化检索接口；图查询与向量检索组合；与 LangChain / LlamaIndex 生态衔接；配套教程与文档。

**适用场景**
已经（或计划）使用 Neo4j 作为主存储的团队——这时候用官方 SDK 能省掉大量胶水代码。

**活跃度**
1,295★ / 247 fork；创建 2024-02-27，最近推送 **2026-09-29**（活跃）。

**优缺点**
- ✅ **官方维护 + 更新活跃**，与 Neo4j 版本同步有保障
- ✅ `VectorCypherRetriever` 这个模式非常实用：向量负责"找入口"，Cypher 负责"沿图扩展"，是混合检索的标准解法
- ✅ 作为"零件库"而非"框架"，侵入性低，容易嵌进已有系统
- ❌ **强绑定 Neo4j**，用别的图库就不适用
- ❌ ⚠️ **许可为 NOASSERTION**（GitHub 无法识别为标准 SPDX 许可），商用前**必须自行核对 LICENSE 原文**——这类"自定义许可"经常藏有额外限制
- ❌ 不提供端到端方案，索引成本控制、提示词调优等仍要自己做

---

### 3.8 neo4j-labs/llm-graph-builder —— 上手最快的"文档 → 图谱"工具

**核心思路**
定位是 Neo4j Labs 的实验性工具，把"把非结构化文档变成知识图谱"这件事做成**有界面的应用**，降低第一次接触 GraphRAG 的门槛。

**技术方案**
- **输入**：PDF、DOC、TXT、YouTube 视频、网页
- **抽取**：LLM + LangChain 抽取节点、关系、属性
- **存储**：Neo4j 5.23+（需装 APOC 插件）
- **检索模式**：`vector`、`graph`、`graph_vector`、`fulltext`、`entity_vector`、`global_vector` 六种混合模式可选
- **模型支持**：OpenAI、Gemini、Diffbot、Anthropic、Ollama（可本地）
- **可视化**：集成 Neo4j Bloom 看图谱

**主要功能**
对话式问答并可回溯来源；图谱可视化浏览；自定义 Schema；自定义 Embedding 模型；Token 统计；支持本地 / GCP Cloud Run / Docker 部署。

**适用场景**
想快速验证"知识图谱对我这批文档有没有用"的场景；企业内部知识库原型；教学演示。

**活跃度**
5,272★ / 879 fork；创建 2024-01-11，最近推送 2026-09-16（活跃，但主语言标记为 Jupyter Notebook，说明相当部分是 notebook 形态）。

**优缺点**
- ✅ **六种检索模式可选**，可以直观对比"纯向量 vs 纯图 vs 混合"的效果差异——这是它最有价值的地方，等于一个内置的消融实验台
- ✅ 有可视化界面，非工程背景的人也能上手
- ✅ 支持 Ollama 本地模型，数据不出内网
- ❌ **Labs 实验性项目**，不是 Neo4j 官方长期支持产品，API 稳定性无保证
- ❌ 强依赖 Neo4j 5.23+ 与 APOC，环境要求不算低
- ❌ 大规模语料的索引成本与性能未在文档中给出明确指引

---

### 3.9 OSU-NLP-Group/HippoRAG —— 用海马体机制做多跳检索

**核心思路**
灵感来自人脑长期记忆的**海马体索引理论**：人不是把整段记忆存起来，而是存一个稀疏的"索引"，检索时通过索引激活相关记忆网络。落到工程上：离线用 OpenIE 抽取三元组建图，在线用 **Personalized PageRank** 在图上传播，激活与查询相关的节点集合，实现多跳关联检索。

**技术方案**
- **离线索引**：OpenIE 三元组抽取 → 构建知识图谱
- **在线检索**：Personalized PageRank 在图上做关联传播（这是它与其他项目最本质的区别——**不是沿边逐跳遍历，而是全局概率传播**）
- **模型/存储兼容**：OpenAI、Azure、vLLM、Bedrock；Qdrant、Chroma、Milvus
- **论文**：NeurIPS 2024（HippoRAG 1）；HippoRAG 2 为后续版本

**主要功能**
多跳问答；长文档理解；持续学习（新文档增量入图）；单次检索即可完成多跳（不像迭代检索那样多轮调 LLM）。

**适用场景**
多跳问答、长文档理解、需要"关联性"与"意义建构"的任务。README 明确对标 GraphRAG / RAPTOR / LightRAG，主张以**更低的成本与延迟**完成离线索引。

**活跃度**
4,033★ / 434 fork；创建 2024-05-23，最近推送 **2026-09-29**（活跃）。

**优缺点**
- ✅ **算法有理论支撑**，不是工程拼凑；Personalized PageRank 是图论里成熟且可解释的方法
- ✅ 相比迭代式多跳检索（多次调 LLM），PPR 是一次计算，**成本与延迟优势明显**
- ✅ 有 NeurIPS 论文，学术引用基础好，适合作为论文基线
- ❌ **学术项目形态**：文档与工程化程度不如 LightRAG，生产化需要自己做不少工作
- ❌ OpenIE 抽取质量直接决定图质量，对抽取模型较敏感
- ❌ 面向多跳问答优化，简单事实型问题未必比朴素 RAG 好

---

### 3.10 circlemind-ai/fast-graphrag —— 成本最低的 GraphRAG 实现（但已停滞）

**核心思路**
README 里给了一个非常具体的成本对比：用《绿野仙踪》做测试，`fast-graphrag` 花费 **$0.08**，而 `graphrag` 花费 **$0.48**——**6 倍成本差**，且数据量越大、插入次数越多，差距越明显。做法是简化索引管道 + 用 Personalized PageRank 做图探索（README 明确说受 HippoRAG 论文启发）。

**技术方案**
- 精简版 GraphRAG 管道，全异步 + 完整类型支持
- **Personalized PageRank 做图探索**定位相关信息
- 支持增量更新、检查点（checkpoint）机制避免数据损坏
- 可配置兼容 OpenAI 协议的任意 LLM 与嵌入模型

**主要功能**
领域（domain）+ 示例查询 + 实体类型三项配置即可启动；查询结果可带引用（`with_references=True`）；异步高并发。

**适用场景**
预算敏感的 GraphRAG 落地；需要低延迟的检索管道；想读一份"能看完的 GraphRAG 实现"来学习的开发者。

**活跃度**
3,955★ / 260 fork；创建 2024-10-30，**最近推送 2025-11-01**——距今约 11 个月无更新。

**优缺点**
- ✅ **成本优势有具体数字支撑**，不是空口宣称
- ✅ 代码量小、全异步、类型完整，**可读性极佳**，适合作为二次开发起点
- ✅ 明确标注了学术来源（HippoRAG），技术溯源清楚
- ❌ ⚠️ **近 11 个月无提交**，活跃度是本次重点项目中最低的之一
- ❌ 有商业托管服务（circlemind.co），开源版与商业版的能力边界需自行确认
- ❌ 功能面窄于 LightRAG，缺少存储后端可插拔、多查询模式等

---

### 3.11 xerrors/Yuxi（语析）—— 中文生态里最完整的"图谱 + 多智能体"平台

**核心思路**
把知识库检索、知识图谱、多智能体编排、MCP/Skills、沙盒工具、权限管理**放进同一个工作区**。定位是"可私有部署的多租户知识智能体平台"，目标是让每个用户拥有一个云端 Codex。

**技术方案**
- **技术栈**：LangGraph（智能体编排）、Vue（前端）、FastAPI（后端）、Milvus（向量）、Neo4j（图）、PostgreSQL、MinerU / PaddleOCR（文档解析）
- **图谱构建路径**：从 Milvus 知识库的文档块中抽取实体与关系 → 写入 Neo4j → 参与检索
- **检索**：Agentic RAG 套件，支持 Embedding/Rerank 配置与多路召回测试
- **部署**：完全基于 Docker Compose

**主要功能**
统一智能体工作台（`@` 引用知识库/文件/Skill、任务状态追踪、人工审批）；知识库与 RAG（多格式入库、切片管理、Rerank、内置 RAG 评估）；**知识图谱与知识导图**（图谱构建、子图浏览、节点详情、基于文件元数据的多层级知识导图）；多智能体与 SubAgents 并行；沙盒工作区与产物预览；多租户权限与 Dashboard。

**适用场景**
企业内网私有部署的多用户知识助手；需要权限隔离与审计的团队；中文文档为主的知识库。

**活跃度**
7,241★ / 1,151 fork；创建 2024-07-05，最近推送 2026-09-29（活跃）。MIT 许可，明确"二开友好"。

**优缺点**
- ✅ **中文项目里少见的完整度**：从文档解析、图谱构建、检索评估到权限治理全覆盖
- ✅ **MIT 协议**，商业二开友好（但需注意它引用的第三方组件各自的许可）
- ✅ 图谱能力与多智能体能力是**打通**的，不是两个独立模块拼在一起
- ✅ 内置 RAG 评估工具，能自己量化检索质量——这点很多同类平台没有
- ❌ **部署重**：Milvus + Neo4j + PostgreSQL + MinerU/PaddleOCR 一套下来资源要求不低
- ❌ 跨小版本升级需停机备份迁移（文档明确说明 v0.7.1/0.7.2 → 当前版本不能直接 `docker compose up`）
- ❌ 功能面广也意味着**单点深度可能不及专用框架**，图谱能力主要服务于知识库增强，不是 KAG 那种逻辑推理

---

### 3.12 其他值得知道的项目（简版）

| 项目 | 核心思路 | 技术方案 | 适用场景 | 活跃度 | 主要优缺点 |
|---|---|---|---|---|---|
| **gusye1234/nano-graphrag** | 极简 GraphRAG 实现，为"读懂"而非"上生产" | 千行量级的 GraphRAG 复刻，支持本地模型 | 学习 GraphRAG 内部机制；作为二次开发起点 | 3,992★；推送 2026-01-27（低频） | ✅ 代码可通读，是理解 GraphRAG 最快的路径；❌ 功能刻意精简，不适合生产 |
| **microsoft/PIKE-RAG** | 面向专业领域，用"专业知识 + 推理链"增强生成 | 分层知识抽取 + 多粒度知识组织 + 推理链构建 | 工业/医疗等专业领域问答 | 2,481★；推送 2025-09-10；**已归档** | ✅ 微软研究院的方法论有参考价值；❌ **已归档，不可用于新项目** |
| **FalkorDB/GraphRAG-SDK** | 把 GraphRAG 做成多智能体协作（本体/知识/回答三个 Agent） | FalkorDB 图数据库 + 多 Agent 架构 | 需要快速搭建 GraphRAG 应用的团队 | 1,003★；推送 2026-09-28（活跃） | ✅ 多 Agent 分工设计有想法，迭代活跃；❌ 强绑定 FalkorDB，生态相对小 |
| **automataIA/graphrag-rs** | 用 Rust 重写 GraphRAG，追求性能 | Rust 实现，可配置实体抽取 + 本地 LLM 集成 | 对性能/资源占用敏感的部署环境 | 528★；推送 2026-06-02 | ✅ 性能与部署体积优势；❌ 生态与文档远不如 Python 系 |
| **BUPT-GAMMA/PathRAG** | 用**关系路径**做剪枝，只保留真正相关的推理路径 | 基于路径的图剪枝 + 路径提示 | 多跳推理，需要抑制图噪声的场景 | 377★；推送 2025-12-17 | ✅ 思路直击"图越大噪声越多"的痛点；❌ 无 License 声明，学术代码工程化程度低 |
| **Graph-COM/SubgraphRAG** | "简单即有效"：检索子图后用轻量 MLP 打分，不依赖复杂图算法 | 子图检索 + MLP 相关性评分（ICLR 2025） | 研究基线；轻量 KGQA | 186★；推送 2025-01-27 | ✅ 方法简洁，论文结论有说服力；❌ 更新停滞，仅适合研究参考 |
| **nju-websoft/KG2RAG** | 用知识图谱**引导**检索过程本身（而非只提供检索内容） | KG 引导的检索路径规划（NAACL 2025） | 研究；KGQA | 143★；推送 2025-02-26 | ✅ 视角新颖；❌ GPL-3.0 许可对商用不友好 |
| **YZ-Cai/SimGRAG** | 把查询转成"目标子图"，在图库中找**语义相似子图** | 图语义距离度量（ACL 2025） | 研究；图结构检索 | 142★；推送 2025-07-25 | ✅ 思路适合结构化查询；❌ 无 License，工程化低 |
| **LHRLAB/HyperGraphRAG** | 用**超图**替代普通图——一条超边可连多个节点，能表达 n 元关系 | 超图结构知识表示（NeurIPS 2025） | 需要表达复杂多元关系的领域 | 453★；推送 2026-05-12 | ✅ 超图比普通图表达能力更强；❌ 相对小众，工具链与可视化支持弱 |
| **yifanfeng97/Hyper-Extract** | 一条命令把非结构化文本转成图/超图/时空抽取 | LLM 驱动的结构化抽取工具 | 抽取环节的独立组件 | 4,051★；推送 2026-09-29（活跃） | ✅ 抽取是建图的瓶颈，它专注做好这一件事，且很活跃；❌ 只做抽取，不涉及检索与生成 |
| **vitali87/code-graph-rag** | 用 Tree-sitter 解析代码建图，LLM 生成 Cypher 查询 | Tree-sitter + Memgraph + Qdrant，支持 AST 精确改写 | 单体仓库的代码问答与重构 | 5,189★；推送 2026-09-30（活跃） | ✅ 支持 13+ 语言，可叠加运行时 trace 补静态分析盲区；❌ 需 Docker + Memgraph，环境重 |
| **BaranziniLab/KG_RAG** | 把领域知识图谱接入 LLM 做知识密集型问答 | 生物医学 KG + RAG 管道 | 生物医学研究问答 | 947★；推送 2024-11-09（停滞） | ✅ 垂直领域落地范例，有配套数据集；❌ 近两年无更新 |
| **Ais1on/CTI-RAG** | 知识图谱 + 因果推理做网络威胁情报分析 | KG + 因果推理框架 | 安全分析师的情报分析 | 243★；推送 2026-05-12 | ✅ 垂直场景明确；❌ GPL-3.0，商用受限 |
| **DEEP-PolyU/Awesome-GraphRAG** | 资源策展清单 | 综述/论文/基准/项目索引 | **做文献综述或找论文时从这里起步** | 2,663★；推送 2026-06-02 | ✅ 省去大量检索时间；❌ 清单本身不提供代码 |
| **severian42/GraphRAG-Local-UI** | 给微软 GraphRAG 套一个本地 LLM 的图形界面 | Gradio UI + 本地模型 + 索引/查询/可视化多应用 | 完全离线环境下试用 GraphRAG | 2,321★；推送 **2024-11-09**（停滞近 2 年） | ✅ 离线可用；❌ **近两年无更新，微软 GraphRAG 已多次破坏性变更，极可能已不兼容** |
| **1517005260/graph-rag-agent** | 把 GraphRAG、LightRAG、Neo4j-llm-graph-builder 融合，加 DeepSearch 推理与自研评估框架 | 多框架集成 + DeepSearch + 自建评估 | 想对比多个 GraphRAG 方案的中文开发者 | 2,341★；推送 2025-11-05（停滞近 1 年） | ✅ 中文文档，集成思路有参考价值；❌ 近一年无更新，且是多框架拼接，维护负担大 |
| **pingcap/autoflow** | 基于 TiDB Serverless 向量存储的 Graph RAG 对话式知识库 | TiDB + 图 RAG | 已用 TiDB 的团队 | 2,975★；推送 2026-04-27 | ✅ 与 TiDB 生态无缝；❌ 近 5 个月无更新，且绑定 TiDB Serverless |

---

## 4. 横向对比：怎么选

### 4.1 按需求选

| 你的需求 | 首选 | 备选 | 理由 |
|---|---|---|---|
| 快速上手，要能上生产 | **LightRAG** | cognee | 工程完整度 + 活跃度 + 成本三方面最均衡 |
| 预算极紧 / 要跑本地模型 | **LightRAG**（配 Ollama） | fast-graphrag | LLM 角色分离可精细控成本；fast-graphrag 更省但已停滞 |
| 需要逻辑严谨、可审计 | **OpenSPG/KAG** | — | 唯一走符号推理路线的，但需接受低活跃度与高门槛 |
| 做 Agent 长期记忆 | **Graphiti** | cognee | 双时序模型是刚需级差异，且迭代最活跃 |
| 已有 Neo4j | **neo4j-graphrag-python** | llm-graph-builder | 官方 SDK，`VectorCypherRetriever` 直接可用 |
| 要可视化、给人演示 | **llm-graph-builder** | Yuxi | 有 Bloom 图可视化，六种检索模式可现场对比 |
| 企业内网、多用户、要权限 | **Yuxi** | cognee | 唯一把多租户权限做完整的 |
| 代码库问答 | **code-graph-rag** | graphify | 前者成熟可自托管；后者热度高但绑定 AI 编码工具生态 |
| 做论文 / 需要基线 | **HippoRAG** | SubgraphRAG、KG2RAG | 有顶会论文，算法可解释，适合做对照 |
| 做文献综述 | **Awesome-GraphRAG** | — | 策展清单，省检索时间 |

### 4.2 关键指标对照

| 项目 | Stars | 最近更新 | Issue 积压 | 许可风险 | 生产就绪度 |
|---|---|---|---|---|---|
| graphify | 122,559 | 2026-09-29 | ⚠️ 1,502 | 低（Apache-2.0） | 中（太新） |
| ragflow | 91,522 | 2026-09-30 | ⚠️ 1,559 | 低 | 高（但图谱非核心） |
| LightRAG | 39,928 | 2026-09-28 | 中 | 低（MIT） | **高** |
| microsoft/graphrag | 36,169 | 2026-09-28 | 低（47） | 低（MIT） | ⚠️ 维护模式 |
| graphiti | 31,317 | 2026-09-29 | ⚠️ 443 | 低（Apache-2.0） | 高 |
| cognee | 31,231 | 2026-09-30 | ⚠️ 444 | 低（Apache-2.0） | 中高 |
| KAG | 9,079 | 2026-01-28 | ⚠️ 173 | 低（Apache-2.0） | 中 |
| Yuxi | 7,241 | 2026-09-29 | — | 低（MIT） | 中高 |
| llm-graph-builder | 5,272 | 2026-09-16 | — | 低（Apache-2.0） | 中（Labs 项目） |
| HippoRAG | 4,033 | 2026-09-29 | — | 低（MIT） | 中（学术） |
| fast-graphrag | 3,955 | 2025-11-01 | — | 低（MIT） | ⚠️ 停滞 |
| PIKE-RAG | 2,481 | 2025-09-10 | — | 低（MIT） | ❌ 已归档 |
| neo4j-graphrag-python | 1,295 | 2026-09-29 | — | ⚠️ **NOASSERTION** | 高 |

---

## 5. 三条值得单独记住的结论

### 5.1 热度与成熟度严重脱钩

本次调研中最反直觉的发现：**star 数最高的两个项目（graphify 122,559、ragflow 91,522）都不是"知识图谱 RAG 框架"**。

- `graphify` 是代码知识图谱，**明确不用向量库**，解决的是 AI 编码工具的上下文问题；
- `ragflow` 是通用 RAG 平台，图谱只是它众多能力之一（且其 topics 已不含 knowledge-graph，重心转向 Agentic 检索）。

**给选型的启示**：不要按 star 排序选型。真正在"文档 + 知识图谱 + RAG"这个交叉点上做得最深的，是 star 排在第 3–6 位的 LightRAG、microsoft/graphrag、graphiti、cognee。

### 5.2 官方项目会消失，活跃度会断崖

- `aws-samples/graphrag-toolkit` 抓取时返回 **404**，仓库已不可访问；
- `microsoft/graphrag` 官方宣布**进入维护模式**，不再接受新功能；
- `microsoft/PIKE-RAG`、`Azure-Samples/graphrag-accelerator` 均已**归档**；
- `fast-graphrag`、`GraphRAG-Local-UI`、`graph-rag-agent` 停更 1–2 年。

**给选型的启示**：这个领域的项目死亡率不低。选型时**"最近推送时间"的权重要高于 star 数**。同时，优先选存储/模型可插拔的项目（如 LightRAG），万一上游停更，迁移成本较低。

### 5.3 三个许可陷阱

| 项目 | 问题 | 建议 |
|---|---|---|
| `neo4j-graphrag-python` | 许可为 **NOASSERTION**（GitHub 无法识别） | 商用前**必须读 LICENSE 原文** |
| `PathRAG`、`SimGRAG` | **无 License 声明** | 默认视为"保留所有权利"，不可商用 |
| `KG2RAG`、`CTI-RAG`、`ontologyRAG` | **GPL-3.0** | 传染性许可，闭源商用需谨慎评估 |

---

## 6. 数据来源与复核方法

本报告所有量化数据均可复核：

- **仓库元数据**（stars / forks / pushed_at / created_at / archived / license）
  → GitHub REST API：`https://api.github.com/repos/{owner}/{repo}`
- **检索结果集**（total_count / 命中仓库）
  → `https://api.github.com/search/repositories?q={query}`
- **技术方案描述**
  → 各仓库 `README.md` 原文（`raw.githubusercontent.com/{owner}/{repo}/main/README.md`）
- **数据截止时间**：2026-09-30

**⚠️ 时效性提醒**：GitHub 数据每天都在变，尤其 star 数与最近推送时间。如果本报告用于正式场合（论文、立项、对外汇报），建议在使用前用上述 API 重新拉取一次核对。

---

*报告结束。共覆盖 26 个项目，其中 12 个重点详解。*

---

# 7. 补充复核（2026-10-02）：到底哪些是"成熟"项目

主报告是按 star 数排序的，这带来一个误导：**star 是"收藏"，不等于"在用"。**
本节引入 PyPI 真实下载量作为第二把尺子，重新回答"有没有已经成熟的、可以直接用的项目"。

## 7.1 决定性证据：star 数与真实使用量严重脱钩

数据取自 `pypistats.org/api/packages/{pkg}/recent`，抓取时间 2026-10-02。

| 包名 | 对应项目 | **月下载量** | 周下载量 | Stars | 每 star 换来的月下载 |
|---|---|---|---|---|---|
| `graphiti-core` | getzep/graphiti | **608,917** | 139,928 | 31,317 | 19.4 |
| `neo4j-graphrag` | neo4j/neo4j-graphrag-python | **297,241** | 75,245 | 1,295 | **229.5** |
| `lightrag-hku` | HKUDS/LightRAG | **126,199** | 28,160 | 39,928 | 3.2 |
| `cognee` | topoteretes/cognee | **87,864** | 21,353 | 31,231 | 2.8 |
| `graphrag` | microsoft/graphrag | **47,564** | 13,967 | 36,169 | 1.3 |
| `nano-graphrag` | gusye1234/nano-graphrag | **789** | 237 | 3,992 | **0.20** |
| `fast-graphrag` | circlemind-ai/fast-graphrag | **477** | 106 | 3,955 | **0.12** |

**这张表推翻了三件事：**

1. **`nano-graphrag` 和 `fast-graphrag` 的 star 是"虚假繁荣"。**
   两者都有约 4,000 star，但月下载分别只有 789 和 477——**每 5 个 star 才换来 1 次下载**。
   说明它们被大量"收藏学习"但极少真正投入使用。主报告里我把它们列为"学习用"是对的，
   但当时我没有硬证据，现在有了。

2. **`neo4j-graphrag-python` 被严重低估。** 只有 1,295 star（在主报告里排到第 9 位），
   但月下载 297,241，**每 star 换来 229 次下载**，是全部项目中最高的。
   原因不难理解：它是 SDK，会被其他工具当作依赖自动拉取，真实使用量远大于 GitHub 关注度。
   **如果你已经在用 Neo4j，它其实是被低估的稳妥选择。**

3. **`graphiti` 是实际使用量第一。** 月下载 60 万，是第二名 Neo4j SDK 的 2 倍、
   是 LightRAG 的 4.8 倍。它的"时序知识图谱 + Agent 记忆"定位命中了真实需求。

> ⚠️ **关于下载量的局限，必须说明**：PyPI 下载数会包含 CI 流水线、镜像站同步、
> Docker 构建等非人类调用，因此**不能当作精确的用户数**。
> 但 `477` 与 `608,917` 之间是 **1,276 倍**的差距，这个量级的差异不可能由 CI 噪声解释。
> 用它做**相对排序**是可靠的，用它做**绝对用户估计**不可靠。

## 7.2 版本发布节奏（第二个成熟度信号）

只发 main 分支、不打版本号的项目，说明作者没打算让人稳定依赖它。以下取自 GitHub Releases API：

| 项目 | 最近 5 个版本 | 发布节奏 |
|---|---|---|
| **cognee** | v1.6.2 (09-29)、v1.6.1 (09-24)、v1.6.0 (09-18)、v1.5.4 (09-04) | **9 月发了 4 个版本，最快** |
| **neo4j-graphrag-python** | 1.22.0 (10-01)、1.21.0 (09-23)、1.19.0 (08-26)、1.18.0 (06-24) | 约每月一版，稳定 |
| **graphiti** | v0.30.2 (09-08)、v0.30.0 (09-01)、v0.29.3 (07-27)、v0.29.2 (06-08) | 约 2–6 周，稳定 |
| **LightRAG** | v1.5.7 (09-02)、v1.5.6 (08-06)、v1.5.5 (07-31) | 约每月一版 |
| **microsoft/graphrag** | v3.2.0 (09-24)、v3.1.2 (08-21)、v3.1.1 (07-18)、v3.1.0 (05-28) | 仍在发版，但已宣布不再加新功能 |

## 7.3 成熟度四档结论

把「下载量 + 发布节奏 + 维护状态 + 许可」四条合起来看，26 个项目可以分成四档：

### ✅ 第一档：成熟，可以直接用于生产（4 个）

| 项目 | 为什么算成熟 | 适合谁 |
|---|---|---|
| **Graphiti** | 月下载 60 万（第一）；版本节奏稳定；Zep 商业公司维护；近 30 天持续提交 | 要做 Agent 长期记忆、需要"某时刻真相"可回溯 |
| **neo4j-graphrag-python** | 月下载 29.7 万；每月发版；Neo4j 官方维护；`VectorCypherRetriever` 可直接用 | 已经在用或打算用 Neo4j |
| **LightRAG** | 月下载 12.6 万；约每月发版；有 EMNLP 2025 论文；存储可插拔 | 通用文档知识库，要能自托管 |
| **cognee** | 月下载 8.8 万；9 月发 4 版（最快）；可零 API Key 试用 | 想低成本先验证效果，或要企业知识大脑 |

**这四个是本次调研中唯一可以称为"成熟产品"的。** 它们的共同点：
有版本号、有下载量、有近期提交、有文档。

### ⚠️ 第二档：成熟但有限制（3 个）

| 项目 | 限制 |
|---|---|
| **microsoft/graphrag** | 月下载 4.8 万，仍在发版（v3.2.0），但官方已宣布**维护模式、不再接受新功能 PR**。可以用，但别指望它变好 |
| **RAGFlow** | 91,522 star，非常成熟——但**知识图谱只是它众多能力之一**，其 topics 已不含 knowledge-graph，重心转向 Agentic 检索 |
| **Yuxi（语析）** | 功能完整、MIT 许可、更新活跃，但**部署很重**（Milvus + Neo4j + PostgreSQL + MinerU 一套），且跨小版本升级要停机迁移 |

### 🔶 第三档：能跑，但属于"研究/原型"而非"产品"（3 个）

| 项目 | 判断依据 |
|---|---|
| **OpenSPG/KAG** | 方法最有价值（符号推理路线），但**最近提交距今 8 个月**、Issue 积压 173、未上 PyPI 正式包 |
| **HippoRAG** | 有 NeurIPS 论文，算法扎实，但学术项目形态，工程化需自己补 |
| **llm-graph-builder** | Neo4j **Labs 实验性**项目，API 稳定性无保证 |

### ❌ 第四档：不建议用于任何实际项目（4 类）

| 项目 | 原因 |
|---|---|
| **nano-graphrag**（789 下载/月）、**fast-graphrag**（477 下载/月） | 学习价值高，实际使用量极低，且 fast-graphrag 已停更 11 个月 |
| **microsoft/PIKE-RAG**、**Azure-Samples/graphrag-accelerator** | 已归档 |
| **severian42/GraphRAG-Local-UI** | 停更近 2 年，且上游 GraphRAG 已多次破坏性变更，**极可能已不兼容** |
| **PathRAG / SimGRAG**（无 License）、**KG2RAG / CTI-RAG / ontologyRAG**（GPL-3.0）、**neo4j-graphrag-python**（NOASSERTION） | 许可风险，商用前必须逐一核对 LICENSE 原文 |

## 7.4 一句话回答"有没有成熟的项目"

**有，但只有四个：Graphiti、neo4j-graphrag-python、LightRAG、cognee。**

如果你要的是"现在就能拿来做东西"，从这四个里选：

- **要 Agent 记忆** → Graphiti（实际使用量第一）
- **已经在用 Neo4j** → neo4j-graphrag-python（被低估，每 star 换 229 次下载）
- **通用文档知识库** → LightRAG（综合最均衡）
- **想先零成本验证** → cognee（无 API Key 也能跑）

**其余 22 个项目，要么定位不符（代码图谱 / 通用 RAG 平台），要么是研究原型，要么已经死了。**
主报告里的 star 排名请只当作"关注度参考"，不要当作选型依据。

---

*报告结束。主报告覆盖 26 个项目；§7 补充复核引入 PyPI 下载量与版本节奏，给出成熟度四档结论。*

---

# 8. 效果证据（2026-10-02 补充）：这套流程真的有效吗

前七节回答的是"**有什么项目**"，但没回答一个更根本的问题：**知识图谱 + RAG 这套流程，到底有没有用？**

这一节用三篇论文的实证数据回答。结论比想象中复杂——**它跑通了，但它经常输。**

## 8.1 三篇关键论文

| 论文 | 出处 | 作用 |
|---|---|---|
| **When to use Graphs in RAG**（提出 GraphRAG-Bench） | **ICLR 2026**，厦门大学 + 香港理工大学，arXiv:2506.05690 | 直接回答"什么时候该用图"，评测 11 种框架 |
| **RAG vs. GraphRAG: A Systematic Evaluation and Key Insights** | arXiv:2502.11371 | 通用文本任务上的系统对比，含混合策略实验 |
| **From Local to Global: A Graph RAG Approach to Query-Focused Summarization** | 微软，arXiv:2404.16130 | GraphRAG 的原始论文 |

## 8.2 最重要的那句话

GraphRAG-Bench 的摘要里有一句很重的话，值得逐字引用：

> *"Despite its conceptual promise, recent studies report that **GraphRAG frequently underperforms vanilla RAG on many real-world tasks**. This raises a critical question: Is GraphRAG really effective, and in which scenarios do graph structures provide measurable benefits?"*

翻译过来就是：**尽管概念上很有前景，但近期研究显示，GraphRAG 在很多真实任务上频繁输给朴素 RAG。**

**具体差多少（来自论文引用的既有研究）：**

| 场景 | GraphRAG 相对朴素 RAG 的变化 |
|---|---|
| Natural Questions 问答准确率 | **−13.4%** |
| 时效性查询（需实时知识更新）准确率 | **−16.6%** |
| HotpotQA 多跳推理深度 | +4.5%（但延迟 **×2.3**） |

**所以"跑通了"和"总是更好"是两件事。** 下面这张表才是真正有用的东西。

## 8.3 适用边界：四级任务，谁赢

GraphRAG-Bench 把任务按难度分成四级，结论非常清晰：

| 难度级别 | 任务类型 | 谁赢 | 原因 |
|---|---|---|---|
| **Level 1** | 事实检索 | **传统 RAG 胜** | 图结构引入的冗余/噪声反而干扰简单查询 |
| **Level 2** | 复杂推理（多跳） | **GraphRAG 胜** | 图的遍历能力天然适合跨文档逻辑合成 |
| **Level 3** | 上下文摘要 | **GraphRAG 胜** | 图结构提供更好的上下文连贯性 |
| **Level 4** | 创意生成 | **五五开** | GraphRAG 事实保真度高，但传统 RAG 覆盖面广 |

**具体准确率数字（生成评估，GPT-4o-mini）：**

| 任务 | 数据集 | 朴素 RAG | 最佳 GraphRAG | 差距 |
|---|---|---|---|---|
| 事实检索 ACC | 小说 | **60.92** | 60.14（HippoRAG2） | −0.78 |
| 事实检索 ACC | 医学 | 64.73 | **66.28**（HippoRAG2） | +1.55 |
| **复杂推理 ACC** | 小说 | 42.93 | **53.38**（HippoRAG2） | **+10.45** |
| **复杂推理 ACC** | 医学 | 58.64 | **61.98**（HippoRAG2） | **+3.34** |
| 复杂推理 ROUGE-L | 小说 | 15.39 | **33.42**（HippoRAG2） | **+18.03** |
| 上下文摘要 ACC | 医学 | 65.75 | **67.88**（Fast-GraphRAG） | +2.13 |
| 创意生成 Faithfulness | 医学 | 58.72 | **65.56**（HippoRAG） | +6.84 |

**读法**：在事实检索上基本打平甚至略输；**在复杂推理上领先 3–10 个百分点，ROUGE-L 翻倍**。这就是它的价值区间。

## 8.4 成本账：token 膨胀 1–2 个数量级

数据来自论文 Figure 8 / Figure 9（逐字照录）：

| 方法 | 小说集平均 token | 医学集平均 token | 相对 V-RAG |
|---|---|---|---|
| **V-RAG（朴素 RAG）** | **879** | **954** | 1× |
| HippoRAG2 | 1,008 | 1,020 | ≈1.1× |
| RAPTOR | 3,441 | 3,510 | ≈3.9× |
| Fast-GraphRAG | 4,204 | 4,298 | ≈4.8× |
| HippoRAG | 7,208 | 7,342 | ≈8.2× |
| **LightRAG** | **100,832** | **100,310** | **≈115×** |
| MS-GraphRAG (local) | 38,707 | 39,821 | ≈44× |
| **MS-GraphRAG (global)** | **331,375** | **332,881** | **≈377×** |

> ⚠️ **一处原文自相矛盾，值得记录**：论文正文 Obs.8 描述 MS-GraphRAG(global) 时说
> "reaches a prompt size of up to **4×10⁴** tokens"，但它自己的 Figure 8 表格里写的是
> **331,375**（即 ≈3.3×10⁵）。两者差了一个数量级。
> 本报告采用**表格数值**（表格是原始测量结果）。这也提醒：**同一篇论文里的数字也要交叉核对。**

**这条对选型影响极大**：`HippoRAG2`（≈1.1×）和 `MS-GraphRAG(global)`（≈377×）在成本上差了 **300 多倍**，但前者在复杂推理上反而表现最好。**"用 GraphRAG"不等于"用最贵的那个"。**

## 8.5 一个反直觉但对你可能最重要的发现：语料越大，图越有价值

论文测试了语料规模扩展的影响：

| 语料规模 | 传统 RAG 复杂推理准确率 | GraphRAG（HippoRAG2） |
|---|---|---|
| 56k tokens | 58.64% | 稳定 |
| **1,132k tokens**（约 20 倍） | **43.20%（暴跌 15.4 个点）** | **保持稳定** |

**原因**：语料一大，向量检索容易被"高相似度但无关"的噪声淹没；而图结构靠显式的实体/三元组匹配，能有效过滤噪声。

**这是一条有明确触发条件的结论**：如果你的知识库还小，图的价值有限；如果知识库在持续增长，图的价值会随之上升。

## 8.6 三个必须知道的坑

**坑 1：只从图里检索是短板。** 论文计算了答案实体在构建出的 KG 中的存在比例——**Hotpot 数据集仅 65.8%，NQ 数据集仅 65.5%**。也就是说，**有约 1/3 的答案实体压根没被抽进图里**。所以正确做法是"图 + 原文"混合，不能只建图不保留原文（KG-based 变体在 NQ 上 F1 只有 34.28，而朴素 RAG 是 64.78——**几乎腰斩**）。

**坑 2：Global search 容易幻觉。** 只检索高层社区摘要会丢失细节。在 MultiHop-RAG 的 Null 查询（本应回答"信息不足"）上，Community-GraphRAG (Global) 准确率仅 **19.27%**，而朴素 RAG 是 96.01%。**它会在没有答案时硬编一个出来。**

**坑 3：LLM-as-a-Judge 有严重位置偏差。** 论文发现，**仅仅调换两个答案的呈现顺序，评判结论就会反转**。这意味着很多"GraphRAG 比 RAG 好"的结论可能不可靠。

> 💡 **坑 3 对你有直接参考价值**：你做生成评测时用的就是模型判定 + 配对统计。
> 位置偏差是这类评测的系统性风险，值得在设计实验时显式控制（例如交换顺序各判一次）。

## 8.7 一句话总结

**知识图谱 + RAG 这套流程：学术上已跑通、工程上有 4 个成熟实现、生产上有真实用量——
但它在事实检索上打平甚至略输，代价是 1–2 个数量级的 token 膨胀，价值集中在复杂推理与上下文摘要。**

**它是手术刀，不是万金油。**

---

*报告结束。主报告覆盖 26 个项目；§7 补充 PyPI 下载量与成熟度四档；§8 补充效果实证与适用边界。*
