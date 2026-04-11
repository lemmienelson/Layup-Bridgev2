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
LB_USER     = "lemmienelson@gmail.com"
LB_PASSWORD = "Carrie55@"
LB_DOMAIN   = "default"

# ─────────────────────────────────────────────
# ACCOUNTS
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
# ─────────────────────────────────────────────
DAILY_COMPOUND_RATE = 1.08
SAFETY_CAP          = 0.80
BASE_RATE           = 0.00008

def get_trading_day_number(start_date_str: str) -> int:
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    today = date.today()
    if today < start:
        return 1
    day_num = 0
    current = start
    while current <= today:
        if current.weekday() < 5:
            day_num += 1
        current = current.fromordinal(current.toordinal() + 1)
    return max(1, day_num)

def calculate_lot_size(account: dict) -> float:
    start_bal  = account["start_bal"]
    start_date = account["start_date"]
    base_mult = round(start_bal * BASE_RATE, 2)
    base_mult = max(base_mult, 0.01)
    day_num   = get_trading_day_number(start_date)
    full_mult = round(base_mult * (DAILY_COMPOUND_RATE ** (day_num - 1)), 2)
    safe_mult = round(full_mult * SAFETY_CAP, 2)
    safe_mult = max(safe_mult, 0.01)
    logger.info(f"[{account['label']}] Day {day_num} | Full mult: {full_mult} | Safe mult: {safe_mult}")
    return safe_mult

# ─────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────
sessions = {}

def login(account_code: str) -> str | None:
    try:
        resp = requests.post(
            f"{LB_API}/login",
            json={
                "username": LB_USER,
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
    return sessions.get(account_code) or login(account_code)

# ─────────────────────────────────────────────
# ORDER PLACEMENT
# ─────────────────────────────────────────────
def place_order(account: dict, side: str, symbol: str, qty: float, price: float) -> dict:
    account_code = account["code"]
    token = get_session(account_code)
    if not token:
        return {"error": "no session"}

    order_side = "Buy" if side.lower() == "long" else "Sell"
    payload = {
        "account":   account_code,
        "symbol":    symbol,
        "side":      order_side,
        "orderType": "Market",
        "quantity":  qty,
        "price":     price,
    }

    try:
        resp = requests.post(
            f"{LB_API}/placeOrder",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = resp.json()
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
        logger.​​​​​​​​​​​​​​​​
