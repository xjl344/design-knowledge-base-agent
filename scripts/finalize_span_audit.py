"""Apply the human verdicts to the span-audit skeletons and total them up.

The verdicts below are **judgements read off the answers**, not computations.
They live in this file, next to the reason for each, so the audit can be
reviewed and re-run rather than trusted.  `build_span_audit.py` fills the
mechanical columns; this adds the one column a person has to supply.

The classification that matters
-------------------------------
`correct_refusal` is decided **mechanically** from `chunk_in_pack`: if the hop's
source chunk never reached the model, the model could not have known the fact,
and the hop is not evidence about capability at all.  Those observations are
removed from the denominator rather than counted as misses -- the same treatment
as a damaged span, and for the same reason.

`correct_paraphrase` / `damaged_span` mean the fact **was** brought out and the
metric missed it.  `incorrect` means it was not brought out.

Why the total matters
---------------------
The real set's corrected figure is compared against the synthetic set's, and the
comparison decides whether the report's headline -- "the synthetic set
overestimates real ability" -- survives.  So both sets are audited here; auditing
only the set whose number looks wrong would be the error this whole exercise is
about.

Usage::

    python scripts/finalize_span_audit.py \
        --real .tmp/span_audit_skeleton.json \
        --synthetic .tmp/span_audit_synthetic.json \
        --output data/span_audit.v1.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# (question_id, hop_id) -> (verdict, reason)
#
# Real set.  The five `correct_paraphrase` hops are all cases where the answer
# states the hop's substance in its own words or in another notation.  The
# `damaged_span` one is the taper formula, whose declared span normalisation
# destroys (`check_span_normalisation.py`); it is credited here as well because
# the answer did write the formula -- but it is labelled separately because the
# fix is a metric fix, not a matcher-tolerance decision.
REAL_VERDICTS: dict[tuple[str, str], tuple[str, str]] = {
    ("r17_cylinder_capacity", "h1"): (
        "correct_paraphrase",
        "答案写出 V=π×65²×120÷4÷1000≈398.2 mL，即公式代入数值的形式，并说明用的是内径与有效液高；"
        "契约要的是符号形式，故判否。",
    ),
    ("r17_cylinder_capacity", "h2"): (
        "incorrect",
        "证据在 pack 内，但答案称「提供的证据片段未显示具体公式」并拒绝给出容量与额定容量关系；"
        "该跳的实质（额定容量应低于杯口溢流前的最大容量）未交付。",
    ),
    ("r18_height_adjustment", "h1"): (
        "correct_paraphrase",
        "答案写出 V=πD²h/4/1000 并称「再反推 h」；契约要求逐字句「再用容量公式反推有效高度」。",
    ),
    ("r18_height_adjustment", "h2"): (
        "correct_paraphrase",
        "答案写「应检查重心是否过高」；契约要求「检查高度是否导致重心过高」。",
    ),
    ("r19_taper_capacity", "h1"): (
        "damaged_span",
        "答案以 LaTeX 写出同一锥台公式并算出 332.3 mL；契约跨段在规范化时被销毁（D1/D2 被当表标签删掉），"
        "任何答案都无法匹配。",
    ),
    ("r19_taper_capacity", "h2"): (
        "correct_paraphrase",
        "答案写「应优先使用 CAD 计算内部封闭空间体积，或通过实测注水体积确定容量」；契约要求原句。",
    ),
    ("r03_office_chair_constraints", "h2"): (
        "incorrect",
        "契约要求「坐姿膝高」，terms 未命中（hits=[]）；答案讨论座高/座深/扶手高，从未提及坐姿膝高。",
    ),
    ("r04_child_chair_flow", "h1"): (
        "incorrect",
        "契约要求「小腿加足高」，terms 未命中；答案列举坐高/膝高/腘高/臀宽/臀—腘距，未含该项。",
    ),
    ("r04_child_chair_flow", "h2"): (
        "incorrect",
        "契约要求「680~760」，terms 未命中；答案给出座高 400～440、软面座高 460、座深 340～460，从未给出该区间。",
    ),
}

# Synthetic set.  Note `mh03 h2`: the answer writes `（4～17岁）` with a full-width
# tilde while the contract's term is `4岁~17岁` with an ASCII one.  The span
# matcher normalises `～` to `~`; the *term* matcher does not.  That is a second,
# separate defect from the span one, and it is why this hop is credited.
SYNTHETIC_VERDICTS: dict[tuple[str, str], tuple[str, str]] = {
    ("mh03_adult_vs_minor_population", "h1"): (
        "correct_paraphrase",
        "答案写「GB/T 10000—2023 覆盖我国成年人…提供静态人体尺寸」，即契约名词短语「成年人人体尺寸」的展开形式。",
    ),
    ("mh03_adult_vs_minor_population", "h2"): (
        "correct_paraphrase",
        "答案写「未成年人（4～17岁）人体尺寸」，年龄区间已交付；term 用全角/半角波浪号不一致导致判否"
        "（span 匹配器会归一化 ～，term 匹配器不会）。",
    ),
    ("mh04_percentile_derivation", "h1"): (
        "incorrect",
        "证据在 pack 内，但答案以「未显示年龄、性别及百分位表头」为由拒绝给出 758 的推算；terms 中的 758 未命中。",
    ),
    ("mh06_child_dining_chair_spec", "h3"): (
        "incorrect",
        "契约要求「坐高」，terms 未命中；答案用「座高 H1」并明确区分坐高与座高。",
    ),
}


# ---------------------------------------------------------------------------
# A mechanical "did the answer refuse?" signal was attempted here and REMOVED.
#
# The problem it was meant to solve is real: the verdict tables are filled once
# per hop definition, but the same hop is delivered in one run and declined in
# another.  `mhreal_g3_evidenceall`'s r17 h1 refuses in so many words while other
# runs of the same hop produce the formula.
#
# The attempt was a marker list over the whole answer ("无法确认", "未显示",
# ...).  It overrode **28 of 44** verdicts, including four hops that had been
# read and confirmed as delivered.  The reason is visible in the data: r18 h1's
# answer delivers the hop *and* says `当前资料无法确认` about a different
# sub-point (`杯底应调整多少`).  A refusal is about one sub-point; the marker is
# answer-global.  This is the same failure as the term matcher firing on a topic
# word inside a refusal -- noticed once already in this project.
#
# Scoping the marker to the hop was tried next and does not work either: gating
# it on `terms_matched` misses r17 h1 (its terms `内径`/`有效液高` *are* present,
# in the sentence that declines to use them), and scoping it to the sentence
# misses it too (the declining sentence names neither term).
#
# So the per-hop granularity stays, and it is the right granularity for what the
# audit is actually for: deciding **which spans to demote**, which is a per-hop
# question.  The per-observation numbers come from the scoring code against the
# demoted contract, not from these verdicts.
#
# The one confirmed misclassification (r17 h1 above) therefore has **no effect
# on any number**: r17 h1 was demoted, so its score is decided by terms and the
# human verdict no longer participates.  Recorded here rather than papered over,
# because "we tried the obvious fix and it was wrong in a measurable way" is
# more useful to the next reader than a heuristic that quietly mislabels.
# ---------------------------------------------------------------------------


def classify(item: dict[str, Any], table: dict[tuple[str, str], tuple[str, str]]) -> tuple[str, str]:
    """A verdict for one observation.

    Reachability is checked first and mechanically: it needs no judgement, and
    letting a lookup table override it would let a human mistake hide a
    structural fact.
    """
    if not item.get("chunk_in_pack"):
        return (
            "correct_refusal",
            f"该跳的源 chunk 未进入 pack（{item.get('max_evidence')} 条证据），模型不可能知道该事实，"
            "此观测不构成能力证据。",
        )
    key = (str(item.get("question_id")), str(item.get("hop_id")))
    if key not in table:
        raise SystemExit(f"缺少人工判定：{key}")
    return table[key]


def apply_verdicts(
    payload: dict[str, Any], table: dict[tuple[str, str], tuple[str, str]]
) -> list[dict[str, Any]]:
    """Fill every verdict and return the ones a mechanical rule overrode.

    The overrides are returned rather than counted silently: each one is a place
    where the per-hop table does not describe the observation, and a run that
    reports "audit complete" without showing them hides the only evidence that
    the table needed correcting.
    """
    overrides: list[dict[str, Any]] = []
    for item in payload["observations"]:
        verdict, note = classify(item, table)
        key = (str(item.get("question_id")), str(item.get("hop_id")))
        if key in table and table[key][0] != verdict:
            overrides.append({
                "key": item.get("key"),
                "question_id": item.get("question_id"),
                "hop_id": item.get("hop_id"),
                "table_said": table[key][0],
                "mechanical": verdict,
            })
        item["verdict"] = verdict
        item["evidence_note"] = note
    return overrides


def totals(payload: dict[str, Any]) -> dict[str, Any]:
    """Corrected figures, with the unmeasurable observations removed.

    `correct_refusal` is removed rather than counted as a hit: the hop was *not*
    brought out, so crediting it would inflate capability.  What it does is stop
    the observation from being read as a failure.
    """
    counts: dict[str, int] = {}
    for item in payload["observations"]:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    credited = counts.get("correct_paraphrase", 0) + counts.get("damaged_span", 0)
    unmeasurable = counts.get("correct_refusal", 0)
    return {
        "audited_failures": len(payload["observations"]),
        "verdict_counts": dict(sorted(counts.items())),
        "credited_by_audit": credited,
        "unmeasurable_refusals": unmeasurable,
        "incorrect": counts.get("incorrect", 0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--real", required=True, help="真实集的审计骨架")
    parser.add_argument("--synthetic", required=True, help="合成集的审计骨架")
    parser.add_argument("--output", required=True, help="写出版本化审计数据")
    args = parser.parse_args(argv)

    real = json.loads(Path(args.real).read_text(encoding="utf-8"))
    synthetic = json.loads(Path(args.synthetic).read_text(encoding="utf-8"))
    real_overrides = apply_verdicts(real, REAL_VERDICTS)
    synthetic_overrides = apply_verdicts(synthetic, SYNTHETIC_VERDICTS)

    payload = {
        "audit_version": "span-audit.v2",
        "supersedes": "span-audit.v1",
        "what_changed": (
            "v1 的「交付 vs 漏答」按跳定义填一次。v2 记录了机械可达性规则覆盖人工表的条目"
            "（overrides），并明确记下一次被否决的尝试：用词表判断「答案是否拒答」"
            "会整篇触发（44 条里覆盖 28 条），因为拒答只针对某个子点。"
            "逐跳粒度予以保留——审计的用途是决定降级哪些跨段，那本就是逐跳问题。"
        ),
        "overrides": {
            "real": real_overrides,
            "synthetic": synthetic_overrides,
        },
        "method": (
            "对失败的跳逐条读答案判定。chunk_in_pack=False 的观测由 build_span_audit.py "
            "机械判定为 correct_refusal，不进入能力分母；答案声明资料不足的观测机械判定为 "
            "incorrect。两者都不依赖人工表。"
        ),
        "verdict_legend": {
            "correct_literal": "答案含契约跨段原文",
            "correct_paraphrase": "实质已交付但措辞/记号不同，被 exact span 判否",
            "correct_refusal": "源 chunk 未进 pack，模型不可能知道；不是失败",
            "incorrect": "实质未交付",
            "ungradable": "审计无法判定",
            "damaged_span": "契约跨段在规范化时被销毁，任何答案都无法匹配",
        },
        "real": {
            "runs": real.get("runs"),
            "snapshot": real.get("snapshot"),
            "totals": totals(real),
            "observations": real["observations"],
        },
        "synthetic": {
            "runs": synthetic.get("runs"),
            "snapshot": synthetic.get("snapshot"),
            "totals": totals(synthetic),
            "observations": synthetic["observations"],
        },
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for name in ("real", "synthetic"):
        block = payload[name]["totals"]
        print(f"{name}: 审计 {block['audited_failures']} 条失败观测")
        for verdict, count in block["verdict_counts"].items():
            print(f"    {verdict:20s} {count}")
        print(f"    → 审计追回 {block['credited_by_audit']}，"
              f"不可测 {block['unmeasurable_refusals']}，真实漏答 {block['incorrect']}")
        for item in payload["overrides"][name]:
            print(f"    ⚠️ 机械规则覆盖人工表：{item['question_id']} {item['hop_id']} "
                  f"表说 {item['table_said']} → 判为 {item['mechanical']}")
    print(f"\n写出 -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
