# Dynamic Market Gate — 2026-08-29

## Approved methodology

The final READY floor is now **90/100** for Stocks, Equity Options, SPX Core and SPX V20.
The floor never drops below 90. Market conditions can only make the system more selective.

- Healthy/normal trend: 90+
- Caution / incomplete confirmation: 92+
- Range or mixed regime: 93+
- Counter-trend versus broad market regime: 93+
- High volatility: 94+
- Low participation but clear direction: 94+
- Low participation + unclear direction: NO TRADE
- Very wide option spread / low contract quality: NO TRADE
- Stale/invalid data: NO TRADE (existing freshness gates remain authoritative)

## Direction neutrality

The market gate does not choose CALL or PUT. Core Strategy keeps separate Bull/Bear scoring, and SPX V20 keeps independent CALL/PUT scoring. The gate applies the same quality policy to both sides.

## Risk adaptation

High-volatility, range, caution, counter-trend and low-liquidity states cap paper risk below the global maximum. SPX 0DTE retains its existing stricter risk cap.

## Option workflow

Options use a pre-contract directional quality filter, then the final combined signal + contract score must beat the dynamic threshold. A strong contract cannot rescue a poor or blocked market thesis, and a strong thesis cannot rescue an illiquid contract.

## Render migration safety

The code enforces an effective floor of 90 even if an older Render environment still has `MIN_SCORE=75`.
