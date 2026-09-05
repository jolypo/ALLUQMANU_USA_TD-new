# Neon Profit Card — 2026-08-28

- Replaced the previous block-style profit image with the approved minimalist neon layout.
- The artwork now shows Arabic `مبروك الأرباح` and the real dynamic USD profit amount.
- Preserved the approved profit color tiers:
  - under $100: green/cyan
  - $100 to under $300: yellow/gold
  - $300 and above: blue
- The same renderer is used by normal option profit updates, success-threshold milestones, and private message tests.
- The detailed contract/entry/current/P&L/SAR information remains in the Telegram caption.
- The profit card defensively clamps negative values so a congratulatory image can never display a negative amount.
