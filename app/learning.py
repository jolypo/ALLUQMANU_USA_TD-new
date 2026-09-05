from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

import httpx

from app.config import settings


LEARNING_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _horizon(option: dict[str, Any]) -> str:
    raw = _norm(option.get("horizon") or option.get("dte_mode"), "")
    if raw in {"DAILY", "0DTE"}:
        return "DAILY"
    if raw in {"WEEKLY", "INTRADAY"}:
        return "WEEKLY"
    if raw in {"MONTHLY", "SWING"}:
        return "MONTHLY"
    try:
        dte = int(option.get("dte"))
        if dte == 0:
            return "DAILY"
        if 1 <= dte <= 7:
            return "WEEKLY"
        if 8 <= dte <= 35:
            return "MONTHLY"
    except (TypeError, ValueError):
        pass
    return "UNKNOWN"


def _asset_class(trade_type: str) -> str:
    t = _norm(trade_type, "")
    if t.startswith("INDEX_OPTION"):
        return "INDEX_OPTION"
    if t.startswith("EQUITY_OPTION"):
        return "EQUITY_OPTION"
    return "OTHER"


@dataclass(frozen=True)
class LearningAdjustment:
    adjustment: float
    samples: int
    win_rate: float | None
    status: str
    source: str


class LearningStore:
    """Conservative learning memory for Confirmed Setup Judge only.

    It does not rewrite source code or strategy thresholds. It records completed
    Confirmed Setup trades, builds cohort statistics, and returns a bounded Judge
    adjustment. The raw Judge 90 floor and existing market/contract gates remain
    authoritative.
    """

    def __init__(self, history_repo, path: str | Path | None = None):
        self.history = history_repo
        self.path = Path(path or (Path(settings.data_path) / settings.learning_memory_filename))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self._last_sync = 0.0
        if not self.path.exists():
            self._write_unlocked(self._empty())

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": LEARNING_VERSION, "updated_at": _now(), "samples": {}}

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or int(data.get("version", 0)) != LEARNING_VERSION:
                return self._empty()
            samples = data.get("samples")
            if not isinstance(samples, dict):
                samples = {}
            return {"version": LEARNING_VERSION, "updated_at": str(data.get("updated_at") or _now()), "samples": samples}
        except Exception:
            return self._empty()

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        data["version"] = LEARNING_VERSION
        data["updated_at"] = _now()
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _outcome(trade: dict[str, Any]) -> str | None:
        # Project semantics: once an option reaches configured cash success,
        # statistical success remains WIN even if realized P&L later turns down.
        if bool(trade.get("success_reached") or trade.get("success_100_reached")):
            return "WIN"
        result = _norm(trade.get("final_result"), "")
        if result in {"WIN", "LOSS", "BREAKEVEN"}:
            return result
        status = _norm(trade.get("status"), "")
        if status in {"WIN", "LOSS", "BREAKEVEN"}:
            return status
        if status != "CLOSED":
            return None
        try:
            pnl = float(trade.get("pnl_pct"))
        except (TypeError, ValueError):
            return None
        if pnl > 0.01:
            return "WIN"
        if pnl < -0.01:
            return "LOSS"
        return "BREAKEVEN"

    def _sample_from_trade(self, trade: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        trade_id = str(trade.get("trade_id") or "").strip()
        option = trade.get("option") or {}
        if not trade_id or _norm(option.get("strategy_mode"), "") != "CONFIRMED_SETUP":
            return None
        outcome = self._outcome(trade)
        if outcome is None:
            return None
        row = {
            "trade_id": trade_id,
            "symbol": _norm(trade.get("symbol")),
            "asset_class": _asset_class(str(trade.get("trade_type") or "")),
            "direction": _norm(option.get("underlying_direction") or trade.get("direction")),
            "horizon": _horizon(option),
            "market_state": _norm(trade.get("market_state"), "NORMAL"),
            "liquidity_state": _norm(trade.get("liquidity_state"), "NORMAL"),
            "volatility_state": _norm(trade.get("volatility_state"), "NORMAL"),
            "signal_score": float(trade.get("score") or 0.0),
            "judge_score": float(option.get("judge_score") or (trade.get("market_context") or {}).get("judge_score") or 0.0),
            "outcome": outcome,
            "success_reached": bool(trade.get("success_reached") or trade.get("success_100_reached")),
            "pnl_pct": trade.get("pnl_pct"),
            "cash_pnl_usd": trade.get("cash_pnl_usd"),
            "closed_at": str(trade.get("closed_at") or trade.get("performance_finalized_at") or _now()),
        }
        return trade_id, row

    def refresh_from_history(self) -> int:
        added = 0
        with self.lock:
            data = self._read_unlocked()
            samples = dict(data["samples"])
            for trade in self.history.all():
                if not isinstance(trade, dict):
                    continue
                parsed = self._sample_from_trade(trade)
                if parsed is None:
                    continue
                trade_id, row = parsed
                if samples.get(trade_id) != row:
                    if trade_id not in samples:
                        added += 1
                    samples[trade_id] = row
            if added or samples != data["samples"]:
                data["samples"] = samples
                self._write_unlocked(data)
        return added

    @staticmethod
    def _bayesian_win_rate(rows: list[dict[str, Any]]) -> float:
        # 8 virtual observations at 50/50 prevent aggressive reactions to a week
        # with only a handful of trades.
        wins = 4.0
        n = 8.0
        for row in rows:
            result = row.get("outcome")
            wins += 1.0 if result == "WIN" else 0.5 if result == "BREAKEVEN" else 0.0
            n += 1.0
        return wins / n

    def adjustment_for_signal(self, signal) -> LearningAdjustment:
        self.refresh_from_history()
        with self.lock:
            rows = list(self._read_unlocked()["samples"].values())
        total = len(rows)
        overall_rate = self._bayesian_win_rate(rows) if rows else None
        if total < settings.learning_min_global_samples:
            return LearningAdjustment(0.0, total, None if overall_rate is None else round(overall_rate * 100, 1), "COLLECTING", "global")

        option = signal.option or {}
        asset = _asset_class(signal.trade_type.value)
        direction = _norm(option.get("underlying_direction") or signal.direction)
        horizon = _horizon(option)
        market = _norm(signal.market_state, "NORMAL")
        liquidity = _norm(signal.liquidity_state, "NORMAL")

        primary = [r for r in rows if r.get("asset_class") == asset and r.get("direction") == direction and r.get("horizon") == horizon]
        environment = [r for r in rows if r.get("market_state") == market and r.get("liquidity_state") == liquidity]

        components: list[tuple[float, str]] = []
        if len(primary) >= settings.learning_min_bucket_samples:
            rate = self._bayesian_win_rate(primary)
            components.append(((rate - 0.50) * 10.0, f"profile:{len(primary)}"))
        if len(environment) >= settings.learning_min_bucket_samples:
            rate = self._bayesian_win_rate(environment)
            components.append(((rate - 0.50) * 6.0, f"environment:{len(environment)}"))
        assert overall_rate is not None
        components.append(((overall_rate - 0.50) * 4.0, f"global:{total}"))

        raw = sum(x[0] for x in components) / len(components)
        adjustment = round(max(settings.learning_max_penalty, min(settings.learning_max_bonus, raw)), 2)
        return LearningAdjustment(adjustment, total, round(overall_rate * 100, 1), "ACTIVE", "+".join(x[1] for x in components))

    def summary(self) -> dict[str, Any]:
        self.refresh_from_history()
        with self.lock:
            rows = list(self._read_unlocked()["samples"].values())
        wins = sum(1 for r in rows if r.get("outcome") == "WIN")
        losses = sum(1 for r in rows if r.get("outcome") == "LOSS")
        be = sum(1 for r in rows if r.get("outcome") == "BREAKEVEN")
        rate = self._bayesian_win_rate(rows) * 100 if rows else None
        return {
            "enabled": bool(settings.learning_enabled),
            "status": "ACTIVE" if len(rows) >= settings.learning_min_global_samples else "COLLECTING",
            "samples": len(rows),
            "required_samples": int(settings.learning_min_global_samples),
            "wins": wins,
            "losses": losses,
            "breakeven": be,
            "bayesian_win_rate": None if rate is None else round(rate, 1),
            "memory_file": str(self.path),
            "github_sync_enabled": bool(settings.learning_github_token and settings.learning_github_repo),
            "github_branch": settings.learning_github_branch,
        }


    def export_snapshot(self) -> Path:
        """Refresh completed-trade samples and return the current memory file."""
        self.refresh_from_history()
        with self.lock:
            data = self._read_unlocked()
            self._write_unlocked(data)
        return self.path

    def import_memory_file(self, source: str | Path) -> dict[str, int]:
        """Validate and merge a v1 learning-memory JSON file by trade_id."""
        source_path = Path(source)
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Learning file root must be a JSON object")
        if int(payload.get("version", 0) or 0) != LEARNING_VERSION:
            raise ValueError("Unsupported learning file version")
        incoming = payload.get("samples")
        if not isinstance(incoming, dict):
            raise ValueError("Learning file samples must be an object")

        clean: dict[str, dict[str, Any]] = {}
        for trade_id, row in incoming.items():
            tid = str(trade_id or "").strip()
            if not tid or not isinstance(row, dict):
                continue
            if str(row.get("trade_id") or tid).strip() != tid:
                raise ValueError(f"trade_id mismatch: {tid}")
            outcome = _norm(row.get("outcome"), "")
            if outcome not in {"WIN", "LOSS", "BREAKEVEN"}:
                raise ValueError(f"Invalid outcome for {tid}")
            normalized = dict(row)
            normalized["trade_id"] = tid
            normalized["outcome"] = outcome
            clean[tid] = normalized

        with self.lock:
            local = self._read_unlocked()
            before = len(local["samples"])
            merged = self._merge_samples(local["samples"], clean)
            local["samples"] = merged
            self._write_unlocked(local)
            after = len(merged)
        return {"received": len(clean), "added": max(0, after - before), "total": after}

    @staticmethod
    def _merge_samples(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        merged = dict(a)
        merged.update(b)
        return merged

    async def sync_github_if_due(self, force: bool = False) -> bool:
        """Merge memory with GitHub using a dedicated non-deploy branch.

        This is optional. A fine-grained token with Contents read/write can be
        stored in Render as LEARNING_GITHUB_TOKEN. The default branch is
        `learning-data`, avoiding a redeploy loop on `main`.
        """
        if not settings.learning_github_token or not settings.learning_github_repo:
            return False
        now = time.monotonic()
        if not force and now - self._last_sync < settings.learning_github_sync_seconds:
            return False
        self._last_sync = now
        self.refresh_from_history()

        repo = settings.learning_github_repo.strip().strip("/")
        branch = settings.learning_github_branch
        path = settings.learning_github_path
        base = f"https://api.github.com/repos/{repo}"
        headers = {
            "Authorization": f"Bearer {settings.learning_github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
                ref = await client.get(f"{base}/git/ref/heads/{branch}")
                if ref.status_code == 404:
                    main_ref = await client.get(f"{base}/git/ref/heads/main")
                    main_ref.raise_for_status()
                    sha = main_ref.json()["object"]["sha"]
                    created = await client.post(f"{base}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
                    if created.status_code not in {201, 422}:
                        created.raise_for_status()

                remote_sha = None
                remote_samples: dict[str, Any] = {}
                got = await client.get(f"{base}/contents/{path}", params={"ref": branch})
                if got.status_code == 200:
                    payload = got.json()
                    remote_sha = payload.get("sha")
                    decoded = base64.b64decode(payload.get("content", "")).decode("utf-8")
                    remote = json.loads(decoded)
                    if isinstance(remote, dict) and isinstance(remote.get("samples"), dict):
                        remote_samples = remote["samples"]
                elif got.status_code != 404:
                    got.raise_for_status()

                with self.lock:
                    local = self._read_unlocked()
                    merged_samples = self._merge_samples(remote_samples, local["samples"])
                    local["samples"] = merged_samples
                    self._write_unlocked(local)

                if merged_samples == remote_samples:
                    return False
                payload_data = {"version": LEARNING_VERSION, "updated_at": _now(), "samples": merged_samples}
                content = base64.b64encode(json.dumps(payload_data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")).decode("ascii")
                body: dict[str, Any] = {
                    "message": f"Update learning memory ({len(merged_samples)} samples)",
                    "content": content,
                    "branch": branch,
                }
                if remote_sha:
                    body["sha"] = remote_sha
                put = await client.put(f"{base}/contents/{path}", json=body)
                put.raise_for_status()
                return True
        except Exception:
            # GitHub persistence must never block trading scans.
            return False
