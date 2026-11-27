# -*- coding: utf-8 -*-
"""通过 CDP 控制已登录的 Edge 浏览器，执行 JS 并取回结果。

用法：
    python cdp_client.py list                      # 列出标签页
    python cdp_client.py open <url>                # 打开新标签页
    python cdp_client.py eval <js>                 # 在当前页执行 JS
    python cdp_client.py text                      # 取当前页纯文本
"""
import json
import os
import sys
import time
import urllib.request

import websocket

sys.stdout.reconfigure(encoding="utf-8")

# 支持多实例并行：不同采集任务连不同调试端口
CDP = "http://127.0.0.1:" + os.environ.get("CDP_PORT", "9224")

# 绕过系统代理（沙箱代理不转发 localhost）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_OPENER)


def http(path):
    with _OPENER.open(CDP + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def pages():
    return [t for t in http("/json/list") if t.get("type") == "page"]


def pick(ws_url=None):
    ps = pages()
    if not ps:
        raise SystemExit("没有可用标签页")
    if ws_url:
        return ws_url
    # 优先匹配 CDP_TARGET 指定的域名（多标签页并行时防止抓错页）
    target = os.environ.get("CDP_TARGET", "")
    if target:
        for p in ps:
            if target in p.get("url", ""):
                return p["webSocketDebuggerUrl"]
    # 优先选非 about:blank
    for p in ps:
        if p["url"] != "about:blank":
            return p["webSocketDebuggerUrl"]
    return ps[0]["webSocketDebuggerUrl"]


class Client:
    def __init__(self, ws_url=None):
        self.ws = websocket.create_connection(pick(ws_url), timeout=120,
                                              suppress_origin=True)
        self.i = 0

    def send(self, method, params=None):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                return {"result": {"error": "ws timeout"}}
            if msg.get("id") == mid:
                return msg

    def eval(self, js):
        r = self.send("Runtime.evaluate", {
            "expression": js, "returnByValue": True, "awaitPromise": True,
            "userGesture": True})
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return {"error": str(res["exceptionDetails"])[:400]}
        return res.get("result", {}).get("value")

    def goto(self, url, wait=5):
        self.send("Page.enable")
        self.send("Page.navigate", {"url": url})
        time.sleep(wait)

    def close(self):
        self.ws.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for t in pages():
            print(f"[{t.get('type')}] {t.get('title','')[:44]} | {t['url'][:80]}")
    elif cmd == "open":
        url = sys.argv[2]
        with urllib.request.urlopen(
                urllib.request.Request(f"{CDP}/json/new?{url}", method="PUT"),
                timeout=15) as r:
            print("已打开:", json.loads(r.read().decode())["url"][:80])
    else:
        c = Client()
        try:
            if cmd == "eval":
                print(json.dumps(c.eval(sys.argv[2]), ensure_ascii=False, indent=2)[:4000])
            elif cmd == "text":
                t = c.eval("document.body.innerText")
                print((t or "")[:5000])
            elif cmd == "title":
                print(c.eval("document.title"))
        finally:
            c.close()
