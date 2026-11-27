# -*- coding: utf-8 -*-
"""把爬到的原始 json 清洗成「候选证据清单」。

规则：
- 剔除寒暄、求资料、纯表情、短于 MIN_LEN 的评论
- 按四方向关键词归类
- 每条保留：来源、标题、作者、日期、链接、原文
- 输出 markdown + 一份 json，供后续人工判定 direct / indirect / inferred
"""
import glob
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.join(BASE, "网络证据")
OUT = os.path.join(BASE, "候选证据.md")
OUT_JSON = os.path.join(BASE, "候选证据.json")

MIN_LEN = 12

NOISE_PAT = re.compile(
    r"^(学习了|谢谢|感谢|支持|不错|优秀|收藏|马克|留名|666|牛逼|厉害|好看|已三连|一键三连|求资料|求这份|求分享|怎么没人|第一次|沙发|路过)"
)
URL_ONLY = re.compile(r"^https?://\S+$")

DIRS = {
    "A_决策依据与说服": ["说服", "依据", "凭什么", "拍脑袋", "汇报", "被怼", "老板", "客户不认", "解释", "证明"],
    "B_按功能找参考": ["参考", "灵感", "同质化", "抄", "借鉴", "找不到", "素材"],
    "C_按受众派生材料": ["效果图", "渲染", "看不懂", "草图", "PPT", "汇报材料", "保真"],
    "D_工艺可制造性": ["开模", "模具", "拔模", "缩水", "滑块", "斜顶", "DFM", "结构", "打回", "返工", "试模", "报价", "成本"],
}


def keep(text):
    t = (text or "").strip()
    if len(t) < MIN_LEN:
        return False
    if URL_ONLY.match(t):
        return False
    if NOISE_PAT.match(t) and len(t) < 40:
        return False
    return True


def classify(text):
    hits = []
    for d, kws in DIRS.items():
        if any(k in text for k in kws):
            hits.append(d)
    return hits or ["未分类"]


def load_bili():
    rows = []
    for f in glob.glob(os.path.join(EVID, "bili_api_*.json")):
        kw = os.path.basename(f).replace("bili_api_", "").replace(".json", "")
        for v in json.load(open(f, encoding="utf-8")):
            meta = {
                "platform": "B站",
                "keyword": kw,
                "title": v.get("title", ""),
                "author": v.get("author", ""),
                "date": v.get("date", ""),
                "url": v.get("url", ""),
                "view": v.get("view"),
            }
            # 视频标题/简介本身也可能是证据
            desc = (v.get("desc") or "").strip()
            if keep(desc) and len(desc) > 30:
                rows.append({**meta, "text": desc[:400], "kind": "简介"})
            for c in (v.get("comments") or []):
                if keep(c.get("text")):
                    rows.append({
                        **meta,
                        "text": c.get("text", "").strip(),
                        "author": c.get("user", "") or meta["author"],
                        "kind": "评论",
                        "like": c.get("like"),
                    })
    return rows


def load_xhs():
    rows = []
    for f in glob.glob(os.path.join(EVID, "xhs_*.json")):
        kw = os.path.basename(f).replace("xhs_", "").replace(".json", "")
        for v in json.load(open(f, encoding="utf-8")):
            meta = {
                "platform": "小红书",
                "keyword": kw,
                "title": v.get("title", ""),
                "author": v.get("author", ""),
                "date": v.get("date", ""),
                "url": v.get("url", ""),
                "like": v.get("like"),
            }
            desc = (v.get("desc") or "").strip()
            if keep(desc):
                rows.append({**meta, "text": desc[:400], "kind": "正文"})
            for c in (v.get("comments") or []):
                if keep(c.get("text")):
                    rows.append({
                        **meta,
                        "text": c.get("text", "").strip(),
                        "author": c.get("user", "") or meta["author"],
                        "kind": "评论",
                        "like": c.get("like"),
                    })
    return rows


def load_douyin():
    rows = []
    for f in glob.glob(os.path.join(EVID, "douyin_*.json")):
        kw = os.path.basename(f).replace("douyin_", "").replace(".json", "")
        for v in json.load(open(f, encoding="utf-8")):
            rows.append({
                "platform": "抖音",
                "keyword": kw,
                "title": v.get("title", ""),
                "author": v.get("author", ""),
                "date": v.get("date", ""),
                "url": v.get("search_url", ""),
                "view": v.get("play"),
                "text": v.get("title", ""),
                "kind": "标题",
            })
    return rows


def main():
    rows = load_bili() + load_xhs() + load_douyin()
    for r in rows:
        r["dirs"] = classify(r["title"] + " " + r["text"])

    # 去重
    seen, uniq = set(), []
    for r in rows:
        k = (r["platform"], r["text"][:60])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=2)

    lines = ["# 候选证据清单（自动清洗，未人工判定）\n",
             f"> 原始条目 {len(rows)} 条，去重后 {len(uniq)} 条。\n",
             "> ⚠️ 这里只是**候选**。是否算 direct 原话，必须逐条看上下文，不能直接引用。\n"]
    for d in list(DIRS.keys()) + ["未分类"]:
        sub = [r for r in uniq if d in r["dirs"]]
        lines.append(f"\n## {d}（{len(sub)} 条）\n")
        for r in sub[:60]:
            lines.append(f"- **[{r['platform']}]** {r['title'][:40]}")
            lines.append(f"  - {r['kind']}｜{r['author']}｜{r.get('date','')}｜{r['url']}")
            lines.append(f"  - 「{r['text'][:220]}」")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"原始 {len(rows)} → 去重 {len(uniq)}")
    for d in DIRS:
        print(f"  {d}: {len([r for r in uniq if d in r['dirs']])}")
    print("写出:", OUT)


if __name__ == "__main__":
    main()
