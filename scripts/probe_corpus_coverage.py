"""Probe whether the knowledge base actually covers a process family.

Why this is a script and not a one-off check
-------------------------------------------
Adding documents and seeing the chunk count go up proves nothing.  The only
question that matters is whether a *question a student would actually ask*
retrieves the right passage.  This script asks that question and can fail.

Design notes
------------
1. **Questions are phrased the way a designer would ask**, not the way the
   document phrases it.  Copying the document's wording would test string
   matching, not retrieval.
2. **A miss and a low rank are different failures.**  A document at rank 6 is
   found but unusable when the app passes only 5 evidence items; the report
   prints both so the two are not conflated.
3. **Negative controls are included.**  A question whose answer is genuinely
   absent should return *something* -- the index always does -- so the probe
   records whether the system would have been able to tell.  That is a
   separate signal from hit rate and must not be folded into it.
4. **The default run disables the cross-encoder.**  On CPU the reranker costs
   ~150 s per question, which makes a 10-question probe unusable.  Without it
   the probe measures first-stage recall, which is the question "is the
   knowledge in the index at all".  Pass --rerank to measure the shipped path.

Usage
-----
    python scripts/probe_corpus_coverage.py
    python scripts/probe_corpus_coverage.py --rerank --top-k 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Probe:
    family: str
    question: str
    expect: str | None      # substring of the source path that must appear
    note: str = ""          # what the correct answer looks like
    # The literal string the retrieved chunk must contain to count as an
    # answer.  Checking only `expect` (the file name) overstates coverage:
    # a document can be retrieved while the passage holding the number is
    # not, and the generator then correctly refuses -- which looks exactly
    # like "the knowledge base has no answer" from the outside.
    anchor: str | None = None


PROBES: tuple[Probe, ...] = (
    # ---- Sheet metal ----------------------------------------------------
    Probe(
        family="Sheet_Metal",
        question="1.5mm厚的不锈钢板，折弯处离孔最近能留多少距离？",
        expect="Sheet_Metal",
        note="最小法兰长度 = 4 倍板厚（1.5mm 板即 6mm）",
        anchor="4 times the material thickness",
    ),
    Probe(
        family="Sheet_Metal",
        question="钣金件最小能做多大尺寸？",
        expect="Sheet_Metal",
        note="平板 6.35x6.35mm，成型件 12.7x12.7mm",
        anchor="6.35mm x 6.35mm",
    ),
    # ---- Injection molding ----------------------------------------------
    Probe(
        family="Injection_Molding",
        question="PC注塑件壁厚一般做多少毫米？",
        expect="Injection_Molding",
        note="Polycarbonate 1.016-3.81mm",
        anchor="1.016mm - 3.81mm",
    ),
    Probe(
        family="Injection_Molding",
        question="注塑件垂直面需要多少脱模斜度？",
        expect="Injection_Molding",
        note="垂直面 0.5 度，一般 2 度，止口最小 3 度",
        anchor="VERTICAL FACES",
    ),
    # ---- CNC ------------------------------------------------------------
    Probe(
        family="CNC_Machining",
        question="CNC铣削最小能加工多薄的特征？",
        expect="CNC_Milling",
        note="特征厚度建议 >0.020 in (0.51mm)",
        anchor="0.020 in",
    ),
    Probe(
        family="CNC_Machining",
        question="铝件用CNC铣削最大能做多大？",
        expect="CNC_Milling",
        note="3轴 559x356x95.3mm，5轴 254x178x95.3mm",
        anchor="559mm x 356mm x 95.3mm",
    ),
    Probe(
        family="CNC_Machining",
        question="车削件的壁厚最薄能到多少？",
        expect="CNC_Turning",
        note="0.020 in (0.51mm)",
        anchor="0.51mm",
    ),
    # ---- Overmolding ----------------------------------------------------
    Probe(
        family="Assembly_Joining",
        question="包胶成型时对基材和粘接有什么要求？",
        expect="LSR_Overmolding",
        note="该文档实为注塑模具通用准则，无包胶粘接内容 -> 内容缺口",
    ),
    # ---- Additive manufacturing -----------------------------------------
    Probe(
        family="Additive_3D_Printing",
        question="SLA光固化打印的最小壁厚能做到多少？",
        expect="Additive_3D_Printing",
        note="文档只有标题、数值在图片里 -> 内容缺口",
    ),
    Probe(
        family="Additive_3D_Printing",
        question="SLS尼龙件最小壁厚和层厚是多少？",
        expect="Additive_3D_Printing",
        note="最小壁厚 0.762mm，层厚 0.1016mm",
        anchor="0.762mm",
    ),
    # ---- Die casting ----------------------------------------------------
    Probe(
        family="Die_Casting",
        question="压铸件的壁厚一般取多少？太厚会有什么问题？",
        expect="Die_Casting",
        note="Xometry 只给定性原则，无数值 -> 内容缺口",
    ),
    Probe(
        family="Die_Casting",
        question="压铸件为什么不能做尖角？圆角怎么定？",
        expect="Die_Casting",
        note="圆角改善金属流动、减少热集中、防止冷隔",
        anchor="cold shuts",
    ),
    # ---- Surface treatment ----------------------------------------------
    Probe(
        family="Surface_Treatment",
        question="铝件阳极氧化膜厚一般是多少微米？",
        expect="Surface_Treatment",
        note="1 mil = 25 微米；防护装饰性 <0.4 mil 等分级",
        anchor="25 microns equals 1.0 mil",
    ),
    Probe(
        family="Surface_Treatment",
        question="阳极氧化会影响零件尺寸吗？公差怎么处理？",
        expect="Surface_Treatment",
        note="AAC 公报讲膜厚分级与合金选择，未讲尺寸增量 -> 内容缺口",
    ),
    # ---- Regression control: an older capability must survive -----------
    Probe(
        family="(对照) 旧能力",
        question="直筒水杯内径65mm、有效液高120mm，理想容量怎么估算？",
        expect="水杯尺寸推导规则",
        note="按圆柱体积公式估算，不能直接当额定容量",
    ),
    Probe(
        family="(对照) 旧能力",
        question="Tritan和PC做透明杯体怎么选？",
        expect="PC与Tritan选择规则",
        note="门槛项、比较项、验证顺序",
    ),
    # ---- Negative control: answer is genuinely absent -------------------
    Probe(
        family="(负样本) 应缺",
        question="压铸件的最小壁厚一般取多少？",
        expect=None,
        note="知识库没有压铸壁厚数值；正确行为是说明证据不足，不是编一个数",
    ),
)


def run_probe(probe: Probe, top_k: int) -> dict[str, object]:
    from src.retriever import retrieve_documents

    started = time.time()
    try:
        docs = retrieve_documents(probe.question, top_k=top_k)
    except Exception as exc:
        return {
            "question": probe.question,
            "family": probe.family,
            "error": str(exc),
            "elapsed": time.time() - started,
        }
    elapsed = time.time() - started

    ranks: list[int] = []
    answer_rank: int | None = None
    sources: list[str] = []
    for i, doc in enumerate(docs, 1):
        src = str(doc.metadata.get("source", ""))
        sources.append(src)
        if probe.expect and probe.expect in src:
            ranks.append(i)
        if probe.anchor and probe.anchor.lower() in doc.page_content.lower():
            if answer_rank is None:
                answer_rank = i

    return {
        "question": probe.question,
        "family": probe.family,
        "expect": probe.expect,
        "note": probe.note,
        "anchor": probe.anchor,
        "best_rank": min(ranks) if ranks else None,
        "answer_rank": answer_rank,
        "hits": len(ranks),
        "returned": len(docs),
        "sources": sources,
        "elapsed": round(elapsed, 1),
    }


def summarise(results: list[dict[str, object]], top_k: int, pass_at: int) -> dict[str, object]:
    scored = [r for r in results if r.get("expect")]
    negatives = [r for r in results if not r.get("expect")]

    hit_at_pass = sum(1 for r in scored if r["best_rank"] and r["best_rank"] <= pass_at)
    hit_any = sum(1 for r in scored if r["best_rank"])
    answer_hit = sum(
        1 for r in scored
        if r.get("answer_rank") and r["answer_rank"] <= pass_at
    )

    by_family: dict[str, list[dict[str, object]]] = {}
    for r in scored:
        by_family.setdefault(str(r["family"]), []).append(r)

    family_rows = []
    for fam, rows in by_family.items():
        ok = sum(1 for r in rows if r["best_rank"] and r["best_rank"] <= pass_at)
        ranks = [r["best_rank"] for r in rows if r["best_rank"]]
        family_rows.append(
            {
                "family": fam,
                "passed": ok,
                "total": len(rows),
                "ranks": ranks,
            }
        )

    return {
        "top_k": top_k,
        "pass_at": pass_at,
        "scored": len(scored),
        "hit_at_pass": hit_at_pass,
        "hit_any": hit_any,
        "hit_rate_at_pass": round(hit_at_pass / len(scored), 3) if scored else None,
        "hit_rate_any": round(hit_any / len(scored), 3) if scored else None,
        # Answer-level recall is the number that matters: a document in the
        # top-k means nothing if the sentence holding the value is not there.
        "answer_scored": len([r for r in scored if r.get("anchor")]),
        "answer_hit_at_pass": answer_hit,
        "answer_rate": (
            round(answer_hit / len([r for r in scored if r.get("anchor")]), 3)
            if any(r.get("anchor") for r in scored) else None
        ),
        "families": family_rows,
        "negatives": [
            {"question": r["question"], "top_source": (r["sources"] or [""])[0]}
            for r in negatives
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="探测知识库对工艺族的覆盖")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--pass-at", type=int, default=5,
                        help="应用实际传入的证据条数（GENERATOR_MAX_EVIDENCE）")
    parser.add_argument("--rerank", action="store_true",
                        help="启用交叉编码重排（慢，但等于线上链路）")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--min-rate", type=float, default=0.8,
                        help="命中率低于该值时返回非零（闸门）")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not args.rerank:
        os.environ["RERANKER_ENABLED"] = "false"
    os.environ.setdefault("EMBEDDING_DEVICE", "cpu")

    print(f"探测 {len(PROBES)} 题 | top_k={args.top_k} | pass@{args.pass_at} | "
          f"重排={'开' if args.rerank else '关'}\n")

    results: list[dict[str, object]] = []
    for i, probe in enumerate(PROBES, 1):
        r = run_probe(probe, args.top_k)
        results.append(r)
        if "error" in r:
            print(f"[{i:2d}] 失败 {probe.question[:30]} -> {r['error']}")
            continue
        rank = r["best_rank"]
        arank = r.get("answer_rank")
        if not probe.anchor:
            mark, rank_text = "-", "无锚点(内容缺口)"
        elif arank and arank <= args.pass_at:
            mark, rank_text = "✓", f"答案第{arank}位"
        elif arank:
            mark, rank_text = "~", f"答案第{arank}位(超窗)"
        else:
            mark, rank_text = "✗", f"文档{'第%d位' % rank if rank else '未召回'}·答案未召回"
        print(f"[{i:2d}] {mark} {probe.family:14s} {probe.question[:24]:26s} {rank_text}  ({r['elapsed']}s)", flush=True)

    summary = summarise(results, args.top_k, args.pass_at)

    print("\n--- 分族 ---")
    for row in summary["families"]:
        print(f"  {row['family']:16s} {row['passed']}/{row['total']} 通过  排名={row['ranks']}")

    print("\n--- 负样本（应缺的资料）---")
    for neg in summary["negatives"]:
        print(f"  {neg['question'][:34]:36s} top1={Path(neg['top_source']).name[:44]}")

    print(
        f"\n文档命中 pass@{args.pass_at}: {summary['hit_at_pass']}/{summary['scored']}"
        f" = {summary['hit_rate_at_pass']}"
    )
    print(
        f"答案命中 pass@{args.pass_at}: {summary['answer_hit_at_pass']}/"
        f"{summary['answer_scored']} = {summary['answer_rate']}"
        f"   （只有设了锚点的题参与；文档命中 ≠ 答案命中）"
    )

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps({"summary": summary, "results": results},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"明细已写入 {args.json_out}")

    # The gate is answer-level on purpose.  A document in the top-k is a much
    # weaker claim: the same file can supply several unrelated chunks while
    # the passage holding the number is never retrieved.  Gating on document
    # hit rate would report 0.875 here against a real answer rate of 0.4.
    rate = summary["answer_rate"] or 0.0
    print(
        f"\n闸门（答案级）: {rate} vs 阈值 {args.min_rate}"
        f"   [参考] 文档级 {summary['hit_rate_at_pass']}"
    )
    if rate < args.min_rate:
        print("闸门未通过：答案级召回不足，补语料不等于可回答。")
        return 2
    print("闸门通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
