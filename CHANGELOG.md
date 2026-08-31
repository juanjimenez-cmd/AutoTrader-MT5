# Changelog

## Unreleased

- Block new Forex, gold, and US-index entries during a configurable conservative weekend closure window.
- Keep cryptocurrency entry handling separate from the weekend guard.
- Add deterministic session-boundary and engine-level regression tests.
- Normalize macOS bridge server timestamps to UTC without changing native Windows timestamps.
- Reject stale ticks and closed candles before scoring or order preparation.
- Prefer canonical aliases and reject equity symbols incorrectly resolved as XAUUSD spot gold.
- Remove unavailable BTCUSD and ETHUSD from the MetaQuotes-Demo live scanner defaults.
- Reduced the default and per-asset DEMO risk to 0.10%.
- Added a 25% projected deposit-load cap using MetaTrader margin calculation before order submission.
- Changed the daily-loss gate to reserve open and proposed risk before admitting a trade.
- Reduced the maximum portfolio to two positions and 0.50% simultaneous risk.
- Temporarily disabled GBPUSD and USDJPY in the default live scanner.
- Added broker trade-mode preflight plus tick-size and minimum-stop normalization.
- Instruments rejected as `Trade disabled` are blocked until restart instead of retried every minute.
- Documented the 100–200 closed-trade DEMO validation criteria.
