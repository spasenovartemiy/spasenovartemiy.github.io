import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

import db
from config import BOT_TOKEN, OWNER_ID

log = logging.getLogger("bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _score_icon(score: int) -> str:
    if score >= 9:
        return "🟢"
    if score >= 6:
        return "🟡"
    return "🟠"


def card_text(v) -> str:
    salary = v["salary_raw"] or "не указана"
    company = v["company"] or "—"
    lines = [
        f"{_score_icon(v['score'])} <b>{v['title']}</b>",
        f"Компания: {company}",
        f"З/П: {salary}",
        f"Оценка: {v['score']}/10 — {v['reason']}",
    ]
    if v["url"]:
        try:
            contact = v["contact"]
        except (KeyError, IndexError):
            contact = ""
        label = "Написать рекрутёру" if contact else "Открыть вакансию"
        lines.append(f'\n<a href="{v["url"]}">{label}</a>')
    return "\n".join(lines)


def card_kb(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"pick:{uid}"),
        InlineKeyboardButton(text="✖️ Скип", callback_data=f"skip:{uid}"),
    ]])


def retry_kb(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Попробовать снова", callback_data=f"pick:{uid}"),
    ]])


async def send_vacancy(v):
    await bot.send_message(
        OWNER_ID,
        card_text(v),
        reply_markup=card_kb(v["uid"]),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    db.set_status(v["uid"], "sent")


# ---------------- пайплайн генерации ----------------

async def run_generation(uid: str, status_msg: Message):
    """Тянет JD -> генерит резюме -> публикует -> присылает ссылку и письмо."""
    from generator import generate
    from jd_fetcher import fetch_jd
    from publisher import publish

    v = db.get_vacancy(uid)
    title = v["title"]

    try:
        await status_msg.edit_text(
            f"⏳ <b>{title}</b>\nЧитаю описание вакансии…", parse_mode="HTML")
        try:
            inline_jd = v["jd_inline"]
        except (KeyError, IndexError):
            inline_jd = ""
        if inline_jd:
            jd = inline_jd               # single-формат: текст уже в посте
        else:
            jd = await asyncio.to_thread(fetch_jd, v["url"])

        note = "по тексту вакансии" if jd else "по названию (описание не открылось)"
        await status_msg.edit_text(
            f"⏳ <b>{title}</b>\nСобираю резюме {note}…", parse_mode="HTML")
        filename, page, letter, content, slug = await asyncio.to_thread(
            generate, v, jd)

        await status_msg.edit_text(
            f"⏳ <b>{title}</b>\nПубликую страницу…", parse_mode="HTML")
        url = await asyncio.to_thread(
            publish, filename, page, f"resume: {title} ({v['company'] or '-'})")

        db.set_status(uid, "generated")

        vac_line = f'\n🔗 Вакансия: {v["url"]}' if v["url"] else ""
        await status_msg.edit_text(
            f"✅ <b>{title}</b>\n"
            f"{v['company'] or '—'} · {v['salary_raw'] or 'з/п не указана'}\n\n"
            f"📄 Резюме: {url}"
            f"{vac_line}\n\n"
            f"<i>Открой вакансию, откликнись там и приложи ссылку на резюме "
            f"вместе с письмом ниже. Если страница не открылась — подожди минуту, "
            f"GitHub Pages пересобирается.</i>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

        # PDF — локальный рендеринг, запросов к модели не делает
        try:
            from pdf_builder import build_pdf, pdf_filename
            import os
            pdf_path = os.path.join("/tmp", pdf_filename(slug))
            await asyncio.to_thread(build_pdf, pdf_path, title,
                                    v["company"] or "", content, url)
            await bot.send_document(
                OWNER_ID, FSInputFile(pdf_path),
                caption=f"PDF-версия — {title}")
            os.remove(pdf_path)
        except Exception as e:
            log.exception("PDF не собрался")
            await bot.send_message(
                OWNER_ID, f"⚠️ PDF не собрался: {str(e)[:200]}\nСсылка выше рабочая.")

        if letter:
            tail = f'\n\n📄 Резюме: {url}'
            if v["url"]:
                tail += f'\n🔗 Вакансия: {v["url"]}'
            await bot.send_message(
                OWNER_ID,
                f"<b>Сопроводительное — {title}</b>\n\n{letter}{tail}",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    except Exception as e:
        log.exception("генерация упала для %s", uid)
        db.set_status(uid, "sent")
        await status_msg.edit_text(
            f"❌ <b>{title}</b>\nНе получилось: {str(e)[:250]}",
            parse_mode="HTML",
            reply_markup=retry_kb(uid),
        )


# ---------------- хендлеры ----------------

@dp.callback_query(F.data.startswith("pick:"))
async def on_pick(cb: CallbackQuery):
    uid = cb.data.split(":", 1)[1]
    db.set_status(uid, "selected")
    await cb.answer("Готовлю резюме")
    msg = await cb.message.edit_text("⏳ Начинаю…")
    asyncio.create_task(run_generation(uid, msg))


@dp.callback_query(F.data.startswith("skip:"))
async def on_skip(cb: CallbackQuery):
    uid = cb.data.split(":", 1)[1]
    db.set_status(uid, "skipped")
    v = db.get_vacancy(uid)
    await cb.message.edit_text(
        card_text(v) + "\n\n✖️ <i>Пропущено</i>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await cb.answer("Скип")


@dp.message(Command("queue"))
async def cmd_queue(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    rows = db.list_by_status("generated")
    if not rows:
        await m.answer("Пока ничего не сгенерировано.")
        return
    text = "\n".join(
        f"{i}. {r['title']} — {r['company'] or '—'}"
        for i, r in enumerate(rows, 1)
    )
    await m.answer(f"<b>Готовые резюме ({len(rows)}):</b>\n{text}", parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    by_status, posts = db.stats()
    lines = [f"Обработано постов: {posts}"]
    for k, v in by_status.items():
        lines.append(f"{k}: {v}")
    await m.answer("\n".join(lines))


@dp.message(Command("start"))
async def cmd_start(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    await m.answer(
        "Слежу за каналом с вакансиями.\n"
        "Подходящие присылаю карточкой. Жмёшь «Откликнуться» — собираю "
        "резюме под эту вакансию, публикую и присылаю ссылку с сопроводительным.\n\n"
        "/queue — готовые резюме\n"
        "/stats — статистика"
    )
