"""把界面会话的检索画像导出成可复核的证据文件。

为什么需要它
------------
README 里那条最重要的论断——「同一道题的耗时差三个数量级，根因是进程内检索缓存」——
依据是 `retrieval_profile`。而 `retrieval_profile` 只落在 `.tmp/checkpoints/*.json`，
`.tmp/` 不入库，于是论断不可复核。这个脚本把它蒸馏成一个小 JSON 提交进仓库。

用法：
    python scripts/dump_retrieval_profiles.py \
        --checkpoints .tmp/checkpoints \
        --out docs/portfolio/retrieval_cache_evidence.json

判读方式：
    cache_hit=True 的行，其 `reranker_seconds` / `bm25_seconds` 是**填充缓存那次冷检索**的
    组件耗时（命中时不会重新计算，画像被原样带过来）。所以若干 True 行若带着完全相同的
    组件耗时，说明它们是**同一个缓存条目**的多次命中，而不是多次独立检索。
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

# 只保留这些字段，避免把整个 state（含答案正文）拖进来。
PROFILE_KEYS = (
    "cache_hit",
    "local_retrieval_seconds",
    "embedding_seconds",
    "dense_seconds",
    "bm25_seconds",
    "reranker_seconds",
    "fusion_seconds",
    "dedup_seconds",
    "error_type",
    "timeout_stage",
    "total_seconds",
)


def load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    # checkpoint 可能是 {"state": {...}} 也可能是裸 state
    if isinstance(blob, dict) and isinstance(blob.get("state"), dict):
        return blob["state"]
    return blob


def collect(checkpoint_dir: str) -> list[dict]:
    observations = []
    for path in sorted(glob.glob(os.path.join(checkpoint_dir, "*.json"))):
        try:
            state = load_state(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"跳过（无法解析）：{path} — {exc}", file=sys.stderr)
            continue

        profile = state.get("retrieval_profile") or {}
        if not profile:
            # 没有检索画像的运行（早期会话、未走到检索）不构成证据
            continue

        spec = state.get("problem_spec") or {}
        observations.append({
            "checkpoint": os.path.basename(path),
            "checkpoint_written_at": datetime.datetime.fromtimestamp(
                os.path.getmtime(path)
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": state.get("session_id"),
            "question": spec.get("object"),
            "question_type": state.get("question_type"),
            "route": state.get("route"),
            "status": state.get("status"),
            "deliverable": state.get("deliverable"),
            "retrieval_profile": {k: profile.get(k) for k in PROFILE_KEYS},
        })

    observations.sort(key=lambda o: o["checkpoint_written_at"])
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoints", default=".tmp/checkpoints")
    parser.add_argument("--out", default="docs/portfolio/retrieval_cache_evidence.json")
    args = parser.parse_args()

    observations = collect(args.checkpoints)
    if not observations:
        print("没有找到任何带 retrieval_profile 的 checkpoint。", file=sys.stderr)
        return 1

    cache_hits = sum(
        1 for o in observations if o["retrieval_profile"].get("cache_hit") is True
    )
    timeouts = sum(
        1 for o in observations
        if o["retrieval_profile"].get("error_type") == "retrieval_timeout"
    )

    payload = {
        "description": (
            "界面会话的检索画像快照。用于核对 README 中"
            "「同一道题的耗时差三个数量级，根因是进程内检索缓存」这一论断。"
        ),
        "how_to_reproduce": (
            "python scripts/dump_retrieval_profiles.py "
            "--checkpoints .tmp/checkpoints "
            "--out docs/portfolio/retrieval_cache_evidence.json"
        ),
        "how_to_read": (
            "cache_hit=True 时，reranker_seconds / bm25_seconds 是填充缓存那次冷检索的"
            "组件耗时（命中不重算，画像被原样带过来）。多个 True 行若带着完全相同的"
            "组件耗时，说明它们是同一个缓存条目的多次命中。"
        ),
        "counts": {
            "observations": len(observations),
            "cache_hit_true": cache_hits,
            "cold_retrieval": len(observations) - cache_hits,
            "retrieval_timeout": timeouts,
        },
        "observations": observations,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"已写出 {args.out}")
    print(
        f"共 {len(observations)} 次带画像的运行："
        f"缓存命中 {cache_hits} 次，冷检索 {len(observations) - cache_hits} 次，"
        f"其中撞上检索上限 {timeouts} 次。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
