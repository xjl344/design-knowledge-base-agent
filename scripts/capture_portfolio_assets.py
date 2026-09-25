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

    # Cold-retrieval demo: restart the app first so the in-process retrieval cache is empty,
    # then record immediately.  A cold run can take 3~5 minutes (and may hit the 300 s ceiling).
    python scripts/capture_portfolio_assets.py --timeout 540 --viewport-height 760 \
        --out-dir docs/portfolio/cold --mp4

Requires: playwright (installed outside the project venv; uses the local Edge browser).

Setup (once, outside the project venv):

    pip install playwright
    playwright install ffmpeg   # video recording needs this binary; Edge itself is reused
    pip install imageio-ffmpeg  # only for --mp4: Playwright's ffmpeg ships VP8 but no libx264

Without `playwright install ffmpeg` the browser context fails at `new_page()` with
"Video rendering requires ffmpeg binary" — the error is raised before any question is sent,
so it costs nothing but the setup step.

Recording is always written as `.webm` (VP8) because Playwright's bundled ffmpeg has no
libx264.  Pass `--mp4` to also transcode a silent H.264 copy via imageio-ffmpeg's ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import subprocess
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


def wait_for_completion(page, timeout_seconds: int, log, abort_after: float | None = None) -> tuple[float, bool]:
    """Poll the status column until the pipeline reports a delivery verdict.

    Returns (elapsed_seconds, completed).  `completed` is False when the timeout expired, so
    the caller can still keep whatever was captured instead of discarding the run.

    `abort_after` bails out early.  Retrieval latency on this machine is bimodal — the same
    query has been measured at ~20 s and at the 300 s ceiling — so a run that is still
    retrieving after a minute is usually going to hit the ceiling.  Note that abandoning the
    page does NOT cancel the server-side job; the app has to be restarted to free the CPU.
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
        if abort_after is not None and elapsed >= abort_after:
            log(f"  [{elapsed:6.1f}s] 提前放弃：超过 {abort_after:.0f} 秒仍未交付")
            return elapsed, False
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


def transcode_to_mp4(webm: Path, mp4: Path, log) -> bool:
    """把 Playwright 录出的 VP8/WebM 转成 H.264/MP4。

    为什么要转：Playwright 自带的 ffmpeg 只编了 VP8，没有 libx264，所以录出来只能是 .webm。
    有些播放器/浏览器对 VP8 支持不好，作品集里用 MP4 更稳。
    这里借 imageio-ffmpeg 的 ffmpeg（带 libx264 + aac）来转，**静音输出**——
    旁白由人按讲稿自己配，视频里不留音轨。

    转码失败不算捕获失败：.webm 原件仍在，调用方只需提示。
    """
    try:
        import imageio_ffmpeg
    except ImportError:
        log("      ⚠️ 未安装 imageio-ffmpeg，跳过 MP4 转码（.webm 原件已保留）")
        log("         安装：pip install imageio-ffmpeg")
        return False

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-i", str(webm),
        "-c:v", "libx264", "-preset", "slow", "-crf", "30",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-an", str(mp4),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        log(f"      ⚠️ MP4 转码失败（返回码 {result.returncode}），.webm 原件仍在")
        return False
    log(f"      · MP4：{mp4.name}（{mp4.stat().st_size / 1e6:.2f} MB，静音 H.264）")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7860/")
    parser.add_argument("--question", default=QUESTION)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout", type=int, default=900, help="max seconds to wait for the answer")
    parser.add_argument(
        "--viewport-height",
        type=int,
        default=1000,
        help="浏览器视口高度。截图是整页截取，视口过高会在输入框下方留下大片空白",
    )
    parser.add_argument(
        "--abort-after",
        type=float,
        default=None,
        help="超过这么多秒仍未交付就放弃（用于重试慢运行；放弃不会取消服务端任务）",
    )
    parser.add_argument("--no-video", action="store_true", help="screenshots only, skip recording")
    parser.add_argument(
        "--mp4",
        action="store_true",
        help="录屏后额外转一份 H.264 MP4（静音）。需要 imageio-ffmpeg；失败不影响 .webm",
    )
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
        context_kwargs = {"viewport": {"width": 1600, "height": args.viewport_height}}
        if not args.no_video:
            context_kwargs["record_video_dir"] = str(video_dir)
            context_kwargs["record_video_size"] = {"width": 1600, "height": args.viewport_height}
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

        elapsed, completed = wait_for_completion(page, args.timeout, log, args.abort_after)
        if completed:
            log(f"[4/5] 运行结束，真实耗时 {elapsed:.1f} 秒")
        else:
            log(f"[4/5] ⚠️ 等待 {elapsed:.1f} 秒仍未拿到交付判定（超时）；仍会保存已捕获的画面")

        main_shot = out_dir / f"02_main_interface_{stamp}.png"
        # The chatbot autoscrolls to the bottom, so a naive screenshot shows only the tail of
        # the answer — the 【资料事实】 table holding the actual answer is scrolled out of view.
        # Scroll the message container back to the top for the "final answer" shot.
        scrolled = page.evaluate(
            """() => {
              const el = document.querySelector('.bubble-wrap') || document.querySelector('.chatbot');
              if (!el) return null;
              el.scrollTop = 0;
              return {cls: el.className.toString().slice(0, 40), scrollHeight: el.scrollHeight, clientHeight: el.clientHeight};
            }"""
        )
        if scrolled:
            log(f"      · 聊天区已滚回顶部：{scrolled['cls']} "
                f"（内容 {scrolled['scrollHeight']}px / 可视 {scrolled['clientHeight']}px）")
        else:
            log("      · ⚠️ 没找到聊天滚动容器，截图可能仍停在底部")
        page.wait_for_timeout(800)
        page.screenshot(path=str(main_shot), full_page=True)

        status_col = page.locator("#status-column")
        trace_shot = out_dir / f"03_execution_trace_{stamp}.png"
        status_col.screenshot(path=str(trace_shot))

        # A second shot of the answer's tail (证据不足与冲突 / 风险与验证 / 参考资料), which is the
        # part the delivery gate judges.  Same page, same run — not a re-enactment.
        tail_shot = out_dir / f"02b_answer_tail_{stamp}.png"
        page.evaluate(
            """() => {
              const el = document.querySelector('.bubble-wrap') || document.querySelector('.chatbot');
              if (el) el.scrollTop = el.scrollHeight;
            }"""
        )
        page.wait_for_timeout(600)
        page.screenshot(path=str(tail_shot), full_page=True)

        status_text = status_col.inner_text()
        # The answer lives in the chatbot column; grab the whole body and let the caller trim.
        answer_text = page.inner_text("body")
        trace_is_populated = '"session_id"' in status_text

        log(f"      · 主界面：{main_shot.name}")
        log(f"      · 执行追踪：{trace_shot.name}")

        context.close()  # flushes the video file
        browser.close()

    video_path = None
    mp4_path = None
    if not args.no_video:
        candidates = sorted(video_dir.glob("*.webm"), key=lambda f: f.stat().st_mtime, reverse=True)
        if candidates:
            target = out_dir / f"demo_recording_{stamp}.webm"
            candidates[0].replace(target)
            video_path = target
            log(f"      · 录屏：{target.name}（{target.stat().st_size / 1e6:.1f} MB）")
            if args.mp4:
                mp4_target = out_dir / f"demo_recording_{stamp}.mp4"
                if transcode_to_mp4(target, mp4_target, log):
                    mp4_path = mp4_target

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
            "answer_tail": tail_shot.name,
            "execution_trace": trace_shot.name,
        },
        "video": video_path.name if video_path else None,
        "video_mp4": mp4_path.name if mp4_path else None,
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

    # Exit code 2 signals "captured but the pipeline never reported a delivery verdict",
    # so a retry loop can distinguish it from a crash (1) or success (0).
    return 0 if completed else 2


if __name__ == "__main__":
    sys.exit(main())
