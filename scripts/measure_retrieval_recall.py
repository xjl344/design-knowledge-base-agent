"""How deep does retrieval have to go before the right source shows up?

The question this answers, before any provider time is spent: **is there headroom
in retrieval depth at all?**  A measured generation gain of +0.25 from showing
more evidence only helps if the evidence is there to show.  If the correct
source never appears beyond rank 10, raising `RETRIEVER_TOP_K` adds noise, not
answers, and the whole second segment of the plan should be dropped.

Everything here is local -- `bge-m3` embeddings, a local cross-encoder, a
persisted Chroma index -- so this costs no API calls and touches no frozen
artefact.  Retrieval is *run*, never modified.

⚠️ What this measures, and what it does not
-------------------------------------------
The ground truth is `expected_sources`, which is a list of **files**.  So a
source counts as found when *any* of its chunks is in the returned list.  That
makes every number here an **upper bound on usefulness**: the right file being
present does not mean the right passage is present.  There is no chunk-level
ground truth to measure against, so "the passage is in there somewhere" cannot
be checked -- only "the document is".

Two consequences worth stating plainly:

* a recall of 0.81 at rank 20 means "81% of expected *files* have a chunk in the
  top 20", not "81% of the needed facts are reachable";
* the **missing** set is trustworthy, because a file that never appears cannot
  contain a reachable passage.  So "20 of 103 expected files never appear" is a
  sound lower bound on what depth cannot fix.

⚠️ Matching is by **basename**.  `expected_sources` holds file names
(`26158-2010-gbt-e-300.pdf`) while the index metadata holds relative paths.
Comparing the two raw produced an all-zero result once already, which reads as
"retrieval finds nothing" rather than "the comparison is wrong".

Usage::

    python scripts/measure_retrieval_recall.py
    python scripts/measure_retrieval_recall.py --top-k 30 --json
    # Is the missing set capped away, or genuinely absent?  Widen the pool:
    RETRIEVER_RERANK_TOP_K=30 RETRIEVER_SOURCE_CAP=99 \
        python scripts/measure_retrieval_recall.py --only c06,c07 --top-k 30
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUESTIONS = ROOT / "data" / "test_qa_35_complex.json"
CUTOFFS = (5, 10, 20, 30)


def _basename(value: Any) -> str:
    return Path(str(value or "").replace("\\", "/")).name.strip().lower()


def _first_rank(expected: str, sources: list[str]) -> int | None:
    """1-based rank of the first chunk whose source matches, or None."""
    wanted = _basename(expected)
    if not wanted:
        return None
    for index, source in enumerate(sources, 1):
        if _basename(source) == wanted:
            return index
    return None


def measure(
    top_k: int,
    limit: int | None = None,
    output: Path | None = None,
    progress: bool = True,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """Run the measurement, reporting and persisting progress as it goes.

    The first version of this printed only at the end, and a full run takes over
    an hour on CPU -- so "how far along is it" was unanswerable, and killing it
    lost everything.  Each question now prints a line and rewrites the output
    file, so partial results are usable and the remaining time is visible.
    """
    import time as _time

    from src.retriever import retrieve_documents

    payload = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    questions = payload.get("questions") or payload.get("cases") or []
    if only:
        # Re-measuring every question when only the ones with a missing source
        # can change the answer doubles a 75-minute run for nothing.
        questions = [q for q in questions if str(q.get("id")) in only]
    if limit:
        questions = questions[:limit]

    per_question: list[dict[str, Any]] = []
    started = _time.perf_counter()

    def snapshot() -> dict[str, Any]:
        return _summarise(per_question, top_k)

    for index, item in enumerate(questions, 1):
        question = str(item.get("question") or "")
        expected = [str(x) for x in item.get("expected_sources") or []]
        began = _time.perf_counter()
        documents = retrieve_documents(question, top_k=top_k)
        sources = [str(doc.metadata.get("source") or "") for doc in documents]
        ranks = {name: _first_rank(name, sources) for name in expected}
        entry = {
            "id": str(item.get("id")),
            "category": str(item.get("category") or ""),
            "difficulty": str(item.get("difficulty") or ""),
            "returned": len(documents),
            "expected": expected,
            "ranks": ranks,
            "found": sum(1 for rank in ranks.values() if rank is not None),
            "missing": [name for name, rank in ranks.items() if rank is None],
            "seconds": round(_time.perf_counter() - began, 1),
        }
        per_question.append(entry)
        if progress:
            elapsed = _time.perf_counter() - started
            rate = elapsed / index
            remaining = rate * (len(questions) - index)
            # Progress goes to **stderr**, not stdout.  On stdout it would be
            # interleaved with the `--json` payload and the result would not
            # parse -- a pipe into `jq` would fail on a perfectly good run.
            print(
                f"[{index}/{len(questions)}] {entry['id']} 返回 {entry['returned']} 条 "
                f"命中 {entry['found']}/{len(expected)} "
                f"({entry['seconds']}s，已用 {elapsed / 60:.1f} 分，"
                f"预计还需 {remaining / 60:.1f} 分)",
                file=sys.stderr,
                flush=True,
            )
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
            )

    return snapshot()


def _summarise(per_question: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    if not per_question:
        return {"top_k_requested": top_k, "questions": 0}

    def recall_at(cutoff: int) -> float:
        """Share of expected sources found at or above `cutoff`."""
        total = sum(len(q["expected"]) for q in per_question) or 1
        hit = sum(
            1
            for q in per_question
            for rank in q["ranks"].values()
            if rank is not None and rank <= cutoff
        )
        return round(hit / total, 4)

    def question_hit_at(cutoff: int) -> float:
        """Share of questions with *every* expected source at or above `cutoff`."""
        total = len(per_question) or 1
        hit = sum(
            1
            for q in per_question
            if q["expected"]
            and all(rank is not None and rank <= cutoff for rank in q["ranks"].values())
        )
        return round(hit / total, 4)

    by_category: dict[str, dict[str, Any]] = {}
    for question in per_question:
        bucket = by_category.setdefault(
            question["category"], {"questions": 0, "expected": 0, "found_at_all": 0}
        )
        bucket["questions"] += 1
        bucket["expected"] += len(question["expected"])
        bucket["found_at_all"] += question["found"]

    return {
        "top_k_requested": top_k,
        "questions": len(per_question),
        "returned_mean": round(
            statistics.mean([q["returned"] for q in per_question]), 2
        ),
        "returned_min": min(q["returned"] for q in per_question),
        "returned_max": max(q["returned"] for q in per_question),
        "seconds_mean": round(
            statistics.mean([q["seconds"] for q in per_question]), 1
        ),
        "source_recall_at": {str(c): recall_at(c) for c in CUTOFFS},
        "all_sources_present_at": {str(c): question_hit_at(c) for c in CUTOFFS},
        "expected_sources_total": sum(len(q["expected"]) for q in per_question),
        "expected_sources_found": sum(q["found"] for q in per_question),
        "by_category": by_category,
        "per_question": per_question,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true", help="不打印逐题进度")
    parser.add_argument(
        "--only",
        default=None,
        help="只测这些题 id（逗号分隔）；用于只重测有缺失来源的题",
    )
    args = parser.parse_args(argv)

    only = (
        {item.strip() for item in args.only.split(",") if item.strip()}
        if args.only
        else None
    )
    result = measure(
        args.top_k,
        args.limit,
        output=args.output,
        progress=not args.quiet,
        only=only,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"题数 {result['questions']}，请求 top_k={result['top_k_requested']}，"
          f"实际返回 {result['returned_min']}~{result['returned_max']}（均 {result['returned_mean']}）")
    print(f"期望来源总数 {result['expected_sources_total']}，"
          f"能命中（任意深度）{result['expected_sources_found']}")
    print()
    print("来源级召回（按来源计）：")
    for cutoff, value in result["source_recall_at"].items():
        print(f"  Recall@{cutoff:>2s}: {value:.3f}")
    print()
    print("题目级全命中（该题的全部期望来源都进前 K）：")
    for cutoff, value in result["all_sources_present_at"].items():
        print(f"  @{cutoff:>2s}: {value:.3f}")
    print()
    never = [q["id"] for q in result["per_question"] if q["missing"]]
    if never:
        print(f"有来源在 {result['top_k_requested']} 名内完全不出现的题（{len(never)}）：")
        for question in result["per_question"]:
            if question["missing"]:
                print(f"  {question['id']}: 缺 {question['missing']}")
    else:
        print(f"所有题的期望来源都在前 {result['top_k_requested']} 名内出现过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
