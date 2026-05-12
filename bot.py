import os
import asyncio
import anthropic
import httpx
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz

# Configuration
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TWELVE_DATA_KEY   = os.environ.get("TWELVE_DATA_KEY", "f4c5ab2102dc48058107a1d5cca8923e")
TIMEZONE          = os.environ.get("TIMEZONE", "Asia/Baghdad")

alert_levels: dict = {}
seen_news: set = set()
last_gold_price: float = 0.0

# Symbols to fetch
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

# Fetch real prices from Twelve Data
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

# Daily Report
async def daily_report_job():
    global alert_levels
    print(f"[{datetime.now()}] Running daily report...")
    try:
        prices = await fetch_prices()
        prices_text = "\n".join([f"- {k}: {v}" for k, v in prices.items()]) if prices else "غير متاحة"

        prompt = f"""
أنت محلل أسواق مالي محترف. أصدر نشرة يومية شاملة بعد إغلاق الأسواق.

الأسعار الحالية الدقيقة من Twelve Data:
{prices_text}

الأصول المطلوب تحليلها:
- فوركس: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, GBPJPY
- معادن: XAUUSD (ذهب)
- طاقة: WTI (نفط)
- كريبتو: BTCUSD
- مؤشرات: S&P 500, NASDAQ, DAX, DOW JONES

لكل أصل اذكر:
1. سعر الإغلاق الدقيق (من البيانات أعلاه)
2. الاتجاه العام
3. أهم دعم ومقاومة
4. البيفوت: R2, R1, PP, S1, S2
5. توصية: شراء/بيع/انتظار + دخول + هدف + وقف خسارة

ثم أضف قسم:
## 📰 أهم الأخبار المؤثرة على الذهب والدولار اليوم
(ابحث عن أهم 5 أخبار اقتصادية وجيوسياسية مؤثرة)

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

# Gold Live Update every 15 min
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

# News Check
async def news_check_job():
    global seen_news
    try:
        prompt = """
ابحث عن أهم الأخبار الاقتصادية والجيوسياسية المؤثرة على الذهب والدولار الصادرة خلال آخر 30 دقيقة فقط.

أجب بـ JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
[{"id":"id_فريد","title":"عنوان الخبر","summary":"ملخص في جملتين","impact":"high/medium","time":"الوقت"}]
[JSON_END]

إذا لم توجد أخبار جديدة مهمة خلال 30 دقيقة: [JSON_START][][JSON_END]
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

# Iraqi & Dubai Markets News
async def local_markets_job():
    global seen_news
    try:
        prompt = """
ابحث عن أحدث الأخبار من بورصة العراق (isx-iq.net) وهيئة الأوراق المالية العراقية (isc.gov.iq) وسوق دبي المالي (dfm.ae) خلال آخر 30 دقيقة.

أجب بـ JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
[{"id":"id_فريد","source":"بورصة العراق","title":"عنوان","summary":"ملخص","time":"الوقت"}]
[JSON_END]

إذا لم توجد أخبار: [JSON_START][][JSON_END]
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
            source = news.get("source", "")
            emoji = "🇮🇶" if "عراق" in source else "🇦🇪"
            msg = (
                f"{emoji} *خبر عاجل | {source}*\n\n"
                f"📌 {news.get('title','')}\n\n"
                f"{news.get('summary','')}\n\n"
                f"🕐 {news.get('time','')}"
            )
            await send_telegram(msg)
    except Exception as e:
        print(f"[Local News Error] {e}")

# Main
async def main():
    tz        = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(daily_report_job,   "cron",     hour=0,    minute=0)
    scheduler.add_job(daily_report_job,   "date")
    scheduler.add_job(gold_update_job,    "interval", minutes=15)
    scheduler.add_job(alert_check_job,    "interval", minutes=15)
    scheduler.add_job(news_check_job,     "interval", minutes=30)
    scheduler.add_job(local_markets_job,  "interval", minutes=30)
    scheduler.start()

    print(f"✅ Bot started | {TIMEZONE}")

    await send_telegram(
        "🤖 *بوت الأسواق المالية — النسخة المحدّثة*\n\n"
        "📅 النشرة اليومية: 12:00 ليلاً\n"
        "🥇 تحديث الذهب اللحظي: كل 15 دقيقة\n"
        "⚡️ تنبيهات كسر المستويات: كل 15 دقيقة\n"
        "📰 أخبار الذهب والدولار: كل 30 دقيقة\n"
        "🇮🇶🇦🇪 أخبار بورصة العراق ودبي: كل 30 دقيقة\n\n"
        "✅ أسعار دقيقة من Twelve Data"
    )

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())

