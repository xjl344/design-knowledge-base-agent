"""发布前密钥审计（v2，快速版）。

v1 对每个 blob 起一次 git 子进程，太慢被杀了。v2 改用：
  · 当前文件：纯 Python 读
  · 全部历史：一次 `git grep -F -f <模式文件>` 覆盖所有提交

安全约束：只报命中位置，密钥值一律打码；模式文件用完立即删除。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP_PATTERN_FILE = ROOT / ".tmp" / "_secret_patterns.txt"

SECRET_SOURCES = [".env", ".env.bak.timeout20", ".env.bak.timeout60"]
MIN_SECRET_LEN = 12
SKIP_VALUE_HINTS = ("true", "false", "cpu", "cuda", "none", "auto", "0", "1")

# 只有「变量名像凭据」的才拿去全库搜。否则会把 RETRIEVER_RANKING_MODE=reranker+rrf、
# LANGCHAIN_PROJECT=design-knowledge-qa 这类普通配置值也当成密钥，产生大量误报。
CREDENTIAL_NAME = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|secret|token|password|passwd|credential)"
)

# 这些值不是凭据，但会暴露你的配置，单独提示（不参与「泄漏」判定）
INFORMATIONAL_NAME = re.compile(r"(?i)(base[_-]?url|endpoint|host|project|model)")

GENERIC_PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{20,}", "OpenAI 风格 sk-"),
    (r"lsv2_[A-Za-z0-9]{10,}", "LangSmith lsv2_"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub PAT"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained PAT"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    (r"(?i)\b(?:api[_-]?key|apikey|secret|access[_-]?token)\b\s*[:=]\s*[\"']([A-Za-z0-9_\-]{28,})[\"']", "疑似密钥赋值"),
]


def mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…({len(value)}字符)…{value[-3:]}"


def git(*args: str, timeout: int = 240) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout


def load_real_secrets() -> list[tuple[str, str, str]]:
    """只返回「变量名像凭据」的项。"""
    found = []
    for name in SECRET_SOURCES:
        p = ROOT / name
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if not CREDENTIAL_NAME.search(key):
                continue
            if len(value) < MIN_SECRET_LEN or value.lower() in SKIP_VALUE_HINTS:
                continue
            found.append((name, key, value))
    return found


def load_informational() -> list[tuple[str, str, str]]:
    """非凭据但会暴露配置的项（如 provider 地址、项目名），仅作提示。"""
    found = []
    seen = set()
    for name in SECRET_SOURCES:
        p = ROOT / name
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if len(value) < MIN_SECRET_LEN or not INFORMATIONAL_NAME.search(key):
                continue
            if (key, value) in seen:
                continue
            seen.add((key, value))
            found.append((name, key, value))
    return found


def main() -> int:
    secrets = load_real_secrets()
    info = load_informational()
    print("=" * 78)
    print("发布前密钥审计 v2")
    print("=" * 78)
    print(f"\n凭据类变量 {len(secrets)} 个（拿去全库搜，打码显示）：")
    for src, key, val in secrets:
        print(f"  · {src}  {key} = {mask(val)}")
    print(f"\n非凭据但会暴露配置的项 {len(info)} 个（仅提示，不算泄漏）：")
    for src, key, val in info:
        print(f"  · {src}  {key} = {val}")

    hits = 0

    print("\n" + "-" * 78)
    print("[1/4] 已跟踪文件（将被发布的内容）")
    print("-" * 78)
    tracked = [f for f in git("ls-files").splitlines() if f.strip()]
    print(f"已跟踪文件：{len(tracked)} 个")
    for rel in tracked:
        p = ROOT / rel
        if not p.is_file() or p.stat().st_size > 8_000_000:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for src, key, val in secrets:
            if val in text:
                print(f"  ❌ {rel}：含 {src} 的 {key} = {mask(val)}")
                hits += 1
        for pat, desc in GENERIC_PATTERNS:
            for m in re.finditer(pat, text):
                frag = m.group(1) if m.groups() else m.group(0)
                print(f"  ⚠️ {rel}：{desc} = {mask(frag)}")
                hits += 1

    print("\n" + "-" * 78)
    print("[2/4] 全部 git 历史（删掉的文件仍在这里）")
    print("-" * 78)
    patterns: list[str] = [v for _, _, v in secrets] + [p for p, _ in GENERIC_PATTERNS]
    TMP_PATTERN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TMP_PATTERN_FILE.write_text("\n".join(p for p in patterns if p.strip()) + "\n",
                                encoding="utf-8")
    try:
        commits = [c for c in git("rev-list", "--all").splitlines() if c.strip()]
        out = git("grep", "-I", "-F", "-l", "-f", str(TMP_PATTERN_FILE), *commits)
        lines = [l for l in out.splitlines() if l.strip()]
        if lines:
            for l in lines[:40]:
                sha, _, path = l.partition(":")
                print(f"  ⚠️ {sha[:8]}  {path}")
            hits += len(lines)
            print(f"  （共 {len(lines)} 条，历史里存在匹配，需逐条人工确认）")
        else:
            print("  ✅ 全部历史中未命中任何密钥值或密钥形态")
    finally:
        if TMP_PATTERN_FILE.exists():
            os.remove(TMP_PATTERN_FILE)
            print("  （模式文件已删除）")

    print("\n" + "-" * 78)
    print("[3/4] .env 是否曾被提交过")
    print("-" * 78)
    ever = git("log", "--all", "--oneline", "--diff-filter=A", "--", ".env", ".env.*")
    print(f"  {'⚠️ 曾经提交过：' + ever.strip() if ever.strip() else '✅ .env 及其备份从未进入任何提交'}")

    print("\n" + "-" * 78)
    print("[4/4] 未跟踪但未被忽略的文件（可能被误提交）")
    print("-" * 78)
    untracked = [f for f in git("ls-files", "--others", "--exclude-standard").splitlines() if f.strip()]
    print(f"未跟踪且未忽略：{len(untracked)} 个")
    for rel in untracked:
        p = ROOT / rel
        if not p.is_file() or p.stat().st_size > 8_000_000:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for src, key, val in secrets:
            if val in text:
                print(f"  ❌ {rel}：含 {src} 的 {key}")
                hits += 1
    if untracked:
        print("  文件清单（前 40 个）：")
        for rel in untracked[:40]:
            print(f"    · {rel}")

    print("\n" + "=" * 78)
    if hits == 0:
        print("✅ 未发现密钥泄漏")
        return 0
    print(f"⚠️ 共 {hits} 处命中，逐条见上（含历史匹配，需人工判断真伪）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
