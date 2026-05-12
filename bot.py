import os
import asyncio
import anthropic
import httpx
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz

# ─── Configuration ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TIMEZONE          = os.environ.get("TIMEZONE", "Asia/Riyadh")

# ─── Alert levels storage ─────────────────────────────────────────────────────
alert_levels: dict = {}

# ─── Telegram Helper ──────────────────────────────────────────────────────────
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

# ─── Claude API Call ──────────────────────────────────────────────────────────
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

# ─── Daily Report Prompt ──────────────────────────────────────────────────────
DAILY_REPORT_PROMPT = """
أنت محلل أسواق مالي محترف. أصدر نشرة يومية شاملة بعد إغلاق الأسواق تغطي:

الأصول:
- فوركس: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, GBPJPY
- معادن: XAUUSD (ذهب)
- طاقة: WTI Crude Oil (نفط)
- كريبتو: BTCUSD (بيتكوين)
- مؤشرات: S&P 500, NASDAQ, DAX, DOW JONES

لكل أصل اذكر:
1. سعر الإغلاق الحالي
2. الاتجاه العام
3. أهم دعم ومقاومة
4. البيفوت: R2, R1, PP, S1, S2
5. توصية: شراء/بيع/انتظار + دخول + هدف + وقف خسارة

ثم في نهاية النشرة أضف:
## مستويات المراقبة التلقائية

وفي آخر ردك أضف JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
{
  "EURUSD":   {"resistance": 0.0, "support": 0.0},
  "GBPUSD":   {"resistance": 0.0, "support": 0.0},
  "USDJPY":   {"resistance": 0.0, "support": 0.0},
  "USDCHF":   {"resistance": 0.0, "support": 0.0},
  "AUDUSD":   {"resistance": 0.0, "support": 0.0},
  "USDCAD":   {"resistance": 0.0, "support": 0.0},
  "GBPJPY":   {"resistance": 0.0, "support": 0.0},
  "XAUUSD":   {"resistance": 0.0, "support": 0.0},
  "WTIUSD":   {"resistance": 0.0, "support": 0.0},
  "BTCUSD":   {"resistance": 0.0, "support": 0.0},
  "SPX500":   {"resistance": 0.0, "support": 0.0},
  "NASDAQ":   {"resistance": 0.0, "support": 0.0},
  "DAX":      {"resistance": 0.0, "support": 0.0},
  "DOWJONES": {"resistance": 0.0, "support": 0.0}
}
[JSON_END]

النشرة باللغة العربية. ابدأ بـ:
📊 *النشرة اليومية الشاملة* — [التاريخ]
"""

# ─── Alert Check ──────────────────────────────────────────────────────────────
def build_alert_prompt(levels: dict) -> str:
    return f"""
أنت محلل أسواق. هذه المستويات الحرجة للمراقبة:
{json.dumps(levels, indent=2)}

ابحث عن الأسعار الحالية اللحظية لكل أصل.
هل كسر أي أصل مستوى الدعم أو المقاومة؟

أجب فقط بـ JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
[
  {{
    "asset": "EURUSD",
    "type": "resistance_break",
    "message": "⚡️ تنبيه | EURUSD كسر مقاومة 1.0950 صعوداً — إشارة شراء 🟢"
  }}
]
[JSON_END]

إذا لم يكسر شيء: [JSON_START][][JSON_END]
"""

# ─── Parse JSON ───────────────────────────────────────────────────────────────
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

# ─── Daily Report Job ─────────────────────────────────────────────────────────
async def daily_report_job():
    global alert_levels
    print(f"[{datetime.now()}] Running daily report...")
    try:
        response = await call_claude(DAILY_REPORT_PROMPT, max_tokens=8000)

        # Send clean report (without JSON block)
        clean = response
        if "[JSON_START]" in response:
            clean = response[:response.find("[JSON_START]")].strip()
        await send_telegram(clean)

        # Update alert levels
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
            print(f"[{datetime.now()}] Alert levels set for: {list(alert_levels.keys())}")
            await send_telegram("✅ *تم تحديث مستويات التنبيه التلقائية للجلسة القادمة.*")

    except Exception as e:
        print(f"[{datetime.now()}] Report error: {e}")
        await send_telegram(f"⚠️ خطأ في النشرة:\n`{e}`")

# ─── Alert Check Job ──────────────────────────────────────────────────────────
async def alert_check_job():
    global alert_levels
    if not alert_levels:
        return
    print(f"[{datetime.now()}] Checking alerts...")
    try:
        response = await call_claude(build_alert_prompt(alert_levels), max_tokens=2000)
        alerts   = extract_json(response)
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
        print(f"[{datetime.now()}] Alert error: {e}")

# ─── News Storage ─────────────────────────────────────────────────────────────
seen_news: set = set()

# ─── News Check Job ───────────────────────────────────────────────────────────
async def news_check_job():
    global seen_news
    try:
        prompt = """
ابحث عن أحدث الأخبار والإعلانات الصادرة اليوم من:
1. بورصة العراق للأوراق المالية (ISX) - isx-iq.net
2. هيئة الأوراق المالية العراقية (ISC) - isc.gov.iq
3. سوق دبي المالي (DFM) - dfm.ae

أعطني فقط الأخبار الجديدة الصادرة خلال آخر 30 دقيقة.

أجب بـ JSON بين [JSON_START] و [JSON_END]:
[JSON_START]
[
  {
    "id": "عنوان_مختصر_فريد",
    "source": "بورصة العراق",
    "title": "عنوان الخبر",
    "summary": "ملخص الخبر في جملتين",
    "time": "الوقت"
  }
]
[JSON_END]

إذا لم توجد أخبار جديدة: [JSON_START][][JSON_END]
"""
        response = await call_claude(prompt, max_tokens=2000)
        news_list = extract_json(response)
        if not news_list or not isinstance(news_list, list):
            return
        for news in news_list:
            news_id = news.get("id", "")
            if not news_id or news_id in seen_news:
                continue
            seen_news.add(news_id)
            source   = news.get("source", "")
            title    = news.get("title", "")
            summary  = news.get("summary", "")
            time_str = news.get("time", "")
            emoji = "🇮🇶" if "عراق" in source else "🇦🇪"
            msg = (
                f"{emoji} *خبر عاجل | {source}*\n\n"
                f"📌 {title}\n\n"
                f"{summary}\n\n"
                f"🕐 {time_str}"
            )
            await send_telegram(msg)
    except Exception as e:
        print(f"[News Error] {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    tz        = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(daily_report_job, "cron", hour=0, minute=0)
    scheduler.add_job(daily_report_job, "date")
    scheduler.add_job(alert_check_job,  "interval", minutes=15)
    scheduler.add_job(news_check_job,   "interval", minutes=30)
    scheduler.start()

    print(f"✅ Bot started | Report: 00:00 {TIMEZONE} | Alerts: every 15 min | News: every 30 min")

    await send_telegram(
        "🤖 *بوت الأسواق المالية — يعمل الآن*\n\n"
        "📅 النشرة اليومية: كل يوم 12:00 ليلاً\n"
        "🔔 تنبيهات المستويات: كل 15 دقيقة\n"
        "📰 أخبار البورصات: كل 30 دقيقة\n\n"
        "🔸 فوركس | ذهب | نفط | كريبتو | مؤشرات\n"
        "🇮🇶 بورصة العراق | هيئة الأوراق المالية\n"
        "🇦🇪 سوق دبي المالي"
    )

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
