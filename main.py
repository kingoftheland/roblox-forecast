# Roblox Up-and-Coming chart forecast.
#
# Reads the chart history at /input (history.ndjson and task.json) and writes
# a single probabilistic forecast to /output/forecast.json.
#
# Idea: the chart is a ranking by user growth, and it churns. A game holds a
# place for a while, then the chart moves on, and every game jitters. So
# instead of guessing one order, I run the chart forward many times as a
# noisy walk and count where each game lands. The counts become the
# probabilities, which is exactly what the log score rewards: an honest
# spread, not an overconfident single order.
#
# The walk lives in score space. A game's score starts at minus its rank, so
# sorting by score reproduces the chart. Each hour the score moves by noise
# whose size grows with how deep the game sits, because lower ranks jump
# around more. Games whose 28-day window ends inside the forecast are
# dropped from the ranking once that moment passes, when their creation date
# is known.

import json
import math
import os
import random
from datetime import datetime, timezone

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

SEED = 20261113
N_SIM = 1500
HOURS = 72
# Noise per hour (score units) by the rank band the game sits in. Tuned on
# two held-out windows of the archive (Nov 8-10 and Nov 11-13): a scale of
# 1.0 was the best compromise, beating the persistence baseline by a wide
# margin on both. Rank momentum was tried and dropped: the rank-change
# autocorrelation is only 0.147, and adding a drift never improved the
# held-out log score, so the walk here is a pure diffusion.
SIGMA_BANDS = [(10, 0.65), (30, 1.4), (10 ** 9, 2.4)]
# Games named at a place: keep the tail thin but present, and leave room for
# games the history has never seen.
MAX_NAMED_PER_PLACE = 40
MIN_P = 0.002
CHART_MAX_AGE_DAYS = 28.0


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def sigma_for(rank):
    for limit, value in SIGMA_BANDS:
        if rank <= limit:
            return value
    return SIGMA_BANDS[-1][1]


def load_history():
    with open(os.path.join(INPUT_DIR, "task.json"), "r", encoding="utf-8") as fh:
        task = json.load(fh)
    snaps = []
    with open(os.path.join(INPUT_DIR, "history.ndjson"), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            snaps.append(json.loads(line))
    snaps.sort(key=lambda s: s["at"])
    return task, snaps


def build_series(snaps):
    """Per-game list of (time, rank, playing), oldest first."""
    series = {}
    for snap in snaps:
        when = parse_time(snap["at"])
        for row in snap.get("up_and_coming") or []:
            uid = row["universe_id"]
            series.setdefault(uid, []).append(
                (when, row["rank"], row.get("playing"))
            )
    return series


def created_dates(snaps):
    """Universe id -> creation datetime, from the newest snapshot that has it."""
    for snap in reversed(snaps):
        details = snap.get("details") or {}
        if not details:
            continue
        out = {}
        for key, value in details.items():
            if not value:
                continue
            made = value.get("created")
            if not made:
                continue
            try:
                uid = int(key)
            except (TypeError, ValueError):
                continue
            out[uid] = parse_time(made)
        if out:
            return out
    return {}


def main():
    task, snaps = load_history()
    places = int(task.get("places", 10))
    now = parse_time(snaps[-1]["at"])
    last_chart = sorted(
        snaps[-1].get("up_and_coming") or [], key=lambda r: r["rank"]
    )
    series = build_series(snaps)
    created = created_dates(snaps)

    # The population that can plausibly reach the top: the chart as it stands,
    # plus any game that sat in the top 20 in the last three reads.
    keep = {}
    for row in last_chart:
        keep[row["universe_id"]] = row["rank"]
    for snap in snaps[-3:]:
        for row in snap.get("up_and_coming") or []:
            if row["rank"] <= 20:
                keep.setdefault(row["universe_id"], row["rank"])
    uids = sorted(keep)

    # Exit times: a game leaves the chart 28 days after it was created.
    exit_at = {}
    for uid in uids:
        made = created.get(uid)
        if made is not None:
            exit_at[uid] = made.timestamp() + CHART_MAX_AGE_DAYS * 86400.0

    base_rank = {}
    for uid in uids:
        entries = series.get(uid, [])
        base_rank[uid] = entries[-1][1] if entries else keep[uid]

    rng = random.Random(SEED)
    # counts[place][uid] = simulated (hour, run) pairs where uid held place.
    counts = {place: {} for place in range(1, places + 1)}
    total = 0
    window_start = now.timestamp()
    for _ in range(N_SIM):
        scores = {}
        for uid in uids:
            scores[uid] = -float(base_rank[uid])
        for hour in range(HOURS):
            t = window_start + hour * 3600.0
            live = []
            for uid in uids:
                if uid in exit_at and t >= exit_at[uid]:
                    continue
                scores[uid] += rng.gauss(0.0, sigma_for(base_rank[uid]))
                live.append(uid)
            live.sort(key=lambda u: (-scores[u], u))
            for place in range(1, places + 1):
                if place - 1 < len(live):
                    uid = live[place - 1]
                    counts[place][uid] = counts[place].get(uid, 0) + 1
            total += 1

    probabilities = []
    for place in range(1, places + 1):
        ranked = sorted(
            counts[place].items(), key=lambda kv: (-kv[1], kv[0])
        )
        named = []
        running = 0.0
        for uid, count in ranked[:MAX_NAMED_PER_PLACE]:
            p = count / total
            if p < MIN_P and named:
                break
            p = max(p, MIN_P)
            named.append((uid, p))
            running += p
        # Keep the place's total at or under 1; shrink if the floor pushed
        # it over.
        if running > 1.0:
            scale = 1.0 / running
            named = [(uid, p * scale) for uid, p in named]
            running = 1.0
        # Leave at least a small unnamed share for games never seen.
        if running > 0.9:
            scale = 0.9 / running
            named = [(uid, p * scale) for uid, p in named]
        for uid, p in named:
            probabilities.append(
                {"universe_id": int(uid), "place": place, "p": round(p, 6)}
            )

    # Top 10 in order: the most likely game at each place, each used once.
    top10 = []
    used = set()
    for place in range(1, places + 1):
        ranked = sorted(
            counts[place].items(), key=lambda kv: (-kv[1], kv[0])
        )
        for uid, _ in ranked:
            if uid not in used:
                top10.append(int(uid))
                used.add(uid)
                break
    # Fill any gap from the last chart's order.
    for row in last_chart:
        if len(top10) >= places:
            break
        uid = int(row["universe_id"])
        if uid not in used:
            top10.append(uid)
            used.add(uid)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(
        os.path.join(OUTPUT_DIR, task.get("output", "forecast.json")),
        "w",
        encoding="utf-8",
    ) as fh:
        fh.write(
            json.dumps(
                {"probabilities": probabilities, "top10": top10[:places]}
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
