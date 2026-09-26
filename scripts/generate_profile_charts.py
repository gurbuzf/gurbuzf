#!/usr/bin/env python3
"""Generate the SVG charts shown on the GitHub profile README.

Fetches public profile data from the GitHub GraphQL API and renders, in a light
and a dark variant each:

  * activity.svg   - weekly contributions over the last 12 months
  * languages.svg  - top languages across owned repositories (by code size)

Only the standard library is used, so the workflow needs no `pip install`.

Usage:
    GITHUB_TOKEN=... python scripts/generate_profile_charts.py --user gurbuzf --out dist
    python scripts/generate_profile_charts.py --sample --out dist   # offline preview
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import urllib.request
from html import escape
from pathlib import Path

API_URL = "https://api.github.com/graphql"

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

# Chart tokens per theme. Surfaces follow GitHub's own light/dark backgrounds so
# the cards sit naturally on the profile page; the series blue was validated for
# lightness band, chroma and >= 3:1 contrast against each surface.
THEMES = {
    "light": {
        "surface": "#ffffff",
        "border": "#d0d7de",
        "text_primary": "#1f2328",
        "text_secondary": "#59636e",
        "text_muted": "#818b98",
        "grid": "#eaeef2",
        "baseline": "#d0d7de",
        "series": "#2a78d6",
        "track": "#cde2fb",
    },
    "dark": {
        "surface": "#0d1117",
        "border": "#30363d",
        "text_primary": "#f0f6fc",
        "text_secondary": "#9198a1",
        "text_muted": "#6e7681",
        "grid": "#21262d",
        "baseline": "#30363d",
        "series": "#3987e5",
        "track": "#1c2d44",
    },
}

WIDTH = 840

QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, after: $cursor,
                 orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------- data


def graphql(token: str, variables: dict) -> dict:
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-charts",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]["user"]


def fetch_profile(user: str, token: str) -> dict:
    cursor = None
    repos: list[dict] = []
    first_page = None
    while True:
        page = graphql(token, {"login": user, "cursor": cursor})
        if page is None:
            raise RuntimeError(f"GitHub user {user!r} not found")
        first_page = first_page or page
        repos.extend(page["repositories"]["nodes"])
        info = page["repositories"]["pageInfo"]
        if not info["hasNextPage"]:
            break
        cursor = info["endCursor"]

    cc = first_page["contributionsCollection"]
    days = [
        (dt.date.fromisoformat(d["date"]), d["contributionCount"])
        for w in cc["contributionCalendar"]["weeks"]
        for d in w["contributionDays"]
    ]
    languages: dict[str, int] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            languages[name] = languages.get(name, 0) + edge["size"]

    return {
        "login": first_page["login"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "repos": first_page["repositories"]["totalCount"],
        "followers": first_page["followers"]["totalCount"],
        "commits": cc["totalCommitContributions"] + cc["restrictedContributionsCount"],
        "prs": cc["totalPullRequestContributions"],
        "issues": cc["totalIssueContributions"],
        "reviews": cc["totalPullRequestReviewContributions"],
        "contributions": cc["contributionCalendar"]["totalContributions"],
        "days": days,
        "languages": languages,
    }


def sample_profile() -> dict:
    """Plausible fake data for rendering previews without network access."""
    rng = random.Random(7)
    end = dt.date.today()
    start = end - dt.timedelta(days=370)
    days = []
    for i in range((end - start).days + 1):
        day = start + dt.timedelta(days=i)
        busy = 0.35 + 0.3 * (i / 370)
        count = rng.randint(1, 9) if rng.random() < busy else 0
        days.append((day, count))
    return {
        "login": "gurbuzf",
        "stars": 42,
        "repos": 31,
        "followers": 18,
        "commits": 612,
        "prs": 37,
        "issues": 14,
        "reviews": 9,
        "contributions": sum(c for _, c in days),
        "days": days,
        "languages": {
            "Python": 1_850_000,
            "JavaScript": 610_000,
            "TypeScript": 280_000,
            "HTML": 240_000,
            "CSS": 120_000,
            "Shell": 40_000,
            "Dockerfile": 6_000,
        },
    }


# --------------------------------------------------------------------------- svg helpers


def compact(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n / 1_000:.0f}K"
    if n >= 1_000:
        return f"{n:,.0f}"
    return f"{n:g}"


def nice_axis(value: float) -> tuple[int, int]:
    """Tightest (maximum, tick count) >= value with whole 1/2/5 x 10^n steps."""
    best = None
    for ticks in (4, 5):
        raw = max(value / ticks, 1)
        exp = 10 ** (len(str(int(raw))) - 1)
        step = next(m * exp for m in (1, 2, 5, 10) if raw <= m * exp)
        if best is None or step * ticks < best[0]:
            best = (step * ticks, ticks)
    return best


def svg_document(width: int, height: int, title: str, body: str, t: dict) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
<title>{escape(title)}</title>
<style>
  text {{ font-family: {FONT}; }}
  .title {{ font-size: 16px; font-weight: 600; fill: {t['text_primary']}; }}
  .subtitle {{ font-size: 12px; fill: {t['text_secondary']}; }}
  .axis {{ font-size: 11px; fill: {t['text_muted']}; font-variant-numeric: tabular-nums; }}
  .label {{ font-size: 13px; fill: {t['text_primary']}; }}
  .value {{ font-size: 12px; fill: {t['text_secondary']}; font-variant-numeric: tabular-nums; }}
  .annot {{ font-size: 11px; font-weight: 600; fill: {t['text_primary']}; }}
</style>
<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="{t['surface']}" stroke="{t['border']}"/>
{body}
</svg>
"""


def header(title: str, subtitle: str) -> str:
    return (
        f'<text class="title" x="24" y="36">{escape(title)}</text>\n'
        f'<text class="subtitle" x="24" y="56">{escape(subtitle)}</text>\n'
    )


# --------------------------------------------------------------------------- charts


def weekly_totals(days: list[tuple[dt.date, int]], weeks: int = 52) -> list[tuple[dt.date, int]]:
    """Sum daily counts into Sunday-starting weeks, keeping the last `weeks`."""
    buckets: dict[dt.date, int] = {}
    for day, count in days:
        start = day - dt.timedelta(days=(day.weekday() + 1) % 7)
        buckets[start] = buckets.get(start, 0) + count
    return sorted(buckets.items())[-weeks:]


def render_activity(p: dict, t: dict) -> str:
    data = weekly_totals(p["days"])
    height = 300
    left, right, top, bottom = 56, WIDTH - 32, 84, height - 44
    ymax, ticks = nice_axis(max((c for _, c in data), default=0) * 1.1)
    n = len(data)

    def xs(i: int) -> float:
        return left + (right - left) * (i / max(n - 1, 1))

    def ys(v: float) -> float:
        return bottom - (bottom - top) * (v / ymax)

    parts = [
        header(
            "Contribution activity",
            f"Contributions per week · {p['contributions']:,} in the last 12 months"
            + (f" · {data[-1][1]} this week" if data else ""),
        )
    ]
    # Gridlines + y ticks
    for k in range(ticks + 1):
        v = ymax * k / ticks
        y = ys(v)
        stroke = t["baseline"] if k == 0 else t["grid"]
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{stroke}"/>')
        parts.append(f'<text class="axis" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{compact(v)}</text>')

    # Month ticks at the first week of each month
    last_month = None
    for i, (week, _) in enumerate(data):
        if week.month != last_month:
            if last_month is not None and xs(i) < right - 20:
                label = week.strftime("%b")
                if week.month == 1:
                    label = week.strftime("%b %Y")
                parts.append(
                    f'<text class="axis" x="{xs(i):.1f}" y="{bottom + 20}" text-anchor="middle">{label}</text>'
                )
            last_month = week.month

    if data:
        points = [(xs(i), ys(c)) for i, (_, c) in enumerate(data)]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        area = f"{left:.1f},{bottom:.1f} {line} {points[-1][0]:.1f},{bottom:.1f}"
        parts.append(f'<polygon points="{area}" fill="{t["series"]}" fill-opacity="0.12"/>')
        parts.append(
            f'<polyline points="{line}" fill="none" stroke="{t["series"]}" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )

        # Direct label on the busiest week only; the latest week is in the subtitle.
        peak_i = max(range(n), key=lambda i: data[i][1])
        marks = {peak_i: f"Peak · {data[peak_i][1]} (week of {data[peak_i][0]:%b %d})"}
        for i, text in marks.items():
            x, y = points[i]
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{t["series"]}" '
                f'stroke="{t["surface"]}" stroke-width="2"/>'
            )
            anchor = "end" if x > right - 140 else ("start" if x < left + 140 else "middle")
            ty = y - 12 if y - 12 > top - 6 else y + 20
            parts.append(f'<text class="annot" x="{x:.1f}" y="{ty:.1f}" text-anchor="{anchor}">{escape(text)}</text>')

    return svg_document(WIDTH, height, "Weekly contribution activity", "\n".join(parts), t)


def render_languages(p: dict, t: dict, exclude: set[str], top_n: int = 6) -> str:
    langs = sorted(
        ((k, v) for k, v in p["languages"].items() if k not in exclude),
        key=lambda kv: kv[1],
        reverse=True,
    )
    total = sum(v for _, v in langs) or 1
    shown = langs[:top_n]
    rest = sum(v for _, v in langs[top_n:])
    if rest:
        shown.append(("Other", rest))

    bar_h, gap, top = 14, 16, 80
    height = top + len(shown) * (bar_h + gap) + 16
    label_w, left, right = 120, 24, WIDTH - 80
    track_x = left + label_w
    track_w = right - track_x
    note = f" · excluding {', '.join(sorted(exclude))}" if exclude else ""
    parts = [header("Most used languages", f"Share of code across my repositories{note}")]
    max_share = max((v / total for _, v in shown), default=1)
    for i, (name, size) in enumerate(shown):
        y = top + i * (bar_h + gap)
        share = size / total
        w = max(track_w * share / max_share, 4)
        parts.append(f'<text class="label" x="{left}" y="{y + bar_h - 2}">{escape(name)}</text>')
        # Rounded data-end, square at the baseline.
        r = 4
        parts.append(
            f'<path d="M{track_x},{y} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{bar_h - 2 * r} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z" fill="{t["series"]}"/>'
        )
        parts.append(
            f'<text class="value" x="{track_x + w + 8:.1f}" y="{y + bar_h - 2}">{share * 100:.1f}%</text>'
        )
    return svg_document(WIDTH, height, "Most used languages", "\n".join(parts), t)


# --------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", default=os.environ.get("GITHUB_USER", "gurbuzf"))
    parser.add_argument("--out", default="dist")
    parser.add_argument("--sample", action="store_true", help="render with fake data (no network)")
    args = parser.parse_args()

    exclude = {
        s.strip()
        for s in os.environ.get("EXCLUDED_LANGUAGES", "Jupyter Notebook").split(",")
        if s.strip()
    }

    if args.sample:
        profile = sample_profile()
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is required (or pass --sample)")
        profile = fetch_profile(args.user, token)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for mode, theme in THEMES.items():
        suffix = "" if mode == "light" else "-dark"
        (out / f"activity{suffix}.svg").write_text(render_activity(profile, theme))
        (out / f"languages{suffix}.svg").write_text(render_languages(profile, theme, exclude))
    print(f"Wrote charts for @{profile['login']} to {out}/")


if __name__ == "__main__":
    main()
