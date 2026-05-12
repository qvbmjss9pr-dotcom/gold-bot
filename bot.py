import os
import asyncio
import anthropic
import httpx
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TIMEZONE          = os.environ.get("TIMEZONE", "Asia/Riyadh")

alert_levels: dict = {}

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

async def call_claude(prompt: str, max_tokens: int = 6000) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )
    result = ""
    for block in message.content:
        if hasattr(block, "text"):
            result += block.text
    return result.strip()

DAILY_REPORT_PROMPT = """
أنت محلل أسواق مالي محترف. أصدر نشرة يومية شاملة بعد إغلاق الأسواق تغطي:
- فوركس: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, GBPJPY
- معادن: XAUUSD (ذهب)
- طاقة: WTI Crude Oil
- كريبتو: BTCUSD
- مؤشرات: S&P 500, NASDAQ, DAX, DOW JONES

لكل أصل: السعر، الاتجاه، الدعم، المقاومة، البيفوت، التوصية.

في آخر ردك أضف JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
{"EURUSD":{"resistance":0.0,"support":0.0},"GBPUSD":{"resistance":0.0,"support":0.0},"USDJPY":{"resistance":0.0,"support":0.0},"USDCHF":{"resistance":0.0,"support":0.0},"AUDUSD":{"resistance":0.0,"support":0.0},"USDCAD":{"resistance":0.0,"support":0.0},"GBPJPY":{"resistance":0.0,"support":0.0},"XAUUSD":{"resistance":0.0,"support":0.0},"WTIUSD":{"resistance":0.0,"support":0.0},"BTCUSD":{"resistance":0.0,"support":0.0},"SPX500":{"resistance":0.0,"support":0.0},"NASDAQ":{"resistance":0.0,"support":0.0},"DAX":{"resistance":0.0,"support":0.0},"DOWJONES":{"resistance":0.0,"support":0.0}}
[JSON_END]

النشرة باللغة العربية. ابدأ بـ: 📊 *النشرة اليومية الشاملة* — [التاريخ]
"""

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

async def daily_report_job():
    global alert_levels
    print(f"[{datetime.now()}] Running daily report...")
    try:
        response = await call_claude(DAILY_REPORT_PROMPT, max_tokens=8000)
        clean = response
        if "[JSON_START]" in response:
            clean = response[:response.find("[JSON_START]")].strip()
        await send_telegram(clean)
        data = extract_json(response)
        if data and isinstance(data, dict):
            alert_levels = {asset: {"resistance": vals.get("resistance", 0), "support": vals.get("support", 0), "res_alerted": False, "sup_alerted": False} for asset, vals in data.items()}
            await send_telegram("✅ *تم تحديث مستويات التنبيه التلقائية.*")
    except Exception as e:
        await send_telegram(f"⚠️ خطأ: `{e}`")

async def alert_check_job():
    global alert_levels
    if not alert_levels:
        return
    try:
        prompt = f"ابحث عن الأسعار الحالية لهذه الأصول وأخبرني إذا كسر أي منها مستوى الدعم أو المقاومة:\n{json.dumps(alert_levels)}\nأجب بـ JSON بين [JSON_START] و [JSON_END]: [{{'asset':'EURUSD','type':'resistance_break','message':'تنبيه...'}}] أو مصفوفة فارغة."
        response = await call_claude(prompt, max_tokens=2000)
        alerts = extract_json(response)
        if not alerts or not isinstance(alerts, list):
            return
        for alert in alerts:
            asset = alert.get("asset", "")
            atype = alert.get("type", "")
            msg   = alert.get("message", "")
            if not msg or asset not in alert_levels:
                continue
            if atype == "resistance_break" and alert_levels[asset].get("res_alerted"):
                continue
            if atype == "support_break" and alert_levels[asset].get("sup_alerted"):
                continue
            await send_telegram(msg)
            if atype == "resistance_break":
                alert_levels[asset]["res_alerted"] = True
            elif atype == "support_break":
                alert_levels[asset]["sup_alerted"] = True
    except Exception as e:
        print(f"[Alert Error] {e}")

async def main():
    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(daily_report_job, "cron", hour=0, minute=0)
    scheduler.add_job(alert_check_job, "interval", minutes=15)
    scheduler.start()
    await send_telegram("🤖 *بوت الأسواق المالية — يعمل الآن*\n\n📅 النشرة: 12:00 ليلاً\n🔔 تنبيهات: كل 15 دقيقة")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
