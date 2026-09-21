"""LangSmith evaluation driver for the design knowledge assistant.

Usage:
    python eval_langsmith.py --check      # local pre-check: retrieval only, no LLM/LangSmith
    python eval_langsmith.py              # full LangSmith eval (needs LANGCHAIN_API_KEY)
    python eval_langsmith.py --with-web   # allow web-search fallback during eval (default: off)

Environment (in .env or shell):
    LANGCHAIN_API_KEY=lsv2_...            # required for full eval
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_PROJECT=design-knowledge-qa
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from config import settings

QA_FILE = Path(__file__).parent / "data" / "test_qa_20.json"
DATASET_NAME = "design-knowledge-qa-20"


def load_questions() -> list[dict]:
    with QA_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)["questions"]


# --------------------------------------------------------------------------
# Local pre-check (retrieval only, no LLM, no LangSmith)
# --------------------------------------------------------------------------
def check_local() -> int:
    import asyncio

    from src.retriever import retrieve_documents_multi

    questions = load_questions()
    full = partial = zero = 0
    print("== 本地检索预检（混合检索：查询分解会调用一次 LLM，不调用评分/生成）==")
    for q in questions:
        expected = q.get("expected_sources") or []
        try:
            docs = asyncio.run(retrieve_documents_multi(q["question"]))
        except Exception as exc:
            print("ERROR %-6s %s" % (q["id"], exc))
            continue
        locations = [d.metadata.get("source", "") for d in docs]
        if not expected:
            first = locations[0].split("/")[-1] if locations else "(无本地来源)"
            print("MISS  %-6s 首位召回: %s" % (q["id"], first))
            continue
        found = [e for e in expected if any(e in loc for loc in locations)]
        ratio = len(found) / len(expected)
        if ratio >= 1.0:
            full += 1
            tag = "OK   "
        elif ratio > 0:
            partial += 1
            tag = "PART "
        else:
            zero += 1
            tag = "FAIL "
        print("%s %-6s 期望来源命中 %d/%d%s" % (
            tag, q["id"], len(found), len(expected),
            "" if ratio >= 1.0 else "  <- 缺失: %s" % [e.split("/")[-1] for e in expected if e not in found],
        ))
    print("== 预检小结：全命中 %d，部分命中 %d，零命中 %d ==" % (full, partial, zero))
    return 0 if zero == 0 else 1


# --------------------------------------------------------------------------
# Full LangSmith evaluation
# --------------------------------------------------------------------------
def _make_target(with_web: bool):
    from src.graph_builder import build_graph
    from src.services import DefaultServices

    class EvalServices(DefaultServices):
        """Default services, but web search disabled unless --with-web."""

        async def search(self, question: str) -> list:
            return []

    graph = build_graph(DefaultServices() if with_web else EvalServices())

    def _run(question: str) -> dict:
        loop = asyncio.new_event_loop()
        try:
            state = loop.run_until_complete(graph.ainvoke({"question": question}))
        finally:
            loop.close()
        return {
            "answer": state.get("generation", ""),
            "sources": state.get("sources", []),
            "route": state.get("route", ""),
            "web_search_triggered": state.get("web_search_triggered", False),
            "errors": state.get("errors", []),
        }

    def target(example: dict) -> dict:
        return _run(example["question"])

    return target


def source_hit(run, example) -> dict:
    """Deterministic: fraction of expected local sources actually used."""
    expected = example.outputs.get("expected_sources") or []
    predicted = [
        s.get("location", "")
        for s in (run.outputs.get("sources") or [])
        if s.get("type") != "web"
    ]
    if not expected:
        score = 1.0 if not predicted else 0.0
        return {
            "key": "source_hit",
            "score": score,
            "comment": "本地肯定没有：%s本地来源" % ("无" if not predicted else "意外使用了 %d 个" % len(predicted)),
        }
    found = sum(1 for e in expected if any(e in p for p in predicted))
    return {
        "key": "source_hit",
        "score": found / len(expected),
        "comment": "期望 %d 个来源，命中 %d 个" % (len(expected), found),
    }


def make_correctness_evaluator():
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    settings.require_llm()
    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是严格的中文评测助手。根据“问题”与“参考答案”，判断“模型回答”是否准确、完整、"
                "忠于资料、没有编造。只输出 JSON，格式：{{\"score\": 0-10, \"reason\": \"一句话原因\"}}。"
                "10 表示完全正确，0 表示完全错误或编造。",
            ),
            ("human", "问题：{question}\n\n参考答案：{reference}\n\n模型回答：{answer}"),
        ]
    )
    chain = prompt | llm

    def evaluator(run, example) -> dict:
        question = example.inputs.get("question", "")
        reference = example.outputs.get("expected_answer", "")
        answer = run.outputs.get("answer", "")
        try:
            response = chain.invoke(
                {"question": question, "reference": reference, "answer": answer}
            )
            text = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            return {"key": "answer_correctness", "score": 0.0, "comment": f"评测调用失败：{exc}"}
        match = re.search(r"\{.*\}", text, re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
            score = max(0.0, min(1.0, float(data.get("score", 0)) / 10))
            reason = str(data.get("reason", ""))[:200]
        except Exception:
            score, reason = 0.0, text[:200]
        return {"key": "answer_correctness", "score": score, "comment": reason}

    return evaluator


def run_evaluation(args) -> int:
    from langsmith import Client
    from langsmith.evaluation import evaluate

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGCHAIN_PROJECT", "design-knowledge-qa"))

    questions = load_questions()
    client = Client()
    try:
        dataset = client.read_dataset(dataset_name=args.dataset)
        dataset_created = False
    except Exception as exc:
        # 当前 LangSmith SDK 没有 get_or_create_dataset；读取失败时创建新数据集。
        # 这里只捕获读取失败，创建过程本身的认证或网络错误仍会向上抛出。
        from langsmith.utils import LangSmithNotFoundError

        if not isinstance(exc, LangSmithNotFoundError):
            raise
        dataset = client.create_dataset(
            dataset_name=args.dataset,
            description="设计知识库 20 问评测集（本地肯定有/没有/模棱两可）",
        )
        dataset_created = True
    existing = list(client.list_examples(dataset_id=dataset.id))
    if not existing:
        client.create_examples(
            dataset_id=dataset.id,
            inputs=[{"question": q["question"]} for q in questions],
            outputs=[
                {
                    "expected_answer": q["expected_answer"],
                    "expected_sources": q.get("expected_sources", []),
                    "category": q["category"],
                }
                for q in questions
            ],
        )
        message = "已在 LangSmith 创建数据集" if dataset_created else "已向 LangSmith 补充数据集用例"
        print("%s：%s（%d 个用例）" % (message, args.dataset, len(questions)))
    else:
        print("数据集已存在（%d 个用例），检查是否需要同步本地测试集..." % len(existing))
        # Sync: match by question text, update any example whose expected
        # outputs differ from the local QA file (e.g. after source fixes).
        local_by_question = {q["question"]: q for q in questions}
        updated = 0
        for example in existing:
            q = local_by_question.get((example.inputs or {}).get("question", ""))
            if not q:
                continue
            expected_outputs = {
                "expected_answer": q["expected_answer"],
                "expected_sources": q.get("expected_sources", []),
                "category": q["category"],
            }
            if (example.outputs or {}) != expected_outputs:
                client.update_example(
                    example.id,
                    inputs={"question": q["question"]},
                    outputs=expected_outputs,
                )
                updated += 1
        print("同步完成：更新 %d 个用例（其余与本地一致）" % updated)

    target = _make_target(args.with_web)
    evaluate(
        target,
        data=args.dataset,
        evaluators=[source_hit, make_correctness_evaluator()],
        experiment_prefix="design-kb",
        metadata={"with_web": args.with_web},
    )
    print("评测完成，结果已上传 LangSmith（Project: %s）" % os.environ["LANGCHAIN_PROJECT"])
    return 0


# --------------------------------------------------------------------------
# Report: pull experiment results from LangSmith into a local markdown report
# --------------------------------------------------------------------------
def _match_local_question(question: str) -> dict:
    """Match a LangSmith example back to the local QA file (for its id)."""
    for q in load_questions():
        if q["question"] == question:
            return q
    return {}


def _feedback_map(client, run) -> dict:
    """{feedback_key: {"score": ..., "comment": ...}} for one run."""
    result = {}
    try:
        for fb in client.list_feedback(run_ids=[run.id]):
            result[fb.key] = {"score": fb.score, "comment": fb.comment or ""}
    except Exception as exc:  # 反馈偶尔延迟，读不到就给占位
        result["_error"] = str(exc)
    return result


def _fmt_score(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def generate_report(args) -> int:
    """Pull the latest (or --experiment) run's feedback into logs/eval_report_*.md."""
    from datetime import datetime

    import warnings

    warnings.filterwarnings("ignore", message=".*list_runs.*deprecated.*")

    from langsmith import Client

    from config import LOG_DIR

    client = Client()
    project_name = args.experiment
    if not project_name:
        projects = list(client.list_projects(limit=200))
        candidates = sorted(
            (p for p in projects if getattr(p, "name", "").startswith("design-kb-")),
            key=lambda p: p.start_time or datetime.min,
            reverse=True,
        )
        if not candidates:
            print("未找到 design-kb-* 实验。请先运行全量评测，或用 --experiment 指定实验名。")
            return 1
        project_name = candidates[0].name
    print("拉取实验：%s ..." % project_name)

    runs = [r for r in client.list_runs(project_name=project_name, is_root=True) if r.name == "Target"]
    if not runs:
        print("实验中没有 Target run（可能评测尚未完成或实验为空）。")
        return 1
    runs.sort(key=lambda r: r.start_time or datetime.min)

    rows = []
    for run in runs:
        example_inputs = (run.inputs or {}).get("example", {})
        question = example_inputs.get("question", "")
        local = _match_local_question(question)
        row = {
            "qid": local.get("id", "?"),
            "category": local.get("category", "?"),
            "question": question,
            "answer": (run.outputs or {}).get("answer", ""),
            "route": (run.outputs or {}).get("route", ""),
            "web_search_triggered": (run.outputs or {}).get("web_search_triggered", False),
            "errors": (run.outputs or {}).get("errors", []) or [],
            "sources_used": [
                {"location": s.get("location", ""), "page": s.get("page"), "title": s.get("title")}
                for s in (run.outputs or {}).get("sources", []) or []
                if s.get("type") != "web"
            ],
            "expected_sources": local.get("expected_sources", []),
            "expected_answer": local.get("expected_answer", ""),
            "tokens": run.total_tokens or 0,
            "latency_seconds": (
                (run.end_time - run.start_time).total_seconds()
                if run.end_time and run.start_time
                else None
            ),
            "run_id": str(run.id),
            "feedback": _feedback_map(client, run),
        }
        rows.append(row)

    # ---- aggregate ----
    def avg(key):
        vals = [r["feedback"].get(key, {}).get("score") for r in rows]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    n = len(rows)
    sh = avg("source_hit")
    ac = avg("answer_correctness")
    # 评测器失败（prompt 报错等）的题目单独统计
    broken = [
        r for r in rows
        if "INVALID_PROMPT_INPUT" in (r["feedback"].get("answer_correctness", {}).get("comment", ""))
        or "评测调用失败" in (r["feedback"].get("answer_correctness", {}).get("comment", ""))
    ]

    lines = []
    lines.append("# 设计知识库评测报告\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 实验 | `{project_name}` |")
    lines.append(f"| 生成时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |")
    lines.append(f"| 题目数 | {n} |")
    lines.append(f"| source_hit 平均 | {_fmt_score(sh)} |")
    lines.append(f"| answer_correctness 平均 | {_fmt_score(ac)} |")
    lines.append(f"| 正确性评测器失败的题 | {len(broken)}（通常为 prompt 模板 bug，需修复后重跑全量） |")
    lines.append("")

    # ---- per-category breakdown ----
    lines.append("## 按类别汇总\n")
    lines.append("| 类别 | 题数 | source_hit 均值 | correctness 均值 |")
    lines.append("|---|---|---|---|")
    for cat in ("本地肯定有", "本地肯定没有", "模棱两可"):
        group = [r for r in rows if r["category"] == cat]
        if not group:
            continue

        def gavg(key):
            vals = [
                r["feedback"].get(key, {}).get("score")
                for r in group
                if r["feedback"].get(key, {}).get("score") is not None
            ]
            return sum(vals) / len(vals) if vals else None

        lines.append(f"| {cat} | {len(group)} | {_fmt_score(gavg('source_hit'))} | {_fmt_score(gavg('answer_correctness'))} |")
    lines.append("")

    # ---- per-question table ----
    lines.append("## 逐题明细\n")
    lines.append("| # | ID | 类别 | 路由 | source_hit | correctness | tokens | 耗时(s) | 问题 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        q = r["question"]
        q = q if len(q) <= 36 else q[:33] + "..."
        sh_v = r["feedback"].get("source_hit", {}).get("score")
        ac_v = r["feedback"].get("answer_correctness", {}).get("score")
        lat = f"{r['latency_seconds']:.1f}" if r["latency_seconds"] is not None else "—"
        lines.append(
            f"| {i} | {r['qid']} | {r['category']} | {r['route']} | {_fmt_score(sh_v)} | {_fmt_score(ac_v)} "
            f"| {r['tokens']} | {lat} | {q} |"
        )
    lines.append("")

    # ---- details ----
    lines.append("## 逐题详情\n")
    for i, r in enumerate(rows, 1):
        sh_v = r["feedback"].get("source_hit", {})
        ac_v = r["feedback"].get("answer_correctness", {})
        lines.append(f"### {i}. [{r['qid']}] {r['category']} — {r['question']}\n")
        base = (f"- 路由：`{r['route']}`，web 回退：{'是' if r['web_search_triggered'] else '否'}"
                f"，tokens：{r['tokens']}")
        if r["latency_seconds"] is not None:
            base += f"，耗时：{r['latency_seconds']:.1f}s"
        lines.append(base)
        lines.append(f"- source_hit：**{_fmt_score(sh_v.get('score'))}** — {sh_v.get('comment', '')}")
        ac_comment = ac_v.get("comment", "")
        if "INVALID_PROMPT_INPUT" in ac_comment or "评测调用失败" in ac_comment:
            lines.append(f"- answer_correctness：⚠️ **评测器失败** — {ac_comment[:160]}")
        else:
            lines.append(f"- answer_correctness：**{_fmt_score(ac_v.get('score'))}** — {ac_comment[:160]}")
        lines.append(f"- 期望来源：{r['expected_sources']}")
        if r["sources_used"]:
            lines.append(
                f"- 实际引用：{[(s['location'].split('/')[-1] if s['location'] else '?', s['page']) for s in r['sources_used']]}"
            )
        else:
            lines.append("- 实际引用：无本地来源")
        if r["errors"]:
            lines.append(f"- 运行错误：{r['errors'][:3]}")
        answer = r["answer"].replace("\n", " ").strip()
        lines.append(f"- 回答：> {answer[:220]}{'...' if len(answer) > 220 else ''}")
        lines.append("")

    report_path = LOG_DIR / f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:40]))
    print(f"\n报告已保存：{report_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="本地预检：混合检索（含查询分解），验证期望来源是否可召回（不跑评分/生成、不上传 LangSmith）",
    )
    parser.add_argument(
        "--with-web",
        action="store_true",
        help="评测时允许网络搜索回退（默认关闭，聚焦本地知识库质量）",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="从 LangSmith 拉取最近一次实验（或 --experiment 指定），生成本地评测报告 logs/eval_report_*.md",
    )
    parser.add_argument(
        "--experiment",
        default=None,
        help="指定要拉取报告的实验名（默认取最新的 design-kb-* 实验）",
    )
    parser.add_argument("--dataset", default=DATASET_NAME, help="LangSmith 数据集名")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check:
        return check_local()

    if not os.getenv("LANGCHAIN_API_KEY"):
        print("错误：未设置 LANGCHAIN_API_KEY。请在 .env 中配置：")
        print("  LANGCHAIN_API_KEY=lsv2_...")
        print("  LANGCHAIN_TRACING_V2=true")
        return 1
    if args.report:
        return generate_report(args)
    return run_evaluation(args)


if __name__ == "__main__":
    raise SystemExit(main())
