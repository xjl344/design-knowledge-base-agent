# S06–S08 产品/工业设计师真实痛点调研（Agent 3）

调研时间：2026-11-07
调研对象：产品设计师 / 工业设计师 / 驻厂设计师 / 设计咨询（不含 UI-UX、平面、室内）
证据口径：只有当事人公开发言才记 direct；本轮 8 条记录全部为 direct，无 JD 间接证据（原因见文末）。

---

## 总体判断（约 180 字）

四条是设计师特有的：多格式 3D 数据与制造图纸的版本错会变成物理报废；方案可被"拆件拼贴"，产生明知不成立的组合；渲染保真度被误读为完成度，逼设计师用更高成本换一句反馈；作品集要过程证据，但过程材料不沉淀。三条是所有乙方共有的：客户朝令夕改、多人意见不一、为不同受众反复做汇报材料——广告、装修、软件外包同样如此。真正窄的机会只有两个：设计表征的"按受众换保真度"，和决策理由的结构化沉淀。

---

## 记录

### P-S06a-01 文件靠"final / final2"命名，半年后没人知道哪版是真的

- **设计师类型**：设计咨询公司产品设计师（CAD + 渲染 + 提案多格式并行）
- **工作阶段**：S06a 反馈收集、版本管理、修改迭代
- **具体场景**：一个消费电子项目做到第 4 个月，同一目录下并排躺着 `concept_v3.ai`、`concept_final.ai`、`concept_final2.ai`、`final_final`。客户临时要改一处倒角，设计师凭记忆打开"final"改完发出，事后发现那是两周前被否掉的版本。或者同事接手，在 `F:\clients\client_x\design history\engineering\internals\motor\components\` 这样的路径里翻了十几分钟，仍不确定哪个装配体是最新的。
- **原话**：
  - cloughna（2009-05-04）：
    > "I am tired of having to navigate to F:\clients\client_x\design history\engineering\internals\motor\components\etc. to get the files I want. Worse, with multiple people working on the same project, it is an inevitability that things will be put in the 'wrong' folder."
    > "It just seems that about halfway through a project things start to get messy. Once you reach a certain complexity in the project, it doesn't become instantly clear what folder a certain file should go in. People aren't going to sit there and wax philosophic about where to put the file, they will just do the first thing that makes sense. After this has gone on for a bit, it can become a chore to find a file. And windows search is useless."
    （译：项目做到一半就开始乱。复杂到一定程度后，一个文件该放哪个文件夹已经不再显然。没人会坐下来哲学式地思考文件该放哪，都是怎么顺手怎么放。这样持续一阵，找文件就变成苦差。而 Windows 搜索基本没用。）
  - seurban（2011-02-02）：
    > "We've actually pretty much banned the use of 'final' in any folder or file name. It is certainly very useful to create a folder of the final files for a project, but if you have to go back and edit something the 'final' is no longer final."
    （译：我们基本禁止在任何文件夹或文件名里用"final"。单独建一个交付文件夹是有用的，但一旦要回去改，"final"就不再是 final 了。）
  - 同帖 engio 的回复（论坛搜索摘要，原文被截断）：
    > "Just name it 'Final 2', and then 'New final', and ultimately 'Final final' … Then someone 4 months later goes to make a revision or do something for the client and grabs the 'Final' folder, not noticing there was…"
    （译：先叫"Final 2"，再叫"New final"，最后叫"Final final"……四个月后有人要改东西，抓了那个"Final"文件夹，没注意到还有……）
  - bennybtl（2009-05-04）：
    > "Even meta data has issues because all users have to remember to enter meta data and be consistent with meta-data. An example being: 'Client X', 'ClientX', 'cleint-x', 'CLX'"
    （译：元数据也有问题，因为所有人都得记得填，还得填得一致。比如"Client X"、"ClientX"、"cleint-x"、"CLX"。）
- **问题原因**：设计的中间产物是"同一物体的多个表征"（草图 / 曲面 / AI 排版 / KeyShot 场景 / 工程图），它们之间没有稳定主键。文件系统只能用"路径 + 文件名"表达关系，而路径是项目启动时定的，项目演化后语义失效。版本状态实际被编码在人的记忆里，而不是数据里——而"最新""已交付""客户已确认"是三种不同状态，文件名只能承载一种。
- **目前怎么解决**：每周、每次会后、每次方向大改就整目录复制一份加日期（seurban）；或干脆全平铺、用 `_01/_02` 递增后缀（Brett_nyc）；或用 PDMWorks 打元数据标签后靠搜索（NURB）；或口头交接。
- **时间与资源成本**：seurban 说"usually a new folder is created every week, after every meeting, or with any significant direction change"——3 个月项目约 12–20 次整目录复制，硬盘冗余以 GB 计。单次找文件 5–20 分钟，按每天 3 次计约 0.5–1 小时/天（据帖中描述推算，非原话数字）。
- **造成的影响**：误用旧版导致返工；交接成本高；文件膨胀；更隐蔽的是"不敢删"，所有历史版本永久堆积，检索越来越慢。
- **现有工具为什么没解决**：Windows 资源管理器对 CAD/渲染文件内容无索引，cloughna 直接说"windows search is useless"；SolidWorks PDMWorks 能管 CAD 元数据，但要每个用户手工填且只管 CAD，管不了 AI/PS/KeyShot/提案 PDF；Adobe Version Cue 已停止维护；坚果云/Dropbox 只做同步，不理解"这一版改了什么"。
- **证据来源**：
  - https://boards.core77.com/t/best-way-to-organize-project-folders/25711 （2011-02-01 至 2011-02-02，cash68 / seurban / Brett_nyc / rkuchinsky / engio）
  - https://boards.core77.com/t/organizing-projects/22131 （2009-05-04，cloughna / NURB / bennybtl）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：在几十个同源文件里确认"当前有效版本"，并把它交给下一个人（同事、客户、工厂）。
  2. **为什么困难**：版本语义在项目中途会变（最新 ≠ 已交付 ≠ 客户已确认），文件名承载不了三重状态；多人协作时命名规范必然退化到"怎么顺手怎么放"。
  3. **目前怎么解决**：整目录按日期复制、后缀递增、PDM 元数据、口头交接。
  4. **造成什么后果**：误用旧版返工、交接耗时、文件无限膨胀、不敢删。
  5. **现有工具为什么没解决**：文件系统没有"设计状态"这个概念；PDM 只覆盖 CAD 且依赖人工录入一致性；搜索不索引 3D/图像内容。
  6. **有没有潜在机会**：以"项目状态"而非"文件夹"为主键；自动从修改记录和文件内容推断"这版改了什么"，生成人可读的版本说明。但这是通用文件管理问题，AI 的增量只在"读懂 3D/图像差异"，不是再做一版网盘。
- **六维打分**：
  - 发生频率 5：每个多格式项目都会发生，cloughna 说"about halfway through a project things start to get messy"，是必然而非偶发。
  - 严重程度 3：多数可恢复，但会稳定消耗时间并偶发误交付。
  - 解决成本 3：命名规范几乎零成本，难的是执行一致性；PDM 有部署成本。
  - 未满足程度 4：现有工具覆盖 CAD 或覆盖同步，没有覆盖"设计状态"。
  - AI 适配度 3：能读懂文件差异并生成摘要，但需要本地/网盘文件权限，且 3D 差异理解仍不稳。
  - 改变意愿 3：设计师普遍认同这是问题（原帖就是在求方案），但不愿为它改变工作习惯。

---

### P-S06a-02 客户要求"把三个方案拼成一个"，设计师被迫做明知不成立的方案

- **设计师类型**：设计咨询 / 驻厂产品设计师
- **工作阶段**：S06a（提案反馈环节）
- **具体场景**：提案会上，客户指着三张图说"我喜欢 A 的曲面、B 的出风口、C 的按键，合成一个看看"。设计师知道三者的比例语言和分型逻辑互相冲突，做出来必然是个怪胎，但不做就是"不配合"。做完之后客户更困惑，会议没有结论，下周再来一轮。
- **原话**：
  - NATE（2005-12-20）：
    > "We've all been there: you present some concepts and the client wants to cut and paste elements of a few designs together into one because they like different parts of each the the 'best!' They just 'want to see what it would look like.' You know that this won't result in a cohesive design solution. Rather, it WILL result in a Franken-concept that won't be a viable solution, even though the client may be convinced it's a winner."
    （译：我们都遇到过：你提案，客户想把几个设计里的元素剪贴拼成一个，因为他喜欢各自"最好的部分"。他们只是"想看看会是什么样"。你知道这不可能形成连贯的方案。它一定会变成一个弗兰肯方案，不可行，但客户可能坚信它是个赢家。）
  - cg1（2005-12-20）：
    > "Frankenstein was sewn together with parts from the same species. The trick is to design several different species that can't biologically mix."
    （译：弗兰肯斯坦是用同一物种的部件缝起来的。诀窍是设计几个在生物学上无法混种的、不同物种的方案。）
  - skinny（2005-12-20）：
    > "The non-designer client doesn't always know exactly what it is about a particular concept that they like, so they pick pieces that 'seem' to represent what they want…"
    （译：非设计师客户并不总清楚自己到底喜欢某个方案的哪一点，所以他们挑那些"看起来"代表他们想要的东西的碎片……）
- **问题原因**：客户无法把"整体形态语言"当作一个整体来评价，只能对局部特征做偏好判断；而形态是耦合系统（比例、圆角、分型线、拔模方向互相约束），局部最优不构成整体可行。设计师又缺少低成本方式让客户"看见"组合后的失败，只能用做出来这一种方式证明。
- **目前怎么解决**：提前把方案做成"不同物种"，让拼贴在物理上不可能（ufo / cg1）；先照做一版 franken，再和新方案并排展示（skinny）；用需求清单把讨论拉回 brief（ufo）；靠个人权威直接拒绝（skyarrow）；或直接照做以换取推进（多位回复者的现实选择）。
- **时间与资源成本**：每轮 franken 方案需要重新建模 + 渲染，约 0.5–2 天；通常 1–3 轮（据建模工作量推算，帖中无明确数字，标为推断）。
- **造成的影响**：项目节奏被打断；设计师做无效劳动且情绪消耗大；最终往往仍回到原方案，客户对设计师判断力的信任反而下降。
- **现有工具为什么没解决**：Rhino/KeyShot 只能"做出来"，不能快速判断"组合后为什么不行"；PPT 并排图无法表达体量与比例冲突；没有任何工具能在拼贴时提示"这两个特征的圆角半径 / 拔模角 / 分型线互相冲突"。
- **证据来源**：https://boards.core77.com/t/clients-like-to-cut-paste/14173 （2005-12-20 至 2006-04-08，NATE / skyarrow / ufo / cg1 / skinny / symbiont / velodesigner）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：在客户提出"拼贴"要求后，要么做出一个能推进会议的替代物，要么说服客户放弃拼贴。
  2. **为什么困难**：客户说的是"局部偏好"，设计师要维护的是"整体系统"；两者之间没有共同语言，且拼贴结果的好坏要等做出来才知道。
  3. **目前怎么解决**：设计"不可混种"的方案、先做一版再对比、用 brief 拉回、靠权威拒绝。
  4. **造成什么后果**：1–3 轮无效方案；会议无法收敛；设计师与客户互相认为对方不专业。
  5. **现有工具为什么没解决**：造型工具不提供"兼容性判断"；二维拼贴无法暴露三维约束冲突。
  6. **有没有潜在机会**：形态兼容性预检（圆角/拔模/分型/比例冲突）+ 秒级合成可视化。但必须克制：AI 能提示几何与工艺冲突，不能判断"丑"。
- **六维打分**：
  - 发生频率 4：咨询公司提案场景高频，但不是每个项目都发生。
  - 严重程度 4：直接导致 1–3 轮返工，且是情绪冲突的引爆点。
  - 解决成本 4：本质是人际博弈，工具只能辅助，不能替代专业权威。
  - 未满足程度 4：现有造型工具完全没有"组合可行性"这一层。
  - AI 适配度 2：核心是说服与判断，AI 只能做可视化辅助，不能承担结论。
  - 改变意愿 3：设计师想要工具，但不相信工具能替自己挡客户。
- **通用性**：设计师特有。广告/文案也会被"拼贴"，但那是内容层面；形态拼贴会直接产生一个物理上不可制造的东西，后果不同。

---

### P-S06b-01 图纸发出去了，但没人负责把旧版从车间收回来

- **设计师类型**：设计咨询公司（承接医疗器械/消费品结构交付）
- **工作阶段**：S06b 与工程师、供应商的信息传递
- **具体场景**：咨询公司改了壳体卡扣，按流程升版图纸、邮件发给客户；客户转给模具厂。三个月后开模，模具厂用的还是 Rev.A——因为客户的采购把新图放在共享盘，而车间打印的是旧图。设计师直到试模才发现，此时改模费用已经产生。
- **原话**：
  - andrew_w（2012-02-06）：
    > "We do not 'control' data flow; for example we rely on the client or manufacturer to take control of removing outdated drawings from circulation and to decide whether stock / field / WIP items are affected and when to implement the changeover."
    > "How can we ensure that old documents owned by the client are removed and filed / discarded to prevent unintended use?"
    > "We need to clearly and correctly divide the responsibilities between ourselves and our clients, and make sure that nothing 'falls down the gap'."
    （译：我们不"控制"数据流；比如我们依赖客户或制造商去把过期图纸从流通中移除，并决定库存/现场/在制品是否受影响、何时切换。我们怎么确保客户手上的旧文件被收回作废，防止被误用？……我们需要清楚正确地划分我们和客户的责任，确保没有东西"掉进缝里"。）
  - idguy88（2011-04-21）：
    > "even when you get it all 100% there, China will have a question on something because they don't get it and the client will want to come back to you to clarify or revise or add an assembly or other detailed view."
    （译：就算你做到 100%，中国那边还是会因为没看懂而提问，客户又会回来找你澄清、修改，或者补一张装配图/细节视图。）
- **问题原因**：设计方与制造方之间没有共同的"版本权威源"。图纸是文件，不是状态机——它没有"当前有效"这个属性，只有"某处存在某个文件"。而"谁负责回收旧版"在合同里通常不写，形成责任真空：设计方以为客户管，客户以为工厂管，工厂只看手上那张纸。
- **目前怎么解决**：图纸上加 change record box 简单描述改了什么；邮件发新版；口头提醒客户"记得给工厂"；在报价里预留 post-project support 时间（bcpid 估计占 5%–20%）；靠长期合作关系。
- **时间与资源成本**：bcpid（2011-04-29）："Figure 80%-95% of your time will be spent before you submit to your client, and the remaining 5%-20% will be spent supporting your deliverables"——交付后答疑占 5%–20%。idguy88 举例：客户工厂要更详细的图纸和视图，"maybe another 16hrs worth of work $800"。
- **造成的影响**：开模报废（模具费通常数万至数十万，帖中未给具体金额，此处为行业常识推断）；交期延误；责任纠纷；设计师被迫免费补图。
- **现有工具为什么没解决**：Teamcenter/Windchill 能做完整变更闭环，但报价与实施成本对 10 人以下设计公司不现实；SolidWorks PDM 只覆盖自家 CAD，管不到客户和工厂那端；微信/邮件无法证明"谁在什么时间看到了哪一版"；共享盘没有签收概念。
- **证据来源**：
  - https://boards.core77.com/t/drawing-and-design-control-for-product-design-consultancies/27423 （2012-02-06，andrew_w，全帖仅 1 楼、0 回复、3989 次浏览——说明这是个"没人有答案"的问题）
  - https://boards.core77.com/t/post-project-support/26193 （2011-04-19 至 2011-06-10，idguy88 / iab / bcpid / chevisw）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：把一次设计变更准确、可追溯地传递到客户和工厂，并确保旧版停止使用。
  2. **为什么困难**：设计方对客户内部和工厂现场没有控制权；变更的"生效"发生在别人的组织里，且没有回执机制。
  3. **目前怎么解决**：图纸变更栏、邮件、口头提醒、预留售后工时、长期关系。
  4. **造成什么后果**：旧版被制造 → 报废/返工/延期；责任无法界定；设计师免费补图。
  5. **现有工具为什么没解决**：PLM 太重且只覆盖企业内部；PDM 只管 CAD；IM 没有版本与签收语义。
  6. **有没有潜在机会**：轻量的"交付物版本 + 签收"层，只覆盖"设计方→客户→工厂"这一段；AI 可自动生成变更影响清单（哪些零件/工艺/文件受影响），这正是 andrew_w 缺的 ECO 表。
- **六维打分**：
  - 发生频率 4：凡涉及外部制造的变更都会遇到，但并非每日。
  - 严重程度 5：旧版被物理执行，损失不可逆（模具、批量报废）。
  - 解决成本 4：要么上 PLM，要么靠人盯，中间态方案少。
  - 未满足程度 4：中小企业几乎没有可用工具。
  - AI 适配度 2：核心是流程与责任，不是生成能力；AI 只能做影响分析。
  - 改变意愿 3：设计师想要，但推动客户和工厂改流程超出他的权限。
- **通用性**：设计师特有。软件行业的旧版本不会自己上线；图纸的旧版本会被车间真的执行。

---

### P-S06b-02 设计意图传到工厂就变形，样品回来要靠锯开才知道改了什么

- **设计师类型**：设计咨询 / 驻厂产品设计师（消费电子、户外产品）
- **工作阶段**：S06b
- **具体场景**：把 CAD 发给工厂打样，明确要求"上下壳可拆，便于清洁"。工厂出于好意做了跌落测试，发现组装状态会散开，于是把卡扣改成一次性卡死。样品回来装不上也拆不开，设计师只能把样品锯成两半，才看清内部结构被改了。另一单：发出去的是 CAD，回来的是明显手工打磨的样件，上面还有锉刀痕。
- **原话**：
  - product_tank（2013-05-21）：
    > "I sent (and I will not be alone in this experience I'm sure) manufacturing data to china … and what came back (due to a Chinese desire to over deliver) had been completely changed. The product was designed to come apart for ease of cleaning, but the manufacturer in it's eagerness to please, did a drop test on the assembled product and found that it fell apart (of course) so changed the tool so that the clips were fit and forget. When we got the first off tool samples back, we had to saw it in half to get it apart and work out what had gone wrong."
    > "On another project I sent CAD data out to the FE and what came back as a sample was clearly hand made, it still had the hand file marks in it."
    （译：我把制造数据发到中国……回来的东西被彻底改了（因为对方想"超额交付"）。产品本来设计成可拆便于清洁，制造商为了讨好我们，对组装状态做了跌落测试，发现会散（当然会），于是改了模具，把卡扣做成一次性卡死。拿到第一套试模件时，我们只能把它锯成两半才能拆开，搞清楚到底哪里出了问题。另一个项目我把 CAD 发给工厂，回来的样件明显是手工做的，上面还有锉刀痕。）
  - Anonymous1（2005-03-16）：
    > "marketing and purchasing always cut additional costs out of the design, then the product sucks and is axed! Of course nobody in the organization asks me for a design revision to meet their revised profit goals…not once! In addition, nobody provides cost feedback (tooling, part cost, etc.) so I have no idea how fix the issue."
    （译：市场和采购总是从设计里砍成本，然后产品变烂被砍掉！当然，组织里没人来找我做一次设计修改去匹配他们改过的利润目标……一次都没有！而且没人给我成本反馈（模具费、零件成本等），我根本不知道该怎么修。）
  - 知乎答主（29 岁，Steelcase 慕尼黑设计中心产品设计师）：
    > "早上10点左右模型师一般会过来和我沟通遇到的问题，有什么设计意图表达不明确的或者没有标明的需要我重新给他们更明确的细节说明。"
- **问题原因**：CAD 只承载几何，不承载"为什么这样"。工厂拿到的是几何 + 零散口头说明，一旦遇到约束冲突（可拆 vs 抗跌落），就会按自己的目标函数（良率、成本、通过测试）做局部优化。设计师不在场，改动不可见，且工厂认为自己在"帮忙"。
- **目前怎么解决**：出差驻厂；在图纸上补 detail view；把关键意图写成邮件；靠长期合作"懂你"的工厂；打样后锯开/剖切检查；报价时预留返工。
- **时间与资源成本**：product_tank 的例子是"first off tool samples"——已经开模才发现，损失是一轮改模费 + 一轮试模周期（通常 3–6 周）。帖中未给金额，不臆造。Anonymous1 的情况是成本反馈完全缺失，连"改哪里能省多少"都不知道。
- **造成的影响**：外观与体验被悄悄降级；返工；设计师对制造端失去控制；最坏情况是量产后才发现，损失放大到批量。
- **现有工具为什么没解决**：STEP/IGES 不携带设计意图；MBD（基于模型的定义）在中小企业未普及；微信群里发图无法追溯"哪条要求被确认过"；PLM 管版本不管语义；DFM 检查工具（如 Protolabs 的自动报价）只查可制造性，不查"是否违背设计意图"。
- **证据来源**：
  - https://boards.core77.com/t/how-effective-is-product-design-process/28850 （2013-05-12 至 2013-05-26，product_tank / singletrack / Mr-914）
  - https://boards.core77.com/t/cant-seem-to-get-good-designs-into-production/11131 （2005-03-16 至 2005-03-28，Anonymous1 等）
  - https://www.zhihu.com/question/67260224 （知乎「二三十岁的工业设计师（产品设计）的生活是什么样的？」，Steelcase 设计师回答；页面未显示回答日期，抓取于 2026-11-07）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：把"这个特征不能改，因为它服务于清洁/握持/装配顺序"这类意图，准确传给不在同一现场的工厂。
  2. **为什么困难**：意图是隐性的、和约束绑定的，几何文件里没有它的位置；工厂遇到冲突时必须自己决策，而决策依据不会回传。
  3. **目前怎么解决**：驻厂、补图、邮件、长期关系、事后剖切检查。
  4. **造成什么后果**：设计被静默降级；开模后返工；成本反馈缺失导致下一轮仍然错。
  5. **现有工具为什么没解决**：文件格式不承载意图；IM 不承载确认；PLM 不承载语义；DFM 工具只查"能不能做"，不查"是不是我要的"。
  6. **有没有潜在机会**：把设计意图变成结构化、可被工厂查询的约束（这个特征不可改，理由是清洁），并在收到 DFM 反馈时自动比对"哪些意图被违反"。这是知识库/RAG 能接的活，且与委托人现有项目同构。
- **六维打分**：
  - 发生频率 4：跨厂打样几乎每单都有不同程度的意图损耗。
  - 严重程度 4：轻则返工，重则开模报废。
  - 解决成本 4：需要改变工厂侧的沟通方式，单靠设计师一方很难。
  - 未满足程度 4：没有任何轻量工具承载"设计意图"。
  - AI 适配度 3：可以做意图结构化与冲突检测，但依赖工厂愿意读。
  - 改变意愿 4：设计师痛感强，且愿意试新方法（原帖就是在求助）。
- **通用性**：设计师特有。软件外包的需求变更至少有文档可查；制造的改动发生在物理世界，且改动者认为自己在帮忙。

---

### P-S07a-01 同一个方案要做三套不同保真度的材料，因为不同受众"读"法不一样

- **设计师类型**：设计咨询 / 企业内设计部门产品设计师
- **工作阶段**：S07a 方案说服与汇报材料制作
- **具体场景**：周一给设计总监看手绘草图，讨论结构；周三给市场部看，必须换成带场景的渲染，否则对方认为"还没开始"并当场提改动；周五给客户老板看，要做 30 页 PPT 加一段动画，否则"看不出质感"。同一个造型，三套材料，三次重新排版。更麻烦的是：渲染做得越好，客户越容易说"就这样定了"，而实际上内部还需要两周深化。
- **原话**：
  - Greenman（2010-03-29）：
    > "You can present a group of sketches to a group of designers and have meaningful dialogue because they understand this communication medium. Do the same thing (and i have) with marketing/sales people and generally the impression is that you aren't finished working on your design to a point to be taken seriously, or, they view it as early stages and start to interject all kinds of changes."
    （译：你可以拿一组草图给设计师看，能有实质对话，因为他们懂这种表达方式。同样的事拿给市场/销售人员看（我做过），通常的印象是你还没做完、不值得认真对待；或者他们把它当成早期阶段，开始插进各种修改意见。）
  - leezard（2013-05-06）：
    > "We have one client who can't visualize anything without an ultra-photorealistic render of the thing, and we have another client who's totally cool with hand motions, sound effects, and scribbles on paper. They have nearly identical products, but we have to use opposite approaches with them to get to the same end result. I prefer the one that can use scribbles on paper since it saves a ton of time: I can print out what we have so far, draw all over it, scan it in, then email it to him and get a quick response vs painstakingly CAD modeling and getting the lighting and materials just so and spending forever waiting for an HD render before I can get a response."
    （译：我们有个客户，不看超写实渲染就什么都想象不出来；另一个客户，手势加音效加纸上涂鸦就够了。两个产品几乎一样，但为了到同一个结果，我们得用完全相反的方式。我更喜欢后者，因为省太多时间：把现有东西打出来、在上面画、扫描、发邮件，很快就有回复；而前者要 painstakingly 建 CAD、把灯光材质调到刚好，再等一张 HD 渲染等很久，才能拿到一句反馈。）
  - _YO（2013-03-09）：
    > "Just because something looks finished doesn't mean it is. Over the years I've had many other people in the process see something that looked polished and say 'awesome, we are done' only have to tell them it was actually sh!t and needed a few more weeks to percolate in design refinement… and this goes for any type of design communication tool. A polished 2d rendering too early can have the same misleading effect."
    （译：看起来完成，不等于完成了。这些年很多流程里的人看到精致的东西就说"太好了，我们做完了"，我只好告诉他们这其实是垃圾，还需要几周深化……任何设计表达工具都一样，过早给一张精致的 2D 渲染，同样会误导。）
  - Atohms（2010-03-31）：
    > "day6: Creating the presentations/persuasive drawings / day7: Creating the presentations/persuasive drawings / So my ideal workflow would be 70% idea-generation/exploring/testing/ and 30% getting the ideas presentable/sellable. Off course this is (mostly) not always the case due to time-constraints."
    （译：第 6 天：做提案/说服性图；第 7 天：做提案/说服性图。我的理想流程是 70% 想点子/探索/测试，30% 把点子做得可展示、可卖。当然因为时间限制，多数时候做不到。）
- **问题原因**：设计表征（sketch / render / model）同时承担"探索"和"说服"两种功能，而这两种功能要求相反的保真度。保真度本质上是"受众阅读能力"的函数，不是"设计阶段"的函数；一个会议室里同时坐着市场、工程、采购、老板，任何一套材料必然对一部分人过精、对另一部分人过粗。于是"做材料"变成按人重做的重复劳动。
- **目前怎么解决**：按会议对象重做材料；故意把渲染做成"草图感"（Cyberdemon：白模 + 手绘线 + 手写标注）；提前私下沟通让关键人先买账（product_tank）；准备"备用页"应对现场追问。
- **时间与资源成本**：Atohms 的 7 天理想流程里 2 天（约 29%）用于做提案材料，且他说现实中经常做不到。leezard 描述的"painstakingly CAD modeling and getting the lighting and materials just so"再加"spending forever waiting for an HD render"——单张 HD 渲染 2–8 小时（据渲染工作量推算，帖中无数字）。
- **造成的影响**：创意时间被压缩；材料成为"说服税"；过度精细的渲染还会让客户提前锁定方案，压缩探索窗口（Generatewhatsnext：高保真"allowed clients to jump to conclusions premature in the development process, reducing our available creative window"）。
- **现有工具为什么没解决**：KeyShot/V-Ray 只能提高保真度，不能"降低到刚好够用"；PPT/Keynote 是排版工具，不解决"这页该用什么保真度"；Figma 面向 UI；Miro 只能贴图；没有任何工具能从一个 3D 源自动派生"给市场部的版本 / 给工程师的版本 / 给老板的版本"。
- **证据来源**：
  - https://boards.core77.com/t/concept-fidelity-impacts-on-clients/28611 （2013-03-08 至 2013-05-06，Mr-914 / Generatewhatsnext / Cyberdemon / _YO / leezard）
  - https://boards.core77.com/t/better-ideas-vs-better-presentations/23940 （2010-03-27 至 2010-04-23，Dubya / Greenman / Atohms / carton / skyarrow）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：把一个已经定下来的造型，翻译成不同受众能正确解读的材料。
  2. **为什么困难**：受众的解读能力不同，而保真度会改变"完成度"的感知，不只是"清晰度"；做高了会被当成已定稿，做低了会被当成没开始。
  3. **目前怎么解决**：按人重做、故意降保真、提前私下沟通、准备备用页。
  4. **造成什么后果**：2/7 的时间花在材料上；创意窗口被压缩；客户提前锁定未成熟的方案。
  5. **现有工具为什么没解决**：渲染工具只有"更高保真"一个方向；排版工具不理解内容；没有"从同一 3D 源派生多保真度版本"的工具。
  6. **有没有潜在机会**：从同一 3D 源按受众自动生成不同表征（白模+标注 / 场景渲染 / 分解图 / 一页对比）。这是目前图像生成能力最匹配的窄口，但必须解决几何一致性——不能生成一个不存在的产品。
- **六维打分**：
  - 发生频率 5：每个项目、每个汇报节点都会发生。
  - 严重程度 3：不致命，但稳定消耗 20%–30% 的项目时间。
  - 解决成本 3：技术上可行，难在"保真度分级"的标准要按客户定制。
  - 未满足程度 4：现有工具只解决"做出来"，不解决"做到什么程度"。
  - AI 适配度 5：多保真度图像派生是生成式模型最擅长的事，且 3D 源提供了几何约束。
  - 改变意愿 4：设计师普遍愿意，因为省的是纯体力时间。
- **通用性**：部分通用。任何提案都要面向不同受众；但"保真度被误读为完成度"是设计表征特有的——PPT 做得再精致，也不会让客户以为产品已经量产。

---

### P-S07b-01 验证只能靠焦点小组，但结论不可信，最后退回"凭经验"

- **设计师类型**：企业内设计部门 / 咨询公司产品设计师
- **工作阶段**：S07b 用户测试与可行性验证
- **具体场景**：新产品形态定了 3 个方向，公司安排一场焦点小组：8 个消费者，2 小时，一间会议室。用户说"这个看起来廉价"，团队慌了，回去改配色；设计师后来把同一双鞋换成黑白版本再拿出来，同一批人却说好看。会议记录写满"用户喜欢 X"，但没有一条能指导"哪个特征导致了什么"。最后方案还是按总监的直觉定。
- **原话**：
  - rkuchinsky（2012-01-20）：
    > "Frankly I put no stock in focus group testing I trust my intuition better than any comments from random people."
    （译：说实话我不信焦点小组测试，我信自己的直觉胜过随机路人的任何评论。）
  - _YO（2012-01-20）：
    > "I remember we were testing a new shoe technology with high school athletes. When the shoe came out of the bag they hated it, I mean they literally BOOO'd. We talked to them for 10 minutes trying to figure out why. Finally I pulled a black white version of the SAME shoe out and they loved it! When I told them they were the same shoe, they would not believe me… Lesson, these things don't tell you that much about product, but they do tell you about people."
    （译：我记得拿一项新鞋技术去测高中生运动员。鞋从袋子里拿出来他们就讨厌，真的直接嘘。我们聊了 10 分钟想知道为什么。最后我拿出同一双鞋的黑白版本，他们爱了！我告诉他们这是同一双，他们不信……教训是：这些东西说明不了多少产品问题，只能说明人的问题。）
  - PackageID（2012-01-21）：
    > "Focus groups can turn you subjects into art directors an this is where it gets really dagerous. The fact is like R mentioned consumers do not know what they don't know… They have the ability to cause major confusion and if you have the wrong people observing than they will twist the feedback many different ways."
    （译：焦点小组会把被试变成艺术总监，这才是真正危险的地方。事实是，就像 R 说的，消费者不知道自己不知道什么……它们能制造巨大的混乱，如果观察的人不对，还会把反馈拧成各种方向。）
  - _YO（2012-01-16）：
    > "Don't listen to what people say, watch what they do… and even then, a reaction in a contrived observed group setting is not realistic."
    （译：别听人说什么，看人做什么……即便如此，在一个刻意安排的被观察的群体场景里的反应，也是不真实的。）
  - GEBS（2012-01-15）：
    > "I'm observing my first focus group tomorrow, and I'm both excited and nervous because several of my projects are being reviewed."
- **问题原因**：物理产品的可用性与审美判断依赖"上手"和"长期使用"，而焦点小组是去情境化的口头反应。被测者无法把"我不喜欢这个颜色"和"这个形态不对"分开表达；主持人也无法把"喜欢/不喜欢"还原成"哪个特征导致了什么行为"。设计师需要的是"特征→反应"的因果，现有方法只能采集"整体态度"。
- **目前怎么解决**：让用户实际挑选/带走，看行为不看说法（rkuchinsky 的床单案例：说的和拿的不一样）；观察而非询问；把焦点小组当"访谈刺激物"而不是测试（bcpid）；靠有经验的观察者翻译（PackageID）；干脆不信，用直觉。
- **时间与资源成本**：一场焦点小组需要招募 8–12 人、场地、主持人、记录、转录，周期 1–2 周；中小设计公司一个项目通常只能做 0–1 次（帖中无金额，据流程推算）。成本高 + 结论弱 = 直接不用。
- **造成的影响**：关键形态/CMF 决策缺少证据，退回甲方权力或设计师直觉；错误结论还会被写进 brief 放大；设计师对"研究"整体不信任。
- **现有工具为什么没解决**：问卷星/腾讯问卷只能问，不能观察；UserTesting/Maze 面向屏幕界面，无法测握持、重量、开合手感；可用性实验室按项目报价，小公司用不起；眼动仪只能给注意力分布，不给因果；Dovetail 只能整理已经采到的访谈，不解决"采不到"。
- **证据来源**：https://boards.core77.com/t/my-first-focus-group/27328 （2012-01-15 至 2012-05-14，GEBS / PackageID / _YO / rkuchinsky / bcpid / robertcj / Dugan27）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：在开模前判断"这个形态/CMF/结构"是否会被目标用户接受，并拿到能指导修改的证据。
  2. **为什么困难**：物理产品的判断依赖上手与长期使用；口头反应无法归因到具体特征；测试环境本身会改变反应。
  3. **目前怎么解决**：看行为不看说法、让用户实际选择、把焦点小组当刺激物、靠经验翻译、干脆靠直觉。
  4. **造成什么后果**：决策无依据；错误结论进入 brief；设计师对研究方法失去信任。
  5. **现有工具为什么没解决**：数字问卷与屏幕端测试工具无法覆盖物理交互；实验室成本高；分析工具只能整理已采集的数据。
  6. **有没有潜在机会**：用"场景化刺激 + 结构化观察"替代口头提问——把 3 个形态放进真实使用场景的图/短视频里做强制选择，并记录选择理由；AI 的活是把访谈/观察记录转成"特征→反应"表。要非常小心合成用户的效度，不能生成假用户来替代真人。
- **六维打分**：
  - 发生频率 3：不是每个项目都做，但每个重要项目都要面对"要不要验证"。
  - 严重程度 4：验证缺失直接导致开模后的市场风险。
  - 解决成本 5：真人测试的招募与组织成本高，且难以复用。
  - 未满足程度 5：物理产品的远程/低成本验证几乎没有可用方案。
  - AI 适配度 3：能做记录结构化与场景化刺激生成，但不能替代真人反应，效度风险高。
  - 改变意愿 3：设计师想要证据，但不相信又一个"用户研究工具"。
- **通用性**：设计师特有。软件/AI 产品的可用性测试可以在屏幕上做，物理产品的握持、重量、开合手感无法远程测。

---

### P-S08a-01 项目做完没有过程材料，作品集只能"事后补过程"

- **设计师类型**：在职产品设计师（换工作时）、工业设计应届生
- **工作阶段**：S08a 作品集整理与项目复盘
- **具体场景**：离职前两周开始整理作品集。项目做了 8 个月，手上只有最终渲染图和几张汇报截图；草图在旧电脑，用户访谈录音在微信，结构讨论在邮件，被否掉的方案在另一个盘。为了讲清"为什么这么设计"，只能凭记忆重画一遍过程——画出来又像是对最终方案的描图，面试官一眼看出"这不是当时的草图"。
- **原话**：
  - jarman65（2014-09-19）：
    > "I'm still trying to figure out how to showcase some of these thinking and thumbnail sketches in my portfolio… I'm having trouble trying to figure out how to explain my sketch process in my portfolio."
    （译：我还在琢磨怎么把那些思考草图和缩略草图放进作品集……我很难想清楚怎么在作品集里解释我的草图过程。）
  - Sain（2014-09-19）：
    > "What Im trying to say is most of your sketches seem to be of the final product. Almost like overlays over CAD screenshots."
    （译：我想说的是，你的大多数草图看起来都是最终产品的样子，几乎像是在 CAD 截图上描的。）
  - jw_41（2019-02-28）：
    > "My roles have been in retail and gifting/retail respectively, but I rarely work on the whole project which has limited the commercial projects I can put in my portfolio."
    （译：我的岗位分别在零售和礼品/零售领域，但我很少参与整个项目，这限制了我能放进作品集的商业项目。）
  - ralphzoontjens（2019-03-01）：
    > "What I am missing are some solid underpinnings for your design decisions."
    （译：我缺的是你设计决策的扎实依据。）
- **问题原因**：项目过程中产生的"证据"（草图、被否方案、测试记录、决策邮件）只服务于当下沟通，不做归档设计；它们的存放位置由工具决定（微信/邮箱/本地盘），不由项目决定。作品集需要的是"决策链"，而项目结束时留下的只有"结果"。更根本的是：过程中的思考大多没有留下可读的痕迹——草图是给自己看的，不是给未来的自己看的。
- **目前怎么解决**：拍照留存草图墙；把旧项目重做一遍；用"问题-调研-草图-方案"模板倒推；向原公司申请授权展示；只放做完整流程的项目。
- **时间与资源成本**：作品集重做通常 2–6 周（全职投入）；jw_41 是"folio re-design"，且需要补做被否项目的细节。jarman65 花了"the last couple of weeks"更新一个项目。
- **造成的影响**：有能力的项目讲不出来；作品集趋同（都是同一套流程模板）；面试时无法回答"为什么"；jw_41 的情况更严重——因为只做项目的一小段，作品集里根本没有完整项目可放。
- **现有工具为什么没解决**：Behance/Issuu 是展示层，不解决素材；Eagle/花瓣能存图但脱离项目上下文；Notion 需要人主动维护；公司 PDM 归档的是交付物，不是过程；微信/邮件里的决策讨论无法被检索；Figma 的版本历史只管设计文件，管不到聊天和邮件。
- **证据来源**：
  - https://boards.core77.com/t/portfolio-critique-new-id-grad/29886 （2014-06-28 至 2014-09-19，jarman65 / klohr / Sain / bepster）
  - https://boards.core77.com/t/portfolio-can-i-get-a-real-id-job-with-this/32043 （2019-02-28 至 2019-03-19，jw_41 / _YO / ralphzoontjens / lin-da）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：把 8 个月的项目压缩成 6–10 页，讲清"问题→判断→方案→验证"。
  2. **为什么困难**：过程证据在项目中没有被留存；留下的只有结果图；而作品集考核的恰恰是过程。
  3. **目前怎么解决**：拍照、重做、套模板、申请授权、只放完整项目。
  4. **造成什么后果**：作品集趋同、决策讲不清、面试失分；参与度低的岗位直接没有素材。
  5. **现有工具为什么没解决**：展示工具不生产素材；图库工具脱离上下文；PDM 只存交付物；IM 不可检索。
  6. **有没有潜在机会**：项目过程中的"轻量留痕"——自动把散落在聊天/邮件/文件里的决策点收进项目时间线，项目结束时直接生成复盘草稿。这是 RAG 知识库的自然延伸，难点是"设计师愿不愿意在过程中记录"。
- **六维打分**：
  - 发生频率 3：集中在换工作/求职期，但那时强度极高。
  - 严重程度 3：不致命，但直接影响职业机会。
  - 解决成本 3：重做作品集的时间成本高，但技术门槛低。
  - 未满足程度 4：没有任何工具在"过程中"为"未来的复盘"采集。
  - AI 适配度 3：能整理与生成叙事，但素材本身缺失时无能为力。
  - 改变意愿 4：求职期意愿极强；在职期意愿低。
- **通用性**：部分通用。任何知识工作者换工作都要整理成果；但设计作品集要求展示"未采用的方案和手绘过程"，这是设计行业特有的证据要求。

---

### P-S08b-01 上一个项目的判断依据留不下来，下一个项目从零开始对齐

- **设计师类型**：设计咨询 / 驻厂设计师（材料工艺与供应商知识密集）
- **工作阶段**：S08b 跨项目知识复用
- **具体场景**：新项目做户外电源。设计师记得上个项目在"IPX4 密封圈压缩量"上踩过坑，但找不到当时的计算、供应商回复和测试结果；问前同事，对方只记得"好像加了 0.3mm"。同时新客户对"详细图纸"的定义又和上一个客户不同——上一个客户一小时能出图，这个客户要三到四小时、多好几页。交付标准要重新谈一遍，报价也要重新猜。
- **原话**：
  - symbiont（2006-02-13）：
    > "I've seen this time and time again where you are part of a project team within a company or firm and someone leaves the team to move on to another project or another job elsewhere, effectively taking the designs current raison d'etre with them. I've done it myself."
    > "This is why it is extremely important to keep a well organized documented history of the projects development so you can review where and why the project has made detours to satisfy new criteria and individual team inputs."
    （译：我一次次看到这种情况：你在一个项目团队里，有人转去别的项目或别家公司，实际上把设计当时存在的理由一起带走了。我自己也干过。这就是为什么必须保留一份组织良好的项目开发记录，好让你回头查：项目在哪里、为什么为了满足新标准和个人意见而绕了弯。）
  - chevisw（2011-04-21）：
    > "I have several clients and their definition of 'detailed' or 'manufacturing' drawings vary vastly. One client i can crank out the drawings in less then an hour, another client there are several pages and it takes 3- 4 hours."
    （译：我有好几个客户，他们对"详细图"或"制造图"的定义差别巨大。一个客户的图我一小时以内就能出，另一个客户要好几页、花三到四小时。）
  - Anonymous1（2005-03-16）：
    > "nobody provides cost feedback (tooling, part cost, etc.) so I have no idea how fix the issue."
    （译：没人给成本反馈（模具费、零件成本等），我根本不知道该怎么修。）
- **问题原因**：设计经验的主要载体是"某个决定在当时为什么成立"，它是情境化的、和具体约束（材料、工艺、供应商、成本、客户标准）绑定。而归档系统存的是文件（图纸、模型、报告），不是"约束→决定→代价→验证结果"这条链。加上项目结束即解散，人一走，索引就没了；剩下的文件没有语义，等于没有。
- **目前怎么解决**：靠个人笔记/微信收藏；靠老员工口口相传；每个新客户重新对齐交付标准（要样例图纸）；报价时预留返工时间；把踩过的坑写在个人 Excel 里。
- **时间与资源成本**：每个新项目重新对齐交付标准 1–3 小时（chevisw 的 1 小时 vs 3–4 小时差距，差异本身就是成本）；经验断层导致的重复踩坑无法量化，此处标为推断。
- **造成的影响**：同样的错误重复犯；报价偏差（低估图纸工作量）；新人上手慢；公司层面没有积累，只有个人积累。
- **现有工具为什么没解决**：企业网盘只存文件；Notion/语雀需要人主动写且结构自定；PLM 存 BOM 和变更，不存"为什么"；Confluence 是文档库不是决策库；委托人现有的 RAG 知识库如果只索引文档，也拿不到"当时的约束"——因为约束从来没被写下来。
- **证据来源**：
  - https://boards.core77.com/t/clients-like-to-cut-paste/14173 （symbiont，2006-02-13）
  - https://boards.core77.com/t/post-project-support/26193 （chevisw，2011-04-21）
  - https://boards.core77.com/t/cant-seem-to-get-good-designs-into-production/11131 （Anonymous1，2005-03-16）
- **证据等级**：direct
- **六问深挖**：
  1. **具体任务**：在新项目开始时，快速取回上一个同类项目的关键判断（材料、结构、供应商、成本、客户标准）。
  2. **为什么困难**：判断是情境化的，脱离当时的约束就无法复用；归档系统只存结果，不存约束；人一走索引就断。
  3. **目前怎么解决**：个人笔记、口头传承、每个客户重新对齐标准、报价留余量。
  4. **造成什么后果**：重复踩坑、报价偏差、新人上手慢、公司无积累。
  5. **现有工具为什么没解决**：网盘/文档库/PLM 都以"文件"为单位，不以"决策"为单位；RAG 只能检索已写下的内容，写不下来的就检索不到。
  6. **有没有潜在机会**：以"决策"为单位而不是以"文档"为单位建库：每个决定记录约束、备选、代价、后续验证结果。这是委托人 RAG 项目最该做的方向，但前提是采集发生在项目过程中，而不是事后补。
- **六维打分**：
  - 发生频率 4：每个新项目都要重新建立上下文。
  - 严重程度 3：单次损失不大，但复利式累积。
  - 解决成本 4：需要改变记录习惯，且要跨项目统一结构。
  - 未满足程度 5：现有工具没有一个以"决策"为单位。
  - AI 适配度 4：抽取、结构化、按情境检索正是 RAG 的强项，且委托人已有基础设施。
  - 改变意愿 3：设计师认同价值，但不愿在项目中途多写一份记录。
- **通用性**：通用。所有项目型行业都有知识流失；但设计行业的约束高度依赖材料/工艺/供应商，跨项目迁移更难、价值也更高。

---

## 我搜过但没找到的

1. **Reddit r/IndustrialDesign**：直接访问与 `r.jina.ai` 代理均失败，未取回任何内容，本轮零条 Reddit 证据。
2. **小红书**：搜索能定位到"设计师改稿""设计师吐槽"类笔记标题，但正文需登录，抓取只得到登录墙，无法取得可引用的原话和稳定链接。因此本轮没有一条小红书证据——这是本次调研最大的缺口，因为委托人特别提到这类内容在小红书很多。
3. **站酷 ZCOOL**：文章页可访问，但内容以教程与作品展示为主，评论区在抓取中未呈现；没有找到"改稿/汇报/复盘"的当事人自述。
4. **知乎**：部分问题页可正常打开（如 67260224），但多数设计类问题页返回页面骨架或登录提示。可打开的"设计师 改稿"高赞内容几乎全是 UI/平面/视频案例（字体、logo、D1–D6 版本），按 scope 排除。没有找到工业设计语境下关于图纸版本、结构返工的高质量问答。
5. **BOSS 直聘 JD**：页面为动态渲染且需登录，未能稳定抓取到工业设计师 JD 原文。因此本轮 8 条记录全部为 direct，没有使用任何 indirect 的招聘证据——这也意味着"企业真实要求"这一维度本轮是空的。
6. **"改到第几版会失控"的具体版数**：没有任何一条原话给出"第 5 版"这类数字。社区里能确认的是"版本命名失控"（Final / Final2 / Final final）和"每次会后复制一个目录"，而不是"第 N 版"。因此 P-S06a-01 的成本只能给流程量，不能给版数。
7. **手板/打样/开模的具体金额与轮次**：中文侧搜索结果几乎全是手板厂的 SEO 软文（"深圳手板避坑""3D 打印 vs CNC"），没有设计师自述"一轮打样多少钱、多少天、返工几次"；Core77 上也没有专门讨论打样成本的帖子。因此 P-S06b-02 只能给流程性成本，没给定量的钱。
8. **设计公司公开的失败复盘**：搜到的都是营销性质的"项目案例包装"，没有真实的失败复盘或"我们做错了什么"的自述。
9. **被排除的近似证据**（说明为什么没用）：
   - 知乎「做为设计师，什么样的修改意见会让你崩溃？」（https://www.zhihu.com/question/546433068 ）高赞回答是 UI/平面/字体案例，不符合"产品/工业设计师"范围，未采用。
   - 微信公众号文章《图纸后台早已更新，车间现场依旧躺着作废旧版》（2026-11-02，梦柯，浙江压力容器人）是很好的版本失控案例，但对象是压力容器制造企业的技术部与车间，不是产品/工业设计师，故未作为记录，仅作背景参考：https://mp.weixin.qq.com/s?__biz=MzcwMzI5MTQwNw==&mid=2247484332&idx=1&sn=03e73fc23c8833db3794ba047ac616fe
   - 百家号/搜狐上的"ID 与 MD 为什么总掐架"类文章（如 https://baijiahao.baidu.com/s?id=1832628519203393135 ，2025-05-20）是自媒体观点稿，无当事人原话、无评论区，只能算 inferred，未采用。

---

## 附：本轮证据的结构性偏差（供下一轮修正）

- **语言偏差**：8 条记录中 7 条来自英文 Core77，1 条来自中文知乎。中文社区（小红书、站酷、V2EX、设计癖）本轮基本没吃到，原因是登录墙与反爬，不是没有内容。
- **年代偏差**：Core77 的有效讨论集中在 2005–2014 年。那时的痛点（文件命名、图纸版本、焦点小组）在今天可能已被 PDM/PLM/在线协作部分缓解，但"设计意图不可传递""保真度误读""决策理由不留存"这三条在 AI 时代反而更突出。下一轮应优先补 2020 年之后的证据。
- **角色偏差**：说英语的独立咨询公司设计师占比过高，驻厂设计师、供应商侧、模具厂侧的视角缺失。S06b 如果要真正做深，需要补工厂/结构工程师一侧的原话。
