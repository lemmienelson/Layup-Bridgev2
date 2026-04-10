from flask import Flask, request, jsonify
import requests
import uuid
import logging
import os

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ===== CREDENTIALS =====
LB_USERNAME  = os.environ.get("LB_USERNAME", "lemmienelson@gmail.com")
LB_PASSWORD  = os.environ.get("LB_PASSWORD", "Carrie55@")
LB_DOMAIN    = "default"
LB_ACCOUNT   = "default:2833714_4"
LB_SYMBOL    = "US30"
LB_BASE_URL  = "https://api.liquidcharts.com/dxsca-web"
DEFAULT_QTY  = 0.01

session_token = None

def login():
    global session_token
    url  = f"{LB_BASE_URL}/login"
    body = {
        "username": LB_USERNAME,
        "domain":   LB_DOMAIN,
        "password": LB_PASSWORD
    }
    resp = requests.post(url, json=body)
    if resp.status_code == 200:
        session_token = resp.json().get("sessionToken")
        logging.info("✅ Logged in to Liquid Charts")
        return True
    else:
        logging.error(f"❌ Login failed: {resp.status_code} {resp.text}")
        return False

def get_headers():
    return {
        "Authorization": f"DXAPI {session_token}",
        "Content-Type":  "application/json"
    }

def place_order(side, qty):
    global session_token
    if not session_token:
        if not login():
            return False, "Login failed"

    account_encoded = LB_ACCOUNT.replace(":", "%3A")
    url = f"{LB_BASE_URL}/accounts/{account_encoded}/orders"
    client_order_id = f"layup_{uuid.uuid4().hex[:16]}"

    body = {
        "clientOrderId":  client_order_id,
        "type":           "MARKET",
        "instrument":     LB_SYMBOL,
        "side":           side,
        "quantity":       str(qty),
        "positionEffect": "OPEN"
    }

    resp = requests.post(url, json=body, headers=get_headers())

    if resp.status_code == 401:
        logging.warning("⚠️ Token expired, re-logging in...")
        session_token = None
        if login():
            resp = requests.post(url, json=body, headers=get_headers())

    if resp.status_code in (200, 201):
        logging.info(f"✅ Order placed: {side} {qty} {LB_SYMBOL}")
        return True, resp.json()
    else:
        logging.error(f"❌ Order failed: {resp.status_code} {resp.text}")
        return False, resp.text

def close_position():
    global session_token
    if not session_token:
        if not login():
            return False, "Login failed"

    account_encoded = LB_ACCOUNT.replace(":", "%3A")
    url  = f"{LB_BASE_URL}/accounts/{account_encoded}/positions"
    resp = requests.get(url, headers=get_headers())

    if resp.status_code == 401:
        session_token = None
        if login():
            resp = requests.get(url, headers=get_headers())

    if resp.status_code != 200:
        logging.error(f"❌ Could not fetch positions: {resp.text}")
        return False, resp.text

    positions = resp.json()
    logging.info(f"📊 Positions: {positions}")

    pos_list = positions if isinstance(positions, list) else positions.get("positions", [])

    for pos in pos_list:
        symbol = pos.get("instrument", pos.get("symbol", ""))
        if symbol == LB_SYMBOL:
            qty  = abs(float(pos.get("quantity", 0)))
            side = pos.get("side", "")

            if qty == 0:
                logging.info("ℹ️ No open position to close")
                return True, "flat"

            close_side = "SELL" if side == "BUY" else "BUY"
            logging.info(f"🔄 Closing {side} {qty} with {close_side}")
            return place_order(close_side, qty)

    logging.info("ℹ️ No US30 position found")
    return True, "no position"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    logging.info(f"📨 Webhook received: {data}")

    event = data.get("event", "")
    side  = data.get("side", "")
    qty   = float(data.get("qty", DEFAULT_QTY)) or DEFAULT_QTY

    if event == "entry":
        if side == "long":
            ok, result = place_order("BUY", qty)
        elif side == "short":
            ok, result = place_order("SELL", qty)
        else:
            return jsonify({"error": f"Unknown side: {side}"}), 400
        return jsonify({"status": "ok" if ok else "error", "result": str(result)}), 200 if ok else 500

    elif event in ("exit", "reset"):
        ok, result = close_position()
        return jsonify({"status": "ok" if ok else "error", "result": str(result)}), 200 if ok else 500

    else:
        return jsonify({"error": f"Unknown event: {event}"}), 400

@app.route("/", methods=["GET"])
def health():
    status = "logged in" if session_token else "not logged in"
    return jsonify({"status": "bridge running", "session": status}), 200

if __name__ == "__main__":
    login()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
