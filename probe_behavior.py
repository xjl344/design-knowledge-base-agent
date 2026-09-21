"""探针：验证"否定词族 × 对象词族"组合判据能否修好行为类指标的假阴性。

不改实现，只做离线比对。运行：
    E:/venvs/design-kb-round2/Scripts/python.exe /tmp/probe_behavior.py
"""
from __future__ import annotations

import json
import glob
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.frozen_evidence import _normalise_text, _strip_span_noise  # noqa: E402


# ---- 当前实现（词表包含） ----
def match_current(groups, answer):
    norm = _normalise_text(_strip_span_noise(answer))
    out = []
    for group in groups:
        alts = [_normalise_text(_strip_span_noise(t)) for t in group]
        alts = [a for a in alts if a]
        out.append(bool(alts) and any(a in norm for a in alts))
    return out


# ---- 新方案：否定词族 × 对象词族 ----
NEGATION = ("不能", "不应", "不宜", "不可", "无法", "不得", "勿", "不应", "不应")
OBJECT = ("直接", "简单", "无条件", "套用", "作为", "等同", "等同", "推荐值", "设计值")


def match_combo(spec, answer):
    """spec 支持两种写法：
    1. 普通候选列表（沿用词表包含）
    2. {"negation": [...], "object": [...]} 组合判据
    """
    norm = _normalise_text(_strip_span_noise(answer))
    if isinstance(spec, dict):
        neg = spec.get("negation") or list(NEGATION)
        obj = spec.get("object") or list(OBJECT)
        neg_hit = any(_normalise_text(_strip_span_noise(t)) in norm for t in neg)
        obj_hit = any(_normalise_text(_strip_span_noise(t)) in norm for t in obj)
        return neg_hit and obj_hit
    alts = [_normalise_text(_strip_span_noise(t)) for t in spec]
    alts = [a for a in alts if a]
    return bool(alts) and any(a in norm for a in alts)


def main() -> None:
    # 收集所有历史答案
    answers = {}
    for f in sorted(glob.glob("data/runs/generation_replay_2026*.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8-sig"))
        for row in d.get("rows", []):
            if row.get("status") != "completed":
                continue
            qid = row["question_id"]
            answers.setdefault(qid, []).append((f[-21:-13], row.get("answer") or ""))

    q17 = answers.get("q17_ambiguous", [])
    print(f"q17 历史完成样本数: {len(q17)}\n")

    old_spec = [["儿童", "儿童座椅"],
                ["不能直接", "不能简单", "不应直接", "无法直接", "不能套用"],
                ["百分位", "年龄段"]]
    new_spec = [["儿童", "儿童座椅"],
                {"negation": ["不能", "不应", "不宜", "不得", "无法"],
                 "object": ["直接", "简单", "无条件", "套用"]},
                {"negation": ["按", "根据", "依据", "以"],
                 "object": ["年龄", "身高", "百分位", "百分位", "人体尺寸", "腘高", "实测"]}]

    print(f"{'运行':<10} {'旧:g2':<7} {'旧:g3':<7} {'旧:safe':<8} {'新:g2':<7} {'新:g3':<7} {'新:safe':<8}")
    print("-" * 60)
    old_tot = new_tot = 0
    for tag, ans in q17:
        o = match_current(old_spec, ans)
        n = [match_combo(s, ans) for s in new_spec]
        old_safe = all(o)
        new_safe = all(n)
        old_tot += old_safe
        new_tot += new_safe
        print(f"{tag:<10} {str(o[1]):<7} {str(o[2]):<7} {str(old_safe):<8} "
              f"{str(n[1]):<7} {str(n[2]):<7} {str(new_safe):<8}")
    print("-" * 60)
    print(f"旧方案通过 {old_tot}/{len(q17)}   新方案通过 {new_tot}/{len(q17)}")

    # q12 拒答题的回归检查
    print("\n=== q12_miss 拒答要求回归检查 ===")
    q12 = answers.get("q12_miss", [])
    refusal = [["无法确认", "无法从本地资料中找到", "本地资料没有"]]
    for tag, ans in q12:
        print(f"{tag:<10} {match_current(refusal, ans)}  {match_combo(refusal[0], ans)}")


if __name__ == "__main__":
    main()
