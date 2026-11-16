#!/usr/bin/env python3
"""
QUANTUM GEX // Institutional Whale Alerter + Live Dashboard API
-----------------------------------------------------------------
Features:
  - Strict Whale Filters:      25x Vol/OI, $5M+ Premium, $1.00-$5.00 Contract Price Band
  - Distinct Telegram Alerts:  🚨 [NEW ENTRY] vs ✅ [TARGET HIT] (Reply-Threaded) + Expiry
  - Live Dashboard API:        Serves real-time GEX & Alerts at /api/status for GitHub Pages
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
from scipy.stats import norm
import yfinance as yf

# ==============================================================================
# CONFIGURATION
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

WATCHLIST = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD"]

MIN_VOL_OI_RATIO = 25.0            # 25x volume over open interest
MIN_WHALE_PREMIUM = 5000000.0      # $5,000,000+ minimum institutional premium
MIN_CONTRACT_PRICE = 1.00          # Minimum $1.00 option price ($100 / contract)
MAX_CONTRACT_PRICE = 5.00          # Maximum $5.00 option price ($500 / contract)
CHECK_INTERVAL_SECONDS = 180       # Scan every 3 minutes
WIN_THRESHOLD_PCT = 40.0           # Minimum peak gain to count as a win (40%)
SILENT_TARGET_HITS = False         # Set to True for silent target hit notifications

PERFORMANCE_FILE = "alerts_performance.json"

# Thread-safe shared state for the /api/status endpoint
STATE_LOCK = threading.Lock()
LIVE_STATE = {
    "last_updated": None,
    "tickers": {},
    "alerts": [],
    "stats": {"total_alerts": 0, "total_wins": 0, "win_rate": 0.0}
}


class HealthAndApiHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/status"):
            with STATE_LOCK:
                payload = json.dumps({
                    "last_updated": LIVE_STATE["last_updated"],
                    "tickers": list(LIVE_STATE["tickers"].values()),
                    "alerts": LIVE_STATE["alerts"],
                    "stats": LIVE_STATE["stats"]
                })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b"GEX ALERTER WITH LIVE PERFORMANCE TRACKING ACTIVE")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthAndApiHandler)
    server.serve_forever()


def black_scholes_gamma(S, K, T, r=0.045, sigma=0.25):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))


def format_expiry(exp_str: str) -> str:
    try:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp_dt - datetime.now().date()).days
        dte_label = "0DTE Today" if dte == 0 else f"{dte} DTE"
        return f"{exp_dt.strftime('%b %d, %Y')} ({dte_label})"
    except Exception:
        return exp_str


def send_telegram_alert(message_html: str, reply_to_message_id: int = None, silent: bool = False):
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
                self.history = {"total_alerts": 0, "total_wins": 0}
        else:
            self.history = {"total_alerts": 0, "total_wins": 0}
        self.sync_live_state()

    def save_history(self):
        try:
            with open(PERFORMANCE_FILE, "w") as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")

    def sync_live_state(self):
        all_trades = list(self.active_trades.values()) + self.completed_today
        serializable_alerts = []
        for t in reversed(all_trades):
            serializable_alerts.append({
                "ticker": t["ticker"],
                "type": t["type"],
                "strike": t["strike"],
                "expiry": t["expiry"],
                "expiry_display": format_expiry(t["expiry"]),
                "entry_price": round(t["entry_price"], 2),
                "max_price": round(t["max_price"], 2),
                "max_gain_pct": round(t["max_gain_pct"], 1),
                "premium": round(t["premium"], 0),
                "alert_time": t["alert_time"],
                "milestones_hit": sorted(list(t["milestones_hit"]))
            })

        total = self.history.get("total_alerts", 0)
        wins = self.history.get("total_wins", 0)
        win_rate = round((wins / total) * 100, 1) if total > 0 else 0.0

        with STATE_LOCK:
            LIVE_STATE["alerts"] = serializable_alerts
            LIVE_STATE["stats"] = {
                "total_alerts": total,
                "total_wins": wins,
                "win_rate": win_rate
            }

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
                "alert_time": datetime.now().strftime("%I:%M %p")
            }
        elif message_id and not self.active_trades[cid].get("entry_message_id"):
            self.active_trades[cid]["entry_message_id"] = message_id
        self.sync_live_state()

    def update_and_check_milestones(self, ticker_symbol, opt_chain):
        updated = False
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
                updated = True

            gain = t["max_gain_pct"]

            if gain >= 50.0 and 50 not in t["milestones_hit"]:
                t["milestones_hit"].add(50)
                self.dispatch_milestone(t, current_price, gain, "✅ [TARGET HIT +50%]", "Take partial profits & move stop to breakeven.")
                updated = True

            if gain >= 100.0 and 100 not in t["milestones_hit"]:
                t["milestones_hit"].add(100)
                self.dispatch_milestone(t, current_price, gain, "🏆 [TARGET HIT +100% // 2X]", "Position doubled. Lock in risk-free runner.")
                updated = True

            if gain >= 200.0 and 200 not in t["milestones_hit"]:
                t["milestones_hit"].add(200)
                self.dispatch_milestone(t, current_price, gain, "💎 [TARGET HIT +200%+]", "Mega institutional gamma expansion.")
                updated = True

        if updated:
            self.sync_live_state()

    def dispatch_milestone(self, t, current_price, gain, header_tag, advice):
        exp_display = format_expiry(t["expiry"])
        msg = (
            f"<b>{header_tag} {t['ticker']} ${t['strike']:.0f}{t['type'][0]}</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"<b>Contract:</b> <code>{t['ticker']} ${t['strike']:.2f} {t['type']}</code>\n"
            f"<b>Expiration:</b> <b>{exp_display}</b>\n"
            f"<b>Entry:</b> ${t['entry_price']:.2f} &rarr; <b>Peak:${current_price:.2f} (+{gain:.1f}%)</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"💡 <i>{advice}</i>"
        )
        send_telegram_alert(msg, reply_to_message_id=t.get("entry_message_id"), silent=SILENT_TARGET_HITS)

    def check_daily_scorecard(self):
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
            self.sync_live_state()

            card_rows = []
            for t in sorted(all_trades, key=lambda x: x["max_gain_pct"], reverse=True):
                status_icon = "✅" if t["max_gain_pct"] >= WIN_THRESHOLD_PCT else "❌"
                card_rows.append(
                    f"• {status_icon} <code>{t['ticker']} ${t['strike']:.0f}{t['type'][0]} ({t['expiry']})</code>: "
                    f"Peak <b>${t['max_price']:.2f}</b> (<b>{t['max_gain_pct']:+.1f}%</b>)"
                )

            msg = (
                f"<b>📊 [DAILY SCORECARD]</b>\n"
                f"═══════════════════\n"
                f"<b>Alerts Today:</b> {total_alerts} | <b>Win Rate (&ge; +{WIN_THRESHOLD_PCT:.0f}%):</b> <b>{win_rate:.1f}%</b>\n"
                f"<b>Avg Peak Gain:</b> <b>+{avg_gain:.1f}%</b>\n"
                f"═══════════════════\n"
                f"{chr(10).join(card_rows)}\n"
                f"═══════════════════\n"
                f"📈 <b>All-Time Win Rate:</b> <b>{cum_win_rate:.1f}%</b> ({self.history['total_wins']}/{self.history['total_alerts']})"
            )
            send_telegram_alert(msg)
            self.scorecard_sent_today = True

        if now.hour == 9 and now.minute < 30:
            self.scorecard_sent_today = False
            self.completed_today.extend(list(self.active_trades.values()))
            self.active_trades.clear()
            self.sync_live_state()


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

        all_gex_rows = []
        candidate_whale_trades = []
        today = pd.Timestamp.today().normalize()

        for exp_str in expirations[:3]:
            exp_date = pd.Timestamp(exp_str)
            dte = max((exp_date - today).days, 0.5)
            T = dte / 365.25
            opt_chain = ticker.option_chain(exp_str)
            self.tracker.update_and_check_milestones(ticker_symbol, opt_chain)

            for _, row in opt_chain.calls.iterrows():
                strike = float(row['strike'])
                oi = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                iv = float(row.get('impliedVolatility', 0) or 0)
                price = float(row.get('lastPrice', 0) or 0)

                if oi > 0 and iv > 0.01:
                    gamma = black_scholes_gamma(spot, strike, T, 0.045, iv)
                    call_gex = gamma * (spot ** 2) * 0.01 * 100 * oi
                    all_gex_rows.append({'strike': strike, 'call_gex': call_gex, 'put_gex': 0.0, 'net_gex': call_gex})

                premium = vol * price * 100
                vol_oi = vol / oi if oi > 0 else 0

                if (
                    vol_oi >= MIN_VOL_OI_RATIO and
                    premium >= MIN_WHALE_PREMIUM and
                    MIN_CONTRACT_PRICE <= price <= MAX_CONTRACT_PRICE
                ):
                    candidate_whale_trades.append({
                        'ticker': ticker_symbol, 'spot': spot, 'type': 'CALL',
                        'strike': strike, 'expiry': exp_str, 'vol': vol, 'oi': oi,
                        'vol_oi': vol_oi, 'price': price, 'contract_cost': price * 100,
                        'premium': premium, 'contract_id': f"{ticker_symbol}_C_{strike}_{exp_str}"
                    })

            for _, row in opt_chain.puts.iterrows():
                strike = float(row['strike'])
                oi = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                iv = float(row.get('impliedVolatility', 0) or 0)
                price = float(row.get('lastPrice', 0) or 0)

                if oi > 0 and iv > 0.01:
                    gamma = black_scholes_gamma(spot, strike, T, 0.045, iv)
                    put_gex = -1 * gamma * (spot ** 2) * 0.01 * 100 * oi
                    all_gex_rows.append({'strike': strike, 'call_gex': 0.0, 'put_gex': put_gex, 'net_gex': put_gex})

                premium = vol * price * 100
                vol_oi = vol / oi if oi > 0 else 0

                if (
                    vol_oi >= MIN_VOL_OI_RATIO and
                    premium >= MIN_WHALE_PREMIUM and
                    MIN_CONTRACT_PRICE <= price <= MAX_CONTRACT_PRICE
                ):
                    candidate_whale_trades.append({
                        'ticker': ticker_symbol, 'spot': spot, 'type': 'PUT',
                        'strike': strike, 'expiry': exp_str, 'vol': vol, 'oi': oi,
                        'vol_oi': vol_oi, 'price': price, 'contract_cost': price * 100,
                        'premium': premium, 'contract_id': f"{ticker_symbol}_P_{strike}_{exp_str}"
                    })

        # Update live GEX state for /api/status
        if all_gex_rows:
            df = pd.DataFrame(all_gex_rows)
            agg = df.groupby('strike').sum().reset_index().sort_values('strike')
            total_net_gex_m = round(float(agg['net_gex'].sum()) / 1e6, 1)
            call_wall = float(agg.loc[agg['call_gex'].idxmax(), 'strike'])
            put_wall = float(agg.loc[agg['put_gex'].idxmin(), 'strike'])

            agg['cum_gex'] = agg['net_gex'].cumsum()
            zero_flip = spot
            for i in range(len(agg) - 1):
                if agg.iloc[i]['cum_gex'] <= 0 and agg.iloc[i + 1]['cum_gex'] > 0:
                    zero_flip = float(agg.iloc[i]['strike'])
                    break

            near = agg[(agg['strike'] >= spot * 0.92) & (agg['strike'] <= spot * 1.08)]
            strikes_payload = [
                {
                    "strike": float(r['strike']),
                    "call_gex": round(float(r['call_gex']) / 1e6, 1),
                    "put_gex": round(float(r['put_gex']) / 1e6, 1),
                    "net_gex": round(float(r['net_gex']) / 1e6, 1)
                }
                for _, r in near.iterrows()
            ]

            with STATE_LOCK:
                LIVE_STATE["tickers"][ticker_symbol] = {
                    "ticker": ticker_symbol,
                    "spot": round(spot, 2),
                    "net_gex": total_net_gex_m,
                    "zero_flip": round(zero_flip, 2),
                    "call_wall": round(call_wall, 2),
                    "put_wall": round(put_wall, 2),
                    "strikes": strikes_payload
                }
                LIVE_STATE["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

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
        exp_display = format_expiry(trade['expiry'])

        msg = (
            f"<b>🚨 [NEW ENTRY] {badge}: {trade['ticker']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Contract:</b> <code>{trade['ticker']} ${trade['strike']:.2f} {trade['type']}</code>\n"
            f"<b>Expiration:</b> <b>{exp_display}</b>\n"
            f"<b>Entry Price:</b> <b>${trade['price']:.2f}</b> (${trade['contract_cost']:,.0f} / contract)\n"
            f"<b>Whale Premium:</b> <b>${trade['premium'] / 1e6:.2f} Million</b>\n"
            f"<b>Volume Multiple:</b> <b>{trade['vol_oi']:.1f}x OI</b> ({int(trade['vol']):,} vol vs {int(trade['oi']):,} oi)\n"
            f"<b>Underlying Spot:</b> ${trade['spot']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        return send_telegram_alert(msg)

    def start_monitoring(self):
        send_telegram_alert(
            "<b>🎯 PERFORMANCE TRACKER &amp; LIVE DASHBOARD API ACTIVE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Entries:</b> 🚨 [NEW ENTRY] (25x Vol/OI &bull; $5M+ &bull; $1-$5)\n"
            "• <b>Targets:</b> ✅ [TARGET HIT] direct replies (+50%, +100%, +200%)\n"
            "• <b>Dashboard Sync:</b> Live /api/status feed enabled\n"
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
