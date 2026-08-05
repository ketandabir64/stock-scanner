#!/usr/bin/env python3
# =============================================================================
# 📊 COMBINED SUPER SCANNER + AUTO EMAIL  (v2 - bug-fixed + VWAP sheet added)
# Reads email credentials from environment variables
# Designed to run top-to-bottom in Google Colab
# =============================================================================
#
# CHANGES IN THIS VERSION (v2):
#  1. auto_adjust=False added to every yf.download() call.
#     -> Newer yfinance versions default to auto_adjust=True, which returns
#        DIVIDEND/SPLIT-ADJUSTED close prices instead of the actual traded
#        price. That silently made CMP / VWAP / DMA figures drift away from
#        what you'd see on NSE. This was likely the #1 cause of "data not
#        looking 100% accurate".
#  2. Removed the redundant second download per stock (old Sheet-3 logic used
#     to re-download 1y of data separately from the 2y data already fetched
#     for Sheet 1/2 -- two separate network calls fetched at two different
#     moments, which could return slightly different data and doubled your
#     chance of hitting Yahoo's rate limit). Now we reuse the single 2y
#     download for everything.
#  3. Batched downloads: instead of looping and calling yf.download() once
#     per stock (200+ individual network calls -> frequent timeouts/rate
#     limit failures), we now download all tickers together in a couple of
#     batched calls. This is dramatically more reliable in Colab.
#  4. Fixed a real formula bug in adx_c(): the -DM (minus directional
#     movement) calculation used low.diff().abs() instead of -low.diff().
#     This made ADX/+DI/-DI numerically wrong in cases where the day's low
#     was rising. Rewritten to the standard Wilder formula.
#  5. Added a retry-with-backoff wrapper around downloads so a single flaky
#     network blip doesn't wipe out the whole run.
#  6. NEW Sheet 4 "VWAP_Data": for every stock in your list, shows Weekly
#     VWAP, Monthly VWAP, Daily (today's intraday) VWAP, plus
#     Monthly-vs-Weekly % difference and Weekly-vs-Daily % difference.
#  7. Weekly/Monthly VWAP now use a ROLLING TRADING-DAY WINDOW instead of
#     calendar month/week: last 5 trading days = Weekly VWAP, last 21
#     trading days = Monthly VWAP. (Calendar-anchored version could show
#     identical Weekly/Monthly VWAP right after a month started on the
#     same day as the current week - this fixes that.)
#  8. News sentiment is shown as an informational column in Sheet 1/2/3
#     (🟢/🔴/⚪ + score + headline) but is NOT used as a filter - stocks
#     stay in the list regardless of sentiment, so nothing gets excluded.
# =============================================================================

import yfinance as yf
import pandas as pd
import numpy as np
import warnings
import logging
import smtplib
import os
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.filterwarnings('ignore')

today_date = datetime.now().strftime("%d-%m-%Y")

# =============================================================================
# 🔧 EMAIL CONFIGURATION — Environment Variables se read karta hai
# =============================================================================
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'YOUR_EMAIL@gmail.com')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD', 'YOUR_APP_PASSWORD')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'YOUR_EMAIL@gmail.com')

# =============================================================================
# ROBUST DOWNLOAD HELPER (retry + backoff so flaky network doesn't kill the run)
# =============================================================================
def safe_download(tickers, retries=3, delay=5, **kwargs):
    """Wrapper around yf.download with retries. Always sets auto_adjust=False
    so prices match actual traded (NSE) prices instead of dividend/split
    adjusted prices."""
    kwargs.setdefault('auto_adjust', False)
    kwargs.setdefault('progress', False)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            df = yf.download(tickers, **kwargs)
            if df is not None and not df.empty:
                return df
            last_err = "empty dataframe returned"
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(delay)
    print(f"   ⚠️ Download failed after {retries} attempts for {tickers if isinstance(tickers, str) else f'{len(tickers)} tickers'}: {last_err}")
    return pd.DataFrame()

# =============================================================================
# 🌍 GLOBAL MARKET MOOD (NEW in v3)
# Fetches overnight US markets + today's Asian markets + Crude/Dollar/USDINR
# so the 8:15 AM email tells you the "mood" before Indian market opens.
# =============================================================================
GLOBAL_TICKERS = {
    'Dow Jones':      '^DJI',
    'Nasdaq':         '^IXIC',
    'S&P 500':        '^GSPC',
    'Nikkei 225':     '^N225',
    'Hang Seng':      '^HSI',
    'FTSE 100':       '^FTSE',
    'Crude Oil(WTI)': 'CL=F',
    'Dollar Index':   'DX-Y.NYB',
    'USD/INR':        'INR=X',
}
# These are used to decide overall Bullish/Bearish/Neutral mood
EQUITY_INDICES_FOR_MOOD = ['Dow Jones', 'Nasdaq', 'S&P 500', 'Nikkei 225', 'Hang Seng', 'FTSE 100']

def get_global_market_mood():
    """Returns (mood_label, mood_emoji, avg_change_pct, rows) where rows is a
    list of dicts with Name/LTP/Change% for each global instrument."""
    rows = []
    try:
        data = safe_download(list(GLOBAL_TICKERS.values()), period='5d', interval='1d', group_by='ticker')
    except Exception:
        data = pd.DataFrame()

    for name, tkr in GLOBAL_TICKERS.items():
        try:
            if isinstance(data.columns, pd.MultiIndex):
                closes = data[tkr]['Close'].dropna()
            else:
                closes = data['Close'].dropna()
            if len(closes) < 2:
                rows.append({'Name': name, 'LTP': 'N/A', 'Change%': None})
                continue
            last, prev = closes.iloc[-1], closes.iloc[-2]
            chg_pct = ((last - prev) / prev) * 100
            rows.append({'Name': name, 'LTP': round(float(last), 2), 'Change%': round(float(chg_pct), 2)})
        except Exception:
            rows.append({'Name': name, 'LTP': 'N/A', 'Change%': None})

    equity_changes = [r['Change%'] for r in rows if r['Name'] in EQUITY_INDICES_FOR_MOOD and r['Change%'] is not None]
    avg_change = sum(equity_changes) / len(equity_changes) if equity_changes else 0.0

    if avg_change > 0.3:
        mood_label, mood_emoji = "Bullish", "🟢"
    elif avg_change < -0.3:
        mood_label, mood_emoji = "Bearish", "🔴"
    else:
        mood_label, mood_emoji = "Neutral / Mixed", "⚪"

    return mood_label, mood_emoji, round(avg_change, 2), rows

def build_global_mood_html(mood_label, mood_emoji, avg_change, rows):
    row_html = ""
    for r in rows:
        chg = r['Change%']
        if chg is None:
            color = "#7f8c8d"
            chg_display = "N/A"
        else:
            color = "#27ae60" if chg >= 0 else "#e74c3c"
            chg_display = f"{'+' if chg >= 0 else ''}{chg}%"
        row_html += f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;">{r['Name']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{r['LTP']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right; color: {color}; font-weight: bold;">{chg_display}</td>
        </tr>"""

    return f"""
    <h3 style="color: #2c3e50; margin-bottom: 4px;">🌍 Global Market Mood: {mood_emoji} {mood_label} (avg {avg_change:+.2f}%)</h3>
    <table style="border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 15px;">
        <tr style="background: #34495e; color: white;">
            <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Index / Asset</th>
            <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Last</th>
            <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Change</th>
        </tr>
        {row_html}
    </table>
    """

# =============================================================================
# NEWS SENTIMENT FUNCTION
# =============================================================================
def get_news_sentiment(ticker):
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        if not news or len(news) == 0:
            return "⚪ No News", 0, "N/A"

        latest = news[0]
        title = latest.get('title', '')
        summary = latest.get('summary', '')
        text = f"{title} {summary}".lower()

        positive = ['profit', 'growth', 'rise', 'gain', 'bull', 'up', 'high', 'record',
                    'strong', 'beat', 'surge', 'rally', 'buy', 'outperform', 'positive',
                    'bonus', 'dividend', 'expansion', 'deal', 'contract', 'order',
                    'फायदा', 'मुनाफा', 'तेजी', 'बढ़त', 'ऊपर', 'मजबूत', 'लाभ']
        negative = ['loss', 'fall', 'drop', 'bear', 'down', 'low', 'weak', 'miss',
                    'decline', 'sell', 'underperform', 'negative', 'crash', 'debt',
                    'fraud', 'penalty', 'investigation', 'default',
                    'घाटा', 'गिरावट', 'नुकसान', 'नीचे', 'कमजोर', 'बिकवाली', 'हानि']

        pos_score = sum(1 for w in positive if w in text)
        neg_score = sum(1 for w in negative if w in text)

        if pos_score > neg_score:
            return "🟢 Bullish", pos_score - neg_score, title[:120]
        elif neg_score > pos_score:
            return "🔴 Bearish", neg_score - pos_score, title[:120]
        else:
            return "⚪ Neutral", 0, title[:120]

    except Exception:
        return "⚪ Error", 0, "N/A"

# =============================================================================
# INDICATORS
# =============================================================================
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calculate_vwap_hlc3(df):
    """Session/period VWAP using typical price (H+L+C)/3 weighted by volume."""
    if df is None or df.empty or df['Volume'].sum() == 0:
        return np.nan
    hlc3 = (df['High'] + df['Low'] + df['Close']) / 3
    vwap = (hlc3 * df['Volume']).cumsum() / df['Volume'].cumsum()
    return vwap.iloc[-1]

def ema(s, p): return s.ewm(span=p, adjust=False).mean()
def sma(s, p): return s.rolling(window=p).mean()

def rsi_c(s, p=14):
    d = s.diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = (-d.where(d < 0, 0)).rolling(p).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

def adx_c(h, l, c, p=14):
    """Standard Wilder ADX / +DI / -DI.
    FIX: previous version used low.diff().abs() for down-move, which is
    wrong whenever the low is rising (abs() incorrectly turned a
    'no down-move' day into a positive down-move). Correct down-move is
    -low.diff() (kept negative, i.e. excluded, when low is rising)."""
    up_move = h.diff()
    down_move = -l.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(p).mean()

    plus_di = 100 * (plus_dm.rolling(p).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(p).mean() / atr.replace(0, np.nan))

    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.rolling(p).mean()
    return adx, plus_di, minus_di

def macd_c(c):
    ml = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    return ml, ml.ewm(span=9, adjust=False).mean()

def atr_c(h, l, c, p=14):
    return pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1).rolling(p).mean()

def calc_vwap_rolling(df, window):
    """Rolling-window VWAP series (HLC3-based) over the last `window` trading
    days (NOT calendar month/week - a fixed trading-day count). Returns a
    Series aligned to df's index; first (window-1) values are NaN.
    e.g. window=5  -> "weekly" VWAP (last 5 trading days)
         window=21 -> "monthly" VWAP (last 21 trading days)"""
    hlc3 = (df['High'] + df['Low'] + df['Close']) / 3
    vol_hlc3 = df['Volume'] * hlc3
    roll_vol = df['Volume'].rolling(window).sum()
    roll_vol_hlc3 = vol_hlc3.rolling(window).sum()
    return roll_vol_hlc3 / roll_vol.replace(0, np.nan)

# =============================================================================
# EMAIL FUNCTION
# =============================================================================
def send_email_with_attachment(excel_path, summary_html):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = f"📊 Stock Scanner Results — {today_date}"

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <h2 style="color: #2c3e50;">📊 Daily Stock Scanner Report</h2>
            <p><b>Date:</b> {today_date}</p>
            <p><b>Time:</b> {datetime.now().strftime("%H:%M IST")}</p>
            <hr>
            {summary_html}
            <hr>
            <p style="color: #7f8c8d; font-size: 12px;">
                Auto-generated by Combined Super Scanner<br>
                Sheets: Main Breakout | Weekly vs Monthly | VWAP Crossover | VWAP Data
            </p>
        </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))

        if os.path.exists(excel_path):
            with open(excel_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(excel_path)}"')
            msg.attach(part)

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"✅ Email sent successfully to {RECEIVER_EMAIL}")
        return True

    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False

# =============================================================================
# STOCK LIST
# =============================================================================
my_stocks = [
    '360ONE.NS', 'ABB.NS', 'APLAPOLLO.NS', 'AUBANK.NS', 'ADANIENSOL.NS',
    'ADANIENT.NS', 'ADANIGREEN.NS', 'ADANIPORTS.NS', 'ADANIPOWER.NS', 'ABCAPITAL.NS',
    'ALKEM.NS', 'AMBER.NS', 'AMBUJACEM.NS', 'ANGELONE.NS', 'APOLLOHOSP.NS',
    'ASHOKLEY.NS', 'ASIANPAINT.NS', 'ASTRAL.NS', 'AUROPHARMA.NS', 'DMART.NS',
    'AXISBANK.NS', 'BSE.NS', 'BAJAJ-AUTO.NS', 'BAJFINANCE.NS', 'BAJAJFINSV.NS',
    'BAJAJHLDNG.NS', 'BANDHANBNK.NS', 'BANKBARODA.NS', 'BANKINDIA.NS', 'BDL.NS',
    'BEL.NS', 'BHARATFORG.NS', 'BHEL.NS', 'BPCL.NS', 'BHARTIARTL.NS',
    'BIOCON.NS', 'BLUESTARCO.NS', 'BOSCHLTD.NS', 'BRITANNIA.NS', 'CGPOWER.NS',
    'CANBK.NS', 'CDSL.NS', 'CHOLAFIN.NS', 'CIPLA.NS', 'COALINDIA.NS',
    'COCHINSHIP.NS', 'COFORGE.NS', 'COLPAL.NS', 'CAMS.NS', 'CONCOR.NS',
    'CROMPTON.NS', 'CUMMINSIND.NS', 'DLF.NS', 'DABUR.NS', 'DALBHARAT.NS',
    'DELHIVERY.NS', 'DIVISLAB.NS', 'DIXON.NS', 'DRREDDY.NS', 'ETERNAL.NS',
    'EICHERMOT.NS', 'EXIDEIND.NS', 'FORCEMOT.NS', 'NYKAA.NS', 'FORTIS.NS',
    'GAIL.NS', 'GVT&D.NS', 'GMRAIRPORT.NS', 'GLENMARK.NS', 'GODFRYPHLP.NS',
    'GODREJCP.NS', 'GODREJPROP.NS', 'GRASIM.NS', 'HCLTECH.NS', 'HDFCAMC.NS',
    'HDFCBANK.NS', 'HDFCLIFE.NS', 'HAVELLS.NS', 'HEROMOTOCO.NS', 'HINDALCO.NS',
    'HAL.NS', 'HINDPETRO.NS', 'HINDUNILVR.NS', 'HINDZINC.NS', 'POWERINDIA.NS',
    'HYUNDAI.NS', 'ICICIBANK.NS', 'ICICIGI.NS', 'ICICIPRULI.NS', 'IDFCFIRSTB.NS',
    'ITC.NS', 'INDIANB.NS', 'IEX.NS', 'IOC.NS', 'IRFC.NS', 'IREDA.NS',
    'INDUSTOWER.NS', 'INDUSINDBK.NS', 'NAUKRI.NS', 'INFY.NS', 'INOXWIND.NS',
    'INDIGO.NS', 'JINDALSTEL.NS', 'JSWENERGY.NS', 'JSWSTEEL.NS', 'JIOFIN.NS',
    'JUBLFOOD.NS', 'KEI.NS', 'KPITTECH.NS', 'KALYANKJIL.NS', 'KAYNES.NS',
    'KFINTECH.NS', 'KOTAKBANK.NS', 'LTF.NS', 'LICHSGFIN.NS', 'LTM.NS',
    'LT.NS', 'LAURUSLABS.NS', 'LICI.NS', 'LODHA.NS', 'LUPIN.NS',
    'M&M.NS', 'MANAPPURAM.NS', 'MANKIND.NS', 'MARICO.NS', 'MARUTI.NS',
    'MFSL.NS', 'MAXHEALTH.NS', 'MAZDOCK.NS', 'MOTILALOFS.NS', 'MPHASIS.NS',
    'MCX.NS', 'MUTHOOTFIN.NS', 'NBCC.NS', 'NHPC.NS', 'NMDC.NS',
    'NTPC.NS', 'NATIONALUM.NS', 'NESTLEIND.NS', 'NAM-INDIA.NS', 'NUVAMA.NS',
    'OBEROIRLTY.NS', 'ONGC.NS', 'OIL.NS', 'PAYTM.NS', 'OFSS.NS',
    'POLICYBZR.NS', 'PGEL.NS', 'PIIND.NS', 'PNBHOUSING.NS', 'PAGEIND.NS',
    'PATANJALI.NS', 'PERSISTENT.NS', 'PETRONET.NS', 'PIDILITIND.NS', 'POLYCAB.NS',
    'PFC.NS', 'POWERGRID.NS', 'PREMIERENE.NS', 'PRESTIGE.NS', 'PNB.NS',
    'RBLBANK.NS', 'RECLTD.NS', 'RADICO.NS', 'RVNL.NS', 'RELIANCE.NS',
    'SBICARD.NS', 'SBILIFE.NS', 'SHREECEM.NS', 'SRF.NS', 'MOTHERSON.NS',
    'SHRIRAMFIN.NS', 'SIEMENS.NS', 'SOLARINDS.NS', 'SONACOMS.NS', 'SBIN.NS',
    'SAIL.NS', 'SUNPHARMA.NS', 'SUPREMEIND.NS', 'SUZLON.NS', 'SWIGGY.NS',
    'TATACONSUM.NS', 'TVSMOTOR.NS', 'TCS.NS', 'TATAELXSI.NS', 'TMPV.NS',
    'TATAPOWER.NS', 'TATASTEEL.NS', 'TECHM.NS', 'FEDERALBNK.NS', 'INDHOTEL.NS',
    'PHOENIXLTD.NS', 'TITAN.NS', 'TORNTPHARM.NS', 'TRENT.NS', 'TIINDIA.NS',
    'UNOMINDA.NS', 'UPL.NS', 'ULTRACEMCO.NS', 'UNIONBANK.NS', 'UNITDSPR.NS',
    'VBL.NS', 'VEDL.NS', 'VMM.NS', 'IDEA.NS', 'VOLTAS.NS',
    'WAAREEENER.NS', 'WIPRO.NS', 'YESBANK.NS', 'ZYDUSLIFE.NS'
]

# =============================================================================
# BATCH DOWNLOAD (reliability fix: a handful of big calls instead of 200+
# single-ticker calls; each ticker is fetched exactly ONCE for daily data)
# =============================================================================
BATCH_SIZE = 50  # yfinance can choke on very large ticker lists in one shot

def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

print(f"🔍 कुल {len(my_stocks)} शेयरों की स्कैनिंग शुरू... कृपया प्रतीक्षा करें।\n")
print("📥 Step 1/2: Downloading 2-year daily data in batches...")

daily_frames = {}
for batch in chunked(my_stocks, BATCH_SIZE):
    df_batch = safe_download(batch, period="2y", interval="1d", group_by='ticker', threads=True)
    if df_batch.empty:
        continue
    for t in batch:
        try:
            sub = df_batch[t] if isinstance(df_batch.columns, pd.MultiIndex) else df_batch
            sub = sub.dropna(how='all')
            if not sub.empty:
                daily_frames[t] = sub
        except (KeyError, Exception):
            continue
    time.sleep(1)  # small gap between batches, be gentle with Yahoo

print(f"   → {len(daily_frames)}/{len(my_stocks)} stocks got daily data")

print("📥 Step 2/2: Downloading today's intraday (5m) data in batches for Daily VWAP...")

intraday_frames = {}
for batch in chunked(my_stocks, BATCH_SIZE):
    df_batch = safe_download(batch, period="1d", interval="5m", group_by='ticker', threads=True)
    if df_batch.empty:
        continue
    for t in batch:
        try:
            sub = df_batch[t] if isinstance(df_batch.columns, pd.MultiIndex) else df_batch
            sub = sub.dropna(how='all')
            if not sub.empty:
                intraday_frames[t] = sub
        except (KeyError, Exception):
            continue
    time.sleep(1)

print(f"   → {len(intraday_frames)}/{len(my_stocks)} stocks got today's intraday data\n")

# =============================================================================
# MAIN SCANNER
# =============================================================================
results_main = []
results_sheet2 = []
results_sheet3 = []
results_sheet4 = []  # NEW: Daily / Weekly / Monthly VWAP sheet

for idx, ticker in enumerate(my_stocks):
    if (idx + 1) % 20 == 0:
        print(f"   {idx+1}/{len(my_stocks)} stocks processed...")

    data = daily_frames.get(ticker)
    if data is None or data.empty or len(data) < 200:
        continue

    try:
        close_prices = data['Close']
        high_prices = data['High']
        low_prices = data['Low']
        volume = data['Volume']

        if isinstance(close_prices, pd.DataFrame):
            close_prices = close_prices.iloc[:, 0]
            high_prices = high_prices.iloc[:, 0]
            low_prices = low_prices.iloc[:, 0]
            volume = volume.iloc[:, 0]

        cmp = close_prices.iloc[-1]
        sentiment, sent_score, news_title = get_news_sentiment(ticker)

        dma_30 = close_prices.rolling(window=30).mean().iloc[-1]
        dma_50 = close_prices.rolling(window=50).mean().iloc[-1]
        dma_200 = close_prices.rolling(window=200).mean().iloc[-1]
        dist_200_dma = ((cmp - dma_200) / dma_200) * 100

        rsi = calculate_rsi(close_prices).iloc[-1]

        last_1y_data = data.tail(252)
        high_series = last_1y_data['High']
        if isinstance(high_series, pd.DataFrame):
            high_series = high_series.iloc[:, 0]
        high_date = high_series.idxmax()
        car_data = close_prices.loc[high_date:]
        if len(car_data) < 10:
            continue
        car_values = car_data.expanding().mean()
        last_10_car = car_values.tail(10)
        car_status = 'Positive' if last_10_car.is_monotonic_increasing else 'Negative'

        # Rolling-window VWAP (fixed trading-day count, not calendar
        # month/week): last 5 trading days = "weekly", last 21 trading
        # days = "monthly". This avoids weekly == monthly VWAP collisions
        # that happened at the start of a calendar month/week.
        WEEKLY_WINDOW = 5
        MONTHLY_WINDOW = 21

        if len(data) < MONTHLY_WINDOW:
            continue

        weekly_vwap = calculate_vwap_hlc3(data.tail(WEEKLY_WINDOW))
        monthly_vwap = calculate_vwap_hlc3(data.tail(MONTHLY_WINDOW))

        if pd.isna(monthly_vwap) or pd.isna(weekly_vwap) or monthly_vwap == 0:
            continue

        vwap_diff_pct = ((weekly_vwap - monthly_vwap) / monthly_vwap) * 100

        # SHEET 1
        if (cmp > dma_30) and (cmp > dma_50) and (cmp > dma_200) and (car_status == 'Positive') and (rsi > 50):
            results_main.append({
                'Date': today_date, 'Stock': ticker.replace('.NS', ''),
                'CMP': round(cmp, 2), '30 DMA': round(dma_30, 2),
                '50 DMA': round(dma_50, 2), '200 DMA': round(dma_200, 2),
                '200 DMA Dist %': round(dist_200_dma, 2), 'RSI(14)': round(rsi, 2),
                'Monthly VWAP HLC3': round(monthly_vwap, 2),
                'Weekly VWAP HLC3': round(weekly_vwap, 2),
                'VWAP Diff %': round(vwap_diff_pct, 2),
                'CAR Status': car_status,
                'News Sentiment': sentiment, 'Sentiment Score': sent_score,
                'Latest News': news_title, 'Action': '🟢 Positive Breakout'
            })

        # SHEET 2
        if (cmp > weekly_vwap) and (cmp > monthly_vwap) and (-1 <= vwap_diff_pct <= 11):
            results_sheet2.append({
                'Date': today_date, 'Stock': ticker.replace('.NS', ''),
                'CMP': round(cmp, 2), 'Monthly VWAP HLC3': round(monthly_vwap, 2),
                'Weekly VWAP HLC3': round(weekly_vwap, 2),
                'VWAP Diff %': round(vwap_diff_pct, 2), 'RSI(14)': round(rsi, 2),
                'News Sentiment': sentiment, 'Sentiment Score': sent_score,
                'Latest News': news_title,
                'Signal': '✅ Weekly vs Monthly (Bullish)'
            })

        # SHEET 3 (VWAP Crossover) - now reuses the SAME daily data, no
        # redundant re-download
        try:
            PRICE_MIN, PRICE_MAX = 50, 10000
            VOL_MULT = 1.3
            RSI_MIN, RSI_MAX = 40, 70
            ATR_SL_MULT, RR_TARGET = 1.5, 2.0
            CLOSE_UPPER_PCT = 0.40
            MIN_COND_MET = 1

            if not (PRICE_MIN <= cmp <= PRICE_MAX):
                raise ValueError("price out of range")

            mvwap_series = calc_vwap_rolling(data, MONTHLY_WINDOW)
            wvwap_series = calc_vwap_rolling(data, WEEKLY_WINDOW)

            if len(data) < MONTHLY_WINDOW + 1:
                raise ValueError("not enough rows")
            if not (wvwap_series.iloc[-2] <= mvwap_series.iloc[-2] and
                    wvwap_series.iloc[-1] > mvwap_series.iloc[-1]):
                raise ValueError("no crossover")

            c, h, l, o, v = close_prices, high_prices, low_prices, data['Open'], volume
            if isinstance(o, pd.DataFrame):
                o = o.iloc[:, 0]

            v20 = v.iloc[-20:].mean()
            if v.iloc[-1] <= VOL_MULT * v20:
                raise ValueError("volume filter")
            if c.iloc[-1] <= o.iloc[-1]:
                raise ValueError("not a green candle")

            dr = h.iloc[-1] - l.iloc[-1]
            if dr == 0:
                raise ValueError("zero range")
            cp = (c.iloc[-1] - l.iloc[-1]) / dr
            if cp < (1 - CLOSE_UPPER_PCT):
                raise ValueError("close position filter")

            e5 = ema(c, 5).iloc[-1]
            e20 = ema(c, 20).iloc[-1]
            s40 = sma(c, 40).iloc[-1]
            r = rsi_c(c, 14).iloc[-1]
            ad, dp, dm = adx_c(h, l, c, 14)
            adx_v, dplus, dmin = ad.iloc[-1], dp.iloc[-1], dm.iloc[-1]
            mc, ms = macd_c(c)
            mcd, msl = mc.iloc[-1], ms.iloc[-1]
            atr = atr_c(h, l, c, 14).iloc[-1]

            if not (RSI_MIN <= r <= RSI_MAX):
                raise ValueError("rsi filter")
            if cmp <= e20:
                raise ValueError("below EMA20")

            conds = []
            if (e5 > e20) and (e20 > s40):
                conds.append("EMA Trend")
            if (dplus > dmin) and (adx_v > 25):
                conds.append("ADX Strong")
            if (mcd > msl) and (mcd > 0):
                conds.append("MACD Bullish")
            if len(conds) < MIN_COND_MET:
                raise ValueError("condition count")

            entry = h.iloc[-1] * 1.001
            sl = max(entry - (atr * ATR_SL_MULT), l.iloc[-1] * 0.995)
            max_sl = entry * 0.97
            if sl < max_sl:
                sl = max_sl
            risk = entry - sl
            target = entry + (risk * RR_TARGET)

            results_sheet3.append({
                'Date': today_date, 'Stock': ticker.replace('.NS', ''),
                'CMP': round(cmp, 2), 'Monthly_VWAP': round(mvwap_series.iloc[-1], 2),
                'Weekly_VWAP': round(wvwap_series.iloc[-1], 2),
                'Volume_20x': round(v.iloc[-1] / v20, 2), 'RSI': round(r, 2),
                'ADX': round(adx_v, 2), 'ATR': round(atr, 2),
                'Close_Upper_%': round(cp * 100, 1), 'Conditions': ', '.join(conds),
                'Entry': round(entry, 2), 'SL': round(sl, 2),
                'Risk': round(risk, 2), 'Target': round(target, 2),
                'RR': f"1:{RR_TARGET}",
                'News Sentiment': sentiment, 'Sentiment Score': sent_score,
                'Latest News': news_title, 'Action': '✅ VWAP Cross Buy'
            })
        except ValueError:
            pass

        # SHEET 4 (NEW) — Daily / Weekly / Monthly VWAP for EVERY stock
        intraday_today = intraday_frames.get(ticker)
        daily_vwap = calculate_vwap_hlc3(intraday_today) if intraday_today is not None else np.nan

        monthly_vs_weekly_pct = ((monthly_vwap - weekly_vwap) / weekly_vwap) * 100 if weekly_vwap else np.nan
        weekly_vs_daily_pct = (((weekly_vwap - daily_vwap) / daily_vwap) * 100
                                if (not pd.isna(daily_vwap) and daily_vwap != 0) else np.nan)

        results_sheet4.append({
            'Date': today_date,
            'Stock': ticker.replace('.NS', ''),
            'CMP': round(cmp, 2),
            'Weekly_VWAP': round(weekly_vwap, 2),
            'Monthly_VWAP': round(monthly_vwap, 2),
            'Daily_VWAP': round(daily_vwap, 2) if not pd.isna(daily_vwap) else 'N/A',
            'Monthly_vs_Weekly_%': round(monthly_vs_weekly_pct, 2) if not pd.isna(monthly_vs_weekly_pct) else 'N/A',
            'Weekly_vs_Daily_%': round(weekly_vs_daily_pct, 2) if not pd.isna(weekly_vs_daily_pct) else 'N/A',
        })

    except Exception:
        continue

# =============================================================================
# DATAFRAMES & SORTING
# =============================================================================
df_main = pd.DataFrame(results_main)
df_sheet2 = pd.DataFrame(results_sheet2)
df_sheet3 = pd.DataFrame(results_sheet3)
df_sheet4 = pd.DataFrame(results_sheet4)

if not df_main.empty:
    df_main = df_main.sort_values(by='200 DMA Dist %', ascending=True)
if not df_sheet2.empty:
    df_sheet2 = df_sheet2.sort_values(by='VWAP Diff %', ascending=False)
if not df_sheet3.empty:
    df_sheet3 = df_sheet3.sort_values(by='RSI', ascending=False)
if not df_sheet4.empty:
    df_sheet4 = df_sheet4.sort_values(by='Stock', ascending=True)

# =============================================================================
# 🌍 FETCH GLOBAL MARKET MOOD
# =============================================================================
print("\n" + "="*90)
print("--- 🌍 GLOBAL MARKET MOOD ---")
print("="*90)
try:
    g_mood_label, g_mood_emoji, g_avg_change, g_rows = get_global_market_mood()
    print(f"{g_mood_emoji} {g_mood_label}  (avg equity index change: {g_avg_change:+.2f}%)")
    for r in g_rows:
        chg = f"{r['Change%']:+.2f}%" if r['Change%'] is not None else "N/A"
        print(f"   {r['Name']:<16} LTP: {r['LTP']:<10} Change: {chg}")
    global_mood_html = build_global_mood_html(g_mood_label, g_mood_emoji, g_avg_change, g_rows)
except Exception as e:
    print(f"⚠️ Global market mood fetch failed: {e}")
    global_mood_html = "<p style='color:#7f8c8d;'>🌍 Global market data unavailable today.</p>"

# =============================================================================
# PRINT RESULTS
# =============================================================================
print("\n" + "="*90)
print("--- 🟢 SHEET 1: मुख्य POSITIVE BREAKOUT ---")
print("="*90)
if df_main.empty:
    print("आज कोई भी स्टॉक मुख्य ब्रेकआउट शर्तों को पार नहीं कर पाया।")
else:
    print(df_main.to_string(index=False))

print("\n" + "="*90)
print("--- ✅ SHEET 2: Weekly vs Monthly VWAP ---")
print("="*90)
if df_sheet2.empty:
    print("आज कोई भी स्टॉक इस सिचुएशन में नहीं है।")
else:
    print(df_sheet2.to_string(index=False))

print("\n" + "="*90)
print("--- 📊 SHEET 3: VWAP CROSSOVER STRATEGY ---")
print("="*90)
if df_sheet3.empty:
    print("❌ आज कोई VWAP Crossover सिग्नल नहीं मिला।")
else:
    print(f"✅ {len(df_sheet3)} STOCKS FOUND\n")
    print(df_sheet3.to_string(index=False))

print("\n" + "="*90)
print("--- 📐 SHEET 4: DAILY / WEEKLY / MONTHLY VWAP (all stocks) ---")
print("="*90)
if df_sheet4.empty:
    print("❌ VWAP डेटा नहीं मिल पाया।")
else:
    print(f"✅ {len(df_sheet4)} STOCKS\n")
    print(df_sheet4.to_string(index=False))

# =============================================================================
# EXCEL EXPORT
# =============================================================================
excel_name = f"Combined_Scanner_{datetime.now().strftime('%Y-%m-%d')}.xlsx"

if not df_main.empty or not df_sheet2.empty or not df_sheet3.empty or not df_sheet4.empty:
    with pd.ExcelWriter(excel_name, engine='openpyxl') as writer:
        if not df_main.empty:
            df_main.to_excel(writer, sheet_name='Main Breakout', index=False)
        if not df_sheet2.empty:
            df_sheet2.to_excel(writer, sheet_name='Weekly_vs_Monthly', index=False)
        if not df_sheet3.empty:
            df_sheet3.to_excel(writer, sheet_name='VWAP Crossover', index=False)
        if not df_sheet4.empty:
            df_sheet4.to_excel(writer, sheet_name='VWAP_Data', index=False)

    print("\n" + "="*90)
    print(f"✅ फाइल '{excel_name}' चार शीट्स के साथ सेव हो गई!")
    print("="*90)
else:
    print("\n⚠️ कोई भी stock नहीं मिला, Excel file नहीं बनी।")

# =============================================================================
# EMAIL SUMMARY
# =============================================================================
summary_html = f"""
<table style="border-collapse: collapse; width: 100%; font-size: 14px;">
    <tr style="background: #3498db; color: white;">
        <th style="padding: 10px; border: 1px solid #ddd;">Sheet</th>
        <th style="padding: 10px; border: 1px solid #ddd;">Stocks Found</th>
        <th style="padding: 10px; border: 1px solid #ddd;">Status</th>
    </tr>
    <tr>
        <td style="padding: 10px; border: 1px solid #ddd;">🟢 Main Breakout</td>
        <td style="padding: 10px; border: 1px solid #ddd; text-align: center;">{len(df_main)}</td>
        <td style="padding: 10px; border: 1px solid #ddd;">{'✅ Active' if len(df_main) > 0 else '❌ None'}</td>
    </tr>
    <tr style="background: #f9f9f9;">
        <td style="padding: 10px; border: 1px solid #ddd;">✅ Weekly vs Monthly</td>
        <td style="padding: 10px; border: 1px solid #ddd; text-align: center;">{len(df_sheet2)}</td>
        <td style="padding: 10px; border: 1px solid #ddd;">{'✅ Active' if len(df_sheet2) > 0 else '❌ None'}</td>
    </tr>
    <tr>
        <td style="padding: 10px; border: 1px solid #ddd;">📊 VWAP Crossover</td>
        <td style="padding: 10px; border: 1px solid #ddd; text-align: center;">{len(df_sheet3)}</td>
        <td style="padding: 10px; border: 1px solid #ddd;">{'✅ Active' if len(df_sheet3) > 0 else '❌ None'}</td>
    </tr>
    <tr style="background: #f9f9f9;">
        <td style="padding: 10px; border: 1px solid #ddd;">📐 VWAP Data (D/W/M)</td>
        <td style="padding: 10px; border: 1px solid #ddd; text-align: center;">{len(df_sheet4)}</td>
        <td style="padding: 10px; border: 1px solid #ddd;">{'✅ Active' if len(df_sheet4) > 0 else '❌ None'}</td>
    </tr>
</table>
"""

# Global market mood goes ABOVE the stock summary in the email
summary_html = global_mood_html + summary_html

# =============================================================================
# SEND EMAIL — always sent, even if no stocks matched today (excel attached
# only if it was actually created). This ensures the 8:15 AM mail arrives
# daily with at least the global market mood, instead of staying silent
# on days with zero breakout hits.
# =============================================================================
send_email_with_attachment(excel_name, summary_html)
if not os.path.exists(excel_name):
    print("\nℹ️ कोई भी stock match नहीं हुआ आज, इसलिए Excel attach नहीं हुई — लेकिन summary email भेज दी गई।")

print("\n🏁 SCANNING & EMAIL COMPLETE!")

try:
    from google.colab import files
    files.download(excel_name)
except Exception:
    pass
