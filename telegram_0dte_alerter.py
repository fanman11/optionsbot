#!/usr/bin/env python3
"""
QUANTUM GEX // 0DTE Gamma Squeeze Alerter with Live Milestone Tracking & Performance Ledger
------------------------------------------------------------------------------------------
Features:
  - Expiration Filter:             Strictly 0DTE (Expiring TODAY only)
  - Extreme Volume Anomaly:        Volume >= 40.0x Open Interest
  - Institutional Size:            Total Premium >= $1,000,000+
  - Convexity / Lotto Floor:       Contract Price <= $1.00 (under $100 / contract)
  - Live 0DTE Milestones:          Real-time Telegram pings when lottos hit +100%, +250%, and +500%
  - 0DTE Daily Scorecard:          Automated performance recap and win-rate digest at market close
"""

import os
import json
import time
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import numpy as np
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
WIN_THRESHOLD_PCT = 50.0           # 0DTE win threshold: at least +50% peak gain

PERFORMANCE_FILE = "0dte_performance.json"


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"0DTE LOTTO ALERTER WITH LIVE PERFORMANCE TRACKING ACTIVE")

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


class ZeroDtePerformanceTracker:
    """Tracks active 0DTE alerts in real-time for high-water marks and milestone surges."""
    def __init__(self):
        self.active_trades = {}
        self.completed_today = []
        self.scorecard_sent_today = False
        self.load_history()

    def load_history(self):
        if os.path.exists(PERFORMANCE_FILE):
            try:
                with open(PERFORMANCE_FILE, "r") as f:
                    self.history = json.load(f)
            except Exception:
                self.history = {"total_alerts": 0, "total_wins": 0, "days": []}
        else:
            self.history = {"total_alerts": 0, "total_wins": 0, "days": []}

    def save_history(self):
        try:
            with open(PERFORMANCE_FILE, "w") as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")

    def register_alert(self, trade):
        cid = trade["contract_id"]
        if cid not in self.active_trades:
            self.active_trades[cid] = {
                "ticker": trade["ticker"],
                "type": trade["type"],
                "strike": trade["strike"],
                "expiry": trade["expiry"],
                "entry_price": trade["price"],
                "contract_cost": trade["contract_cost"],
                "premium": trade["premium"],
                "max_price": trade["price"],
                "max_gain_pct": 0.0,
                "milestones_hit": set(),
                "alert_time": datetime.now().strftime("%H:%M:%S")
            }

    def update_and_check_milestones(self, ticker_symbol, opt_chain):
        for cid, t in list(self.active_trades.items()):
            if t["ticker"] != ticker_symbol:
                continue

            chain = opt_chain.calls if t["type"] == "CALL" else opt_chain.puts
            match = chain[chain["strike"] == t["strike"]]
            if match.empty:
                continue

            current_price = float(match.iloc[0].get("lastPrice", 0) or 0)
            if current_price <= 0:
                continue

            # Update High-Water Mark
            if current_price > t["max_price"]:
                t["max_price"] = current_price
                t["max_gain_pct"] = ((current_price - t["entry_price"]) / t["entry_price"]) * 100

            gain = t["max_gain_pct"]

            # Milestone 1: +100% (2x Double)
            if gain >= 100.0 and 100 not in t["milestones_hit"]:
                t["milestones_hit"].add(100)
                self.dispatch_milestone(t, current_price, gain, "⚡ 0DTE LOTTO SURGE: +100% (2x)", "Position doubled. Take initial capital off the table.")

            # Milestone 2: +250% (3.5x Multi-Bagger)
            if gain >= 250.0 and 250 not in t["milestones_hit"]:
                t["milestones_hit"].add(250)
                self.dispatch_milestone(t, current_price, gain, "🚀 0DTE GAMMA SQUEEZE: +250%", "Huge convexity runner. Lock in major gains.")

            # Milestone 3: +500%+ (Nuclear Gamma Explosion)
            if gain >= 500.0 and 500 not in t["milestones_hit"]:
                t["milestones_hit"].add(500)
                self.dispatch_milestone(t, current_price, gain, "💣 0DTE NUCLEAR MULTI-BAGGER: +500%+", "Epic intraday gamma cascade in play.")

    def dispatch_milestone(self, t, current_price, gain, title, advice):
        msg = (
            f"<b>{title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Contract:</b> <code>{t['ticker']} ${t['strike']:.2f} {t['type']} (0DTE)</code>\n"
            f"<b>Entry Price:</b> ${t['entry_price']:.2f} (${t['contract_cost']:,.0f})\n"
            f"<b>Current Peak:</b> <b>${current_price:.2f}</b> (<b>+{gain:.1f}%</b>)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <i>{advice}</i>"
        )
        print(f"[!] 0DTE Milestone: {t['ticker']} ${t['strike']} {t['type']} reached +{gain:.1f}%!")
        send_telegram_alert(msg)

    def check_daily_scorecard(self):
        now = datetime.now()
        if now.hour == 16 and 5 <= now.minute <= 30 and not self.scorecard_sent_today:
            if not self.active_trades and not self.completed_today:
                return

            all_trades = list(self.active_trades.values()) + self.completed_today
            if not all_trades:
                return

            total_alerts = len(all_trades)
            wins = [t for t in all_trades if t["max_gain_pct"] >= WIN_THRESHOLD_PCT]
            win_count = len(wins)
            win_rate = (win_count / total_alerts) * 100 if total_alerts > 0 else 0
            avg_gain = np.mean([t["max_gain_pct"] for t in all_trades])

            # Update historical persistent ledger
            self.history["total_alerts"] += total_alerts
            self.history["total_wins"] += win_count
            cum_win_rate = (self.history["total_wins"] / self.history["total_alerts"]) * 100 if self.history["total_alerts"] > 0 else 0
            self.save_history()

            card_rows = []
            for t in sorted(all_trades, key=lambda x: x["max_gain_pct"], reverse=True):
                is_win = t["max_gain_pct"] >= WIN_THRESHOLD_PCT
                status_icon = "✅" if is_win else "❌"
                card_rows.append(
                    f"• {status_icon} <code>{t['ticker']} ${t['strike']:.0f}{t['type'][0]} (0DTE)</code>: "
                    f"Peak <b>${t['max_price']:.2f}</b> (<b>{t['max_gain_pct']:+.1f}%</b>)"
                )

            trades_list_str = "\n".join(card_rows)

            msg = (
                f"<b>📊 0DTE DAILY LOTTO SCORECARD</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Total 0DTE Signals Today:</b> {total_alerts}\n"
                f"<b>Win Rate (&ge; +{WIN_THRESHOLD_PCT:.0f}% Peak):</b> <b>{win_rate:.1f}%</b> ({win_count}W / {total_alerts - win_count}L)\n"
                f"<b>Average Peak Gain:</b> <b>+{avg_gain:.1f}%</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{trades_list_str}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 <b>0DTE All-Time Win Rate:</b> <b>{cum_win_rate:.1f}%</b> ({self.history['total_wins']} / {self.history['total_alerts']} alerts)"
            )

            send_telegram_alert(msg)
            self.scorecard_sent_today = True

        if now.hour == 9 and now.minute < 30:
            self.scorecard_sent_today = False
            self.completed_today.extend(list(self.active_trades.values()))
            self.active_trades.clear()


class ZeroDteLottoAlerter:
    def __init__(self):
        self.alerted_contracts = {}
        self.tracker = ZeroDtePerformanceTracker()

    def scan_ticker(self, ticker_symbol: str):
        ticker = yf.Ticker(ticker_symbol)
        history = ticker.history(period="1d")
        if history.empty:
            return
        spot = float(history['Close'].iloc[-1])

        expirations = ticker.options
        if not expirations:
            return

        today = pd.Timestamp.today().normalize()
        zero_dte_expirations = [
            exp for exp in expirations[:2]
            if pd.Timestamp(exp).normalize() == today
        ]

        if not zero_dte_expirations:
            return

        exp_str = zero_dte_expirations[0]
        opt_chain = ticker.option_chain(exp_str)

        # Update live performance tracking for active 0DTE trades
        self.tracker.update_and_check_milestones(ticker_symbol, opt_chain)

        candidate_lottos = []

        # 1. Calls
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

        # 2. Puts
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

        if candidate_lottos:
            candidate_lottos.sort(key=lambda x: x['premium'], reverse=True)
            top_trade = candidate_lottos[0]
            cid = top_trade['contract_id']
            last_vol = self.alerted_contracts.get(cid, 0)

            if last_vol == 0 or top_trade['vol'] >= (last_vol * 1.5):
                self.alerted_contracts[cid] = top_trade['vol']
                self.tracker.register_alert(top_trade)
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
            f"📡 <i>Tracking live peak % gains (+100%, +250%, +500%+).</i>"
        )
        print(f"[!] 0DTE WHALE ALERT: {trade['ticker']} ${trade['strike']} {trade['type']} @ ${trade['price']:.2f} (${trade['premium']/1e6:.2f}M)")
        send_telegram_alert(msg)

    def start_monitoring(self):
        send_telegram_alert(
            "<b>⚡ 0DTE GAMMA SQUEEZE ENGINE ONLINE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Filters:</b> 0DTE &bull; 40x Vol/OI &bull; $1M+ Outlay &bull; &le; $1.00 Price\n"
            "• <b>Milestone Tracking:</b> Live +100%, +250%, +500% alerts\n"
            "• <b>Scorecard:</b> Automated 0DTE win-rate digest at 4:05 PM EST\n"
            "━━━━━━━━━━━━━━━━━━━"
        )

        while True:
            for ticker in WATCHLIST:
                try:
                    self.scan_ticker(ticker)
                except Exception as e:
                    print(f"Error scanning 0DTE {ticker}: {e}")
                time.sleep(1)

            self.tracker.check_daily_scorecard()
            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    alerter = ZeroDteLottoAlerter()
    alerter.start_monitoring()
