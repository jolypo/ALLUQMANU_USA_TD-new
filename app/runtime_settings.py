from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from app.config import settings


class SuccessRuleStore:
    """Persistent admin-editable success thresholds.

    These thresholds describe *signal success*, not the final realized result.
    A trade may reach the success threshold and later close as LOSS; both facts
    are intentionally preserved.

    Units:
    - stock: percent P&L from confirmed entry
    - equity_option/index_option: cash P&L in USD

    A threshold <= 0 disables statistical success tracking for that category.
    """

    CATEGORIES = ("stock", "equity_option", "index_option")

    def __init__(self, filename: str = "success_rules.json"):
        self.path = Path(settings.data_path) / filename
        self.lock = RLock()
        self.defaults = {
            "stock": {
                "threshold": float(settings.stock_success_pct_default),
                "unit": "PCT",
            },
            "equity_option": {
                "threshold": float(settings.equity_option_success_usd_default),
                "unit": "USD",
            },
            "index_option": {
                "threshold": float(settings.index_option_success_usd_default),
                "unit": "USD",
            },
        }
        self._ensure()

    def _ensure(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._write_unlocked(self.defaults)
                return
            data = self._read_unlocked()
            changed = False
            for category, default in self.defaults.items():
                if category not in data or not isinstance(data.get(category), dict):
                    data[category] = dict(default)
                    changed = True
                    continue
                if "threshold" not in data[category]:
                    data[category]["threshold"] = default["threshold"]
                    changed = True
                if "unit" not in data[category]:
                    data[category]["unit"] = default["unit"]
                    changed = True
            if changed:
                self._write_unlocked(data)

    def _read_unlocked(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _write_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def all(self) -> dict:
        with self.lock:
            data = self._read_unlocked()
            out = {}
            for category in self.CATEGORIES:
                default = self.defaults[category]
                row = data.get(category) or {}
                try:
                    threshold = float(row.get("threshold", default["threshold"]))
                except (TypeError, ValueError):
                    threshold = float(default["threshold"])
                out[category] = {
                    "threshold": max(0.0, threshold),
                    "unit": default["unit"],
                }
            return out

    def get(self, category: str) -> dict:
        if category not in self.CATEGORIES:
            raise ValueError(f"Unsupported success category: {category}")
        return self.all()[category]

    def set_threshold(self, category: str, value: float) -> dict:
        if category not in self.CATEGORIES:
            raise ValueError(f"Unsupported success category: {category}")
        value = float(value)
        if value < 0:
            raise ValueError("threshold must be >= 0")
        if value > 1_000_000:
            raise ValueError("threshold is unreasonably large")
        with self.lock:
            data = self._read_unlocked()
            for key, default in self.defaults.items():
                if not isinstance(data.get(key), dict):
                    data[key] = dict(default)
            data[category]["threshold"] = round(value, 4)
            data[category]["unit"] = self.defaults[category]["unit"]
            self._write_unlocked(data)
        return self.get(category)


success_rules = SuccessRuleStore()


class ContractSearchStore:
    """Persistent admin-editable max premium filters by asset + DTE horizon.

    A value <= 0 means unlimited. These are search filters only and do not
    weaken liquidity/Greeks/spread/data-quality gates.

    Horizons:
    - daily:   0DTE only
    - weekly:  1-7 DTE
    - monthly: 8-35 DTE
    """

    CATEGORIES = ("equity_option", "index_option")
    HORIZONS = ("daily", "weekly", "monthly")

    def __init__(self, filename: str = "contract_search_settings.json"):
        self.path = Path(settings.data_path) / filename
        self.lock = RLock()
        self.defaults = {
            "equity_option": {
                h: float(settings.equity_option_max_contract_price_default)
                for h in self.HORIZONS
            },
            "index_option": {
                h: float(settings.index_option_max_contract_price_default)
                for h in self.HORIZONS
            },
        }
        self._ensure()

    def _read_unlocked(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _write_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _norm_horizon(cls, horizon: str) -> str:
        h = str(horizon or "").strip().lower()
        if h not in cls.HORIZONS:
            raise ValueError(f"Unsupported contract-search horizon: {horizon}")
        return h

    def _ensure(self) -> None:
        """Create schema and migrate the old one-price-per-category format.

        If an older deployment stored {max_contract_price: X}, copy X into all
        three horizons so an existing admin limit is not silently lost.
        """
        with self.lock:
            data = self._read_unlocked() if self.path.exists() else {}
            changed = not self.path.exists()
            for category in self.CATEGORIES:
                default = self.defaults[category]
                row = data.get(category)
                if not isinstance(row, dict):
                    data[category] = dict(default)
                    changed = True
                    continue

                legacy = row.get("max_contract_price")
                for horizon in self.HORIZONS:
                    if horizon not in row:
                        if legacy is not None:
                            try:
                                row[horizon] = max(0.0, float(legacy))
                            except (TypeError, ValueError):
                                row[horizon] = float(default[horizon])
                        else:
                            row[horizon] = float(default[horizon])
                        changed = True
                if "max_contract_price" in row:
                    row.pop("max_contract_price", None)
                    changed = True
            if changed:
                self._write_unlocked(data)

    def all(self) -> dict:
        with self.lock:
            data = self._read_unlocked()
            out = {}
            for category in self.CATEGORIES:
                src = data.get(category) or {}
                out[category] = {}
                for horizon in self.HORIZONS:
                    default = self.defaults[category][horizon]
                    try:
                        value = float(src.get(horizon, default))
                    except (TypeError, ValueError):
                        value = float(default)
                    out[category][horizon] = max(0.0, value)
            return out

    def get(self, category: str, horizon: str | None = None) -> dict:
        if category not in self.CATEGORIES:
            raise ValueError(f"Unsupported contract-search category: {category}")
        values = self.all()[category]
        if horizon is None:
            return dict(values)
        h = self._norm_horizon(horizon)
        return {"max_contract_price": values[h], "horizon": h}

    def get_max_price(self, category: str, horizon: str) -> float:
        return float(self.get(category, horizon)["max_contract_price"])

    def set_max_price(self, category: str, horizon: str, value: float) -> dict:
        if category not in self.CATEGORIES:
            raise ValueError(f"Unsupported contract-search category: {category}")
        h = self._norm_horizon(horizon)
        value = float(value)
        if value < 0 or value > 100000:
            raise ValueError("max contract price must be between 0 and 100000")
        with self.lock:
            data = self._read_unlocked()
            if not isinstance(data.get(category), dict):
                data[category] = dict(self.defaults[category])
            data[category][h] = round(value, 4)
            data[category].pop("max_contract_price", None)
            self._write_unlocked(data)
        return self.get(category, h)

    def reset(self, category: str | None = None, horizon: str | None = None) -> dict:
        with self.lock:
            data = self._read_unlocked()
            targets = self.CATEGORIES if category is None else (category,)
            for key in targets:
                if key not in self.CATEGORIES:
                    raise ValueError(f"Unsupported contract-search category: {key}")
                if not isinstance(data.get(key), dict):
                    data[key] = dict(self.defaults[key])
                if horizon is None:
                    data[key] = dict(self.defaults[key])
                else:
                    h = self._norm_horizon(horizon)
                    data[key][h] = float(self.defaults[key][h])
                    data[key].pop("max_contract_price", None)
            self._write_unlocked(data)
        return self.all()


contract_search_rules = ContractSearchStore()


class ProfitAlertStore:
    """Persistent Telegram-configurable option profit alert increment.

    The value is in option premium dollars, e.g. 0.10 means one alert for
    each new 10-cent profit level above the confirmed entry. It applies to
    both equity options and index/SPX options.
    """

    def __init__(self, filename: str = "profit_alert_settings.json"):
        self.path = Path(settings.data_path) / filename
        self.lock = RLock()
        self.default_step = float(settings.option_profit_alert_step_default)
        self._ensure()

    def _ensure(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._write_unlocked({"step": self.default_step})
                return
            data = self._read_unlocked()
            if "step" not in data:
                data["step"] = self.default_step
                self._write_unlocked(data)

    def _read_unlocked(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _write_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_step(self) -> float:
        with self.lock:
            data = self._read_unlocked()
            try:
                value = float(data.get("step", self.default_step))
            except (TypeError, ValueError):
                value = self.default_step
            return max(0.01, round(value, 4))

    def set_step(self, value: float) -> float:
        value = float(value)
        if value < 0.01 or value > 1000:
            raise ValueError("profit alert step must be between 0.01 and 1000")
        with self.lock:
            self._write_unlocked({"step": round(value, 4)})
        return self.get_step()

    def reset(self) -> float:
        with self.lock:
            self._write_unlocked({"step": self.default_step})
        return self.get_step()


profit_alert_rules = ProfitAlertStore()
