"""One-shot snapshot creation using the existing frozen retriever."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from src.frozen_evidence import (  # noqa: E402
    FrozenDocument,
    FrozenRetrievalCase,
    index_fingerprint,
    retrieval_config_snapshot,
    write_cases,
)
from src.retriever import retrieve_documents  # noqa: E402


# 既有快照（data/frozen_retrieval_cases.jsonl）用的就是这 12 个 id。
# 它们必须保持为**默认值**：改默认值等于换掉已被历史运行引用的那一代快照。
DEFAULT_QUESTION_IDS: tuple[str, ...] = (
    tuple(f"q{index:02d}_hit" for index in range(1, 11)) + ("q12_miss", "q17_ambiguous")
)


def parse_ids(value: str) -> set[str]:
    """Parse a comma-separated question-id list."""
    ids = {item.strip() for item in value.split(",") if item.strip()}
    if not ids:
        raise ValueError("--ids 解析后为空")
    return ids


def load_questions(path: Path, ids: set[str]) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = [item for item in payload.get("questions", []) if item.get("id") in ids]
    missing = ids - {str(item.get("id")) for item in questions}
    if missing:
        raise ValueError(f"评测集缺少问题：{', '.join(sorted(missing))}")
    return questions


def main() -> int:
    parser = argparse.ArgumentParser(description="调用现有检索器一次并保存完整证据快照")
    parser.add_argument("--output", default=str(ROOT / "data" / "frozen_retrieval_cases.jsonl"))
    parser.add_argument("--questions", default=str(ROOT / "data" / "test_qa_20.json"))
    parser.add_argument("--top-k", type=int, default=0)
    parser.add_argument(
        "--ids",
        default="",
        help=(
            "逗号分隔的问题 id。缺省时用既有快照的 12 个 id，"
            "以保证 data/frozen_retrieval_cases.jsonl 可原样重建。"
        ),
    )
    args = parser.parse_args()
    ids = parse_ids(args.ids) if args.ids else set(DEFAULT_QUESTION_IDS)
    questions = load_questions(Path(args.questions), ids)
    config_snapshot = retrieval_config_snapshot(settings)
    fingerprint = index_fingerprint(settings)
    snapshot_id = hashlib.sha256(
        json.dumps({"config": config_snapshot, "index": fingerprint}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    cases: list[FrozenRetrievalCase] = []
    for item in questions:
        question = str(item["question"])
        started = time.perf_counter()
        documents = retrieve_documents(question, args.top_k or None)
        elapsed = round(time.perf_counter() - started, 3)
        frozen = tuple(FrozenDocument.from_document(document, index) for index, document in enumerate(documents, 1))
        profile = dict(documents[0].metadata.get("retrieval_profile", {})) if documents else {}
        cases.append(FrozenRetrievalCase(
            question_id=str(item["id"]),
            question=question,
            retrieval_snapshot_id=snapshot_id,
            retrieval_config=config_snapshot,
            index_fingerprint=fingerprint,
            documents=frozen,
            retrieval_profile=profile,
            retrieval_timing={"retrieval_seconds": elapsed, "document_count": len(frozen)},
        ))
        print(f"{item['id']}: documents={len(frozen)} retrieval_seconds={elapsed:.3f}")
    write_cases(args.output, cases)
    print(f"已写入冻结快照：{args.output}")
    print(f"snapshot_id={snapshot_id} index_count={fingerprint['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
