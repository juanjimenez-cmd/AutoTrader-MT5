"""TOML configuration with strict demo-only validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "EURUSD": ("EURUSD",),
    "GBPUSD": ("GBPUSD",),
    "USDJPY": ("USDJPY",),
    "XAUUSD": ("XAUUSD", "GOLD"),
    "NASDAQ": ("NASDAQ", "NAS100", "USTEC", "US100", "NDX"),
    "SP500": ("SP500", "SPX500", "US500", "SPX"),
    "BTCUSD": ("BTCUSD", "BTCUSDT", "BITCOIN"),
    "ETHUSD": ("ETHUSD", "ETHUSDT", "ETHEREUM"),
}

DEFAULT_GROUPS = {
    "EURUSD": "usd",
    "GBPUSD": "usd",
    "USDJPY": "usd",
    "XAUUSD": "usd",
    "NASDAQ": "us_indices",
    "SP500": "us_indices",
    "BTCUSD": "crypto",
    "ETHUSD": "crypto",
}

DEFAULT_ACTIVE_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NASDAQ", "SP500")


@dataclass(frozen=True, slots=True)
class AssetProfile:
    risk_percent: float = 0.10
    atr_stop_multiplier: float = 1.5
    reward_risk: float = 2.0
    group: str = "usd"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    default_risk_percent: float = 0.10
    daily_loss_limit_percent: float = 2.0
    max_simultaneous_risk_percent: float = 0.50
    max_deposit_load_percent: float = 25.0
    max_positions: int = 2
    max_group_risk_percent: dict[str, float] = field(
        default_factory=lambda: {"usd": 0.5, "us_indices": 0.4, "crypto": 0.3}
    )


@dataclass(frozen=True, slots=True)
class ManagementConfig:
    breakeven_at_r: float = 1.0
    trailing_start_at_r: float = 1.5
    trailing_atr_multiplier: float = 1.25


@dataclass(frozen=True, slots=True)
class EntryControlConfig:
    """Turnover controls applied before a new order can be submitted."""

    cooldown_minutes: int = 90
    max_entries_per_symbol_day: int = 2

    def __post_init__(self) -> None:
        if self.cooldown_minutes < 0:
            raise ValueError("entry_controls.cooldown_minutes must not be negative")
        if self.max_entries_per_symbol_day <= 0:
            raise ValueError("entry_controls.max_entries_per_symbol_day must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Controls whether a validated signal can reach the MT5 order API."""

    mode: str = "observation"

    def __post_init__(self) -> None:
        if self.mode not in {"observation", "demo"}:
            raise ValueError("execution.mode must be observation or demo")


@dataclass(frozen=True, slots=True)
class BacktestCostConfig:
    """EURUSD-style friction model used by the shared-signal backtest.

    It is intentionally explicit: this is an estimate, not a replacement for
    the broker's real-tick tester or a filled-order report.
    """

    spread_pips: float = 0.8
    slippage_pips_per_side: float = 0.1
    commission_per_lot_round_turn: float = 7.0
    pip_size: float = 0.0001
    contract_size: float = 100_000.0

    def __post_init__(self) -> None:
        values = (
            self.spread_pips,
            self.slippage_pips_per_side,
            self.commission_per_lot_round_turn,
            self.pip_size,
            self.contract_size,
        )
        if any(value < 0 for value in values) or self.pip_size == 0 or self.contract_size == 0:
            raise ValueError("backtest_costs must use non-negative costs and positive pip/contract sizes")


@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    bridge_server_timezone: str = "Europe/Helsinki"
    max_tick_age_seconds: int = 120
    closed_bar_grace_seconds: int = 90
    future_tolerance_seconds: int = 5

    def __post_init__(self) -> None:
        if not self.bridge_server_timezone:
            raise ValueError("market_data.bridge_server_timezone must not be empty")
        if self.max_tick_age_seconds <= 0 or self.closed_bar_grace_seconds < 0:
            raise ValueError("market-data freshness limits must be positive")
        if self.future_tolerance_seconds < 0:
            raise ValueError("market_data.future_tolerance_seconds must not be negative")


def _validate_clock(value: str, field_name: str) -> None:
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"{field_name} must use HH:MM")
    hour, minute = map(int, parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{field_name} must use HH:MM")


@dataclass(frozen=True, slots=True)
class EntrySchedule:
    """Local-time windows that permit new entries for one canonical symbol."""

    timezone: str
    windows: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown session timezone {self.timezone!r}") from error
        if not self.windows:
            raise ValueError("session entry schedule must contain at least one window")
        for window in self.windows:
            start, separator, end = window.partition("-")
            if separator != "-":
                raise ValueError("session entry windows must use HH:MM-HH:MM")
            _validate_clock(start, "session entry window start")
            _validate_clock(end, "session entry window end")
            if start == end:
                raise ValueError("session entry window start and end must differ")


@dataclass(frozen=True, slots=True)
class SessionConfig:
    weekend_guard_enabled: bool = True
    friday_entry_cutoff_utc: str = "20:30"
    sunday_entry_resume_utc: str = "22:30"
    guarded_groups: tuple[str, ...] = ("usd", "us_indices")
    entry_schedules: dict[str, EntrySchedule] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_clock(self.friday_entry_cutoff_utc, "sessions.friday_entry_cutoff_utc")
        _validate_clock(self.sunday_entry_resume_utc, "sessions.sunday_entry_resume_utc")
        if not self.guarded_groups:
            raise ValueError("sessions.guarded_groups must not be empty")


@dataclass(frozen=True, slots=True)
class MT5ConnectionConfig:
    backend: str = "auto"
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 18813

    def __post_init__(self) -> None:
        if self.backend not in {"auto", "native", "bridge"}:
            raise ValueError("mt5.backend must be auto, native, or bridge")
        if not 1 <= self.bridge_port <= 65535:
            raise ValueError("mt5.bridge_port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class AppConfig:
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    min_score: int
    scan_interval_seconds: int
    candle_count: int
    demo_only: bool
    magic_number: int
    credentials_file: Path
    log_directory: Path
    risk: RiskConfig
    management: ManagementConfig
    entry_controls: EntryControlConfig
    execution: ExecutionConfig
    backtest_costs: BacktestCostConfig
    market_data: MarketDataConfig
    sessions: SessionConfig
    mt5: MT5ConnectionConfig
    profiles: dict[str, AssetProfile]
    aliases: dict[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        if self.demo_only is not True:
            raise ValueError("AutoTrader-MT5 v1 refuses to start unless demo_only=true")
        if not 0 <= self.min_score <= 100:
            raise ValueError("min_score must be between 0 and 100")
        if set(self.timeframes) - {"M5", "M15", "H1"}:
            raise ValueError("v1 supports M5, M15, and H1 context candles only")
        if not {"M5", "M15", "H1"}.issubset(self.timeframes):
            raise ValueError("v2 trend strategy requires M5, M15, and H1 candles")
        if self.risk.daily_loss_limit_percent <= 0 or self.risk.max_positions <= 0:
            raise ValueError("risk limits must be positive")
        if not 0 < self.risk.max_deposit_load_percent <= 100:
            raise ValueError("max_deposit_load_percent must be between 0 and 100")

    def profile_for(self, symbol: str) -> AssetProfile:
        return self.profiles.get(
            symbol,
            AssetProfile(risk_percent=self.risk.default_risk_percent, group=DEFAULT_GROUPS.get(symbol, "usd")),
        )


def _as_path(value: str, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    bot = raw.get("bot", {})
    risk_raw = raw.get("risk", {})
    management_raw = raw.get("management", {})
    entry_controls_raw = raw.get("entry_controls", {})
    execution_raw = raw.get("execution", {})
    backtest_costs_raw = raw.get("backtest_costs", {})
    market_data_raw = raw.get("market_data", {})
    sessions_raw = raw.get("sessions", {})
    mt5_raw = raw.get("mt5", {})
    profiles_raw = raw.get("profiles", {})
    aliases_raw = raw.get("aliases", {})

    risk = RiskConfig(
        default_risk_percent=float(risk_raw.get("default_risk_percent", 0.10)),
        daily_loss_limit_percent=float(risk_raw.get("daily_loss_limit_percent", 2.0)),
        max_simultaneous_risk_percent=float(risk_raw.get("max_simultaneous_risk_percent", 0.50)),
        max_deposit_load_percent=float(risk_raw.get("max_deposit_load_percent", 25.0)),
        max_positions=int(risk_raw.get("max_positions", 2)),
        max_group_risk_percent={
            str(k): float(v)
            for k, v in risk_raw.get(
                "max_group_risk_percent", {"usd": 0.5, "us_indices": 0.4, "crypto": 0.3}
            ).items()
        },
    )
    management = ManagementConfig(
        breakeven_at_r=float(management_raw.get("breakeven_at_r", 1.0)),
        trailing_start_at_r=float(management_raw.get("trailing_start_at_r", 1.5)),
        trailing_atr_multiplier=float(management_raw.get("trailing_atr_multiplier", 1.25)),
    )
    entry_controls = EntryControlConfig(
        cooldown_minutes=int(entry_controls_raw.get("cooldown_minutes", 90)),
        max_entries_per_symbol_day=int(entry_controls_raw.get("max_entries_per_symbol_day", 2)),
    )
    execution = ExecutionConfig(mode=str(execution_raw.get("mode", "observation")).lower())
    backtest_costs = BacktestCostConfig(
        spread_pips=float(backtest_costs_raw.get("spread_pips", 0.8)),
        slippage_pips_per_side=float(backtest_costs_raw.get("slippage_pips_per_side", 0.1)),
        commission_per_lot_round_turn=float(
            backtest_costs_raw.get("commission_per_lot_round_turn", 7.0)
        ),
        pip_size=float(backtest_costs_raw.get("pip_size", 0.0001)),
        contract_size=float(backtest_costs_raw.get("contract_size", 100_000.0)),
    )
    market_data = MarketDataConfig(
        bridge_server_timezone=str(market_data_raw.get("bridge_server_timezone", "Europe/Helsinki")),
        max_tick_age_seconds=int(market_data_raw.get("max_tick_age_seconds", 120)),
        closed_bar_grace_seconds=int(market_data_raw.get("closed_bar_grace_seconds", 90)),
        future_tolerance_seconds=int(market_data_raw.get("future_tolerance_seconds", 5)),
    )
    schedules_raw = sessions_raw.get("entry_schedules", {})
    if not isinstance(schedules_raw, dict):
        raise ValueError("sessions.entry_schedules must be a TOML table")
    entry_schedules: dict[str, EntrySchedule] = {}
    for symbol, schedule_raw in schedules_raw.items():
        if not isinstance(schedule_raw, dict):
            raise ValueError(f"sessions.entry_schedules.{symbol} must be a TOML table")
        entry_schedules[str(symbol)] = EntrySchedule(
            timezone=str(schedule_raw.get("timezone", "America/Guayaquil")),
            windows=tuple(map(str, schedule_raw.get("windows", ()))),
        )
    sessions = SessionConfig(
        weekend_guard_enabled=bool(sessions_raw.get("weekend_guard_enabled", True)),
        friday_entry_cutoff_utc=str(sessions_raw.get("friday_entry_cutoff_utc", "20:30")),
        sunday_entry_resume_utc=str(sessions_raw.get("sunday_entry_resume_utc", "22:30")),
        guarded_groups=tuple(map(str, sessions_raw.get("guarded_groups", ("usd", "us_indices")))),
        entry_schedules=entry_schedules,
    )
    mt5 = MT5ConnectionConfig(
        backend=str(mt5_raw.get("backend", "auto")).lower(),
        bridge_host=str(mt5_raw.get("bridge_host", "127.0.0.1")),
        bridge_port=int(mt5_raw.get("bridge_port", 18813)),
    )
    profiles: dict[str, AssetProfile] = {}
    for symbol in bot.get("symbols", DEFAULT_ACTIVE_SYMBOLS):
        item = profiles_raw.get(symbol, {})
        profiles[symbol] = AssetProfile(
            risk_percent=float(item.get("risk_percent", risk.default_risk_percent)),
            atr_stop_multiplier=float(item.get("atr_stop_multiplier", 1.5)),
            reward_risk=float(item.get("reward_risk", 2.0)),
            group=str(item.get("group", DEFAULT_GROUPS.get(symbol, "usd"))),
        )
    aliases = {**DEFAULT_ALIASES}
    aliases.update({str(k): tuple(map(str, v)) for k, v in aliases_raw.items()})
    base = config_path.parent
    return AppConfig(
        symbols=tuple(map(str, bot.get("symbols", DEFAULT_ACTIVE_SYMBOLS))),
        timeframes=tuple(map(str, bot.get("timeframes", ("M5", "M15")))),
        min_score=int(bot.get("min_score", 65)),
        scan_interval_seconds=int(bot.get("scan_interval_seconds", 60)),
        candle_count=int(bot.get("candle_count", 120)),
        demo_only=bool(bot.get("demo_only", True)),
        magic_number=int(bot.get("magic_number", 26082601)),
        credentials_file=_as_path(str(bot.get("credentials_file", "autotrader.credentials.json")), base),
        log_directory=_as_path(str(bot.get("log_directory", "logs")), base),
        risk=risk,
        management=management,
        entry_controls=entry_controls,
        execution=execution,
        backtest_costs=backtest_costs,
        market_data=market_data,
        sessions=sessions,
        mt5=mt5,
        profiles=profiles,
        aliases=aliases,
    )
