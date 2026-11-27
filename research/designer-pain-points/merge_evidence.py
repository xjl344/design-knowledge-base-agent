# -*- coding: utf-8 -*-
"""把三轮调研记录合并成一张可比较的表。

不做判断，只做抽取与汇总。方向归属靠 DIRECTION_MAP，需要人工维护。
"""
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(BASE, "results")

# 来源文件 -> (轮次, 平台/语种)
FILES = {
    "agent1_s01_s03.md": ("R1", "Core77"),
    "agent2_s04_s05.md": ("R1", "Core77"),
    "agent3_s06_s08.md": ("R1", "Core77"),
    "agent4_tools_jd.md": ("R1", "工具/JD"),
    "round2_en_forums.md": ("R2", "英文论坛"),
    "round2_cn_forums.md": ("R2", "中文社区"),
    "round3_bili_D.md": ("R3", "B站"),
    "round3_social_media.md": ("R3", "抖音/小红书"),
}

HEAD = re.compile(r"^#{2,3}\s+([A-Z]+-[A-Za-z0-9\-]+)\s*(.*)$")
SCORE = re.compile(
    r"发生频率\s*(\d).*?严重程度\s*(\d).*?解决成本\s*(\d).*?"
    r"未满足程度\s*(\d).*?AI\s*适配度\s*(\d).*?改变意愿\s*(\d)",
    re.S,
)
GRADE = re.compile(r"证据等级\*\*[：:]\s*\**\s*(direct|indirect|inferred)", re.I)
COUNTER = re.compile(r"是否反例\*\*[：:]\s*\**\s*([^\n]+)")
LINK = re.compile(r"https?://[^\s\)\]<>，。；]+")


def parse(path, tag):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lines = text.split("\n")
    recs = []
    for i, line in enumerate(lines):
        m = HEAD.match(line)
        if not m:
            continue
        rid, title = m.group(1), m.group(2).strip()
        block = "\n".join(lines[i + 1: i + 60])
        # 截断到下一条记录
        nxt = next((j for j in range(i + 1, min(i + 60, len(lines)))
                    if HEAD.match(lines[j])), None)
        if nxt:
            block = "\n".join(lines[i + 1: nxt])
        sc = SCORE.search(block)
        if not sc:
            continue
        g = GRADE.search(block)
        c = COUNTER.search(block)
        lk = LINK.search(block)
        recs.append({
            "id": rid,
            "title": title,
            "file": os.path.basename(path),
            "round": tag[0],
            "platform": tag[1],
            "freq": int(sc.group(1)),
            "severity": int(sc.group(2)),
            "cost": int(sc.group(3)),
            "unmet": int(sc.group(4)),
            "ai": int(sc.group(5)),
            "will": int(sc.group(6)),
            "grade": (g.group(1).lower() if g else "?"),
            "counter": (c.group(1).strip()[:40] if c else ""),
            "link": (lk.group(0).rstrip(".,;") if lk else ""),
        })
    return recs


def main():
    allrecs = []
    for fn, tag in FILES.items():
        p = os.path.join(RES, fn)
        if not os.path.exists(p):
            print("MISSING:", fn)
            continue
        r = parse(p, tag)
        print(f"{fn:28s} {len(r):3d} 条")
        allrecs.extend(r)
    with open(os.path.join(BASE, "merged_raw.json"), "w", encoding="utf-8") as fh:
        json.dump(allrecs, fh, ensure_ascii=False, indent=1)
    print("总计", len(allrecs))
    # 无链接的挑出来
    nolink = [r for r in allrecs if not r["link"]]
    print("无链接:", len(nolink), [r["id"] for r in nolink])


if __name__ == "__main__":
    main()
