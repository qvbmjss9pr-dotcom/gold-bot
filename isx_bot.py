#!/usr/bin/env python3
"""
Iraq Stock Exchange News Monitor Bot
بوت مراقبة أخبار بورصة العراق
"""

import asyncio
import logging
import hashlib
import json
import random
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from telegram import Bot

# ─── الإعدادات ───────────────────────────────
BOT_TOKEN      = "8695663808:AAGJPSZ5BUoD6y1vxDGEyRc_w0HSr2-jg-c"
CHAT_ID        = "191727756"
CHECK_INTERVAL = 300
SEEN_FILE      = "seen_news.json"
MAX_SEEN       = 500
NEWS_URL       = "http://www.isx-iq.net/isxportal/portal/storyList.html"
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_seen() -> set:
    if Path(SEEN_FILE).exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    items = list(seen)
    if len(items) > MAX_SEEN:
        items = items[-MAX_SEEN:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)


def make_id(title: str, date_str: str) -> str:
    raw = f"{title.strip()}{date_str.strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


async def fetch_news(client: httpx.AsyncClient) -> list:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "ar,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "http://www.isx-iq.net",
        "Referer": "http://www.isx-iq.net/isxportal/portal/storyList.html?activeTab=0",
    }

    params = {
        "methodName": "getAnnouncmentStoryList",
        "random": str(random.random()),
    }

    try:
        resp = await client.post(NEWS_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.error("فشل جلب الأخبار: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    news = []

    # التاريخ في td class="table-newsdata"
    # العنوان في span class="indnews-title" > a
    date_cells = soup.find_all("td", class_="table-newsdata")

    for td in date_cells:
        date_str = td.get_text(strip=True)

        # العنوان والرابط في نفس الصف
        tr = td.find_parent("tr")
        if not tr:
            continue

        span = tr.find("span", class_="indnews-title")
        if not span:
            continue

        a_tag = span.find("a", href=True)
        if not a_tag:
            continue

        title = a_tag.get_text(strip=True)
        href  = a_tag["href"]
        link  = href if href.startswith("http") else f"http://www.isx-iq.net{href}"

        if not title:
            continue

        news.append({
            "title": title,
            "date":  date_str,
            "link":  link,
        })

    log.info("عدد الأخبار المجلوبة: %d", len(news))
    return news


async def send_news(bot: Bot, item: dict):
    date_str = item.get("date", "")
    title    = item.get("title", "")
    link     = item.get("link", "")

    text = (
        "📢 خبر جديد - بورصة العراق\n"
        "─────────────────────\n"
        f"🕐 {date_str}\n\n"
        f"📌 {title}\n\n"
        f"🔗 {link}"
    )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=text)
        log.info("✅ أُرسل: %s", title[:60])
    except Exception as e:
        log.error("❌ فشل الإرسال: %s", e)


async def main():
    log.info("🚀 بدأ بوت بورصة العراق")
    bot  = Bot(token=BOT_TOKEN)
    seen = load_seen()

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="✅ بوت بورصة العراق يعمل الآن\n🔄 يراقب الأخبار كل 5 دقائق",
        )
    except Exception as e:
        log.error("فشل رسالة البداية: %s", e)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        while True:
            log.info("🔍 جاري التحقق من الأخبار...")
            news_list = await fetch_news(client)

            new_items = []
            for item in news_list:
                nid = make_id(item["title"], item["date"])
                if nid not in seen:
                    new_items.append((nid, item))

            if new_items:
                log.info("📰 %d خبر جديد", len(new_items))
                for nid, item in reversed(new_items):
                    await send_news(bot, item)
                    seen.add(nid)
                    await asyncio.sleep(1)
                save_seen(seen)
            else:
                log.info("لا جديد")

            await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
