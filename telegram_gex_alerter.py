#!/usr/bin/env python3
"""
QUANTUM GEX // Institutional Whale Alerter (No Spread Filter + Explicit Strike Vol/OI)
-------------------------------------------------------------------------------------
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

WATCHLIST = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD"]

MIN_VOL_OI_RATIO = 25.0
MIN_WHALE_PREMIUM = 5000000.0
MIN_CONTRACT_PRICE = 1.00
MAX_CONTRACT_PRICE = 5.00
MIN_RUNWAY_PCT = 1.5
CHECK_INTERVAL_SECONDS = 60
WIN_THRESHOLD_PCT = 40.0
SILENT_TARGET_HITS = False

NY_TZ = ZoneInfo("America/New_York")
PERFORMANCE_FILE = "alerts_performance.json"

STATE_LOCK = threading.Lock()
LIVE_STATE = {
    "last_updated": None,
    "tickers": {},
    "alerts": [],
    "scan_logs": [],
    "stats": {"total_alerts": 0, "total_wins": 0, "win_rate": 0.0}
}


def ny_now() -> datetime:
    return datetime.now(NY_TZ)


def format_elapsed(entry_dt: datetime, hit_dt: datetime) -> str:
    total_mins = max(int((hit_dt - entry_dt).total_seconds() // 60), 1)
    if total_mins < 60:
        return f"{total_mins} min" if total_mins == 1 else f"{total_mins} mins"
    hours, mins = divmod(total_mins, 60)
    return f"{hours}h {mins}m"


def is_market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= mins < (16 * 60)


def add_scan_log(ticker: str, status: str, reason: str):
    entry = {
        "time": ny_now().strftime("%I:%M:%S %p ET"),
        "ticker": ticker,
        "status": status,
        "reason": reason
    }
    with STATE_LOCK:
        LIVE_STATE["scan_logs"].insert(0, entry)
        LIVE_STATE["scan_logs"] = LIVE_STATE["scan_logs"][:35]


class HealthAndApiHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/status"):
            with STATE_LOCK:
                payload = json.dumps({
                    "last_updated": LIVE_STATE["last_updated"],
                    "tickers": list(LIVE_STATE["tickers"].values()),
                    "alerts": LIVE_STATE["alerts"],
                    "scan_logs": LIVE_STATE["scan_logs"],
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
            self.wfile.write(b"GEX ALERTER ACTIVE")

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
        dte = (exp_dt - ny_now().date()).days
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
                "vol": int(t.get("vol", 0)),
                "oi": int(t.get("oi", 0)),
                "vol_oi": round(t.get("vol_oi", 0.0), 1),
                "runway_pct": t.get("runway_pct", 0.0),
                "target_wall": t.get("target_wall", 0.0),
                "alert_time": t["alert_time"],
                "milestones_hit": sorted(list(t["milestones_hit"])),
                "milestone_times": t.get("milestone_times", {})
            })

        total = self.history.get("total_alerts", 0)
        wins = self.history.get("total_wins", 0)
        win_rate = round((wins / total) * 100, 1) if total > 0 else 0.0

        with STATE_LOCK:
            LIVE_STATE["alerts"] = serializable_alerts
            LIVE_STATE["stats"] = {"total_alerts": total, "total_wins": wins, "win_rate": win_rate}

    def register_alert(self, trade, message_id=None):
        cid = trade["contract_id"]
        now = ny_now()
        if cid not in self.active_trades:
            self.active_trades[cid] = {
                "ticker": trade["ticker"],
                "type": trade["type"],
                "strike": trade["strike"],
                "expiry": trade["expiry"],
                "entry_price": trade["price"],
                "contract_cost": trade["contract_cost"],
                "premium": trade["premium"],
                "vol": trade["vol"],
                "oi": trade["oi"],
                "vol_oi": trade["vol_oi"],
                "runway_pct": trade["runway_pct"],
                "target_wall": trade["target_wall"],
                "max_price": trade["price"],
                "max_gain_pct": 0.0,
                "milestones_hit": set(),
                "milestone_times": {},
                "entry_dt": now,
                "entry_message_id": message_id,
                "alert_time": now.strftime("%I:%M %p ET")
            }
        elif message_id and not self.active_trades[cid].get("entry_message_id"):
            self.active_trades[cid]["entry_message_id"] = message_id
        self.sync_live_state()

    def update_and_check_milestones(self, ticker_symbol, opt_chain):
        updated = False
        now = ny_now()
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
            elapsed = format_elapsed(t["entry_dt"], now)

            if gain >= 50.0 and 50 not in t["milestones_hit"]:
                t["milestones_hit"].add(50)
                t["milestone_times"]["50"] = elapsed
                self.dispatch_milestone(t, current_price, gain, elapsed, "✅ [TARGET HIT +50%]", "Take partial profits & move stop to breakeven.")
                updated = True
            if gain >= 100.0 and 100 not in t["milestones_hit"]:
                t["milestones_hit"].add(100)
                t["milestone_times"]["100"] = elapsed
                self.dispatch_milestone(t, current_price, gain, elapsed, "🏆 [TARGET HIT +100% // 2X]", "Position doubled. Lock in risk-free runner.")
                updated = True
            if gain >= 200.0 and 200 not in t["milestones_hit"]:
                t["milestones_hit"].add(200)
                t["milestone_times"]["200"] = elapsed
                self.dispatch_milestone(t, current_price, gain, elapsed, "💎 [TARGET HIT +200%+]", "Mega institutional gamma expansion.")
                updated = True
        if updated:
            self.sync_live_state()

    def dispatch_milestone(self, t, current_price, gain, elapsed, header_tag, advice):
        exp_display = format_expiry(t["expiry"])
        msg = (
            f"<b>{header_tag} {t['ticker']} ${t['strike']:.0f}{t['type'][0]}</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"<b>Contract:</b> <code>{t['ticker']} ${t['strike']:.2f} {t['type']}</code>\n"
            f"<b>Expiration:</b> <b>{exp_display}</b>\n"
            f"<b>Entry:</b> ${t['entry_price']:.2f} &rarr; <b>Peak: ${current_price:.2f} (+{gain:.1f}%)</b>\n"
            f"<b>Time to Target:</b> <b>{elapsed}</b>\n"
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
            self.sync_live_state()
            send_telegram_alert(
                "<b>🔔 [MARKET OPEN] Main Whale Bot Online</b>\n"
                f"Scanning {', '.join(WATCHLIST)} every {CHECK_INTERVAL_SECONDS}s (ET)."
            )

        if (12 * 60 + 30) <= mins <= (12 * 60 + 40) and self.sent_events.get("midday") != today_str:
            self.sent_events["midday"] = today_str
            send_telegram_alert(
                "<b>☀️ [MID-DAY CHECK-IN] Main Bot Running</b>\n"
                f"Active alerts tracked so far today: <b>{len(self.active_trades)}</b>"
            )

        if (16 * 60) <= mins <= (16 * 60 + 4) and self.sent_events.get("close") != today_str:
            self.sent_events["close"] = today_str
            send_telegram_alert(
                "<b>🔕 [MARKET CLOSED] Main Bot Paused</b>\n"
                "Scanning paused until tomorrow at 9:30 AM ET. Daily summary coming at 4:05 PM ET."
            )

        if (16 * 60 + 5) <= mins <= (16 * 60 + 25) and self.sent_events.get("scorecard") != today_str:
            self.sent_events["scorecard"] = today_str
            all_trades = list(self.active_trades.values()) + self.completed_today
            total_alerts = len(all_trades)

            if total_alerts == 0:
                total_hist = self.history.get("total_alerts", 0)
                wins_hist = self.history.get("total_wins", 0)
                cum_wr = (wins_hist / total_hist) * 100 if total_hist > 0 else 0.0
                send_telegram_alert(
                    "<b>📊 [DAILY SCORECARD]</b>\n"
                    "═══════════════════\n"
                    "<b>Alerts Today:</b> 0 (No trades met your 25x / $5M+ / Open Runway rules today)\n"
                    f"📈 <b>All-Time Win Rate:</b> <b>{cum_wr:.1f}%</b> ({wins_hist}/{total_hist})"
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
            self.sync_live_state()

            card_rows = []
            for t in sorted(all_trades, key=lambda x: x["max_gain_pct"], reverse=True):
                status_icon = "✅" if t["max_gain_pct"] >= WIN_THRESHOLD_PCT else "❌"
                times = t.get("milestone_times", {})
                speed_parts = [f"+{k}% in {times[k]}" for k in ("50", "100", "200") if k in times]
                speed_str = f" ({', '.join(speed_parts)})" if speed_parts else ""
                card_rows.append(
                    f"• {status_icon} <code>{t['ticker']} ${t['strike']:.0f}{t['type'][0]} ({t['expiry']})</code>: "
                    f"Peak <b>${t['max_price']:.2f}</b> (<b>{t['max_gain_pct']:+.1f}%</b>){speed_str}"
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


class MegaWhaleAlerter:
    def __init__(self):
        self.alerted_contracts = {}
        self.tracker = PerformanceTracker()

    def evaluate_option_row(self, ticker_symbol, spot, opt_type, row, exp_str):
        strike = float(row['strike'])
        oi = float(row.get('openInterest', 0) or 0)
        vol = float(row.get('volume', 0) or 0)
        price = float(row.get('lastPrice', 0) or 0)

        premium = vol * price * 100
        vol_oi = vol / oi if oi > 0 else 0

        if not (vol_oi >= MIN_VOL_OI_RATIO and premium >= MIN_WHALE_PREMIUM and MIN_CONTRACT_PRICE <= price <= MAX_CONTRACT_PRICE):
            return None

        return {
            'ticker': ticker_symbol, 'spot': spot, 'type': opt_type, 'strike': strike,
            'expiry': exp_str, 'vol': vol, 'oi': oi, 'vol_oi': vol_oi, 'price': price,
            'contract_cost': price * 100, 'premium': premium,
            'contract_id': f"{ticker_symbol}_{opt_type[0]}_{strike}_{exp_str}"
        }

    def scan_ticker(self, ticker_symbol: str):
        ticker = yf.Ticker(ticker_symbol)
        history = ticker.history(period="1d")
        if history.empty:
            add_scan_log(ticker_symbol, "QUIET", "No price data returned")
            return
        spot = float(history['Close'].iloc[-1])

        expirations = ticker.options
        if not expirations:
            return

        all_gex_rows = []
        candidate_whale_trades = []
        filtered_reasons = []
        max_vol_oi_seen = 0.0
        today = pd.Timestamp(ny_now().date())

        for exp_str in expirations[:3]:
            exp_date = pd.Timestamp(exp_str)
            dte = max((exp_date - today).days, 0.5)
            T = dte / 365.25
            opt_chain = ticker.option_chain(exp_str)
            self.tracker.update_and_check_milestones(ticker_symbol, opt_chain)

            for opt_type, df_side in [("CALL", opt_chain.calls), ("PUT", opt_chain.puts)]:
                for _, row in df_side.iterrows():
                    strike = float(row['strike'])
                    oi = float(row.get('openInterest', 0) or 0)
                    vol = float(row.get('volume', 0) or 0)
                    iv = float(row.get('impliedVolatility', 0) or 0)

                    if oi > 0:
                        max_vol_oi_seen = max(max_vol_oi_seen, vol / oi)
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

                    trade = self.evaluate_option_row(ticker_symbol, spot, opt_type, row, exp_str)
                    if trade:
                        candidate_whale_trades.append(trade)

        call_wall, put_wall, zero_flip = spot, spot, spot
        if all_gex_rows:
            df = pd.DataFrame(all_gex_rows)
            agg = df.groupby('strike').sum().reset_index().sort_values('strike')
            total_net_gex_m = round(float(agg['net_gex'].sum()) / 1e6, 1)
            call_wall = float(agg.loc[agg['call_gex'].idxmax(), 'strike'])
            put_wall = float(agg.loc[agg['put_gex'].idxmin(), 'strike'])

            agg['cum_gex'] = agg['net_gex'].cumsum()
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
                    "ticker": ticker_symbol, "spot": round(spot, 2),
                    "net_gex": total_net_gex_m, "zero_flip": round(zero_flip, 2),
                    "call_wall": round(call_wall, 2), "put_wall": round(put_wall, 2),
                    "strikes": strikes_payload
                }
                LIVE_STATE["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        passing_trades = []
        for trade in candidate_whale_trades:
            if trade['type'] == 'CALL':
                runway_pct = ((call_wall - spot) / spot) * 100
                right_side = spot >= zero_flip
                target_wall = call_wall
            else:
                runway_pct = ((spot - put_wall) / spot) * 100
                right_side = spot < zero_flip
                target_wall = put_wall

            if not right_side:
                filtered_reasons.append(f"Filtered {ticker_symbol} ${trade['strike']:.0f}{trade['type'][0]}: Against Zero Flip (${zero_flip:.2f})")
            elif runway_pct < MIN_RUNWAY_PCT:
                filtered_reasons.append(f"Filtered {ticker_symbol} ${trade['strike']:.0f}{trade['type'][0]}: Only {runway_pct:.2f}% to Wall (${target_wall:.2f})")
            else:
                trade['runway_pct'] = round(runway_pct, 2)
                trade['target_wall'] = round(target_wall, 2)
                passing_trades.append(trade)

        if passing_trades:
            passing_trades.sort(key=lambda x: x['premium'], reverse=True)
            top_trade = passing_trades[0]
            cid = top_trade['contract_id']
            last_vol = self.alerted_contracts.get(cid, 0)

            if last_vol == 0 or top_trade['vol'] >= (last_vol * 1.4):
                self.alerted_contracts[cid] = top_trade['vol']
                msg_id = self.dispatch_whale_alert(top_trade)
                self.tracker.register_alert(top_trade, message_id=msg_id)
                add_scan_log(ticker_symbol, "ALERT", f"Sent ${top_trade['strike']:.0f}{top_trade['type'][0]} | Vol {int(top_trade['vol']):,} vs OI {int(top_trade['oi']):,} | Runway {top_trade['runway_pct']}%")
        elif filtered_reasons:
            add_scan_log(ticker_symbol, "FILTERED", filtered_reasons[0])
        else:
            add_scan_log(ticker_symbol, "QUIET", f"No $5M+ whale met 25x Vol/OI (max seen: {max_vol_oi_seen:.1f}x)")

    def dispatch_whale_alert(self, trade):
        is_call = trade['type'] == 'CALL'
        badge = "🟢 BULLISH WHALE" if is_call else "🔴 BEARISH WHALE"
        wall_label = "Call Wall" if is_call else "Put Wall"
        exp_display = format_expiry(trade['expiry'])

        msg = (
            f"<b>🚨 [NEW ENTRY] {badge}: {trade['ticker']}</b>\n"
            f"🟢 <b>OPEN RUNWAY (STRICT PASS)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Contract:</b> <code>{trade['ticker']} ${trade['strike']:.2f} {trade['type']}</code>\n"
            f"<b>Expiration:</b> <b>{exp_display}</b>\n"
            f"<b>Entry Price:</b> <b>${trade['price']:.2f}</b> (${trade['contract_cost']:,.0f} / contract)\n"
            f"<b>Strike Volume:</b> <b>{int(trade['vol']):,}</b>\n"
            f"<b>Strike Open Interest:</b> <b>{int(trade['oi']):,}</b> (<b>{trade['vol_oi']:.1f}x Vol/OI</b>)\n"
            f"<b>Runway to {wall_label}:</b> <b>{trade['runway_pct']}%</b> (Target: ${trade['target_wall']:.2f})\n"
            f"<b>Whale Premium:</b> <b>${trade['premium'] / 1e6:.2f} Million</b>\n"
            f"<b>Underlying Spot:</b> ${trade['spot']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        return send_telegram_alert(msg)

    def start_monitoring(self):
        send_telegram_alert(
            "<b>🎯 MAIN WHALE BOT DEPLOYED</b>\n"
            "• <b>Filters:</b> 25x Vol/OI &bull; $5M+ &bull; $1-$5 &bull; Strict Open Runway\n"
            "• <b>Spread Filter:</b> Removed (shows Strike Volume &amp; OI directly)\n"
            "• <b>Check-ins:</b> 9:30 AM, 12:30 PM, 4:00 PM ET | <b>Scorecard:</b> 4:05 PM ET"
        )

        while True:
            now = ny_now()
            self.tracker.check_scheduled_messages()

            if is_market_open(now):
                for ticker in WATCHLIST:
                    try:
                        self.scan_ticker(ticker)
                    except Exception as e:
                        add_scan_log(ticker, "FILTERED", f"Scan error: {str(e)[:60]}")
                    time.sleep(1)
                time.sleep(CHECK_INTERVAL_SECONDS)
            else:
                time.sleep(30)


if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    alerter = MegaWhaleAlerter()
    alerter.start_monitoring()
