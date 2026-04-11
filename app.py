import os
import json
import math
import logging
import requests
from datetime import datetime, date
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CREDENTIALS
# ─────────────────────────────────────────────
LB_API      = "https://api.liquidcharts.com/dxsca-web"
LB_USER     = "2833714"
LB_PASSWORD = "Carrie55@"
LB_DOMAIN   = "default"

# ─────────────────────────────────────────────
# ACCOUNTS
# Each account has:
#   code        → sent to Liquid Brokers API
#   start_bal   → the balance this account started at
#   start_date  → the date trading began (YYYY-MM-DD)
#   label       → friendly name for logs
# ─────────────────────────────────────────────
ACCOUNTS = [
    {"code": "DEM:2833714_6", "start_bal": 500,   "start_date": "2026-04-10", "label": "demo_500"},
    {"code": "DEM:2833714_5", "start_bal": 1000,  "start_date": "2026-04-10", "label": "demo_1000"},
    {"code": "DEM:2833714_2", "start_bal": 2000,  "start_date": "2026-04-10", "label": "demo_2000"},
    {"code": "DEM:2833714_3", "start_bal": 5000,  "start_date": "2026-04-10", "label": "demo_5000"},
    {"code": "DEM:2833714_1", "start_bal": 9050,  "start_date": "2026-04-10", "label": "demo_9050"},
    # Uncomment when ready to go live:
    # {"code": "ECN:2833714_4", "start_bal": 882, "start_date": "2026-03-30", "label": "live"},
]

# ─────────────────────────────────────────────
# COMPOUNDING SCHEDULE
# Multiplier is calculated from starting balance.
# Formula mirrors your spreadsheet:
#   Day 1 multiplier  = round(start_bal * 0.00008, 2)  (~0.08% of balance)
#   Each day compounds ~8% on the multiplier
# Safety cap = 80% of full multiplier (matches your spreadsheet)
# ─────────────────────────────────────────────

DAILY_COMPOUND_RATE = 1.08   # 8% multiplier growth per winning day
SAFETY_CAP          = 0.80   # 80% safety cap (matches your spreadsheet)
BASE_RATE           = 0.00008  # starting multiplier per dollar of balance

def get_trading_day_number(start_date_str: str) -> int:
    """Returns how many weekdays have passed since start_date (1-indexed)."""
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    today = date.today()
    if today < start:
        return 1
    day_num = 0
    current = start
    while current <= today:
        if current.weekday() < 5:  # Mon–Fri only
            day_num += 1
        current = current.fromordinal(current.toordinal() + 1)
    return max(1, day_num)

def calculate_lot_size(account: dict) -> float:
    """
    Calculate today's lot size for an account based on its schedule.
    Mirrors the Millionaire Weekday Multiplier spreadsheet logic.
    """
    start_bal  = account["start_bal"]
    start_date = account["start_date"]

    # Day 1 base multiplier
    base_mult = round(start_bal * BASE_RATE, 2)
    base_mult = max(base_mult, 0.01)  # never below 0.01

    # Compound for each trading day elapsed
    day_num   = get_trading_day_number(start_date)
    full_mult = round(base_mult * (DAILY_COMPOUND_RATE ** (day_num - 1)), 2)

    # Apply safety cap
    safe_mult = round(full_mult * SAFETY_CAP, 2)
    safe_mult = max(safe_mult, 0.01)

    logger.info(f"[{account['label']}] Day {day_num} | Full mult: {full_mult} | Safe mult: {safe_mult}")
    return safe_mult

# ─────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────
sessions = {}  # account_code → session_token

def login(account_code: str) -> str | None:
    """Login to Liquid Brokers and return session token."""
    try:
        resp = requests.post(
            f"{LB_API}/login",
            json={
                "login":    LB_USER,
                "password": LB_PASSWORD,
                "domain":   LB_DOMAIN,
            },
            timeout=10,
        )
        data = resp.json()
        token = data.get("sessionToken") or data.get("token")
        if token:
            sessions[account_code] = token
            logger.info(f"[{account_code}] Login OK")
            return token
        logger.error(f"[{account_code}] Login failed: {data}")
        return None
    except Exception as e:
        logger.error(f"[{account_code}] Login error: {e}")
        return None

def get_session(account_code: str) -> str | None:
    """Return existing session or re-login."""
    return sessions.get(account_code) or login(account_code)

# ─────────────────────────────────────────────
# ORDER PLACEMENT
# ─────────────────────────────────────────────
def place_order(account: dict, side: str, symbol: str, qty: float, price: float) -> dict:
    """Place a market order on Liquid Brokers for one account."""
    account_code = account["code"]
    token = get_session(account_code)
    if not token:
        return {"error": "no session"}

    order_side = "Buy" if side.lower() == "long" else "Sell"
    payload = {
        "account":    account_code,
        "symbol":     symbol,
        "side":       order_side,
        "orderType":  "Market",
        "quantity":   qty,
        "price":      price,
    }

    try:
        resp = requests.post(
            f"{LB_API}/placeOrder",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = resp.json()

        # Auto re-login on session expiry
        if resp.status_code == 401 or "session" in str(data).lower():
            logger.info(f"[{account_code}] Session expired, re-logging in")
            token = login(account_code)
            if token:
                resp = requests.post(
                    f"{LB_API}/placeOrder",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                data = resp.json()

        logger.info(f"[{account_code}] Order result: {data}")
        return data

    except Exception as e:
        logger.error(f"[{account_code}] Order error: {e}")
        return {"error": str(e)}

def close_position(account: dict, symbol: str) -> dict:
    """Close open position for one account."""
    account_code = account["code"]
    token = get_session(account_code)
    if not token:
        return {"error": "no session"}

    try:
        resp = requests.post(
            f"{LB_API}/closePosition",
            json={"account": account_code, "symbol": symbol},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = resp.json()
        logger.info(f"[{account_code}] Close result: {data}")
        return data
    except Exception as e:
        logger.error(f"[{account_code}] Close error: {e}")
        return {"error": str(e)}

# ─────────────────────────────────────────────
# WEBHOOK ENDPOINT
# ─────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    logger.info(f"Webhook received: {data}")

    event  = data.get("event", "").lower()
    side   = data.get("side", "long")
    symbol = data.get("symbol", "US30")
    price  = float(data.get("price", 0))

    results = {}

    if event == "entry":
        for account in ACCOUNTS:
            qty = calculate_lot_size(account)
            result = place_order(account, side, symbol, qty, price)
            results[account["label"]] = {"lot_size": qty, "result": result}

    elif event in ("exit", "tp", "sl", "reset"):
        for account in ACCOUNTS:
            result = close_position(account, symbol)
            results[account["label"]] = result

    else:
        return jsonify({"error": f"Unknown event: {event}"}), 400

    logger.info(f"Results: {results}")
    return jsonify({"status": "ok", "event": event, "results": results})

# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    today_sizes = {
        acc["label"]: calculate_lot_size(acc) for acc in ACCOUNTS
    }
    return jsonify({
        "status":       "running",
        "date":         str(date.today()),
        "lot_sizes_today": today_sizes,
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
