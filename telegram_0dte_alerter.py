#!/usr/bin/env python3
"""
QUANTUM GEX // 0DTE Gamma Squeeze & Lotto Whale Alerter
------------------------------------------------------
Specialized 0DTE (Same-Day Expiration) Engine:
  - Expiration Filter:             Strictly 0DTE (Expiring TODAY only)
  - Extreme Volume Anomaly:        Volume >= 40.0x Open Interest
  - Institutional Size:            Total Premium >= $1,000,000+
  - Convexity / Lotto Floor:       Contract Price <= $1.00 (under $100 / contract)
  - Rate-Limiting & Memory:        Max 1 top 0DTE play per ticker, zero repeat spam
"""

import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import pandas as pd
import yfinance as yf

# ==============================================================================
# CONFIGURATION
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# Core 0DTE universe: Daily index ETFs + High-Beta single stocks (which become 0DTE on Fridays)
WATCHLIST = ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AMD", "META"]

# --- 0DTE Lotto Whale Filters ---
MIN_VOL_OI_RATIO = 40.0            # Minimum 40x volume over open interest
MIN_WHALE_PREMIUM = 1000000.0      # $1,000,000+ minimum institutional outlay
MAX_CONTRACT_PRICE = 1.00          # Maximum $1.00 price ($100 per contract)
CHECK_INTERVAL_SECONDS = 90        # Scan every 90 seconds during market hours


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"0DTE LOTTO ALERTER ACTIVE")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", 8081))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


def send_telegram_alert(message_html: str) -> bool:
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        r = requests.post(url, json=payload, timeout=5)
        return r.status_code == 200
    except Exception as e:
        print(f"[!] Telegram send error: {e}")
        return False


class ZeroDteLottoAlerter:
    def __init__(self):
        self.alerted_contracts = {}

    def scan_ticker(self, ticker_symbol: str):
        ticker = yf.Ticker(ticker_symbol)
        history = ticker.history(period="1d")
        if history.empty:
            return
        spot = float(history['Close'].iloc[-1])

        expirations = ticker.options
        if not expirations:
            return

        # Target ONLY today's expiration (0DTE)
        today = pd.Timestamp.today().normalize()
        zero_dte_expirations = [
            exp for exp in expirations[:2]
            if pd.Timestamp(exp).normalize() == today
        ]

        if not zero_dte_expirations:
            return  # No 0DTE expiration active for this ticker today

        exp_str = zero_dte_expirations[0]
        opt_chain = ticker.option_chain(exp_str)
        candidate_lottos = []

        # 1. Scan 0DTE Calls
        for _, row in opt_chain.calls.iterrows():
            strike = float(row['strike'])
            oi = float(row.get('openInterest', 0) or 0)
            vol = float(row.get('volume', 0) or 0)
            price = float(row.get('lastPrice', 0) or 0)

            premium = vol * price * 100
            vol_oi = vol / oi if oi > 0 else 0

            if (
                vol_oi >= MIN_VOL_OI_RATIO and
                premium >= MIN_WHALE_PREMIUM and
                0.05 <= price <= MAX_CONTRACT_PRICE
            ):
                candidate_lottos.append({
                    'ticker': ticker_symbol,
                    'spot': spot,
                    'type': 'CALL',
                    'strike': strike,
                    'expiry': exp_str,
                    'vol': vol,
                    'oi': oi,
                    'vol_oi': vol_oi,
                    'price': price,
                    'contract_cost': price * 100,
                    'premium': premium,
                    'contract_id': f"{ticker_symbol}_0DTE_C_{strike}_{exp_str}"
                })

        # 2. Scan 0DTE Puts
        for _, row in opt_chain.puts.iterrows():
            strike = float(row['strike'])
            oi = float(row.get('openInterest', 0) or 0)
            vol = float(row.get('volume', 0) or 0)
            price = float(row.get('lastPrice', 0) or 0)

            premium = vol * price * 100
            vol_oi = vol / oi if oi > 0 else 0

            if (
                vol_oi >= MIN_VOL_OI_RATIO and
                premium >= MIN_WHALE_PREMIUM and
                0.05 <= price <= MAX_CONTRACT_PRICE
            ):
                candidate_lottos.append({
                    'ticker': ticker_symbol,
                    'spot': spot,
                    'type': 'PUT',
                    'strike': strike,
                    'expiry': exp_str,
                    'vol': vol,
                    'oi': oi,
                    'vol_oi': vol_oi,
                    'price': price,
                    'contract_cost': price * 100,
                    'premium': premium,
                    'contract_id': f"{ticker_symbol}_0DTE_P_{strike}_{exp_str}"
                })

        # Send ONLY the highest premium 0DTE whale trade per ticker & deduplicate
        if candidate_lottos:
            candidate_lottos.sort(key=lambda x: x['premium'], reverse=True)
            top_trade = candidate_lottos[0]
            cid = top_trade['contract_id']
            last_vol = self.alerted_contracts.get(cid, 0)

            if last_vol == 0 or top_trade['vol'] >= (last_vol * 1.5):
                self.alerted_contracts[cid] = top_trade['vol']
                self.dispatch_0dte_alert(top_trade)

    def dispatch_0dte_alert(self, trade):
        is_call = trade['type'] == 'CALL'
        badge = "⚡ 0DTE GAMMA SQUEEZE (CALL)" if is_call else "💥 0DTE LIQUIDATION CASCADE (PUT)"
        dist_pct = ((trade['strike'] - trade['spot']) / trade['spot']) * 100

        msg = (
            f"<b>🎲 {badge}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Ticker:</b> <code>{trade['ticker']}</code> &bull; <b>Spot:</b> ${trade['spot']:.2f}\n"
            f"<b>Contract:</b> <code>${trade['strike']:.2f} {trade['type']} (0DTE Today)</code>\n"
            f"<b>Contract Cost:</b> <b>${trade['price']:.2f}</b> (${trade['contract_cost']:,.0f} / contract)\n"
            f"<b>Whale Outlay:</b> <b>${trade['premium'] / 1e6:.2f} Million</b> ({int(trade['vol']):,} contracts)\n"
            f"<b>Volume Multiple:</b> <b>{trade['vol_oi']:.1f}x OI</b> ({int(trade['vol']):,} vol vs {int(trade['oi']):,} oi)\n"
            f"<b>Strike Distance:</b> {dist_pct:+.2f}% from spot\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <i>High-convexity 0DTE lotto. Market makers forced into heavy intraday gamma hedging.</i>"
        )
        print(f"[!] 0DTE WHALE ALERT: {trade['ticker']} ${trade['strike']} {trade['type']} @ ${trade['price']:.2f} (${trade['premium']/1e6:.2f}M)")
        send_telegram_alert(msg)

    def start_monitoring(self):
        send_telegram_alert(
            "<b>⚡ 0DTE GAMMA SQUEEZE ENGINE ONLINE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Expiration:</b> 0DTE ONLY (Expiring Today)\n"
            "• <b>Volume Multiple:</b> &ge; 40.0x OI\n"
            "• <b>Whale Premium:</b> &ge; $1.0 Million\n"
            "• <b>Contract Ceiling:</b> &le; $1.00 (Under $100/contract)\n"
            "• <b>Rate Limit:</b> Max 1 Top 0DTE Whale per ticker\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Waiting for extreme institutional 0DTE gamma flow..."
        )

        while True:
            for ticker in WATCHLIST:
                try:
                    self.scan_ticker(ticker)
                except Exception as e:
                    print(f"Error scanning 0DTE {ticker}: {e}")
                time.sleep(1)

            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    alerter = ZeroDteLottoAlerter()
    alerter.start_monitoring()
