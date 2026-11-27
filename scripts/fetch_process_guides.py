"""Fetch public DFM design guidelines into the local knowledge base.

Why this exists
---------------
The knowledge base covered materials and standards well but was thin on the
one thing design students actually lack: **what a process can physically
produce** (minimum wall, minimum bend radius, hole-to-edge distance,
achievable tolerance).  That knowledge lives in manufacturer design guides,
not in textbooks -- a textbook says "sheet metal can be bent", a fabricator's
guide says "hole-to-bend must be at least 0.38 mm or the hole distorts".

Rules this script follows
-------------------------
1. **Tables become Markdown tables.**  A tolerance table flattened into prose
   loses the row/column relationship that makes the number usable.  "0.005 in"
   means nothing once you can no longer see which header column it sat under.
2. **Every file carries its source URL and fetch date.**  A claim in the index
   must be traceable to a page, otherwise it is indistinguishable from
   something the model invented.
3. **Only the article body is kept.**  Navigation, cookie banners and footers
   would otherwise be embedded and retrieved as if they were evidence.
4. **Nothing is written into the repository's version control.**  These pages
   are third-party copyrighted material; `data/documents/` is gitignored for
   exactly this reason.

Usage
-----
    python scripts/fetch_process_guides.py --list
    python scripts/fetch_process_guides.py --family sheet_metal
    python scripts/fetch_process_guides.py --all
    python scripts/fetch_process_guides.py --url <url> --family <name> --title <t>
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
from lxml import html as LH

ROOT = Path(__file__).resolve().parent.parent
PROCESS_DIR = ROOT / "data" / "documents" / "设计知识库" / "05_制造工艺"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 40

# Tags whose text never belongs in the extracted article.
DROP_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "form")


@dataclass(frozen=True)
class Source:
    family: str          # target folder under 05_制造工艺
    slug: str            # output file stem
    title: str           # human-readable title, written into the file header
    url: str
    publisher: str       # who publishes it; this is the authority signal


# Only official manufacturer pages are listed here.  Aggregator blogs and
# Q&A sites were deliberately excluded: they paraphrase, they omit the table
# headers, and they cannot be cited.
SOURCES: tuple[Source, ...] = (
    Source(
        family="Injection_Molding",
        slug="Protolabs_Injection_Molding_Design_Guidelines",
        title="塑料注塑成型设计准则（Design Guidelines for Injection Molding）",
        url="https://www.protolabs.com/services/injection-molding/plastic-injection-molding/design-guidelines/",
        publisher="Protolabs",
    ),
    Source(
        family="Sheet_Metal",
        slug="Protolabs_Sheet_Metal_Design_Guidelines",
        title="钣金加工设计准则（Design Guidelines for Sheet Metal Fabrication）",
        url="https://www.protolabs.com/services/sheet-metal-fabrication/design-guidelines/",
        publisher="Protolabs",
    ),
    Source(
        family="CNC_Machining",
        slug="Protolabs_CNC_Milling_Design_Guidelines",
        title="CNC 铣削设计准则（Design Guidelines for CNC Milling）",
        url="https://www.protolabs.com/services/cnc-machining/cnc-milling/design-guidelines/",
        publisher="Protolabs",
    ),
    Source(
        family="CNC_Machining",
        slug="Protolabs_CNC_Turning_Design_Guidelines",
        title="CNC 车削设计准则（Design Guidelines for CNC Turning）",
        url="https://www.protolabs.com/services/cnc-machining/cnc-turning/design-guidelines/",
        publisher="Protolabs",
    ),
    Source(
        family="Assembly_Joining",
        slug="Protolabs_LSR_Overmolding_Design_Guidelines",
        title="液态硅胶与包胶成型设计准则（Design Guidelines for LSR / Overmolding）",
        url="https://www.protolabs.com/services/injection-molding/liquid-silicone-rubber-molding/design-guidelines/",
        publisher="Protolabs",
    ),
    # ---- Additive manufacturing -----------------------------------------
    # The "Design for 3D Printing Toolkit" page was tried and rejected: it is
    # a resource index (links plus one-line blurbs), not a guideline.  The
    # per-process service pages do carry the numbers (build envelope, layer
    # thickness, minimum wall), so those are used instead.
    Source(
        family="Additive_3D_Printing",
        slug="Protolabs_SLA_Design_Guidelines",
        title="SLA 光固化 3D 打印设计准则（Stereolithography Design Guidelines）",
        url="https://www.protolabs.com/services/3d-printing/stereolithography/",
        publisher="Protolabs",
    ),
    Source(
        family="Additive_3D_Printing",
        slug="Protolabs_SLS_Design_Guidelines",
        title="SLS 激光烧结 3D 打印设计准则（Selective Laser Sintering Design Guidelines）",
        url="https://www.protolabs.com/services/3d-printing/selective-laser-sintering/",
        publisher="Protolabs",
    ),
    # ---- Die casting -----------------------------------------------------
    Source(
        family="Die_Casting",
        slug="Xometry_Die_Casting_Design_Tips",
        title="压铸零件设计要点（Top Design Tips for Die Casting Parts）",
        url="https://www.xometry.com/resources/industry-design-tips/die-casting-tips/",
        publisher="Xometry",
    ),
    # ---- Surface treatment ----------------------------------------------
    # anodizing.org's landing page is an index of section titles with no
    # content; the Technical Bulletin and Reference Guide underneath it do
    # carry coating thickness values, so only those two are registered.
    Source(
        family="Surface_Treatment",
        slug="AAC_Specifying_Anodized_Aluminum",
        title="如何指定阳极氧化铝（Specifying Anodized Aluminum，AAC 技术公报）",
        url="https://www.anodizing.org/specifying-anodized-aluminum/",
        publisher="Aluminum Anodizers Council (AAC)",
    ),
    Source(
        family="Surface_Treatment",
        slug="AAC_Anodizing_Reference_Guide",
        title="阳极氧化参考指南（Anodizing Reference Guide）",
        url="https://www.anodizing.org/anodizing-reference-guide/",
        publisher="Aluminum Anodizers Council (AAC)",
    ),
)


# --------------------------------------------------------------------------
# HTML -> Markdown
# --------------------------------------------------------------------------


def _clean(text: str) -> str:
    """Collapse whitespace runs; the HTML has newlines and nbsp everywhere."""
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _table_to_markdown(table) -> tuple[str, set[str]]:
    """Render an HTML table as a Markdown table, plus the cell texts it used.

    Tolerances only mean something next to their header, so the header row is
    kept and padded to the widest row; ragged rows are filled with blanks
    rather than being dropped.

    The cell set is returned so the caller can suppress the loose duplicate
    text nodes the same page emits next to the table -- those fragments would
    otherwise be indexed as separate, context-free numbers.
    """
    rows: list[list[str]] = []
    for tr in table.iter("tr"):
        cells = [_clean("".join(c.itertext())) for c in tr if c.tag in ("td", "th")]
        if any(cells):
            rows.append(cells)
    if not rows:
        return "", set()

    seen_cells = {c for row in rows for c in row if c}
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]

    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines), seen_cells


def _node_to_markdown(node) -> str:
    """Convert one block-level element into Markdown (or '' to skip)."""
    tag = node.tag if isinstance(node.tag, str) else ""

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        text = _clean("".join(node.itertext()))
        return f"{'#' * level} {text}" if text else ""

    if tag == "table":
        # Tables are handled in extract_article so their cell texts can be
        # used to suppress the loose duplicates the page emits beside them.
        return ""

    if tag in ("ul", "ol"):
        items = []
        for li in node.iter("li"):
            text = _clean("".join(li.itertext()))
            if text:
                items.append(f"- {text}")
        return "\n".join(items)

    if tag == "p":
        text = _clean("".join(node.itertext()))
        return text

    if tag in ("figure", "figcaption"):
        text = _clean("".join(node.itertext()))
        return f"> 图注：{text}" if text else ""

    return ""


def extract_article(page_html: str) -> str:
    """Extract the article body from a design-guideline page as Markdown."""
    doc = LH.fromstring(page_html)
    for tag in DROP_TAGS:
        for node in doc.iter(tag):
            node.getparent().remove(node)

    main = None
    for selector in (".//main", ".//article", ".//body"):
        candidate = doc.find(selector)
        if candidate is not None:
            main = candidate
            break
    if main is None:
        return ""

    blocks: list[str] = []
    table_cells: set[str] = set()
    for node in main.iter():
        if not isinstance(node.tag, str):
            continue

        if node.tag == "table":
            md, cells = _table_to_markdown(node)
            if md:
                blocks.append(md)
                table_cells |= cells
            continue

        md = _node_to_markdown(node)
        if not md:
            continue

        # A <ul> already contains its <li>; do not re-emit children as blocks.
        if md in blocks[-1:]:
            continue
        # Suppress loose duplicates of table cells (context-free numbers).
        # The page splits a single cell into several sibling nodes, so the
        # fragment is a *substring* of the cell text, not equal to it.
        if (
            node.tag in ("p", "div", "span")
            and len(md) >= 6
            and any(md in cell for cell in table_cells)
        ):
            continue
        blocks.append(md)

    # Deduplicate consecutive identical blocks (nested divs repeat text).
    deduped: list[str] = []
    for b in blocks:
        if not deduped or deduped[-1] != b:
            deduped.append(b)
    return "\n\n".join(deduped)


# --------------------------------------------------------------------------
# Fetch + write
# --------------------------------------------------------------------------


def fetch(src: Source) -> tuple[str, int]:
    resp = requests.get(
        src.url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,zh-CN;q=0.8"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text, resp.status_code


def render_document(src: Source, body: str) -> str:
    today = dt.date.today().isoformat()
    header = (
        f"# {src.title}\n\n"
        f"- 来源：{src.publisher}\n"
        f"- 原文地址：{src.url}\n"
        f"- 抓取日期：{today}\n"
        f"- 文档类型：厂商官方设计准则（DFM），给出尺寸上限、公差、最小特征等落地约束\n"
        f"- 使用限制：原文版权归 {src.publisher} 所有，仅本地检索使用，不随仓库分发\n\n"
        f"---\n\n"
    )
    return header + body + "\n"


def save(src: Source, text: str) -> Path:
    out_dir = PROCESS_DIR / src.family
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{src.slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def run(src: Source, dry_run: bool = False) -> bool:
    try:
        page_html, status = fetch(src)
    except Exception as exc:  # network or HTTP failure
        print(f"  [失败] {src.slug}: {exc}")
        return False

    body = extract_article(page_html)
    if len(body) < 500:
        print(f"  [跳过] {src.slug}: 正文仅 {len(body)} 字符，可能页面结构变了")
        return False

    text = render_document(src, body)
    if dry_run:
        print(f"  [试跑] {src.slug}: HTTP {status}, 正文 {len(body)} 字符")
        return True

    path = save(src, text)
    print(f"  [完成] {src.slug}: 正文 {len(body)} 字符 -> {path.relative_to(ROOT)}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取厂商公开的设计准则文档")
    parser.add_argument("--list", action="store_true", help="列出已登记来源")
    parser.add_argument("--family", help="只抓某一工艺族，例如 Sheet_Metal")
    parser.add_argument("--all", action="store_true", help="抓全部已登记来源")
    parser.add_argument("--url", help="临时抓取一个未登记的地址")
    parser.add_argument("--title", default="", help="配合 --url 使用的标题")
    parser.add_argument("--slug", default="", help="配合 --url 使用的文件名")
    parser.add_argument("--dry-run", action="store_true", help="只检查正文长度，不写文件")
    args = parser.parse_args(argv)

    if args.list:
        for s in SOURCES:
            print(f"{s.family:20s} {s.slug:46s} {s.publisher}")
        return 0

    targets: list[Source] = []
    if args.url:
        if not (args.title and args.slug):
            parser.error("--url 需要同时提供 --title 和 --slug")
        targets = [
            Source(
                family=args.family or "Process_Reference",
                slug=args.slug,
                title=args.title,
                url=args.url,
                publisher="(见原文地址)",
            )
        ]
    elif args.all:
        targets = list(SOURCES)
    elif args.family:
        targets = [s for s in SOURCES if s.family == args.family]
        if not targets:
            print(f"没有登记 {args.family} 族的来源；用 --list 查看可用族")
            return 1
    else:
        parser.print_help()
        return 1

    ok = 0
    for s in targets:
        print(f"[抓取] {s.family} / {s.slug}")
        ok += 1 if run(s, dry_run=args.dry_run) else 0

    print(f"\n完成 {ok}/{len(targets)}")
    return 0 if ok == len(targets) else 2


if __name__ == "__main__":
    sys.exit(main())
