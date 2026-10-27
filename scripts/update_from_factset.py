#!/usr/bin/env python3
"""
Weekly updater for the S&P 500 Earnings Season Tracker.

1. Finds the newest FactSet Earnings Insight PDF (factset.com/earningsinsight redirects to it;
   falls back to probing recent dates).
2. Extracts the scorecard, growth, guidance and context figures with pdftotext + regex.
3. Merges them into data/tracker_data.json (current snapshot, season path, quarterly history,
   guidance history, drift log).

Exit codes: 0 = updated or nothing new; 1 = download/parse failure (the GitHub Action then fails
and GitHub emails the repo owner).

Usage:
    python scripts/update_from_factset.py --data data/tracker_data.json [--edition 103026] [--pdf local.pdf] [--dry-run]
Requires: Python 3.9+, poppler-utils (pdftotext).
"""
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE = ("https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/"
        "Earnings%20Insight/EarningsInsight_")
LATEST = "https://www.factset.com/earningsinsight"
UA = {"User-Agent": "Mozilla/5.0 (earnings-season-tracker; weekly GitHub Action)"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
QWORD = {"first": 1, "second": 2, "third": 3, "fourth": 4}
QEND = {1: "Mar 31", 2: "Jun 30", 3: "Sep 30", 4: "Dec 31"}
MIN_COMPANIES_FOR_NEW_SEASON = 15     # don't start a new season's row off 2-3 early reporters
N_INDEX = 500


# ----------------------------------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------------------------------
def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read()


def is_pdf(b):
    return b[:4] == b"%PDF"


def fetch_edition(code=None, pdf_path=None):
    """Return (code, pdf_bytes)."""
    if pdf_path:
        b = Path(pdf_path).read_bytes()
        m = re.search(r"(\d{6}[A-Z]?)", Path(pdf_path).name)
        return (m.group(1) if m else "local"), b
    if code:
        _, b = http_get(BASE + code + ".pdf")
        if not is_pdf(b):
            raise RuntimeError(f"edition {code} did not return a PDF")
        return code, b
    try:
        final_url, b = http_get(LATEST)
        m = re.search(r"EarningsInsight_(\d{6}[A-Z]?)\.pdf", final_url)
        if is_pdf(b) and m:
            return m.group(1), b
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"latest redirect failed ({e}); probing recent dates", file=sys.stderr)
    today = dt.date.today()
    for back in range(0, 15):
        d = today - dt.timedelta(days=back)
        for suf in ("", "A", "B", "C"):
            c = d.strftime("%m%d%y") + suf
            try:
                _, b = http_get(BASE + c + ".pdf", timeout=30)
                if is_pdf(b):
                    return c, b
            except urllib.error.HTTPError:
                continue
    raise RuntimeError("no Earnings Insight edition found in the last 15 days")


def pdf_to_text(pdf_bytes):
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext not found (install poppler-utils)")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ei.pdf"
        p.write_bytes(pdf_bytes)
        out = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, check=True).stdout
    return out.decode("utf-8", "replace")


# ----------------------------------------------------------------------------------------------
# Parse
# ----------------------------------------------------------------------------------------------
def normalize(t):
    t = t.replace("’", "'").replace("–", "-").replace("—", "-").replace(" ", " ")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(\d+)\s?year average", r"\1-year average", t)          # "10year average" -> "10-year average"
    t = re.sub(r"(\d+)-year\s*\(", r"\1-year average (", t)              # 2021 style "5-year (75%) average"
    t = re.sub(r"year-over- year", "year-over-year", t)
    t = re.sub(r"(\w)- (\w)", r"\1-\2", t)
    return t


def qlabel(qn, year):
    return f"Q{qn}'{str(year)[-2:]}"


def sentence(t, start_pat, maxlen=600):
    m = re.search(start_pat, t)
    if not m:
        return None
    s = t[m.start(): m.start() + maxlen]
    end = re.search(r"(?<!\d)\.(?=\s+[A-Z])", s)
    return s[: end.start()] if end else s


def parse(text):
    t = normalize(text)
    d = {}

    # edition date: first "Month D, YYYY" on page 1
    m = re.search(r"(%s) (\d{1,2}), (20\d\d)" % "|".join(MONTHS), t[:3000])
    if not m:
        raise ValueError("edition date not found")
    d["date"] = dt.date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2))).isoformat()

    # scorecard (percent form, or count form early in a season)
    m = re.search(r"Earnings Scorecard: For Q([1-4]) (20\d\d) \(with (?:more than |over |>\s?)?(\d+)% (?:of )?"
                  r"(?:the companies in )?(?:the )?S&P 500(?: companies)? reporting actual results\), "
                  r"(\d+)% of S&P 500 compan(?:y|ies) ha(?:ve|s) reported a positive EPS surprise and "
                  r"(\d+)% of S&P 500 compan(?:y|ies) ha(?:ve|s) reported a positive revenue", t)
    if m:
        d["score"] = {"q": qlabel(int(m.group(1)), m.group(2)), "pct": int(m.group(3)),
                      "n": round(int(m.group(3)) / 100 * N_INDEX), "beat": int(m.group(4)),
                      "rev_beat": int(m.group(5)), "form": "pct"}
    else:
        m = re.search(r"Earnings Scorecard: For Q([1-4]) (20\d\d) \(with (\d+) S&P 500 compan(?:y|ies) reporting "
                      r"actual results\), (\d+) S&P 500 compan(?:y|ies) ha(?:ve|s) reported a positive EPS surprise "
                      r"and (\d+) S&P 500 compan(?:y|ies) ha(?:ve|s) reported a positive revenue", t)
        if m:
            n = int(m.group(3))
            d["score"] = {"q": qlabel(int(m.group(1)), m.group(2)), "pct": round(n / N_INDEX * 100), "n": n,
                          "beat": round(int(m.group(4)) / n * 100), "rev_beat": round(int(m.group(5)) / n * 100),
                          "form": "count"}

    m = re.search(r"Of these companies, (\d+)% have reported actual EPS above the mean EPS estimate, (\d+)% have "
                  r"reported actual EPS equal to the mean EPS estimate, and (\d+)% have reported actual EPS below", t)
    if m:
        d["split"] = [int(m.group(1)), int(m.group(2)), int(m.group(3))]

    s = sentence(t, r"The percentage of companies reporting EPS above the mean EPS estimate is")
    if s:
        for k, pat in (("beat_1y", r"1-year average \((\d+)%\)"), ("beat_5y", r"5-year average \((\d+)%\)"),
                       ("beat_10y", r"10-year average \((\d+)%\)")):
            mm = re.search(pat, s)
            if mm:
                d[k] = int(mm.group(1))

    s = sentence(t, r"In aggregate, companies are reporting earnings that are")
    if s:
        mm = re.search(r"earnings that are (-?\d+\.\d)% (above|below)", s)
        if mm:
            v = float(mm.group(1))
            d["surprise"] = -abs(v) if mm.group(2) == "below" else v
        for k, pat in (("surprise_5y", r"5-year average (?:of )?\(?\+?(-?\d+\.\d)%"),
                       ("surprise_10y", r"10-year average (?:of )?\(?\+?(-?\d+\.\d)%")):
            mm = re.search(pat, s)
            if mm:
                d[k] = float(mm.group(1))

    s = sentence(t, r"In terms of revenues, \d+% of S&P 500 companies have reported actual revenues above estimates")
    if s:
        mm = re.search(r"5-year average of (\d+)%", s)
        if mm:
            d["rev_beat_5y"] = int(mm.group(1))
        mm = re.search(r"10-year average of (\d+)%", s)
        if mm:
            d["rev_beat_10y"] = int(mm.group(1))
    s = sentence(t, r"companies are reporting revenues that are")
    if s:
        mm = re.search(r"revenues that are (-?\d+\.\d)% (above|below)", s)
        if mm:
            v = float(mm.group(1))
            d["rev_surprise"] = -abs(v) if mm.group(2) == "below" else v
        mm = re.search(r"5-year average of \+?(-?\d+\.\d)%", s)
        if mm:
            d["rev_surprise_5y"] = float(mm.group(1))

    # growth bullet (refers to the reporting quarter in-season, to the next quarter between seasons)
    m = re.search(r"Earnings (?:Growth|Decline): For Q([1-4]) (20\d\d), the (blended|estimated) (?:\(year-over-year\) )?"
                  r"earnings (?:growth rate|decline) for the S&P 500 is (-?\d+\.\d)%", t)
    if m:
        d["growth"] = {"q": qlabel(int(m.group(1)), m.group(2)), "kind": m.group(3), "v": float(m.group(4))}
    m = re.search(r"earnings (?:growth rate|decline) of (-?\d+\.\d)% at the end of the (first|second|third|fourth) "
                  r"quarter \((\w+) (\d+)\)", t)
    if m:
        d["growth_qend"] = float(m.group(1))
        d["qend_label"] = f"{m.group(3)[:3]} {int(m.group(4))}"
    m = re.search(r"blended revenue growth rate for the (?:first|second|third|fourth) quarter is (-?\d+\.\d)%", t)
    if m:
        d["rev_growth"] = float(m.group(1))

    # quarterly guidance paragraph
    m = re.search(r"At this point in time, (\d+) companies in the index have issued EPS guidance for Q([1-4]) (20\d\d)"
                  r"[.,] Of these (?:\d+ )?companies, (\d+) have issued (negative|positive) EPS guidance and (\d+) have "
                  r"issued (positive|negative) EPS guidance", t)
    if m:
        a, b = int(m.group(4)), int(m.group(6))
        pos, neg = (b, a) if m.group(5) == "negative" else (a, b)
        g = {"q": qlabel(int(m.group(2)), m.group(3)), "pos": pos, "neg": neg}
        mm = re.search(r"percentage of (?:S&P 500 )?companies issuing (negative|positive) EPS guidance for Q"
                       + m.group(2) + " " + m.group(3) +
                       r" is \d+% \(\d+ out of \d+\), which is[^.]*?5-year average of (\d+)%", t)
        if mm:
            g["pos5"] = (100 - int(mm.group(2))) if mm.group(1) == "negative" else int(mm.group(2))
        d["guidance"] = g
    else:
        m = re.search(r"Earnings Guidance: For Q([1-4]) (20\d\d), (\d+) S&P 500 compan(?:y|ies) ha(?:ve|s) issued negative "
                      r"EPS guidance and (\d+) S&P 500 compan(?:y|ies) ha(?:ve|s) issued positive EPS guidance", t)
        if m:
            d["guidance"] = {"q": qlabel(int(m.group(1)), m.group(2)), "pos": int(m.group(4)), "neg": int(m.group(3))}

    m = re.search(r"(\d+) companies in the index have issued EPS guidance for the current fiscal year[^.]*\. Of these "
                  r"\d+ companies, (\d+) have issued negative EPS guidance and (\d+) have issued positive", t)
    if m:
        d["fy_neg"], d["fy_pos"] = int(m.group(2)), int(m.group(3))

    m = re.search(r"During the upcoming week, (\d+) S&P 500 companies(?: \(including (\d+) Dow 30 components?\))? are "
                  r"scheduled to report results for the (first|second|third|fourth) quarter", t)
    if m:
        d["next_week"] = int(m.group(1))
        d["next_week_dow"] = int(m.group(2)) if m.group(2) else 0
        d["next_week_qn"] = QWORD[m.group(3)]

    m = re.search(r"The forward 12-month P/E ratio for the S&P 500 is (\d+\.\d)", t)
    if m:
        d["fwd_pe"] = float(m.group(1))

    m = re.search(r"The ([A-Z][A-Za-z ]+?) \(\+?(-?\d+\.\d)%\) sector (?:is reporting|reported) the largest positive "
                  r"\(aggregate\) difference between actual earnings", t)
    if m:
        d["sector_top"] = [m.group(1).strip(), float(m.group(2))]
    m = re.search(r"the ([A-Z][A-Za-z ]+?) \((-\d+\.\d)%\) sector (?:is reporting|reported) the largest negative "
                  r"\(aggregate\) difference between actual (?:earnings|and estimated earnings)", t)
    if m:
        d["sector_bottom"] = [m.group(1).strip(), float(m.group(2))]
    return d


# ----------------------------------------------------------------------------------------------
# Merge into state
# ----------------------------------------------------------------------------------------------
def qkey(q):
    return (2000 + int(q[-2:]), int(q[1]))


def long_q(q):
    return f"Q{q[1]} 20{q[-2:]}"


def fmt_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def short_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def read_label(score, iso):
    rep = f"{score['n']} companies" if score["form"] == "count" else f"{score['pct']}% reported"
    return f"{rep} · {fmt_date(iso)}"


def finalize_drift(state, q):
    """When a quarter closes, log its quarter-way reading against where it finished."""
    rs = [r for r in state.get("readings", []) if r["q"] == q]
    if len(rs) < 2:
        return
    early = [r for r in rs if 15 <= r["pct"] <= 40]
    if not early:
        return
    e = min(early, key=lambda r: abs(r["pct"] - 25))
    f = rs[-1]
    state["drift"] = [x for x in state["drift"] if x["q"] != q] + [{"q": q, "early": e["beat"], "at": e["pct"], "final": f["beat"]}]
    state["drift"] = sorted(state["drift"], key=lambda x: qkey(x["q"]))[-6:]


def merge(state, snap, code):
    log = []
    meta = state["meta"]
    if code in meta["editions_seen"]:
        return False, [f"edition {code} already processed"]
    hist, guid, cur = state["history"], state["guidance"], state["current"]
    live = hist[-1]
    sc = snap.get("score")

    # ---- scorecard / quarterly history ----
    if sc:
        sq = sc["q"]
        enough = sc["form"] == "pct" or sc["n"] >= MIN_COMPANIES_FOR_NEW_SEASON
        if qkey(sq) > qkey(live["q"]) and enough:
            live["flag"] = "" if live.get("last_pct", 100) >= 90 else "interim"
            finalize_drift(state, live["q"])
            row = {"q": sq, "beat": sc["beat"], "avg5": snap.get("beat_5y", live["avg5"]), "growth": None,
                   "read": "", "src": code, "flag": "live"}
            hist.append(row)
            qn = int(sq[1])
            state["season_weeks"] = [{"date": snap.get("qend_label", QEND[qn]), "note": "", "reported": "0%",
                                      "eps": None, "rev": None, "surp": None, "gpos": None, "gneg": None,
                                      "growth": snap.get("growth_qend"), "src": None}]
            log.append(f"new season {sq}; {live['q']} closed at {live['beat']}%")
            live = row
        if qkey(sq) == qkey(live["q"]) and (enough or live.get("beat") is not None):
            sw = state.get("season_weeks") or []
            if sw and sw[0].get("src") is None and "growth_qend" in snap:
                sw[0]["growth"] = snap["growth_qend"]
                sw[0]["date"] = snap.get("qend_label", sw[0]["date"])
            live.update({"beat": sc["beat"], "read": read_label(sc, snap["date"]), "src": code, "last_pct": sc["pct"]})
            if "beat_5y" in snap:
                live["avg5"] = snap["beat_5y"]
            g = snap.get("growth")
            if g and g["q"] == sq:
                live["growth"] = g["v"]
            if live["growth"] is None and g:
                live["growth"] = g["v"]
            state.setdefault("readings", []).append({"q": sq, "date": snap["date"], "pct": sc["pct"], "beat": sc["beat"]})
            # season path row
            ng = snap.get("guidance")
            week = {"date": short_date(snap["date"]), "note": "",
                    "reported": f"{sc['n']} cos" if sc["form"] == "count" else f"{sc['pct']}%",
                    "eps": sc["beat"], "rev": sc["rev_beat"], "surp": snap.get("surprise"),
                    "gpos": ng["pos"] if ng and qkey(ng["q"]) > qkey(sq) else None,
                    "gneg": ng["neg"] if ng and qkey(ng["q"]) > qkey(sq) else None,
                    "growth": g["v"] if g and g["q"] == sq else live["growth"], "src": code}
            state["season_weeks"].append(week)
            log.append(f"{sq}: {sc['pct']}% reported, {sc['beat']}% beat")
            # current snapshot (only when the scorecard is for the live quarter)
            cur.update({
                "quarter": long_q(sq), "edition": fmt_date(snap["date"]), "edition_code": code,
                "edition_url": BASE + code + ".pdf", "pct_reported": sc["pct"], "n_index": N_INDEX,
                "n_reported": sc["n"], "eps_beat": sc["beat"], "rev_beat": sc["rev_beat"],
            })
            if "split" in snap:
                cur["eps_beat"], cur["eps_inline"], cur["eps_miss"] = snap["split"]
            for k_snap, k_cur in (("beat_1y", "eps_beat_1y"), ("beat_5y", "eps_beat_5y"), ("beat_10y", "eps_beat_10y"),
                                  ("surprise", "eps_surprise"), ("surprise_5y", "eps_surprise_5y"),
                                  ("surprise_10y", "eps_surprise_10y"), ("rev_beat_5y", "rev_beat_5y"),
                                  ("rev_beat_10y", "rev_beat_10y"), ("rev_surprise", "rev_surprise"),
                                  ("rev_surprise_5y", "rev_surprise_5y"), ("rev_growth", "rev_growth"),
                                  ("growth_qend", "eps_growth_qend"), ("qend_label", "qend_label"),
                                  ("sector_top", "sector_top"), ("sector_bottom", "sector_bottom")):
                if k_snap in snap:
                    cur[k_cur] = snap[k_snap]
            if g and g["q"] == sq:
                cur["eps_growth"] = g["v"]
        elif qkey(sq) < qkey(live["q"]):
            log.append(f"scorecard {sq} older than live {live['q']}; ignored")
        else:
            log.append(f"{sq} has only {sc['n']} reporters; waiting for {MIN_COMPANIES_FOR_NEW_SEASON}")

    # ---- context fields that apply regardless of season ----
    for k in ("fy_pos", "fy_neg", "fwd_pe"):
        if k in snap:
            cur[k] = snap[k]
    if "next_week" in snap:
        cur["next_week"], cur["next_week_dow"] = snap["next_week"], snap["next_week_dow"]
        cur["next_week_q"] = snap["next_week_qn"]
    else:
        cur["next_week"], cur["next_week_dow"] = 0, 0

    # ---- quarterly guidance history ----
    g = snap.get("guidance")
    if g:
        row = next((r for r in guid if r["q"] == g["q"]), None)
        if row is None:
            if guid and qkey(g["q"]) < qkey(guid[-1]["q"]):
                log.append(f"guidance for {g['q']} older than latest row; ignored")
            else:
                row = {"q": g["q"], "pos5": None}
                guid.append(row)
        if row is not None and qkey(g["q"]) >= qkey(guid[-1]["q"]):
            row.update({"pos": g["pos"], "neg": g["neg"], "src": code})
            if "pos5" in g:
                row["pos5"] = g["pos5"]
            for r in guid:
                r.pop("live", None)
            row["live"] = True
            log.append(f"guidance {g['q']}: {g['pos']} positive / {g['neg']} negative")
        cur["g_next_q"] = long_q(guid[-1]["q"])

    meta["editions_seen"] = (meta["editions_seen"] + [code])[-200:]
    meta["last_edition_code"], meta["last_edition_date"] = code, snap["date"]
    meta["updated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return True, log


def validate(snap):
    problems = []
    if "score" not in snap and "guidance" not in snap:
        problems.append("neither a scorecard nor a guidance count could be parsed")
    sc = snap.get("score")
    if sc and not (0 <= sc["pct"] <= 100 and 0 <= sc["beat"] <= 100):
        problems.append(f"scorecard out of range: {sc}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/tracker_data.json")
    ap.add_argument("--edition", default="", help="edition code such as 103026 or 103026A (default: latest)")
    ap.add_argument("--pdf", default="", help="parse a local PDF instead of downloading")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.edition and not re.fullmatch(r"\d{6}[A-Z]?", a.edition):
        sys.exit("edition must look like 103026 or 103026A")
    try:
        code, pdf = fetch_edition(a.edition or None, a.pdf or None)
        snap = parse(pdf_to_text(pdf))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    problems = validate(snap)
    if problems:
        print("ERROR: " + "; ".join(problems), file=sys.stderr)
        sys.exit(1)
    print(f"edition {code} ({snap['date']})")
    path = Path(a.data)
    state = json.loads(path.read_text())
    changed, log = merge(state, snap, code)
    for line in log:
        print("  " + line)
    if changed and not a.dry_run:
        path.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {path}")
    elif not changed:
        print("nothing new")


if __name__ == "__main__":
    main()
