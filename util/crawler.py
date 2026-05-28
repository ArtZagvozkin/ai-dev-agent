#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://docs.axel.pro/"
START_URLS = [BASE_URL]

BASE_DIR = "/opt/ai-dev-agent/tmp/docs.axel.pro.max"
OUT_ROOT = Path(BASE_DIR + "/entries")
STATE_PATH = Path(BASE_DIR + "/state_crawl.json")
LOG_PATH = Path(BASE_DIR + "/logs/crawl.log")
DEBUG_DIR = Path(BASE_DIR + "/debug")

MAX_ENTRIES = 0        # 0 => no limit
REQUEST_DELAY_SEC = 2.5
TIMEOUT = (10, 40)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": BASE_URL,
}

ARTICLE_RE = re.compile(r"_(\d+)\.html?$", re.I)
FNAME_SAFE_RE = re.compile(r"[^A-Za-zА-Яа-я0-9()._\-]+")

SKIP_PATH_PREFIXES = (
    "/login/", "/register/", "/comment/", "/print/", "/news/", "/rssfeed/", "/rss.php",
    "/client/", "/admin/",
)
SKIP_EXT = (
    ".pdf", ".zip", ".rar", ".7z", ".exe", ".mp4", ".mkv", ".avi",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".css", ".js",
)
NO_LINK_SCAN_URLS = {
    "https://docs.axel.pro/baza-znanij-sherlok-109/",
    "https://docs.axel.pro/baza-znanij-logiq-38/",
    "https://docs.axel.pro/yuridicheskie-dokumenty-64/",
    "https://docs.axel.pro/sistema-kontrolya-dostupa-k-seti-axelnac-versiya-100-8/",
    "https://docs.axel.pro/sistema-kontrolya-dostupa-k-seti-axelnac-versiya-120-115/",
}


# ---------------- util ----------------

def iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dump_debug(prefix: str, url: str, html: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    p = DEBUG_DIR / f"{prefix}_{h}.html"
    p.write_text(html, encoding="utf-8")
    return p


def norm_url(url: str) -> str:
    return urldefrag(url)[0]


def is_internal(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and p.netloc == urlparse(BASE_URL).netloc


def entry_id_from(url: str) -> int | None:
    m = ARTICLE_RE.search(urlparse(url).path)
    return int(m.group(1)) if m else None


def should_skip(url: str) -> bool:
    path = urlparse(url).path.lower()
    return (
        any(path.startswith(p) for p in SKIP_PATH_PREFIXES) or
        any(path.endswith(ext) for ext in SKIP_EXT)
    )


def safe_name_from_url(url: str) -> str:
    name = Path(urlparse(url).path).name
    if not name:
        ext = Path(urlparse(url).path).suffix or ".bin"
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ext
    name = FNAME_SAFE_RE.sub("_", name).strip("._") or "file.bin"
    return name


def is_verification_html(html: str) -> bool:
    h = html.lower()
    return ("data:image/gif;base64" in h and "__js_p_" in h and "get_jhash" in h)


def full_title_from(soup: BeautifulSoup) -> str | None:
    """
    From breadcrumbs, e.g.:
    Axel PRO :: База знаний / База знаний AxelNAC / Обучающие материалы / Основы NAC-систем
    """
    nav = soup.select_one(".navigation.view_left")
    if not nav:
        return None

    parts: list[str] = []
    for el in nav.select("span.navigation"):
        t = el.get_text(" ", strip=True)
        if t:
            parts.append(t)

    return " / ".join(parts) or None


def setup_logging() -> logging.Logger:
    level_name = os.getenv("AXEL_LOG", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger("axel_crawl")
    log.setLevel(level)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")):
        h.setLevel(level)
        h.setFormatter(fmt)
        log.addHandler(h)

    log.info("Log level: %s (set AXEL_LOG=DEBUG for more)", level_name)
    return log


def load_state() -> dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4, connect=4, read=4, backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    a = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", a)
    s.mount("http://", a)
    return s


# ---------------- JS challenge ----------------

def _fixed_encode_uri_component(s: str) -> str:
    # fixedEncodeURIComponent = encodeURIComponent + escape !'()*
    return (
        quote(s, safe="~()*!.'")
        .replace("!", "%21").replace("'", "%27")
        .replace("(", "%28").replace(")", "%29").replace("*", "%2A")
    )


def _get_jhash(code: int) -> int:
    x, k, b = 123456789, 0, int(code)
    MOD = 16776960
    for i in range(1677696):
        x = ((x + b) ^ (x + (x % 3) + (x % 17) + b) ^ i) % MOD
        if x % 117 == 0:
            k = (k + 1) % 1111
    return k


def maybe_solve_js_challenge(log: logging.Logger, s: requests.Session, resp: requests.Response) -> bool:
    js_p = s.cookies.get("__js_p_")
    if not js_p:
        log.debug("No __js_p_. Set-Cookie: %s", resp.headers.get("Set-Cookie"))
        return False

    parts = js_p.split(",")
    try:
        code = int(parts[0].strip())
        age = int(parts[1].strip()) if len(parts) > 1 else 1800
        sec = int(parts[2].strip()) if len(parts) > 2 else 0
    except ValueError:
        log.warning("Bad __js_p_ format: %r", js_p)
        return False

    log.info("JS-CHALLENGE detected. __js_p_=%r => code=%d age=%d sec=%d", js_p, code, age, sec)
    t0 = time.time()
    jhash = _get_jhash(code)
    log.info("JS-CHALLENGE solved: __jhash_=%s (%.2fs), retrying...", jhash, time.time() - t0)

    dom = urlparse(BASE_URL).netloc
    s.cookies.set("__jhash_", str(jhash), path="/", domain=dom)
    s.cookies.set("__jua_", _fixed_encode_uri_component(UA), path="/", domain=dom)
    return True


# ---------------- fetch / parse ----------------

def fetch_html(log: logging.Logger, s: requests.Session, url: str) -> str:
    def get() -> requests.Response:
        return s.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)

    r = get()
    ctype = r.headers.get("Content-Type", "")
    log.info("FETCH %s -> %s %s (len=%s, ctype=%s)", url, r.status_code, r.url, len(r.content), ctype)
    if log.isEnabledFor(logging.DEBUG):
        log.debug("COOKIES: %s", s.cookies.get_dict())

    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    html = r.text

    if is_verification_html(html):
        p = dump_debug("verification", url, html)
        log.warning("Got verification HTML. Dumped: %s", p)
        if maybe_solve_js_challenge(log, s, r):
            time.sleep(1.05)
            r2 = get()
            ctype2 = r2.headers.get("Content-Type", "")
            log.info("FETCH(retry) %s -> %s %s (len=%s, ctype=%s)", url, r2.status_code, r2.url, len(r2.content), ctype2)
            r2.raise_for_status()
            r2.encoding = r2.encoding or "utf-8"
            html2 = r2.text
            if is_verification_html(html2):
                p2 = dump_debug("verification_retry", url, html2)
                log.error("Still verification after solving. Dumped: %s", p2)
            return html2

    return html


def fetch_bytes(log: logging.Logger, s: requests.Session, url: str) -> bytes:
    r = s.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    log.debug("FETCH(bin) %s -> %s %s (len=%s)", url, r.status_code, r.url, len(r.content))
    r.raise_for_status()
    return r.content


def collect_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [norm_url(urljoin(base, a["href"])) for a in soup.select("a[href]")]


def caption_for(img: Tag) -> str | None:
    # Small/cheap heuristic: figure>figcaption OR next <p> within reasonable length
    fig = img.find_parent("figure")
    if fig:
        cap = fig.find("figcaption")
        if cap:
            t = cap.get_text(" ", strip=True)
            if t:
                return t
    p = img.find_parent("p")
    if p:
        nxt = p.find_next_sibling("p")
        if nxt:
            t = nxt.get_text(" ", strip=True)
            if t:
                return t
    return None


def download_entry(log: logging.Logger, s: requests.Session, url: str, html: str) -> int:
    eid = entry_id_from(url)
    if eid is None:
        raise ValueError("no entry id")

    entry_dir = OUT_ROOT / str(eid)
    assets = entry_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    soup = BeautifulSoup(html, "html.parser")

    full_title = full_title_from(soup)

    title = None
    t = soup.select_one("#title_text") or soup.find("title")
    if t:
        title = t.get_text(" ", strip=True) or None

    updated_at, revision = None, None
    abb = soup.select_one(".abbBlock")
    if abb:
        text = abb.get_text("\n", strip=True)
        m = re.search(r"Последнее обновление:\s*(.+)", text, re.I)
        if m:
            updated_at = m.group(1).strip()
        m = re.search(r"Ревизия:\s*(\d+)", text, re.I)
        if m:
            revision = int(m.group(1))

    imgs = soup.select("#kbp_article_body img[src]")
    log.info("ENTRY %s: images found in body: %d", eid, len(imgs))

    images_meta: list[dict] = []
    for img in imgs:
        src = img.get("src") or ""
        abs_src = urljoin(url, src)
        if abs_src.startswith("data:") or not is_internal(abs_src):
            continue

        data = fetch_bytes(log, s, abs_src)
        name = safe_name_from_url(abs_src)
        out = assets / name

        if out.exists() and out.read_bytes() != data:
            h = hashlib.sha1(data).hexdigest()[:8]
            out = assets / f"{out.stem}_{h}{out.suffix or '.bin'}"

        out.write_bytes(data)
        img["src"] = f"./assets/{out.name}"

        images_meta.append({
            "local_path": f"assets/{out.name}",
            "original_url": abs_src,
            "alt": (img.get("alt") or "").strip() or None,
            "caption": caption_for(img),
        })

        time.sleep(REQUEST_DELAY_SEC)

    (entry_dir / "page.html").write_text(str(soup), encoding="utf-8")
    (entry_dir / "meta.json").write_text(json.dumps({
        "entry_id": eid,
        "source_url": url,
        "fetched_at": iso_utc(),
        "title": title,
        "full_title": full_title,
        "updated_at": updated_at,
        "revision": revision,
        "images": images_meta,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return eid


# ---------------- main ----------------

def main() -> None:
    log = setup_logging()
    log.info("START_URLS: %s", START_URLS)
    log.info("MAX_ENTRIES=%s REQUEST_DELAY_SEC=%s", MAX_ENTRIES, REQUEST_DELAY_SEC)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    downloaded = load_state()
    visited: set[str] = set()
    stats = Counter()

    q = deque(norm_url(u) for u in START_URLS)
    pages_seen = 0
    downloaded_this_run = 0

    def limit_reached() -> bool:
        return MAX_ENTRIES > 0 and downloaded_this_run >= MAX_ENTRIES

    with build_session() as s:
        while q and not limit_reached():
            url = q.popleft()
            stats["dequeued"] += 1

            if url in visited:
                stats["dupe_skipped"] += 1
                continue
            visited.add(url)

            if not is_internal(url):
                stats["external_skipped"] += 1
                continue
            if should_skip(url):
                stats["should_skip"] += 1
                continue

            pages_seen += 1
            log.info("PAGE %d: %s", pages_seen, url)

            try:
                html = fetch_html(log, s, url)
            except Exception as e:
                stats["fetch_fail"] += 1
                log.warning("FETCH FAIL %s (%s: %s)", url, type(e).__name__, e)
                time.sleep(REQUEST_DELAY_SEC)
                continue

            if is_verification_html(html):
                stats["verification_pages"] += 1
                time.sleep(REQUEST_DELAY_SEC)
                continue

            eid = entry_id_from(url)
            if eid is None:
                stats["not_an_entry"] += 1
            else:
                stats["entry_pages_seen"] += 1

            if eid is not None and str(eid) not in downloaded and not limit_reached():
                log.info("[%d/%s] entry %s: %s ...",
                         downloaded_this_run + 1, (MAX_ENTRIES or "*"), eid, url)
                try:
                    got = download_entry(log, s, url, html)
                    downloaded[str(got)] = url
                    save_state(downloaded)
                    downloaded_this_run += 1
                    stats["download_ok"] += 1
                    log.info("ENTRY %s OK", got)
                except Exception as e:
                    stats["download_fail"] += 1
                    log.error("ENTRY %s FAIL (%s: %s)", eid, type(e).__name__, e)

                time.sleep(REQUEST_DELAY_SEC)

            if norm_url(url) in NO_LINK_SCAN_URLS:
                stats["link_scan_skipped"] += 1
                log.info("LINK SCAN skipped for hub page: %s", url)
                time.sleep(REQUEST_DELAY_SEC)
                continue

            links = collect_links(html, url)
            stats["links_found"] += len(links)

            enq = 0
            for link in links:
                if link in visited:
                    continue
                if not is_internal(link) or should_skip(link):
                    continue
                q.append(link)
                enq += 1

            stats["enqueued"] += enq
            log.info("LINKS found=%d, ENQUEUE +%d (queue_size=%d)", len(links), enq, len(q))
            time.sleep(REQUEST_DELAY_SEC)

    save_state(downloaded)

    log.info("DONE. Pages visited: %d | Entries downloaded: %d | Total entries in state: %d",
             pages_seen, downloaded_this_run, len(downloaded))
    log.info("STATS:")
    for k in sorted(stats):
        log.info("  %-28s %d", k, stats[k])


if __name__ == "__main__":
    main()
