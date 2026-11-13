# Roblox Up-and-Coming Chart Forecast

A probabilistic forecast for the Roblox "Up-and-Coming" chart — which games are
rising fastest by user growth. Built as an entry for the make.fun competition
`roblox-up-and-coming-forecast-01`.

## Idea

The chart is a ranking by user growth, and it churns: a game holds a place for
a while, then the chart moves on, and every game jitters. Instead of guessing
one order, this program runs the chart forward many times as a noisy walk and
counts where each game lands. The counts become the probabilities — an honest
spread, not an overconfident single order, which is exactly what the log score
rewards.

The walk lives in score space. A game's score starts at minus its rank, so
sorting by score reproduces the chart. Each hour the score moves by noise whose
size grows with how deep the game sits, because lower ranks jump around more.
Games whose 28-day window ends inside the forecast are dropped from the ranking
once that moment passes, when their creation date is known.

## Method

- Monte Carlo rank diffusion in score space (pure diffusion — momentum was
  tested and dropped: rank-change autocorrelation is only ~0.147).
- Sigma bands by rank: (10, 0.65), (30, 1.4), (inf, 2.4).
- 1,500 simulations × 72 hours, seeded (`SEED = 20261113`), deterministic.
- 28-day exit via creation dates.

## Usage

```bash
python3 main.py
```

Reads chart history from `/input` (`history.ndjson` and `task.json`, or set
`MAKEFUN_INPUT_DIR`) and writes a single probabilistic forecast to
`/output/forecast.json` (or set `MAKEFUN_OUTPUT_DIR`). Output: 340
probability rows, top-10 with 10 distinct ids, each place summing to 0.9.
Python 3.13 standard library only, no dependencies.

## Validation

Local scorer (verified to reproduce the published baseline exactly):

| Window | This model | Persistence baseline |
|---|---|---|
| Practice Nov 8–10 | −34.90 | −43.51 |
| Second split Nov 11–13 | −35.58 | −59.49 |

It beats the persistence baseline by a wide margin on both windows. Output is
deterministic (two runs byte-identical).

## Competition

Submitted to make.fun `roblox-up-and-coming-forecast-01` ($500 prize pool) as
"Rank Diffusion Forecast". Entries closed 2026-11-13 13:00 WIB.

## License

MIT
