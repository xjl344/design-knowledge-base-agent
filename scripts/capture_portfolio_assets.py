"""Capture portfolio assets from a *running* app: screenshots + a real-time screen recording.

Why this exists
---------------
The portfolio plan (B4) requires three screenshots and a demo video. Hand-recording is
error-prone and easy to fake; this script drives the real UI against a real question and
records what actually happens, including the real elapsed time.

It deliberately does NOT speed up or cut the waiting period: the long retrieval latency is
part of the project's credibility (see 作品集Demo讲稿.md).

Usage
-----
    # 1. start the app first (ASCII-path venv):
    #    E:\\venvs\\design-kb-round2\\Scripts\\python.exe -u app.py
    # 2. then:
    python scripts/capture_portfolio_assets.py --question "GB/T 16252—2023 的名称和适用范围是什么？"

Requires: playwright (installed outside the project venv; uses the local Edge browser).

Setup (once, outside the project venv):

    pip install playwright
    playwright install ffmpeg   # video recording needs this binary; Edge itself is reused

Without `playwright install ffmpeg` the browser context fails at `new_page()` with
"Video rendering requires ffmpeg binary" — the error is raised before any question is sent,
so it costs nothing but the setup step.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "portfolio"
QUESTION = "GB/T 16252—2023 的名称和适用范围是什么？"

# The status markdown shows these while the pipeline is running.
RUNNING_MARKERS = ("正在生成", "正在处理", "正在整理来源")

# Only the final yield of `chat()` emits "可交付：是/否" (see app.py:format_delivery_status).
# That makes it the one string that is *exclusive* to a finished run.
#
# Do NOT try to detect completion by the absence of RUNNING_MARKERS alone: Gradio paints its
# own queue tracker into the same column ("processing | 16.1s"), which matches no marker and
# therefore looks like "done" 8 seconds into a 5-minute run.  The first version of this script
# did exactly that and produced screenshots of an idle UI plus an 8-second "recording".
DONE_MARKER = "可交付"


def wait_for_completion(page, timeout_seconds: int, log) -> tuple[float, bool]:
    """Poll the status column until the pipeline reports a delivery verdict.

    Returns (elapsed_seconds, completed).  `completed` is False when the timeout expired, so
    the caller can still keep whatever was captured instead of discarding the run.
    """
    started = time.time()
    last_text = ""
    while time.time() - started < timeout_seconds:
        page.wait_for_timeout(5000)
        try:
            text = page.locator("#status-column").inner_text(timeout=10000)
        except Exception:
            continue
        elapsed = time.time() - started
        if text != last_text:
            first_line = text.strip().splitlines()[0] if text.strip() else "(空)"
            log(f"  [{elapsed:6.1f}s] 状态变化：{first_line}")
            last_text = text
        if DONE_MARKER in text and not any(marker in text for marker in RUNNING_MARKERS):
            # Give the UI one more beat to flush the JSON panel and clear the tracker.
            page.wait_for_timeout(4000)
            return time.time() - started, True
    return time.time() - started, False


REPORT_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 48px 56px;
  background: #ffffff; color: #1f2937;
  font-family: "Segoe UI", "Microsoft YaHei", system-ui, -apple-system, sans-serif;
  font-size: 15px; line-height: 1.65; max-width: 1180px;
}
h1 { font-size: 26px; margin: 0 0 6px; color: #111827; letter-spacing: .2px; }
h2 { font-size: 19px; margin: 34px 0 12px; color: #111827;
     border-left: 4px solid #4f46e5; padding-left: 10px; }
p { margin: 8px 0; }
code { background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 4px;
       padding: 1px 5px; font-family: Consolas, "Cascadia Mono", monospace; font-size: 13px; }
table { border-collapse: collapse; margin: 14px 0 22px; width: 100%;
        font-variant-numeric: tabular-nums; }
th, td { border: 1px solid #e5e7eb; padding: 7px 12px; text-align: left; }
th { background: #f9fafb; font-weight: 600; color: #374151; }
tbody tr:nth-child(even) { background: #fcfcfd; }
td:nth-child(n+3), th:nth-child(n+3) { text-align: right; }
.meta { color: #6b7280; font-size: 13px; }
"""


def render_report_screenshot(page, md_path: Path, out_path: Path) -> None:
    """Render a Markdown report to a standalone page and screenshot it.

    Uses `page.set_content` rather than a temp file so nothing is left on disk and the
    report's own text is the only thing in frame.
    """
    import markdown  # local import: only the report mode needs it

    text = md_path.read_text(encoding="utf-8-sig")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    html = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<style>{REPORT_CSS}</style></head><body>{body}</body></html>"
    )
    page.set_content(html, wait_until="load")
    page.wait_for_timeout(1200)
    page.screenshot(path=str(out_path), full_page=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7860/")
    parser.add_argument("--question", default=QUESTION)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout", type=int, default=900, help="max seconds to wait for the answer")
    parser.add_argument("--no-video", action="store_true", help="screenshots only, skip recording")
    parser.add_argument(
        "--report-md",
        default=None,
        help="只渲染这份 Markdown 报告并截图（评测结果表截图），不跑应用",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_dir = out_dir / "_video"
    video_dir.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        print(message, flush=True)

    if args.report_md:
        md_path = Path(args.report_md)
        if not md_path.is_absolute():
            md_path = ROOT / md_path
        if not md_path.exists():
            log(f"报告不存在：{md_path}")
            return 1
        target = out_dir / f"04_eval_baseline_{stamp}.png"
        log(f"[报告模式] 渲染 {md_path.name}")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1240, "height": 1200})
            render_report_screenshot(page, md_path, target)
            browser.close()
        log(f"      · 已存：{target.name}")
        return 0

    log(f"[1/5] 目标问题：{args.question}")
    log(f"[2/5] 输出目录：{out_dir}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context_kwargs = {"viewport": {"width": 1600, "height": 1000}}
        if not args.no_video:
            context_kwargs["record_video_dir"] = str(video_dir)
            context_kwargs["record_video_size"] = {"width": 1600, "height": 1000}
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("textarea", timeout=60000)
        page.wait_for_timeout(5000)

        idle_shot = out_dir / f"00_idle_{stamp}.png"
        page.screenshot(path=str(idle_shot), full_page=True)
        log(f"      · 已存初始界面：{idle_shot.name}")

        log("[3/5] 提交问题，开始计时（不加速、不剪辑）")
        page.fill("textarea", args.question)
        page.click('button[data-testid="submit-button"]')
        page.wait_for_timeout(8000)

        running_shot = out_dir / f"01_running_{stamp}.png"
        page.screenshot(path=str(running_shot), full_page=True)
        log(f"      · 已存运行中界面：{running_shot.name}")

        elapsed, completed = wait_for_completion(page, args.timeout, log)
        if completed:
            log(f"[4/5] 运行结束，真实耗时 {elapsed:.1f} 秒")
        else:
            log(f"[4/5] ⚠️ 等待 {elapsed:.1f} 秒仍未拿到交付判定（超时）；仍会保存已捕获的画面")

        main_shot = out_dir / f"02_main_interface_{stamp}.png"
        page.screenshot(path=str(main_shot), full_page=True)

        status_col = page.locator("#status-column")
        trace_shot = out_dir / f"03_execution_trace_{stamp}.png"
        status_col.screenshot(path=str(trace_shot))

        status_text = status_col.inner_text()
        # The answer lives in the chatbot column; grab the whole body and let the caller trim.
        answer_text = page.inner_text("body")
        trace_is_populated = '"session_id"' in status_text

        log(f"      · 主界面：{main_shot.name}")
        log(f"      · 执行追踪：{trace_shot.name}")

        context.close()  # flushes the video file
        browser.close()

    video_path = None
    if not args.no_video:
        candidates = sorted(video_dir.glob("*.webm"), key=lambda f: f.stat().st_mtime, reverse=True)
        if candidates:
            target = out_dir / f"demo_recording_{stamp}.webm"
            candidates[0].replace(target)
            video_path = target
            log(f"      · 录屏：{target.name}（{target.stat().st_size / 1e6:.1f} MB）")

    summary = {
        "captured_at": stamp,
        "question": args.question,
        "elapsed_seconds": round(elapsed, 1),
        "completed": completed,
        "trace_panel_populated": trace_is_populated,
        "screenshots": {
            "idle": idle_shot.name,
            "running": running_shot.name,
            "main_interface": main_shot.name,
            "execution_trace": trace_shot.name,
        },
        "video": video_path.name if video_path else None,
        "status_text": status_text.strip(),
        "answer_excerpt": answer_text.strip()[:2000],
    }
    summary_path = out_dir / f"capture_summary_{stamp}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[5/5] 摘要：{summary_path.name}")

    if not trace_is_populated:
        log("")
        log("⚠️ 执行追踪面板里没有 session_id：这次捕获不可用，请检查截图后重跑。")

    log("")
    log("=== 运行状态 ===")
    log(status_text.strip()[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
