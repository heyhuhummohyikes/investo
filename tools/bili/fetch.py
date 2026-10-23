"""Fetch Bilibili video metadata, subtitles, AI summary, hot comments and audio.

Usage: python fetch.py <BVID> <OUT_DIR>
Set BILI_PROXY (e.g. socks5h://127.0.0.1:40000) to route through a proxy.
Every step except metadata is best-effort; failures are logged, not fatal.
"""
import hashlib
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.parse
import uuid

import requests

BV = sys.argv[1]
OUT = pathlib.Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

S = requests.Session()
if os.environ.get("BILI_PROXY"):
    S.proxies = {"http": os.environ["BILI_PROXY"], "https": os.environ["BILI_PROXY"]}
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": f"https://www.bilibili.com/video/{BV}/",
    "Origin": "https://www.bilibili.com",
})

MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
         33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
         61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
         36, 20, 34, 44, 52]


def log(*a):
    print("[fetch]", *a, flush=True)


def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def get_json(url, params=None):
    r = S.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} {url}: {r.text[:200]!r}")
    return r.json()


def wbi_key():
    img = get_json("https://api.bilibili.com/x/web-interface/nav")["data"]["wbi_img"]
    stem = lambda u: u.rsplit("/", 1)[1].split(".")[0]
    raw = stem(img["img_url"]) + stem(img["sub_url"])
    return "".join(raw[i] for i in MIXIN)[:32]


def wbi(params, key):
    if not key:
        return params
    p = dict(params, wts=int(time.time()))
    p = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in sorted(p.items())}
    p["w_rid"] = hashlib.md5((urllib.parse.urlencode(p) + key).encode()).hexdigest()
    return p


def step(name):
    def deco(fn):
        def run(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:  # best-effort steps
                log(f"{name} failed: {e!r}")
        return run
    return deco


def seed_cookies():
    try:
        r = S.get("https://www.bilibili.com/", timeout=30, headers={"Accept": "text/html"})
        log("homepage:", r.status_code)
    except Exception as e:
        log("homepage failed:", repr(e))
    try:
        spi = get_json("https://api.bilibili.com/x/frontend/finger/spi")["data"]
        S.cookies.set("buvid3", spi["b_3"], domain=".bilibili.com")
        S.cookies.set("buvid4", spi["b_4"], domain=".bilibili.com")
    except Exception as e:
        log("spi failed:", repr(e))
        S.cookies.set("buvid3", f"{uuid.uuid4()}{random.randint(10000, 99999)}infoc".upper(),
                      domain=".bilibili.com")
    S.cookies.set("b_nut", str(int(time.time())), domain=".bilibili.com")
    S.cookies.set("_uuid", f"{uuid.uuid4()}{random.randint(10000, 99999)}infoc".upper(),
                  domain=".bilibili.com")
    log("cookies:", sorted(S.cookies.keys()))


def view_from_page():
    r = S.get(f"https://www.bilibili.com/video/{BV}/", timeout=30, headers={"Accept": "text/html"})
    if r.status_code != 200:
        raise RuntimeError(f"page HTTP {r.status_code}")
    m = re.search(r"window\.__INITIAL_STATE__=(\{.*?\});\(function", r.text)
    return {"code": 0, "data": json.loads(m.group(1))["videoData"]}


seed_cookies()
KEY = None
try:
    KEY = wbi_key()
except Exception as e:
    log("wbi key failed:", repr(e))

view = None
for name, fn in [
    ("wbi/view", lambda: get_json("https://api.bilibili.com/x/web-interface/wbi/view", wbi({"bvid": BV}, KEY))),
    ("view", lambda: get_json("https://api.bilibili.com/x/web-interface/view", {"bvid": BV})),
    ("page", view_from_page),
]:
    try:
        v = fn()
        if v.get("code") == 0:
            view = v
            log("view via", name)
            break
        log(name, "code:", v.get("code"), v.get("message"))
    except Exception as e:
        log(name, "failed:", repr(e))
if not view:
    sys.exit(1)

save("view.json", view)
d = view["data"]
aid, cid, mid = d["aid"], d["pages"][0]["cid"], d["owner"]["mid"]
meta = {
    "bvid": BV, "aid": aid, "cid": cid, "title": d["title"], "desc": d.get("desc"),
    "owner": d["owner"], "duration": d["duration"], "pubdate": d["pubdate"],
    "tname": d.get("tname"), "pages": [(p["page"], p["part"], p["duration"]) for p in d["pages"]],
    "stat": d.get("stat"),
}
save("meta.json", meta)
log("title:", d["title"], "| duration:", d["duration"], "s | pages:", len(d["pages"]))


@step("tags")
def tags():
    t = get_json("https://api.bilibili.com/x/tag/archive/tags", {"bvid": BV})
    save("tags.json", [x.get("tag_name") for x in (t.get("data") or [])])


@step("subtitles")
def subtitles():
    url = "https://api.bilibili.com/x/player/wbi/v2" if KEY else "https://api.bilibili.com/x/player/v2"
    subs = get_json(url, wbi({"aid": aid, "cid": cid}, KEY)).get("data", {}).get("subtitle", {}).get("subtitles") or []
    save("subtitle_list.json", subs)
    log("subtitle tracks:", [s.get("lan") for s in subs])
    if not subs:
        return
    subs.sort(key=lambda s: (s.get("lan") not in ("zh-CN", "zh-Hans", "ai-zh"), s.get("lan", "")))
    u = subs[0]["subtitle_url"]
    body = get_json("https:" + u if u.startswith("//") else u)["body"]
    lines = [f"[{int(b['from'] // 60):02d}:{int(b['from'] % 60):02d}] {b['content']}" for b in body]
    (OUT / "subtitles.txt").write_text("\n".join(lines), encoding="utf-8")
    log("subtitle lines:", len(lines))


@step("ai_conclusion")
def conclusion():
    if not KEY:
        return
    c = get_json("https://api.bilibili.com/x/web-interface/view/conclusion/get",
                 wbi({"bvid": BV, "cid": cid, "up_mid": mid}, KEY))
    save("ai_conclusion.json", c)
    log("ai conclusion code:", c.get("code"), "| model code:", (c.get("data") or {}).get("code"))


@step("comments")
def comments():
    if KEY:
        c = get_json("https://api.bilibili.com/x/v2/reply/wbi/main",
                     wbi({"oid": aid, "type": 1, "mode": 3, "plat": 1}, KEY))
    else:
        c = get_json("https://api.bilibili.com/x/v2/reply", {"oid": aid, "type": 1, "sort": 1, "ps": 20})
    data = c.get("data") or {}
    replies = (data.get("top_replies") or []) + (data.get("replies") or [])
    out = [f"[{r.get('like', 0)} likes] {r['member']['uname']}: {r['content']['message']}" for r in replies]
    (OUT / "comments.txt").write_text("\n\n".join(out), encoding="utf-8")
    log("comments:", len(out))


def ranged_download(src, dest, chunk=1_500_000):
    # The CDN drops long cross-border transfers, so fetch in small ranges with retries.
    size, pos = None, 0
    with open(dest, "wb") as f:
        while size is None or pos < size:
            for attempt in range(6):
                try:
                    r = S.get(src, headers={"Range": f"bytes={pos}-{pos + chunk - 1}"}, timeout=60)
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {r.status_code}")
                    break
                except Exception as e:
                    log(f"range {pos} attempt {attempt} failed: {e!r}")
                    time.sleep(2 * (attempt + 1))
            else:
                raise RuntimeError(f"range {pos} failed")
            if size is None:
                cr = r.headers.get("Content-Range")
                size = int(cr.rsplit("/", 1)[1]) if cr else len(r.content)
            f.write(r.content)
            pos += len(r.content)
    return size


@step("audio")
def audio():
    params = {"bvid": BV, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 0}
    url = "https://api.bilibili.com/x/player/wbi/playurl" if KEY else "https://api.bilibili.com/x/player/playurl"
    pu = get_json(url, wbi(params, KEY))
    tracks = (pu.get("data") or {}).get("dash", {}).get("audio") or []
    if not tracks:
        log("no dash audio:", pu.get("code"), pu.get("message"))
        return
    t = min(tracks, key=lambda a: a.get("bandwidth", 0))
    dest = OUT / "audio.m4a"
    for src in [t.get("baseUrl") or t.get("base_url")] + (t.get("backupUrl") or t.get("backup_url") or []):
        try:
            log("audio bytes:", ranged_download(src, dest))
            return
        except Exception as e:
            log("audio src failed:", repr(e))
            dest.unlink(missing_ok=True)


tags()
subtitles()
conclusion()
comments()
audio()
