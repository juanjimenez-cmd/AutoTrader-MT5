"""Broker-independent symbol auto-discovery."""

from __future__ import annotations

import re


class SymbolResolutionError(ValueError):
    pass


def normalize_symbol(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


class SymbolResolver:
    def __init__(self, aliases: dict[str, tuple[str, ...]]):
        self.aliases = aliases

    def resolve(self, canonical: str, broker_symbols: list[str] | tuple[str, ...]) -> str:
        aliases = tuple(normalize_symbol(item) for item in self.aliases.get(canonical, (canonical,)))
        candidates: list[tuple[int, int, int, str]] = []
        for broker_symbol in broker_symbols:
            normalized = normalize_symbol(broker_symbol)
            for alias_index, alias in enumerate(aliases):
                if normalized == alias:
                    rank = 300
                elif normalized.startswith(alias) or normalized.endswith(alias):
                    rank = 200 - abs(len(normalized) - len(alias))
                elif alias in normalized:
                    rank = 100 - abs(len(normalized) - len(alias))
                else:
                    continue
                # Alias order is authoritative. The canonical name is first and
                # must beat an exact but ambiguous secondary alias such as GOLD.
                candidates.append((-alias_index, rank, -len(broker_symbol), broker_symbol))
        if not candidates:
            raise SymbolResolutionError(f"No broker symbol found for {canonical}; aliases={aliases}")
        candidates.sort(reverse=True)
        return candidates[0][3]

    def resolve_all(
        self, canonical_symbols: tuple[str, ...], broker_symbols: list[str] | tuple[str, ...]
    ) -> tuple[dict[str, str], dict[str, str]]:
        resolved: dict[str, str] = {}
        errors: dict[str, str] = {}
        for canonical in canonical_symbols:
            try:
                resolved[canonical] = self.resolve(canonical, broker_symbols)
            except SymbolResolutionError as error:
                errors[canonical] = str(error)
        return resolved, errors
