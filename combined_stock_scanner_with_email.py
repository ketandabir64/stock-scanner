#!/usr/bin/env python3
# =============================================================================
# 📊 COMBINED SUPER SCANNER + AUTO EMAIL
# Reads email credentials from environment variables
# =============================================================================

import yfinance as yf
import pandas as pd
import numpy as np
import warnings
import logging
import smtplib
import os
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

    except Exception as e:
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
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_vwap_hlc3(df):
    if df.empty or df['Volume'].sum() == 0:
        return np.nan
    hlc3 = (df['High'] + df['Low'] + df['Close']) / 3
    vwap = (hlc3 * df['Volume']).cumsum() / df['Volume'].cumsum()
    return vwap.iloc[-1]

def ema(s, p): return s.ewm(span=p, adjust=False).mean()
def sma(s, p): return s.rolling(window=p).mean()
def rsi_c(s, p=14):
    d = s.diff()
    g = d.where(d>0,0).rolling(p).mean()
    l = (-d.where(d<0,0)).rolling(p).mean()
    return 100 - (100 / (1 + g/l))
def adx_c(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(p).mean()
    pdm = h.diff().where((h.diff()>l.diff().abs())&(h.diff()>0),0).rolling(p).mean()/atr
    mdm = l.diff().abs().where((l.diff().abs()>h.diff())&(l.diff().abs()>0),0).rolling(p).mean()/atr
    dx = ((100*pdm - 100*mdm).abs() / (100*pdm + 100*mdm)) * 100
    return dx.rolling(p).mean(), 100*pdm, 100*mdm
def macd_c(c):
    ml = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    return ml, ml.ewm(span=9, adjust=False).mean()
def atr_c(h, l, c, p=14):
    return pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1).rolling(p).mean()

def calc_vwap(df, anchor='month'):
    hlc3 = (df['High'] + df['Low'] + df['Close']) / 3
    df_temp = df.copy()
    df_temp['HLC3'] = hlc3
    df_temp['Vol_x_HLC3'] = df['Volume'] * hlc3
    if anchor == 'month':
        grouper = pd.Grouper(freq='MS')
    else:
        grouper = pd.Grouper(freq='W-MON')
    df_temp['Cum_Vol'] = df_temp.groupby(grouper)['Volume'].cumsum()
    df_temp['Cum_Vol_HLC3'] = df_temp.groupby(grouper)['Vol_x_HLC3'].cumsum()
    return df_temp['Cum_Vol_HLC3'] / df_temp['Cum_Vol']

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
                Sheets: Main Breakout | Weekly vs Monthly | VWAP Crossover
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
# MAIN SCANNER
# =============================================================================
results_main = []
results_sheet2 = []
results_sheet3 = []

print(f"🔍 कुल {len(my_stocks)} शेयरों की स्कैनिंग शुरू... कृपया प्रतीक्षा करें।\n")

for idx, ticker in enumerate(my_stocks):
    if (idx + 1) % 20 == 0:
        print(f"   {idx+1}/{len(my_stocks)} stocks processed...")

    try:
        data = yf.download(ticker, period="2y", interval="1d", progress=False)
        if data.empty or len(data) < 200:
            continue

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

        current_month = data.index[-1].month
        current_year = data.index[-1].year
        monthly_mask = (data.index.month == current_month) & (data.index.year == current_year)
        monthly_df = pd.DataFrame({
            'High': high_prices[monthly_mask],
            'Low': low_prices[monthly_mask],
            'Close': close_prices[monthly_mask],
            'Volume': volume[monthly_mask]
        })
        monthly_vwap = calculate_vwap_hlc3(monthly_df)

        iso_cal = data.index.isocalendar()
        current_week = iso_cal['week'].iloc[-1]
        current_year_week = iso_cal['year'].iloc[-1]
        weekly_mask = (iso_cal['week'] == current_week) & (iso_cal['year'] == current_year_week)
        weekly_df = pd.DataFrame({
            'High': high_prices[weekly_mask],
            'Low': low_prices[weekly_mask],
            'Close': close_prices[weekly_mask],
            'Volume': volume[weekly_mask]
        })
        weekly_vwap = calculate_vwap_hlc3(weekly_df)

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

        # SHEET 3
        try:
            df_1y = yf.download(ticker, period="1y", interval="1d", progress=False)
            if df_1y.empty or len(df_1y) < 60:
                continue
            if isinstance(df_1y.columns, pd.MultiIndex):
                df_1y.columns = df_1y.columns.get_level_values(0)
            df_1y.index = pd.to_datetime(df_1y.index)

            PRICE_MIN, PRICE_MAX = 50, 10000
            VOL_MULT = 1.3
            RSI_MIN, RSI_MAX = 40, 70
            ATR_SL_MULT, RR_TARGET = 1.5, 2.0
            CLOSE_UPPER_PCT = 0.40
            MIN_COND_MET = 1

            cmp_1y = df_1y['Close'].iloc[-1]
            if not (PRICE_MIN <= cmp_1y <= PRICE_MAX):
                continue

            df_1y['MVWAP'] = calc_vwap(df_1y, 'month')
            df_1y['WVWAP'] = calc_vwap(df_1y, 'week')

            if len(df_1y) < 3:
                continue
            if not (df_1y['WVWAP'].iloc[-2] <= df_1y['MVWAP'].iloc[-2] and 
                    df_1y['WVWAP'].iloc[-1] > df_1y['MVWAP'].iloc[-1]):
                continue

            c, h, l, o, v = df_1y['Close'], df_1y['High'], df_1y['Low'], df_1y['Open'], df_1y['Volume']

            v20 = v.iloc[-20:].mean()
            if v.iloc[-1] <= VOL_MULT * v20:
                continue
            if c.iloc[-1] <= o.iloc[-1]:
                continue

            dr = h.iloc[-1] - l.iloc[-1]
            if dr == 0:
                continue
            cp = (c.iloc[-1] - l.iloc[-1]) / dr
            if cp < (1 - CLOSE_UPPER_PCT):
                continue

            e5 = ema(c,5).iloc[-1]
            e20 = ema(c,20).iloc[-1]
            s40 = sma(c,40).iloc[-1]
            r = rsi_c(c,14).iloc[-1]
            ad, dp, dm = adx_c(h,l,c,14)
            adx_v, dplus, dmin = ad.iloc[-1], dp.iloc[-1], dm.iloc[-1]
            mc, ms = macd_c(c)
            mcd, msl = mc.iloc[-1], ms.iloc[-1]
            atr = atr_c(h,l,c,14).iloc[-1]

            if not (RSI_MIN <= r <= RSI_MAX):
                continue
            if cmp_1y <= e20:
                continue

            conds = []
            if (e5 > e20) and (e20 > s40): 
                conds.append("EMA Trend")
            if (dplus > dmin) and (adx_v > 25): 
                conds.append("ADX Strong")
            if (mcd > msl) and (mcd > 0): 
                conds.append("MACD Bullish")
            if len(conds) < MIN_COND_MET:
                continue

            entry = h.iloc[-1] * 1.001
            sl = max(entry - (atr * ATR_SL_MULT), l.iloc[-1] * 0.995)
            max_sl = entry * 0.97
            if sl < max_sl: 
                sl = max_sl
            risk = entry - sl
            target = entry + (risk * RR_TARGET)

            results_sheet3.append({
                'Date': today_date, 'Stock': ticker.replace('.NS',''), 
                'CMP': round(cmp_1y,2), 'Monthly_VWAP': round(df_1y['MVWAP'].iloc[-1],2),
                'Weekly_VWAP': round(df_1y['WVWAP'].iloc[-1],2),
                'Volume_20x': round(v.iloc[-1]/v20,2), 'RSI': round(r,2),
                'ADX': round(adx_v,2), 'ATR': round(atr,2),
                'Close_Upper_%': round(cp*100,1), 'Conditions': ', '.join(conds),
                'Entry': round(entry,2), 'SL': round(sl,2),
                'Risk': round(risk,2), 'Target': round(target,2),
                'RR': f"1:{RR_TARGET}",
                'News Sentiment': sentiment, 'Sentiment Score': sent_score,
                'Latest News': news_title, 'Action': '✅ VWAP Cross Buy'
            })
        except:
            pass

    except Exception as e:
        pass

# =============================================================================
# DATAFRAMES & SORTING
# =============================================================================
df_main = pd.DataFrame(results_main)
df_sheet2 = pd.DataFrame(results_sheet2)
df_sheet3 = pd.DataFrame(results_sheet3)

if not df_main.empty:
    df_main = df_main.sort_values(by='200 DMA Dist %', ascending=True)
if not df_sheet2.empty:
    df_sheet2 = df_sheet2.sort_values(by='VWAP Diff %', ascending=False)
if not df_sheet3.empty:
    df_sheet3 = df_sheet3.sort_values(by='RSI', ascending=False)

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

# =============================================================================
# EXCEL EXPORT
# =============================================================================
excel_name = f"Combined_Scanner_{datetime.now().strftime('%Y-%m-%d')}.xlsx"

if not df_main.empty or not df_sheet2.empty or not df_sheet3.empty:
    with pd.ExcelWriter(excel_name, engine='openpyxl') as writer:
        if not df_main.empty:
            df_main.to_excel(writer, sheet_name='Main Breakout', index=False)
        if not df_sheet2.empty:
            df_sheet2.to_excel(writer, sheet_name='Weekly_vs_Monthly', index=False)
        if not df_sheet3.empty:
            df_sheet3.to_excel(writer, sheet_name='VWAP Crossover', index=False)

    print("\n" + "="*90)
    print(f"✅ फाइल '{excel_name}' तीन शीट्स के साथ सेव हो गई!")
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
</table>
"""

# =============================================================================
# SEND EMAIL
# =============================================================================
if os.path.exists(excel_name):
    send_email_with_attachment(excel_name, summary_html)
else:
    print("\n⚠️ Excel file नहीं बनी, email नहीं भेजी गई।")

print("\n🏁 SCANNING & EMAIL COMPLETE!")
