"""Wait until a Windows process goes idle, by watching its CPU time.

Why this exists
---------------
The app keeps executing a queued request server-side even after the browser page that
submitted it is gone.  Starting the next capture while the previous one is still burning
CPU makes both runs slower and the recorded "real elapsed time" meaningless — and the
portfolio's whole point is that the recorded number is real.

Usage:
    python scripts/wait_for_idle.py --pid 63204 --threshold 60 --timeout 900
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


def open_process(pid: int) -> int:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        raise OSError(f"无法打开 PID {pid}，错误码 {ctypes.get_last_error()}")
    return handle


def cpu_seconds(handle: int) -> float:
    created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    _kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    )
    total = ((kernel.hi << 32) | kernel.lo) + ((user.hi << 32) | user.lo)
    return total / 1e7


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--threshold", type=float, default=60.0, help="单核占用百分比上限")
    parser.add_argument("--window", type=float, default=10.0, help="每次采样的秒数")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    handle = open_process(args.pid)
    started = time.time()
    while True:
        before = cpu_seconds(handle)
        time.sleep(args.window)
        after = cpu_seconds(handle)
        percent = (after - before) / args.window * 100
        elapsed = time.time() - started
        print(f"[{elapsed:6.1f}s] 单核占用约 {percent:5.0f}%", flush=True)
        if percent <= args.threshold:
            print(f"已空闲（<= {args.threshold:.0f}%），可以开始下一次捕获。", flush=True)
            return 0
        if elapsed > args.timeout:
            print(f"等待超过 {args.timeout:.0f} 秒仍未空闲，放弃等待。", flush=True)
            return 1


if __name__ == "__main__":
    sys.exit(main())
