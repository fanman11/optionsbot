#!/usr/bin/env python3
"""
QUANTUM GEX // Institutional Whale Alerter with Reply-Threaded Target Tracking
------------------------------------------------------------------------------
Features:
  - Strict Whale Filters:      25x Vol/OI, $5M+ Premium, $1.00-$5.00 Contract Price Band
  - Distinct Visual Headers:   🚨 [NEW ENTRY] vs ✅ [TARGET HIT] vs 📊 [DAILY SCORECARD]
  - Direct Reply Threading:    Target hits reply directly to the original entry message
  - Automated Daily Scorecard: Posts daily win rate and peak gains at 4:05 PM EST
"""

import os
import json
import time
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import numpy as np
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
MIN_CONTRACT_PRICE = 1.00          # Minimum $1.00 option price ($100 / contract)
MAX_CONTRACT_PRICE = 5.00          # Maximum $5.00 option price ($500 / contract)
CHECK_INTERVAL_SECONDS = 180       # Scan every 3 minutes
WIN_THRESHOLD_PCT = 40.0           # Minimum peak gain to count as a win (40%)
SILENT_TARGET_HITS = False         # Set to True to make target hits silent on your phone

PERFORMANCE_FILE = "alerts_performance.json"


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GEX ALERTER WITH LIVE PERFORMANCE TRACKING ACTIVE")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


def send_telegram_alert(message_html: str, reply_to_message_id: int = None, silent: bool = False):
    """Sends a Telegram message and returns its message_id for reply threading."""
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return None

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": silent
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    try:
        r = requests.post(url, json=payload, timeout=5)
        if r.status_code == 200:
            return r.json().get("result", {}).get("message_id")
        return None
    except Exception as e:
        print(f"[!] Telegram send error: {e}")
        return None


class PerformanceTracker:
    """Tracks active alerts in real-time, monitoring high-water marks and win rates."""
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

    def register_alert(self, trade, message_id=None):
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
                "entry_message_id": message_id,
                "alert_time": datetime.now().strftime("%H:%M:%S")
            }
        elif message_id and not self.active_trades[cid].get("entry_message_id"):
            self.active_trades[cid]["entry_message_id"] = message_id

    def update_and_check_milestones(self, ticker_symbol, opt_chain):
        """Checks live contract prices and triggers reply-threaded target hits."""
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

            if current_price > t["max_price"]:
                t["max_price"] = current_price
                t["max_gain_pct"] = ((current_price - t["entry_price"]) / t["entry_price"]) * 100

            gain = t["max_gain_pct"]

            if gain >= 50.0 and 50 not in t["milestones_hit"]:
                t["milestones_hit"].add(50)
                self.dispatch_milestone(t, current_price, gain, "✅ [TARGET HIT +50%]", "Take partial profits & move stop to breakeven.")

            if gain >= 100.0 and 100 not in t["milestones_hit"]:
                t["milestones_hit"].add(100)
                self.dispatch_milestone(t, current_price, gain, "🏆 [TARGET HIT +100% // 2X]", "Position doubled. Lock in risk-free runner.")

            if gain >= 200.0 and 200 not in t["milestones_hit"]:
                t["milestones_hit"].add(200)
                self.dispatch_milestone(t, current_price, gain, "💎 [TARGET HIT +200%+]", "Mega institutional gamma expansion.")

    def dispatch_milestone(self, t, current_price, gain, header_tag, advice):
        msg = (
            f"<b>{header_tag} {t['ticker']} ${t['strike']:.0f}{t['type'][0]}</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"<b>Contract:</b> <code>{t['ticker']} ${t['strike']:.2f} {t['type']} ({t['expiry']})</code>\n"
            f"<b>Entry:</b> ${t['entry_price']:.2f} &rarr; <b>Peak: ${current_price:.2f} (+{gain:.1f}%)</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"💡 <i>{advice}</i>"
        )
        print(f"[!] Target Hit: {t['ticker']} ${t['strike']} {t['type']} +{gain:.1f}%")
        send_telegram_alert(
            msg,
            reply_to_message_id=t.get("entry_message_id"),
            silent=SILENT_TARGET_HITS
        )

    def check_daily_scorecard(self):
        """Sends an automated performance recap at market close (after 16:05 EST)."""
        now = datetime.now()
        if now.hour == 16 and 5 <= now.minute <= 30 and not self.scorecard_sent_today:
            all_trades = list(self.active_trades.values()) + self.completed_today
            if not all_trades:
                return

            total_alerts = len(all_trades)
            wins = [t for t in all_trades if t["max_gain_pct"] >= WIN_THRESHOLD_PCT]
            win_count = len(wins)
            win_rate = (win_count / total_alerts) * 100 if total_alerts > 0 else 0
            avg_gain = np.mean([t["max_gain_pct"] for t in all_trades])

            self.history["total_alerts"] += total_alerts
            self.history["total_wins"] += win_count
            cum_win_rate = (self.history["total_wins"] / self.history["total_alerts"]) * 100 if self.history["total_alerts"] > 0 else 0
            self.save_history()

            card_rows = []
            for t in sorted(all_trades, key=lambda x: x["max_gain_pct"], reverse=True):
                is_win = t["max_gain_pct"] >= WIN_THRESHOLD_PCT
                status_icon = "✅" if is_win else "❌"
                card_rows.append(
                    f"• {status_icon} <code>{t['ticker']} ${t['strike']:.0f}{t['type'][0]}</code>: "
                    f"Peak <b>${t['max_price']:.2f}</b> (<b>{t['max_gain_pct']:+.1f}%</b>)"
                )

            trades_list_str = "\n".join(card_rows)

            msg = (
                f"<b>📊 [DAILY SCORECARD]</b>\n"
                f"═══════════════════\n"
                f"<b>Alerts Today:</b> {total_alerts} | <b>Win Rate (&ge; +{WIN_THRESHOLD_PCT:.0f}%):</b> <b>{win_rate:.1f}%</b>\n"
                f"<b>Avg Peak Gain:</b> <b>+{avg_gain:.1f}%</b>\n"
                f"═══════════════════\n"
                f"{trades_list_str}\n"
                f"═══════════════════\n"
                f"📈 <b>All-Time Win Rate:</b> <b>{cum_win_rate:.1f}%</b> ({self.history['total_wins']}/{self.history['total_alerts']})"
            )

            send_telegram_alert(msg)
            self.scorecard_sent_today = True

        if now.hour == 9 and now.minute < 30:
            self.scorecard_sent_today = False
            self.completed_today.extend(list(self.active_trades.values()))
            self.active_trades.clear()


class MegaWhaleAlerter:
    def __init__(self):
        self.alerted_contracts = {}
        self.tracker = PerformanceTracker()

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

        for exp_str in expirations[:3]:
            opt_chain = ticker.option_chain(exp_str)
            self.tracker.update_and_check_milestones(ticker_symbol, opt_chain)

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
                    MIN_CONTRACT_PRICE <= price <= MAX_CONTRACT_PRICE
                ):
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
                    MIN_CONTRACT_PRICE <= price <= MAX_CONTRACT_PRICE
                ):
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

        if candidate_whale_trades:
            candidate_whale_trades.sort(key=lambda x: x['premium'], reverse=True)
            top_trade = candidate_whale_trades[0]
            cid = top_trade['contract_id']
            last_vol = self.alerted_contracts.get(cid, 0)

            if last_vol == 0 or top_trade['vol'] >= (last_vol * 1.4):
                self.alerted_contracts[cid] = top_trade['vol']
                msg_id = self.dispatch_whale_alert(top_trade)
                self.tracker.register_alert(top_trade, message_id=msg_id)

    def dispatch_whale_alert(self, trade):
        is_call = trade['type'] == 'CALL'
        badge = "🟢 BULLISH WHALE" if is_call else "🔴 BEARISH WHALE"

        msg = (
            f"<b>🚨 [NEW ENTRY] {badge}: {trade['ticker']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Contract:</b> <code>{trade['ticker']} ${trade['strike']:.2f} {trade['type']} ({trade['expiry']})</code>\n"
            f"<b>Entry Price:</b> <b>${trade['price']:.2f}</b> (${trade['contract_cost']:,.0f} / contract)\n"
            f"<b>Whale Premium:</b> <b>${trade['premium'] / 1e6:.2f} Million</b>\n"
            f"<b>Volume Multiple:</b> <b>{trade['vol_oi']:.1f}x OI</b> ({int(trade['vol']):,} vol vs {int(trade['oi']):,} oi)\n"
            f"<b>Underlying Spot:</b> ${trade['spot']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        print(f"[+] Entry Dispatched: {trade['ticker']} ${trade['strike']} {trade['type']} @ ${trade['price']:.2f}")
        return send_telegram_alert(msg)

    def start_monitoring(self):
        send_telegram_alert(
            "<b>🎯 PERFORMANCE TRACKER ACTIVE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Entries:</b> 🚨 [NEW ENTRY] (25x Vol/OI &bull; $5M+ &bull; $1-$5)\n"
            "• <b>Targets:</b> ✅ [TARGET HIT] direct replies (+50%, +100%, +200%)\n"
            "• <b>Scorecard:</b> 📊 Daily win-rate recap at 4:05 PM EST\n"
            "━━━━━━━━━━━━━━━━━━━"
        )

        while True:
            for ticker in WATCHLIST:
                try:
                    self.scan_ticker(ticker)
                except Exception as e:
                    print(f"Error scanning {ticker}: {e}")
                time.sleep(1)

            self.tracker.check_daily_scorecard()
            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    alerter = MegaWhaleAlerter()
    alerter.start_monitoring()
