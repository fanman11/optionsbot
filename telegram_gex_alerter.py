#!/usr/bin/env python3
"""
QUANTUM GEX // Institutional Whale Alerter (Refined Parameters)
--------------------------------------------------------------
Strict Institutional Filters:
  - Minimum Volume / Open Interest: 25.0x (Extreme opening volume)
  - Minimum Total Dollar Premium:   $5,000,000+ ($5M+ whale size)
  - Clear Contract Cost Display:    Shows exact price per contract ($ and $ / contract)
  - Clean Format:                   Streamlined alert card (no extra context text)
  - Rate Limit:                     Max 1 top whale per ticker with deduplication
"""

import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import numpy as np
import pandas as pd
from scipy.stats import norm
import yfinance as yf

# ==============================================================================
# CONFIGURATION
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

WATCHLIST = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD"]

# --- Strict User-Defined Thresholds ---
MIN_VOL_OI_RATIO = 25.0            # 25x volume over open interest
MIN_WHALE_PREMIUM = 5000000.0      # $5,000,000+ minimum institutional premium
MAX_ALERTS_PER_TICKER = 1          # Top 1 highest whale trade per ticker
CHECK_INTERVAL_SECONDS = 180       # Scan every 3 minutes


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GEX ALERTER ACTIVE")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", 8080))
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


class MegaWhaleAlerter:
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

        candidate_whale_trades = []
        today = pd.Timestamp.today().normalize()

        # Check near-term expirations
        for exp_str in expirations[:3]:
            opt_chain = ticker.option_chain(exp_str)

            # 1. Process Calls
            for _, row in opt_chain.calls.iterrows():
                strike = float(row['strike'])
                oi = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                price = float(row.get('lastPrice', 0) or 0)

                premium = vol * price * 100
                vol_oi = vol / oi if oi > 0 else 0

                # Strict 25x Vol/OI and $5M+ filter
                if vol_oi >= MIN_VOL_OI_RATIO and premium >= MIN_WHALE_PREMIUM:
                    candidate_whale_trades.append({
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
                        'contract_id': f"{ticker_symbol}_C_{strike}_{exp_str}"
                    })

            # 2. Process Puts
            for _, row in opt_chain.puts.iterrows():
                strike = float(row['strike'])
                oi = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                price = float(row.get('lastPrice', 0) or 0)

                premium = vol * price * 100
                vol_oi = vol / oi if oi > 0 else 0

                if vol_oi >= MIN_VOL_OI_RATIO and premium >= MIN_WHALE_PREMIUM:
                    candidate_whale_trades.append({
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
                        'contract_id': f"{ticker_symbol}_P_{strike}_{exp_str}"
                    })

        # Send ONLY the top whale trade per ticker & deduplicate
        if candidate_whale_trades:
            candidate_whale_trades.sort(key=lambda x: x['premium'], reverse=True)
            dispatched = 0
            for trade in candidate_whale_trades:
                if dispatched >= MAX_ALERTS_PER_TICKER:
                    break

                cid = trade['contract_id']
                last_vol = self.alerted_contracts.get(cid, 0)

                # Only alert if new or if volume expanded by another 40%
                if last_vol == 0 or trade['vol'] >= (last_vol * 1.4):
                    self.alerted_contracts[cid] = trade['vol']
                    self.dispatch_whale_alert(trade)
                    dispatched += 1

    def dispatch_whale_alert(self, trade):
        is_call = trade['type'] == 'CALL'
        badge = "🟢 $5M+ BULLISH WHALE" if is_call else "🔴 $5M+ BEARISH WHALE"

        msg = (
            f"<b>🐋 {badge}: {trade['ticker']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Contract:</b> <code>{trade['ticker']} ${trade['strike']:.2f} {trade['type']} ({trade['expiry']})</code>\n"
            f"<b>Contract Price:</b> <b>${trade['price']:.2f}</b> (${trade['contract_cost']:,.0f} / contract)\n"
            f"<b>Whale Premium:</b> <b>${trade['premium'] / 1e6:.2f} Million</b>\n"
            f"<b>Volume Multiple:</b> <b>{trade['vol_oi']:.1f}x OI</b> ({int(trade['vol']):,} vol vs {int(trade['oi']):,} oi)\n"
            f"<b>Underlying Spot:</b> ${trade['spot']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        print(f"[+] Alert Dispatched: {trade['ticker']} ${trade['strike']} {trade['type']} @ ${trade['price']:.2f} (${trade['premium']/1e6:.2f}M)")
        send_telegram_alert(msg)

    def start_monitoring(self):
        send_telegram_alert(
            "<b>🎯 $5M+ MEGA-WHALE FILTER ACTIVE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Vol/OI Multiple:</b> &ge; 25.0x\n"
            "• <b>Whale Premium:</b> &ge; $5.0M\n"
            "• <b>Contract Cost:</b> Included per strike\n"
            "• <b>Rate Limit:</b> Max 1 Top Whale per ticker\n"
            "━━━━━━━━━━━━━━━━━━━"
        )

        while True:
            for ticker in WATCHLIST:
                try:
                    self.scan_ticker(ticker)
                except Exception as e:
                    print(f"Error scanning {ticker}: {e}")
                time.sleep(1)

            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    alerter = MegaWhaleAlerter()
    alerter.start_monitoring()
