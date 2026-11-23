#!/usr/bin/env python3
"""
QUANTUM GEX // 0DTE Gamma Squeeze Alerter + Scheduled Check-Ins & Daily Scorecard
--------------------------------------------------------------------------------
"""

import os
import json
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
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

WATCHLIST = ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AMD", "META"]

MIN_VOL_OI_RATIO = 40.0
MIN_WHALE_PREMIUM = 1000000.0
MAX_CONTRACT_PRICE = 1.00
MAX_SPREAD_PCT = 12.5
MIN_RUNWAY_PCT = 1.0
CHECK_INTERVAL_SECONDS = 45        # Fast 45s scan during market hours
WIN_THRESHOLD_PCT = 50.0
SILENT_TARGET_HITS = False

NY_TZ = ZoneInfo("America/New_York")
PERFORMANCE_FILE = "0dte_performance.json"


def ny_now() -> datetime:
    return datetime.now(NY_TZ)


def is_market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= mins < (16 * 60)


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

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


def black_scholes_gamma(S, K, T, r=0.045, sigma=0.25):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))


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


class ZeroDtePerformanceTracker:
    def __init__(self):
        self.active_trades = {}
        self.completed_today = []
        self.sent_events = {}
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
                "ticker": trade["ticker"], "type": trade["type"], "strike": trade["strike"],
                "expiry": trade["expiry"], "entry_price": trade["price"],
                "contract_cost": trade["contract_cost"], "max_price": trade["price"],
                "max_gain_pct": 0.0, "milestones_hit": set(), "entry_message_id": message_id
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
            if current_price > t["max_price"]:
                t["max_price"] = current_price
                t["max_gain_pct"] = ((current_price - t["entry_price"]) / t["entry_price"]) * 100

            gain = t["max_gain_pct"]
            if gain >= 100.0 and 100 not in t["milestones_hit"]:
                t["milestones_hit"].add(100)
                self.dispatch_milestone(t, current_price, gain, "✅ [0DTE TARGET +100% // 2X]", "Position doubled. Take initial capital off.")
            if gain >= 250.0 and 250 not in t["milestones_hit"]:
                t["milestones_hit"].add(250)
                self.dispatch_milestone(t, current_price, gain, "🏆 [0DTE TARGET +250%]", "Huge gamma runner. Lock in major gains.")
            if gain >= 500.0 and 500 not in t["milestones_hit"]:
                t["milestones_hit"].add(500)
                self.dispatch_milestone(t, current_price, gain, "💎 [0DTE TARGET +500%+]", "Nuclear gamma squeeze in play.")

    def dispatch_milestone(self, t, current_price, gain, header_tag, advice):
        msg = (
            f"<b>{header_tag} {t['ticker']} ${t['strike']:.0f}{t['type'][0]}</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"<b>Contract:</b> <code>{t['ticker']} ${t['strike']:.2f} {t['type']} (0DTE Today)</code>\n"
            f"<b>Entry:</b> ${t['entry_price']:.2f} &rarr; <b>Peak: ${current_price:.2f} (+{gain:.1f}%)</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"💡 <i>{advice}</i>"
        )
        send_telegram_alert(msg, reply_to_message_id=t.get("entry_message_id"), silent=SILENT_TARGET_HITS)

    def check_scheduled_messages(self):
        now = ny_now()
        if now.weekday() >= 5:
            return
        today_str = now.strftime("%Y-%m-%d")
        mins = now.hour * 60 + now.minute

        if (9 * 60 + 30) <= mins <= (9 * 60 + 40) and self.sent_events.get("open") != today_str:
            self.sent_events["open"] = today_str
            self.completed_today.clear()
            self.active_trades.clear()
            send_telegram_alert(f"<b>⚡ [MARKET OPEN] 0DTE Bot Online</b>\nScanning {', '.join(WATCHLIST)} every {CHECK_INTERVAL_SECONDS}s (ET).")

        if (12 * 60 + 30) <= mins <= (12 * 60 + 40) and self.sent_events.get("midday") != today_str:
            self.sent_events["midday"] = today_str
            send_telegram_alert(f"<b>☀️ [MID-DAY CHECK-IN] 0DTE Bot Running</b>\nActive 0DTE alerts today: <b>{len(self.active_trades)}</b>")

        if (16 * 60) <= mins <= (16 * 60 + 4) and self.sent_events.get("close") != today_str:
            self.sent_events["close"] = today_str
            send_telegram_alert("<b>🔕 [MARKET CLOSED] 0DTE Bot Paused</b>\n0DTE scanning paused until 9:30 AM ET. Summary coming at 4:05 PM ET.")

        if (16 * 60 + 5) <= mins <= (16 * 60 + 25) and self.sent_events.get("scorecard") != today_str:
            self.sent_events["scorecard"] = today_str
            all_trades = list(self.active_trades.values()) + self.completed_today
            total_alerts = len(all_trades)

            if total_alerts == 0:
                total_hist = self.history.get("total_alerts", 0)
                wins_hist = self.history.get("total_wins", 0)
                cum_wr = (wins_hist / total_hist) * 100 if total_hist > 0 else 0.0
                send_telegram_alert(
                    "<b>📊 [0DTE DAILY SCORECARD]</b>\n"
                    "═══════════════════\n"
                    "<b>0DTE Signals Today:</b> 0 (No trades met 40x / $1M+ / Open Runway)\n"
                    f"📈 <b>All-Time 0DTE Win Rate:</b> <b>{cum_wr:.1f}%</b> ({wins_hist}/{total_hist})"
                )
                return

            wins = [t for t in all_trades if t["max_gain_pct"] >= WIN_THRESHOLD_PCT]
            win_count = len(wins)
            win_rate = (win_count / total_alerts) * 100
            avg_gain = np.mean([t["max_gain_pct"] for t in all_trades])

            self.history["total_alerts"] += total_alerts
            self.history["total_wins"] += win_count
            cum_win_rate = (self.history["total_wins"] / self.history["total_alerts"]) * 100
            self.save_history()

            card_rows = [
                f"• {'✅' if t['max_gain_pct'] >= WIN_THRESHOLD_PCT else '❌'} <code>{t['ticker']} ${t['strike']:.0f}{t['type'][0]}</code>: Peak <b>${t['max_price']:.2f}</b> (<b>{t['max_gain_pct']:+.1f}%</b>)"
                for t in sorted(all_trades, key=lambda x: x["max_gain_pct"], reverse=True)
            ]
            msg = (
                f"<b>📊 [0DTE DAILY SCORECARD]</b>\n"
                f"═══════════════════\n"
                f"<b>Signals:</b> {total_alerts} | <b>Win Rate:</b> <b>{win_rate:.1f}%</b> | <b>Avg Peak:</b> <b>+{avg_gain:.1f}%</b>\n"
                f"═══════════════════\n"
                f"{chr(10).join(card_rows)}\n"
                f"═══════════════════\n"
                f"📈 <b>All-Time 0DTE Win Rate:</b> <b>{cum_win_rate:.1f}%</b>"
            )
            send_telegram_alert(msg)


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

        today = pd.Timestamp(ny_now().date())
        zero_dte = [exp for exp in expirations[:2] if pd.Timestamp(exp).normalize() == today]
        if not zero_dte:
            return

        exp_str = zero_dte[0]
        opt_chain = ticker.option_chain(exp_str)
        self.tracker.update_and_check_milestones(ticker_symbol, opt_chain)

        T = 0.5 / 365.25
        all_gex_rows = []
        candidates = []

        for opt_type, df_side in [("CALL", opt_chain.calls), ("PUT", opt_chain.puts)]:
            for _, row in df_side.iterrows():
                strike = float(row['strike'])
                oi = float(row.get('openInterest', 0) or 0)
                vol = float(row.get('volume', 0) or 0)
                iv = float(row.get('impliedVolatility', 0) or 0)
                price = float(row.get('lastPrice', 0) or 0)
                bid = float(row.get('bid', 0) or 0)
                ask = float(row.get('ask', 0) or 0)

                if oi > 0 and iv > 0.01:
                    gamma = black_scholes_gamma(spot, strike, T, 0.045, iv)
                    sign = 1.0 if opt_type == "CALL" else -1.0
                    gex_val = sign * gamma * (spot ** 2) * 0.01 * 100 * oi
                    all_gex_rows.append({
                        'strike': strike,
                        'call_gex': gex_val if sign > 0 else 0.0,
                        'put_gex': gex_val if sign < 0 else 0.0,
                        'net_gex': gex_val
                    })

                premium = vol * price * 100
                vol_oi = vol / oi if oi > 0 else 0
                if not (vol_oi >= MIN_VOL_OI_RATIO and premium >= MIN_WHALE_PREMIUM and 0.05 <= price <= MAX_CONTRACT_PRICE):
                    continue
                if bid <= 0 or ask <= 0 or ask < bid:
                    continue
                spread_pct = ((ask - bid) / ask) * 100
                if spread_pct > MAX_SPREAD_PCT:
                    continue

                candidates.append({
                    'ticker': ticker_symbol, 'spot': spot, 'type': opt_type,
                    'strike': strike, 'expiry': exp_str, 'vol': vol, 'oi': oi,
                    'vol_oi': vol_oi, 'price': price, 'bid': bid, 'ask': ask,
                    'spread_pct': round(spread_pct, 1), 'contract_cost': price * 100,
                    'premium': premium, 'contract_id': f"{ticker_symbol}_0DTE_{opt_type[0]}_{strike}_{exp_str}"
                })

        if not all_gex_rows or not candidates:
            return

        df = pd.DataFrame(all_gex_rows)
        agg = df.groupby('strike').sum().reset_index().sort_values('strike')
        call_wall = float(agg.loc[agg['call_gex'].idxmax(), 'strike'])
        put_wall = float(agg.loc[agg['put_gex'].idxmin(), 'strike'])
        agg['cum_gex'] = agg['net_gex'].cumsum()
        zero_flip = spot
        for i in range(len(agg) - 1):
            if agg.iloc[i]['cum_gex'] <= 0 and agg.iloc[i + 1]['cum_gex'] > 0:
                zero_flip = float(agg.iloc[i]['strike'])
                break

        passing = []
        for t in candidates:
            if t['type'] == 'CALL':
                runway = ((call_wall - spot) / spot) * 100
                if spot >= zero_flip and runway >= MIN_RUNWAY_PCT:
                    t['runway_pct'] = round(runway, 2)
                    t['target_wall'] = round(call_wall, 2)
                    passing.append(t)
            else:
                runway = ((spot - put_wall) / spot) * 100
                if spot < zero_flip and runway >= MIN_RUNWAY_PCT:
                    t['runway_pct'] = round(runway, 2)
                    t['target_wall'] = round(put_wall, 2)
                    passing.append(t)

        if passing:
            passing.sort(key=lambda x: x['premium'], reverse=True)
            top = passing[0]
            cid = top['contract_id']
            last_vol = self.alerted_contracts.get(cid, 0)
            if last_vol == 0 or top['vol'] >= (last_vol * 1.5):
                self.alerted_contracts[cid] = top['vol']
                msg_id = self.dispatch_0dte_alert(top)
                self.tracker.register_alert(top, message_id=msg_id)

    def dispatch_0dte_alert(self, trade):
        is_call = trade['type'] == 'CALL'
        badge = "⚡ 0DTE GAMMA SQUEEZE (CALL)" if is_call else "💥 0DTE CASCADE (PUT)"
        wall_label = "Call Wall" if is_call else "Put Wall"

        msg = (
            f"<b>🚨 [NEW 0DTE ENTRY] {badge}</b>\n"
            f"🟢 <b>OPEN RUNWAY (STRICT PASS)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Contract:</b> <code>{trade['ticker']} ${trade['strike']:.2f} {trade['type']}</code>\n"
            f"<b>Expiration:</b> <b>{trade['expiry']} (0DTE Today)</b>\n"
            f"<b>Entry Price:</b> <b>${trade['price']:.2f}</b> (${trade['contract_cost']:,.0f} / contract)\n"
            f"<b>Option Spread:</b> <b>{trade['spread_pct']}%</b> (${trade['bid']:.2f} Bid / ${trade['ask']:.2f} Ask)\n"
            f"<b>Runway to {wall_label}:</b> <b>{trade['runway_pct']}%</b> (Target: ${trade['target_wall']:.2f})\n"
            f"<b>Whale Outlay:</b> <b>${trade['premium'] / 1e6:.2f} Million</b> ({int(trade['vol']):,} contracts)\n"
            f"<b>Volume Multiple:</b> <b>{trade['vol_oi']:.1f}x OI</b>\n"
            f"<b>Underlying Spot:</b> ${trade['spot']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        return send_telegram_alert(msg)

    def start_monitoring(self):
        send_telegram_alert(
            "<b>⚡ 0DTE BOT DEPLOYED</b>\n"
            "• <b>Check-ins:</b> 9:30 AM, 12:30 PM, 4:00 PM ET\n"
            "• <b>Daily Scorecard:</b> 4:05 PM ET\n"
            "• <b>Market-Hours Only:</b> Ignores stale after-hours prints"
        )
        while True:
            now = ny_now()
            self.tracker.check_scheduled_messages()

            if is_market_open(now):
                for ticker in WATCHLIST:
                    try:
                        self.scan_ticker(ticker)
                    except Exception as e:
                        print(f"Error scanning 0DTE {ticker}: {e}")
                    time.sleep(1)
                time.sleep(CHECK_INTERVAL_SECONDS)
            else:
                time.sleep(30)


if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()
    alerter = ZeroDteLottoAlerter()
    alerter.start_monitoring()
