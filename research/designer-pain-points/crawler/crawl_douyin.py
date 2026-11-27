# -*- coding: utf-8 -*-
"""抖音搜索采集（CDP 直连已登录 Edge）。

说明：
- 抖音搜索页未登录即可出结果，但详情页/评论需要登录。
- 搜索结果没有可提取的 /video/ 链接，本脚本保留「搜索页 URL + 标题 + 作者 + 日期 + 播放量」，
  链接指向搜索页，可复核但非单条直链——引用时必须注明这一点。
"""
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_client import Client  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "网络证据")
os.makedirs(OUTDIR, exist_ok=True)

TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
PLAY_RE = re.compile(r"^[\d.]+[万亿]?$")
DATE_RE = re.compile(r"^[·•]?\s*\d{4}年\d{1,2}月\d{1,2}日$|^[·•]?\s*\d{1,2}月\d{1,2}日$|^[·•]?\s*[前后]\d+\s*(小时|天|周|月)前$")


def parse(body):
    lines = [l.strip() for l in body.split("\n")]
    cards = []
    i = 0
    n = len(lines)
    while i < n:
        if TIME_RE.match(lines[i]):
            dur = lines[i]
            j = i + 1
            play = ""
            if j < n and PLAY_RE.match(lines[j]):
                play = lines[j]
                j += 1
            title = lines[j] if j < n else ""
            k = j + 1
            author = ""
            date = ""
            if k < n and lines[k].startswith("@"):
                author = lines[k]
                k += 1
            if k < n and DATE_RE.match(lines[k]):
                date = lines[k]
                k += 1
            if title and len(title) > 6 and "抖音" not in title:
                cards.append({"duration": dur, "play": play, "title": title,
                              "author": author, "date": date})
            i = k
        else:
            i += 1
    return cards


def main():
    kws = sys.argv[1:] or ["工业设计 开模"]
    c = Client()
    all_data = {}
    try:
        for kw in kws:
            url = "https://www.douyin.com/search/" + urllib.parse.quote(kw)
            print(f"=== 抖音搜索：{kw} ===")
            c.goto(url, wait=10)
            for _ in range(4):
                c.eval("window.scrollBy(0, 1500)")
                time.sleep(2)
            body = str(c.eval("document.body.innerText") or "")
            cards = parse(body)
            for x in cards:
                x["keyword"] = kw
                x["search_url"] = url
            print(f"  解析到 {len(cards)} 条")
            all_data[kw] = cards
            safe = re.sub(r"[^\w\u4e00-\u9fa5]", "_", kw)
            p = os.path.join(OUTDIR, f"douyin_{safe}.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cards, f, ensure_ascii=False, indent=2)
            print(f"  已写出 {p}")
    finally:
        c.close()

    lines = ["# 抖音搜索采集结果\n",
             "> ⚠️ 链接为搜索页，非单条视频直链；引用时须注明「来源：抖音搜索页，可复核」。\n"]
    for kw, cards in all_data.items():
        lines.append(f"\n## {kw}（{len(cards)} 条）\n")
        for i, r in enumerate(cards, 1):
            lines.append(f"{i}. **{r['title'][:80]}**")
            lines.append(f"   - {r['author']}｜{r['date']}｜{r['duration']}｜播放 {r['play']}")
            lines.append(f"   - {r['search_url']}")
    p = os.path.join(OUTDIR, "抖音_采集汇总.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("汇总已写出", p)


if __name__ == "__main__":
    main()
