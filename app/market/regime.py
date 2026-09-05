from app.strategies.engine import StrategyEngine


class MarketRegimeEngine:
    def __init__(self, provider):
        self.provider = provider

    async def get(self) -> str:
        try:
            df = await self.provider.bars("SPY", "1Day", 260)
            if len(df) < 60:
                return "UNKNOWN"
            a = StrategyEngine().analyze(df)
            direction = str(a.get("direction", "NEUTRAL")).upper()
            score = float(a.get("score", 0.0) or 0.0)
            if direction == "LONG" and score >= 70:
                return "BULL"
            if direction == "SHORT" and score >= 70:
                return "BEAR"
            return "RANGE"
        except Exception:
            return "UNKNOWN"
