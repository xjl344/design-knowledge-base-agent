# -*- coding: utf-8 -*-
"""四方向合并表：把三轮 54 条带打分记录归入 A/B/C/D，按机会分排序。

方向定义（用户定的四个）：
  A 设计决策的依据与说服 —— 被问「凭什么」、方案被否、判断权不在自己手里
  B 按功能/问题找参考，而不是按外观 —— 灵感、收藏、同质化、机构原理
  C 按受众派生不同保真度的材料 —— 草图 vs 效果图、多套汇报材料
  D 工艺/可制造性/开模/交付 —— 用户明确否定过的方向，本轮做证伪与复核
  X 未归入四方向（工具效率、版本管理、隐性工作等），保留不删
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))

DIRECTION_MAP = {
    # ---- A 决策依据与说服 ----
    "P-S01-01": "A", "P-S01-02": "A", "P-S02-02": "A",
    "P-S03-01": "A", "P-S03-02": "A", "P-S06a-02": "A",
    "P-S07b-01": "A", "P-S08b-01": "A",
    "E-6": "A", "C-2": "A", "C-9": "A",
    # ---- B 按功能找参考 ----
    "P-S02-01": "B", "P-S04-01": "B", "P-S04-02": "B",
    # ---- C 按受众派生材料 ----
    "P-S02-04": "C", "P-S05-01": "C", "P-S07a-01": "C",
    # ---- D 工艺/可制造性 ----
    "P-S02-03": "D", "P-S05-05": "D", "P-S05-06": "D", "P-S06b-02": "D",
    "F-1-1": "D", "F-1-2": "D", "F-1-3": "D", "F-1-5": "D",
    "E-1": "D", "E-2": "D", "E-3": "D", "E-4": "D", "E-5": "D",
    "E-7": "D", "E-8": "D", "E-9": "D", "E-10": "D", "E-11": "D", "E-12": "D",
    "C-1": "D", "C-3": "D", "C-4": "D", "C-5": "D", "C-6": "D",
    "C-7": "D", "C-8": "D", "C-10": "D", "C-12": "D",
    # ---- X 未归入四方向 ----
    "P-S05-02": "X", "P-S05-03": "X", "P-S05-04": "X",
    "P-S06a-01": "X", "P-S06b-01": "X", "P-S08a-01": "X",
    "F-1-4": "X", "F-1-6": "X", "C-11": "X",
}

LABEL = {
    "A": "A · 设计决策的依据与说服",
    "B": "B · 按功能找参考，而不是按外观",
    "C": "C · 按受众派生不同保真度的材料",
    "D": "D · 工艺 / 可制造性 / 开模交付（用户否定过的方向）",
    "X": "X · 未归入四方向（保留备查）",
}


def main():
    with open(os.path.join(BASE, "merged_raw.json"), encoding="utf-8") as fh:
        recs = json.load(fh)

    missing = [r["id"] for r in recs if r["id"] not in DIRECTION_MAP]
    if missing:
        print("!! 未映射:", missing)

    for r in recs:
        r["dir"] = DIRECTION_MAP.get(r["id"], "X")
        # 机会分：未满足 × AI适配 × 改变意愿（1–125）
        r["opp"] = r["unmet"] * r["ai"] * r["will"]

    groups = {}
    for r in recs:
        groups.setdefault(r["dir"], []).append(r)

    out = []
    out.append("# 设计师痛点证据总表（三轮合并）\n")
    out.append("> 合并来源：R1 四个子 Agent（Core77 + 工具/JD）、R2 英文论坛 + 中文社区、R3 社媒（B站/抖音/小红书）")
    out.append("> 记录数：%d 条（全部带可点击链接，无链接者已剔除）" % len(recs))
    out.append("> 机会分 = 未满足程度 × AI适配度 × 改变意愿（1–125），只用于同向排序，不跨方向比大小")
    out.append("> ⚠️ 六维打分由各轮 Agent 各自给出，**不是同一把尺子**，跨轮比较只能看排序不能看差值\n")

    out.append("## 方向分布\n")
    out.append("| 方向 | 条数 | 机会分中位数 | 最高机会分 | 反例条数 |")
    out.append("|---|---|---|---|---|")
    for k in ["A", "B", "C", "D", "X"]:
        g = groups.get(k, [])
        if not g:
            continue
        opps = sorted(x["opp"] for x in g)
        med = opps[len(opps) // 2]
        cnt = sum(1 for x in g if x["counter"] and not x["counter"].startswith("否"))
        out.append("| %s | %d | %d | %d | %d |" % (LABEL[k], len(g), med, opps[-1], cnt))
    out.append("")

    for k in ["A", "B", "C", "D", "X"]:
        g = groups.get(k, [])
        if not g:
            continue
        out.append("\n## %s\n" % LABEL[k])
        out.append("| ID | 机会分 | 频率 | 严重 | 未满足 | AI适配 | 意愿 | 证据 | 平台 | 一句话 |")
        out.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted(g, key=lambda x: (-x["opp"], -x["severity"])):
            t = r["title"].replace("|", "／")
            flag = " ⚠️反例" if r["counter"] and not r["counter"].startswith("否") else ""
            out.append("| [%s](%s) | %d | %d | %d | %d | %d | %d | %s | %s | %s%s |" % (
                r["id"], r["link"], r["opp"], r["freq"], r["severity"],
                r["unmet"], r["ai"], r["will"], r["grade"], r["platform"], t, flag))
        out.append("")

    p = os.path.join(BASE, "evidence-master-table.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))

    # ---- 附：R3 社媒频次证据（无六维打分，单列，不混入主表）----
    cand = os.path.join(BASE, "crawler", "候选证据.json")
    if os.path.exists(cand):
        with open(cand, encoding="utf-8") as fh:
            c3 = json.load(fh)
        HOWTO = ["教程", "模板", "技巧", "教你", "如何", "怎么", "方法", "干货", "课程",
                 "培训", "PPT", "ppt", "领取", "链接", "https", "考研", "职称", "推荐",
                 "网站", "合集", "分享"]
        PAIN = ["被否", "被怼", "说不清", "讲不出", "没依据", "凭感觉", "拍脑袋", "老板",
                "质疑", "推翻", "改稿", "返工", "没底", "心虚", "说服", "翻车", "打回",
                "踩坑", "不懂", "对不上", "扯皮", "加班"]
        sec = ["\n---\n", "## 附录：R3 社媒频次证据（773 条，未打六维分）\n"]
        sec.append("> 这 773 条**没有六维打分**，与上面 54 条不是同一类证据，不能合并计算。")
        sec.append("> 它们的作用是「频次」与「措辞」，不是「机会分」。\n")
        sec.append("| 方向 | 条数 | 痛点自述向 | 教程/模板向 | 教程占比 |")
        sec.append("|---|---|---|---|---|")
        for k in ["A_决策依据与说服", "B_按功能找参考", "C_按受众派生材料", "D_工艺可制造性"]:
            sub = [x for x in c3 if k in x["dirs"]]
            if not sub:
                continue
            h = sum(1 for x in sub
                    if sum(1 for w in HOWTO if w in x["text"]) >
                    sum(1 for w in PAIN if w in x["text"]))
            pa = sum(1 for x in sub
                     if sum(1 for w in PAIN if w in x["text"]) >
                     sum(1 for w in HOWTO if w in x["text"]))
            sec.append("| %s | %d | %d | %d | %.0f%% |" % (k, len(sub), pa, h, h / len(sub) * 100))
        sec.append("")
        sec.append("⚠️ **教程占比不可跨方向比较**：D 方向的关键词本身是痛点词（翻车/打回/踩坑），"
                   "A/B/C 是中性任务词（评审/参考/汇报）。关键词选法直接决定搜出抱怨还是教程。")
        sec.append("")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n".join(sec))
        print("已附加 R3 频次证据:", len(c3), "条")

    print("写入", p, len(recs), "条")

    # 反例单独汇总
    print("\n=== 反例 ===")
    for r in recs:
        if r["counter"] and not r["counter"].startswith("否"):
            print(f"{r['id']:10s} [{r['dir']}] {r['counter'][:30]} | {r['title'][:40]}")


if __name__ == "__main__":
    main()
