import os
import asyncio
import anthropic
import httpx
import json
import hashlib
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

# Scrape Iraqi Securities Commission directly
async def scrape_isc() -> list:
    news = []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get("https://isc.gov.iq/", headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all(["article", "div", "li"], limit=30)
            for item in items:
                text = item.get_text(strip=True)
                links = item.find_all("a", href=True)
                if len(text) > 30:
                    news_id = hashlib.md5(text[:100].encode()).hexdigest()
                    link = ""
                    for a in links:
                        href = a.get("href", "")
                        if href.startswith("http") or href.startswith("/"):
                            link = href if href.startswith("http") else f"https://isc.gov.iq{href}"
                            break
                    news.append({"id": news_id, "text": text[:300], "link": link, "source": "هيئة الأوراق المالية العراقية"})
    except Exception as e:
        print(f"[ISC Scrape Error] {e}")
    return news

# Scrape Iraq Stock Exchange
async def scrape_isx() -> list:
    news = []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get("https://www.isx-iq.net/isxportal/portal/newsDisplay.html", headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.find_all(["tr", "div", "article"], limit=20)
            for row in rows:
                text = row.get_text(strip=True)
                if len(text) > 30:
                    news_id = hashlib.md5(text[:100].encode()).hexdigest()
                    news.append({"id": news_id, "text": text[:300], "link": "", "source": "بورصة العراق للأوراق المالية"})
    except Exception as e:
        print(f"[ISX Scrape Error] {e}")
    return news

# Scrape Dubai Financial Market
async def scrape_dfm() -> list:
    news = []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get("https://www.dfm.ae/en/news", headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all(["article", "div", "li"], limit=20)
            for item in items:
                text = item.get_text(strip=True)
                if len(text) > 30:
                    news_id = hashlib.md5(text[:100].encode()).hexdigest()
                    links = item.find_all("a", href=True)
                    link = ""
                    for a in links:
                        href = a.get("href", "")
                        if href:
                            link = href if href.startswith("http") else f"https://www.dfm.ae{href}"
                            break
                    news.append({"id": news_id, "text": text[:300], "link": link, "source": "سوق دبي المالي"})
    except Exception as e:
        print(f"[DFM Scrape Error] {e}")
    return news

# Local Markets Job
async def local_markets_job():
    global seen_news
    all_news = []
    all_news += await scrape_isc()
    all_news += await scrape_isx()
    all_news += await scrape_dfm()

    for item in all_news:
        news_id = item.get("id", "")
        if not news_id or news_id in seen_news:
            continue
        text = item.get("text", "").strip()
        if len(text) < 50:
            continue
        seen_news.add(news_id)
        source = item.get("source", "")
        link = item.get("link", "")
        emoji = "🇮🇶" if "عراق" in source or "العراق" in source else "🇦🇪"
        msg = f"{emoji} *{source}*\n\n{text}"
        if link:
            msg += f"\n\n🔗 {link}"
        await send_telegram(msg)
        await asyncio.sleep(1)

# Daily Report
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

الأصول المطلوب تحليلها:
- فوركس: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, GBPJPY
- معادن: XAUUSD (ذهب)
- طاقة: WTI (نفط)
- كريبتو: BTCUSD
- مؤشرات: S&P 500, NASDAQ, DAX, DOW JONES

لكل أصل اذكر:
1. سعر الإغلاق الدقيق
2. الاتجاه العام
3. أهم دعم ومقاومة
4. البيفوت: R2, R1, PP, S1, S2
5. توصية: شراء/بيع/انتظار + دخول + هدف + وقف خسارة

ثم أضف:
## 📰 أهم أخبار اليوم المؤثرة على الذهب والدولار

في آخر ردك أضف JSON:
[JSON_START]
{{"EURUSD":{{"resistance":0.0,"support":0.0}},"GBPUSD":{{"resistance":0.0,"support":0.0}},"USDJPY":{{"resistance":0.0,"support":0.0}},"USDCHF":{{"resistance":0.0,"support":0.0}},"AUDUSD":{{"resistance":0.0,"support":0.0}},"USDCAD":{{"resistance":0.0,"support":0.0}},"GBPJPY":{{"resistance":0.0,"support":0.0}},"XAUUSD":{{"resistance":0.0,"support":0.0}},"WTIUSD":{{"resistance":0.0,"support":0.0}},"BTCUSD":{{"resistance":0.0,"support":0.0}},"SPX500":{{"resistance":0.0,"support":0.0}},"NASDAQ":{{"resistance":0.0,"support":0.0}},"DAX":{{"resistance":0.0,"support":0.0}},"DOWJONES":{{"resistance":0.0,"support":0.0}}}}
[JSON_END]

النشرة باللغة العربية. ابدأ بـ:
📊 *النشرة اليومية الشاملة* — [التاريخ]
"""
        response = await call_claude(prompt, max_tokens=8000)
        clean = response
        if "[JSON_START]" in response:
            clean = response[:response.find("[JSON_START]")].strip()
        await send_telegram(clean)

        data = extract_json(response)
        if data and isinstance(data, dict):
            alert_levels = {
                asset: {
                    "resistance":  vals.get("resistance", 0),
                    "support":     vals.get("support", 0),
                    "res_alerted": False,
                    "sup_alerted": False,
                }
                for asset, vals in data.items()
            }
            await send_telegram("✅ *تم تحديث مستويات التنبيه التلقائية.*")
    except Exception as e:
        print(f"[Report Error] {e}")
        await send_telegram(f"⚠️ خطأ في النشرة:\n`{e}`")

# Gold Live Update
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
            msg = (
                f"🥇 *تحديث الذهب اللحظي*\n\n"
                f"السعر الحالي: *{price:.2f}* دولار\n"
                f"التغيير: {arrow} {abs(change):.2f}\n"
                f"🕐 {datetime.now().strftime('%H:%M')} بتوقيت بغداد"
            )
            if last_gold_price > 0 and abs(change) > 0.5:
                await send_telegram(msg)
            last_gold_price = price
    except Exception as e:
        print(f"[Gold Update Error] {e}")

# Alert Check
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
            resistance = levels.get("resistance", 0)
            support = levels.get("support", 0)
            if resistance > 0 and price >= resistance and not levels.get("res_alerted"):
                await send_telegram(f"⚡️ *تنبيه | {asset}*\nكسر مقاومة {resistance:.4f} صعوداً عند {price:.4f} 🟢")
                alert_levels[asset]["res_alerted"] = True
            if support > 0 and price <= support and not levels.get("sup_alerted"):
                await send_telegram(f"⚡️ *تنبيه | {asset}*\nكسر دعم {support:.4f} هبوطاً عند {price:.4f} 🔴")
                alert_levels[asset]["sup_alerted"] = True
    except Exception as e:
        print(f"[Alert Error] {e}")

# Gold & Dollar News
async def news_check_job():
    global seen_news
    try:
        prompt = """
ابحث عن أهم الأخبار الاقتصادية والجيوسياسية المؤثرة على الذهب والدولار خلال آخر 30 دقيقة فقط.

أجب بـ JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
[{"id":"id_فريد","title":"عنوان الخبر","summary":"ملخص في جملتين","impact":"high/medium","time":"الوقت"}]
[JSON_END]

إذا لم توجد أخبار مهمة جديدة: [JSON_START][][JSON_END]
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
            impact = news.get("impact", "medium")
            emoji = "🚨" if impact == "high" else "📰"
            msg = (
                f"{emoji} *خبر مؤثر على الذهب والدولار*\n\n"
                f"📌 {news.get('title','')}\n\n"
                f"{news.get('summary','')}\n\n"
                f"🕐 {news.get('time','')}"
            )
            await send_telegram(msg)
    except Exception as e:
        print(f"[News Error] {e}")

# Main
async def main():
    tz        = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(daily_report_job,  "cron",     hour=0,    minute=0)
    scheduler.add_job(daily_report_job,  "date")
    scheduler.add_job(gold_update_job,   "interval", minutes=15)
    scheduler.add_job(alert_check_job,   "interval", minutes=15)
    scheduler.add_job(news_check_job,    "interval", minutes=30)
    scheduler.add_job(local_markets_job, "interval", minutes=30)
    scheduler.start()

    print(f"✅ Bot started | {TIMEZONE}")

    await send_telegram(
        "🤖 *بوت الأسواق المالية — النسخة النهائية*\n\n"
        "📅 النشرة اليومية: 12:00 ليلاً\n"
        "🥇 تحديث الذهب: كل 15 دقيقة\n"
        "⚡️ تنبيهات المستويات: كل 15 دقيقة\n"
        "📰 أخبار الذهب والدولار: كل 30 دقيقة\n"
        "🇮🇶 بورصة العراق وهيئة الأوراق المالية: مباشر\n"
        "🇦🇪 سوق دبي المالي: مباشر\n\n"
        "✅ أسعار دقيقة من Twelve Data"
    )

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
