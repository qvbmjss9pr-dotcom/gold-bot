import os
import asyncio
import anthropic
import httpx
import json
import hashlib
import re
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz
from bs4 import BeautifulSoup

# Configuration
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TWELVE_DATA_KEY   = os.environ.get("TWELVE_DATA_KEY", "f4c5ab2102dc48058107a1d5cca8923e")
TIMEZONE          = os.environ.get("TIMEZONE", "Asia/Baghdad")

alert_levels: dict = {}
seen_news: set = set()
last_gold_price: float = 0.0

SYMBOLS = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "USDCHF": "USD/CHF",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "GBPJPY": "GBP/JPY",
    "XAUUSD": "XAU/USD",
    "WTIUSD": "WTI/USD",
    "BTCUSD": "BTC/USD",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# Fetch real prices
async def fetch_prices() -> dict:
    prices = {}
    symbols = ",".join(SYMBOLS.values())
    url = f"https://api.twelvedata.com/price?symbol={symbols}&apikey={TWELVE_DATA_KEY}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=30)
            data = resp.json()
            for key, sym in SYMBOLS.items():
                if sym in data and "price" in data[sym]:
                    prices[key] = float(data[sym]["price"])
                elif key in data and "price" in data[key]:
                    prices[key] = float(data[key]["price"])
    except Exception as e:
        print(f"[Price Error] {e}")
    return prices

# Telegram
async def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            try:
                resp = await client.post(url, json=payload, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                print(f"[Telegram Error] {e}")
            await asyncio.sleep(0.5)

# Claude API
async def call_claude(prompt: str, max_tokens: int = 6000) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=max_tokens,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )
    result = ""
    for block in message.content:
        if hasattr(block, "text"):
            result += block.text
    return result.strip()

def extract_json(text: str):
    try:
        start = text.find("[JSON_START]") + len("[JSON_START]")
        end   = text.find("[JSON_END]")
        if start < len("[JSON_START]") or end == -1:
            return None
        return json.loads(text[start:end].strip())
    except Exception as e:
        print(f"[JSON Error] {e}")
        return None

# ─── ISX News (بورصة العراق) ──────────────────────────────────────────────────
async def fetch_isx_news() -> list:
    news_list = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "http://www.isx-iq.net/isxweb/main/news.aspx",
                headers=HEADERS
            )
            soup = BeautifulSoup(resp.content, "html.parser")
            # Target span elements with lblNews in their id
            items = soup.find_all("span", id=lambda x: x and "lblNews" in x)
            for item in items:
                text = item.get_text(strip=True)
                if text and len(text) > 20:
                    news_list.append(text)
    except Exception as e:
        print(f"[ISX Error] {e}")
    return news_list

# ─── ISC News (هيئة الأوراق المالية) ─────────────────────────────────────────
async def fetch_isc_news() -> list:
    news_list = []
    urls = ["https://isc.gov.iq/", "https://isc.gov.iq/?lang=ar"]
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers=HEADERS)
                html = resp.text
                # Find PDF upload links
                pdfs = re.findall(
                    r'https://isc\.gov\.iq/upload/\d{4}/\d{2}/\d{2}/[a-zA-Z0-9]+\.pdf',
                    html
                )
                for pdf in pdfs:
                    # Get surrounding text
                    idx = html.find(pdf)
                    if idx > 0:
                        raw = html[max(0, idx-400):idx+100]
                        clean = re.sub(r'<[^>]+>', ' ', raw)
                        clean = re.sub(r'\s+', ' ', clean).strip()
                        news_list.append({"text": clean[:500], "link": pdf})
                    else:
                        news_list.append({"text": "إعلان جديد", "link": pdf})
            if news_list:
                break
        except Exception as e:
            print(f"[ISC Error] {url}: {e}")
    return news_list

# ─── DFM News (سوق دبي) ───────────────────────────────────────────────────────
async def fetch_dfm_news() -> list:
    news_list = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.dfm.ae/the-exchange/news-announcement/market-news",
                headers=HEADERS
            )
            soup = BeautifulSoup(resp.content, "html.parser")
            # Find news items
            for tag in ["article", "div", "li"]:
                items = soup.find_all(tag, class_=re.compile(r"news|announce|item", re.I))
                for item in items[:10]:
                    text = item.get_text(strip=True)
                    if len(text) > 30:
                        link_tag = item.find("a", href=True)
                        link = ""
                        if link_tag:
                            href = link_tag["href"]
                            link = href if href.startswith("http") else f"https://www.dfm.ae{href}"
                        news_list.append({"text": text[:400], "link": link})
                if news_list:
                    break
    except Exception as e:
        print(f"[DFM Error] {e}")
    return news_list

# ─── Local Markets Job ────────────────────────────────────────────────────────
async def local_markets_job():
    global seen_news
    today = datetime.now().strftime("%d/%m/%Y")

    # ISX
    isx_news = await fetch_isx_news()
    for text in isx_news:
        news_id = hashlib.md5(text[:80].encode()).hexdigest()
        if news_id in seen_news or len(text) < 20:
            continue
        seen_news.add(news_id)
        await send_telegram(f"🇮🇶 *بورصة العراق للأوراق المالية*\n\n📅 {today}\n\n{text}")
        await asyncio.sleep(1)

    # ISC
    isc_news = await fetch_isc_news()
    for item in isc_news:
        link = item.get("link", "")
        news_id = hashlib.md5(link.encode()).hexdigest()
        if news_id in seen_news:
            continue
        seen_news.add(news_id)
        text = item.get("text", "إعلان جديد من هيئة الأوراق المالية العراقية")
        msg = f"🇮🇶 *هيئة الأوراق المالية العراقية*\n\n📅 {today}\n\n{text}"
        if link:
            msg += f"\n\n📎 {link}"
        await send_telegram(msg)
        await asyncio.sleep(1)

    # DFM
    dfm_news = await fetch_dfm_news()
    for item in dfm_news:
        text = item.get("text", "")
        news_id = hashlib.md5(text[:80].encode()).hexdigest()
        if news_id in seen_news or len(text) < 30:
            continue
        seen_news.add(news_id)
        link = item.get("link", "")
        msg = f"🇦🇪 *سوق دبي المالي*\n\n📅 {today}\n\n{text}"
        if link:
            msg += f"\n\n📎 {link}"
        await send_telegram(msg)
        await asyncio.sleep(1)

# ─── Daily Report ─────────────────────────────────────────────────────────────
async def daily_report_job():
    global alert_levels
    print(f"[{datetime.now()}] Running daily report...")
    try:
        prices = await fetch_prices()
        prices_text = "\n".join([f"- {k}: {v}" for k, v in prices.items()]) if prices else "غير متاحة"
        prompt = f"""
أنت محلل أسواق مالي محترف. أصدر نشرة يومية شاملة بعد إغلاق الأسواق.

الأسعار الحالية الدقيقة:
{prices_text}

الأصول:
- فوركس: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, GBPJPY
- معادن: XAUUSD | طاقة: WTI | كريبتو: BTCUSD
- مؤشرات: S&P 500, NASDAQ, DAX, DOW JONES

لكل أصل: السعر الدقيق، الاتجاه، الدعم، المقاومة، البيفوت (R2,R1,PP,S1,S2)، التوصية+دخول+هدف+وقف خسارة.

ثم أضف: ## 📰 أهم أخبار اليوم المؤثرة على الذهب والدولار

في آخر ردك JSON:
[JSON_START]
{{"EURUSD":{{"resistance":0.0,"support":0.0}},"GBPUSD":{{"resistance":0.0,"support":0.0}},"USDJPY":{{"resistance":0.0,"support":0.0}},"USDCHF":{{"resistance":0.0,"support":0.0}},"AUDUSD":{{"resistance":0.0,"support":0.0}},"USDCAD":{{"resistance":0.0,"support":0.0}},"GBPJPY":{{"resistance":0.0,"support":0.0}},"XAUUSD":{{"resistance":0.0,"support":0.0}},"WTIUSD":{{"resistance":0.0,"support":0.0}},"BTCUSD":{{"resistance":0.0,"support":0.0}},"SPX500":{{"resistance":0.0,"support":0.0}},"NASDAQ":{{"resistance":0.0,"support":0.0}},"DAX":{{"resistance":0.0,"support":0.0}},"DOWJONES":{{"resistance":0.0,"support":0.0}}}}
[JSON_END]

النشرة باللغة العربية. ابدأ بـ: 📊 *النشرة اليومية الشاملة* — [التاريخ]
"""
        response = await call_claude(prompt, max_tokens=8000)
        clean = response
        if "[JSON_START]" in response:
            clean = response[:response.find("[JSON_START]")].strip()
        await send_telegram(clean)
        data = extract_json(response)
        if data and isinstance(data, dict):
            alert_levels = {
                asset: {"resistance": vals.get("resistance", 0), "support": vals.get("support", 0), "res_alerted": False, "sup_alerted": False}
                for asset, vals in data.items()
            }
            await send_telegram("✅ *تم تحديث مستويات التنبيه التلقائية.*")
    except Exception as e:
        print(f"[Report Error] {e}")
        await send_telegram(f"⚠️ خطأ في النشرة:\n`{e}`")

# ─── Gold Update ──────────────────────────────────────────────────────────────
async def gold_update_job():
    global last_gold_price
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_KEY}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15)
            data = resp.json()
            price = float(data.get("price", 0))
            if price == 0:
                return
            change = price - last_gold_price if last_gold_price > 0 else 0
            arrow = "🟢 ▲" if change > 0 else "🔴 ▼" if change < 0 else "⚪️ ─"
            if last_gold_price > 0 and abs(change) > 0.5:
                await send_telegram(
                    f"🥇 *تحديث الذهب اللحظي*\n\n"
                    f"السعر: *{price:.2f}* دولار\n"
                    f"التغيير: {arrow} {abs(change):.2f}\n"
                    f"🕐 {datetime.now().strftime('%H:%M')} بغداد"
                )
            last_gold_price = price
    except Exception as e:
        print(f"[Gold Error] {e}")

# ─── Alert Check ──────────────────────────────────────────────────────────────
async def alert_check_job():
    global alert_levels
    if not alert_levels:
        return
    try:
        prices = await fetch_prices()
        for asset, levels in alert_levels.items():
            price = prices.get(asset, 0)
            if price == 0:
                continue
            if levels.get("resistance", 0) > 0 and price >= levels["resistance"] and not levels.get("res_alerted"):
                await send_telegram(f"⚡️ *تنبيه | {asset}*\nكسر مقاومة {levels['resistance']:.4f} عند {price:.4f} 🟢")
                alert_levels[asset]["res_alerted"] = True
            if levels.get("support", 0) > 0 and price <= levels["support"] and not levels.get("sup_alerted"):
                await send_telegram(f"⚡️ *تنبيه | {asset}*\nكسر دعم {levels['support']:.4f} عند {price:.4f} 🔴")
                alert_levels[asset]["sup_alerted"] = True
    except Exception as e:
        print(f"[Alert Error] {e}")

# ─── Gold & Dollar News ───────────────────────────────────────────────────────
async def news_check_job():
    global seen_news
    try:
        prompt = """
ابحث عن أهم الأخبار المؤثرة على الذهب والدولار خلال آخر 30 دقيقة فقط.
أجب بـ JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
[{"id":"id_فريد","title":"عنوان","summary":"ملخص جملتين","impact":"high/medium","time":"الوقت"}]
[JSON_END]
إذا لم توجد: [JSON_START][][JSON_END]
"""
        response = await call_claude(prompt, max_tokens=1500)
        news_list = extract_json(response)
        if not news_list or not isinstance(news_list, list):
            return
        for news in news_list:
            news_id = news.get("id", "")
            if not news_id or news_id in seen_news:
                continue
            seen_news.add(news_id)
            emoji = "🚨" if news.get("impact") == "high" else "📰"
            await send_telegram(
                f"{emoji} *خبر مؤثر على الذهب والدولار*\n\n"
                f"📌 {news.get('title','')}\n\n"
                f"{news.get('summary','')}\n\n"
                f"🕐 {news.get('time','')}"
            )
    except Exception as e:
        print(f"[News Error] {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    tz        = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(daily_report_job,  "cron",     hour=0,  minute=0)
    scheduler.add_job(daily_report_job,  "date")
    scheduler.add_job(gold_update_job,   "interval", minutes=15)
    scheduler.add_job(alert_check_job,   "interval", minutes=15)
    scheduler.add_job(news_check_job,    "interval", minutes=30)
    scheduler.add_job(local_markets_job, "interval", minutes=10)
    scheduler.start()

    print(f"✅ Bot started | {TIMEZONE}")

    await send_telegram(
        "🤖 *بوت الأسواق المالية — النسخة النهائية*\n\n"
        "📅 النشرة اليومية: 12:00 ليلاً\n"
        "🥇 تحديث الذهب: كل 15 دقيقة\n"
        "⚡️ تنبيهات المستويات: كل 15 دقيقة\n"
        "📰 أخبار الذهب والدولار: كل 30 دقيقة\n"
        "🇮🇶 بورصة العراق وهيئة الأوراق المالية: كل 10 دقائق\n"
        "🇦🇪 سوق دبي المالي: كل 10 دقائق\n\n"
        "✅ أسعار دقيقة من Twelve Data"
    )

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
