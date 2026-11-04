import asyncio
import csv
import logging
import os
import random
import sqlite3
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
from dotenv import load_dotenv

import config

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
_gid = os.environ["GROUP_ID"].strip()            # @username публичной группы или числовой id (-100...)
GROUP_ID = int(_gid) if _gid.lstrip("-").isdigit() else _gid
GROUP_LINK = os.environ["GROUP_LINK"]           # https://t.me/+xxxx или https://t.me/name
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
DB_PATH = os.environ.get("DB_PATH", "giveaway.db")

BASE = Path(__file__).parent
GIF_DIR = BASE / "gifs"

router = Router()


# ---------- БД ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS guests (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                phone TEXT,
                birthday TEXT,
                prize TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                spun_at TEXT
            )"""
        )


def get_guest(user_id):
    with db() as c:
        return c.execute("SELECT * FROM guests WHERE user_id=?", (user_id,)).fetchone()


# ---------- Состояния анкеты ----------
class Form(StatesGroup):
    name = State()
    phone = State()
    birthday = State()


def subscribe_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Вступить в группу", url=GROUP_LINK)],
            [InlineKeyboardButton(text="✅ Я вступил(а)", callback_data="check_sub")],
        ]
    )


def spin_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎡 Крутить колесо", callback_data="spin")]]
    )


async def is_member(bot: Bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(GROUP_ID, user_id)
    except Exception as e:  # бот не админ в группе / неверный id
        logging.error("Не удалось проверить подписку: %s", e)
        return False
    return m.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    )


# ---------- Хэндлеры ----------
@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    g = get_guest(message.from_user.id)
    if g and g["prize"]:
        await message.answer(
            f"Вы уже участвовали в розыгрыше 🎉\nВаш приз: <b>{g['prize']}</b>\n"
            "Покажите это сообщение сотруднику."
        )
        return
    if g and g["phone"] and g["birthday"]:
        await ask_subscription(message)
        return
    await message.answer(config.WELCOME_TEXT)
    await state.set_state(Form.name)


@router.message(Form.name, F.text)
async def got_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip()[:100])
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Отправьте ваш номер телефона кнопкой ниже 👇", reply_markup=kb)
    await state.set_state(Form.phone)


@router.message(Form.phone)
async def got_phone(message: Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
    elif message.text and sum(ch.isdigit() for ch in message.text) >= 9:
        phone = message.text.strip()
    else:
        await message.answer("Нажмите кнопку «Отправить номер» или напишите номер цифрами.")
        return
    await state.update_data(phone=phone)
    await message.answer(
        "Дата рождения (ДД.ММ.ГГГГ) — пришлём подарок ко дню рождения 🎂",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Form.birthday)


@router.message(Form.birthday, F.text)
async def got_birthday(message: Message, state: FSMContext):
    data = await state.get_data()
    u = message.from_user
    with db() as c:
        c.execute(
            """INSERT INTO guests (user_id, username, name, phone, birthday)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username, name=excluded.name,
                 phone=excluded.phone, birthday=excluded.birthday""",
            (u.id, u.username, data["name"], data["phone"], message.text.strip()[:20]),
        )
    await state.clear()
    await ask_subscription(message)


async def ask_subscription(message: Message):
    await message.answer(config.SUBSCRIBE_TEXT, reply_markup=subscribe_kb())


@router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery, bot: Bot):
    if await is_member(bot, call.from_user.id):
        await call.message.answer(config.READY_TEXT, reply_markup=spin_kb())
        await call.answer()
    else:
        await call.answer("Вы ещё не в группе. Вступите и нажмите кнопку снова.", show_alert=True)


@router.callback_query(F.data == "spin")
async def spin(call: CallbackQuery, bot: Bot):
    uid = call.from_user.id
    g = get_guest(uid)
    if not g:
        await call.answer("Сначала нажмите /start", show_alert=True)
        return
    if g["prize"]:
        await call.answer("Вы уже крутили колесо 🙂", show_alert=True)
        return
    if not await is_member(bot, uid):
        await call.answer("Сначала вступите в группу", show_alert=True)
        return

    prizes = config.PRIZES
    idx = random.choices(range(len(prizes)), weights=[p["weight"] for p in prizes])[0]
    prize = prizes[idx]["name"]

    # атомарно фиксируем приз, чтобы нельзя было крутить дважды
    with db() as c:
        cur = c.execute(
            "UPDATE guests SET prize=?, spun_at=CURRENT_TIMESTAMP WHERE user_id=? AND prize IS NULL",
            (prize, uid),
        )
        if cur.rowcount == 0:
            await call.answer("Вы уже крутили колесо 🙂", show_alert=True)
            return

    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_animation(uid, FSInputFile(GIF_DIR / f"spin_{idx}.gif"))
    await asyncio.sleep(config.GIF_SECONDS)
    await bot.send_message(uid, config.WIN_TEXT.format(prize=prize))


# ---------- Админ ----------
@router.message(Command("export"))
async def export(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with db() as c:
        rows = c.execute("SELECT * FROM guests ORDER BY created_at").fetchall()
    path = BASE / "guests.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(rows[0].keys() if rows else ["пусто"])
        for r in rows:
            w.writerow(list(r))
    await message.answer_document(FSInputFile(path), caption=f"Участников: {len(rows)}")


@router.message(Command("stats"))
async def stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with db() as c:
        total = c.execute("SELECT COUNT(*) FROM guests").fetchone()[0]
        spun = c.execute("SELECT COUNT(*) FROM guests WHERE prize IS NOT NULL").fetchone()[0]
        by_prize = c.execute(
            "SELECT prize, COUNT(*) n FROM guests WHERE prize IS NOT NULL GROUP BY prize"
        ).fetchall()
    text = f"Анкет: {total}\nПрокруток: {spun}\n\n" + "\n".join(f"{r['prize']}: {r['n']}" for r in by_prize)
    await message.answer(text)


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
