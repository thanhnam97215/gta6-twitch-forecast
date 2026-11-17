# GTA VI launch-day Twitch forecast

[![make.fun](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/thanhnam97215/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/thanhnam97215/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)

Entry for the make.fun bounty [Forecast GTA VI's launch-day Twitch viewers hour by hour](https://make.fun/bounty/gta6-twitch-ccu-forecast-01).

`main.py` (Python 3.13, standard library only, no network) reads one case —
everything recorded up to the end of the release day's first hour, that hour's
Twitch viewer count included — and forecasts the viewer count for each of hours
2–24 as five quantiles (5%, 25%, 50%, 75%, 95%).

## How it works

A launch day's shape is driven by the unlock rolling around the world at local
midnight: Europe and Oceania are already playing when the UTC day starts, the
US East Coast unlocks at 05:00Z and the West Coast at 08:00Z, and the audience
peaks in the US/European evening. That shape is stable across launches once
each launch is scaled by its own hour-1 print, so the forecast is

    hour h viewers = hour-1 print x ratio_h

where `ratio_h` comes from the comparable launches shipped in the case file,
each adjusted to this case before they are combined:

- **Selection.** Only launches whose hour 1 already carries a real audience
  (day peak at most 15x the hour-1 print, no single-hour jump above 3x).
  Launches that unlock all at once later in the day have a near-zero hour 1
  and a wildly different shape; GTA VI unlocks at rolling local midnight, so
  they are not analogues. Early-access-contaminated, weak and target-history
  comparables are left out too.
- **Weighting.** Console-only launches weigh 1, launches with a PC version
  weigh 0.5 (a simultaneous PC unlock distorts the curve), times a Gaussian
  kernel on log hour-1 size, so launches of a similar scale matter most.
- **Level adjustment.** Bigger hour-1 prints mean flatter days — a huge first
  hour already holds most of the launch audience — so each comparable's log
  ratio is shifted by −0.2 × (log hour-1 of the case − log hour-1 of the
  comparable).
- **Front-loading adjustment.** A launch whose hour 1 towers over the hour
  before it (the UK unlock step) tends to stay flatter afterwards, so the log
  ratios shift by −0.3 × the difference in that step, capped at ±0.35.
- **Centre and spread.** Per hour, the weighted median of the adjusted log
  ratios is the median; the weighted standard deviation around it, floored at
  0.3 log units and widened by 1.1, gives the five quantiles through the
  normal quantiles. The median curve's peak is kept at or above the hour-1
  print, as it must be by definition.

If no comparable survives selection, the program falls back to a persistence
forecast (every hour at the hour-1 print, spread widening like a random walk),
so it always writes a valid forecast. The output is fully deterministic: no
randomness, sorted iteration, fixed rounding.

## Validation

The practice cases' true answers are scored server-side only, so the model was
tuned and validated by leave-one-out over the comparables themselves: each of
the 19 h1-informative past launches was treated as a pseudo-case (hour-1 print
known, hours 2–24 scored against its actual curve), forecast from the other
comparables. Mean pinball loss on the log scale:

| | this entry | house persistence baseline |
| --- | --- | --- |
| rolling-like launches (n=19) | **0.256** | 0.403 |
| core launches, peak/hour-1 ≤ 6 (n=16) | **0.179** | 0.288 |

90% coverage on the rolling-like set is 0.85. Runtime is about 0.1 s per case
(limit 8 s).

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

The grader runs `python3 -P -s /bot/main.py` with the case in `/input` and
expects `/output/<id>.out`.
