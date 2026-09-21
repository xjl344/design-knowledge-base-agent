"""Inspect metadata coverage in the parallel F/R/P Chroma indexes."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REQUIRED_FIELDS = (
    "source",
    "source_title",
    "title",
    "source_category",
    "parent_document",
    "section_path",
    "chunk_id",
)


def inspect(strategy: str) -> dict:
    import chromadb

    normalized = strategy.upper()
    persist = ROOT / "data" / "chunking_experiments" / normalized
    collection_name = f"design_knowledge_{normalized.lower()}"
    result = {
        "strategy": normalized,
        "persist_directory": str(persist),
        "collection_name": collection_name,
        "exists": persist.is_dir(),
        "count": 0,
        "source_categories": {},
        "missing_field_counts": {},
        "sample": [],
        "error": None,
    }
    if not persist.is_dir():
        result["error"] = "index directory does not exist"
        return result
    client = chromadb.PersistentClient(path=str(persist))
    try:
        collection = client.get_collection(collection_name)
        payload = collection.get(include=["metadatas"], limit=100000)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        client.close()
    metadatas = [dict(item or {}) for item in (payload.get("metadatas") or [])]
    result["count"] = len(metadatas)
    categories = Counter(str(item.get("source_category", "missing")) for item in metadatas)
    result["source_categories"] = dict(categories)
    result["missing_field_counts"] = {
        field: sum(1 for item in metadatas if not str(item.get(field, "")).strip())
        for field in REQUIRED_FIELDS
    }
    result["sample"] = [
        {field: item.get(field) for field in REQUIRED_FIELDS}
        for item in metadatas[:3]
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="+", default=["F", "R", "P"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [inspect(strategy) for strategy in args.strategies]
    rendered = json.dumps({"reports": reports}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if all(report["error"] is None for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
