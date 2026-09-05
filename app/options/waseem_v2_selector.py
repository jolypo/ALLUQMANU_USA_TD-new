from __future__ import annotations

from app.options.waseem_selector import WaseemContractSelector


class WaseemV2ContractSelector:
    """V2 strike-efficiency layer built on the isolated V1 near-OTM selector."""

    def __init__(self):
        self.base = WaseemContractSelector()

    @staticmethod
    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def rank(self, *args, **kwargs):
        kwargs = dict(kwargs)
        requested = int(kwargs.pop("max_results", 3) or 3)
        base_rows, diagnostics = self.base.rank(*args, max_results=max(8, requested * 3), **kwargs)
        if not base_rows:
            return [], diagnostics
        gammas = [abs(self._f(r.get("gamma"), 0.0)) for r in base_rows]
        max_gamma = max(gammas) if gammas else 0.0
        for row in base_rows:
            base_score = self._f(row.get("contract_score"), 0.0)
            delta = abs(self._f(row.get("delta"), 0.0)) if row.get("delta") is not None else None
            gamma = abs(self._f(row.get("gamma"), 0.0))
            theta = abs(self._f(row.get("theta"), 0.0)) if row.get("theta") is not None else 0.0
            coverage = self._f(row.get("expected_move_coverage"), 0.0)
            spread = self._f(row.get("spread_pct"), 99.0)
            # Sweet spot: near-OTM, responsive delta/gamma, acceptable decay and execution.
            delta_fit = 75.0 if delta is None else max(35.0, 100.0 - abs(delta - 0.45) * 120.0)
            gamma_fit = 70.0 if max_gamma <= 0 else min(100.0, 55.0 + 45.0 * gamma / max_gamma)
            theta_fit = max(35.0, 100.0 - theta * 35.0)
            move_fit = 82.0 if coverage <= 0 else max(35.0, 100.0 - abs(coverage - 0.75) * 55.0)
            execution_fit = max(0.0, 100.0 - spread * 5.5)
            efficiency = 0.35 * base_score + 0.18 * delta_fit + 0.16 * gamma_fit + 0.10 * theta_fit + 0.13 * move_fit + 0.08 * execution_fit
            row["waseem_v1_contract_score"] = round(base_score, 1)
            row["strike_efficiency"] = round(max(0.0, min(100.0, efficiency)), 1)
            row["contract_score"] = row["strike_efficiency"]
            row["selection_engine"] = "WASEEM_V2"
            row["selection_reason"] = "Waseem V2 Strike Efficiency: near-OTM + expected move + response + liquidity"
        base_rows.sort(key=lambda r: (self._f(r.get("strike_efficiency")), -self._f(r.get("spread_pct"), 999)), reverse=True)
        diagnostics = ["Waseem V2 Strike Efficiency enabled"] + list(diagnostics)
        return base_rows[:requested], diagnostics
