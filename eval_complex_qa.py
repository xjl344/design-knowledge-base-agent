"""Run the 35-question evaluation in LangSmith Datasets & Experiments."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

from config import LOG_DIR, settings

DATA_FILE = Path(__file__).parent / "data" / "test_qa_35_complex.json"
DATASET_NAME = "design-knowledge-qa-complex-35"


def load_questions(path: Path) -> list[dict]:
    questions = json.loads(path.read_text(encoding="utf-8")).get("questions", [])
    if len(questions) != 35:
        raise ValueError(f"复杂评测集应有35题，当前为{len(questions)}题")
    return questions


BALANCED_CATEGORIES = (
    "人体工学综合",
    "材料对比推荐",
    "尺寸计算推导",
    "制造工艺验证",
    "标准冲突与证据边界",
    "证据不足与拒答",
)


def select_balanced_questions(questions: list[dict], per_category: int = 3) -> list[dict]:
    """Select a deterministic small regression set from the 35-question set.

    The one ``端到端方案`` item is intentionally kept out of the smoke set:
    it is valuable for full regression but cannot satisfy the three-per-type
    balance requested for quick chunking experiments.
    """
    if per_category <= 0:
        raise ValueError("per_category must be positive")
    selected: list[dict] = []
    for category in BALANCED_CATEGORIES:
        category_items = [item for item in questions if item.get("category") == category]
        selected.extend(category_items[:per_category])
    return selected


CHUNKING_CATEGORIES = (
    "人体工学综合",
    "材料对比推荐",
    "尺寸计算推导",
    "制造工艺验证",
    "证据不足与拒答",
)


def select_chunking_questions(questions: list[dict], per_category: int = 2) -> list[dict]:
    """Return a deterministic ten-question retrieval-only comparison set."""
    if per_category <= 0:
        raise ValueError("per_category must be positive")
    selected: list[dict] = []
    for category in CHUNKING_CATEGORIES:
        category_items = [item for item in questions if item.get("category") == category]
        selected.extend(category_items[:per_category])
    return selected


def _expected(item: dict) -> dict:
    return {"expected_answer": item.get("expected_answer", ""), "expected_sources": item.get("expected_sources", []), "category": item.get("category", ""), "difficulty": item.get("difficulty", ""), "rubric": item.get("rubric", [])}


def sync_dataset(client, name: str, questions: list[dict]):
    from langsmith.utils import LangSmithNotFoundError
    try:
        dataset = client.read_dataset(dataset_name=name)
        existing = list(client.list_examples(dataset_id=dataset.id, limit=1000))
    except LangSmithNotFoundError:
        dataset = client.create_dataset(dataset_name=name, description="设计知识库35题复杂评测集")
        existing = []
    by_question = {(e.inputs or {}).get("question", ""): e for e in existing}
    created = updated = 0
    for item in questions:
        question, outputs = item["question"], _expected(item)
        example = by_question.get(question)
        if example is None:
            client.create_examples(dataset_id=dataset.id, examples=[{"inputs": {"question": question}, "outputs": outputs}])
            created += 1
        elif (example.outputs or {}) != outputs:
            client.update_example(example.id, inputs={"question": question}, outputs=outputs)
            updated += 1
    print(f"Dataset {name}: 新增 {created}，更新 {updated}，已有 {len(existing)} 条")
    return dataset


def make_target(with_web: bool):
    from src.services import DefaultServices

    def target(example: dict) -> dict:
        # Use a fresh service/graph per example so one transient LLM failure
        # cannot open the shared circuit breaker for all following questions.
        from src.graph_builder import build_graph

        class EvalServices(DefaultServices):
            async def search(self, question: str) -> list:
                return []

        graph = build_graph(DefaultServices() if with_web else EvalServices())
        loop = asyncio.new_event_loop()
        try:
            state = loop.run_until_complete(graph.ainvoke({"question": example["question"]}))
        finally:
            loop.close()
        answer = state.get("generation", "")
        return {
            "answer": answer,
            "sources": state.get("sources", []),
            "route": state.get("route", ""),
            "web_search_triggered": state.get("web_search_triggered", False),
            "errors": state.get("errors", []),
            "status": state.get("status", ""),
            "token_count": state.get("token_count", 0),
            "fallback_reason": "; ".join(state.get("errors", [])) if "答案生成模型当前不可用" in answer else None,
        }
    return target


def _outputs(run) -> dict:
    return getattr(run, "outputs", None) or (run if isinstance(run, dict) else {})


def source_hit(run, example) -> dict:
    expected = (example.outputs or {}).get("expected_sources", [])
    predicted = [s.get("location", "") for s in _outputs(run).get("sources", []) if s.get("type") != "web"]

    def normalise(value: str) -> str:
        value = str(value or "").replace("\\", "/").casefold()
        return re.sub(r"[\s_+\-()（）【】\[\]{}]", "", value)

    def matches(expected: str, actual: str) -> bool:
        expected_n = normalise(expected)
        actual_n = normalise(actual)
        expected_name = expected_n.rsplit("/", 1)[-1]
        actual_name = actual_n.rsplit("/", 1)[-1]
        return expected_n in actual_n or expected_name == actual_name or expected_name in actual_n

    if not expected:
        score = 1.0 if not predicted else 0.0
    else:
        score = sum(1 for item in expected if any(matches(item, p) for p in predicted)) / len(expected)
    return {"key": "source_hit", "score": score, "comment": f"期望来源{len(expected)}个，命中率{score:.2f}"}


def correctness_evaluator():
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI
    settings.require_llm()
    llm = ChatOpenAI(model=settings.llm_model, api_key=settings.llm_api_key, base_url=settings.llm_base_url, temperature=0, timeout=settings.llm_timeout_seconds, max_retries=1)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是严格的中文评测助手。根据问题、参考答案和评分要点，判断模型回答是否准确、完整、忠于知识库且没有编造。只输出JSON：{{\"score\":0-10,\"reason\":\"一句话原因\"}}。"),
        ("human", "问题：{question}\n\n参考答案：{reference}\n\n评分要点：{rubric}\n\n模型回答：{answer}"),
    ])
    chain = prompt | llm
    def evaluator(run, example) -> dict:
        try:
            response = chain.invoke({"question": (example.inputs or {}).get("question", ""), "reference": (example.outputs or {}).get("expected_answer", ""), "rubric": "、".join((example.outputs or {}).get("rubric", [])), "answer": _outputs(run).get("answer", "")})
            raw = response.content if hasattr(response, "content") else str(response)
            match = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(match.group(0)) if match else {}
            return {"key": "answer_correctness", "score": max(0.0, min(1.0, float(data.get("score", 0)) / 10)), "comment": str(data.get("reason", ""))[:200]}
        except Exception as exc:
            return {"key": "answer_correctness", "score": None, "status": "evaluator_failed", "comment": f"评测调用失败：{exc}"}
    return evaluator


def run_evaluation(args) -> int:
    if args.local_model:
        os.environ["RAG_LOCAL_MODEL"] = "true"
        if args.local_model is not True:
            os.environ["LOCAL_PLANNER_MODEL"] = str(args.local_model)
            os.environ["LOCAL_GRADER_MODEL"] = str(args.local_model)
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    questions = load_questions(Path(args.dataset))
    if args.balanced:
        selected_items = select_balanced_questions(questions, args.per_category)
    else:
        selected_items = questions[max(args.start, 1) - 1:]
        if args.limit is not None:
            selected_items = selected_items[:args.limit]
    if args.dry_run:
        target = make_target(with_web=args.with_web)
        rows = [target({"question": item["question"]}) for item in selected_items]
        print(json.dumps({"question_count": len(rows), "results": rows}, ensure_ascii=False, indent=2))
        return 0

    from langsmith import Client
    from langsmith.evaluation import evaluate
    client = Client()
    dataset = sync_dataset(client, args.dataset_name, questions)
    examples = list(client.list_examples(dataset_id=dataset.id, limit=1000))
    by_question = {(e.inputs or {}).get("question", ""): e for e in examples}
    selected = [by_question[item["question"]] for item in selected_items]
    evaluators = [source_hit] if args.no_judge else [source_hit, correctness_evaluator()]
    prefix = args.experiment_prefix or "design-kb-complex"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    manifest = LOG_DIR / f"complex_eval_manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    manifest.write_text(json.dumps({"dataset": args.dataset_name, "experiment_prefix": prefix, "question_count": len(selected), "ids": [i["id"] for i in selected_items]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"开始 LangSmith Experiment：{prefix}，题目 {len(selected)}，并发 1")
    evaluate(make_target(args.with_web), data=selected, evaluators=evaluators, experiment_prefix=prefix, description="设计知识库35题复杂评测，结果位于 Datasets & Experiments", metadata={"dataset": args.dataset_name, "with_web": args.with_web}, max_concurrency=1, client=client, blocking=True, upload_results=True)
    print(f"Experiment 已完成，请在 LangSmith -> Datasets & Experiments 查看；记录：{manifest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DATA_FILE))
    parser.add_argument("--dataset-name", default=DATASET_NAME)
    parser.add_argument("--experiment-prefix", default=None)
    parser.add_argument("--with-web", action="store_true")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--local-model", nargs="?", const=True, default=False, help="规划/评分使用本地 OpenAI-compatible Qwen；可选传模型名")
    parser.add_argument("--dry-run", action="store_true", help="只运行目标并输出 JSON，不上传 LangSmith 或调用评测器")
    parser.add_argument("--balanced", action="store_true", help="按六类各抽取固定数量题目，适合快速回归")
    parser.add_argument("--per-category", type=int, default=3, help="--balanced 模式下每类抽取题数，默认3")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=1)
    return run_evaluation(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
