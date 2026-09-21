"""Run the generic design-decision quality evaluation set."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from config import LOG_DIR
from src.graph_builder import build_graph


def load_questions(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("questions", [])


async def run(path: Path) -> int:
    graph = build_graph()
    rows = []
    for item in load_questions(path):
        try:
            state = await graph.ainvoke({"question": item["question"]})
            rows.append({
                "id": item.get("id"),
                "category": item.get("category"),
                "question": item["question"],
                "metrics": state.get("decision_metrics", {}),
                "evidence_matrix": state.get("evidence_matrix", []),
                "claims": state.get("claims", []),
                "conflicts": state.get("conflicts", []),
                "errors": state.get("errors", []),
            })
        except Exception as exc:
            rows.append({"id": item.get("id"), "question": item["question"], "errors": [str(exc)]})
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "question_count": len(rows),
        "rows": rows,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    output = LOG_DIR / f"decision_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"专项评测完成：{output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/design_decision_eval.json")
    args = parser.parse_args()
    return asyncio.run(run(Path(args.dataset)))


if __name__ == "__main__":
    raise SystemExit(main())
