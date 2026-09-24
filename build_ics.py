"""EDEN Division カレンダーから NO LiMIT の予定を取り出して docs/nolimit.ics を作る。"""
import datetime as dt
import html
import json
import re
import urllib.request
from pathlib import Path

BASE = "https://edendivision.dothome.co.kr/"
GROUP = "NO LiMIT"
MONTHS_BACK, MONTHS_AHEAD = 1, 6
KST = dt.timezone(dt.timedelta(hours=9))
ROOT = Path(__file__).parent
ARCHIVE = ROOT / "data" / "events.json"

DAY_RE = re.compile(r'<section class="feed-day">(.*?)</section>', re.S)
H3_RE = re.compile(r"<h3>(\d+)/(\d+)</h3>")
CARD_RE = re.compile(r'<a\s[^>]*class="feed-card[^"]*"[^>]*href="([^"]+)"[^>]*data-event-id="(\d+)"[^>]*>(.*?)</a>', re.S)
TAG_RE = lambda t: re.compile(rf"<{t}>(.*?)</{t}>", re.S)


def text(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def fetch(year, month):
    url = f"{BASE}?year={year}&month={month}&groups=no-limit"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (nolimit-ics)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def parse(page, year, month):
    for day in DAY_RE.findall(page):
        m = H3_RE.search(day)
        if not m:
            continue
        mm, dd = int(m.group(1)), int(m.group(2))
        y = year + (1 if month == 12 and mm == 1 else -1 if month == 1 and mm == 12 else 0)
        for href, eid, body in CARD_RE.findall(day):
            strong = TAG_RE("strong").findall(body)
            span = TAG_RE("span").findall(body)
            small = [text(s) for s in TAG_RE("small").findall(body)]
            groups = small[-1] if small else ""
            if GROUP not in groups:
                continue
            yield {
                "id": eid,
                "date": dt.date(y, mm, dd).isoformat(),
                "title": text(strong[0]) if strong else "",
                "time": text(span[0]) if span else "",
                "venue": small[0] if len(small) > 1 else "",
                "groups": groups,
                "url": html.unescape(href),
            }


def esc(s):
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\;").replace("\n", "\\n")


def fold(line):
    # iCalendar は1行75オクテットまで
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > 74:
            out.append(cur.decode())
            cur = b" "
        cur += b
    out.append(cur.decode())
    return "\r\n".join(out)


def to_ics(events):
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nolimit-calendar//EN", "CALSCALE:GREGORIAN",
         "METHOD:PUBLISH", "X-WR-CALNAME:NO LiMIT", "X-WR-TIMEZONE:Asia/Seoul",
         "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
         "BEGIN:VTIMEZONE", "TZID:Asia/Seoul", "BEGIN:STANDARD", "DTSTART:19700101T000000",
         "TZOFFSETFROM:+0900", "TZOFFSETTO:+0900", "TZNAME:KST", "END:STANDARD", "END:VTIMEZONE"]
    for e in events:
        title = e["title"]
        if title == "[공개 예정]":
            title = f"[공개 예정] {e['groups']}"
        L += ["BEGIN:VEVENT", f"UID:eden-{e['id']}@edendivision", f"DTSTAMP:{e['stamp']}"]
        if re.fullmatch(r"\d{1,2}:\d{2}", e["time"]):
            h, mi = map(int, e["time"].split(":"))
            s = dt.datetime.combine(dt.date.fromisoformat(e["date"]), dt.time(h, mi))
            en = s + dt.timedelta(hours=2)
            L += [f"DTSTART;TZID=Asia/Seoul:{s:%Y%m%dT%H%M%S}", f"DTEND;TZID=Asia/Seoul:{en:%Y%m%dT%H%M%S}"]
        else:
            d = dt.date.fromisoformat(e["date"])
            L += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}"]
        L.append(f"SUMMARY:{esc(title)}")
        if e["venue"]:
            L.append(f"LOCATION:{esc(e['venue'])}")
        L += [f"DESCRIPTION:{esc('出演: ' + e['groups'] + chr(10) + e['url'])}", f"URL:{e['url']}", "END:VEVENT"]
    L.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in L) + "\r\n"


def main():
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    today = dt.datetime.now(KST).date()
    months = []
    for off in range(-MONTHS_BACK, MONTHS_AHEAD + 1):
        y, m = divmod(today.year * 12 + today.month - 1 + off, 12)
        months.append((y, m + 1))
    scraped = {}
    for y, m in months:
        for e in parse(fetch(y, m), y, m):
            scraped[e["id"]] = e
    if not scraped:
        raise SystemExit("予定が1件も取れなかった（サイトの構造が変わった可能性）")

    archive = json.loads(ARCHIVE.read_text(encoding="utf-8")) if ARCHIVE.exists() else {}
    window_start = dt.date(*months[0], 1).isoformat()
    # 読み込み範囲より前の予定はサイトから取らないので、保存済みのものを残す。
    # 範囲内でサイトから消えた予定は中止とみなして消す。
    events = {k: e for k, e in archive.items() if e["date"] < window_start}
    for k, e in scraped.items():
        old = archive.get(k)
        same = old is not None and {f: v for f, v in old.items() if f != "stamp"} == e
        e["stamp"] = old["stamp"] if same else now
        events[k] = e

    evs = sorted(events.values(), key=lambda e: (e["date"], e["time"]))
    ARCHIVE.parent.mkdir(exist_ok=True)
    ARCHIVE.write_text(json.dumps({e["id"]: e for e in evs}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out = ROOT / "docs" / "nolimit.ics"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(to_ics(evs).encode("utf-8"))
    print(f"{len(evs)} events ({len(scraped)} from site) -> {out}")


if __name__ == "__main__":
    main()
