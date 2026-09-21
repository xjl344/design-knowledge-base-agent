# 环境重建与 LangSmith 测试说明

旧环境已删除：

- `E:\设计知识库助手\.venv`
- `E:\venvs\design-kb`

这两个目录都不是项目数据，不包含源文档、Chroma 数据库、模型或日志。

## 1. 创建新的 ASCII 路径环境

```powershell
py -3.14 -m venv E:\venvs\design-kb-round2
E:\venvs\design-kb-round2\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r E:\设计知识库助手\requirements.txt
```

确认进入环境：

```powershell
python -c "import sys; print(sys.executable)"
```

输出应包含：

```text
E:\venvs\design-kb-round2\Scripts\python.exe
```

## 2. 配置 LangSmith

在项目根目录 `.env` 中确认：

```dotenv
LANGCHAIN_API_KEY=你的LangSmith密钥
LANGCHAIN_TRACING_V2=false
LANGCHAIN_PROJECT=design-knowledge-qa
```

`eval_round2.py` 不提供本地-only模式。每次执行都会：

1. 同步 Dataset `design-knowledge-qa-round2-8`；
2. 执行全部8题；
3. 创建新的 LangSmith Experiment；
4. 上传答案、来源、`deliverable`、阻断原因和引用校验结果。

## 3. 运行第二轮测试

```powershell
Set-Location E:\设计知识库助手
.\scripts\Run-Round2Questions.ps1 -PythonPath 'E:\venvs\design-kb-round2\Scripts\python.exe'
```

如果还要上传 LLM 答案质量评分：

```powershell
.\scripts\Run-Round2Questions.ps1 -PythonPath 'E:\venvs\design-kb-round2\Scripts\python.exe' -WithJudge
```

如果要测试网络补证路径：

```powershell
.\scripts\Run-Round2Questions.ps1 -PythonPath 'E:\venvs\design-kb-round2\Scripts\python.exe' -WithWeb
```

每次运行都会在 LangSmith 的 **Datasets & Experiments** 中产生新的 Experiment；本地 `logs` 只保存上传清单，不能代替上传结果。

## 4. 可复现参数与本地结果

每次运行都会在 `logs/round2_runs/` 保存 JSON、Markdown 和 manifest。manifest 包含实际生效的模型、分块、top-k、重排、规划、查询分解、网络搜索和 judge 配置；LangSmith 上传失败时仍会保留本地结果。

```powershell
.\scripts\Run-Round2Questions.ps1 -PythonPath 'E:\venvs\design-kb-round2\Scripts\python.exe' -WithJudge -RetrieverTopK 10 -DenseTopK 50 -Bm25TopK 50 -RerankerEnabled $true -PlanningEnabled $true -QueryDecomposeEnabled $true -RunLabel 'baseline-reranker-planning'
```

结果分为三层：检索层（Recall/MRR/NDCG、top-k、检索延迟）、答案层（交付状态、证据覆盖、引用有效性、答案结构、judge 正确性）和端到端层（规划、检索、网络搜索、生成、重写及总延迟）。
