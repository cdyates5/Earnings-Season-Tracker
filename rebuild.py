#!/usr/bin/env python3
"""
S&P 500 Earnings Season Tracker — page builder.

    python rebuild.py [output_dir]          (default: docs)

Reads data/tracker_data.json (kept current by scripts/update_from_factset.py) and writes:
    <output_dir>/index.html                            standalone page (GitHub Pages serves this)
    <output_dir>/earnings_season_tracker.artifact.html same page without the document wrapper

Source: FactSet "Earnings Insight" (John Butters), weekly PDF editions at
advantage.factset.com/hubfs/Website/Resources Section/Research Desk/Earnings Insight/EarningsInsight_MMDDYY.pdf
Python standard library only.
"""
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "tracker_data.json"
FS = "https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/"


def qkey(q):
    """"Q3'21" -> (2021, 3)"""
    return (2000 + int(q[-2:]), int(q[1]))


def qshift(q, n):
    y, k = qkey(q)
    i = y * 4 + (k - 1) + n
    return f"Q{i % 4 + 1}'{(i // 4) % 100:02d}"


def short_q(longq):
    """"Q4 2026" -> "Q4'26\""""
    return f"{longq[:2]}'{longq[-2:]}"


def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else float("nan")


def compute_ttm(D):
    """TTM y/y EPS growth.
    ttm     : FactSet's quarterly y/y rates for the last four quarters, weighted by the year-ago quarters'
              bottom-up EPS (= growth in the four-quarter sum, on FactSet's like-for-like basis).
    ttm_lvl : plain ratio of summed bottom-up EPS levels (cross-check; only where all levels are printed).
    Year-ago levels FactSet only prints in chart images are imputed as L(q-4) * (1 + g(q))."""
    hist, levels = D["history"], dict(D["eps_levels"])
    g = {h["q"]: h["growth"] for h in hist if h.get("growth") is not None}
    g.update({k: v[0] for k, v in D["pre_growth"].items()})
    imputed = []

    def level(q):
        if q in levels:
            return levels[q]
        prev = qshift(q, -4)
        if q in g and (prev in levels or qkey(prev) >= qkey(min(levels, key=qkey))):
            levels[q] = round(level(prev) * (1 + g[q] / 100), 2)
            imputed.append(q)
            return levels[q]
        raise KeyError(q)

    for h in hist:
        q = h["q"]
        cur = [qshift(q, -i) for i in range(4)]
        if any(c not in g for c in cur):
            h["ttm"] = h["ttm_lvl"] = None
            continue
        ago = [qshift(c, -4) for c in cur]
        w = [level(a) for a in ago]
        num = sum(wi * (1 + g[c] / 100) for wi, c in zip(w, cur))
        h["ttm"] = round((num / sum(w) - 1) * 100, 1)
        printed = all(c in D["eps_levels"] for c in cur + ago) and h.get("flag") != "live"
        h["ttm_lvl"] = round((sum(D["eps_levels"][c] for c in cur) / sum(w) - 1) * 100, 1) if printed else None
    return sorted(set(imputed), key=qkey)


def pts(v):
    return f"{abs(v)} pt" + ("" if abs(v) == 1 else "s")


def since_phrase(series, idx, higher=True):
    """Most recent earlier point at least as high (or as low) as series[idx]."""
    v = series[idx][1]
    for q, x in reversed(series[:idx]):
        if (x >= v) if higher else (x <= v):
            return q
    return None


def build_context(D):
    c = dict(D["current"])
    hist, guid = D["history"], D["guidance"]
    for gq in guid:
        gq["pct"] = round(gq["pos"] / (gq["pos"] + gq["neg"]) * 100) if (gq["pos"] + gq["neg"]) else 0
    imputed = compute_ttm(D)
    live = hist[-1]
    is_live = live.get("flag") == "live"
    done = [h for h in hist if h.get("flag") != "live"]
    sq_long = c["quarter"]
    sq = short_q(sq_long)
    pct = c["pct_reported"]
    ctx = dict(c)
    ctx["fs"] = FS
    ctx["built"] = D["meta"].get("updated_utc") or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ---- masthead / KPI 1 ----
    status = "in progress" if pct < 98 else "complete"
    ctx["season_status"] = f"S&amp;P 500 · {sq_long} earnings season · {status}"
    n_rep = c.get("n_reported") or round(pct / 100 * c["n_index"])
    ctx["n_reported"] = n_rep
    nxt = c.get("next_week") or 0
    if nxt and c.get("next_week_q") == int(sq[1]) and pct < 98:
        after = min(100, round((n_rep + nxt) / c["n_index"] * 100))
        dow = f" ({c['next_week_dow']} Dow 30)" if c.get("next_week_dow") else ""
        ctx["kpi1_ctx"] = (f"<b>{nxt}</b> companies{dow} report this week. If all do, the season is "
                           f"≈{after}% complete by the next edition.")
    else:
        after = pct
        ctx["kpi1_ctx"] = "The season is effectively complete; the next season's scorecard starts with the big banks."
    ctx["pct_after_next"] = after

    # ---- KPI 2 ----
    gap = c["eps_beat"] - c["eps_beat_5y"]
    ctx["beat_gap_5y"] = gap
    cls = "up" if gap > 0 else ("dn" if gap < 0 else "nt")
    ctx["beat_pill"] = f'<span class="pill {cls}">{"+" if gap > 0 else ("−" if gap < 0 else "±")}{abs(gap)} pts vs 5-yr</span>'

    # ---- KPI 3: next-quarter guidance ----
    gl = guid[-1]
    g_tot = gl["pos"] + gl["neg"]
    ctx["g_next_q"] = f"Q{gl['q'][1]} 20{gl['q'][-2:]}"
    ctx["g_next_short"] = gl["q"]
    ctx.update({"g_pos": gl["pos"], "g_neg": gl["neg"], "g_total": g_tot, "g_pos_pct": gl["pct"],
                "g_pos_5y": gl.get("pos5") if gl.get("pos5") is not None else 40})
    fy_tot = c.get("fy_pos", 0) + c.get("fy_neg", 0)
    ctx["fy_total"] = fy_tot
    ctx["fy_pos_pct"] = round(c["fy_pos"] / fy_tot * 100) if fy_tot else 0
    k3 = f"FY guidance: <b>{c.get('fy_pos', 0)} of {fy_tot}</b> ({ctx['fy_pos_pct']}%) positive."
    gcur = next((x for x in guid if x["q"] == sq), None)
    if gcur and gcur is not gl:
        k3 += (f" Before the season, <b>{gcur['pos']} of {gcur['pos'] + gcur['neg']}</b> ({gcur['pct']}%) guided "
               f"{sq_long} above consensus vs a {gcur.get('pos5') or '≈40'}% norm.")
    ctx["kpi3_ctx"] = k3

    # ---- strip ----
    ctx["rev_cls"] = "dn" if c["rev_beat"] < c["rev_beat_5y"] else ("up" if c["rev_beat"] > c["rev_beat_5y"] else "")
    s, s5 = c["eps_surprise"], c["eps_surprise_5y"]
    ctx["surp_cls"] = "dn" if s < s5 else "up"
    sign = lambda v: f"{v:+.1f}%"
    ctx["eps_surprise_fmt"], ctx["eps_surprise_5y_fmt"] = sign(s), sign(s5)
    ctx["eps_surprise_10y_fmt"] = sign(c.get("eps_surprise_10y", s5))
    ctx["ttm_live"] = live["ttm"]
    ctx["growth_cell_note"] = (f"TTM {live['ttm']}% · quarter was {c.get('eps_growth_qend', '—')}% at "
                               f"{c.get('qend_label', 'quarter end')}")
    ctx["live_growth_legend"] = f"{sq_long} blended, in progress" if is_live else "Latest quarter"

    # ---- beat chart note ----
    above = [h for h in done if h["beat"] > h["avg5"]]
    best = max(done, key=lambda h: h["beat"])
    worst = min(done, key=lambda h: h["beat"])
    ctx.update({"n_done": len(done), "n_above": len(above), "best_q": best["q"], "best_v": best["beat"],
                "worst_q": worst["q"], "worst_v": worst["beat"], "first_q": hist[0]["q"]})

    # ---- growth note ----
    series = [(h["q"], h["ttm"]) for h in hist if h["ttm"] is not None]
    li = len(series) - 1
    prev_q, prev_ttm = series[li - 1]
    rising = live["ttm"] >= prev_ttm
    sp = since_phrase(series, li, higher=rising)
    since_txt = (f"the {'highest' if rising else 'lowest'} since {sp}" if sp
                 else f"the {'highest' if rising else 'lowest'} in this series")
    trough = min(done, key=lambda h: h["ttm"] if h["ttm"] is not None else 1e9)
    negs = [h["q"] for h in done if h["growth"] is not None and h["growth"] < 0]
    window = [qshift(live["q"], -i) for i in range(4)]
    notes = [f"{h['q']}: {h['gnote']}" for h in hist if h.get("gnote") and h["q"] in window]
    gn = (f"TTM growth is {live['ttm']}%{' including the blended ' + sq_long + ' figure' if is_live else ''}, "
          f"{'up' if rising else 'down'} from {prev_ttm}% through {prev_q} and {since_txt}. "
          f"The trough was {trough['ttm']}% ({trough['q']})")
    gn += f"; quarterly earnings fell y/y in {', '.join(negs)}." if negs else "."
    if notes:
        gn += " One-offs inside the TTM window: " + "; ".join(notes) + "."
    ctx["growth_note"] = html.escape(gn, quote=False)

    # ---- guidance note ----
    gdone = [x for x in guid if not x.get("live")]
    gmap = {x["q"]: x["pct"] for x in guid}
    pairs = [(gmap[h["q"]], h["beat"]) for h in done if h["q"] in gmap]
    r = corr([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) > 3 else float("nan")
    with_avg = [x for x in gdone if x.get("pos5") is not None]
    g_above = [x for x in with_avg if x["pct"] > x["pos5"]]
    streak = 0
    for x in reversed(with_avg):
        if x["pct"] > x["pos5"]:
            streak += 1
        else:
            break
    gseries = [(x["q"], x["pct"]) for x in gdone]
    last_q, last_pct = gseries[-1]
    lhigh = last_pct >= (with_avg[-1]["pos5"] if with_avg else 40)
    gsp = since_phrase(gseries, len(gseries) - 1, higher=lhigh)
    ctx["guide_note"] = (f"{len(g_above)} of {len(with_avg)} completed quarters ran above the 5-yr average"
                         + (f", the last {streak} in a row" if streak > 1 else "") + ". "
                         + f"{last_q}'s {last_pct}% was the {'highest' if lhigh else 'lowest'} "
                         + (f"since {gsp}" if gsp else "in this series") + ". "
                         + f"Across {len(pairs)} quarters, the pre-season positive share and that quarter's beat "
                           f"rate correlate at r = {r:.2f}.")

    # ---- findings ----
    F = []
    s10 = c.get("eps_surprise_10y", s5)
    if gap > 0 and s >= s5:
        t1, k1 = "Beats are broad and large", "g"
    elif gap > 0:
        t1, k1 = "Breadth above average, magnitude below", "n"
    elif s >= s5:
        t1, k1 = "Fewer beats, but bigger ones", "n"
    else:
        t1, k1 = "Beats are narrower and smaller than usual", "w"
    txt = (f"<b>{c['eps_beat']}%</b> of reporters beat, {pts(gap)} {'above' if gap >= 0 else 'below'} the 5-yr "
           f"average ({c['eps_beat_5y']}%). In aggregate they beat by <b>{sign(s)}</b> against a {sign(s5)} norm "
           f"({sign(s10)} over 10 years).")
    st, sb = c.get("sector_top"), c.get("sector_bottom")
    if st and sb:
        txt += f" Sector extremes: {st[0]} {sign(st[1])}, {sb[0]} {sign(sb[1])}."
    elif st:
        txt += f" Largest sector surprise: {st[0]} {sign(st[1])}."
    F.append((k1, t1, txt))

    rb, rb5 = c["rev_beat"], c["rev_beat_5y"]
    F.append(("w" if rb < rb5 else "g",
              "The top line is the soft spot" if rb < rb5 else "Revenue beats are running above average",
              f"Revenue beats run at <b>{rb}%</b> vs {rb5}% (5-yr) and {c.get('rev_beat_10y', '—')}% (10-yr), with a "
              f"{sign(c.get('rev_surprise', 0))} aggregate surprise vs {sign(c.get('rev_surprise_5y', 0))}."))

    pos5 = ctx["g_pos_5y"]
    gt = ("Guidance leans positive" if gl["pct"] > pos5 else "Guidance leans negative") + \
         (", on a small sample" if g_tot < 40 else "")
    gtxt = (f"{gl['pos']} of {g_tot} companies guiding {ctx['g_next_q']} came in above consensus "
            f"({gl['pct']}% vs ≈{pos5}% norm).")
    if gcur and gcur is not gl:
        gtxt += f" The pre-season {sq_long} read: {gcur['pos']} of {gcur['pos'] + gcur['neg']} ({gcur['pct']}%)."
    gtxt += f" Annual guidance is {ctx['fy_pos_pct']}% positive."
    F.append(("g" if gl["pct"] > pos5 else "w", gt, gtxt))

    F.append(("g" if rising else "w",
              "Growth is re-accelerating" if rising else "Growth is slowing",
              f"{'Blended ' if is_live else ''}{sq_long} growth is <b>{live['growth']}%</b> and TTM growth "
              f"<b>{live['ttm']}%</b>, {since_txt}."
              + (" " + html.escape("; ".join(notes), quote=False) + "." if notes else "")))

    since22 = [h for h in done if int(h["q"][-2:]) >= 22]
    q4 = [h for h in since22 if h["q"].startswith("Q4")]
    nq4 = [h for h in since22 if not h["q"].startswith("Q4")]
    if q4 and nq4:
        q4avg = round(sum(h["beat"] for h in q4) / len(q4))
        nq4avg = round(sum(h["beat"] for h in nq4) / len(nq4))
        below = sum(1 for h in q4 if h["beat"] < h["avg5"])
        ctx.update({"q4_avg": q4avg, "nonq4_avg": nq4avg})
        F.append(("w" if below >= len(q4) / 2 else "n",
                  "Q4 is the seasonal weak spot" if q4avg < nq4avg else "No Q4 weakness lately",
                  f"Since 2022, Q4 seasons have printed <b>{', '.join(str(h['beat']) + '%' for h in q4)}</b>, below the "
                  f"5-yr average in {below} of {len(q4)} years and averaging <b>{q4avg}%</b> against {nq4avg}% for "
                  f"other quarters." + (" This is a Q4 season." if sq[1] == "4" else "")))
    else:
        ctx.update({"q4_avg": "—", "nonq4_avg": "—"})

    dr = D["drift"][-4:]
    if pct < 90 and dr:
        moves = [abs(d["final"] - d["early"]) for d in dr]
        F.append(("", f"Treat {pct}% reported as provisional",
                  f"Recent seasons moved {min(moves)} to {max(moves)} pts between a quarter-way read and the close "
                  f"(table at left). The current print sits {pts(gap)} {'over' if gap >= 0 else 'under'} the average, "
                  + ("wider than any drift in that sample." if abs(gap) > max(moves) else "within the range drift has covered.")))
    else:
        F.append(("", f"Season is {pct}% complete",
                  "Too few companies remain to move the beat rate by more than a point or so."))
    ctx["findings_html"] = "\n".join(
        f'        <li class="{k}"><div><h3>{t}</h3>\n          <p>{p}</p></div></li>' for k, t, p in F)

    ctx["eps_imputed_note"] = (f" · imputed from growth: {', '.join(imputed)}" if imputed else "")
    diffs = [abs(h["ttm"] - h["ttm_lvl"]) for h in done if h.get("ttm_lvl") is not None and h["ttm"] is not None]
    ctx["lvl_diff_mean"] = round(sum(diffs) / len(diffs), 1) if diffs else "—"
    ctx["lvl_diff_max"] = round(max(diffs), 1) if diffs else "—"
    stats = {"q4_avg": ctx["q4_avg"], "nonq4_avg": ctx["nonq4_avg"]}
    return ctx, stats


TEMPLATE = r"""<title>S&amp;P 500 Earnings Season Tracker</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap">
<style>
/* Layout: masthead + three headline gauges, a thin context strip, the long-run beat-rate chart,
   then season path / findings side by side, history ledger and source notes. Acheron house style. */
:root{
  --paper:#F7F1E6; --card:#FDFAF3; --ink:#221C14; --ink-2:#5E5446; --ink-3:#8C8170;
  --rule:#E3D8C6; --track:#ECE3D3;
  --orange:#D2622A; --teal:#0E756C; --ochre:#A67A22; --plum:#7A4A66; --slate:#4E6577;
  --f-display:"Space Grotesk", "Helvetica Neue", Arial, sans-serif;
  --f-body:"IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --f-mono:"IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#16130F; --card:#1F1B15; --ink:#EEE6D8; --ink-2:#B3A895; --ink-3:#857B6B;
    --rule:#352E25; --track:#2B251E;
    --orange:#E57C47; --teal:#3BAA98; --ochre:#CFA24A; --plum:#B18AA6; --slate:#91A5BA;
    color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --paper:#16130F; --card:#1F1B15; --ink:#EEE6D8; --ink-2:#B3A895; --ink-3:#857B6B;
  --rule:#352E25; --track:#2B251E;
  --orange:#E57C47; --teal:#3BAA98; --ochre:#CFA24A; --plum:#B18AA6; --slate:#91A5BA;
  color-scheme:dark;
}
*{box-sizing:border-box}
body{background:var(--paper); color:var(--ink); font-family:var(--f-body); font-size:15px; line-height:1.5;
  padding-left:max(16px, env(safe-area-inset-left, 0px)); padding-right:max(16px, env(safe-area-inset-right, 0px));}
.wrap{max-width:1180px; margin:0 auto; padding-block:28px 48px; display:flex; flex-direction:column; gap:22px}
a{color:var(--teal)}
a:focus-visible, button:focus-visible{outline:2px solid var(--orange); outline-offset:2px}
.mono{font-family:var(--f-mono); font-variant-numeric:tabular-nums}
.eyebrow{font-family:var(--f-mono); font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-3)}

/* masthead */
.mast{display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:12px 24px;
  border-bottom:2px solid var(--ink); padding-bottom:14px}
.mast h1{font-family:var(--f-display); font-weight:700; font-size:clamp(26px, 4vw, 38px); line-height:1.05;
  letter-spacing:-.02em; margin:4px 0 0; text-wrap:balance}
.mast h1 span{color:var(--orange)}
.mast .meta{font-family:var(--f-mono); font-size:12px; color:var(--ink-2); text-align:right; line-height:1.6}
.mast .meta b{color:var(--ink); font-weight:600}

/* headline gauges */
.kpis{display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:16px}
.kpi{background:var(--card); border:1px solid var(--rule); border-radius:6px; padding:18px 18px 16px;
  display:flex; flex-direction:column; gap:10px; min-width:0}
.kpi .lab{font-family:var(--f-display); font-weight:600; font-size:14px; letter-spacing:.01em}
.kpi .big{display:flex; align-items:baseline; gap:10px; flex-wrap:wrap}
.kpi .big .v{font-family:var(--f-mono); font-weight:600; font-size:46px; line-height:1; letter-spacing:-.02em; font-variant-numeric:tabular-nums}
.kpi .big .sub{font-family:var(--f-mono); font-size:13px; color:var(--ink-2)}
.pill{display:inline-flex; align-items:center; gap:5px; font-family:var(--f-mono); font-size:11.5px; font-weight:600;
  padding:3px 8px; border-radius:999px; white-space:nowrap}
.pill.up{color:var(--teal); background:color-mix(in srgb, var(--teal) 14%, transparent)}
.pill.dn{color:var(--orange); background:color-mix(in srgb, var(--orange) 14%, transparent)}
.pill.nt{color:var(--slate); background:color-mix(in srgb, var(--slate) 14%, transparent)}
.kpi .ctx{font-size:13px; color:var(--ink-2); line-height:1.45}
.kpi .ctx b{color:var(--ink); font-weight:600}

/* gauge bars */
.gauge{position:relative; height:12px; background:var(--track); border-radius:2px}
.gauge .fill{position:absolute; left:0; top:0; bottom:0; border-radius:2px}
.gauge .ghost{position:absolute; top:0; bottom:0; border-radius:0 2px 2px 0;
  background:repeating-linear-gradient(135deg, color-mix(in srgb, var(--ink-3) 45%, transparent) 0 3px, transparent 3px 7px)}
.gauge .mk{position:absolute; top:-5px; bottom:-5px; width:2px; background:var(--ink)}
.scale{position:relative; height:16px; font-family:var(--f-mono); font-size:10.5px; color:var(--ink-3)}
.scale span{position:absolute; transform:translateX(-50%); white-space:nowrap}
.scale span:first-child{transform:none}
.scale span:last-child{transform:translateX(-100%)}
.split{display:flex; height:10px; border-radius:2px; overflow:hidden; gap:2px}
.split div{height:100%}
.keyrow{display:flex; flex-wrap:wrap; gap:4px 14px; font-family:var(--f-mono); font-size:11.5px; color:var(--ink-2)}
.keyrow i{display:inline-block; width:9px; height:9px; border-radius:1px; margin-right:5px; vertical-align:-1px}

/* context strip */
.strip{display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); border-top:1px solid var(--rule); border-bottom:1px solid var(--rule)}
.strip > div{padding:12px 14px; display:flex; flex-direction:column; gap:2px; min-width:0}
.strip > div + div{border-left:1px solid var(--rule)}
.strip .k{font-family:var(--f-mono); font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-3)}
.strip .v{font-family:var(--f-mono); font-size:22px; font-weight:600; font-variant-numeric:tabular-nums}
.strip .c{font-size:12.5px; color:var(--ink-2)}
.strip .v.dn{color:var(--orange)} .strip .v.up{color:var(--teal)}

/* panels */
.panel{background:var(--card); border:1px solid var(--rule); border-radius:6px; padding:20px; min-width:0}
.panel h2{font-family:var(--f-display); font-weight:600; font-size:19px; letter-spacing:-.01em; margin:0; text-wrap:balance}
.panel .dek{font-size:13.5px; color:var(--ink-2); margin:4px 0 0; max-width:72ch}
.phead{display:flex; flex-wrap:wrap; justify-content:space-between; align-items:flex-end; gap:12px; margin-bottom:14px}
.legend{display:flex; flex-wrap:wrap; gap:6px 16px; font-family:var(--f-mono); font-size:11.5px; color:var(--ink-2); align-items:center}
.legend .sw{display:inline-block; width:18px; height:0; border-top:2px solid; vertical-align:middle; margin-right:6px}
.legend .dot{display:inline-block; width:9px; height:9px; border-radius:50%; vertical-align:-1px; margin-right:6px}
.seg{display:inline-flex; border:1px solid var(--rule); border-radius:4px; overflow:hidden}
.seg button{font:500 12px var(--f-mono); color:var(--ink-2); background:transparent; border:0; padding:6px 11px; cursor:pointer}
.seg button + button{border-left:1px solid var(--rule)}
.seg button[aria-pressed="true"]{background:var(--ink); color:var(--paper)}
.chartbox{position:relative; height:360px}
.chartnote{margin-top:10px; font-size:12.5px; color:var(--ink-3)}

.two{display:grid; grid-template-columns:minmax(0,1.2fr) minmax(0,1fr); gap:16px}
.compact th, .compact td{padding:7px 7px}
.tscroll{overflow-x:auto; -webkit-overflow-scrolling:touch}
table{border-collapse:collapse; width:100%; font-size:13px}
th{font-family:var(--f-mono); font-weight:500; font-size:10.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-3);
  text-align:right; padding:8px 10px; border-bottom:1px solid var(--ink); white-space:nowrap}
td{padding:8px 10px; border-bottom:1px solid var(--rule); text-align:right; font-family:var(--f-mono); font-variant-numeric:tabular-nums; white-space:nowrap}
th:first-child, td:first-child{text-align:left}
td.txt, th.txt{text-align:left; font-family:var(--f-body); white-space:normal; min-width:240px}
td .muted{color:var(--ink-3)}
tr.now td{background:color-mix(in srgb, var(--ochre) 12%, transparent)}
.pos{color:var(--teal)} .neg{color:var(--orange)} .flat{color:var(--slate)}

.findings{list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:14px}
.findings li{display:grid; grid-template-columns:4px minmax(0,1fr); gap:12px}
.findings li::before{content:""; border-radius:2px; background:var(--slate)}
.findings li.g::before{background:var(--teal)} .findings li.w::before{background:var(--orange)} .findings li.n::before{background:var(--ochre)}
.findings h3{font-family:var(--f-display); font-weight:600; font-size:14.5px; margin:0 0 2px}
.findings p{margin:0; font-size:13.5px; color:var(--ink-2); max-width:68ch}
.findings p b{color:var(--ink); font-weight:600}

.ledger td.q{font-weight:600}
.tag{font-family:var(--f-mono); font-size:10px; letter-spacing:.05em; text-transform:uppercase; padding:1px 5px; border-radius:3px; margin-left:6px; vertical-align:1px}
.tag.live{color:var(--ochre); border:1px solid var(--ochre)}
.tag.interim{color:var(--plum); border:1px solid var(--plum)}
.tag.br{color:var(--slate); border:1px dashed var(--slate)}

.notes{font-size:12.5px; color:var(--ink-2); display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:10px 28px}
.notes h4{font-family:var(--f-display); font-size:13px; margin:0 0 4px; color:var(--ink)}
.notes p, .notes ul{margin:0}
.notes ul{padding-left:18px}
.notes li{margin:2px 0}
.foot{font-family:var(--f-mono); font-size:11px; color:var(--ink-3); border-top:1px solid var(--rule); padding-top:10px}

@media (max-width: 1080px){ .two{grid-template-columns:minmax(0,1fr)} }
@media (max-width: 900px){
  .kpis{grid-template-columns:minmax(0,1fr)}
  .strip{grid-template-columns:repeat(2, minmax(0,1fr))}
  .strip > div:nth-child(3){border-left:0}
  .strip > div:nth-child(n+3){border-top:1px solid var(--rule)}
  .notes{grid-template-columns:minmax(0,1fr)}
}
@media (max-width: 520px){
  .mast .meta{text-align:left}
  .chartbox{height:300px}
  .kpi .big .v{font-size:40px}
  .panel{padding:16px}
}
@media (prefers-reduced-motion: reduce){ *{transition:none!important; animation:none!important} }
</style>

<div class="wrap">
  <header class="mast">
    <div>
      <div class="eyebrow">{{season_status}}</div>
      <h1>Earnings Season Tracker <span>{{quarter}}</span></h1>
    </div>
    <div class="meta">Source: <b>FactSet Earnings Insight</b><br>Edition <b><a href="{{edition_url}}">{{edition}}</a></b> · updated {{built}}</div>
  </header>

  <section class="kpis" aria-label="Headline metrics">
    <article class="kpi">
      <div class="lab">Companies reported</div>
      <div class="big"><span class="v">{{pct_reported}}%</span><span class="sub">≈{{n_reported}} of {{n_index}}</span></div>
      <div>
        <div class="gauge" role="img" aria-label="{{pct_reported}}% reported; about {{pct_after_next}}% after next week's scheduled reports">
          <div class="fill" style="width:{{pct_reported}}%; background:var(--ink)"></div>
          <div class="ghost" style="left:{{pct_reported}}%; width:calc({{pct_after_next}}% - {{pct_reported}}%)"></div>
        </div>
        <div class="scale" style="margin-top:4px"><span style="left:0">0%</span><span style="left:{{pct_after_next}}%">≈{{pct_after_next}}%</span><span style="left:100%">100%</span></div>
      </div>
      <div class="ctx">{{kpi1_ctx}}</div>
    </article>

    <article class="kpi">
      <div class="lab">Beating EPS estimates</div>
      <div class="big"><span class="v">{{eps_beat}}%</span>{{beat_pill}}</div>
      <div>
        <div class="gauge" role="img" aria-label="{{eps_beat}}% beat, against a 5-year average of {{eps_beat_5y}}% and 10-year average of {{eps_beat_10y}}%">
          <div class="fill" style="width:calc(({{eps_beat}} - 60) / 30 * 100%); background:var(--teal)"></div>
          <div class="mk" style="left:calc(({{eps_beat_5y}} - 60) / 30 * 100%)" title="5-yr avg {{eps_beat_5y}}%"></div>
          <div class="mk" style="left:calc(({{eps_beat_10y}} - 60) / 30 * 100%); background:var(--ink-3)" title="10-yr avg {{eps_beat_10y}}%"></div>
        </div>
        <div class="scale" style="margin-top:4px"><span style="left:0">60%</span><span style="left:calc(({{eps_beat_5y}} - 60) / 30 * 100%)">5y {{eps_beat_5y}}</span><span style="left:100%">90%</span></div>
      </div>
      <div class="split" aria-hidden="true">
        <div style="flex:{{eps_beat}}; background:var(--teal)"></div>
        <div style="flex:{{eps_inline}}; background:var(--slate)"></div>
        <div style="flex:{{eps_miss}}; background:var(--orange)"></div>
      </div>
      <div class="keyrow"><span><i style="background:var(--teal)"></i>Beat {{eps_beat}}%</span><span><i style="background:var(--slate)"></i>In line {{eps_inline}}%</span><span><i style="background:var(--orange)"></i>Miss {{eps_miss}}%</span></div>
      <div class="ctx">1-yr avg <b>{{eps_beat_1y}}%</b>, 10-yr avg <b>{{eps_beat_10y}}%</b>. Averages use all 500 companies; the current figure covers only those reported.</div>
    </article>

    <article class="kpi">
      <div class="lab">Positive {{g_next_q}} EPS guidance</div>
      <div class="big"><span class="v">{{g_pos_pct}}%</span><span class="sub">{{g_pos}} of {{g_total}} guiders</span><span class="pill up">5-yr ≈{{g_pos_5y}}%</span></div>
      <div>
        <div class="split" style="height:12px" role="img" aria-label="{{g_pos}} positive and {{g_neg}} negative guidance">
          <div style="flex:{{g_pos}}; background:var(--teal)"></div>
          <div style="flex:{{g_neg}}; background:var(--orange)"></div>
        </div>
        <div class="scale" style="margin-top:4px"><span style="left:0">{{g_pos}} positive</span><span style="left:{{g_pos_5y}}%">5y ≈{{g_pos_5y}}%</span><span style="left:100%">{{g_neg}} negative</span></div>
      </div>
      <div class="ctx">{{kpi3_ctx}}</div>
    </article>
  </section>

  <section class="strip" aria-label="Season context">
    <div><span class="k">Revenue beats</span><span class="v {{rev_cls}}">{{rev_beat}}%</span><span class="c">5-yr {{rev_beat_5y}}% · 10-yr {{rev_beat_10y}}%</span></div>
    <div><span class="k">EPS surprise, aggregate</span><span class="v {{surp_cls}}">{{eps_surprise_fmt}}</span><span class="c">5-yr {{eps_surprise_5y_fmt}} · 10-yr {{eps_surprise_10y_fmt}}</span></div>
    <div><span class="k">EPS growth y/y</span><span class="v">{{eps_growth}}%</span><span class="c">{{growth_cell_note}}</span></div>
    <div><span class="k">Blended revenue growth y/y</span><span class="v">{{rev_growth}}%</span><span class="c">Fwd 12M P/E {{fwd_pe}}</span></div>
  </section>

  <section class="panel" aria-labelledby="h-hist">
    <div class="phead">
      <div>
        <h2 id="h-hist">Share of companies beating EPS estimates, by quarter</h2>
        <p class="dek">{{first_q}} to {{quarter}}. Each quarter is shown against the 5-year average FactSet quoted during that season. Shading marks the gap.</p>
      </div>
      <div class="seg" role="group" aria-label="Highlight">
        <button type="button" id="hl-all" aria-pressed="true">All quarters</button>
        <button type="button" id="hl-q4" aria-pressed="false">Q4 seasons</button>
      </div>
    </div>
    <div class="legend" style="margin-bottom:10px">
      <span><span class="sw" style="border-color:var(--ink)"></span>% beating</span>
      <span><span class="sw" style="border-color:var(--ink-3); border-top-style:dashed"></span>5-yr average</span>
      <span><span class="dot" style="background:var(--teal)"></span>Above avg</span>
      <span><span class="dot" style="background:var(--orange)"></span>Below avg</span>
      <span><span class="dot" style="border:2px solid var(--ochre); background:transparent; width:10px; height:10px"></span>Season in progress / interim read</span>
    </div>
    <div class="chartbox"><canvas id="histChart" aria-label="Line chart of the quarterly share of S&amp;P 500 companies beating EPS estimates against the 5-year average" role="img"></canvas></div>
    <p class="chartnote">{{n_above}} of the {{n_done}} completed quarters printed above their 5-year average. Range: {{worst_v}}% ({{worst_q}}) to {{best_v}}% ({{best_q}}).</p>
  </section>

  <section class="panel" aria-labelledby="h-growth">
    <div class="phead">
      <div>
        <h2 id="h-growth">Earnings growth, year over year</h2>
        <p class="dek">Bars: quarterly growth as FactSet reported it late in each season. Line: trailing four quarters (TTM), weighted by year-ago bottom-up EPS.</p>
      </div>
      <div class="seg" role="group" aria-label="Range">
        <button type="button" id="gr-all" aria-pressed="false">Since Q2'21</button>
        <button type="button" id="gr-22" aria-pressed="true">Since Q1'22</button>
      </div>
    </div>
    <div class="legend" style="margin-bottom:10px">
      <span><span class="dot" style="border-radius:1px; background:var(--slate)"></span>Quarterly y/y</span>
      <span><span class="dot" style="border-radius:1px; background:var(--orange)"></span>Quarterly decline</span>
      <span><span class="sw" style="border-color:var(--ink)"></span>TTM y/y (latest {{ttm_live}}%)</span>
      <span><span class="dot" style="border-radius:1px; background:var(--ochre)"></span>{{live_growth_legend}}</span>
    </div>
    <div class="chartbox"><canvas id="growthChart" role="img" aria-label="Bar chart of quarterly year-over-year S&amp;P 500 earnings growth with a trailing twelve-month growth line"></canvas></div>
    <p class="chartnote">{{growth_note}}</p>
  </section>

  <section class="panel" aria-labelledby="h-guide">
    <div class="phead">
      <div>
        <h2 id="h-guide">Share of companies issuing positive quarterly EPS guidance</h2>
        <p class="dek">Guidance for each quarter, counted at quarter end, against FactSet's 5-year average. {{g_next_q}} is still accumulating ({{g_total}} companies so far).</p>
      </div>
    </div>
    <div class="legend" style="margin-bottom:10px">
      <span><span class="sw" style="border-color:var(--ink)"></span>% positive</span>
      <span><span class="sw" style="border-color:var(--ink-3); border-top-style:dashed"></span>5-yr average</span>
      <span><span class="dot" style="background:var(--teal)"></span>Above avg</span>
      <span><span class="dot" style="background:var(--orange)"></span>Below avg</span>
      <span><span class="dot" style="border:2px solid var(--ochre); background:transparent; width:10px; height:10px"></span>Count still accumulating</span>
    </div>
    <div class="chartbox"><canvas id="guideChart" role="img" aria-label="Line chart of the share of S&amp;P 500 companies issuing positive quarterly EPS guidance against the 5-year average"></canvas></div>
    <p class="chartnote">{{guide_note}}</p>
  </section>

  <div class="two">
    <section class="panel" aria-labelledby="h-path">
      <h2 id="h-path">This season, week by week</h2>
      <p class="dek">Each row is one weekly edition of Earnings Insight; dates link to the PDF.</p>
      <div class="tscroll" style="margin-top:12px">
        <table class="compact">
          <thead><tr><th>Edition</th><th>Reported</th><th>EPS beat</th><th>Rev beat</th><th>EPS surp.</th><th>{{g_next_short}} guide</th><th>Growth</th></tr></thead>
          <tbody id="weeks"></tbody>
        </table>
      </div>
      <h2 style="margin-top:22px; font-size:16px">Early reads drift</h2>
      <p class="dek">Beat rate a few weeks in, against where the quarter finished.</p>
      <div class="tscroll" style="margin-top:8px">
        <table>
          <thead><tr><th>Quarter</th><th>Early read</th><th>At % reported</th><th>Final</th><th>Drift</th></tr></thead>
          <tbody id="drift"></tbody>
        </table>
      </div>
    </section>

    <section class="panel" aria-labelledby="h-find">
      <h2 id="h-find">What the numbers say</h2>
      <ul class="findings" style="margin-top:14px">
{{findings_html}}
      </ul>
    </section>
  </div>

  <section class="panel" aria-labelledby="h-ledger">
    <h2 id="h-ledger">Quarterly ledger</h2>
    <p class="dek">Every value in the charts with the FactSet edition it was read from. Guidance percentages link to their own edition.</p>
    <div class="tscroll" style="margin-top:12px">
      <table class="ledger">
        <thead><tr><th>Quarter</th><th>% beat EPS</th><th>5-yr avg</th><th>Gap</th><th>EPS growth y/y</th><th>TTM y/y</th><th>Pos. guidance</th><th>Guide 5-yr</th><th class="txt">Scorecard reading</th></tr></thead>
        <tbody id="ledger"></tbody>
      </table>
    </div>
  </section>

  <section class="notes" aria-label="Method and sources">
    <div>
      <h4>Definitions</h4>
      <ul>
        <li><b>Beat</b>: actual EPS above the mean estimate (FactSet). In-line and misses are split out above.</li>
        <li><b>Positive guidance</b>: a company's EPS guidance (or range midpoint) is above the mean estimate the day before it was issued. FactSet does not publish a "% raising guidance" series; this is its closest measure. Guidance is either positive or negative, so the ≈{{g_pos_5y}}% norm is 100% minus FactSet's {{g_neg_5y}}% negative average.</li>
        <li><b>Reported count</b> (≈{{n_reported}}) is derived: {{pct_reported}}% of FactSet's 500-company base.</li>
      </ul>
    </div>
    <div>
      <h4>Data notes</h4>
      <ul>
        <li>Every completed quarter is a late-season reading (94% or more of companies reported). FactSet's later citations can differ slightly: Q3'23 beat rate 81% later vs 82% at 98%; Q2'21 growth 91.6% vs 90.9%; Q4'21 growth 32.0% vs 30.7% at 95%.</li>
        <li><b>TTM growth</b>: FactSet's quarterly y/y rates for the last four quarters, weighted by the year-ago quarters' bottom-up EPS. A plain ratio of summed bottom-up EPS runs within {{lvl_diff_mean}} pts on average (max {{lvl_diff_max}}), since FactSet's rates compare the same companies year on year.</li>
        <li>Guidance counts are pre-season, from the edition nearest quarter end. Q3'22: FactSet gave no 5-yr percentage that quarter, so the average line is bridged.</li>
        <li>5-yr averages are as quoted in-season by FactSet, not recomputed.</li>
      </ul>
    </div>
    <div>
      <h4>Sources</h4>
      <ul>
        <li><a href="{{edition_url}}">Earnings Insight, {{edition}}</a> (current scorecard, guidance)</li>
        <li>This season's weekly editions: linked from the week-by-week table.</li>
        <li>Historical rows: the edition named in each ledger row (linked).</li>
        <li>Bottom-up EPS (TTM weights): <a href="{{fs}}EarningsInsight_101620.pdf">Oct 16, 2020</a> · <a href="{{fs}}EarningsInsight_040822.pdf">Apr 8, 2022</a> · <a href="{{fs}}EarningsInsight_101323.pdf">Oct 13, 2023</a> · <a href="{{fs}}EarningsInsight_041125A.pdf">Apr 11, 2025</a> · <a href="{{fs}}EarningsInsight_102326.pdf">Oct 23, 2026</a>{{eps_imputed_note}}</li>
      </ul>
    </div>
    <div>
      <h4>Refresh</h4>
      <p>A GitHub Action fetches each new Earnings Insight edition, parses it and rebuilds this page (weekly, Saturday morning Melbourne time). Last edition processed: {{edition}}. Rows and figures are as FactSet printed them; nothing is estimated except where noted.</p>
    </div>
  </section>

  <div class="foot">Not investment advice. Figures transcribed from FactSet Research Systems' public Earnings Insight reports.</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const DATA = /*__DATA__*/null;
(function(){
  const H = DATA.history, N = H.length;
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const rgba = (hex, a) => { const h = hex.replace('#',''); const v = h.length===3 ? h.split('').map(c=>c+c).join('') : h;
    const n = parseInt(v,16); return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`; };
  const fmtPct = v => v==null ? '<span class="muted">—</span>' : v + '%';
  const gapCls = g => g>0 ? 'pos' : (g<0 ? 'neg' : 'flat');
  const sign = g => (g>0?'+':'') + g;

  // tables
  document.getElementById('weeks').innerHTML = DATA.weeks.map((w,i) => {
    const last = i === DATA.weeks.length-1;
    const g = (w.gpos==null) ? '<span class="muted">—</span>' : `<span class="pos">${w.gpos}</span> / <span class="neg">${w.gneg}</span>`;
    const s = w.surp==null ? '<span class="muted">—</span>' : (w.surp>0?'+':'') + w.surp.toFixed(1) + '%';
    const dt = w.src ? `<a href="${DATA.fs}EarningsInsight_${w.src}.pdf">${w.date}</a>` : w.date;
    const gr = w.growth == null ? '<span class="muted">—</span>' : w.growth.toFixed(1) + '%' + (i===0 ? ' <span class="muted">est</span>' : '');
    return `<tr${last?' class="now"':''}><td>${dt}</td><td>${w.reported}</td><td>${fmtPct(w.eps)}</td><td>${fmtPct(w.rev)}</td><td>${s}</td><td>${g}</td><td>${gr}</td></tr>`;
  }).join('');
  document.getElementById('drift').innerHTML = DATA.drift.slice(-4).map(d => {
    const g = d.final - d.early;
    return `<tr><td>${d.q}</td><td>${d.early}%</td><td>${d.at}%</td><td>${d.final}%</td><td class="${gapCls(g)}">${sign(g)} pts</td></tr>`;
  }).join('');
  const GQ = {}; DATA.guidance.forEach(g => GQ[g.q] = g);
  const f1 = v => v == null ? '—' : (v>0?'+':'') + v.toFixed(1) + '%';
  document.getElementById('ledger').innerHTML = H.slice().reverse().map(h => {
    const g = h.beat - h.avg5;
    const tag = h.flag==='live' ? '<span class="tag live">live</span>' : '';
    const gq = GQ[h.q];
    const gcell = gq ? `<a href="${DATA.fs}EarningsInsight_${gq.src}.pdf">${gq.pct}%</a> <span class="muted">${gq.pos}/${gq.pos+gq.neg}</span>` : '<span class="muted">—</span>';
    const g5 = gq && gq.pos5 != null ? gq.pos5 + '%' : '<span class="muted">n/a</span>';
    return `<tr${h.flag==='live'?' class="now"':''}><td class="q">${h.q}${tag}</td><td>${h.beat}%</td><td>${h.avg5}%</td><td class="${gapCls(g)}">${sign(g)}</td>` +
      `<td class="${h.growth<0?'neg':''}">${f1(h.growth)}</td><td class="${h.ttm<0?'neg':''}">${f1(h.ttm)}</td><td>${gcell}</td><td>${g5}</td>` +
      `<td class="txt"><a href="${DATA.fs}EarningsInsight_${h.src}.pdf">${h.read}</a></td></tr>`;
  }).join('');

  if (typeof Chart === 'undefined') {
    document.querySelectorAll('.chartbox').forEach(el => el.innerHTML = '<p class="chartnote">The chart library did not load. All values are in the quarterly ledger below.</p>');
    return;
  }

  const mono = '"IBM Plex Mono", ui-monospace, Menlo, monospace';
  const charts = {};
  let beatMode = 'all', growthRange = '22';
  const palette = () => ({ ink:css('--ink'), ink2:css('--ink-2'), ink3:css('--ink-3'), rule:css('--rule'), card:css('--card'),
      teal:css('--teal'), orange:css('--orange'), slate:css('--slate'), ochre:css('--ochre') });
  const isQ4 = q => q.startsWith('Q4');

  // Shared builder: a series against its 5-yr average, gap shaded, value labels on points.
  function vsAvgChart(id, rows, opt){
    const C = palette();
    const n = rows.length;
    const hollow = i => !!rows[i].hollow;
    const dim = i => opt.dim ? opt.dim(i) : false;
    const ptColor = i => { const r = rows[i]; if (r.avg == null) return C.slate; return r.v > r.avg ? C.teal : (r.v < r.avg ? C.orange : C.slate); };
    const labels = {
      id:'vlabels-'+id,
      afterDatasetsDraw(c){
        const pts = c.getDatasetMeta(0).data; const ctx = c.ctx;
        const gap = pts.length>1 ? pts[1].x - pts[0].x : 99;
        ctx.save(); ctx.textAlign = 'center';
        pts.forEach((p,i) => {
          if (gap < 24 && i % 2 === 1 && i !== n-1 && !(opt.keepLabel && opt.keepLabel(i))) return;
          const r = rows[i]; const below = r.avg != null && r.v < r.avg;
          ctx.font = `${i===n-1?'600':'500'} 10.5px ${mono}`;
          ctx.fillStyle = dim(i) ? rgba(C.ink3, .45) : (hollow(i) ? C.ochre : C.ink2);
          ctx.fillText(r.v, p.x, below ? p.y + 17 : p.y - 10);
        });
        if (opt.banner) { const a = c.chartArea; ctx.textAlign='left'; ctx.font = `500 11px ${mono}`; ctx.fillStyle = C.orange; ctx.fillText(opt.banner, a.left + 6, a.top + 14); }
        ctx.restore();
      }
    };
    const cfg = {
      type:'line',
      data:{ labels: rows.map(r => r.q), datasets:[
        { label: opt.label, data: rows.map(r => r.v), borderColor:C.ink, borderWidth:2, tension:0, order:1,
          fill:{ target:1, above:rgba(C.teal, opt.dim?.07:.16), below:rgba(C.orange, opt.dim?.08:.18) },
          segment:{ borderDash: s => hollow(s.p1DataIndex) ? [4,4] : undefined,
                    borderColor: s => (opt.dim && dim(s.p0DataIndex) && dim(s.p1DataIndex)) ? rgba(C.ink,.35) : C.ink },
          pointRadius: ctx => (opt.dim && !dim(ctx.dataIndex)) ? 6.5 : 5, pointHoverRadius:7,
          pointBorderWidth: ctx => hollow(ctx.dataIndex) ? 2.5 : 1.5,
          pointBackgroundColor: ctx => { const i = ctx.dataIndex; if (hollow(i)) return C.card; const col = ptColor(i); return dim(i) ? rgba(col,.3) : col; },
          pointBorderColor: ctx => { const i = ctx.dataIndex; if (hollow(i)) return C.ochre; return dim(i) ? rgba(C.card,.6) : C.card; } },
        { label:'5-yr average', data: rows.map(r => r.avg), borderColor:C.ink3, borderWidth:1.6, borderDash:[5,4],
          stepped:'middle', spanGaps:true, pointRadius:0, pointHoverRadius:0, fill:false, order:2 }
      ]},
      options:{
        responsive:true, maintainAspectRatio:false, animation:false,
        layout:{ padding:{ top:14, right:6 } }, interaction:{ mode:'index', intersect:false },
        scales:{
          y:{ min:opt.min, max:opt.max, ticks:{ stepSize:opt.step, callback:v => v + '%', font:{ size:11 } }, grid:{ color:C.rule, drawTicks:false }, border:{ display:false } },
          x:{ grid:{ display:false }, border:{ color:C.rule },
              ticks:{ autoSkip:true, maxRotation:0, autoSkipPadding:10, font:{ size:11 }, color: ctx => (opt.dim && !dim(ctx.index)) ? C.orange : C.ink3 } }
        },
        plugins:{ legend:{ display:false },
          tooltip:{ backgroundColor:C.ink, titleColor:C.card, bodyColor:C.card, padding:10, cornerRadius:4,
            titleFont:{ family:mono, weight:'600' }, bodyFont:{ family:mono }, displayColors:false,
            callbacks:{ title: items => rows[items[0].dataIndex].q.replace("'", " 20"),
              label: item => item.datasetIndex===0 ? `${opt.label}: ${item.raw}%` : `5-yr avg: ${item.raw == null ? 'not stated' : item.raw + '%'}`,
              afterBody: items => opt.after ? opt.after(items[0].dataIndex) : [] } } }
      },
      plugins:[labels]
    };
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(document.getElementById(id), cfg);
  }

  function buildBeat(){
    const rows = H.map(h => ({ q:h.q, v:h.beat, avg:h.avg5, hollow:h.flag==='live' }));
    vsAvgChart('histChart', rows, {
      label:'Beat EPS', min:60, max:90, step:5,
      dim: beatMode==='q4' ? (i => !isQ4(H[i].q)) : null,
      keepLabel: i => beatMode==='q4' && isQ4(H[i].q),
      banner: beatMode==='q4' ? `Q4 avg since 2022: ${DATA.stats.q4_avg}%  ·  other quarters: ${DATA.stats.nonq4_avg}%` : null,
      after: i => { const h = H[i]; return [`Gap: ${sign(h.beat - h.avg5)} pts`, h.read]; }
    });
  }

  function buildGuide(){
    const G = DATA.guidance;
    const rows = G.map(g => ({ q:g.q, v:g.pct, avg:g.pos5, hollow:!!g.live }));
    vsAvgChart('guideChart', rows, {
      label:'Positive guidance', min:20, max:70, step:10,
      after: i => { const g = G[i]; return [`${g.pos} positive, ${g.neg} negative`, (g.live ? 'Early count · ' : 'Pre-season count · ') + 'Earnings Insight ' + g.src]; }
    });
  }

  function buildGrowth(){
    const C = palette();
    const start = growthRange === '22' ? H.findIndex(h => h.q === "Q1'22") : 0;
    const rows = H.slice(start); const n = rows.length;
    const live = i => rows[i].flag === 'live';
    const barCol = i => live(i) ? rgba(C.ochre, .75) : (rows[i].growth < 0 ? C.orange : rgba(C.slate, .85));
    const labels = {
      id:'glabels',
      afterDatasetsDraw(c){
        const bars = c.getDatasetMeta(0).data; const line = c.getDatasetMeta(1).data; const ctx = c.ctx;
        const gap = bars.length>1 ? bars[1].x - bars[0].x : 99;
        ctx.save(); ctx.textAlign = 'center'; ctx.font = `500 10px ${mono}`;
        bars.forEach((b,i) => {
          if (gap < 26 && i % 2 === 1 && i !== n-1) return;
          const v = rows[i].growth; const neg = v < 0;
          const tY = line[i].y; let y = neg ? b.y + 13 : b.y - 6;
          if (!neg && Math.abs(y - tY) < 11) y = Math.min(b.y, tY) - 12;
          if (live(i) && !neg && (b.base - b.y) > 22) { ctx.fillStyle = C.card; ctx.fillText(v.toFixed(1), b.x, b.y + 14); return; }
          ctx.fillStyle = live(i) ? C.ochre : (neg ? C.orange : C.ink2);
          ctx.fillText(v.toFixed(1), b.x, y);
        });
        ctx.restore();
      }
    };
    const cfg = {
      data:{ labels: rows.map(r => r.q), datasets:[
        { type:'bar', label:'Quarterly y/y', data: rows.map(r => r.growth), order:2,
          backgroundColor: ctx => barCol(ctx.dataIndex), borderRadius:2, maxBarThickness:28, categoryPercentage:.72, barPercentage:.9 },
        { type:'line', label:'TTM y/y', data: rows.map(r => r.ttm), order:1, borderColor:C.ink, borderWidth:2, tension:.25,
          segment:{ borderDash: s => live(s.p1DataIndex) ? [4,4] : undefined },
          pointRadius: ctx => live(ctx.dataIndex) ? 4.5 : 2.5, pointHoverRadius:5,
          pointBackgroundColor: ctx => live(ctx.dataIndex) ? C.card : C.ink,
          pointBorderColor: ctx => live(ctx.dataIndex) ? C.ochre : C.ink, pointBorderWidth: ctx => live(ctx.dataIndex) ? 2.5 : 1 }
      ]},
      options:{
        responsive:true, maintainAspectRatio:false, animation:false,
        layout:{ padding:{ top:16, right:6 } }, interaction:{ mode:'index', intersect:false },
        scales:{
          y:{ grace:'6%', ticks:{ callback:v => v + '%', font:{ size:11 }, maxTicksLimit:8 },
              grid:{ color: ctx => ctx.tick.value === 0 ? C.ink3 : C.rule, drawTicks:false }, border:{ display:false } },
          x:{ grid:{ display:false }, border:{ color:C.rule }, ticks:{ autoSkip:true, maxRotation:0, autoSkipPadding:10, font:{ size:11 }, color:C.ink3 } }
        },
        plugins:{ legend:{ display:false },
          tooltip:{ backgroundColor:C.ink, titleColor:C.card, bodyColor:C.card, padding:10, cornerRadius:4,
            titleFont:{ family:mono, weight:'600' }, bodyFont:{ family:mono }, displayColors:false,
            callbacks:{ title: items => rows[items[0].dataIndex].q.replace("'", " 20"),
              label: item => `${item.dataset.label}: ${f1(item.raw)}`,
              afterBody: items => { const r = rows[items[0].dataIndex]; const out = [r.flag==='live' ? 'Blended, season in progress' : r.read]; if (r.gnote) out.push(r.gnote); return out; } } } }
      },
      plugins:[labels]
    };
    if (charts.growth) charts.growth.destroy();
    charts.growth = new Chart(document.getElementById('growthChart'), cfg);
  }

  function buildAll(){
    Chart.defaults.font.family = mono; Chart.defaults.color = css('--ink-3');
    buildBeat(); buildGrowth(); buildGuide();
  }
  const press = (on, off) => { document.getElementById(on).setAttribute('aria-pressed','true'); document.getElementById(off).setAttribute('aria-pressed','false'); };
  document.getElementById('hl-all').addEventListener('click', () => { beatMode='all'; press('hl-all','hl-q4'); buildBeat(); });
  document.getElementById('hl-q4').addEventListener('click', () => { beatMode='q4'; press('hl-q4','hl-all'); buildBeat(); });
  document.getElementById('gr-all').addEventListener('click', () => { growthRange='all'; press('gr-all','gr-22'); buildGrowth(); });
  document.getElementById('gr-22').addEventListener('click', () => { growthRange='22'; press('gr-22','gr-all'); buildGrowth(); });

  (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(buildAll, buildAll);
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  if (mq.addEventListener) mq.addEventListener('change', buildAll);
  new MutationObserver(buildAll).observe(document.documentElement, { attributes:true, attributeFilter:['data-theme'] });
})();
</script>
"""


def render(D):
    ctx, stats = build_context(D)
    ctx["g_neg_5y"] = 100 - ctx["g_pos_5y"]
    payload = {"history": D["history"], "guidance": D["guidance"], "weeks": D["season_weeks"],
               "drift": D["drift"][-4:], "stats": stats, "fs": FS}
    out, n = re.subn(r"/\*__DATA__\*/null", lambda m: json.dumps(payload, ensure_ascii=False), TEMPLATE)
    assert n == 1, "data placeholder not found"
    missing = set()

    def fill(m):
        k = m.group(1)
        if k not in ctx or ctx[k] is None:
            missing.add(k)
            return "—"
        return str(ctx[k])

    out = re.sub(r"\{\{(\w+)\}\}", fill, out)
    if missing:
        raise SystemExit(f"unfilled placeholders: {sorted(missing)}")
    return out


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs"
    outdir.mkdir(parents=True, exist_ok=True)
    D = json.loads(DATA_PATH.read_text())
    fragment = render(D)
    standalone = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                  '</head>\n<body>\n' + fragment + '\n</body>\n</html>\n')
    (outdir / "index.html").write_text(standalone, encoding="utf-8")
    (outdir / "earnings_season_tracker.artifact.html").write_text(fragment, encoding="utf-8")
    (outdir / ".nojekyll").write_text("")
    print(f"built {outdir/'index.html'} from edition {D['meta'].get('last_edition_date')}")


if __name__ == "__main__":
    main()
