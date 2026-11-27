# -*- coding: utf-8 -*-
"""从 Edge/Chrome 的 Cookies 数据库中解密出明文 cookie。

原理：
  1. Local State 里的 os_crypt.encrypted_key → DPAPI 解密 → AES-256 主密钥
  2. Cookies 表里每条 encrypted_value → 去掉 'v10' 前缀 → AES-GCM 解密
     （前 12 字节 = nonce，末尾 16 字节 = tag）

用法：python read_cookies.py <Cookies副本路径> <LocalState路径> [域名过滤]
"""
import base64
import json
import sqlite3
import sys

import win32crypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.stdout.reconfigure(encoding="utf-8")


def get_key(local_state_path):
    with open(local_state_path, "r", encoding="utf-8") as f:
        ls = json.load(f)
    enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    enc = enc[5:]  # 去掉 b'DPAPI' 前缀
    return win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]


def decrypt(value, key):
    if value[:3] in (b"v10", b"v11"):
        nonce, ct = value[3:15], value[15:]
        try:
            return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8", "replace")
        except Exception:
            return None
    try:  # 老格式：DPAPI 直解
        return win32crypt.CryptUnprotectData(value, None, None, None, 0)[1].decode(
            "utf-8", "replace")
    except Exception:
        return None


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else "C:/Users/34464/_cookies_copy.db"
    ls = sys.argv[2] if len(sys.argv) > 2 else "C:/Users/34464/_localstate.json"
    filt = sys.argv[3] if len(sys.argv) > 3 else ""

    key = get_key(ls)
    print(f"主密钥长度：{len(key)} 字节")

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.text_factory = bytes
    rows = con.execute(
        "SELECT host_key, name, encrypted_value, path, expires_utc, is_secure "
        "FROM cookies").fetchall()
    print(f"cookie 总数：{len(rows)}")

    out = []
    for host, name, enc, path, exp, sec in rows:
        h = host.decode("utf-8", "replace")
        n = name.decode("utf-8", "replace")
        if filt and filt not in h:
            continue
        v = decrypt(enc, key)
        if v is None:
            continue
        out.append({"host": h, "name": n, "value": v,
                    "path": path.decode("utf-8", "replace"),
                    "secure": bool(sec)})

    # 落盘
    p = "C:/Users/34464/_cookies.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # 按域名统计
    from collections import Counter
    c = Counter(x["host"] for x in out)
    print(f"\n解密成功 {len(out)} 条 → {p}")
    print("\n各域名 cookie 数（前 20）：")
    for h, n in c.most_common(20):
        print(f"  {n:>4}  {h}")


if __name__ == "__main__":
    main()
