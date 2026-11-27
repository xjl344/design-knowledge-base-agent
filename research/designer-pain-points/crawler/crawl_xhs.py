# -*- coding: utf-8 -*-
"""小红书爬虫（CDP 直连已登录的 Edge）。

用法：
    python crawl_xhs.py "关键词1" "关键词2" ...

输出：02-深度调研/网络证据/xhs_<关键词>.json + .md
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

# 提取搜索结果卡片
JS_CARDS = r"""
(() => {
  const cards = [...document.querySelectorAll('section.note-item')];
  return cards.map(el => {
    const a = el.querySelector('a[href*="/search_result/"], a[href*="/explore/"]');
    const title = el.querySelector('.title, [class*=title]');
    const author = el.querySelector('.author, [class*=author] .name, [class*=name]');
    const like = el.querySelector('.like-wrapper, [class*=like] [class*=count], .count');
    const txt = el.innerText.split('\n').filter(x => x.trim());
    return {
      href: a ? a.href : '',
      title: title ? title.innerText.trim() : (txt[0] || ''),
      author: author ? author.innerText.trim() : '',
      like: like ? like.innerText.trim() : '',
      raw: txt.join(' | ').slice(0, 300),
    };
  }).filter(c => c.href);
})()
"""

# 提取笔记详情（含评论）
JS_DETAIL = r"""
(() => {
  const out = {};
  out.title = (document.querySelector('#detail-title, .title') || {}).innerText || document.title;
  out.desc = (document.querySelector('#detail-desc, .desc') || {}).innerText || '';
  out.author = (document.querySelector('.author-wrapper .name, .username') || {}).innerText || '';
  out.date = (document.querySelector('.date, .bottom-container .date') || {}).innerText || '';
  const cs = [...document.querySelectorAll('.comment-item, [class*=comment-item]')];
  out.comments = cs.map(el => {
    const u = el.querySelector('.name, [class*=author] .name, .nickname');
    const ct = el.querySelector('.content, [class*=content]');
    const lk = el.querySelector('.like, [class*=like]');
    return {
      user: u ? u.innerText.trim() : '',
      text: ct ? ct.innerText.trim() : '',
      like: lk ? lk.innerText.trim() : '',
    };
  }).filter(c => c.text);
  out.comment_count = cs.length;
  return out;
})()
"""


def search(c, kw, scrolls=3):
    url = ("https://www.xiaohongshu.com/search_result?keyword="
           + urllib.parse.quote(kw) + "&source=web_explore_feed")
    c.goto(url, wait=7)
    for _ in range(scrolls):
        c.eval("window.scrollBy(0, 2000)")
        time.sleep(2.5)
    cards = c.eval(JS_CARDS) or []
    print(f"  [{kw}] 抓到 {len(cards)} 条笔记")
    return cards


def detail(c, href, wait=6):
    c.goto(href, wait=wait)
    for _ in range(2):
        c.eval("window.scrollBy(0, 1200)")
        time.sleep(2)
    d = c.eval(JS_DETAIL) or {}
    return d


def main():
    kws = sys.argv[1:] or ["独居老人 智能家居"]
    c = Client()
    all_data = {}
    try:
        for kw in kws:
            print(f"=== 搜索：{kw} ===")
            cards = search(c, kw)
            all_data[kw] = []
            # 每条笔记抓详情+评论（限量，避免过慢）
            for i, card in enumerate(cards[:12], 1):
                href = card["href"]
                print(f"  [{i}/{min(12,len(cards))}] {card['title'][:34]}")
                try:
                    d = detail(c, href)
                except Exception as e:
                    print(f"     详情失败: {str(e)[:60]}")
                    d = {}
                if not d.get("title") or "无法浏览" in str(d.get("title")):
                    d.pop("title", None)  # 详情页被限制时保留搜索卡片的标题
                rec = {**card, **d, "keyword": kw, "url": href}
                all_data[kw].append(rec)
            # 落盘
            safe = re.sub(r"[^\w\u4e00-\u9fa5]", "_", kw)
            p = os.path.join(OUTDIR, f"xhs_{safe}.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(all_data[kw], f, ensure_ascii=False, indent=2)
            print(f"  已写出 {p}")
    finally:
        c.close()
    # 汇总 Markdown
    lines = ["# 小红书爬取结果\n"]
    for kw, recs in all_data.items():
        lines.append(f"\n## 关键词：{kw}（{len(recs)} 条）\n")
        for i, r in enumerate(recs, 1):
            lines.append(f"### {i}. {r.get('title','')}")
            lines.append(f"- 作者：{r.get('author','')}　日期：{r.get('date','')}　点赞：{r.get('like','')}")
            lines.append(f"- 链接：{r.get('url','')}")
            if r.get("desc"):
                lines.append(f"- 正文：{r['desc'][:300]}")
            cs = r.get("comments") or []
            if cs:
                lines.append(f"- 评论（{len(cs)} 条）：")
                for cm in cs[:15]:
                    lines.append(f"  - [{cm.get('like','')}赞] {cm.get('user','')}：{cm.get('text','')[:160]}")
            lines.append("")
    p = os.path.join(OUTDIR, "小红书_爬取汇总.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n汇总已写出 {p}")


if __name__ == "__main__":
    main()
