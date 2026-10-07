# ES opening-range experiment — fixed before computing results

Exploratory only: prior project research has examined Dec 2025–Jul 2026.
No portion of these data is a pristine holdout. Do not alter platform splits.

Use ES outright selected by volume accumulated before 09:30 New York on each
UTC date, not the platform's hindsight full-day-volume front-month mapping.
Require all 390 regular-session minute bars (09:30–15:59); report exclusions.
Opening range: 09:30 inclusive through 09:45 exclusive. Freeze high, low, open,
close, volume and delta then. VWAP is cumulative trade-price-times-size / size
starting at 09:30, known only after each completed signal minute.

Screen exactly 40 rules: four families × five filters × two targets (2R, 3R).
Families, both directions symmetrically:
- breakout: first eligible close outside the opening range.
- retest: a prior close outside, then a later bar touches that boundary and
  closes back outside it; breakout state resets after a close inside.
- failed_break: a prior close outside, then a close back inside; fade the break.
- open_retest: previous close at least 0.25 OR widths above/below the 09:30
  open, then a bar touches the open and closes on the approach side; trade bounce.
Filters: none; price on direction-consistent side of VWAP; signal-minute and
trailing-three-minute aggressor delta both direction-consistent; both; both
plus opening volume >= median of previous 20 eligible sessions (min 10).

Signal minutes 09:45–11:28, next-minute-open entry before 11:30, one trade/day.
Stop distance max(4 ES points, half opening width), rounded up to whole ticks;
2R/3R target. Brackets anchored to slipped entry, fixed one ES contract.
Force exit at 15:58 open. Entry and stop/forced-exit slippage one tick each;
commissions read from instruments.yaml. Target must trade through by one tick.
Bar simulation: include entry bar, stops first if both touched, gap stops use
worse of stop/open, then slippage. No parameter changes after observing results.

Select highest development PF among rules with >=30 development trades using
Dec–Apr only. Report May–Jul chronological diagnostic without reselecting.
Replay that selected rule on prints; use first print >= next-bar start +250ms,
activate brackets on subsequent prints, stop fills at triggering print minus
slippage, targets require one-tick trade-through. This is an independent
research simulator, not a platform/Nautilus validation or queue model.
Report all trials, monthly performance, cost stress (2 ticks), remove-five-best
sensitivity, and moving-block bootstrap uncertainty over daily PnL (5 days).

Also report 09:45 directional diagnostics: open-to-close direction, price
relative to opening VWAP, and opening delta predicting the first hit of
OR high +0.5 width versus OR low -0.5 width before noon. Unresolved and
same-minute ambiguous outcomes are reported separately; these are not fills.
