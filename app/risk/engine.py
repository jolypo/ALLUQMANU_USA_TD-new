from app.config import settings


class RiskEngine:
    def assess(
        self,
        score: float,
        data_quality: str,
        rr: float,
        *,
        required_score: float | None = None,
        risk_cap: float | None = None,
    ) -> tuple[bool, float, str]:
        if rr < settings.min_rr:
            return False, 0.0, "R/R أقل من الحد الأدنى"
        threshold = float(settings.ready_score_floor if required_score is None else required_score)
        if score < threshold:
            return False, 0.0, f"Score أقل من الحد المطلوب ({threshold:.1f})"
        if data_quality == "INVALID":
            return False, 0.0, "جودة البيانات غير صالحة"

        # High scores can carry more paper-risk only when the market-quality
        # gate also permits it. Dynamic states cap this value separately.
        risk = 0.005
        if score >= 94:
            risk = 0.01
        elif score >= 90:
            risk = 0.0075
        if data_quality != "GOOD":
            risk = min(risk, 0.005)
        if risk_cap is not None:
            risk = min(risk, float(risk_cap))
        return True, min(risk, settings.max_risk_per_trade), "ACCEPT"
