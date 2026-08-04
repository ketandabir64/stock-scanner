#!/usr/bin/env python3
# =============================================================================
# 📊 PRE-MARKET DECODER (BUG-FIXED v2.0)
# Fixes: 9:30 candle bug, IST timezone, NSE API crash handling, Always sends email
# =============================================================================

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import warnings
import logging
import smtplib
import os
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.filterwarnings('ignore')

# BUG FIX: Proper IST timezone (UTC+5:30) for GitHub Actions runners
def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

today_date = get_ist_time().strftime("%d-%m-%Y")
report_time = get_ist_time().strftime("%H:%M IST")

# =============================================================================
# EMAIL CONFIG
# =============================================================================
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'YOUR_EMAIL@gmail.com')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD', 'YOUR_APP_PASSWORD')
RECEIVER_EMAIL = os.environ.get('RECEIVER_EMAIL', 'YOUR_EMAIL@gmail.com')

# =============================================================================
# 1. GLOBAL MARKETS SENTIMENT
# =============================================================================
def get_global_markets():
    indices = {
        'US_S&P500': '^GSPC', 'US_Dow': '^DJI', 'US_Nasdaq': '^IXIC', 'US_Russell': '^RUT',
        'UK_FTSE': '^FTSE', 'Germany_DAX': '^GDAXI', 'France_CAC': '^FCHI',
        'Japan_Nikkei': '^N225', 'HongKong_HSI': '^HSI', 'China_Shanghai': '000001.SS',
        'Korea_KOSPI': '^KS11', 'Singapore_STI': '^STI', 'Australia_ASX': '^AXJO',
        'Taiwan_TAIEX': '^TWII', 'India_Nifty': '^NSEI',
    }

    results = []
    total_change = 0
    count = 0

    for name, ticker in indices.items():
        try:
            data = yf.download(ticker, period="5d", interval="1d", progress=False)
            if data.empty or len(data) < 2:
                continue
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            data = data.sort_index()

            prev = float(data['Close'].iloc[-2])
            curr = float(data['Close'].iloc[-1])
            change = ((curr - prev) / prev) * 100

            region = name.split('_')[0]
            results.append({
                'Market': name.replace('_', ' '),
                'Region': region,
                'Prev_Close': round(prev, 2),
                'Latest': round(curr, 2),
                'Change_%': round(change, 2),
                'Trend': '🟢 Up' if change > 0 else '🔴 Down' if change < 0 else '⚪ Flat'
            })
            total_change += change
            count += 1
        except:
            continue

    df = pd.DataFrame(results)
    avg_change = round(total_change / count, 2) if count > 0 else 0

    if avg_change > 0.5: sentiment = "🟢 Strongly Bullish"
    elif avg_change > 0: sentiment = "🟢 Mildly Bullish"
    elif avg_change > -0.5: sentiment = "🔴 Mildly Bearish"
    else: sentiment = "🔴 Strongly Bearish"

    return df, sentiment, avg_change

# =============================================================================
# 2. INDIAN MARKET INDICATORS (VIX, Gift Nifty proxy, Nifty/BankNifty 9:15-9:30)
# =============================================================================
def get_indian_indicators():
    indicators = {}

    # India VIX
    try:
        vix = yf.download('^INDIAVIX', period="5d", interval="1d", progress=False)
        if not vix.empty:
            if isinstance(vix.columns, pd.MultiIndex):
                vix.columns = vix.columns.get_level_values(0)
            vix = vix.sort_index()
            val = float(vix['Close'].iloc[-1])
            prev = float(vix['Close'].iloc[-2])
            chg = round(((val - prev) / prev) * 100, 2)
            indicators['India_VIX'] = {
                'Current': round(val, 2), 'Prev': round(prev, 2), 'Change_%': chg,
                'Signal': '🔴 High Fear' if val > 20 else '🟢 Low Fear' if val < 15 else '⚪ Neutral'
            }
    except:
        indicators['India_VIX'] = {'Current': 'N/A', 'Prev': 'N/A', 'Change_%': 'N/A', 'Signal': '⚪ N/A'}

    # Nifty 50 Spot
    try:
        nifty = yf.download('^NSEI', period="5d", interval="1d", progress=False)
        if not nifty.empty:
            if isinstance(nifty.columns, pd.MultiIndex):
                nifty.columns = nifty.columns.get_level_values(0)
            nifty = nifty.sort_index()
            val = float(nifty['Close'].iloc[-1])
            prev = float(nifty['Close'].iloc[-2])
            chg = round(((val - prev) / prev) * 100, 2)
            indicators['Nifty50_Spot'] = {
                'Current': round(val, 2), 'Prev': round(prev, 2), 'Change_%': chg,
                'Signal': '🟢 Gap Up' if chg > 0.3 else '🔴 Gap Down' if chg < -0.3 else '⚪ Flat'
            }
    except:
        indicators['Nifty50_Spot'] = {'Current': 'N/A', 'Prev': 'N/A', 'Change_%': 'N/A', 'Signal': '⚪ N/A'}

    # BUG FIX: Nifty 9:15 to 9:30 — use 4 candles (indices 0,1,2,3) not 3
    try:
        nifty_5m = yf.download('^NSEI', period="1d", interval="5m", progress=False)
        if not nifty_5m.empty and len(nifty_5m) >= 4:
            if isinstance(nifty_5m.columns, pd.MultiIndex):
                nifty_5m.columns = nifty_5m.columns.get_level_values(0)
            nifty_5m = nifty_5m.sort_index()

            # Use first 4 candles = 9:15, 9:20, 9:25, 9:30
            candles_to_use = min(4, len(nifty_5m))
            open_p = float(nifty_5m['Open'].iloc[0])
            high_930 = float(nifty_5m['High'].iloc[:candles_to_use].max())
            low_930 = float(nifty_5m['Low'].iloc[:candles_to_use].min())
            close_930 = float(nifty_5m['Close'].iloc[candles_to_use-1])
            vol_930 = int(nifty_5m['Volume'].iloc[:candles_to_use].sum())
            change = round(((close_930 - open_p) / open_p) * 100, 2)

            indicators['Nifty_915_930'] = {
                'Open': round(open_p, 2), 'High_930': round(high_930, 2),
                'Low_930': round(low_930, 2), 'Close_930': round(close_930, 2),
                'Volume': vol_930, 'Change_from_Open_%': change,
                'Mood': '🟢 Bullish Start' if change > 0.2 else '🔴 Bearish Start' if change < -0.2 else '⚪ Neutral'
            }
        else:
            indicators['Nifty_915_930'] = {
                'Open': 'N/A', 'High_930': 'N/A', 'Low_930': 'N/A', 'Close_930': 'N/A',
                'Volume': 'N/A', 'Change_from_Open_%': 'N/A', 'Mood': '⚪ N/A'
            }
    except Exception as e:
        print(f"   Nifty 9:15-9:30 fetch failed: {e}")
        indicators['Nifty_915_930'] = {
            'Open': 'N/A', 'High_930': 'N/A', 'Low_930': 'N/A', 'Close_930': 'N/A',
            'Volume': 'N/A', 'Change_from_Open_%': 'N/A', 'Mood': '⚪ N/A'
        }

    # BUG FIX: Bank Nifty 9:15-9:30 — use 4th candle (index 3) for 9:30 close
    try:
        bn_5m = yf.download('^NSEBANK', period="1d", interval="5m", progress=False)
        if not bn_5m.empty and len(bn_5m) >= 4:
            if isinstance(bn_5m.columns, pd.MultiIndex):
                bn_5m.columns = bn_5m.columns.get_level_values(0)
            bn_5m = bn_5m.sort_index()

            candles_to_use = min(4, len(bn_5m))
            open_p = float(bn_5m['Open'].iloc[0])
            close_p = float(bn_5m['Close'].iloc[candles_to_use-1])
            change = round(((close_p - open_p) / open_p) * 100, 2)
            indicators['BankNifty_915_930'] = {
                'Open': round(open_p, 2), 'Close_930': round(close_p, 2),
                'Change_%': change,
                'Mood': '🟢 Bullish' if change > 0.3 else '🔴 Bearish' if change < -0.3 else '⚪ Neutral'
            }
        else:
            indicators['BankNifty_915_930'] = {'Open': 'N/A', 'Close_930': 'N/A', 'Change_%': 'N/A', 'Mood': '⚪ N/A'}
    except Exception as e:
        print(f"   BankNifty 9:15-9:30 fetch failed: {e}")
        indicators['BankNifty_915_930'] = {'Open': 'N/A', 'Close_930': 'N/A', 'Change_%': 'N/A', 'Mood': '⚪ N/A'}

    return indicators

# =============================================================================
# 3. PRE-OPEN MARKET DATA (NSE) — 9:00 to 9:08 AM
# =============================================================================
def get_preopen_data():
    preopen = []
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        session = requests.Session()
        # Get cookies first
        resp1 = session.get('https://www.nseindia.com', headers=headers, timeout=15)
        if resp1.status_code == 200:
            url = 'https://www.nseindia.com/api/market-data-pre-open?key=NIFTY'
            resp = session.get(url, headers=headers, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                for item in data.get('data', [])[:25]:
                    meta = item.get('metadata', {})
                    preopen.append({
                        'Symbol': meta.get('symbol', 'N/A'),
                        'Prev_Close': meta.get('previousClose', 'N/A'),
                        'PreOpen_Final': meta.get('lastPrice', 'N/A'),
                        'IEP_Price': meta.get('iep', 'N/A'),
                        'Change_%': meta.get('change', 'N/A'),
                        'Volume': meta.get('totalTurnover', 'N/A'),
                        'Trend': meta.get('pChange', 'N/A')
                    })
    except Exception as e:
        print(f"   Pre-open NSE fetch failed: {e}")

    return pd.DataFrame(preopen)

# =============================================================================
# 4. FII / DII DATA
# =============================================================================
def get_fii_dii():
    fii_dii = []
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
        }
        session = requests.Session()
        resp1 = session.get('https://www.nseindia.com', headers=headers, timeout=15)
        if resp1.status_code == 200:
            url = 'https://www.nseindia.com/api/fiidiiTradeReact'
            resp = session.get(url, headers=headers, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                for item in data.get('data', []):
                    try:
                        net = float(item.get('netValue', 0))
                        fii_dii.append({
                            'Category': item.get('category', 'N/A'),
                            'Date': item.get('date', 'N/A'),
                            'Buy_Cr': item.get('buyValue', 'N/A'),
                            'Sell_Cr': item.get('sellValue', 'N/A'),
                            'Net_Cr': round(net, 2),
                            'Signal': '🟢 Net Buy' if net > 0 else '🔴 Net Sell'
                        })
                    except:
                        continue
    except Exception as e:
        print(f"   FII/DII fetch failed: {e}")

    return pd.DataFrame(fii_dii)

# =============================================================================
# 5. SECTORAL MOOD (9:15-9:30) — BUG FIX: Use 4 candles
# =============================================================================
def get_sectoral_mood():
    sectors = {
        'Nifty_Bank': '^NSEBANK', 'Nifty_IT': '^CNXIT', 'Nifty_Auto': '^CNXAUTO',
        'Nifty_Pharma': '^CNXPHARMA', 'Nifty_FMCG': '^CNXFMCG', 'Nifty_Metal': '^CNXMETAL',
        'Nifty_Energy': '^CNXENERGY', 'Nifty_FinServ': '^CNXFIN', 'Nifty_Media': '^CNXMEDIA',
        'Nifty_Realty': '^CNXREALTY',
    }
    results = []
    for name, ticker in sectors.items():
        try:
            data = yf.download(ticker, period="1d", interval="5m", progress=False)
            if not data.empty and len(data) >= 2:
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                data = data.sort_index()

                # BUG FIX: Use up to 4 candles for 9:15-9:30
                candles = min(4, len(data))
                open_p = float(data['Open'].iloc[0])
                close_p = float(data['Close'].iloc[candles-1])
                change = round(((close_p - open_p) / open_p) * 100, 2)
                results.append({
                    'Sector': name.replace('_', ' '), 'Open': round(open_p, 2),
                    'At_930': round(close_p, 2), 'Change_%': change,
                    'Mood': '🟢 Strong' if change > 0.5 else '🟢 Positive' if change > 0.1 else 
                            '🔴 Weak' if change < -0.5 else '🔴 Negative' if change < -0.1 else '⚪ Neutral'
                })
        except:
            continue
    return pd.DataFrame(results)

# =============================================================================
# 6. MARKET BREADTH (Advance/Decline)
# =============================================================================
def get_market_breadth():
    nifty_50 = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'ICICIBANK.NS', 'INFY.NS',
        'HINDUNILVR.NS', 'ITC.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'KOTAKBANK.NS',
        'LT.NS', 'BAJFINANCE.NS', 'HCLTECH.NS', 'AXISBANK.NS', 'ASIANPAINT.NS',
        'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'ADANIENT.NS', 'ULTRACEMCO.NS',
        'NESTLEIND.NS', 'WIPRO.NS', 'POWERGRID.NS', 'NTPC.NS', 'M&M.NS',
        'COALINDIA.NS', 'TATAMOTORS.NS', 'JSWSTEEL.NS', 'ADANIPORTS.NS', 'TATASTEEL.NS',
        'GRASIM.NS', 'ONGC.NS', 'HDFCLIFE.NS', 'TECHM.NS', 'BRITANNIA.NS',
        'CIPLA.NS', 'SBILIFE.NS', 'EICHERMOT.NS', 'APOLLOHOSP.NS', 'HEROMOTOCO.NS',
        'DRREDDY.NS', 'TATACONSUM.NS', 'INDUSINDBK.NS', 'HINDALCO.NS', 'UPL.NS',
        'BAJAJ-AUTO.NS', 'DIVISLAB.NS', 'BPCL.NS', 'BAJAJFINSV.NS', 'LICI.NS'
    ]

    adv, dec, unc = 0, 0, 0
    for stock in nifty_50:
        try:
            data = yf.download(stock, period="2d", interval="1d", progress=False)
            if not data.empty and len(data) >= 2:
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                data = data.sort_index()
                prev = float(data['Close'].iloc[-2])
                curr = float(data['Close'].iloc[-1])
                if curr > prev: adv += 1
                elif curr < prev: dec += 1
                else: unc += 1
        except:
            continue

    total = adv + dec + unc
    ratio = round(adv / dec, 2) if dec > 0 else 'N/A'
    return {
        'Advanced': adv, 'Declined': dec, 'Unchanged': unc, 'Total': total,
        'A/D_Ratio': ratio,
        'Signal': '🟢 Broad Buying' if adv > dec * 1.5 else '🔴 Broad Selling' if dec > adv * 1.5 else '⚪ Mixed'
    }

# =============================================================================
# 7. OVERALL MARKET DECODE
# =============================================================================
def decode_market(global_df, indian, sectoral, breadth, fii_df):
    score = 0
    reasons = []

    # Global
    if not global_df.empty:
        avg = global_df['Change_%'].mean()
        if avg > 0.5: score += 2; reasons.append("Global markets strongly positive")
        elif avg > 0: score += 1; reasons.append("Global markets mildly positive")
        elif avg < -0.5: score -= 2; reasons.append("Global markets negative")

    # VIX
    vix = indian.get('India_VIX', {})
    vix_curr = vix.get('Current')
    if vix_curr != 'N/A' and isinstance(vix_curr, (int, float)):
        if vix_curr < 15: score += 1; reasons.append("VIX low = low fear")
        elif vix_curr > 20: score -= 1; reasons.append("VIX high = fear elevated")

    # Nifty 9:30
    n30 = indian.get('Nifty_915_930', {})
    n30_chg = n30.get('Change_from_Open_%')
    if n30_chg != 'N/A' and isinstance(n30_chg, (int, float)):
        if n30_chg > 0.3: score += 2; reasons.append("Strong opening 9:15-9:30")
        elif n30_chg > 0: score += 1; reasons.append("Positive opening")
        elif n30_chg < -0.3: score -= 2; reasons.append("Weak opening")

    # Sectoral
    if not sectoral.empty:
        pos = len(sectoral[sectoral['Change_%'] > 0])
        neg = len(sectoral[sectoral['Change_%'] < 0])
        if pos > neg * 1.5: score += 2; reasons.append("Majority sectors green")
        elif neg > pos * 1.5: score -= 2; reasons.append("Majority sectors red")

    # Breadth
    if isinstance(breadth, dict):
        a, d = breadth.get('Advanced', 0), breadth.get('Declined', 0)
        if isinstance(a, int) and isinstance(d, int) and d > 0:
            if a > d * 1.5: score += 2; reasons.append("Strong A/D ratio")
            elif a > d: score += 1; reasons.append("Positive breadth")
            elif d > a * 1.5: score -= 2; reasons.append("Weak breadth")

    # FII
    if not fii_df.empty:
        fii_row = fii_df[fii_df['Category'].str.contains('FII', case=False, na=False)]
        if not fii_row.empty:
            net = fii_row.iloc[0].get('Net_Cr', 0)
            if isinstance(net, (int, float)):
                if net > 1000: score += 2; reasons.append("Heavy FII buying")
                elif net > 0: score += 1; reasons.append("FII net buyers")
                elif net < -1000: score -= 2; reasons.append("Heavy FII selling")
                elif net < 0: score -= 1; reasons.append("FII net sellers")

    if score >= 4: decode = "🟢🟢 STRONG BUY — Favorable setup, go aggressive"
    elif score >= 2: decode = "🟢 BUY — Positive bias, trade confidently"
    elif score >= 0: decode = "⚪ NEUTRAL — Mixed signals, be selective"
    elif score >= -2: decode = "🔴 SELL/AVOID — Negative bias, stay cautious"
    else: decode = "🔴🔴 STRONG SELL — Risk-off, protect capital"

    return {'Score': score, 'Decode': decode, 'Reasons': ' | '.join(reasons) if reasons else 'Insufficient data'}

# =============================================================================
# EMAIL FUNCTION (BUG FIX: Always sends, even with partial data)
# =============================================================================
def send_email(excel_path, html, subject_override=None):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        subj = subject_override or f"📊 Pre-Market Decoder — {today_date} | {report_time}"
        msg['Subject'] = subj
        msg.attach(MIMEText(html, 'html'))

        if os.path.exists(excel_path):
            with open(excel_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(excel_path)}"')
            msg.attach(part)
        else:
            # Attach a note if Excel missing
            note = MIMEText(f"Pre-Market Decoder ran on {today_date} at {report_time}. Excel file was not generated due to data unavailability.", 'plain')
            note.add_header('Content-Disposition', 'attachment; filename="note.txt"')
            msg.attach(note)

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"✅ Email sent to {RECEIVER_EMAIL}")
        return True
    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False

# =============================================================================
# MAIN (BUG FIX: Wrapped in try-except to always attempt email)
# =============================================================================
def main():
    print("="*80)
    print("📊 PRE-MARKET DECODER — Global + Indian Market Intelligence")
    print("="*80)
    print(f"Date: {today_date} | Time: {report_time}\n")

    try:
        print("🌍 1. Fetching Global Markets...")
        global_df, global_sent, global_score = get_global_markets()
        print(f"   → {global_sent} (Avg: {global_score}%)")
    except Exception as e:
        print(f"   ⚠️ Global markets fetch failed: {e}")
        global_df, global_sent, global_score = pd.DataFrame(), "⚪ N/A", 0

    try:
        print("🇮🇳 2. Fetching Indian Indicators (VIX, Nifty, BankNifty)...")
        indian = get_indian_indicators()
        for k, v in indian.items():
            print(f"   → {k}: {v}")
    except Exception as e:
        print(f"   ⚠️ Indian indicators fetch failed: {e}")
        indian = {}

    try:
        print("📈 3. Fetching NSE Pre-Open (9:00-9:08)...")
        preopen_df = get_preopen_data()
        print(f"   → {len(preopen_df)} stocks fetched")
    except Exception as e:
        print(f"   ⚠️ Pre-open fetch failed: {e}")
        preopen_df = pd.DataFrame()

    try:
        print("💰 4. Fetching FII/DII Data...")
        fii_df = get_fii_dii()
        print(f"   → {len(fii_df)} records")
    except Exception as e:
        print(f"   ⚠️ FII/DII fetch failed: {e}")
        fii_df = pd.DataFrame()

    try:
        print("🏭 5. Fetching Sectoral Mood (9:15-9:30)...")
        sectoral_df = get_sectoral_mood()
        print(f"   → {len(sectoral_df)} sectors tracked")
    except Exception as e:
        print(f"   ⚠️ Sectoral mood fetch failed: {e}")
        sectoral_df = pd.DataFrame()

    try:
        print("📊 6. Calculating Market Breadth (A/D)...")
        breadth = get_market_breadth()
        print(f"   → A/D Ratio: {breadth.get('A/D_Ratio', 'N/A')}")
    except Exception as e:
        print(f"   ⚠️ Market breadth failed: {e}")
        breadth = {'Advanced': 0, 'Declined': 0, 'Unchanged': 0, 'Total': 0, 'A/D_Ratio': 'N/A', 'Signal': '⚪ N/A'}

    try:
        print("🧠 7. Decoding Market Mood...")
        decode = decode_market(global_df, indian, sectoral_df, breadth, fii_df)
        print(f"   → {decode['Decode']}")
        print(f"   → Score: {decode['Score']}/10")
    except Exception as e:
        print(f"   ⚠️ Decode failed: {e}")
        decode = {'Score': 0, 'Decode': '⚪ N/A — Error in analysis', 'Reasons': 'Analysis error'}

    # Build Excel
    excel_name = f"PreMarket_Decoder_{get_ist_time().strftime('%Y-%m-%d')}.xlsx"

    try:
        with pd.ExcelWriter(excel_name, engine='openpyxl') as writer:
            # Summary
            try:
                fii_net = fii_df[fii_df['Category'].str.contains('FII', case=False, na=False)]['Net_Cr'].iloc[0] if not fii_df.empty and not fii_df[fii_df['Category'].str.contains('FII', case=False, na=False)].empty else 'N/A'
                dii_net = fii_df[fii_df['Category'].str.contains('DII', case=False, na=False)]['Net_Cr'].iloc[0] if not fii_df.empty and not fii_df[fii_df['Category'].str.contains('DII', case=False, na=False)].empty else 'N/A'
            except:
                fii_net, dii_net = 'N/A', 'N/A'

            summary = {
                'Metric': ['Report Date', 'Report Time', 'Global Sentiment', 'Global Avg Change %',
                           'India VIX', 'VIX Signal', 'Nifty 9:30 Close', 'Nifty 9:30 Mood',
                           'BankNifty 9:30', 'A/D Ratio', 'Breadth Signal',
                           'FII Net (Cr)', 'DII Net (Cr)', 'MARKET DECODE', 'Decode Score', 'Key Reasons'],
                'Value': [today_date, report_time, global_sent, global_score,
                          indian.get('India_VIX', {}).get('Current', 'N/A'),
                          indian.get('India_VIX', {}).get('Signal', 'N/A'),
                          indian.get('Nifty_915_930', {}).get('Close_930', 'N/A'),
                          indian.get('Nifty_915_930', {}).get('Mood', 'N/A'),
                          indian.get('BankNifty_915_930', {}).get('Close_930', 'N/A'),
                          breadth.get('A/D_Ratio', 'N/A'), breadth.get('Signal', 'N/A'),
                          fii_net, dii_net, decode['Decode'], decode['Score'], decode['Reasons']]
            }
            pd.DataFrame(summary).to_excel(writer, sheet_name='Summary', index=False)

            if not global_df.empty:
                global_df.to_excel(writer, sheet_name='Global_Markets', index=False)

            if indian:
                indian_df = pd.DataFrame([{'Indicator': k, **v} for k, v in indian.items()])
                indian_df.to_excel(writer, sheet_name='Indian_Indicators', index=False)

            if not preopen_df.empty:
                preopen_df.to_excel(writer, sheet_name='PreOpen_Top25', index=False)

            if not sectoral_df.empty:
                sectoral_df.to_excel(writer, sheet_name='Sectoral_Mood', index=False)

            pd.DataFrame([breadth]).to_excel(writer, sheet_name='Market_Breadth', index=False)

            if not fii_df.empty:
                fii_df.to_excel(writer, sheet_name='FII_DII', index=False)

        print(f"\n✅ Excel saved: {excel_name}")
    except Exception as e:
        print(f"\n⚠️ Excel save failed: {e}")

    # HTML Email
    vix_val = indian.get('India_VIX', {}).get('Current', 'N/A')
    vix_sig = indian.get('India_VIX', {}).get('Signal', 'N/A')
    n30_close = indian.get('Nifty_915_930', {}).get('Close_930', 'N/A')
    n30_mood = indian.get('Nifty_915_930', {}).get('Mood', 'N/A')
    bn30 = indian.get('BankNifty_915_930', {}).get('Close_930', 'N/A')
    bn30_mood = indian.get('BankNifty_915_930', {}).get('Mood', 'N/A')

    html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f0f2f5;padding:20px;">
    <div style="max-width:800px;margin:0 auto;background:white;padding:30px;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,0.1);">
        <h1 style="color:#1a237e;text-align:center;margin-bottom:5px;">📊 Pre-Market Decoder</h1>
        <p style="text-align:center;color:#666;margin-top:0;">{today_date} | {report_time}</p>

        <div style="background:{'#e8f5e9' if decode['Score']>0 else '#ffebee' if decode['Score']<0 else '#fff8e1'};padding:20px;border-radius:10px;text-align:center;margin:20px 0;border-left:5px solid {'#4caf50' if decode['Score']>0 else '#f44336' if decode['Score']<0 else '#ffc107'};">
            <h2 style="margin:0;color:{'#2e7d32' if decode['Score']>0 else '#c62828' if decode['Score']<0 else '#f57f17'};">{decode['Decode']}</h2>
            <p style="margin:8px 0 0 0;font-size:20px;"><b>Score: {decode['Score']}/10</b></p>
        </div>

        <h3 style="color:#1a237e;border-bottom:2px solid #e0e0e0;padding-bottom:8px;">🌍 Global Sentiment</h3>
        <p><b>{global_sent}</b> | Avg Change: {global_score}%</p>

        <h3 style="color:#1a237e;border-bottom:2px solid #e0e0e0;padding-bottom:8px;">🇮🇳 Key Indicators</h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <tr style="background:#1a237e;color:white;"><th style="padding:10px;border:1px solid #ddd;text-align:left;">Indicator</th><th style="padding:10px;border:1px solid #ddd;">Value</th><th style="padding:10px;border:1px solid #ddd;">Signal</th></tr>
            <tr><td style="padding:10px;border:1px solid #ddd;">India VIX</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{vix_val}</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{vix_sig}</td></tr>
            <tr style="background:#f9f9f9;"><td style="padding:10px;border:1px solid #ddd;">Nifty 9:30</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{n30_close}</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{n30_mood}</td></tr>
            <tr><td style="padding:10px;border:1px solid #ddd;">BankNifty 9:30</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{bn30}</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{bn30_mood}</td></tr>
            <tr style="background:#f9f9f9;"><td style="padding:10px;border:1px solid #ddd;">A/D Ratio</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{breadth.get('A/D_Ratio','N/A')}</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{breadth.get('Signal','N/A')}</td></tr>
            <tr><td style="padding:10px;border:1px solid #ddd;">FII Net (Cr)</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{fii_net}</td><td style="padding:10px;border:1px solid #ddd;text-align:center;">{'🟢 Buy' if isinstance(fii_net,(int,float)) and fii_net>0 else '🔴 Sell'}</td></tr>
        </table>

        <h3 style="color:#1a237e;border-bottom:2px solid #e0e0e0;padding-bottom:8px;margin-top:25px;">📋 Key Reasons</h3>
        <p style="background:#f5f5f5;padding:15px;border-radius:6px;line-height:1.6;">{decode['Reasons']}</p>

        <hr style="margin:30px 0;border:none;border-top:1px solid #e0e0e0;">
        <p style="color:#999;font-size:12px;text-align:center;">Auto-generated by Pre-Market Decoder v2.0 | Sheets: Summary | Global | Indicators | Pre-Open | Sectoral | Breadth | FII/DII</p>
    </div></body></html>
    """

    # BUG FIX: Always attempt to send email, even if some data failed
    send_email(excel_name, html)
    print("\n🏁 PRE-MARKET DECODE COMPLETE!")

if __name__ == "__main__":
    main()
