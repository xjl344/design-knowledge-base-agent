"""Derive the v2 real-question spec: the same questions, plus partial coverage.

Why a second version
--------------------
The v1 set kept only the 5 real questions whose evidence is *entirely* frozen.
That is the honest headline, but it leaves the set too small to say anything:
5 questions, 2 hops each, and provider timeouts cost some of those.  Meanwhile
10 further questions are partly supported -- and the part that is supported is
sitting in the same frozen snapshot, unused.

So v2 admits those questions *at hop level*: a question joins with only the
hops its evidence can actually prove, and it is labelled partial, with the
uncovered needs named.  What this is not is a bigger set of comparable
questions -- a partial question is scored on a subset of its own needs, so it
is easier than a complete one and the two must never be averaged together.
That is why the two groups are built into separate contracts.

What stays true from v1
-----------------------
* the question text is copied verbatim from `data/test_qa_35_complex.json`; a
  hop is never made to fit by rewording the question;
* every hop still has to prove itself against the chunk it names, which
  `build_multihop_snapshot.py` enforces when the set is built;
* nothing is dropped silently: every one of the 35 questions ends up in
  `complete`, `partial`, or `excluded`, with a per-question reason.

The subjective part is *which* hops were admitted, so that choice lives here,
in code, next to the reason for each one, rather than inside a JSON blob.

Usage::

    python scripts/build_real_specs_v2.py            # write the spec
    python scripts/build_real_specs_v2.py --check    # verify, write nothing
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

SPEC_V1 = ROOT / "data" / "multihop_specs.real.v1.json"
SPEC_V2 = ROOT / "data" / "multihop_specs.real.v2.json"
SOURCE_QUESTIONS = ROOT / "data" / "test_qa_35_complex.json"
FROZEN_SNAPSHOT = ROOT / "data" / "frozen_retrieval_cases.jsonl"

SPEC_VERSION = "multihop-specs.v1"  # schema version; the content version is the filename

# Chunks are named by their real ids so the spec is traceable to the frozen
# snapshot.  These are the only chunks the partial questions draw on.
CHUNK_SIZE_RULES = "bf7e88974c201ee0965a1ff7b9fa02921b4b3130c19f7b1c0e6265367c361746"
CHUNK_DIM_TEMPLATE = "d144020f3eee"  # resolved below; see _resolve_chunk
CHUNK_MINOR_SCOPE = "c665e7582f9d1f2dcd82831d3ed0ed96cb76a10f9fb6fcfa4962ae35762ee022"
CHUNK_ADULT_SCOPE = "3c8a063977bd53fe28e8871333675c3c4608a51b7a0b1f5c87c7d8a78641093a"
CHUNK_FURNITURE_SCOPE = "eb5d0ff3d6ceb05745243f0ca7db6b34792b81be7cf22ef77e20cb722bab93af"
CHUNK_MINOR_TABLES = "cc8b928bf166e75d0482548555e27cf22bbf9e3fcc60208bd4af1c96c1094458"

# Which frozen single-hop case each chunk can be pooled from.  A hop's
# `from_question` must be one of these, and it must also appear in the case's
# `sources`; the builder rejects a hop whose evidence cannot be traced.
FROM_Q17 = "q17_ambiguous"  # 水杯尺寸推导规则 + 关键尺寸决策记录模板
FROM_Q06 = "q06_hit"        # GB/T 26158 未成年人人体尺寸
FROM_Q08 = "q08_hit"        # GB/T 10000 成年人人体尺寸
FROM_Q01 = "q01_hit"        # GB/T 3326 家具尺寸

# The partial questions.  Each entry states the hops the frozen evidence can
# prove, and names what it cannot.  A hop is only admitted when the fact is
# literally in the chunk -- the builder re-checks every span and term, so a
# mistake here fails the build rather than producing an unanswerable hop.
PARTIAL_CASES: list[dict[str, Any]] = [
    {
        "id": "p01_child_grip_dimensions",
        "source_id": "c01",
        "sources": [FROM_Q17, FROM_Q06],
        "risk_level": "high",
        "theme": "儿童人体数据与容量推导",
        "slice_tags": ["multi_hop", "cross_standard", "population"],
        "notes": "儿童手部数据缺失：可覆盖容量公式、验证要求与未成年人百分位制度，不能给出握持区间数值。",
        "unsupported_needs": [
            "儿童手部尺寸与握力研究_中国儿童_2020.pdf 不在冻结快照中，无法给出儿童握持区间的具体数值",
            "儿童实测握持与防倾倒实验",
        ],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "必须使用内径和有效液高，不要直接使用外径和总高度",
                "required_terms": [["内径"], ["有效液高"]],
                "why": "容量推导的输入口径：答案若用外径/总高算容量，这一跳就不该命中。",
            },
            {
                "hop_id": "h2",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "尺寸结论应引用握持实验、圆柱体握持研究、目标人群百分位数据或产品测试",
                "required_terms": [["握持实验"]],
                "why": "首轮样机该验证什么：握持结论必须来自实验/测试而不是推导。",
            },
            {
                "hop_id": "h3",
                "from_question": FROM_Q06,
                "chunk_id": CHUNK_MINOR_SCOPE,
                "expected_span": "本标准给出了未成年人(4岁~17岁)72项人体尺寸所涉及的11个百分位数",
                "required_terms": [["百分位数"]],
                "why": "目标人群与百分位：儿童侧唯一可用的证据制度来源。",
            },
        ],
    },
    {
        "id": "p07_child_straw_cup_evidence",
        "source_id": "c07",
        "sources": [FROM_Q06, FROM_Q17],
        "risk_level": "high",
        "theme": "证据检索范围与实测边界",
        "slice_tags": ["multi_hop", "cross_standard", "population"],
        "notes": "吸嘴直径与开启方式无证据（杯盖规则不在快照里）；可覆盖的只有证据范围与分组。",
        "unsupported_needs": [
            "水杯杯盖选择规则.md 不在冻结快照中，吸嘴直径与开启方式没有任何证据",
            "儿童手部尺寸与握力研究_中国儿童_2020.pdf 不在冻结快照中",
        ],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": FROM_Q06,
                "chunk_id": CHUNK_MINOR_SCOPE,
                "expected_span": "本标准将未成年人分为五个年龄组",
                "required_terms": [["年龄组"]],
                "why": "分组：儿童不是单一人群，五个年龄组是本地证据自带的分组方式。",
            },
            {
                "hop_id": "h2",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "先根据目标人群和握持方式确定候选外径范围",
                "required_terms": [["目标人群"]],
                "why": "可支持输入：候选外径的推导起点是人群与握持方式。",
            },
            {
                "hop_id": "h3",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "不要只根据手长、手宽直接推导杯径",
                "required_terms": [["手长", "手宽"]],
                "why": "实测项目：这一条正是「不能只看成人经验/不能凭手部数据直推」的书面依据。",
            },
        ],
    },
    {
        "id": "p08_child_adult_conflict",
        "source_id": "c08",
        "sources": [FROM_Q17, FROM_Q08],
        "risk_level": "medium",
        "theme": "多人群共用一型的冲突处理",
        "slice_tags": ["multi_hop", "cross_standard", "comparison", "population"],
        "notes": "儿童侧只有制度与分组，没有手部数据；成人侧有完整标准范围。",
        "unsupported_needs": [
            "儿童手部尺寸与握力研究_中国儿童_2020.pdf 不在冻结快照中，儿童侧无法给出握持数据",
            "水杯杯盖选择规则.md 不在冻结快照中",
        ],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "先根据目标人群和握持方式确定候选外径范围",
                "required_terms": [["握持方式"]],
                "why": "分群：候选杯径必须先按人群与握持方式分开取。",
            },
            {
                "hop_id": "h2",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "是否需要针对 P5、P50、P95 人群分别验证",
                "required_terms": [["P5", "P50", "P95"]],
                "why": "冲突的处置方式：不同百分位分别验证，而不是取一个折中值。",
            },
            {
                "hop_id": "h3",
                "from_question": FROM_Q08,
                "chunk_id": CHUNK_ADULT_SCOPE,
                "expected_span": "本文件适用于成年人消费用品、交通、服装、家居、建筑、劳动防护、军事等生产与服务产品",
                "required_terms": [["成年人"]],
                "why": "成人侧的数据来源与适用范围。",
            },
        ],
    },
    {
        "id": "p20_child_adult_diameter_variables",
        "source_id": "c20",
        "sources": [FROM_Q17],
        "risk_level": "medium",
        "theme": "尺寸联动与结构变量",
        "slice_tags": ["multi_hop", "derivation", "dimension"],
        "notes": "只覆盖推导规则一侧：儿童握持外径的候选区间无证据。",
        "unsupported_needs": [
            "儿童手部尺寸与握力研究_中国儿童_2020.pdf 不在冻结快照中，儿童握持外径的候选区间无证据",
        ],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "若直径过大，优先检查是否可以通过减少结构占用、改变杯型或降低目标容量解决",
                "required_terms": [["结构占用"]],
                "why": "可调整的结构变量：直径受约束时的第一顺位解法。",
            },
            {
                "hop_id": "h2",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "若高度过大，优先扩大握持区域或调整杯底",
                "required_terms": [["杯底"]],
                "why": "联动：容量不变时，外径与高度互相牵制，改高度会牵出重心与杯底。",
            },
            {
                "hop_id": "h3",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "不要只根据手长、手宽直接推导杯径",
                "required_terms": [["手长", "手宽"]],
                "why": "「哪些不能凭经验确定」的正面回答。",
            },
        ],
    },
    {
        "id": "p21_tip_over_triage",
        "source_id": "c21",
        "sources": [FROM_Q17],
        "risk_level": "medium",
        "theme": "倾倒问题的排查顺序",
        "slice_tags": ["multi_hop", "derivation", "dimension"],
        "notes": "验证项目清单缺失（水杯设计验证规则不在快照里），排查顺序可覆盖。",
        "unsupported_needs": [
            "水杯设计验证规则.md 不在冻结快照中，没有对应的验证项目清单",
        ],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "检查高度是否导致重心过高、倾倒风险增加或单手操作困难",
                "required_terms": [["倾倒"]],
                "why": "顺序：高度是第一个要查的变量，且直接连着倾倒。",
            },
            {
                "hop_id": "h2",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "额定容量应低于杯口溢流前的最大容量",
                "required_terms": [["额定容量"]],
                "why": "容量侧的约束：装满后的状态才是倾倒问题的输入。",
            },
            {
                "hop_id": "h3",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "是否存在装满热水后烫手或重心偏移",
                "required_terms": [["重心"]],
                "why": "杯底与重心的检查项，来自决策记录模板的风险清单。",
            },
        ],
    },
    {
        "id": "p27_standard_applicability_layers",
        "source_id": "c27",
        "sources": [FROM_Q01, FROM_Q06, FROM_Q17],
        "risk_level": "high",
        "theme": "标准适用层级与禁止迁移",
        "slice_tags": ["multi_hop", "cross_standard", "comparison"],
        "notes": "三份证据的适用对象互不相同，正好构成层级判断；儿童手部数据本身仍缺失。",
        "unsupported_needs": [
            "儿童手部尺寸与握力研究_中国儿童_2020.pdf 不在冻结快照中，无法给出儿童手部数据本身",
        ],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": FROM_Q01,
                "chunk_id": CHUNK_FURNITURE_SCOPE,
                "expected_span": "本标准适用于双柜桌、单柜桌、梳妆桌(梳妆台)、单层桌、扶手椅、靠背椅、折叠椅、长方凳、方凳、圆凳的设计和生产",
                "required_terms": [["家具"]],
                "why": "层级一：3326 的适用对象是家具，不是人体，也不是容器。",
            },
            {
                "hop_id": "h2",
                "from_question": FROM_Q06,
                "chunk_id": CHUNK_MINOR_SCOPE,
                "expected_span": "本标准适用于未成年人用品的设计与生产",
                "required_terms": [["未成年人用品"]],
                "why": "层级二：26158 的适用对象是未成年人用品，给出的是人体尺寸制度。",
            },
            {
                "hop_id": "h3",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "不要只根据手长、手宽直接推导杯径",
                "required_terms": [["手长", "手宽"]],
                "why": "禁止迁移：把椅子座高或手部尺寸直接搬成杯径正是这一条禁止的做法。",
            },
        ],
    },
    {
        "id": "p32_insufficient_evidence_answer",
        "source_id": "c32",
        "sources": [FROM_Q17],
        "risk_level": "high",
        "theme": "证据不足时的作答方式",
        "slice_tags": ["multi_hop", "evidence_boundary"],
        "notes": (
            "本题的正确答案形态是「拒绝给出精确值并说明方法」。契约按跳计分"
            "（证据/方法/不编造），未声明 refusal_requirements——一旦声明，"
            "hop 计分会被关闭（refusal 与 hop 互斥），本题就退出 hop 均值。"
        ),
        "unsupported_needs": [
            "儿童手部尺寸与握力研究_中国儿童_2020.pdf 不在冻结快照中，连「人体数据」也只有成人标准可查",
        ],
        "hops": [
            {
                "hop_id": "h1",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "不要只根据手长、手宽直接推导杯径",
                "required_terms": [["手长", "手宽"]],
                "why": "证据不足：手部尺寸不足以定杯径，这正是「不能给精确值」的依据。",
            },
            {
                "hop_id": "h2",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_DIM_TEMPLATE,
                "expected_span": "尺寸结论应引用握持实验、圆柱体握持研究、目标人群百分位数据或产品测试",
                "required_terms": [["产品测试"]],
                "why": "方法：给出可执行替代路径，而不是只拒答。",
            },
            {
                "hop_id": "h3",
                "from_question": FROM_Q17,
                "chunk_id": CHUNK_SIZE_RULES,
                "expected_span": "本文件用于生成初始设计范围，不替代人体工学实验、结构计算、模具试制或产品标准",
                "required_terms": [["不替代"]],
                "why": "不编造：规则文件自己的免责边界，就是不能补写精确值的原因。",
            },
        ],
    },
]

# Questions whose sources all appear in the snapshot by filename, yet whose
# content cannot answer the question.  This is the finding that a filename
# match is not a content match, and it has to stay visible.
CONTENT_EMPTY_REASONS = {
    "c02": "需要手宽/手长推导杯径；GB/T 10000 在快照里只有食指长、掌围、足部等项目，没有手宽手长",
    "c05": "需要 GB/T 16252 手部尺寸；该标准在快照里只有封面与前言两个 chunk，没有任何数据",
    "c28": "需要 GB/T 16252 手部尺寸；该标准在快照里只有封面与前言两个 chunk，没有任何数据",
}

# Questions with at least one source present but not enough for two provable
# hops.  Admitting them would mean scoring a question on evidence that cannot
# address it.
INSUFFICIENT_REASONS = {
    "c06": "4 个来源里只有 GB/T 10000 在快照里，开启力/杯盖结构/密封可靠性均无证据，凑不出两跳",
    "c24": "3 个来源里只有水杯尺寸推导规则在快照里，挤出吹塑与模具设计指南均缺失，问题本身无证据",
    "c35": "8 个来源里只有水杯尺寸推导规则在快照里，材料/杯盖/密封/注塑/验证五份规则全部缺失",
}


def resolve_chunk(prefix: str) -> str:
    """Expand a chunk-id prefix against the frozen snapshot.

    Hard-coding a 64-hex id from memory is how a spec ends up naming a chunk
    that does not exist; the builder would then fail with a less obvious
    message.  Resolving here makes the prefix the single source of truth.
    """
    matches = set()
    for line in FROZEN_SNAPSHOT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        for document in json.loads(line).get("documents", []):
            chunk_id = str(document.get("chunk_id") or "")
            if chunk_id.startswith(prefix):
                matches.add(chunk_id)
    if len(matches) != 1:
        raise SystemExit(f"chunk 前缀 {prefix!r} 命中 {len(matches)} 个 chunk，必须唯一")
    return matches.pop()


def snapshot_source_names() -> set[str]:
    names = set()
    for line in FROZEN_SNAPSHOT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        for document in json.loads(line).get("documents", []):
            source = str((document.get("metadata") or {}).get("source") or "")
            if source:
                names.add(Path(source).name)
    return names


def build_spec() -> dict[str, Any]:
    v1 = json.loads(SPEC_V1.read_text(encoding="utf-8"))
    source_questions = {
        str(item["id"]): item
        for item in json.loads(SOURCE_QUESTIONS.read_text(encoding="utf-8"))["questions"]
    }
    available = snapshot_source_names()

    complete: list[dict[str, Any]] = []
    for case in v1["cases"]:
        entry = dict(case)
        entry["coverage"] = "complete"
        # v1's real slices carried empty `slice_tags`, which `load_slices`
        # rejects outright -- the real set's slice file could not be loaded by
        # anything.  Tags are required, so every case gets real ones.
        entry.setdefault("slice_tags", ["multi_hop"])
        entry.setdefault("risk_level", "medium")
        entry.setdefault("theme", "")
        entry.setdefault("notes", "")
        complete.append(entry)

    partial: list[dict[str, Any]] = []
    for case in PARTIAL_CASES:
        source = source_questions[case["source_id"]]
        entry = {
            "id": case["id"],
            "coverage": "partial",
            "question": source["question"],
            "sources": case["sources"],
            "risk_level": case["risk_level"],
            "theme": case["theme"],
            "slice_tags": case["slice_tags"],
            "notes": case["notes"],
            "unsupported_needs": case["unsupported_needs"],
            "hops": [
                {
                    "hop_id": hop["hop_id"],
                    "from_question": hop["from_question"],
                    "chunk_id": resolve_chunk(hop["chunk_id"]) if len(hop["chunk_id"]) < 64 else hop["chunk_id"],
                    "expected_span": hop["expected_span"],
                    "required_terms": hop["required_terms"],
                    "why": hop["why"],
                }
                for hop in case["hops"]
            ],
        }
        partial.append(entry)

    kept = {case["source_id"] for case in PARTIAL_CASES}
    kept |= {
        f"c{str(case['id']).split('_', 1)[0].lstrip('r').rjust(2, '0')}"
        for case in complete
    }

    excluded: dict[str, str] = {}
    for question_id, question in source_questions.items():
        if question_id in kept:
            continue
        if question_id in CONTENT_EMPTY_REASONS:
            excluded[question_id] = CONTENT_EMPTY_REASONS[question_id]
            continue
        if question_id in INSUFFICIENT_REASONS:
            excluded[question_id] = INSUFFICIENT_REASONS[question_id]
            continue
        missing = [
            Path(item).name
            for item in question.get("expected_sources", [])
            if Path(item).name not in available
        ]
        if not missing:
            raise SystemExit(
                f"{question_id} 既未入选也未被显式说明，而其来源按文件名都在快照里；"
                "必须给出逐题原因"
            )
        excluded[question_id] = "所需来源全部不在冻结快照中：" + "、".join(missing)

    return {
        "version": SPEC_VERSION,
        "content_version": "multihop-specs.real.v2",
        "description": (
            "真实用户提问的多跳题集 v2。题目原文取自 data/test_qa_35_complex.json，"
            "逐字不改。与 v1 的区别是引入了「按跳准入」：证据只能支撑问题一部分的题，"
            "以 coverage=partial 入选，并逐条列出未覆盖的需求。"
            "部分题只在自身需求的一个子集上计分，比完整题容易，"
            "因此两类题构建成两个独立契约，绝不可混合平均。"
        ),
        "provenance": {
            "source_file": "data/test_qa_35_complex.json",
            "source_ids": sorted(
                f"c{str(case['id']).split('_', 1)[0].lstrip('r').rjust(2, '0')}"
                for case in complete
            ),
            "partial_ids": sorted(case["source_id"] for case in PARTIAL_CASES),
            "excluded": {"ids": sorted(excluded), "per_id": dict(sorted(excluded.items()))},
            "note": (
                "v1 只收完全可覆盖的 5 题，并给出一句概括性的排除理由。"
                "v2 逐题给出排除原因：来源全缺、来源在但内容为空（c02/c05/c28）、"
                "或来源不足两跳（c06/c24/c35）——这三类是不同的结论，不应共用一个理由。"
                "另外 v1 的真实契约 generated_from 字段写成 data/multihop_specs.json（合成集），"
                "与自身输入不符；该文件已按哈希记入历史运行，故不重写，仅在此记录。"
            ),
        },
        "cases": complete + partial,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成真实多跳题集 v2 spec（含按跳准入）")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()

    spec = build_spec()
    if args.check:
        current = json.loads(SPEC_V2.read_text(encoding="utf-8"))
        if current != spec:
            raise SystemExit(f"--check 失败：{SPEC_V2} 与重新生成的结果不一致")
        print(json.dumps({"check": "ok", "cases": len(spec["cases"])}, ensure_ascii=False))
        return 0
    SPEC_V2.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(SPEC_V2),
        "complete": sum(1 for case in spec["cases"] if case["coverage"] == "complete"),
        "partial": sum(1 for case in spec["cases"] if case["coverage"] == "partial"),
        "excluded": len(spec["provenance"]["excluded"]["ids"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
