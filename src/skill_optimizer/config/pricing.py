"""Loader for ``config/pricing.yaml``.

Parses Anthropic public per-model rates into a typed ``Pricing`` lookup.
``Pricing.cost_for(model, bucket, tokens)`` is the only call site.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


_DATED_SUFFIX_RE = re.compile(r"-\d{8}$")


def _strip_dated_suffix(model: str) -> str:
    """Strip Anthropic's ``-YYYYMMDD`` suffix to recover the alias.

    The SDK accepts aliases like ``claude-haiku-4-5`` but the API resolves and
    records them as dated IDs like ``claude-haiku-4-5-20251001`` in trace data.
    """
    return _DATED_SUFFIX_RE.sub("", model)


_DEFAULT_PRICING_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"
)


@dataclass(frozen=True)
class ModelRates:
    """Per-Mtok rates for one model's four token buckets."""
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_creation_per_mtok: float


@dataclass(frozen=True)
class Pricing:
    """Static per-model rate lookup. ``as_of`` is preserved for ADR auditability."""
    as_of: str
    models: dict[str, ModelRates]

    def rates_for(self, model: str) -> ModelRates:
        rates = self.models.get(model) or self.models.get(_strip_dated_suffix(model))
        if rates is None:
            raise KeyError(
                f"no pricing entry for model {model!r}; "
                f"known: {sorted(self.models)}"
            )
        return rates


def load_pricing(path: Path | None = None) -> Pricing:
    """Parse pricing.yaml. Defaults to ``config/pricing.yaml``."""
    src = path or _DEFAULT_PRICING_PATH
    raw = yaml.safe_load(src.read_text())
    models = {
        name: ModelRates(
            input_per_mtok=float(spec["input_per_mtok"]),
            output_per_mtok=float(spec["output_per_mtok"]),
            cache_read_per_mtok=float(spec["cache_read_per_mtok"]),
            cache_creation_per_mtok=float(spec["cache_creation_per_mtok"]),
        )
        for name, spec in (raw.get("models") or {}).items()
    }
    return Pricing(as_of=str(raw.get("as_of", "")), models=models)
