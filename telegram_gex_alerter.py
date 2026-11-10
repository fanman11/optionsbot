#!/usr/bin/env python3
"""
QUANTUM GEX // Cloud-Ready Telegram Alert Dispatcher & Market Monitor
-------------------------------------------------------------------
Continuously monitors GEX levels, Zero-Gamma Flips, and Unusual Options Activity (UOA),
dispatching real-time formatted alerts directly to your Telegram phone app in < 500ms.

Cloud-Ready:
Includes an embedded lightweight health-check server on $PORT for 100% free hosting
on platforms like Render.com, Railway, or Fly.io.
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
CHECK_INTERVAL_SECONDS = 60                   # Poll interval during trading hours
MIN_WHALE_PREMIUM = 500000.0                 # Minimum $ premium for UOA sweeps ($500k)
MIN_VOL_OI_RATIO = 3.0                       # Minimum Volume / Open Interest multiple


# Lightweight HTTP Server for Cloud Health Checks (Render / Railway)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"QUANTUM GEX ALERTER: ACTIVE AND MONITORING")

    def log_message(self, format, *args):
        return  # Silence access logs


def run_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


def send_telegram_alert(message_html: str) -> bool:
    """Dispatches a formatted HTML alert to Telegram via Bot API."""
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[!] Cannot send Telegram alert: BOT_TOKEN not configured.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"[!] Telegram API error: {e}")
        return False


def black_scholes_gamma(S, K, T, r=0.045, sigma=0.25):
    """Calculates analytical option Gamma."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


class GexAlerter:
    def __init__(self):
        self.seen_alerts = set()
        self.last_regimes = {}

    def scan_ticker(self, ticker_symbol: str):
        ticker = yf.Ticker(ticker_symbol)
        history = ticker.history(period="1d")
        if history.empty:
            return
        spot = float(history['Close'].iloc[-1])

        expirations = ticker.options
        if not expirations:
            return

        all_rows = []
        today = pd.Timestamp.today().normalize()

        for exp_str in expirations[:2]:
            exp_date = pd.Timestamp(exp_str)
            dte = max((exp_date - today).days, 0.5)
            T = dte / 365.25

            chain = ticker.option_chain(exp_str)

            # Calls
            for _, row in chain.calls.iterrows():
                strike = float(row['strike'])
                oi = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                iv = float(row.get('impliedVolatility', 0) or 0)
                last_price = float(row.get('lastPrice', 0) or 0)

                if oi > 0 and iv > 0.01:
                    gamma = black_scholes_gamma(spot, strike, T, 0.045, iv)
                    dollar_call_gex = gamma * (spot ** 2) * 0.01 * 100 * oi
                    all_rows.append({'strike': strike, 'call_gex': dollar_call_gex, 'put_gex': 0.0, 'net_gex': dollar_call_gex})

                premium = vol * last_price * 100
                vol_oi = vol / oi if oi > 0 else vol
                trade_key = f"{ticker_symbol}_C_{strike}_{exp_str}_{vol}"

                if premium >= MIN_WHALE_PREMIUM and vol_oi >= MIN_VOL_OI_RATIO and trade_key not in self.seen_alerts:
                    self.seen_alerts.add(trade_key)
                    self.dispatch_uoa_alert(ticker_symbol, spot, "CALL", strike, exp_str, vol, oi, vol_oi, premium)

            # Puts
            for _, row in chain.puts.iterrows():
                strike = float(row['strike'])
                oi = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                iv = float(row.get('impliedVolatility', 0) or 0)
                last_price = float(row.get('lastPrice', 0) or 0)

                if oi > 0 and iv > 0.01:
                    gamma = black_scholes_gamma(spot, strike, T, 0.045, iv)
                    dollar_put_gex = -1 * gamma * (spot ** 2) * 0.01 * 100 * oi
                    all_rows.append({'strike': strike, 'call_gex': 0.0, 'put_gex': dollar_put_gex, 'net_gex': dollar_put_gex})

                premium = vol * last_price * 100
                vol_oi = vol / oi if oi > 0 else vol
                trade_key = f"{ticker_symbol}_P_{strike}_{exp_str}_{vol}"

                if premium >= MIN_WHALE_PREMIUM and vol_oi >= MIN_VOL_OI_RATIO and trade_key not in self.seen_alerts:
                    self.seen_alerts.add(trade_key)
                    self.dispatch_uoa_alert(ticker_symbol, spot, "PUT", strike, exp_str, vol, oi, vol_oi, premium)

        if not all_rows:
            return

        df = pd.DataFrame(all_rows)
        agg = df.groupby('strike').sum().reset_index()
        call_wall = float(agg.loc[agg['call_gex'].idxmax(), 'strike'])
        put_wall = float(agg.loc[agg['put_gex'].idxmin(), 'strike'])

        agg['cum_gex'] = agg['net_gex'].cumsum()
        zero_flip = spot
        for i in range(len(agg) - 1):
            if agg.iloc[i]['cum_gex'] <= 0 and agg.iloc[i+1]['cum_gex'] > 0:
                zero_flip = float(agg.iloc[i]['strike'])
                break

        current_regime = "POSITIVE" if spot >= zero_flip else "NEGATIVE"
        last_regime = self.last_regimes.get(ticker_symbol)

        if last_regime and last_regime != current_regime:
            self.dispatch_regime_flip_alert(ticker_symbol, spot, zero_flip, current_regime, call_wall, put_wall)
        
        self.last_regimes[ticker_symbol] = current_regime

    def dispatch_uoa_alert(self, ticker, spot, opt_type, strike, expiry, vol, oi, vol_oi, premium):
        badge = "🟢 BULLISH SWEEP" if opt_type == "CALL" else "🔴 BEARISH SWEEP"
        emoji = "📈" if opt_type == "CALL" else "📉"

        msg = (
            f"<b>{emoji} UNUSUAL OPTIONS FLOW ALERT: {ticker}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Sentiment:</b> {badge}\n"
            f"<b>Contract:</b> <code>{ticker} ${strike:.2f} {opt_type} ({expiry})</code>\n"
            f"<b>Spot Price:</b> ${spot:.2f}\n"
            f"<b>Whale Premium:</b> <b>${premium / 1e6:.2f}M</b>\n"
            f"<b>Volume:</b> {int(vol):,} vs <b>OI:</b> {int(oi):,} (<b>{vol_oi:.1f}x Vol/OI</b>)\n"
            f"<b>Context:</b> Aggressive institutional order flow exceeding existing open positioning.\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        print(f"[!] Sending UOA alert for {ticker} ${strike} {opt_type}...")
        send_telegram_alert(msg)

    def dispatch_regime_flip_alert(self, ticker, spot, flip, new_regime, call_wall, put_wall):
        if new_regime == "NEGATIVE":
            msg = (
                f"<b>⚠️ ZERO GAMMA BREAKDOWN ALERT: {ticker}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Regime:</b> 🚨 <b>NEGATIVE GAMMA (Vol Acceleration)</b>\n"
                f"<b>Current Spot:</b> ${spot:.2f} (Breached Flip at ${flip:.2f})\n"
                f"<b>Downside Target:</b> Put Wall at <b>${put_wall:.2f}</b>\n"
                f"<b>Market Maker Dynamics:</b> Dealers are now net short gamma. Hedging flows will accelerate downward momentum.\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
        else:
            msg = (
                f"<b>✅ RECLAIMED POSITIVE GAMMA: {ticker}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Regime:</b> 🟢 <b>POSITIVE GAMMA (Volatility Dampened)</b>\n"
                f"<b>Current Spot:</b> ${spot:.2f} (Reclaimed Flip at ${flip:.2f})\n"
                f"<b>Upside Target:</b> Call Wall at <b>${call_wall:.2f}</b>\n"
                f"<b>Market Maker Dynamics:</b> Mean-reverting regime restored. Volatility expected to compress toward Call Wall.\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
        print(f"[!] Sending Regime Flip alert for {ticker}...")
        send_telegram_alert(msg)

    def start_monitoring(self):
        print("=" * 60)
        print("  QUANTUM GEX & UOA CLOUD TELEGRAM ALERTER")
        print(f"  Watchlist: {', '.join(WATCHLIST)}")
        print(f"  Status:    Active in Cloud Container")
        print("=" * 60)

        send_telegram_alert(
            "<b>🚀 CLOUD ALERT ENGINE ONLINE (24/7)</b>\n"
            "Monitoring GEX Zero-Flips, Call/Put Walls, and Unusual Whale Sweeps.\n"
            "Alerts will be delivered here in real time."
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
    # Start cloud health check server in background thread
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    # Start main alerter loop
    alerter = GexAlerter()
    alerter.start_monitoring()
