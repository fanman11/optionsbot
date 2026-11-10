# Quantum GEX & Unusual Options Telegram Alerter

Automated institutional scanner that monitors Gamma Exposure (GEX), Zero-Gamma Flips, and Unusual Options Activity (UOA whale sweeps), delivering instant alert cards to Telegram.

## 🚀 Two Free Ways to Run

### Method 1: GitHub Actions (Zero External Servers Needed)
This repository includes `.github/workflows/gex_alerts.yml` which automatically runs every 15 minutes during US market hours directly on GitHub's cloud runners:
1. Go to **Settings** &rarr; **Secrets and variables** &rarr; **Actions** in your GitHub repo.
2. Click **New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`: Your token from `@BotFather`
   - `TELEGRAM_CHAT_ID`: Your numeric ID from `@userinfobot`
3. Go to the **Actions** tab and enable workflows. It will scan and buzz your Telegram automatically!

### Method 2: Render.com or Railway (Continuous 60-Second Loop)
Connect this repository to [Render.com](https://render.com) as a free Web Service:
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python telegram_gex_alerter.py`
- **Environment Variables**: Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## 📦 Local Testing
```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHAT_ID="your_id"
python telegram_gex_alerter.py
```
