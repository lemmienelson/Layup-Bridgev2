import os
import time
import logging
import requests
from datetime import datetime, date
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LB_API      = "https://api.liquidcharts.com/dxsca-web"
LB_USER     = "lemmienelson@gmail.com"
LB_PASSWORD = "Carrie55@"
LB_DOMAIN   = "default"

ACCOUNTS = [
    {"code": "default:DEM_2833714_6", "start_bal": 500,  "start_date": "2026-04-10", "label": "demo_500"},
    {"code": "default:DEM_2833714_5", "start_bal": 1000, "start_date": "2026-04-10", "label": "demo_1000"},
    {"code": "default:DEM_2833714_2", "start_bal": 2000, "start_date": "2026-04-10", "label": "demo_2000"},
    {"code": "default:DEM_2833714_3", "start_bal": 5000, "start_date": "2026-04-10", "label": "demo_5000"},
    {"code": "default:DEM_2833714_1", "start_bal": 9050, "start_date": "2026-04-10", "label": "demo_9050"},
    # Uncomment when ready to go live:
    # {"code": "default:ECN_2833714_4", "start_bal": 882, "start_date": "2026-03-30", "label": "live"},
]

DAILY_COMPOUND_RATE = 1.08
SAFETY_CAP          = 0.80
BASE_RATE           = 0.00008

def get_trading_day_number(start_date_str):
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

def calculate_lot_size(account):
    start_bal  = account["start_bal"]
    start_date = account["start_date"]
    base_mult  = round(start_bal * BASE_RATE, 2)
    base_mult  = max(base_mult, 0.01)
    day_num    = get_trading_day_number(start_date)
    full_mult  = round(base_mult * (DAILY_COMPOUND_RATE ** (day_num - 1)), 2)
    safe_mult  = round(full_mult * SAFETY_CAP, 2)
    safe_mult  = max(safe_mult, 0.01)
    logger.info(account["label"] + " Day " + str(day_num) + " lot=" + str(safe_mult))
    return safe_mult

sessions = {}

def login(account_code):
    try:
        resp = requests.post(
            LB_API + "/login",
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
            logger.info(account_code + " login OK")
            return token
        logger.error(account_code + " login failed: " + str(data))
        return None
    except Exception as e:
        logger.error(account_code + " login error: " + str(e))
        return None

def get_session(account_code):
    return sessions.get(account_code) or login(account_code)

def send_order(account_code, token, payload):
    encoded = requests.utils.quote(account_code, safe="")
    try:
        resp = requests.post(
            LB_API + "/accounts/" + encoded + "/orders",
            json=payload,
            headers={"Authorization": "DXAPI " + token},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 401 or "session" in str(data).lower():
            token = login(account_code)
            if token:
                resp = requests.post(
                    LB_API + "/accounts/" + encoded + "/orders",
                    json=payload,
                    headers={"Authorization": "DXAPI " + token},
                    timeout=10,
                )
                data = resp.json()
        return data
    except Exception as e:
        return {"error": str(e)}

def place_order(account, side, symbol, qty, tp_price, sl_price):
    account_code = account["code"]
    token = get_session(account_code)
    if not token:
        return {"error": "no session"}

    order_side  = "BUY"  if side.lower() == "long" else "SELL"
    close_side  = "SELL" if side.lower() == "long" else "BUY"
    ts          = str(int(time.time()))
    label       = account["label"]

    # 1 — Market entry order
    entry_payload = {
        "orderCode":      "entry" + ts + label,
        "type":           "MARKET",
        "side":           order_side,
        "quantity":       qty,
        "instrument":     symbol,
        "positionEffect": "OPEN",
        "tif":            "GTC",
    }
    entry_result = send_order(account_code, token, entry_payload)
    logger.info(account_code + " entry: " + str(entry_result))
    results = {"entry": entry_result}

    # Only place TP/SL if entry succeeded
    if "orderId" in entry_result:

        # 2 — Take Profit (limit order)
        tp_payload = {
            "orderCode":      "tp" + ts + label,
            "type":           "LIMIT",
            "side":           close_side,
            "quantity":       qty,
            "instrument":     symbol,
            "positionCode":   symbol,
            "positionEffect": "CLOSE",
            "tif":            "GTC",
            "limitPrice":     tp_price,
        }
        tp_result = send_order(account_code, token, tp_payload)
        logger.info(account_code + " TP: " + str(tp_result))
        results["tp"] = tp_result

        # 3 — Stop Loss (stop order)
        sl_payload = {
            "orderCode":      "sl" + ts + label,
            "type":           "STOP",
            "side":           close_side,
            "quantity":       qty,
            "instrument":     symbol,
            "positionCode":   symbol,
            "positionEffect": "CLOSE",
            "tif":            "GTC",
            "stopPrice":      sl_price,
        }
        sl_result = send_order(account_code, token, sl_payload)
        logger.info(account_code + " SL: " + str(sl_result))
        results["sl"] = sl_result

    return results

def close_all(account, symbol):
    account_code = account["code"]
    token = get_session(account_code)
    if not token:
        return {"error": "no session"}

    encoded = requests.utils.quote(account_code, safe="")

    # Cancel all open orders first
    try:
        requests.delete(
            LB_API + "/accounts/" + encoded + "/orders",
            headers={"Authorization": "DXAPI " + token},
            timeout=10,
        )
    except Exception as e:
        logger.error(account_code + " cancel orders error: " + str(e))

    # Then close position with market order
    ts    = str(int(time.time()))
    label = account["label"]
    qty   = calculate_lot_size(account)

    close_payload = {
        "orderCode":      "close" + ts + label,
        "type":           "MARKET",
        "side":           "SELL",
        "quantity":       qty,
        "instrument":     symbol,
        "positionCode":   symbol,
        "positionEffect": "CLOSE",
        "tif":            "GTC",
    }
    result = send_order(account_code, token, close_payload)
    logger.info(account_code + " close: " + str(result))
    return result

@app.route("/webhook", methods=["POST"])
def webhook():
    data   = request.get_json(force=True)
    logger.info("Webhook: " + str(data))

    event    = data.get("event", "").lower()
    side     = data.get("side", "long")
    symbol   = data.get("symbol", "US30")
    tp_price = float(data.get("tp", 0))
    sl_price = float(data.get("sl", 0))

    results = {}

    if event == "entry":
        if tp_price == 0 or sl_price == 0:
            return jsonify({"error": "tp and sl prices required for entry"}), 400
        for account in ACCOUNTS:
            qty    = calculate_lot_size(account)
            result = place_order(account, side, symbol, qty, tp_price, sl_price)
            results[account["label"]] = {"lot_size": qty, "result": result}

    elif event in ("exit", "tp", "sl", "reset"):
        for account in ACCOUNTS:
            result = close_all(account, symbol)
            results[account["label"]] = result

    else:
        return jsonify({"error": "Unknown event: " + event}), 400

    return jsonify({"status": "ok", "event": event, "results": results})

@app.route("/", methods=["GET"])
def health():
    today_sizes = {acc["label"]: calculate_lot_size(acc) for acc in ACCOUNTS}
    return jsonify({
        "status":          "running",
        "date":            str(date.today()),
        "lot_sizes_today": today_sizes,
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port
