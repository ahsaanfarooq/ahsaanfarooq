#!/usr/bin/env python3
"""Generate self-hosted GitHub stats SVGs - replaces the third-party
github-readme-stats.vercel.app widget with graphics this repo draws itself.

Stdlib only (urllib for the GraphQL API), matching the guide's approach so
there's nothing to break in CI. Run by .github/workflows/refresh-stats.yml.

Two determinism traps this avoids (see guide):
  1. The contribution window is pinned to whole UTC days (00:00:00 -> 23:59:59),
     not "the past year from right now" - otherwise two runs minutes apart
     bucket days into different weeks and the sparkline shifts every night.
  2. Repos are filtered to `privacy: PUBLIC` only, so the numbers don't depend
     on whether the token running this can see private repos.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["GITHUB_TOKEN"]
LOGIN = os.environ["GH_LOGIN"]
API = "https://api.github.com/graphql"

now = datetime.now(timezone.utc)
today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
window_start = (today_end - timedelta(days=364)).replace(hour=0, minute=0, second=0, microsecond=0)

FROM = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
TO = today_end.strftime("%Y-%m-%dT%H:%M:%SZ")

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
    repositories(first: 100, privacy: PUBLIC, isFork: false, ownerAffiliations: OWNER) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def svg_wrap(width, height, body, bg="#0d1117"):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" rx="6" fill="{bg}"/>'
        f'{body}</svg>'
    )


def text(x, y, s, size=13, fill="#c9d1d9", weight="normal", family="monospace", anchor="start"):
    return (
        f'<text x="{x}" y="{y}" font-family="{family}, monospace" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{esc(s)}</text>'
    )


def main():
    data = gql(QUERY, {"login": LOGIN, "from": FROM, "to": TO})
    user = data["user"]
    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [d for w in weeks for d in w["contributionDays"]]

    total = sum(d["contributionCount"] for d in days)

    # --- streaks (current + longest) ---
    longest = cur = 0
    best_run = 0
    cur_streak = 0
    streak_start = streak_end = None
    run_start = None
    for d in days:
        if d["contributionCount"] > 0:
            if run_start is None:
                run_start = d["date"]
            cur += 1
            if cur > best_run:
                best_run = cur
                longest_start, longest_end = run_start, d["date"]
        else:
            cur = 0
            run_start = None
    # current streak = trailing run ending today (or most recent day with data)
    cur_streak = 0
    for d in reversed(days):
        if d["contributionCount"] > 0:
            cur_streak += 1
        else:
            break

    repos = user["repositories"]["nodes"]
    lang_bytes = {}
    for r in repos:
        for e in r["languages"]["edges"]:
            name = e["node"]["name"]
            lang_bytes[name] = lang_bytes.get(name, 0) + e["size"]
    top_langs = sorted(lang_bytes.items(), key=lambda kv: -kv[1])[:6]
    total_bytes = sum(v for _, v in top_langs) or 1

    weekly = []
    for w in weeks:
        weekly.append(sum(d["contributionCount"] for d in w["contributionDays"]))

    os.makedirs("assets", exist_ok=True)

    # ---- stats.svg: hero total + weekly sparkline (bars, per the guide - "columns are honest") ----
    W, H = 480, 160
    body = text(20, 34, "contributions, last 365 days", size=12, fill="#8b949e")
    body += text(20, 70, f"{total:,}", size=34, fill="#58a6ff", weight="bold")
    bar_w = (W - 40) / max(len(weekly), 1)
    max_week = max(weekly) or 1
    for i, v in enumerate(weekly):
        h = 0 if v == 0 else max(2, (v / max_week) * 50)
        x = 20 + i * bar_w
        y = 140 - h
        body += f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bar_w - 1, 1):.1f}" height="{h:.1f}" fill="#2ea043" rx="1"/>'
    with open("assets/stats.svg", "w") as f:
        f.write(svg_wrap(W, H, body))

    # ---- streak.svg ----
    W, H = 480, 120
    body = text(20, 30, "current streak", size=12, fill="#8b949e")
    body += text(20, 60, f"{cur_streak} days", size=22, fill="#f0883e", weight="bold")
    body += text(240, 30, "longest streak", size=12, fill="#8b949e")
    body += text(240, 60, f"{best_run} days", size=22, fill="#a371f7", weight="bold")
    if best_run:
        body += text(20, 95, f"longest run: {longest_start} to {longest_end}", size=11, fill="#8b949e")
    with open("assets/streak.svg", "w") as f:
        f.write(svg_wrap(W, H, body))

    # ---- langs.svg: top languages by bytes ----
    W, H = 480, 30 + 24 * len(top_langs) + 10
    body = text(20, 28, "top languages, by bytes", size=12, fill="#8b949e")
    y = 50
    for name, size in top_langs:
        pct = size / total_bytes * 100
        bar_max = 260
        bw = max(2, bar_max * pct / 100)
        body += text(20, y + 5, name, size=12, fill="#c9d1d9")
        body += f'<rect x="140" y="{y - 8}" width="{bar_max}" height="10" rx="3" fill="#21262d"/>'
        body += f'<rect x="140" y="{y - 8}" width="{bw:.1f}" height="10" rx="3" fill="#58a6ff"/>'
        body += text(410, y + 5, f"{pct:.1f}%", size=11, fill="#8b949e")
        y += 24
    with open("assets/langs.svg", "w") as f:
        f.write(svg_wrap(W, H, body))

    # ---- year.svg: heatmap, one cell per day ----
    cols = len(weeks)
    cell = 9
    gap = 2
    W = 40 + cols * (cell + gap)
    H = 40 + 7 * (cell + gap)
    body = text(20, 20, "the year, one cell per day", size=12, fill="#8b949e")

    def color_for(count):
        if count == 0: return "#161b22"
        if count < 3: return "#0e4429"
        if count < 6: return "#006d32"
        if count < 10: return "#26a641"
        return "#39d353"

    for wi, w in enumerate(weeks):
        for di, d in enumerate(w["contributionDays"]):
            x = 20 + wi * (cell + gap)
            y = 30 + di * (cell + gap)
            body += f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{color_for(d["contributionCount"])}"/>'
    with open("assets/year.svg", "w") as f:
        f.write(svg_wrap(W, H, body))

    print(f"Generated: total={total}, current_streak={cur_streak}, longest_streak={best_run}, "
          f"top_lang={top_langs[0][0] if top_langs else 'n/a'}")


if __name__ == "__main__":
    main()
