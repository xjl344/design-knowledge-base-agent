# -*- coding: utf-8 -*-
"""B站爬虫 v2：走官方 API（在浏览器内 fetch，自动带 cookie）。

思路：
  1. 搜索结果页拿 BV 号
  2. /x/web-interface/view?bvid=BVxxx  → 拿 aid + 简介
  3. /x/v2/reply?type=1&oid=<aid>      → 拿评论
比直接打开视频页快得多，也不会超时。

用法：python crawl_bili_api.py "关键词1" "关键词2" ...
输出：02-深度调研/网络证据/bili_api_<关键词>.json
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

JS_SEARCH = """
(() => {
  const NL = String.fromCharCode(10);
  const out = [];
  for (const el of document.querySelectorAll('.bili-video-card')) {
    const a = el.querySelector('a[href*="video/BV"]');
    if (!a) continue;
    const m = a.href.match(/BV[0-9A-Za-z]+/);
    if (!m) continue;
    const parts = el.innerText.split(NL).map(s => s.trim()).filter(s => s);
    out.push({
      bvid: m[0],
      title: parts.length >= 4 ? parts[3] : '',
      author: parts.length >= 5 ? parts[4] : '',
      date: parts.length >= 6 ? parts[5] : '',
      stats: parts.slice(0, 3).join(' / '),
    });
  }
  return out;
})()
"""


def fetch_json(c, url):
    """在浏览器内用同步 XHR 请求（比 awaitPromise 版 fetch 可靠得多）。"""
    js = """
    (() => {
      try {
        const x = new XMLHttpRequest();
        x.open('GET', %s, false);
        x.send();
        return JSON.parse(x.responseText);
      } catch (e) { return {error: String(e)}; }
    })()
    """ % json.dumps(url)
    return c.eval(js)


def main():
    kws = sys.argv[1:] or ["独居老人 智能家居"]
    c = Client()
    all_data = {}
    try:
        for kw in kws:
            print(f"=== B站搜索：{kw} ===")
            c.goto("https://search.bilibili.com/all?keyword=" + urllib.parse.quote(kw), wait=8)
            for _ in range(2):
                c.eval("window.scrollBy(0, 2500)"); time.sleep(2)
            cards = c.eval(JS_SEARCH) or []
            # 去重
            seen, uniq = set(), []
            for x in cards:
                if x["bvid"] not in seen:
                    seen.add(x["bvid"]); uniq.append(x)
            print(f"  视频 {len(uniq)} 个（去重后）")

            recs = []
            for i, card in enumerate(uniq[:15], 1):
                bv = card["bvid"]
                print(f"  [{i}] {card['title'][:34]}")
                info = fetch_json(c, f"https://api.bilibili.com/x/web-interface/view?bvid={bv}")
                d = info.get("data") or {}
                aid = d.get("aid")
                rec = {
                    **card,
                    "aid": aid,
                    "desc": (d.get("desc") or "")[:500],
                    "view": d.get("stat", {}).get("view"),
                    "like": d.get("stat", {}).get("like"),
                    "reply": d.get("stat", {}).get("reply"),
                    "url": f"https://www.bilibili.com/video/{bv}/",
                    "comments": [],
                }
                if aid:
                    rp = fetch_json(c, f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=2&ps=20&pn=1")
                    for r0 in ((rp.get("data") or {}).get("replies") or []):
                        rec["comments"].append({
                            "user": (r0.get("member") or {}).get("uname", ""),
                            "text": (r0.get("content") or {}).get("message", ""),
                            "like": r0.get("like", 0),
                            "ctime": r0.get("ctime"),
                        })
                print(f"      aid={aid} 评论={len(rec['comments'])}")
                recs.append(rec)
                time.sleep(1.2)

            all_data[kw] = recs
            safe = re.sub(r"[^\w\u4e00-\u9fa5]", "_", kw)
            p = os.path.join(OUTDIR, f"bili_api_{safe}.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(recs, f, ensure_ascii=False, indent=2)
            print(f"  已写出 {p}")
    finally:
        c.close()

    lines = ["# B站爬取结果（API 版）\n"]
    for kw, recs in all_data.items():
        lines.append(f"\n## 关键词：{kw}（{len(recs)} 个视频）\n")
        for i, r in enumerate(recs, 1):
            lines.append(f"### {i}. {r.get('title','')}")
            lines.append(f"- UP主：{r.get('author','')}　{r.get('date','')}　播放 {r.get('view')} / 评论 {r.get('reply')}")
            lines.append(f"- 链接：{r.get('url','')}")
            if r.get("desc"):
                lines.append(f"- 简介：{r['desc'][:200]}")
            cs = r.get("comments") or []
            if cs:
                lines.append(f"- 抓到评论 {len(cs)} 条：")
                for cm in cs[:20]:
                    lines.append(f"  - [{cm.get('like')}赞] {cm.get('user','')}：{cm.get('text','')[:180]}")
            lines.append("")
    p = os.path.join(OUTDIR, "B站_爬取汇总.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n汇总已写出 {p}")


if __name__ == "__main__":
    main()
