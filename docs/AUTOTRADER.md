# AutoTrader-MT5 v1

AutoTrader-MT5 is a DEMO-only, multi-asset scanner and execution layer built directly inside the
[`aiomql`](https://github.com/Ichinga-Samuel/aiomql) codebase. It prioritizes auditable architecture and
risk controls; it does not claim or optimize profitability.

## Safety boundary

Version 1 cannot be configured for real-money execution. It rejects `demo_only=false`, validates
MetaTrader's authoritative `ACCOUNT_TRADE_MODE_DEMO` value after login, revalidates it on every account read,
and checks again immediately before order submission or stop modification. Never place real credentials in
`autotrader.credentials.json`.

## Architecture

- `config.py`: typed TOML configuration and asset profiles.
- `symbols.py`: exact/prefix/suffix alias resolution for broker-specific names.
- `strategies/`: independent trend, breakout, momentum, and mean-reversion votes.
- `signals.py`: shared M5/M15 ensemble and normalized 0-100 score.
- `risk.py`: prospective daily loss, margin load, position, portfolio-risk, and correlation-group gates.
- `engine.py`: concurrent market-data scanner with sequential risk-aware execution.
- `management.py`: breakeven and ATR trailing stops, restricted to this bot's magic number.
- `mt5_runtime.py`: platform selector for official Windows MT5 or the local macOS bridge.
- `mt5_adapter.py`: one async broker interface shared by both platform transports.
- `storage.py`: append-only JSONL plus SQLite events.
- `backtest.py`: conservative simulator using the same signal and risk classes as live mode.

The scanner downloads symbols concurrently. Qualified signals are then sorted by score and handled one at a
time; accepted orders are immediately included in the in-memory portfolio, preventing a burst of signals from
bypassing simultaneous or group risk limits.

## Common preparation

Install Python 3.13 or newer, install MetaTrader 5, create a DEMO account, and clone this repository. Copy the
credential template without committing the resulting file:

```text
configs/autotrader.credentials.example.json -> autotrader.credentials.json
```

Fill `login`, `password`, and `server` with the DEMO account. The optional `path` is the Windows path to
`terminal64.exe`; the sample value also matches the standard path inside the macOS Wine environment.

## Windows live DEMO

Windows uses MetaQuotes' official Python package and talks directly to the open local terminal.

```powershell
git clone https://github.com/juanjimenez-cmd/AutoTrader-MT5.git
cd AutoTrader-MT5
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install ".[windows]"
Copy-Item configs\autotrader.credentials.example.json autotrader.credentials.json
autotrader-mt5 doctor --config configs/autotrader.toml
```

Open MetaTrader 5, log in to that same DEMO account, and enable the **Algo Trading** toolbar button.

## macOS live DEMO

The official `MetaTrader5` Python wheel is Windows-only. On macOS this project uses
[`mt5-mac-bridge`](https://github.com/theauheral/mt5-mac-bridge) 0.1.x to reach the official MetaTrader 5 Mac
app through its local Wine environment. This is a community-maintained beta layer, so v1 permits it only
because the entire bot is DEMO-only.

```bash
git clone https://github.com/juanjimenez-cmd/AutoTrader-MT5.git
cd AutoTrader-MT5
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install '.[macos]'
cp configs/autotrader.credentials.example.json autotrader.credentials.json
./scripts/macos_bridge.sh provision
```

The helper downloads the bridge source at pinned revision
`1e8450748d0eaea47a324bbb8d77238061c67bd2`, then provisions Windows Python and the official MT5 package
inside the existing MetaTrader app environment. Review `scripts/macos_bridge.sh` before running it. Open the
MetaTrader 5 app, log in to the DEMO account once, and enable **Algo Trading**. Keep this command open in a
second Terminal window:

```bash
./scripts/macos_bridge.sh serve
```

Back in the project Terminal, verify the dependency, credentials, and local bridge:

```bash
autotrader-mt5 doctor --config configs/autotrader.toml
```

The default bridge address is `127.0.0.1:18813`; change `[mt5]` in the TOML only if the local bridge uses a
different address. AutoTrader never falls back to simulated/mock market data in live mode.

## First live cycle on either platform

Verify that MetaTrader shows the intended DEMO account. Credentials and logs are ignored by Git. Run one
non-repeating cycle first:

```bash
autotrader-mt5 live --config configs/autotrader.toml --once
```

Then inspect `logs/events.jsonl` or `logs/autotrader.sqlite3`. To run continuously:

```bash
autotrader-mt5 live --config configs/autotrader.toml
```

Stop with `Ctrl+C`. Missing or non-tradable broker instruments are logged and skipped before scanning; the bot
stops if none remain.

## Backtesting

Backtesting does not connect to MetaTrader and runs on both Windows and macOS with Python 3.13+. Supply M5 CSV data with
`time,open,high,low,close` and optional `volume` or `tick_volume`. `time` may be a Unix timestamp or ISO-8601.
M15 bars are derived from M5, and the unfinished current bar is never included in a decision.

```bash
PYTHONPATH=src python -m autotrader_mt5 backtest \
  --config configs/autotrader.toml \
  --csv data/EURUSD_M5.csv \
  --symbol EURUSD \
  --output backtesting/EURUSD-report.json
```

The simulator allows one position per symbol, sizes P&L from configured percentage risk, and assumes the stop
was hit before the target when both prices occur inside a single candle. It does not model spread, commission,
slippage, swaps, partial fills, news gaps, or broker latency. Add these before using results for decisions.

## Configuration

`configs/autotrader.toml` contains the `auto` platform transport, macOS bridge endpoint, v1 market profiles,
enabled markets, M5/M15 timeframes, minimum score, scan cadence, asset risk, ATR stops, reward/risk ratios,
daily loss, total exposure, maximum deposit load, maximum positions, USD/index/crypto group limits, breakeven,
trailing, weekend entry guard, and broker aliases. Percentages are percentage points: `0.10` means 0.10% of
equity.

Before scoring, the live engine rejects a symbol when its latest tick is more than 120 seconds old or its last
closed M5/M15 candle is older than two complete timeframe intervals plus the configured grace period. This
prevents Friday candles or stopped quotes from becoming executable signals when a market is closed. Native
Windows timestamps remain UTC. The macOS bridge exposes broker wall-clock timestamps, so
`market_data.bridge_server_timezone` converts them to UTC; change this IANA timezone if the broker does not use
the configured EET/EEST schedule.

The weekend guard blocks new `usd` and `us_indices` positions from Friday 20:30 UTC until Sunday 22:30 UTC.
This conservative window is configurable under `[sessions]`. It does not close positions or disable position
management, and it does not apply to the `crypto` group. Broker trading sessions remain authoritative: a symbol
can still be unavailable outside this window because of holidays, daily breaks, or broker-specific hours.

The conservative DEMO defaults are 0.10% risk per trade, 0.50% maximum simultaneous risk, 25% maximum
deposit load, two positions, and 0.50% total risk for the USD group. The daily gate reserves current open risk
and the proposed new risk before admitting an order, so a new position cannot intentionally overshoot the 2%
daily budget. Before sizing, prices and mandatory SL/TP levels are aligned to the broker tick size and minimum
stop distance. Required margin is calculated with MetaTrader and the projected deposit load is rejected before
`order_send` when it exceeds the configured cap. If the broker still responds `Trade disabled`, that instrument
is blocked until the bot restarts instead of being retried every minute.

GBPUSD and USDJPY are temporarily absent from `bot.symbols` after the initial DEMO report. Their profiles and
aliases remain available for controlled backtests and can be re-enabled only after review.

## DEMO promotion criteria

Do not relax the safety defaults until a comparable DEMO sample contains at least 100–200 closed trades and
meets all of these operational gates:

- profit factor above 1.20;
- maximum deposit load at or below 25%;
- no daily loss above 2%;
- no `No money`, `Invalid stops`, or `Trade disabled` order-check rejections;
- drawdown and symbol-level results reviewed separately, with no single instrument dominating portfolio loss.

These are validation gates, not a profitability guarantee, and v1 remains technically restricted to DEMO.

The score is based on vote conviction, agreement, and strategy/timeframe coverage. M15 and trend votes receive
slightly higher weights; mean reversion receives a lower weight because it naturally conflicts with trend
signals. A score is a filter, not a probability of profit.

## Verification

Run the dependency-free AutoTrader tests outside Windows:

```bash
PYTHONPATH=src python -m unittest discover -s tests_autotrader -v
```

Run the upstream aiomql suite on its supported Windows environment after installing development dependencies:

```powershell
pytest tests
```
