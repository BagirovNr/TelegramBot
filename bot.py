import asyncio
import csv
import logging
import os
import random
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiohttp import web
from dotenv import load_dotenv

import config

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
_gid = os.environ["GROUP_ID"].strip()            # @username публичной группы или числовой id (-100...)
GROUP_ID = int(_gid) if _gid.lstrip("-").isdigit() else _gid
GROUP_LINK = os.environ["GROUP_LINK"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
# Кому присылать данные каждого нового участника (id через запятую). Если не задано — админам.
NOTIFY_IDS = {int(x) for x in os.environ.get("NOTIFY_IDS", "").split(",") if x.strip()} or ADMIN_IDS
DB_PATH = os.environ.get("DB_PATH", "giveaway.db")

BASE = Path(__file__).parent
WHEEL_GIF = BASE / "gifs" / "draw.gif"
rng = random.SystemRandom()
pending_draw: dict[int, str] = {}   # admin_id -> текст приза до подтверждения

router = Router()


# ---------- БД ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS participants (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                phone TEXT,
                joined INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS winners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prize TEXT,
                drawn_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )


def get_p(user_id):
    with db() as c:
        return c.execute("SELECT * FROM participants WHERE user_id=?", (user_id,)).fetchone()


def contact_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=config.CONTACT_BUTTON, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def consent_text():
    text = config.CONSENT_TEXT
    if config.PRIVACY_URL:
        text += f'\n<a href="{config.PRIVACY_URL}">Политика обработки персональных данных</a>'
    return text


def subscribe_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Вступить в группу", url=GROUP_LINK)],
            [InlineKeyboardButton(text="✅ Я вступил(а)", callback_data="check_sub")],
        ]
    )


async def is_member(bot: Bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(GROUP_ID, user_id)
    except Exception as e:
        logging.error("Не удалось проверить подписку %s: %s", user_id, e)
        return False
    return m.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    )


def mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "••••" + digits[-4:] if len(digits) >= 4 else "••••"


# ---------- Гость ----------
@router.message(CommandStart())
async def start(message: Message, bot: Bot):
    p = get_p(message.from_user.id)
    if p and p["phone"]:
        if p["joined"] or await is_member(bot, message.from_user.id):
            await message.answer(config.ALREADY_TEXT)
        else:
            await message.answer(config.SUBSCRIBE_TEXT, reply_markup=subscribe_kb())
        return
    await message.answer(config.WELCOME_TEXT + "\n\n" + consent_text(), reply_markup=contact_kb(),
                         disable_web_page_preview=True)


@router.message(F.contact)
async def got_contact(message: Message, bot: Bot):
    ct, u = message.contact, message.from_user
    if ct.user_id is not None and ct.user_id != u.id:      # чужой контакт не принимаем
        await message.answer(config.CONTACT_RETRY, reply_markup=contact_kb())
        return
    name = " ".join(x for x in (ct.first_name, ct.last_name) if x).strip() or u.full_name
    prev = get_p(u.id)
    is_new = not (prev and prev["phone"])
    with db() as c:
        c.execute(
            """INSERT INTO participants (user_id, username, name, phone)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username, name=excluded.name, phone=excluded.phone""",
            (u.id, u.username, name[:100], ct.phone_number),
        )
    if is_new:
        with db() as c:
            total = c.execute("SELECT COUNT(*) FROM participants WHERE phone IS NOT NULL").fetchone()[0]
        tg = f"@{u.username}" if u.username else "нет username"
        note = (f"🆕 Новый участник №{total}\nИмя: {name}\nТелефон: {ct.phone_number}\n"
                f"Telegram: {tg}\nID: {u.id}")
        for admin_id in NOTIFY_IDS:
            try:
                await bot.send_message(admin_id, note)
            except Exception as e:      # человек ещё не нажимал «Старт» у бота
                logging.warning("Не удалось отправить данные %s: %s", admin_id, e)
    await message.answer("Спасибо! 🍫", reply_markup=ReplyKeyboardRemove())
    await message.answer(config.SUBSCRIBE_TEXT, reply_markup=subscribe_kb())


@router.message(F.text, ~F.text.startswith("/"))
async def other_text(message: Message):
    p = get_p(message.from_user.id)
    if not p or not p["phone"]:
        await message.answer(config.CONTACT_RETRY, reply_markup=contact_kb())


@router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery, bot: Bot):
    if not get_p(call.from_user.id):
        await call.answer("Сначала нажмите /start", show_alert=True)
        return
    if await is_member(bot, call.from_user.id):
        with db() as c:
            c.execute("UPDATE participants SET joined=1 WHERE user_id=?", (call.from_user.id,))
        await call.message.answer(config.JOINED_TEXT)
        await call.answer()
    else:
        await call.answer("Вы ещё не в группе. Вступите и нажмите кнопку снова.", show_alert=True)


# ---------- Админ ----------
def admin_only(message: Message) -> bool:
    return message.from_user.id in ADMIN_IDS


@router.message(Command("draw"))
async def draw_cmd(message: Message, command: CommandObject):
    if not admin_only(message):
        return
    prize = (command.args or "").strip() or config.WINNER_NO_PRIZE
    with db() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM participants WHERE phone IS NOT NULL "
            "AND user_id NOT IN (SELECT user_id FROM winners)"
        ).fetchone()[0]
    pending_draw[message.from_user.id] = prize
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🎡 Провести розыгрыш", callback_data="draw_go"),
            InlineKeyboardButton(text="Отмена", callback_data="draw_cancel"),
        ]]
    )
    await message.answer(
        f"Участников в базе: {n}\nПриз: <b>{prize}</b>\n\n"
        "Колесо и объявление уйдут в группу. Проводим?",
        reply_markup=kb,
    )


@router.callback_query(F.data == "draw_cancel")
async def draw_cancel(call: CallbackQuery):
    pending_draw.pop(call.from_user.id, None)
    await call.message.edit_text("Розыгрыш отменён.")
    await call.answer()


@router.callback_query(F.data == "draw_go")
async def draw_go(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer()
        return
    prize = pending_draw.pop(call.from_user.id, None)
    if prize is None:
        await call.answer("Запрос устарел, отправьте /draw снова", show_alert=True)
        return
    await call.message.edit_text("Идёт розыгрыш…")
    await call.answer()

    with db() as c:
        rows = c.execute(
            "SELECT * FROM participants WHERE phone IS NOT NULL "
            "AND user_id NOT IN (SELECT user_id FROM winners)"
        ).fetchall()
    rows = list(rows)
    rng.shuffle(rows)
    winner = None
    for r in rows:                      # победитель должен быть в группе прямо сейчас
        if await is_member(bot, r["user_id"]):
            winner = r
            break
        await asyncio.sleep(0.05)
    if not winner:
        await call.message.answer("Нет подходящих участников (никто не состоит в группе).")
        return

    with db() as c:
        c.execute("INSERT INTO winners (user_id, prize) VALUES (?,?)", (winner["user_id"], prize))

    await bot.send_message(GROUP_ID, config.DRAW_INTRO)
    await bot.send_animation(GROUP_ID, FSInputFile(WHEEL_GIF))
    await asyncio.sleep(config.GIF_SECONDS)
    await bot.send_message(
        GROUP_ID,
        config.WINNER_GROUP_TEXT.format(
            name=winner["name"], phone=mask_phone(winner["phone"]), prize=prize
        ),
    )
    try:
        await bot.send_message(
            winner["user_id"], config.WINNER_DM_TEXT.format(name=winner["name"], prize=prize)
        )
        dm = "Победителю отправлено личное сообщение."
    except Exception:
        dm = "Не удалось написать победителю в личку — свяжитесь по телефону."
    username = f"@{winner['username']}" if winner["username"] else "нет username"
    await call.message.answer(
        f"🏆 Победитель: <b>{winner['name']}</b>\nТелефон: {winner['phone']}\n"
        f"Telegram: {username}\nПриз: {prize}\n\n{dm}"
    )


@router.message(Command("stats"))
async def stats(message: Message):
    if not admin_only(message):
        return
    with db() as c:
        total = c.execute("SELECT COUNT(*) FROM participants WHERE phone IS NOT NULL").fetchone()[0]
        joined = c.execute("SELECT COUNT(*) FROM participants WHERE joined=1").fetchone()[0]
        wins = c.execute("SELECT COUNT(*) FROM winners").fetchone()[0]
    await message.answer(f"Оставили данные: {total}\nВступили в группу: {joined}\nПобедителей уже выбрано: {wins}")


@router.message(Command("export"))
async def export(message: Message):
    if not admin_only(message):
        return
    with db() as c:
        rows = c.execute(
            "SELECT p.user_id, p.username, p.name, p.phone, p.joined, p.created_at, "
            "CASE WHEN w.user_id IS NULL THEN '' ELSE w.prize END AS prize "
            "FROM participants p LEFT JOIN winners w ON w.user_id=p.user_id ORDER BY p.created_at"
        ).fetchall()
    path = BASE / "guests.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["user_id", "username", "имя", "телефон", "в группе", "дата", "выигранный приз"])
        for r in rows:
            w.writerow(list(r))
    await message.answer_document(FSInputFile(path), caption=f"Участников: {len(rows)}")


async def start_health_server():
    """Мини-сервер для хостингов (Render): отвечает «ok» на запросы пинга."""
    port = int(os.environ.get("PORT", "0"))
    if not port:
        return
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("Health server on port %s", port)


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await start_health_server()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.set_my_description(config.DESCRIPTION)
        await bot.set_my_short_description(config.SHORT_DESCRIPTION)
    except Exception as e:
        logging.warning("Не удалось обновить описание бота: %s", e)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
