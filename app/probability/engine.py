from app.config import settings


class ProbabilityEngine:
    def summarize(self, history: list[dict], trade_type: str) -> dict:
        rows=[x for x in history if x.get("trade_type")==trade_type and x.get("status") in {"WIN","LOSS"}]
        n=len(rows)
        if n < settings.probability_min_samples:
            return {"status":"UNVALIDATED","samples":n,"probability":None,"required":settings.probability_min_samples}
        wins=sum(1 for x in rows if x.get("status")=="WIN")
        return {"status":"VALIDATED","samples":n,"probability":round(100*wins/n,1),"required":settings.probability_min_samples}
