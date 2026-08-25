"""TOML configuration with strict demo-only validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


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


@dataclass(frozen=True, slots=True)
class AssetProfile:
    risk_percent: float = 0.50
    atr_stop_multiplier: float = 1.5
    reward_risk: float = 2.0
    group: str = "usd"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    default_risk_percent: float = 0.50
    daily_loss_limit_percent: float = 2.0
    max_simultaneous_risk_percent: float = 3.0
    max_positions: int = 5
    max_group_risk_percent: dict[str, float] = field(
        default_factory=lambda: {"usd": 1.5, "us_indices": 1.0, "crypto": 1.0}
    )


@dataclass(frozen=True, slots=True)
class ManagementConfig:
    breakeven_at_r: float = 1.0
    trailing_start_at_r: float = 1.5
    trailing_atr_multiplier: float = 1.25


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
    mt5: MT5ConnectionConfig
    profiles: dict[str, AssetProfile]
    aliases: dict[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        if self.demo_only is not True:
            raise ValueError("AutoTrader-MT5 v1 refuses to start unless demo_only=true")
        if not 0 <= self.min_score <= 100:
            raise ValueError("min_score must be between 0 and 100")
        if set(self.timeframes) - {"M5", "M15"}:
            raise ValueError("v1 supports only M5 and M15")
        if self.risk.daily_loss_limit_percent <= 0 or self.risk.max_positions <= 0:
            raise ValueError("risk limits must be positive")

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
    mt5_raw = raw.get("mt5", {})
    profiles_raw = raw.get("profiles", {})
    aliases_raw = raw.get("aliases", {})

    risk = RiskConfig(
        default_risk_percent=float(risk_raw.get("default_risk_percent", 0.50)),
        daily_loss_limit_percent=float(risk_raw.get("daily_loss_limit_percent", 2.0)),
        max_simultaneous_risk_percent=float(risk_raw.get("max_simultaneous_risk_percent", 3.0)),
        max_positions=int(risk_raw.get("max_positions", 5)),
        max_group_risk_percent={
            str(k): float(v)
            for k, v in risk_raw.get(
                "max_group_risk_percent", {"usd": 1.5, "us_indices": 1.0, "crypto": 1.0}
            ).items()
        },
    )
    management = ManagementConfig(
        breakeven_at_r=float(management_raw.get("breakeven_at_r", 1.0)),
        trailing_start_at_r=float(management_raw.get("trailing_start_at_r", 1.5)),
        trailing_atr_multiplier=float(management_raw.get("trailing_atr_multiplier", 1.25)),
    )
    mt5 = MT5ConnectionConfig(
        backend=str(mt5_raw.get("backend", "auto")).lower(),
        bridge_host=str(mt5_raw.get("bridge_host", "127.0.0.1")),
        bridge_port=int(mt5_raw.get("bridge_port", 18813)),
    )
    profiles: dict[str, AssetProfile] = {}
    for symbol in bot.get("symbols", DEFAULT_ALIASES):
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
        symbols=tuple(map(str, bot.get("symbols", DEFAULT_ALIASES.keys()))),
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
        mt5=mt5,
        profiles=profiles,
        aliases=aliases,
    )
