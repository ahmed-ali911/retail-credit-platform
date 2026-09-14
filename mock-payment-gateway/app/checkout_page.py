"""Server-rendered hosted checkout page — "Demo Gateway".

Deliberately generic/simulated branding: a plain, original layout with no
resemblance to any real payment provider's look, logo, or proprietary
interface. The payment method is labelled generically ("Demo Debit Network")
— a KNET-like *journey* (redirect, pick an outcome, return to merchant) is
simulated without copying any real provider's branding.
"""
from __future__ import annotations

from decimal import Decimal

from app.models import CheckoutSession


def _mask_name(name: str) -> str:
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "****"
    masked = []
    for p in parts:
        masked.append(p[0] + "*" * max(len(p) - 1, 1))
    return " ".join(masked)


_OUTCOME_BUTTONS = [
    ("success_settlement", "Successful payment (authorize + settle)", "primary"),
    ("success_authorization_only", "Successful authorization only", "secondary"),
    ("delayed_settlement", "Successful payment — delayed settlement", "secondary"),
    ("insufficient_funds", "Decline — insufficient funds", "danger"),
    ("customer_cancel", "Cancel and return to merchant", "secondary"),
    ("timeout", "Let session time out", "secondary"),
    ("duplicate_webhook", "Successful payment — with duplicate webhook", "secondary"),
    ("invalid_signature", "Send webhook with invalid signature (fault demo)", "secondary"),
    ("settle_then_reverse", "Settle, then reverse shortly after", "secondary"),
]


def render_checkout_page(
    session: CheckoutSession, *, disabled: bool = False, banner_message: str | None = None
) -> str:
    amount = Decimal(session.amount)
    masked_name = _mask_name(session.customer_name)
    buttons_html = "\n".join(
        f'''
        <form method="post" action="/gateway/checkout/{session.token}/simulate" class="outcome-form">
          <input type="hidden" name="outcome" value="{code}" />
          <button type="submit" class="btn btn-{style}" {"disabled" if disabled else ""}>{label}</button>
        </form>'''
        for code, label, style in _OUTCOME_BUTTONS
    )
    disabled_banner = f'<div class="banner">{banner_message}</div>' if (disabled and banner_message) else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Demo Gateway — Checkout</title>
<style>
  :root {{
    --bg: #0f1720; --panel: #16212c; --panel-2: #1c2a37; --border: #2a3b4a;
    --text: #e7edf2; --muted: #93a4b3; --accent: #3fb0a6; --accent-2: #2d8f86;
    --danger: #d9695f;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px;
  }}
  .card {{
    width: 100%; max-width: 460px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; padding: 28px; box-shadow: 0 20px 50px rgba(0,0,0,0.35);
  }}
  .brand {{ display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }}
  .brand-mark {{
    width: 30px; height: 30px; border-radius: 8px; background: var(--accent);
    display: flex; align-items: center; justify-content: center; font-weight: 700; color: #06211d;
  }}
  .brand-name {{ font-weight: 700; letter-spacing: 0.2px; }}
  .brand-tag {{ color: var(--muted); font-size: 12px; margin-left: auto; }}
  .amount {{ font-size: 34px; font-weight: 700; margin: 6px 0 2px; }}
  .currency {{ color: var(--muted); font-size: 14px; margin-bottom: 18px; }}
  .rows {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 18px; }}
  .row {{ display: flex; justify-content: space-between; padding: 5px 0; font-size: 13px; }}
  .row span:first-child {{ color: var(--muted); }}
  .method {{
    display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted);
    border: 1px dashed var(--border); border-radius: 8px; padding: 8px 10px; margin-bottom: 18px;
  }}
  .timer {{ font-size: 12px; color: var(--muted); margin-bottom: 18px; }}
  .timer strong {{ color: var(--text); font-variant-numeric: tabular-nums; }}
  .section-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); margin: 18px 0 10px; }}
  .outcome-form {{ margin-bottom: 8px; }}
  .btn {{
    width: 100%; padding: 11px 14px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel-2); color: var(--text); font-size: 13.5px; cursor: pointer; text-align: left;
  }}
  .btn:hover {{ border-color: var(--accent); }}
  .btn-primary {{ background: var(--accent); border-color: var(--accent); color: #06211d; font-weight: 600; }}
  .btn-primary:hover {{ background: var(--accent-2); }}
  .btn-danger {{ border-color: var(--danger); color: var(--danger); }}
  .btn[disabled] {{ opacity: 0.4; cursor: not-allowed; }}
  .disclaimer {{ margin-top: 18px; font-size: 11px; color: var(--muted); line-height: 1.5; }}
  .banner {{
    background: rgba(217,105,95,0.15); border: 1px solid var(--danger); color: var(--text);
    padding: 10px 12px; border-radius: 8px; font-size: 13px; margin-bottom: 16px;
  }}
  .return-link {{ display: block; text-align: center; margin-top: 14px; color: var(--muted); font-size: 12.5px; text-decoration: none; }}
  .return-link:hover {{ color: var(--text); }}
</style>
</head>
<body>
  <div class="card">
    <div class="brand">
      <div class="brand-mark">D</div>
      <div class="brand-name">Demo Gateway</div>
      <div class="brand-tag">Simulated checkout</div>
    </div>
    {disabled_banner}
    <div class="amount">{amount:.2f}</div>
    <div class="currency">{session.currency} · payable to demo merchant</div>
    <div class="rows">
      <div class="row"><span>Merchant reference</span><span>{session.merchant_reference}</span></div>
      <div class="row"><span>Payment reference</span><span>{session.merchant_reference}</span></div>
      <div class="row"><span>Contract</span><span>{session.contract_reference}</span></div>
      <div class="row"><span>Customer</span><span>{masked_name}</span></div>
      <div class="row"><span>Description</span><span>{session.description or "Installment payment"}</span></div>
    </div>
    <div class="method">Payment method: <strong style="color: var(--text)">Demo Debit Network</strong></div>
    <div class="timer">Session expires <strong id="expiry-clock">—</strong></div>

    <div class="section-label">Choose a simulated outcome (test tool — no real payment credentials are collected)</div>
    {buttons_html}

    <a class="return-link" href="{session.return_url}">Return to merchant without paying</a>
    <div class="disclaimer">
      This is an entirely simulated payment demo for a training/portfolio project.
      It is not affiliated with, and does not claim to be, any real bank or payment
      network. No real card numbers, CVVs, bank credentials, or payment tokens are
      ever requested or processed here.
    </div>
  </div>
  <script>
    var expiresAt = new Date("{session.expires_at.isoformat()}Z").getTime();
    var el = document.getElementById("expiry-clock");
    function tick() {{
      var diff = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
      var m = Math.floor(diff / 60), s = diff % 60;
      el.textContent = m + ":" + (s < 10 ? "0" : "") + s;
      if (diff <= 0) clearInterval(iv);
    }}
    tick();
    var iv = setInterval(tick, 1000);
  </script>
</body>
</html>"""
