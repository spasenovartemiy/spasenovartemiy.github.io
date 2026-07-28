import asyncio
import logging

import db
from bot import bot, dp, send_vacancy
from config import (BOT_TOKEN, CHANNELS, FIRST_RUN_LIMIT, MIN_SCORE, OWNER_ID,
                    POLL_INTERVAL)
from fetcher import fetch_channel_html
from scorer import score_post
from tgparser import parse_page
from tgparser2 import parse_page_single

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("main")


def _parse(html, fmt):
    return parse_page_single(html) if fmt == "single" else parse_page(html)


async def process_channel(channel: str, fmt: str, first_run: bool):
    try:
        html = await asyncio.to_thread(fetch_channel_html, channel)
    except Exception as e:
        log.error("[%s] не смог скачать: %s", channel, e)
        return

    posts = await asyncio.to_thread(_parse, html, fmt)
    log.info("[%s/%s] постов с вакансиями: %d", channel, fmt, len(posts))

    fresh = [p for p in posts if not db.post_seen(p.post_id)]

    if first_run and len(fresh) > FIRST_RUN_LIMIT:
        log.info("[%s] первый запуск: беру %d последних", channel, FIRST_RUN_LIMIT)
        for p in fresh[:-FIRST_RUN_LIMIT]:
            db.mark_post_seen(p.post_id)
        fresh = fresh[-FIRST_RUN_LIMIT:]

    if not fresh:
        log.info("[%s] новых постов нет", channel)
        return

    for post in fresh:
        log.info("[%s] пост %s (%s): %d вакансий",
                 channel, post.post_id, post.company or "-", len(post.vacancies))
        scores = await asyncio.to_thread(score_post, post)

        for v in post.vacancies:
            score, reason = scores.get(v.idx, (5, ""))
            db.save_vacancy(v, score, reason)

            if score >= MIN_SCORE:
                row = db.get_vacancy(v.uid)
                try:
                    await send_vacancy(row)
                    await asyncio.sleep(0.4)
                except Exception as e:
                    log.error("не отправил %s: %s", v.uid, e)

        db.mark_post_seen(post.post_id)


async def process_once():
    for channel, fmt in CHANNELS:
        first = not db.channel_has_seen(channel)
        await process_channel(channel, fmt, first_run=first)


async def poller():
    while True:
        try:
            await process_once()
        except Exception as e:
            log.exception("сбой в цикле поллинга: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


async def main():
    if not BOT_TOKEN or not OWNER_ID:
        raise SystemExit("Не заданы BOT_TOKEN / OWNER_ID — заполни .env")

    db.init()
    log.info("старт: каналы %s, интервал %d сек, порог score %d",
             ", ".join("@%s(%s)" % (c, f) for c, f in CHANNELS),
             POLL_INTERVAL, MIN_SCORE)

    asyncio.create_task(poller())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
