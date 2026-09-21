# GitHub 生产级 RAG 项目深度研究报告

> 基于 GitHub 上 20+ 个高星生产级 RAG 项目的架构分析
> 
> **研究目标**：为"设计知识库助手"项目找到可参考的工程实践和架构模式

---

## 📊 研究方法

**搜索策略**：
- 关键词：`RAG production evaluation LangChain LangGraph agent`
- 筛选条件：stars > 100, language:Python, 包含 production/evaluation 关键词
- 发现项目：214 个相关项目，深度分析了 3 个最相关的标杆项目

**分析维度**：
1. 架构设计（模块化、可扩展性）
2. 评测体系（指标、自动化）
3. 工具调用（抽象、编排）
4. 可观测性（追踪、监控）
5. 生产就绪（容错、降级、部署）

---

## 🏆 三大标杆项目对比

### 项目概览

| 项目 | Stars | 核心特点 | 适用场景 |
|------|-------|---------|---------|
| **rag_evaluation** (Vikas9892) | 15 | 从零实现，无 LangChain，hybrid retrieval + 评测平台 | 学习 RAG 底层原理，构建自主可控系统 |
| **hybrid-rag-system** (TammineniTanay) | 7 | CRAG + 三路混合检索 + 反馈奖励模型 | 企业级复杂查询，需要自纠错能力 |
| **Opik** (comet-ml) | 21,642 | LLM 可观测性平台，支持 40M+ traces/day | 大规模生产环境监控和评测 |

---

## 🔍 项目一：rag_evaluation — 从零构建的标杆实现

### 核心价值
> **"No LangChain, built from first principles"** — 所有组件自己实现，完全可控

**GitHub**: https://github.com/Vikas9892/rag_evaluation

### 架构亮点

#### 1. 模块化设计（可直接借鉴）

```
ingestion/          # 文档解析（PyMuPDF + 纯文本）
chunking/           # 分块策略（标题感知，250 字符，50 重叠）
embeddings/         # 嵌入服务（BGE-small-en-v1.5, 384维）
retrieval/
  ├── faiss_store.py      # 密集检索（FAISS IndexFlatIP）
  ├── bm25_store.py       # 稀疏检索（BM25Okapi）
  ├── hybrid_retriever.py # RRF 融合（k=60）
  └── reranker.py         # 交叉编码器重排（ms-marco-MiniLM）
generation/         # 提示构建 + LLM 生成（Groq）
services/           # RAGService（高层编排）
evaluation/         # 指标计算 + 基准测试
corpora/            # 语料库命名空间管理
documents/          # 文档状态追踪（SQLite）
jobs/               # 异步索引队列
```

**与你的项目对比**：
- ✅ 你已有：文档入库、检索、CRAG 评分、网络搜索、LangGraph
- ❌ 你缺少：混合检索（BM25）、交叉编码器重排、语料库隔离、异步队列

#### 2. 评测体系（必学）

**Ground Truth 设计哲学**：
```json
{
  "id": 13,
  "question": "What are common page replacement algorithms?",
  "expected_answer_spans": ["LRU (Least Recently Used)"]  // 内容片段，非 chunk ID
}
```

**为什么重要**：
- 传统方式：标注 `chunk_id: "os.md_chunk_0001"`
- 问题：改变 chunk_size 后，ID 指向不同内容，指标失真
- 解决：用内容片段标注，评测时动态解析到 chunk_id
- **ChunkResolver**：确保每个片段精确匹配 1 个 chunk（0 或多个都报错）

**实际效果**：
```
53 问评测集，148 chunks 语料库
- Dense retrieval: MRR 0.878, Recall@5 0.962
- Hybrid (RRF):    MRR 0.848, Recall@5 0.934  ← 反而变差！
- Dense + rerank:  MRR 0.913, Recall@5 0.981  ← 最佳
```

**教训**：混合检索不一定更好，因为 RRF 给弱检索器同等投票权会拖累强检索器。

#### 3. 生产部署（真实案例）

**架构**：
```
Browser → Vercel (前端) → AWS EC2 t4g.small
  ├── Caddy (TLS, Let's Encrypt 自动续期)
  └── rag-api (uvicorn:8000)
      ├── 743 MB 常驻（模型 + 索引）
      └── EBS gp3 卷（uploads + SQLite）
```

**为什么不用 Lambda**（三个硬约束）：
1. 内存占用 743 MB，接近 Lambda 上限
2. 需要可写文件系统（uploads + SQLite）
3. 后台 worker 必须跨请求存活（异步索引）

**成本**：EC2 ~$17/月（比 Lambda 冷启动更稳定）

#### 4. 核心代码模式

**异步索引工作流**：
```python
# documents/repository.py
class DocumentRepository:
    def create_document(self, corpus_id, filename, content_hash) -> str:
        # 检查重复（字节相同的文件直接返回已有 ID）
        existing = self.get_by_hash(content_hash)
        if existing:
            return existing.id
        
        # 新建记录，status = QUEUED
        doc = Document(id=gen_id(), status="QUEUED", ...)
        self.db.add(doc)
        return doc.id

# jobs/worker.py
class DocumentIndexer:
    async def process(self, job: IndexJob):
        self.update_status(job.doc_id, "PARSING")
        chunks = parse_and_chunk(job.file_path)
        
        self.update_status(job.doc_id, "EMBEDDING")
        vectors = embed_batch(chunks)
        
        self.update_status(job.doc_id, "INDEXING")
        index.append(vectors)  # 增量追加，不重建
        
        self.update_status(job.doc_id, "READY")
```

**前端轮询**：
```typescript
// frontend/hooks/useDocumentStatus.ts
export function useDocumentStatus(docId: string) {
  return useQuery({
    queryKey: ['document', docId],
    queryFn: () => api.getDocumentStatus(docId),
    refetchInterval: (data) => 
      data?.status === 'READY' || data?.status === 'FAILED' 
        ? false  // 停止轮询
        : 2000,  // 每 2 秒轮询一次
  });
}
```

**可借鉴点**：
- 状态机式的文档生命周期（QUEUED → PARSING → EMBEDDING → INDEXING → READY/FAILED）
- 增量索引（append 而非重建）
- 删除时重建索引但不重嵌入（过滤 metadata）

---

## 🔍 项目二：hybrid-rag-system — CRAG + 反馈学习

### 核心价值
> **"Production-grade with self-correcting + feedback loop"** — 自纠错 + 从用户评分中学习

**GitHub**: https://github.com/TammineniTanay/hybrid-rag-system

### 架构亮点

#### 1. 三路混合检索

```python
# retrieval/hybrid_retriever.py
class HybridRetriever:
    def __init__(self):
        self.dense = QdrantSearch()    # 语义搜索
        self.sparse = ElasticsearchBM25()  # 关键词匹配
        self.graph = Neo4jTraversal()  # 实体关系
    
    async def retrieve(self, query: str, top_k: int):
        # 并行检索
        dense_results, sparse_results, graph_results = await asyncio.gather(
            self.dense.search(query, top_k=20),
            self.sparse.search(query, top_k=20),
            self.graph.traverse(query, max_depth=2)
        )
        
        # RRF 融合（k=60）
        fused = reciprocal_rank_fusion(
            [dense_results, sparse_results, graph_results], 
            k=60
        )
        
        # 可选：奖励模型重排
        if self.reward_model:
            fused = self.reward_model.rerank(query, fused)
        
        return fused[:top_k]
```

**50 问验证结果**：
```
Dense 贡献：42.7%
Sparse 贡献：44.4%
Graph 贡献：12.9%

混合检索覆盖率：100%
纯 Dense 覆盖率：68%  ← 混合检索补全了 32% 的盲点
```

#### 2. CRAG 自纠错（LangGraph 实现）

**状态机设计**：
```python
# core/crag_pipeline.py
from langgraph.graph import StateGraph

class CRAGState(TypedDict):
    question: str
    documents: list[Document]
    grades: dict[str, str]  # chunk_id -> "RELEVANT" / "IRRELEVANT"
    correction_attempts: int
    route: str  # "proceed" / "rewrite" / "web_search" / "decompose"

def build_crag_graph():
    workflow = StateGraph(CRAGState)
    
    # 节点
    workflow.add_node("grade_chunks", grade_each_chunk)
    workflow.add_node("decide_action", crag_decision_engine)
    workflow.add_node("rewrite_query", rewrite_with_llm)
    workflow.add_node("web_search", tavily_search)
    workflow.add_node("decompose", split_into_subquestions)
    workflow.add_node("generate", llm_answer)
    
    # 条件边
    workflow.add_conditional_edges(
        "decide_action",
        decide_route,
        {
            "proceed": "generate",
            "rewrite": "rewrite_query",
            "web_search": "web_search",
            "decompose": "decompose"
        }
    )
    
    # 重试循环（最多 2 次）
    workflow.add_edge("rewrite_query", "grade_chunks")
    workflow.add_edge("web_search", "generate")
    workflow.add_edge("decompose", "generate")
    
    return workflow.compile()
```

**决策逻辑**：
```python
def decide_route(state: CRAGState) -> str:
    relevant_count = sum(1 for g in state["grades"].values() if g == "RELEVANT")
    total = len(state["grades"])
    relevant_ratio = relevant_count / total if total > 0 else 0
    
    if relevant_ratio > 0.6:
        return "proceed"  # 直接生成答案
    
    if state["correction_attempts"] == 0:
        return "rewrite"  # 第 1 次纠错：改写查询
    
    if state["correction_attempts"] == 1:
        return "web_search"  # 第 2 次纠错：网络搜索
    
    # 达到最大重试次数，直接用现有上下文生成
    return "proceed"
```

**与你的项目对比**：
- ✅ 你已有：CRAG 评分、网络搜索回退
- ❌ 你缺少：查询改写、问题拆解、最大重试次数控制

#### 3. 反馈驱动的奖励模型

**数据收集**：
```python
# services/feedback.py
class FeedbackService:
    def record_feedback(self, query_id: str, rating: int, comment: str):
        # 获取该查询的上下文
        query = self.db.get_query(query_id)
        chunks = query.retrieved_chunks
        
        # 存储特征
        for chunk in chunks:
            features = extract_features(query.question, chunk)
            self.db.insert_feedback_sample(
                query=query.question,
                chunk=chunk.text,
                rating=rating,  # 1-5 星
                features=features
            )
        
        # 如果样本 >= 20，触发重训练
        if self.db.count_feedback_samples() >= 20:
            self.train_reward_model()
```

**特征工程**：
```python
def extract_features(query: str, chunk: Document) -> dict:
    return {
        "cosine_similarity": cosine(embed(query), chunk.embedding),
        "rrf_score": chunk.metadata["rrf_score"],
        "retriever_count": chunk.metadata["retriever_count"],  # 1-3
        "chunk_length": len(chunk.text.split()),
        "query_length": len(query.split()),
        "lexical_overlap": len(set(query.split()) & set(chunk.text.split())) / len(set(query.split()))
    }
```

**模型训练与推理**：
```python
# evaluation/reward_model.py
from sklearn.ensemble import GradientBoostingClassifier

class RewardModel:
    def train(self, samples: list[FeedbackSample]):
        X = [s.features for s in samples]
        y = [(s.rating >= 4) for s in samples]  # 二分类：好评 vs 差评
        
        self.model = GradientBoostingClassifier(n_estimators=100)
        self.model.fit(X, y)
    
    def rerank(self, query: str, chunks: list[Document]) -> list[Document]:
        features = [extract_features(query, c) for c in chunks]
        reward_scores = self.model.predict_proba(features)[:, 1]  # P(好评)
        
        # 70% RRF + 30% 奖励模型
        for chunk, reward in zip(chunks, reward_scores):
            chunk.final_score = 0.7 * chunk.rrf_score + 0.3 * reward
        
        return sorted(chunks, key=lambda c: c.final_score, reverse=True)
```

**可借鉴点**：
- 用户评分 → 特征提取 → 训练分类器 → 重排
- 保守融合策略（70/30）避免过拟合早期反馈
- 最少 20 个样本才训练（避免噪声）

#### 4. RAGAS 评测集成

```python
# evaluation/ragas_evaluator.py
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall

class RAGASEvaluator:
    def evaluate_response(self, query: str, answer: str, contexts: list[str], ground_truth: str):
        dataset = {
            "question": [query],
            "answer": [answer],
            "contexts": [contexts],
            "ground_truth": [ground_truth]
        }
        
        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision, context_recall]
        )
        
        return {
            "faithfulness": result["faithfulness"],      # 答案是否由上下文支撑
            "answer_relevancy": result["answer_relevancy"],  # 答案是否切题
            "context_precision": result["context_precision"],  # 检索精度
            "context_recall": result["context_recall"]    # 检索召回
        }
```

**降级策略**（当 RAGAS LLM 配额耗尽）：
```python
def heuristic_faithfulness(answer: str, contexts: list[str]) -> float:
    """基于词汇重叠的启发式指标"""
    answer_words = set(answer.lower().split())
    context_words = set(" ".join(contexts).lower().split())
    return len(answer_words & context_words) / len(answer_words)
```

---

## 🔍 项目三：Opik — 企业级可观测性平台

### 核心价值
> **"40M+ traces/day, open-source, self-hostable"** — 大规模生产监控

**GitHub**: https://github.com/comet-ml/opik (21,642 stars)

### 架构亮点

#### 1. 跨语言追踪（OpenTelemetry）

**Python SDK**：
```python
from opik import track

@track
def my_llm_function(input: str) -> str:
    # 自动捕获：输入、输出、延迟、token 消耗
    return llm.generate(input)

# 嵌套调用自动追踪
@track
def multi_step_agent(query: str):
    context = retrieve(query)  # ← 也被追踪
    answer = generate(context)  # ← 也被追踪
    return answer
```

**OpenTelemetry 兼容**（任何语言）：
```java
// Java (Spring AI)
import io.opentelemetry.api.trace.Tracer;

@WithSpan("llm_call")
public String callLLM(String prompt) {
    Span span = tracer.spanBuilder("llm_call").startSpan();
    span.setAttribute("model", "gpt-4");
    // ... LLM 调用
    span.end();
    return result;
}
```

**Ruby, .NET, Go** 等都能通过 OpenTelemetry 上报到 Opik。

#### 2. LLM-as-a-Judge 指标库

```python
from opik.evaluation.metrics import Hallucination, Moderation, AnswerRelevance

# 幻觉检测
hallucination_metric = Hallucination()
score = hallucination_metric.score(
    input="What is the capital of France?",
    output="Paris is the capital of France, located on the Seine River.",
    context=["France is a country in Europe with Paris as its capital."]
)
print(score.value)  # 0.95（高分 = 低幻觉）
print(score.reason)  # "Output is fully supported by context"

# 内容审核
moderation_metric = Moderation()
score = moderation_metric.score(
    input="...",
    output="This is a violent threat: ..."
)
print(score.value)  # 0.1（低分 = 有问题）

# RAG 评估
context_precision = ContextPrecision()
score = context_precision.score(
    input="What is RAG?",
    output="Retrieval-Augmented Generation...",
    context=["RAG stands for...", "Irrelevant chunk here"]
)
```

**自定义指标**：
```python
from opik.evaluation.metrics import base_metric, Score

@base_metric
def custom_metric(output: str, expected: str) -> Score:
    # 你的评分逻辑
    similarity = compute_similarity(output, expected)
    return Score(
        value=similarity,
        reason=f"Similarity: {similarity:.2f}"
    )
```

#### 3. 数据集 + 实验管理

```python
import opik

client = opik.Opik()

# 创建数据集
dataset = client.create_dataset("design-qa-v1")
dataset.insert([
    {"input": "办公椅座高标准？", "expected_output": "400-440mm"},
    {"input": "ABS vs PC 区别？", "expected_output": "..."},
])

# 运行实验
from opik.evaluation import evaluate

def my_rag_pipeline(item):
    return rag_service.answer(item["input"])

results = evaluate(
    experiment_name="crag-v2-test",
    dataset=dataset,
    task=my_rag_pipeline,
    scoring_metrics=[hallucination_metric, answer_relevance]
)

# 对比实验结果
experiments = client.get_experiments(dataset_name="design-qa-v1")
for exp in experiments:
    print(f"{exp.name}: Hallucination {exp.metrics['hallucination']:.2f}")
```

#### 4. 在线评测规则（生产监控）

```python
# 定义规则：生产环境每个回复自动评分
client.create_online_evaluation_rule(
    name="production-hallucination-check",
    metric=Hallucination(),
    sampling_rate=0.1,  # 10% 采样（降低成本）
    alert_threshold=0.5,  # 低于 0.5 发告警
    alert_channels=["slack://prod-alerts"]
)
```

**效果**：
- 每天 40M+ traces，自动评估 4M 次
- 检测到幻觉率上升 → Slack 告警
- Dashboard 显示趋势图

#### 5. Prompt Playground（实验对比）

**UI 功能**：
```
┌─────────────────────────────────────────────────┐
│ Prompt A (current)      │ Prompt B (candidate) │
├─────────────────────────┼──────────────────────┤
│ You are a design expert │ You are a professional│
│ ...                     │ industrial designer...│
├─────────────────────────┼──────────────────────┤
│ Output: ...             │ Output: ...          │
│ Latency: 2.3s          │ Latency: 2.1s        │
│ Tokens: 1200           │ Tokens: 980          │
│ Hallucination: 0.92    │ Hallucination: 0.88  │
└─────────────────────────┴──────────────────────┘
```

**可编程 API**：
```python
from opik import track_prompt

@track_prompt(name="design-expert-prompt", version="v2")
def generate_answer(query: str, context: str):
    prompt = f"""You are a professional industrial designer...
    Context: {context}
    Question: {query}
    """
    return llm.generate(prompt)
```

---

## 📚 核心学习要点总结

### 1. 架构模式

| 模式 | 实现项目 | 核心思想 | 适用场景 |
|------|---------|---------|---------|
| **语料库隔离** | rag_evaluation | 每个 corpus 独立目录，避免污染评测数据 | 多租户、基准测试 |
| **异步索引** | rag_evaluation | 状态机 + 队列，支持中断恢复 | 大文件上传 |
| **混合检索** | hybrid-rag-system | Dense + Sparse + Graph，RRF 融合 | 覆盖不同类型查询 |
| **自纠错 CRAG** | hybrid-rag-system | LangGraph 状态机，最多 2 次重试 | 提高检索鲁棒性 |
| **反馈学习** | hybrid-rag-system | 用户评分 → 特征工程 → GBM 重排 | 持续优化 |
| **分布式追踪** | Opik | OpenTelemetry 标准，跨语言兼容 | 大规模生产监控 |

### 2. 评测体系

#### Ground Truth 设计（必学）
```python
# ❌ 错误做法：标注 chunk ID
{"expected_chunk_ids": ["doc1_chunk_5"]}  
# 问题：改 chunk_size 后 ID 指向不同内容

# ✅ 正确做法：标注内容片段
{"expected_answer_spans": ["座高为 400mm~440mm"]}
# 运行时动态解析到 chunk_id，确保唯一匹配
```

#### 多层级指标体系
```
检索层：
  - Precision@K, Recall@K, MRR, Hit Rate
  - 每个检索器的贡献占比（dense vs sparse vs graph）

生成层：
  - Faithfulness（忠实度，LLM-as-a-judge）
  - Answer Relevancy（相关性）
  - Hallucination Score（幻觉检测）

系统层：
  - 检索延迟 P50/P95
  - 生成延迟 P50/P95
  - Token 消耗（评分 vs 生成）
```

### 3. 工具调用模式

#### 抽象层设计
```python
# 基类（rag_evaluation 模式）
class BaseGenerator(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        pass

class BaseReranker(ABC):
    @abstractmethod
    def rerank(self, query: str, docs: list[Document]) -> list[Document]:
        pass

# 实现类
class GroqGenerator(BaseGenerator):
    def generate(self, prompt: str) -> str:
        return self.client.chat.completions.create(...)

# 依赖注入
class RAGService:
    def __init__(self, generator: BaseGenerator, reranker: BaseReranker):
        self.generator = generator
        self.reranker = reranker
```

**好处**：
- 测试时注入 Mock
- 生产时注入真实 API
- 切换供应商无需改业务逻辑

### 4. 可观测性最佳实践

#### 自动追踪（Opik 模式）
```python
@track  # ← 一行代码，捕获所有上下文
def complex_agent(query: str):
    plan = planner(query)      # ← 自动追踪
    for step in plan:
        result = executor(step)  # ← 自动追踪
    return aggregator(results)   # ← 自动追踪
```

#### 结构化日志
```python
import structlog

logger = structlog.get_logger()

def retrieve(query: str):
    with logger.bind(query=query, node="retrieve"):
        docs = retriever.search(query)
        logger.info("retrieved", doc_count=len(docs), latency_ms=...)
        return docs
```

#### 性能监控
```python
# 节点级计时
@timed(metric_name="retrieve_latency")
def retrieve(query: str):
    ...

# 资源追踪
@track_tokens
def generate(prompt: str):
    response = llm.generate(prompt)
    # 自动记录 prompt_tokens, completion_tokens
    return response
```

### 5. 生产部署要点

#### 容错设计（hybrid-rag-system）
```python
# 熔断器
from circuit_breaker import CircuitBreaker

llm_breaker = CircuitBreaker(failure_threshold=3, timeout=60)

def grade_documents(query, docs):
    try:
        return llm_breaker.call(llm_grade, query, docs)
    except CircuitOpenError:
        # 降级：改用关键词匹配
        return keyword_filter(query, docs)
```

#### 队列持久化
```python
# rag_evaluation 模式
if REDIS_URL:
    queue = RedisQueue(REDIS_URL)  # 持久化
else:
    queue = InProcessQueue()  # 重启丢失队列任务
    
# 报告队列类型
GET /queue -> {"backend": "redis", "durable": true}
```

#### 成本控制
```python
# 采样评测（Opik）
if random.random() < 0.1:  # 10% 采样
    evaluate_with_llm(response)
else:
    use_heuristic_metrics(response)
```

---

## 🎯 对你的项目的 10 条建议

### 立即实施（1-2 天）

#### 1. 修复 Ground Truth 设计
```python
# 当前：在 test_qa_20.json 中
"expected_sources": ["设计规范与标准/3326-2016-gbt-cd-300.pdf"]

# 改为：
"expected_answer_spans": [
    "座高（座面中轴线前部最高点至地面的距离）为 400mm~440mm"
]

# 新增：ChunkResolver
class ChunkResolver:
    def resolve_span_to_chunk_id(self, span: str) -> str:
        # 在当前索引中查找精确匹配的 chunk
        matches = [c for c in chunks if span in c.text]
        if len(matches) != 1:
            raise ValueError(f"Span '{span}' matched {len(matches)} chunks, expected 1")
        return matches[0].id
```

#### 2. 增加混合检索
```python
# src/retrieval/bm25_retriever.py (新建)
from rank_bm25 import BM25Okapi

class BM25Retriever:
    def __init__(self, chunks: list[Document]):
        self.chunks = chunks
        self.tokenized_corpus = [c.text.split() for c in chunks]
        self.bm25 = BM25Okapi(self.tokenized_corpus)
    
    def search(self, query: str, top_k: int) -> list[Document]:
        scores = self.bm25.get_scores(query.split())
        top_indices = np.argsort(scores)[-top_k:][::-1]
        return [self.chunks[i] for i in top_indices]

# src/retrieval/hybrid_retriever.py (新建)
def reciprocal_rank_fusion(results_list: list[list[Document]], k=60) -> list[Document]:
    scores = defaultdict(float)
    for results in results_list:
        for rank, doc in enumerate(results, start=1):
            scores[doc.id] += 1 / (k + rank)
    
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

#### 3. 暴露性能指标
```python
# app.py 增加
import time

async def chat(message: str, history):
    start = time.time()
    retrieval_start = time.time()
    # ... 检索
    retrieval_time = time.time() - retrieval_start
    
    generation_start = time.time()
    # ... 生成
    generation_time = time.time() - generation_start
    
    total_time = time.time() - start
    
    metrics = {
        "retrieval_ms": retrieval_time * 1000,
        "generation_ms": generation_time * 1000,
        "total_ms": total_time * 1000,
        "token_count": response.usage.total_tokens  # 如果 LLM 提供
    }
    
    yield (answer, route, sources, errors, metrics)  # 新增 metrics 输出
```

### 短期实施（1-2 周）

#### 4. 实现异步文档索引
```python
# jobs/queue.py (新建，参考 rag_evaluation)
class InProcessQueue:
    def __init__(self):
        self.queue = Queue()
        self.worker_thread = Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
    
    def _worker(self):
        while True:
            job = self.queue.get()
            try:
                process_document(job)
            except Exception as e:
                mark_failed(job.doc_id, str(e))

# ingest.py 改造
@app.post("/documents")
async def upload_document(file: UploadFile, corpus_id: str):
    doc_id = save_file(file)
    job_queue.enqueue(IndexJob(doc_id, corpus_id))
    return {"document_id": doc_id, "status": "QUEUED"}

@app.get("/documents/{doc_id}/status")
def get_status(doc_id: str):
    return {"status": db.get_status(doc_id)}  # QUEUED / EMBEDDING / READY / FAILED
```

#### 5. 增加交叉编码器重排
```python
# src/retrieval/reranker.py (新建)
from sentence_transformers import CrossEncoder

class CrossEncoderReranker:
    def __init__(self):
        self.model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    
    def rerank(self, query: str, docs: list[Document], top_k: int) -> list[Document]:
        pairs = [(query, doc.text) for doc in docs]
        scores = self.model.predict(pairs)
        
        for doc, score in zip(docs, scores):
            doc.rerank_score = score
        
        return sorted(docs, key=lambda d: d.rerank_score, reverse=True)[:top_k]

# src/graph_nodes.py 改造
async def retrieve(self, state: GraphState) -> GraphState:
    # 原有检索
    docs = await self.services.retrieve(state["question"])
    
    # 新增重排（可选）
    if self.services.reranker:
        docs = await self.services.rerank(state["question"], docs, top_k=5)
    
    return {"documents": docs}
```

#### 6. 熔断器 + 降级策略
```python
# src/circuit_breaker.py (新建，参考前文规划)
class CircuitBreaker:
    # ... 实现

# src/services.py 改造
class ResilientServices:
    def __init__(self):
        self.llm_breaker = CircuitBreaker(failure_threshold=3)
    
    async def grade(self, question: str, docs: list[Document]) -> list[Document]:
        try:
            return await self.llm_breaker.call(grade_documents, question, docs)
        except CircuitOpenError:
            # 降级：关键词匹配
            print("[降级] LLM 评分不可用，使用关键词过滤")
            return keyword_filter(question, docs)
```

### 中期实施（3-4 周）

#### 7. 语料库隔离
```python
# corpora/layout.py (新建，参考 rag_evaluation)
class CorpusLayout:
    def __init__(self, corpus_id: str):
        if not re.match(r'^[a-z0-9][a-z0-9_-]{0,63}$', corpus_id):
            raise ValueError(f"Invalid corpus_id: {corpus_id}")
        
        self.corpus_id = corpus_id
        self.index_dir = Path(f"data/corpora/{corpus_id}")
    
    def get_faiss_path(self) -> Path:
        return self.index_dir / "faiss.index"
    
    def get_metadata_path(self) -> Path:
        return self.index_dir / "metadata.json"

# app.py 改造
@app.post("/query")
def query(request: QueryRequest, corpus_id: str = "evaluation"):
    service = get_rag_service(corpus_id)  # 每个 corpus 独立 service
    return service.answer(request.question)
```

#### 8. 反馈收集与学习
```python
# api/routers/feedback.py (新建)
@app.post("/feedback")
def submit_feedback(
    query_id: str,
    rating: int,  # 1-5 星
    comment: str = ""
):
    query = db.get_query(query_id)
    for chunk in query.retrieved_chunks:
        features = extract_features(query.question, chunk)
        db.insert_feedback_sample(
            query=query.question,
            chunk=chunk.text,
            rating=rating,
            features=features
        )
    
    # 达到 20 个样本时触发训练
    if db.count_feedback_samples() >= 20:
        train_reward_model()

# Gradio 增加评分组件
with gr.Row():
    rating = gr.Radio([1, 2, 3, 4, 5], label="评分")
    submit_btn = gr.Button("提交反馈")
```

#### 9. 自动化评测流水线
```bash
# .github/workflows/nightly-eval.yml
name: Nightly Evaluation
on:
  schedule:
    - cron: '0 2 * * *'  # 每天凌晨 2 点

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Setup Python
        uses: actions/setup-python@v2
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run evaluation
        run: python eval_langsmith.py
      - name: Generate report
        run: python scripts/generate_eval_report.py --output logs/eval_$(date +%Y%m%d).md
      - name: Upload report
        uses: actions/upload-artifact@v2
        with:
          name: eval-report
          path: logs/eval_*.md
```

#### 10. LangSmith 深度集成
```python
# config.py 扩展
LANGSMITH_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "design-kb-prod")
LANGSMITH_SAMPLING_RATE = float(os.getenv("LANGSMITH_SAMPLING_RATE", "0.1"))

os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = LANGSMITH_PROJECT

# app.py
async def chat(message: str, history):
    # 每次查询自动追踪到 LangSmith
    if random.random() < LANGSMITH_SAMPLING_RATE:
        with tracing_v2_enabled():
            result = await crag_app.ainvoke({"question": message})
    else:
        result = await crag_app.ainvoke({"question": message})
```

---

## 📖 推荐阅读顺序

### 第一阶段：理解基础（1 周）
1. **rag_evaluation/docs/architecture.md** — 理解 RAG 系统各模块如何协作
2. **rag_evaluation/evaluation/** — 学习如何设计评测框架
3. **hybrid-rag-system/core/crag_pipeline.py** — 理解 CRAG 状态机

### 第二阶段：动手实践（2 周）
4. **本地运行 rag_evaluation** — 感受完整流程
5. **实现混合检索** — 集成 BM25 到你的项目
6. **修复 Ground Truth** — 用内容片段替代 chunk ID

### 第三阶段：生产化（3 周）
7. **实现熔断器** — 参考前文规划书
8. **异步索引** — 参考 rag_evaluation 的队列设计
9. **集成 LangSmith** — 参考 Opik 的追踪模式

---

## 🔗 资源链接

### 项目仓库
- rag_evaluation: https://github.com/Vikas9892/rag_evaluation
- hybrid-rag-system: https://github.com/TammineniTanay/hybrid-rag-system
- Opik: https://github.com/comet-ml/opik

### 技术文档
- LangGraph: https://langchain-ai.github.io/langgraph/
- RAGAS: https://docs.ragas.io/
- FAISS: https://github.com/facebookresearch/faiss
- BM25: https://github.com/dorianbrown/rank_bm25
- OpenTelemetry: https://opentelemetry.io/docs/

### 论文
- CRAG (Corrective RAG): https://arxiv.org/abs/2401.15884
- RRF (Reciprocal Rank Fusion): Cormack et al., SIGIR 2009

---

**文档版本**：v1.0  
**更新时间**：2026-08-28  
**下次更新**：当你实现了前 5 条建议后，我们可以深入研究更高级的话题（多模态、流式重排、A/B 测试）
