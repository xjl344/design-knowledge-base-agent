# 第二轮：GitHub 工具与 Agent Skills 盘点（用户调研 / 论坛证据采集）

> 调研日期：2026-11-07　｜　方法：`mcp__github__search_repositories` / `search_code` / `get_file_contents` 实查，未安装任何东西
> 复核对象：上一轮"只看了 `travisvn/awesome-claude-skills` 一个仓库就断言没有现成的用户研究 skill"

---

## 结论（200 字以内）

最值得立刻用的两个：**`unclecode/crawl4ai`**（抓公开页 + 结构化抽取，拿全正文/链接/日期/作者，解难点 1/2/5）与 **`GiaSip/giasip-research`**（每事实带来源 URL、查不到写 not found、单列 To verify，解难点 3/4），接本地 `deep-research` 落库。

**开箱即用的用户研究 skill：没有。** 没有能一次做完"批量抓帖 → 存链接/日期/作者 → 分原话与推断 → 交叉验证去重找反例 → 结构化证据库"的 skill。但上轮结论草率：实际有大批 research 类 skill（T-1~T-8），只是偏通用/市场/竞品，不采论坛原话。官方 `anthropics/skills` 确实一个没有，这点上轮没错。

---

## 数据口径（先读）

- **Star 数、最近更新**：全部来自 GitHub API `search_repositories`（`minimal_output=false`），查询于 2026-11-07。"最近更新"统一取 **`pushed_at`**（代码实际推送时间）而非 `updated_at`（会被 star/issue 变动刷新，几乎都显示当天，不具区分度）。例：`travisvn/awesome-claude-skills` 的 `updated_at` 是今天，但 `pushed_at` 停在 2026-04-28——半年没动过代码。
- **功能描述**：基于仓库 `description`、`topics`、README 片段与 `search_code` 命中的实际文件，未凭记忆补写。
- **"能查到就写"**：附表里仅列 star 数与 `updated_at`（来自同一次搜索），已单独标注，未与 T- 条目混用。
- **本地证据**：本会话已安装 `deep-research` skill，其 `SKILL.md` 的 `homepage` 明确指向 `https://github.com/Weizhena/Deep-Research-skills`；本项目的 `outline.yaml` / `fields.yaml` / `results/` 结构与它的输出模板完全一致，可判断上一轮**实际是在用这个 skill**——委托人"有 skills 为什么不用"的质疑，准确答案不是"没用"，而是"用的 skill 不解决证据采集"。

---

## 一、复核上一轮结论：错在哪，对在哪

| 上轮断言 | 复核结果 |
|---|---|
| 只看 `travisvn/awesome-claude-skills` 一个仓库 | 属实，且该仓库 README 里 "research" 一词只出现在 **"Security research"**（安全研究，见 `search_code` 命中），与用户研究无关 |
| "没有现成的用户研究 skill" | **不准确**。把范围放大到 4 个主流合集后，research 类 skill 大量存在 |
| 官方 `anthropics/skills` 里有没有 | **确实没有**。实查其 `skills/` 目录共 19 个 skill：academy-guide / algorithmic-art / brand-guidelines / canvas-design / claude-api / discernment-nudge / doc-coauthoring / docx / frontend-design / internal-comms / mcp-builder / pdf / pptx / skill-creator / slack-gif-creator / theme-factory / web-artifacts-builder / webapp-testing / xlsx——全是文档、设计、artifact 类 |

实查到的 research 类 skill（推翻"没有"的关键证据）：

- `ComposioHQ/awesome-claude-skills`：`content-research-writer/SKILL.md`（内容研究写作，CONTRIBUTING.md 注明基于 Teresa Torres 的 content research process）、`lead-research-assistant/SKILL.md`、`competitive-ads-extractor/SKILL.md`、以及 README 链接的 `sanjay3290/ai-skills` 的 `deep-research`（"market analysis, competitive landscaping, and literature reviews"）。
- `VoltAgent/awesome-agent-skills`：`GiaSip/giasip-research`（"Source-linked research reports with explicit unresolved checks"）、`openai/notion-research-documentation`、`tronghieu/agent-skills`（"method-driven skills for knowledge work: strategy, research, writing"）。
- 独立 research skill 仓库：`GiaSip/giasip-skills`、`Weizhena/Deep-Research-skills`、`sanjay3290/ai-skills`。

**精确表述**：存在大量"通用/深度/市场/竞品研究 skill"，但**不存在**"论坛用户痛点证据采集专用 skill"（即难点 1+2+3+4+5 的完整闭环）。

---

## 二、逐仓盘点

### 类别 A：Claude / Codex Agent Skills 合集与 research skill

### T-1 anthropics/skills
- **链接**：https://github.com/anthropics/skills
- **Star 数**：188281
- **最近更新**：2026-11-06（pushed_at）
- **它解决上面哪个难点**：无。`skills/` 下 19 个 skill 全为文档/设计/artifact 类，无 research 类
- **能不能直接用**：官方仓库，Python 为主，`skills/` 目录即 SKILL.md 集合，clone 即用，无需 API key
- **局限 / 合规风险**：无抓取能力，与登录墙/robots 无关；作为"官方基线"用来排除"官方有现成货"的假设
- **对本项目的建议**：不用（仅作核对基线）。它证明了"官方没有"，但不提供证据采集能力

### T-2 VoltAgent/awesome-agent-skills
- **链接**：https://github.com/VoltAgent/awesome-agent-skills
- **Star 数**：36940
- **最近更新**：2026-11-05（pushed_at）
- **它解决上面哪个难点**：3、4（作为索引，能定位到证据化研究 skill，间接）
- **能不能直接用**：纯 README 索引（1000+ skills），MIT，clone 即读，无 API key。描述称兼容 Claude Code / Codex / Gemini CLI / Cursor
- **局限 / 合规风险**：只是目录，不含实现；收录质量参差，`search_code` 只命中 2 处 research 条目，需自己甄别
- **对本项目的建议**：用（当作"去哪找 skill"的入口），它是本次找到 `giasip-research` 的来源

### T-3 ComposioHQ/awesome-claude-skills
- **链接**：https://github.com/ComposioHQ/awesome-claude-skills
- **Star 数**：78964
- **最近更新**：2026-09-18（pushed_at）
- **它解决上面哪个难点**：4（竞品/市场侧的间接证据；`competitive-ads-extractor` 含 "Positioning Research"）
- **能不能直接用**：仓库内含可直接用的 `SKILL.md`（content-research-writer、lead-research-assistant、competitive-ads-extractor 等），Python；部分 `composio-skills/*` 需 `rube` MCP 与 Ahrefs/SEMrush 账号
- **局限 / 合规风险**：`competitive-ads-extractor` 的 SKILL.md 自己写明"Only use for research and inspiration / Don't copy ads directly"；SEO 类 skill 依赖第三方商业 API；不抓论坛原话
- **对本项目的建议**：备选。可借它的竞品分析套路补 S02a，但产出是文案/线索，不是设计师原话证据

### T-4 obra/superpowers
- **链接**：https://github.com/obra/superpowers
- **Star 数**：313754
- **最近更新**：2026-11-03（pushed_at）
- **它解决上面哪个难点**：无。定位是 "agentic skills framework & software development methodology"，topics 为 brainstorming / coding / sdlc / subagent-driven-development，偏编码工作流
- **能不能直接用**：Shell 为主，MIT，clone 即用，无 API key
- **局限 / 合规风险**：与调研/采集无关；star 数极高但与本题不相关
- **对本项目的建议**：不用。别被 star 数误导，它解决的是"怎么写代码"不是"怎么找证据"

### T-5 travisvn/awesome-claude-skills
- **链接**：https://github.com/travisvn/awesome-claude-skills
- **Star 数**：15729
- **最近更新**：2026-04-28（pushed_at；`updated_at` 显示 2026-11-07，但代码已半年未动）
- **它解决上面哪个难点**：无
- **能不能直接用**：纯 awesome 列表，无 API key
- **局限 / 合规风险**：`search_code` 实查 "research" 仅命中 "Security research"（安全研究）与恶意 skill 风险提示，与用户研究无关；且维护停滞
- **对本项目的建议**：不用。**上一轮只看它就下结论，是问题所在**——单一低活跃列表不足以代表整个生态

### T-6 GiaSip/giasip-skills（核心 skill：`giasip-research`）
- **链接**：https://github.com/GiaSip/giasip-skills
- **Star 数**：13
- **最近更新**：2026-10-08（pushed_at）
- **它解决上面哪个难点**：3、4（部分覆盖 2：要求来源 URL 与来源类型标注）
- **能不能直接用**：MIT，Shell/纯 Markdown skill，clone 后放到 skills 目录即用，**无 API key、无第三方依赖**；面向 Claude Code / Codex。实读 `skills/giasip-research/SKILL.md` 的硬规则：
  - "Every fact carries a source URL. Prefer official / owner primary sources; label source type (primary / third-party / media)."
  - "If it cannot be found, write 'not found' — never fill from memory. Dates are absolute."
  - "GitHub stars and timestamps come from `gh api` snapshots, not search-result pages."
  - 拆 2–3 个 facet 并行子 agent，**子 agent 只收集不下结论**，末尾单列 **"To verify"**
- **局限 / 合规风险**：star 仅 13、社区验证薄弱；产出是单个 `report.md` 散文报告（<200 行），**不产出结构化字段记录**；不做论坛帖/评论批量抓取，不保留作者/发布日期，不区分 direct/indirect/inferred，不做去重与反例检索
- **对本项目的建议**：用。它是"证据纪律"最接近的一个——直接把它的三条硬规则写进本项目 `fields.yaml` 的 `quality_rules`，正好补上难点 3；但别指望它替你抓数据

### T-7 Weizhena/Deep-Research-skills（= 本地已装的 `deep-research`）
- **链接**：https://github.com/Weizhena/Deep-Research-skills
- **Star 数**：2510
- **最近更新**：2026-08-23（pushed_at）
- **它解决上面哪个难点**：5（结构化证据库骨架），部分 4（并行搜索）
- **能不能直接用**：**本会话已安装**（`C:/Users/34464/.workbuddy-ai/skills/deep-research/`），MIT，v1.0.0，`homepage` 指向本仓库。`/research` 生成 `outline.yaml + fields.yaml`，`/research-deep` 并行 web-search-agent 逐项填充 `results/`，`/research-report` 汇总 `report.md`；另有 `/research-add-items`、`/research-add-fields`、`research-en/` 英文版。本项目的 `outline.yaml`、`fields.yaml`、`results/` 正是这套结构
- **局限 / 合规风险**：主命令 `research/SKILL.md` 实读显示——Step 1 靠"模型内部知识生成初步框架"，Step 2 依赖 `WebSearch` 补 items/fields，web-search-agent 只返回"信息来源 URL 列表"；**不抓帖子原文、不存作者/日期、不内置原话与推断分级、不做去重与反例**。`fields.yaml` 里的 `original_quote` / `evidence_level` 是本项目自己加的，不是 skill 内置。上一轮 `agent4_tools_jd.md` 记录的 Figma 人机验证、McNeel Discourse IP 403、BOSS 直聘强制登录抓不到，正是这个 skill 的能力边界
- **对本项目的建议**：用（且已在用）。它负责"结构化产出"，但必须外挂抓取层（T-9/T-17）和证据纪律层（T-6）才完整。**回应委托人：skill 用了，缺的是它不提供的采集与溯源能力**

### T-8 sanjay3290/ai-skills
- **链接**：https://github.com/sanjay3290/ai-skills
- **Star 数**：452
- **最近更新**：2026-11-02（pushed_at）
- **它解决上面哪个难点**：4（市场分析、竞品格局、文献综述）
- **能不能直接用**：Apache-2.0，Python，24 个跨平台 skill；其 `deep-research` 依赖 Gemini Deep Research Agent，需对应 API 凭证
- **局限 / 合规风险**：靠外部 Deep Research 服务，输出是综述而非原始证据；不保留原话、链接、日期、作者；被 `ComposioHQ` 列表收录但本身社区规模小
- **对本项目的建议**：备选。可用于快速扫竞品全景，不能作为证据源

---

### 类别 B：论坛与网页抓取工具

### T-9 unclecode/crawl4ai
- **链接**：https://github.com/unclecode/crawl4ai
- **Star 数**：87030
- **最近更新**：2026-11-04（pushed_at）
- **它解决上面哪个难点**：1、2、5
- **能不能直接用**：**首推**。Apache-2.0，Python 包 + Docker + MCP server，`topics` 含 playwright / markdown / llm / data-extraction / rag。可**完全自托管、无需 API key**；官方另提供 Cloud API（免费 key，每月 1000 credits，可不绑卡）。基于 Playwright 能渲染 JS 页面，输出 Markdown/JSON，支持 CSS/LLM 结构化抽取
- **局限 / 合规风险**：自托管可控性最好，但仍需自己遵守目标站 `robots.txt` 与速率限制；渲染能力**不等于**可以绕过登录/验证码；登录墙站点应停止并标注"未查到"
- **对本项目的建议**：**用**。一次覆盖难点 1/2/5，且自托管最符合合规要求；用它批量抓 Core77 / Autodesk / Reddit 等公开版块，把正文+URL+日期+作者直接落成 `fields.yaml` 记录

### T-10 firecrawl/firecrawl
- **链接**：https://github.com/firecrawl/firecrawl
- **Star 数**：199342
- **最近更新**：2026-11-07（pushed_at）
- **它解决上面哪个难点**：1、2、5
- **能不能直接用**：AGPL-3.0，TypeScript，官方 Python/Node SDK；`/scrape`、`/crawl`、`/extract`（按 schema 出结构化数据）、`/search`。云 API 需 key，也可自托管（AGPL 需注意传染性）
- **局限 / 合规风险**：AGPL-3.0 对二次分发有约束；云版本数据经第三方；`description` 与 topics 强调 "web data extraction"，但同样不提供绕过登录态的能力
- **对本项目的建议**：用（备选首选）。若不想维护 Playwright 环境，`/extract` 直接按 `fields.yaml` 出结构化记录，最省事；注意 AGPL 许可

### T-11 jina-ai/reader
- **链接**：https://github.com/jina-ai/reader
- **Star 数**：12242
- **最近更新**：2026-05-22（pushed_at，约半年未动代码）
- **它解决上面哪个难点**：1、2（快速把单页转 LLM 友好文本）
- **能不能直接用**：零安装——给 URL 加前缀 `https://r.jina.ai/` 即可；Apache-2.0，TypeScript，可自部署；免费额度可选 API key
- **局限 / 合规风险**：只做"URL→文本"，**不保留作者/日期/结构化字段**，不做批量爬取编排；经第三方代理，敏感内容需评估
- **对本项目的建议**：备选。适合单个 URL 的快速验证与试抓，不适合做证据库主管道

### T-12 ScrapeGraphAI/Scrapegraph-ai
- **链接**：https://github.com/ScrapeGraphAI/Scrapegraph-ai
- **Star 数**：32589
- **最近更新**：2026-10-06（pushed_at）
- **它解决上面哪个难点**：2、5（LLM 驱动、按 prompt/schema 出 JSON）
- **能不能直接用**：MIT，Python 包（`pip install scrapegraphai`），**需 LLM API key**（OpenAI/本地 Ollama 等）
- **局限 / 合规风险**：LLM 抽取有幻觉风险——**可能把推断混进"原话"字段**，必须把 `original_quote` 设为"逐字复制、不得改写"并人工抽检；依赖 LLM 成本
- **对本项目的建议**：备选。结构化抽取能力强，但用在难点 3 上要格外小心，别让 LLM 替你"总结"设计师原话

### T-13 browser-use/browser-use
- **链接**：https://github.com/browser-use/browser-use
- **Star 数**：120232
- **最近更新**：2026-11-07（pushed_at）
- **它解决上面哪个难点**：1、2（AI 浏览器 agent，能处理需交互/渲染的页面）
- **能不能直接用**：MIT，Python 包，需 LLM API key；另有付费 Cloud（stealth browser / hosted agent API）
- **局限 / 合规风险**：**合规敏感**——"stealth browser" 类能力容易被用于规避反爬；本项目应明确只用于公开页的正常渲染，不用于绕过登录墙/验证码/速率限制
- **对本项目的建议**：备选。只在常规抓取确实失败且页面公开可得时使用，且需人工确认合规边界

### T-14 browserbase/stagehand
- **链接**：https://github.com/browserbase/stagehand
- **Star 数**：25976
- **最近更新**：2026-11-07（pushed_at）
- **它解决上面哪个难点**：1、2（自然语言驱动的浏览器自动化 + `extract`）
- **能不能直接用**：MIT，TypeScript/Node SDK（另有 Python），需 Browserbase 云与 LLM key
- **局限 / 合规风险**：强绑定云服务商，成本与数据外发都高于自托管；与 T-9/T-15 能力重叠
- **对本项目的建议**：不用。已有 T-9/T-15 覆盖，无需再引入需付费云 + LLM key 的依赖

### T-15 microsoft/playwright-mcp
- **链接**：https://github.com/microsoft/playwright-mcp
- **Star 数**：39285
- **最近更新**：2026-11-06（pushed_at）
- **它解决上面哪个难点**：1、2（真实浏览器、可人工介入）
- **能不能直接用**：Apache-2.0，npm 包 `@playwright/mcp`，本地运行，**无需 API key**，可直接挂到本会话的 MCP 客户端
- **局限 / 合规风险**：是"浏览器操作"不是"批量爬虫"，需自己编排；可访问登录态意味着**更需自律**——不得用已登录账号批量导出受限内容
- **对本项目的建议**：备选。适合"人工看着、让 agent 一页一页摘录"的合规路径，用于登录墙站点的人工辅助摘录

### T-16 praw-dev/praw
- **链接**：https://github.com/praw-dev/praw
- **Star 数**：4292
- **最近更新**：2026-11-05（pushed_at）
- **它解决上面哪个难点**：1、2（**仅 Reddit**）
- **能不能直接用**：BSD-2-Clause，Python 包（`pip install praw`），需 Reddit OAuth 凭证（免费注册应用即可），走**官方 API**——这是本项目 Reddit 侧唯一推荐的合规路径
- **局限 / 合规风险**：只覆盖 Reddit；受 Reddit API 速率与条款约束；2023 年后 Reddit API 政策收紧，需遵守其 Terms；返回结构含作者、created_utc、permalink，天然满足难点 2
- **对本项目的建议**：**用**（Reddit 专用）。`outline.yaml` 里 `r/IndustrialDesign` 的证据直接用它采，字段能一一映射到 `evidence_source`

---

### 类别 C：证据管理 / 结构化抽取

### T-17 adbar/trafilatura
- **链接**：https://github.com/adbar/trafilatura
- **Star 数**：7084
- **最近更新**：2026-11-06（pushed_at）
- **它解决上面哪个难点**：2、5
- **能不能直接用**：**首推配套**。Apache-2.0，Python 包 + CLI，`pip install trafilatura` 即用，**完全无需 API key**。`description` 原文："gather text and metadata on the Web: Crawling, scraping, extraction, output as CSV, JSON, HTML, MD, TXT, XML"，专门抽正文 + **元数据（author / date / URL / sitename）**并直接输出 CSV/JSON/XML
- **局限 / 合规风险**：不做 JS 渲染（需配 T-9/T-15 先拿 HTML）；元数据抽取依赖页面结构化标记，论坛页可能抽不到作者；纯抽取工具，不做去重/交叉验证
- **对本项目的建议**：**用**。它是最贴合 `fields.yaml` 的"最后一公里"——`evidence_source`（原始链接 + 发布日期 + 作者/来源）三个子字段几乎可自动填充；与 T-9 串成 "抓 HTML → 抽正文+元数据 → 写记录"

> 说明：T-9/T-10/T-12 也具备结构化抽取能力，但 T-17 是唯一以"元数据 + 多格式结构化输出"为唯一目标的轻量工具，且零 key、零云依赖，最适合做证据库的落库层。

---

### 类别 D：深度研究 / 多智能体调研框架

### T-18 assafelovic/gpt-researcher
- **链接**：https://github.com/assafelovic/gpt-researcher
- **Star 数**：30530
- **最近更新**：2026-10-10（pushed_at）
- **它解决上面哪个难点**：4（多源检索聚合），部分 5（生成报告）
- **能不能直接用**：Apache-2.0，Python 包 + Docker + MCP server；需 LLM API key 与搜索后端（Tavily 等）API key
- **局限 / 合规风险**：**产出是综述报告，不是逐条证据记录**；不保留原话与推断的边界，`fields.yaml` 的 `evidence_level` 分级无法自动落地；多源聚合容易把"多条报道"误当成"多个独立信源"
- **对本项目的建议**：备选。可用来发现"还有哪些论坛/话题没覆盖"，但最终证据必须回到逐条记录，不能整段抄报告

### T-19 stanford-oval/storm
- **链接**：https://github.com/stanford-oval/storm
- **Star 数**：31808
- **最近更新**：2025-09-30（pushed_at，**已一年多未更新代码**）
- **它解决上面哪个难点**：4（"researches a topic and generates a full-length report **with citations**"，多视角提问 + 检索）
- **能不能直接用**：MIT，Python，需 LLM key 与检索源（如 You.com / Bing / 自建检索器）
- **局限 / 合规风险**：citation 是"报告级引用"，不是"每条痛点带原话+作者+日期"；代码停滞，依赖可能过期；不针对论坛
- **对本项目的建议**：备选。它的"多视角提问 + 带引用"思路值得借来做反例检索，但不宜直接接入生产

---

### 类别 E：中文场景采集器（**仅存在性与合规风险，不提供任何绕过登录/反爬的做法**）

> 本类别统一结论：**不建议用于本项目自动化采集**。以下仅登记"存在"这一事实与风险，不介绍部署、cookie 获取、签名或绕过方式。

### T-20 Panniantong/Agent-Reach
- **链接**：https://github.com/Panniantong/Agent-Reach
- **Star 数**：109781
- **最近更新**：2026-11-06（pushed_at）
- **它解决上面哪个难点**：1、2（一条 CLI 读 Twitter / Reddit / YouTube / GitHub / Bilibili / 小红书多平台）
- **能不能直接用**：MIT，Python CLI，`description` 宣称 "zero API fees"、无需 API key
- **局限 / 合规风险**：**风险最高**。"零 API 费用、免官方 key"通常意味着走非官方接口或模拟请求，可能违反各平台服务条款；多平台聚合使得 `robots.txt`、速率限制、版权与个人信息合规难以逐站落实；star 数极高但创建于 2026-02，缺乏长期合规验证
- **对本项目的建议**：**不用**。合规风险远大于收益；中文平台请走人工摘录或官方授权渠道

### T-21 xpzouying/xiaohongshu-mcp
- **链接**：https://github.com/xpzouying/xiaohongshu-mcp
- **Star 数**：16974
- **最近更新**：2026-11-06（pushed_at）
- **它解决上面哪个难点**：1、2（小红书，MCP 形式便于接入 agent）
- **能不能直接用**：Apache-2.0，Go 二进制 + MCP server；实际使用通常需要已登录态的会话凭证
- **局限 / 合规风险**：小红书对未授权自动化采集有明确限制；使用登录态批量读取可能违反用户协议，并涉及个人信息处理；本轮明确不提供任何登录态/凭证相关做法
- **对本项目的建议**：**不用**。若确需小红书素材，改为人工浏览后按 `fields.yaml` 手工逐条摘录，并标注 `evidence_level: indirect` 或"人工摘录"

### T-22 JoeanAmier/XHS-Downloader
- **链接**：https://github.com/JoeanAmier/XHS-Downloader
- **Star 数**：13231
- **最近更新**：2026-11-06（pushed_at）
- **它解决上面哪个难点**：2（小红书作品/链接采集，输出 JSON/SQLite）
- **能不能直接用**：GPL-3.0，Python + FastAPI + Docker + 油猴脚本；同样通常需要登录态凭证
- **局限 / 合规风险**：GPL-3.0 许可约束；"作品采集/下载"涉及原帖内容版权，全文留存与二次分发风险高；与本项目"只做必要摘录 + 留链接"的证据原则冲突
- **对本项目的建议**：**不用**。下载器定位与证据库（最小必要摘录）不符

**同类别其他真实存在的仓库**（仅登记存在，star 与 `updated_at` 来自同一次 GitHub 搜索，2026-11-07）：

| 仓库 | Star | updated_at | 平台 | 合规提示 |
|---|---|---|---|---|
| `wycm/zhihu-crawler` | 918 | 2026-11-04 | 知乎（Java，2016 年起） | 老项目，知乎有明确反爬与用户协议限制 |
| `ghh1251/wechat_articles_spider` | 349 | 2026-10-29 | 微信公众号 | 公众号内容受版权保护，批量抓取风险高 |
| `csarron/ITBlogs` | 383 | 2026-11-04 | 技术博客 + 公众号聚合 | 聚合转载版权风险 |
| `liyangbit/PyDataLab` | 380 | 2026-09-15 | 公众号内容仓库 | 同上 |
| `white0dew/XiaohongshuSkills` | 3579 | 2026-11-07 | 小红书（含自动发布/评论/检索） | 含"自动评论/发布"，属平台明确禁止的自动化行为 |
| `ZJU-REAL/Easel` | 4479 | 2026-11-07 | 小红书/抖音/知乎/B站（发布向） | 创作发布向，非采集 |
| `whiteguo233/OpenBiliClaw` | 3704 | 2026-11-07 | 多平台内容发现 | 本地优先，但仍涉多平台条款 |

---

## 三、难点 × 工具对照

| 难点 | 首选 | 配套 / 备选 | 当前缺口 |
|---|---|---|---|
| 1 登录墙 / 反爬 / 403 | T-9 Crawl4AI（自托管渲染）、T-15 Playwright MCP | T-10 Firecrawl、T-11 Jina Reader、T-16 PRAW（Reddit 官方 API） | **登录墙本身不应被"解决"**；403 是停止信号，正确动作是标注"未查到" |
| 2 批量抓帖+评论，保留链接/日期/作者 | T-9 + T-17 Trafilatura | T-10 `/extract`、T-12、T-16 PRAW | 论坛评论区多为 JS 动态加载，需 T-9/T-15 先渲染；作者字段常缺失需兜底 |
| 3 区分原话与 AI 推断 | T-6 `giasip-research` 硬规则 + 本项目 `fields.yaml` 的 `evidence_level` | 人工抽检 | **无任何工具自动完成**；必须用字段约束 + 人工复核。T-12 的 LLM 抽取反而是高风险点 |
| 4 多源交叉验证、去重、找反例 | 人工流程 + T-6 的 "To verify" | T-18 / T-19 的多视角检索 | 去重与反例检索无现成开箱工具，须自建规则 |
| 5 结构化证据库 | T-7 本地 `deep-research`（outline/fields/results） | T-17 CSV/JSON、T-10 `/extract` | 本项目已有 `fields.yaml` 骨架，**缺的是自动填充**，不是缺 schema |

---

## 四、合规红线（必须直说）

1. **抓论坛公开页面 ≠ 绕过登录墙，这是两回事。** 前者是访问"无需登录、未设访问限制"的公开内容；后者是规避身份验证、验证码、IP 封禁、签名校验或速率限制。后者**不要做**，本报告也不提供任何相关做法。
2. **上一轮已经给出了明确的停止信号**，`agent4_tools_jd.md` 第 18 行记录：Figma Community 插件页人机验证拦截、McNeel Discourse 论坛 IP 403、BOSS 直聘详情页强制登录、51job 搜索结果骨架屏未加载、职友集搜索页验证码。当时"标注未查到、不做估算"的处理是**正确的**，应继续保持，而不是换工具去硬闯。
3. **优先合规路径**，按此顺序：官方 API（Reddit → PRAW OAuth）→ 站点自带导出 / RSS / 公开归档 → 站点搜索结果页 → 人工浏览 + 手工逐条摘录 → 明确标注"未查到"。中文平台（知乎、小红书、微信公众号）以及 BOSS 直聘等基本应走人工摘录。
4. **版权**：论坛帖与评论原文受著作权保护。证据库只做"必要摘录 + 原始链接 + 发布日期 + 作者"，不全文转载、不二次分发原文库；T-22 这类"下载器"与最小必要原则冲突。
5. **个人信息**：作者 ID、昵称、头像等属个人信息，最小化留存、仅用于溯源，不对外公开分发。
6. **robots.txt 与速率限制**：即使页面公开，也需逐站检查 `robots.txt` 并限速；自托管（T-9/T-17）比第三方云更容易落实这一点。

---

## 五、可落地的选用建议

**推荐工具链（不安装任何东西，仅给出组合与顺序）：**

```
公开页面
  │
  ├─[T-9 Crawl4AI / T-10 Firecrawl]  渲染 + 批量抓取，输出 HTML/Markdown
  │        └─ 遇 403/验证码/登录墙 → 停止，记「未查到」，不绕过
  │
  ├─[T-17 Trafilatura]  抽正文 + 元数据(author/date/url) → CSV/JSON
  │
  ├─ 按本项目 fields.yaml 落库：
  │     original_quote（逐字，不改写） / evidence_source（链接+日期+作者）
  │     evidence_level（direct / indirect / inferred）
  │
  ├─[T-6 giasip-research 纪律]  每事实带来源 URL、标注来源类型、
  │     查不到写 "not found"、单列 "To verify"
  │
  ├─[T-7 本地 deep-research]  /research-deep 并行补项 + /research-report 汇总
  │
  └─ 人工：多源交叉验证、去重、主动找反例（无现成工具可替）
```

**Reddit 单独走 T-16 PRAW（官方 API）**，其返回天然带 `author` / `created_utc` / `permalink`，可直接映射 `evidence_source`。

**分档结论：**

| 档 | 仓库 | 理由 |
|---|---|---|
| **用** | T-9 Crawl4AI、T-17 Trafilatura、T-16 PRAW、T-6 giasip-research、T-7 本地 deep-research、T-2 VoltAgent（索引） | 分别补齐抓取、结构化落库、合规 Reddit 通道、证据纪律、已有产出框架 |
| **备选** | T-10 Firecrawl、T-11 Jina Reader、T-12 ScrapeGraphAI、T-15 Playwright MCP、T-3 ComposioHQ、T-8 sanjay3290、T-18 gpt-researcher、T-19 STORM | 有特定场景价值，但或需付费/API key，或与首选能力重叠，或产出不是逐条证据 |
| **不用** | T-1 anthropics/skills（基线核对）、T-4 superpowers、T-5 travisvn、T-14 Stagehand、T-13 browser-use（合规敏感）、T-20/T-21/T-22 及全部中文采集器 | 与本题无关、能力重叠、或合规风险超出收益 |

**关于"开箱即用的用户研究 skill"的最终回答：**

- **没有**能一次完成五个难点的 skill。
- **最接近的是 `GiaSip/giasip-research`（证据纪律）+ 本地 `deep-research`（结构化产出）这一对组合**。
- **差在四处**：① 都不抓论坛帖子/评论原文，靠 `WebSearch` 或人工喂 URL；② 都不保留"作者 + 发布日期 + 原始链接"三元组作为强制字段；③ 都没有 `direct / indirect / inferred` 的原话—推断分级，也不阻止 LLM 把总结塞进"原话"；④ 都不做去重、反例检索与多源交叉验证的自动化。
- 这四处恰好对应本项目 `fields.yaml` 已经手写好的 `quality_rules`——**项目真正的缺口不是"缺一个 skill"，而是"缺一个把抓取结果按既有 schema 落库、并强制执行证据等级纪律的采集管道"**，这个管道由 T-9 + T-17 + T-6 三条规则拼起来，目前没有现成单品。

---

## 附：同类其他真实存在的仓库（供扩展，star 与 `updated_at` 来自 2026-11-07 GitHub 搜索）

| 仓库 | Star | updated_at | 类别 | 备注 |
|---|---|---|---|---|
| `Alibaba-NLP/DeepResearch` | 20100 | 2026-11-07 | D 深度研究 | 通义开源 Deep Research Agent |
| `dzhng/deep-research` | 19877 | 2026-11-06 | D | 最简迭代式 deep research 实现（TS） |
| `langchain-ai/open_deep_research` | 12677 | 2026-11-07 | D | **已 archived**，LangGraph 研究模板 |
| `LearningCircuit/local-deep-research` | 9514 | 2026-11-07 | D | 全本地、支持 10+ 检索源 |
| `langchain-ai/local-deep-researcher` | 9406 | 2026-11-06 | D | 全本地网页研究报告 |
| `nickscamara/open-deep-research` | 6295 | 2026-11-04 | B+D | 明确"web data extracted with **Firecrawl**" |
| `jina-ai/node-DeepResearch` | 5243 | 2026-11-06 | D | 与 T-11 同厂 |
| `u14app/deep-research` | 4687 | 2026-11-06 | D | 支持 MCP server |
| `SkyworkAI/DeepResearchAgent` | 3569 | 2026-11-03 | D | 分层多智能体 |
| `mshumer/OpenDeepResearcher` | 2792 | 2026-11-03 | D | Notebook 形态 |
| `guy-hartstein/company-research-agent` | 2315 | 2026-11-07 | D | **LangGraph + Tavily 多智能体公司尽调** |
| `op7418/guizang-social-card-skill` | 8006 | 2026-11-07 | A | 小红书图文/公众号封面生成 skill（非采集） |

**给委托人的一句话**：GitHub 上"调研类 skill"很多，但都是"写报告"的；"论坛证据采集"这件事没有现成 skill，需要的是抓取工具 + 你已有的 `fields.yaml` schema + 三条证据纪律拼出来的管道——而不是再装一个新的 skill。
