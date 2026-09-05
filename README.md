# ALLUQMANU_USA_TD

Telegram analysis and simulated-trade tracking system for US stocks, equity options, and SPX options.

## Current workflow

- The normal admin workflow is Telegram inline menus/buttons: Trading → scan → choose candidate → Approve.
- `/stock`, `/option`, `/indexoption`, `/pic1k`... and `/publish` remain available as fallback commands.
- Scanning never opens or publishes a trade; only final approval publishes/tracks it.
- New publications are tracked as `OPEN` but are not considered filled until the monitored price reaches the entry zone.
- Entry confirmation, profit updates, TP/SL, milestone alerts, and exits reply to the original channel signal using its stored Telegram `channel_message_id`.

## Technical engine

The signal engine now uses grouped scoring rather than stacking similar indicators as independent confirmation:

- EMA 9/20/50/200
- ADX 14
- RSI 14 + RSI slope
- MACD histogram + slope
- 5-bar momentum
- session-aware VWAP
- relative volume + volume slope
- ATR / ATR%
- structure, HH/HL, LH/LL, breakout confirmation
- secondary ICT-style observations (BOS/FVG/liquidity sweep)
- intraday/daily multi-timeframe confirmation
- relative strength versus SPY
- market regime modifier
- Alpaca news/catalyst modifier

News is only a bounded modifier; it does not create trades by itself. Clearly adverse catalyst keywords can reject a long candidate.

## Options

The underlying must pass first, then a separate contract-quality layer checks:

- OCC root / underlying consistency
- strike distance sanity
- bid/ask validity
- spread
- delta
- theta relative to premium
- IV sanity
- activity when available
- DTE

There is no arbitrary maximum contract premium filter.

Alpaca `indicative` option data is not OPRA real-time. The project keeps this limitation explicit in signal messages.

## Profit alerts

Option profit updates are sent on each monitored increase and include:

- current premium
- percentage P&L
- dollar P&L
- Saudi-riyal P&L at the configured USD/SAR rate
- a generated image showing the actual cash profit

Image tier:

- under $100 profit: green
- $100 to under $300: yellow
- $300 and above: blue

Signal-success tracking is now separate from the final realized result. By default, Equity Options and Index Options are marked statistically successful once cash P&L reaches **+$50**. The thresholds are independently editable from the private Telegram **Success Rules** menu. Stocks have a separate percent-based threshold and are OFF by default until the admin sets a value. A successful signal can later close as `LOSS`; both facts remain recorded. The milestone alert includes a momentum state:

- green: strong — continue with profit protection
- yellow: slowing — secure part of profit / raise stop
- red: weak/reversal — consider exiting the contract

The milestone does not stop later profit updates.

## Daily reports

Daily reports are sent privately to `TELEGRAM_ADMIN_USER_ID`, as separate compact messages for categories that had activity that day:

1. US stocks
2. Equity options
3. Index options

Option reports also show USD and SAR cash P&L. Performance and weekly reports show **Successful Signals** separately from **Final Wins / Final Losses**, including successful trades that are still open and trades that reached the success threshold before later closing at a loss.

## SPX 0DTE + Swing

`Index Options` evaluates SPX on two paths:

- **0DTE**: today's expiry only, with stricter spread/delta/contract-score gates and a lower risk cap.
- **Swing**: the normal configured swing DTE window.

No trade is forced. If either path fails technical, risk, liquidity, or contract validation, it returns no candidate for that path.

## Option signal card

Equity-option and SPX-option publications use the horizontal CALL/PUT card: green for CALL and red for PUT. The card shows the dynamic symbol, New York date, strike, entry price, and watermark. The image and signal details are published as **one Telegram media post**. Telegram captions are limited to 1024 characters, so the original detailed text is used unchanged when it fits; oversized signals are safely compacted while preserving levels, contract data, Greeks, data quality, and Trade ID.

## Safety

`LIVE_TRADING=false` remains required. No broker order execution is implemented.

## Deployment

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Copy `.env.example` values into Render Environment and keep real bot/API secrets out of GitHub.

## Validation

Run:

```bash
python -m compileall -q .
pytest -q
```

Local validation for this revision: Python compilation passed and the included test suite passed.

## SPX strategy selection
In the private Telegram menu, `Trading -> Index Options` opens two independent SPX engines:
- **SPX V20**: port of the supplied ALLUQMANI SPX Radar V2.1 READY CALL/PUT logic.
- **SPX Core**: the existing project strategy.

SPX V20 uses SPX price reference bars plus SPY as the volume proxy and never replaces the Core strategy. Both continue through the same validated SPX/SPXW option-contract, risk, approval, publishing, paper-monitoring and reporting pipeline.

## Balanced CALL/PUT Core scoring
Core analysis uses independent bullish and bearish directional conviction. Trend, Structure, Momentum, VWAP and ICT determine direction; Volume and Volatility are quality modifiers only. A low-ADX/range guard prevents tiny EMA stacks from creating false directional trades. Equity Options and SPX Core map bullish underlying signals to CALL and bearish underlying signals to PUT. SPX V20 keeps its own independent CALL/PUT scoring model.

## Dynamic Market Quality Gate (Final methodology)

READY signals use a hard base floor of **90/100**. The system never lowers that floor. It dynamically tightens the requirement when market quality worsens:

- Healthy/normal: 90+
- Caution: 92+
- Range/mixed or counter-trend: 93+
- High volatility or weak participation with clear direction: 94+
- Low participation + unclear direction, stale data, chase/hard veto, or very poor option execution quality: NO TRADE

This policy is symmetric for CALL and PUT. Direction comes from the directional engine (Core Bull/Bear or SPX V20 CALL/PUT scoring); liquidity and volatility only control tradeability/selectivity.

## v6 — Confirmed Setup Learning

The v6 learning layer is deliberately conservative and applies only to `CONFIRMED_SETUP` Judge ranking.

- Completed Confirmed Setup trades are recorded into `data/learning_memory.json` at runtime.
- Statistical option success (`success_reached`) is preserved as a learning WIN, matching report semantics.
- Learning starts in `COLLECTING` mode and does not affect decisions until `LEARNING_MIN_GLOBAL_SAMPLES` is reached (default 12).
- Cohorts include asset class, underlying LONG/SHORT direction, DTE horizon, market state, and liquidity state.
- Bayesian shrinkage reduces overreaction to small samples.
- The learned adjustment is bounded to `[-4, +2]` Judge points.
- **Safety invariant:** a raw Judge score below 90 can never be upgraded to READY by learning.
- Existing Core, SPX Core, SPX V20, Dynamic Market Gate, contract filters, freshness, DTE and risk logic are unchanged.
- `GET /learning` shows current learning status and sample count.

### Optional durable GitHub memory

Render's default filesystem should not be treated as durable state. v6 can optionally merge the learning memory into a dedicated GitHub branch called `learning-data`, separate from `main` so learning commits do not cause a Render redeploy loop.

Configure only in Render Environment (never commit the token):

```text
LEARNING_GITHUB_TOKEN=<fine-grained token with Contents read/write>
LEARNING_GITHUB_REPO=jolypo/ALLUQMANU_USA_TD-new
LEARNING_GITHUB_BRANCH=learning-data
LEARNING_GITHUB_PATH=data/learning_memory.json
```

If no token is configured, the system keeps learning locally and never blocks scanning because GitHub sync is unavailable.

## Waseem V1 (v8)

Waseem V1 is an additional options engine. Existing Core, Confirmed Setup and SPX V20 strategy logic is preserved.

- Equity Options: Waseem V1 supports Daily 0DTE, Weekly 1–7 DTE, Monthly 8–35 DTE, plus a Daily+Weekly dual scan.
- SPX Options: Waseem V1 supports the same horizons and uses actual SPX spot for near-OTM strike distance.
- CALL/PUT selection is directional and mirrored: LONG underlying -> CALL, SHORT underlying -> PUT.
- Strike selection is near-OTM adaptive. Equity distance expands by horizon; SPX Waseem caps the search at 40 index points from spot.
- Contracts are ranked by expected-move fit, premium, spread, liquidity and Greeks when reliable. 0DTE can continue when Greeks are unavailable; weekly/monthly require Delta.
- Waseem diagnostics record why contracts were rejected instead of returning only a generic no-contract message.
- Candidates show engine source, horizon, expected move and strike distance.

### Dedicated profit watcher

Open confirmed option trades are checked by a dedicated 60-second task, independent from the heavier trade-monitor cycle. Profit alerts use the Telegram-configured Profit Alert Step and compare the current observed option price with the last price that actually generated a profit alert. New highs are not repeated after a dip. A qualifying alert sends the approved profit image plus the normal Arabic profit caption.

## Waseem V2 (v9)

Waseem V2 is an isolated options engine. It does not replace or change Core, Confirmed Setup, SPX V20, or Waseem V1.

- Equity Options V2: technical direction + company-news layer already available through Alpaca + SPY/QQQ + sector ETF + relative strength + VIX context + regime-transition soft scoring + Waseem V2 Strike Efficiency.
- SPX/SPXW V2: technical direction + ES/NQ/YM/RTY + VIX + DXY + US10Y + regime-transition soft scoring + Waseem V2 Strike Efficiency.
- Daily 0DTE, Weekly 1–7 DTE, Monthly 8–35 DTE, plus independent Daily+Weekly scan.
- V2 monitoring continues until manual Stop or US cash-market close. Legacy engines keep their existing 3-opportunity auto-stop behavior.
- Public/free context is best-effort. Missing or stale public data is shown as `UNAVAILABLE`, `DELAYED`, or `STALE`; it is never fabricated.
- Economic Calendar, NYSE TICK, institutional GEX and institutional Options Flow are not fabricated when no free reliable source is available; the candidate displays `UNAVAILABLE`.
- Automatic TIME_EXIT and STOP_LOSS notifications are admin-private only. They are not posted to the public signal channel.

## v10 — FRED + Alpha Vantage free context feeds

Waseem V2 can optionally enrich decisions with two low-frequency free sources configured only through environment variables:

- `FRED_API_KEY`: official FRED economic release dates plus daily US 2Y/10Y/30Y Treasury constant-maturity yields.
- `ALPHA_VANTAGE_API_KEY`: forward company earnings calendar for Equity Options.

These feeds are **context only**. They never replace the live/delayed underlying or option quote used for execution. FRED release-date API is treated as `DATE_ONLY`; because it does not guarantee the exact intraday release timestamp, it contributes only a modest soft caution and is never used as an "event in N minutes" hard veto. Alpha Vantage earnings timing is also treated as `DATE_ONLY` unless a future provider supplies verified before/after-market timing.

If a key/feed is missing, limited, or fails, Waseem V2 reports `UNAVAILABLE` and continues without fabricating values. The older engines are unchanged.

For Render, add the real values in **Environment** (do not commit them):

```text
FRED_API_KEY=<your key>
ALPHA_VANTAGE_API_KEY=<your key>
```

## v11 — Live Status Data Diagnostics

Telegram `Status` now performs real, guarded health checks for Alpaca, FRED, Alpha Vantage, and the free Waseem V2 context feeds. It reports `AVAILABLE`, `DELAYED`, `STALE`, `UNAVAILABLE`, or `NOT CONFIGURED`, and lists all scanner modes including Waseem V1/V2. This release does not change trading logic.

## v12 — Waseem V3 Entry Quality + SPX Global Trading Hours

Waseem V3 is a new isolated options engine. Core, Confirmed Setup, SPX V20, Waseem V1, and Waseem V2 trading logic remain unchanged; their candidate/publication messages only receive richer timestamps/diagnostics.

### Equity Options V3

- Reuses the existing Waseem V2 setup and strike-selection output.
- Adds a separate `Entry Quality` layer for the option premium.
- Separates Setup/Contract quality from Entry quality.
- Avoids chasing a premium near the observed option-session high or with poor execution quality.
- A valid contract with an inefficient current premium becomes `WATCH`, not a trade.
- WATCH shows current premium, preferred entry range, reason, chase state, and diagnostics.
- WATCH cannot be published as a trade. The scanner continues and can surface the contract again as `READY` when entry quality improves.

### SPX/SPXW V3

- RTH keeps the existing V2 setup/contract foundation and adds V3 Entry Quality.
- GTH is allowed independently from the US cash-market clock.
- GTH session policy: 20:15–09:25 ET; RTH: 09:30–16:15 ET; the 09:25–09:30 gap is closed.
- During GTH, cash SPX is explicitly `PREVIOUS_CLOSE`, never presented as live.
- GTH direction is futures-led (ES primary, with NQ/YM/RTY confirmation) plus available VIX/DXY/yield/economic context.
- An indicative SPX reference may be estimated from previous SPX cash close plus the available ES move. It is labeled `Indicative`, not live SPX.
- An actual usable SPX/SPXW option quote is mandatory before V3 creates a READY or WATCH candidate. Missing quotes produce `UNAVAILABLE`; no premium is fabricated.

### Message timestamps and diagnostics

Candidate/publication diagnostics now distinguish:

- actual market/option quote timestamp,
- system detection timestamp,
- detection/data lag,
- first V3 detection,
- WATCH-added time,
- READY time when applicable,
- publication time when applicable.

V2/V3 context messages expose every stored data-source line, including missing/stale data. Older engines are not fed V3 context and explicitly remain logically unchanged.

### Health and state

- Telegram Status lists Equity/SPX Waseem V3 monitors.
- Telegram Status exposes Waseem V3 SPX session state and V3 Entry Engine state.
- `GET /health` includes live data-source diagnostics and V3 session/feature state.
- `GET /state` exposes current monitor states, SPX GTH/RTH state, and live data-source diagnostics.

## V13 — SPX/SPXW GTH feed diagnostics
Waseem V3 now reports whether the configured options feed is actually returning SPX/SPXW data during the current session. Status, `/health`, `/state`, and V3 GTH candidate messages show the newest quote/trade timestamps and ages, contract, bid/ask or trade price, snapshot/quote/trade counts, chain source, exact ET/KSA session boundaries, and the last cash-SPX point/session. Missing data is reported explicitly as UNAVAILABLE/STALE rather than inferred.


## Waseem V4 (v16)
Independent Equity + SPX/SPXW engine for Daily and Weekly scans. Combines V2 setup/contract selection, V3 entry/anti-chase, and liquidity/pre-move analysis. Existing engines are unchanged.

## v18 — Telegram Dynamic Equity Watchlist + Waseem V5

### Persistent Master Equity Watchlist

The Telegram Trading menu now includes a single master Equity Watchlist shared by all equity engines. Admin actions are: Show/Refresh, Add, Remove, Disable and Enable. A newly added symbol is validated for market data and active options before it is stored. Enabled symbols enter subsequent scans without source-code changes, restart or redeploy.

Runtime mutations are intentionally writable only when `DATABASE_URL` points to PostgreSQL. If no database is configured, the system shows the static configured watchlist but refuses Add/Remove/Enable/Disable rather than pretending Render's ephemeral filesystem is durable. On first database initialization the existing configured stock universe is bootstrapped into the table.

### Waseem V5 — independent engine

Waseem V5 does not modify V4 or any older engine. It consumes V4 candidates and adds an observable order-flow/execution layer using only fields genuinely available from the current provider: best bid/ask, bid/ask size when supplied, latest trade and cross-scan quote movement. Multi-level book depth/DOM, institutional flow, sweeps, blocks, absorption and replenishment remain `UNAVAILABLE` unless a future provider actually supplies the data required to calculate them.

V5 uses a multi-gate READY decision. Score alone is not sufficient. READY requires the underlying V4/V3 setup to be READY, current premium inside Preferred Entry, fresh quote, acceptable spread, usable order-flow confidence/score, and the V5 score floor. Otherwise the candidate remains WATCH and the continuous monitor can re-evaluate it automatically.

V5 preserves the V3 preferred-entry and structural option stop. Where V4 exposes a valid directional external-liquidity target and contract Delta is usable, V5 can extend targets using an approximate Delta-based premium projection adjusted by observed flow. Missing fields stay `UNAVAILABLE`; no Level-2/DOM data is fabricated.

Render environment additions:

```text
DATABASE_URL=<PostgreSQL connection URL>
WASEEM_V5_READY_FLOOR=88
WASEEM_V5_MIN_FLOW_SCORE=55
```

## Waseem V6 (V23)
Independent delayed/session-aware options engine for Equity Options and SPX/SPXW. It supports combined Daily 0DTE + Weekly 1–7 DTE scans and adds multi-timeframe structure, ICT/Fibonacci context, positive/negative cross confirmation, momentum-decay, late-entry, room-to-target, breakout-quality, reversal-risk, session-awareness, and delayed-feed penalties. V2/V3/V4/V5 remain unchanged.

V23 adds a two-stage lifecycle. Before RTH, V6 builds a non-executable underlying plan (direction, support/resistance, trigger, invalidation, ICT/Fibonacci, ATR/RVOL/VWAP and target) and deliberately does not lock an option contract. During RTH, V6 re-analyzes the underlying first, then scans the option chain and confirms execution using spread, volume/OI, Greeks, IV, contract efficiency, entry quality and anti-chase logic. V6 also changes weights across Opening, Morning, Midday, Afternoon and Power Hour.

## V24 Telegram symbol diversity
Continuous Waseem monitors now scan a wider internal pool and deliver the top unique underlying symbols rather than allowing multiple contracts from the same stock to consume all Telegram slots. A 20-minute cross-engine symbol cooldown suppresses repeat messages while analysis continues internally. WATCH->READY, confirmed direction reversals, and material READY score upgrades can bypass the cooldown.
