"""
vh shop bot v3 — Telegram магазин аккаунтов (Gmail / Discord) + Telegram Stars / Premium
aiogram >= 3.27 + SQLite.

• ручная пошаговая выдача (заявки админу: номер → код, аккаунт → пароль ...)
• ВСЕ тексты, названия кнопок, фото/GIF/видео редактируются из админ-панели
• премиум-эмодзи и форматирование Telegram в текстах и кнопках
• кнопка поддержки перекидывает на владельца
"""
import asyncio
import html
import io
import json
import logging
import math
import os
import re
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import aiosqlite
import aiohttp
from urllib.parse import quote
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNERS = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x}
CUR = "$"  # Все внутренние суммы и цены магазина отображаются только в USD.
SUPPORT_ENV = os.getenv("SUPPORT_USERNAME", "").lstrip("@")
MIN_TOPUP = float(os.getenv("MIN_TOPUP_UAH", os.getenv("MIN_TOPUP", "50")))  # Минимум для ручного пополнения в гривне.
MIN_TOPUP_USD = float(os.getenv("MIN_TOPUP_USD", "1"))
DB = os.getenv("DB_PATH", "vh.db")
SHOP = "vh"


# Цены и страны каталога Telegram; порядок соответствует заданному прайс-листу.
TELEGRAM_COUNTRY_DATA = r"""
Индия|0.75|🇮🇳
Колумбия|0.44|🇨🇴
Нигерия|0.44|🇳🇬
Кения|0.56|🇰🇪
Индонезия|0.56|🇮🇩
Иран|0.62|🇮🇷
Афганистан|0.65|🇦🇫
Бангладеш|0.70|🇧🇩
Марокко|0.71|🇲🇦
Пакистан|0.75|🇵🇰
Египет|0.75|🇪🇬
Нигер|0.75|🇳🇪
Мадагаскар|0.75|🇲🇬
Уганда|0.77|🇺🇬
Судан|0.78|🇸🇩
Ямайка|0.80|🇯🇲
Алжир|0.81|🇩🇿
Бразилия|0.84|🇧🇷
Буркина-Фасо|0.86|🇧🇫
Непал|0.89|🇳🇵
Мексика|0.89|🇲🇽
Вьетнам|0.90|🇻🇳
Доминиканская Республика|0.92|🇩🇴
Сенегал|0.93|🇸🇳
Таиланд|0.95|🇹🇭
Гондурас|0.98|🇭🇳
США|0.99|🇺🇸
Узбекистан|0.99|🇺🇿
Гана|0.99|🇬🇭
Зимбабве|1.00|🇿🇼
Эквадор|1.04|🇪🇨
Уругвай|1.13|🇺🇾
Аргентина|1.14|🇦🇷
Израиль|1.14|🇮🇱
Армения|1.16|🇦🇲
Сомали|1.19|🇸🇴
Саудовская Аравия|1.20|🇸🇦
Австрия|1.23|🇦🇹
Япония|1.26|🇯🇵
Парагвай|1.34|🇵🇾
Венесуэла|1.38|🇻🇪
Финляндия|1.44|🇫🇮
Казахстан|1.46|🇰🇿
Киргизия|1.46|🇰🇬
Турция|1.47|🇹🇷
Сербия|1.50|🇷🇸
Бермуды|1.50|🇧🇲
Португалия|1.50|🇵🇹
Румыния|1.70|🇷🇴
Италия|1.73|🇮🇹
Франция|1.74|🇫🇷
Ирландия|1.79|🇮🇪
Испания|1.89|🇪🇸
Греция|1.89|🇬🇷
Грузия|1.89|🇬🇪
Азербайджан|1.91|🇦🇿
Гонконг|1.95|🇭🇰
Молдавия|1.95|🇲🇩
Словакия|1.95|🇸🇰
Германия|2.04|🇩🇪
Бельгия|2.06|🇧🇪
Кипр|2.25|🇨🇾
Литва|2.36|🇱🇹
Люксембург|2.40|🇱🇺
Чехия|2.51|🇨🇿
Австралия|2.55|🇦🇺
Тайвань|2.64|🇹🇼
Швеция|2.75|🇸🇪
ОАЭ|2.84|🇦🇪
Новая Зеландия|2.93|🇳🇿
Норвегия|2.96|🇳🇴
Катар|3.05|🇶🇦
Швейцария|3.17|🇨🇭
Дания|3.53|🇩🇰
Республика Корея|4.34|🇰🇷
Мьянма|0.68|🇲🇲
Чили|0.68|🇨🇱
Ангола|0.72|🇦🇴
Йемен|0.75|🇾🇪
Мартиника|0.83|🇲🇶
Танзания|0.83|🇹🇿
Республика Конго|0.83|🇨🇬
Либерия|0.83|🇱🇷
Шри-Ланка|0.90|🇱🇰
Польша|0.90|🇵🇱
Эсватини|0.90|🇸🇿
Пуэрто-Рико|0.90|🇵🇷
Куба|0.98|🇨🇺
Перу|0.98|🇵🇪
Коста-Рика|0.98|🇨🇷
Никарагуа|0.98|🇳🇮
Гернси|0.98|🇬🇬
Джерси|0.98|🇯🇪
Ботсвана|0.98|🇧🇼
Лесото|0.98|🇱🇸
Конго|0.98|🇨🇩
Чад|0.98|🇹🇩
Маврикий|0.98|🇲🇺
Гвинея|0.98|🇬🇳
КНР (Китай)|1.05|🇨🇳
Малайзия|1.05|🇲🇾
Великобритания|1.05|🇬🇧
Намибия|1.05|🇳🇦
Мали|1.05|🇲🇱
Туркмения|1.13|🇹🇲
Камбоджа|1.13|🇰🇭
Гаити|1.13|🇭🇹
Гватемала|1.13|🇬🇹
Хорватия|1.13|🇭🇷
Экваториальная Гвинея|1.13|🇬🇶
Кабо-Верде|1.13|🇨🇻
Гамбия|1.13|🇬🇲
Британские Виргинские острова|1.13|🇻🇬
Ангилья|1.13|🇦🇮
Кюрасао|1.20|🇨🇼
Коморы|1.20|🇰🇲
Джибути|1.20|🇩🇯
Тунис|1.20|🇹🇳
Сент-Китс и Невис|1.20|🇰🇳
Тринидад и Тобаго|1.20|🇹🇹
Гуам|1.20|🇬🇺
Гренада|1.20|🇬🇩
Ливан|1.28|🇱🇧
Французская Полинезия|1.28|🇵🇫
Соломоновы Острова|1.28|🇸🇧
Тонга|1.28|🇹🇴
Гайана|1.28|🇬🇾
Сальвадор|1.28|🇸🇻
Реюньон|1.28|🇷🇪
Майотта|1.28|🇾🇹
Кот-д’Ивуар|1.28|🇨🇮
Антигуа и Барбуда|1.28|🇦🇬
Багамы|1.28|🇧🇸
Сирия|1.35|🇸🇾
Восточный Тимор|1.35|🇹🇱
Государство Палестина|1.50|🇵🇸
Самоа|1.50|🇼🇸
Фиджи|1.50|🇫🇯
Вануату|1.50|🇻🇺
Суринам|1.50|🇸🇷
Панама|1.50|🇵🇦
Северная Македония|1.50|🇲🇰
Гренландия|1.50|🇬🇱
Малави|1.50|🇲🇼
Сейшельские Острова|1.50|🇸🇨
Сан-Томе и Принсипи|1.50|🇸🇹
Ливия|1.50|🇱🇾
Сент-Винсент и Гренадины|1.50|🇻🇨
Доминика|1.50|🇩🇲
Сент-Люсия|1.50|🇱🇨
Синт-Мартен|1.50|🇸🇽
Иордания|1.65|🇯🇴
Остров Мэн|1.65|🇮🇲
Теркс и Кайкос|1.65|🇹🇨
Мальдивы|1.80|🇲🇻
Гваделупа|1.80|🇬🇵
Босния и Герцеговина|1.80|🇧🇦
Нидерланды|1.80|🇳🇱
Монголия|1.95|🇲🇳
Мальта|1.95|🇲🇹
Албания|1.95|🇦🇱
Аруба|1.95|🇦🇼
Южный Судан|1.95|🇸🇸
Косово|2.10|🇽🇰
Бутан|2.25|🇧🇹
Оман|2.25|🇴🇲
Новая Каледония|2.25|🇳🇨
Гвиана|2.25|🇬🇫
Боливия|2.25|🇧🇴
Американское Самоа|2.25|🇦🇸
Острова Кайман|2.25|🇰🇾
Американские Виргинские острова|2.25|🇻🇮
Кувейт|2.63|🇰🇼
Макао|2.63|🇲🇴
Украина|3.00|🇺🇦
Ирак|3.15|🇮🇶
Бахрейн|4.20|🇧🇭
Ниуэ|4.50|🇳🇺
Бруней|4.50|🇧🇳
Сингапур|4.50|🇸🇬
"""
TELEGRAM_COUNTRY_PRODUCTS = [
    (f"{flag} {name}", float(price), name)
    for name, price, flag in (line.split("|") for line in TELEGRAM_COUNTRY_DATA.splitlines() if line.strip())
]

COUNTRY_NAME_ALIASES = {
    "індія": "индия", "австралія": "австралия", "австрія": "австрия",
    "вірменія": "армения", "афганістан": "афганистан", "бельгія": "бельгия",
    "бразилія": "бразилия", "буркіна-фасо": "буркина-фасо", "венесуела": "венесуэла",
    "вʼєтнам": "вьетнам", "в'єтнам": "вьетнам", "німеччина": "германия",
    "греція": "греция", "азербайджан": "азербайджан", "молдова": "молдавия",
    "словаччина": "словакия", "киргизстан": "киргизия",
    "виргинские острова": "британские виргинские острова",
}


def country_product_key(title):
    """Ключ названия без флага для сопоставления со старыми товарами."""
    title = re.sub(r"^[^\wА-Яа-яЁё]+", "", (title or "").strip(), flags=re.UNICODE)
    key = re.sub(r"\s+", " ", title).casefold().strip()
    return COUNTRY_NAME_ALIASES.get(key, key)


def conn():
    return aiosqlite.connect(DB, timeout=30)

# ───────────────────────── DB ─────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, lang TEXT,
  balance REAL DEFAULT 0, created TEXT, banned INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS roles(user_id INTEGER PRIMARY KEY, role TEXT);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, title TEXT, link TEXT);
CREATE TABLE IF NOT EXISTS requisites(id INTEGER PRIMARY KEY AUTOINCREMENT, bank TEXT, card TEXT, holder TEXT,
  emoji_id TEXT);
CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, emoji_id TEXT,
  mtype TEXT, mfile TEXT);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, cat_id INTEGER, title TEXT,
  price REAL, active INTEGER DEFAULT 1, emoji_id TEXT, mtype TEXT, mfile TEXT, descr TEXT, steps TEXT, stock_qty INTEGER DEFAULT -1);
CREATE TABLE IF NOT EXISTS stock(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, data TEXT,
  sold INTEGER DEFAULT 0, login TEXT, pw TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER,
  title TEXT, qty INTEGER, total REAL, items TEXT, created TEXT,
  status TEXT DEFAULT 'done', step INTEGER DEFAULT 0, pending INTEGER DEFAULT 0, claimed_by INTEGER, steps TEXT,
  kind TEXT DEFAULT 'steps', last_req INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
  req_id INTEGER, status TEXT, file_id TEXT, created TEXT, currency TEXT DEFAULT 'UAH', paid_amount REAL);
CREATE TABLE IF NOT EXISTS payment_intents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, method TEXT NOT NULL,
  usd_amount REAL NOT NULL, expected_amount REAL NOT NULL, asset TEXT NOT NULL,
  external_id TEXT, pay_url TEXT, payload TEXT, status TEXT DEFAULT 'pending',
  tx_hash TEXT, created TEXT, paid_at TEXT
);
CREATE TABLE IF NOT EXISTS ui_text(key TEXT, lang TEXT, html TEXT, mtype TEXT, mfile TEXT, PRIMARY KEY(key,lang));
CREATE TABLE IF NOT EXISTS ui_btn(key TEXT, lang TEXT, label TEXT, emoji_id TEXT, style TEXT, PRIMARY KEY(key,lang));
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS premium_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, months INTEGER, price REAL,
  active INTEGER DEFAULT 1);
"""
MIGRATIONS = [  # для баз, созданных старой версией
    "ALTER TABLE requisites ADD COLUMN emoji_id TEXT",
    "ALTER TABLE categories ADD COLUMN emoji_id TEXT", "ALTER TABLE categories ADD COLUMN mtype TEXT",
    "ALTER TABLE categories ADD COLUMN mfile TEXT",
    "ALTER TABLE products ADD COLUMN emoji_id TEXT", "ALTER TABLE products ADD COLUMN mtype TEXT",
    "ALTER TABLE products ADD COLUMN mfile TEXT", "ALTER TABLE products ADD COLUMN descr TEXT",
    "ALTER TABLE products ADD COLUMN steps TEXT", "ALTER TABLE products ADD COLUMN stock_qty INTEGER DEFAULT -1",
    "ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'done'", "ALTER TABLE orders ADD COLUMN step INTEGER DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN pending INTEGER DEFAULT 0", "ALTER TABLE orders ADD COLUMN claimed_by INTEGER",
    "ALTER TABLE orders ADD COLUMN steps TEXT",
    "ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0",
    "ALTER TABLE stock ADD COLUMN login TEXT", "ALTER TABLE stock ADD COLUMN pw TEXT",
    "ALTER TABLE stock ADD COLUMN mode TEXT",
    "ALTER TABLE orders ADD COLUMN kind TEXT DEFAULT 'steps'",
    "ALTER TABLE orders ADD COLUMN last_req INTEGER DEFAULT 0",
    "ALTER TABLE deposits ADD COLUMN currency TEXT DEFAULT 'UAH'",
    "ALTER TABLE deposits ADD COLUMN paid_amount REAL",
]


async def init_db():
    async with conn() as c:
        tables_before = {r[0] for r in await (await c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()}
        had_existing_store = "users" in tables_before and "products" in tables_before and "settings" in tables_before
        await c.execute("PRAGMA journal_mode=WAL")
        await c.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                await c.execute(sql)
            except Exception:
                pass

        boot = await (await c.execute("SELECT v FROM settings WHERE k='currency_bootstrap_done'")).fetchone()
        if not boot:
            if had_existing_store:
                counts = []
                for table in ("users", "products", "orders", "deposits", "premium_plans"):
                    try:
                        row = await (await c.execute(f"SELECT COUNT(*) FROM {table}")).fetchone()
                        counts.append(int(row[0]))
                    except Exception:
                        pass
                if any(counts):
                    await c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('legacy_uah_pending','1')")
            await c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('currency_bootstrap_done','1')")

        seeded = await (await c.execute("SELECT v FROM settings WHERE k='plans_seeded'")).fetchone()
        if not seeded:
            await c.executemany("INSERT INTO premium_plans(months,price) VALUES(?,?)", [(3, 16.0), (6, 21.0), (12, 35.0)])
            await c.execute("INSERT INTO settings(k,v) VALUES('plans_seeded','1')")

        n = await (await c.execute("SELECT COUNT(*) FROM categories")).fetchone()
        if n[0] == 0:
            await c.executemany("INSERT INTO categories(name) VALUES(?)", [("📧 Gmail",), ("🎮 Discord",)])

        # Нормализуем название категории, в том числе в существующей базе.
        await c.execute("UPDATE categories SET name='📱 Telegram аккаунты' WHERE name IN ('📱 Telegram акаунти','Telegram акаунти')")
        catrow = await (await c.execute(
            "SELECT id FROM categories WHERE lower(name) LIKE '%telegram%' OR lower(name) LIKE '%телеграм%' LIMIT 1"
        )).fetchone()
        if not catrow:
            cur = await c.execute("INSERT INTO categories(name) VALUES(?)", ("📱 Telegram аккаунты",))
            tg_cat_id = cur.lastrowid
        else:
            tg_cat_id = int(catrow[0])

        # Каталоги VK и TikTok — как Gmail и Discord (создаются, если их ещё нет).
        have = {acc_platform(r_[0]) for r_ in await (await c.execute("SELECT name FROM categories")).fetchall()}
        for key_, name_ in (("vk", "🔵 VK"), ("tiktok", "🔵 TikTok")):
            if key_ not in have:
                await c.execute("INSERT INTO categories(name) VALUES(?)", (name_,))

        defaults = {
            "uah_per_usd": "0",  # 0 disables UAH payments until the admin sets a rate.
            "ton_usd": "0",      # 0 disables TON payments until the admin sets a rate.
        }
        for key, value in defaults.items():
            await c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)", (key, value))

        # Сверяем каталог со списком: существующие товары обновляем на месте,
        # чтобы сохранить связанные складские позиции и историю заказов.
        existing = await (await c.execute(
            "SELECT id, title FROM products WHERE cat_id=?", (tg_cat_id,)
        )).fetchall()
        existing_by_key = {}
        for row in existing:
            existing_by_key.setdefault(country_product_key(row[1]), row)

        # Старое общее название Виргинских островов сопоставляется с первой позицией.
        country_ids = []
        active_target_ids = set()
        for title, price, plain_name in TELEGRAM_COUNTRY_PRODUCTS:
            key = country_product_key(title)
            old = existing_by_key.get(key)
            if old is not None:
                product_id = int(old[0])
                await c.execute(
                    "UPDATE products SET cat_id=?, title=?, price=?, active=1 WHERE id=?",
                    (tg_cat_id, title, price, product_id),
                )
            else:
                cur = await c.execute(
                    "INSERT INTO products(cat_id,title,price,active) VALUES(?,?,?,1)",
                    (tg_cat_id, title, price),
                )
                product_id = int(cur.lastrowid)
            country_ids.append(product_id)
            active_target_ids.add(product_id)

        # Старые страны, которых нет в новом прайсе, скрываем, но не удаляем:
        # это сохраняет историю заказов и складские данные для администратора.
        for row in existing:
            old_id = int(row[0])
            if old_id not in active_target_ids:
                await c.execute("UPDATE products SET active=0 WHERE id=?", (old_id,))

        # Этот список также используется при миграции валюты, чтобы цены стран
        # не пересчитывались как старые пользовательские цены. Сохраняем порядок прайса.
        await c.execute(
            "INSERT OR REPLACE INTO settings(k,v) VALUES('telegram_seed_product_ids',?)",
            (",".join(map(str, country_ids)),),
        )
        await c.commit()


async def q(sql, args=(), fetch=None):
    async with conn() as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(sql, args)
        if fetch == "one":
            return await cur.fetchone()
        if fetch == "all":
            return await cur.fetchall()
        await c.commit()
        return cur.lastrowid


async def qr(sql, args=()):
    """UPDATE/DELETE -> число затронутых строк"""
    async with conn() as c:
        cur = await c.execute(sql, args)
        await c.commit()
        return cur.rowcount


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def money(v):
    return f"${float(v):.2f}"


async def role_of(uid):
    if uid in OWNERS:
        return "owner"
    r = await q("SELECT role FROM roles WHERE user_id=?", (uid,), "one")
    return r["role"] if r else None


async def staff_ids():
    ids = set(OWNERS)
    for r_ in await q("SELECT user_id FROM roles", fetch="all"):
        ids.add(r_["user_id"])
    return ids


def plain_item(x):
    if x.get("login"):
        return x["login"] + (":" + x["pw"] if x.get("pw") else "")
    return x.get("data") or ""


async def buy_atomic(uid, pid, qty):
    async with conn() as c:
        c.row_factory = aiosqlite.Row
        await c.execute("BEGIN IMMEDIATE")
        p = await (await c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,))).fetchone()
        u = await (await c.execute("SELECT balance FROM users WHERE id=?", (uid,))).fetchone()
        if not p or not u:
            await c.rollback()
            return "stock", None
        total = calc_total(p["price"], qty)
        if u["balance"] < total:
            await c.rollback()
            return "balance", None
        steps = json.loads(p["steps"] or "[]")
        if steps:  # ручная выдача; остаток задаётся числом в админке
            if p["stock_qty"] >= 0:
                if p["stock_qty"] < qty:
                    await c.rollback()
                    return "stock", None
                await c.execute("UPDATE products SET stock_qty=stock_qty-? WHERE id=? AND stock_qty>=?", (qty, pid, qty))
            await c.execute("UPDATE users SET balance=ROUND(balance-?,2) WHERE id=?", (total, uid))
            cur = await c.execute(
                "INSERT INTO orders(user_id,product_id,title,qty,total,items,created,status,step,pending,steps)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid, pid, p["title"], qty, total, "", now(), "active", 0, 0, json.dumps(steps)))
            await c.commit()
            return "manual", (cur.lastrowid, total, p["title"], steps)
        rows = await (await c.execute(
            "SELECT id,data,login,pw,mode FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT ?",
            (pid, qty))).fetchall()
        if len(rows) < qty:
            await c.rollback()
            return "stock", None
        rows = [dict(x) for x in rows]
        is_code = any(x["mode"] == "code" for x in rows)
        if is_code and qty > 1:
            await c.rollback()
            return "codeqty", None
        await c.executemany("UPDATE stock SET sold=1 WHERE id=?", [(x["id"],) for x in rows])
        await c.execute("UPDATE users SET balance=ROUND(balance-?,2) WHERE id=?", (total, uid))
        items = "\n".join(plain_item(x) for x in rows)
        if is_code:  # номер + запрос кода у админа
            cur = await c.execute(
                "INSERT INTO orders(user_id,product_id,title,qty,total,items,created,status,step,pending,steps,kind)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, pid, p["title"], qty, total, items, now(), "active", 0, 0,
                 json.dumps([["Запрос кода", None]]), "code"))
            await c.commit()
            return "code", (cur.lastrowid, total, p["title"], rows)
        cur = await c.execute(
            "INSERT INTO orders(user_id,product_id,title,qty,total,items,created,status) VALUES(?,?,?,?,?,?,?,?)",
            (uid, pid, p["title"], qty, total, items, now(), "done"))
        await c.commit()
        return "auto", (cur.lastrowid, total, p["title"], rows)


# ───────────────────────── Тексти і кнопки за замовчуванням (редагуються з адмінки) ─────────────────────────
# ── Тексты с цитатами (blockquote), как в образце ──
_LINE = "━━━━━━━━━━━━━━━━━━"
_QLINE = "━━━━━━━━━━━━━━━"
TXT_COUNTRY_PROD = (
    "<b>{display_title} | Telegram-аккаунты</b>\n"
    "🌍 <b>Страна:</b> {country}\n"
    "💎 <b>Качество:</b> Премиум\n"
    "💰 <b>Цена за 1 шт.:</b> {price}\n"
    "📦 <b>В наличии:</b> {stock_display}\n\n"
    + _LINE + "\n\n"
    "📋 <b>Условия покупки</b>\n\n"
    "▫️ Выберите необходимое количество аккаунтов.\n"
    "▫️ Нажмите «Купить» и подтвердите заказ.\n"
    "▫️ После подтверждения станет доступна кнопка «Получить номер».\n\n"
    + _LINE + "\n\n"
    "<blockquote>🔐 <b>Способ входа в аккаунт</b>\n\n"
    "1. 📱 <b>Получение номера</b>\n"
    "Нажмите «Получить номер». Система подготовит номер для входа в течение 1 минуты.\n\n"
    "2. 🔑 <b>Авторизация</b>\n"
    "Введите предоставленный номер телефона в приложении Telegram.\n\n"
    "3. 💬 <b>Получение кода</b>\n\n"
    "Вернитесь в бота и нажмите «Получить код». Код будет предоставлен в течение 1 минуты.\n\n"
    "4. ✅ <b>Завершение входа</b>\n"
    "Введите полученный код в Telegram и завершите авторизацию.\n\n"
    + _QLINE + "\n\n"
    "⚠️ <b>Важные рекомендации</b>\n\n"
    "▫️ После входа дайте аккаунту некоторое время спокойно поработать, чтобы снизить риск потери доступа.\n"
    "▫️ Перед авторизацией рекомендуется включить запись экрана — она поможет подтвердить проблему при обращении в поддержку.\n"
    "▫️ Если возникнут проблемы со входом, обратитесь в поддержку и предоставьте запись экрана.\n"
    "▫️ <b>Возраст аккаунтов: 7+ дней.</b></blockquote>\n\n"
    "🛒 <b>Выберите количество аккаунтов для покупки</b>\n\n"
    "👇 Укажите нужное количество и переходите к оформлению заказа."
)
TXT_ORDER_PAID = (
    "✅ <b>Оплата успешно получена!</b>\n\n"
    "🧾 <b>Заказ №{id}</b>\n"
    "{title} × {qty}\n"
    "💰 <b>Сумма оплаты:</b> {total}\n\n"
    + _LINE + "\n\n"
    "<blockquote>📱 <b>Следующий шаг — получение номера</b>\n\n"
    "Нажмите кнопку «<b>Получить номер</b>» ниже. Система подготовит номер для входа в Telegram.\n\n"
    "⏳ Обычно это занимает до 1 минуты.\n\n"
    "⚠️ Пожалуйста, не закрывайте бота и дождитесь получения номера</blockquote>"
)
TXT_ORDER_NEXT = (
    "✅ <b>Номер получен! Заказ №{id}</b>\n\n"
    "📱 Следующий шаг — авторизация в Telegram.\n\n"
    "<blockquote>▫️ Откройте Telegram и введите полученный номер телефона.\n"
    "▫️ Вернитесь в бота и нажмите кнопку ниже.\n"
    "▫️ Дождитесь получения кода авторизации.\n\n"
    "⏳ Код будет подготовлен в течение 1 минуты.</blockquote>\n\n"
    "👇 <b>Нажмите «Получить код», когда будете готовы.</b>"
)

# key: (назва для адмінки, змінні, UA, RU)
DT = {
    "welcome": ("Главное меню", "—",
                "👋 Добро пожаловать в <b>" + SHOP + " shop</b>!\n\n▪️ Аккаунты Gmail, Discord и Telegram\n▪️ Telegram Stars та Premium\n▪️ Баланс и цены в USD\n▪️ Автоматическое крипто-пополнение\n\nВыберите раздел:",
                "👋 Приветствуем в <b>" + SHOP + " shop</b>!\n\n▪️ Аккаунты Gmail, Discord и Telegram\n▪️ Telegram Stars и Premium\n▪️ Баланс и цены в USD\n▪️ Крипто-пополнение\n\nВыберите раздел:"),
    "shop_title": ("Витрина: список категорий", "—",
                   "🛒 <b>Витрина товаров</b>\n\nВыберите категорию:", "🛒 <b>Витрина товаров</b>\n\nВыберите категорию:"),
    "cat_title": ("Категория: каталог (шапка)", "{name}",
                  "<b>{name}</b>\n\n<i><b>ВАЖЛИВО!</b> Найдешевші акаунти не завжди є в наявності. "
                  "Ми намагаємось поповнювати акаунти якнайшвидше.</i>",
                  "<b>{name}</b>\n\n<i><b>ВАЖНО!</b> Самые дешёвые аккаунты не всегда есть в наличии. "
                  "Мы стараемся пополнять аккаунты как можно быстрее.</i>"),
    "empty": ("Пустая категория / пустой список", "{name}",
              "<b>{name}</b>\n\nПоки що порожньо.", "<b>{name}</b>\n\nПока пусто."),
    "prod": ("Карточка товара", "{title} {price} {stock} {descr}",
             "<b>{title}</b>\n\n{descr}💰 Цена: <b>{price}</b>\n📦 В наличии: <b>{stock}</b>\n\nВыберите количество:",
             "<b>{title}</b>\n\n{descr}💰 Цена: <b>{price}</b>\n📦 В наличии: <b>{stock}</b>\n\nВыберите количество:"),
    "country_prod": ("Карточка страны: цена и наличие", "{display_title} {country} {price} {stock_display}",
                     TXT_COUNTRY_PROD, TXT_COUNTRY_PROD),
    "on_order": ("Слово «под заказ» (в карточке)", "—", "на замовлення", "под заказ"),
    "confirm": ("Подтверждение покупки", "{title} {qty} {price} {total}",
                "Підтвердіть покупку:\n\n<b>{title}</b>\nКількість: {qty}\nДо сплати: <b>{total}</b>",
                "Подтвердите покупку:\n\n<b>{title}</b>\nКоличество: {qty}\nК оплате: <b>{total}</b>"),
    "bought": ("Выдача со склада (текстом)", "{title} {qty} {total} {items}",
               "✅ <b>Покупка успішна!</b>\n\n{title} × {qty}\nСписано: {total}\n\nВаші дані:\n{items}",
               "✅ <b>Покупка успешна!</b>\n\n{title} × {qty}\nСписано: {total}\n\nВаши данные:\n{items}"),
    "bought_file": ("Выдача со склада (файлом)", "{title} {qty} {total}",
                    "✅ <b>Покупка успішна!</b>\n\n{title} × {qty}\nСписано: {total}\n\nДані у файлі нижче 👇",
                    "✅ <b>Покупка успешна!</b>\n\n{title} × {qty}\nСписано: {total}\n\nДанные в файле ниже 👇"),
    "order_paid": ("Заказ оплачен (ручная выдача)", "{id} {title} {qty} {total}",
                   TXT_ORDER_PAID, TXT_ORDER_PAID),
    "order_wait": ("Заявка отправлена админу", "{id}",
                   "⏳ Заявку по замовленню №{id} надіслано. Очікуйте — адміністратор скоро відповість.",
                   "⏳ Заявка по заказу №{id} отправлена. Ожидайте — администратор скоро ответит."),
    "order_next": ("Этап выдан, дальше кнопка", "{id}",
                   TXT_ORDER_NEXT, TXT_ORDER_NEXT),
    "order_done": ("Заказ выполнен", "{id}",
                   "🎉 Замовлення №{id} виконано. Дякуємо за покупку!",
                   "🎉 Заказ №{id} выполнен. Спасибо за покупку!"),
    "order_refund": ("Возврат средств", "{id} {total}",
                     "↩️ Замовлення №{id} скасовано, кошти повернуто на баланс: {total}",
                     "↩️ Заказ №{id} отменён, средства возвращены на баланс: {total}"),
    "bal_text": ("Экран баланса", "{bal}",
                 "💰 <b>Ваш баланс</b>\n\n💎 Основний: <b>{bal}</b>",
                 "💰 <b>Ваш баланс</b>\n\n💎 Основной: <b>{bal}</b>"),
    "prof_text": ("Профиль", "{id} {bal} {orders}",
                  "👤 <b>Профиль</b>\n\nID: <code>{id}</code>\nБаланс: <b>{bal}</b>\nПокупок: <b>{orders}</b>",
                  "👤 <b>Профиль</b>\n\nID: <code>{id}</code>\nБаланс: <b>{bal}</b>\nПокупок: <b>{orders}</b>"),
    "hist": ("История покупок", "{list}", "📖 <b>Історія покупок</b>\n\n{list}", "📖 <b>История покупок</b>\n\n{list}"),
    "dep_hist": ("История депозитов", "{list}", "🧾 <b>Історія депозитів</b>\n\n{list}",
                 "🧾 <b>История депозитов</b>\n\n{list}"),
    "topup_choose": ("Пополнение: выбор банка", "—",
                     "💳 <b>Пополнение баланса</b>\n\nВыберите способ оплаты:",
                     "💳 <b>Пополнение баланса</b>\n\nВыберите банк для оплаты:"),
    "topup_amount": ("Пополнение: ввод суммы", "—",
                     "Введите сумму в UAH; баланс будет зачислен в USD.",
                     "Введите сумму в UAH; баланс будет зачислен в USD."),
    "topup_details": ("Реквизиты для оплаты", "{bank} {card} {holder} {amount} {id}",
                      "💳 <b>Реквізити для оплати</b>\n\n🏦 Банк: <b>{bank}</b>\n💳 Карта: <code>{card}</code>\n"
                      "👤 Отримувач: {holder}\n💰 Сума: <b>{amount}</b>\n🔖 Заявка №{id}\n\n"
                      "Після оплати натисніть «Надіслати квитанцію» та надішліть скріншот/чек.",
                      "💳 <b>Реквизиты для оплаты</b>\n\n🏦 Банк: <b>{bank}</b>\n💳 Карта: <code>{card}</code>\n"
                      "👤 Получатель: {holder}\n💰 Сумма: <b>{amount}</b>\n🔖 Заявка №{id}\n\n"
                      "После оплаты нажмите «Отправить квитанцию» и пришлите скриншот/чек."),
    "receipt_prompt": ("Запрос квитанции", "—",
                       "📸 Отправьте скриншот или фото квитанции об оплате:",
                       "📸 Отправьте скриншот или фото квитанции об оплате:"),
    "receipt_sent": ("Квитанция отправлена", "—",
                     "✅ Квитанцію надіслано. Очікуйте підтвердження — зазвичай до кількох хвилин.",
                     "✅ Квитанция отправлена. Ожидайте подтверждения — обычно несколько минут."),
    "dep_ok": ("Баланс пополнен", "{amount}",
               "✅ Баланс поповнено на <b>{amount}</b>. Дякуємо!", "✅ Баланс пополнен на <b>{amount}</b>. Спасибо!"),
    "dep_no": ("Заявка на пополнение отклонена", "{id}",
               "❌ Заявку №{id} відхилено. Якщо це помилка — зверніться в підтримку.",
               "❌ Заявка №{id} отклонена. Если это ошибка — обратитесь в поддержку."),
    "sub_required": ("Обязательная подписка", "—",
                     "📢 Щоб користуватись ботом, підпишіться на наші канали:",
                     "📢 Чтобы пользоваться ботом, подпишитесь на наши каналы:"),
    "qty_prompt": ("Запрос количества", "—", "Введите количество (число):", "Введите количество (число):"),
    # короткие всплывающие уведомления (без форматирования)
    "no_balance": ("Алерт: мало денег", "—", "❌ Недостатньо коштів на балансі.", "❌ Недостаточно средств на балансе."),
    "no_stock": ("Алерт: нет товара", "—", "❌ Недостатньо товару в наявності.", "❌ Недостаточно товара в наличии."),
    "bad_amount": ("Алерт: неверная сумма", "—", "❌ Неверная сумма. Введите число не меньше минимума.",
                   "❌ Неверная сумма. Введите число, не меньше минимума."),
    "no_req": ("Алерт: нет реквизитов", "—", "Реквізити тимчасово недоступні. Зверніться в підтримку.",
               "Реквизиты временно недоступны. Обратитесь в поддержку."),
    "receipt_bad": ("Алерт: нужна квитанция", "—", "Отправьте фото или файл квитанции.",
                    "Отправьте фото или файл квитанции."),
    "sub_fail": ("Алерт: не подписан", "—", "❌ Ви ще не підписані на всі канали.",
                 "❌ Вы ещё не подписаны на все каналы."),
    "wait_alert": ("Алерт: заявка уже отправлена", "—", "⏳ Заявка вже надіслана, очікуйте.",
                   "⏳ Заявка уже отправлена, ожидайте."),
    "no_hist": ("Пустая история", "—", "Поки що порожньо.", "Пока пусто."),
    "prod_btn": ("Подпись кнопки товара в каталоге", "{fire} {title} {price}",
                 "{fire}{title} • {price}", "{fire}{title} • {price}"),
    "search_prompt": ("Поиск: запрос", "—", "🔍 Введите название страны или товара:", "🔍 Введите название страны или товара:"),
    "item_pw": ("Выдача: логин + пароль", "{login} {pw}",
                "📧 <b>Логин:</b> <code>{login}</code>\n🔑 <b>Пароль:</b> <code>{pw}</code>",
                "📧 <b>Логин:</b> <code>{login}</code>\n🔑 <b>Пароль:</b> <code>{pw}</code>"),
    "item_nopw": ("Выдача: только логин", "{login}", "📧 <b>Логин:</b> <code>{login}</code>", "📧 <b>Логин:</b> <code>{login}</code>"),
    "item_code": ("Выдача: номер (код по запросу)", "{login}",
                  "📱 Номер: <code>{login}</code>", "📱 Номер: <code>{login}</code>"),
    "order_code": ("Номер выдан + дальнейшие действия", "{id} {title} {items}",
                   "✅ <b>Оплата успешно получена!</b> Заказ №{id}\n\n{items}\n\n"
                   "━━━━━━━━━━━━━━━━━━\n\n"
                   "📱 Информация по заказу показана выше. Выберите действие на кнопках ниже.\n"
                   "Если возникла проблема с заказом, обратитесь в поддержку.",
                   "✅ <b>Оплата успешно получена!</b> Заказ №{id}\n\n{items}\n\n"
                   "━━━━━━━━━━━━━━━━━━\n\n"
                   "📱 Информация по заказу показана выше. Выберите действие на кнопках ниже.\n"
                   "Если возникла проблема с заказом, обратитесь в поддержку."),
    "code_sent": ("Ответ по заказу отправлен", "{id}",
                  "📩 Ответ по заказу №{id} отправлен выше.\n\n"
                  "Если возникла проблема или данные не подходят, обратитесь в поддержку.",
                  "📩 Ответ по заказу №{id} отправлен выше.\n\n"
                  "Если возникла проблема или данные не подходят, обратитесь в поддержку."),
    "code_wait": ("Алерт: рано повторять запрос кода", "{sec}",
                  "⏳ Повторний запит коду буде доступний через {sec} с.",
                  "⏳ Повторный запрос кода будет доступен через {sec} сек."),
    "code_one": ("Алерт: номера по одному", "—", "Номера покупаются по одному.", "Номера покупаются по одному."),
    # ── Telegram Stars / Premium ──
    "tg_stars": ("⭐ Stars: выбор количества (экран)", "{min} {max} {price}",
                 "⌨️ <b>Выберите количество звёзд</b>\n\n<blockquote>Можно выбрать любое количество, но не меньше {min} штук за покупку</blockquote>",
                 "⌨️ <b>Выберите количество звёзд</b>\n\n<blockquote>Вы можете выбрать любое количество не менее {min} штук за покупку</blockquote>"),
    "tg_stars_btn": ("⭐ Stars: подпись кнопки пакета", "{qty} {price}", "{qty} ⭐ — {price}", "{qty} ⭐ — {price}"),
    "tg_prem": ("💎 Premium: выбор периода (экран)", "—",
                "💎 <b>Выберите период подписки</b>\n\n<blockquote>Для покупки <b>Telegram Premium</b> выберите один из доступных планов:</blockquote>",
                "💎 <b>Выберите период подписки</b>\n\n<blockquote>Для покупки <b>Telegram Premium</b> выберите один из доступных планов:</blockquote>"),
    "tg_prem_btn": ("💎 Premium: подпись кнопки плана", "{months} {price}", "{months} • {price}", "{months} • {price}"),
    "tg_qty_prompt": ("⭐ Stars: ввод своего количества", "{min} {max}",
                      "✏️ Введите количество звёзд (от {min} до {max}):", "✏️ Введите количество звёзд (от {min} до {max}):"),
    "tg_qty_bad": ("Алерт: неверное количество Stars", "{min} {max}",
                   "❌ Введите целое число от {min} до {max}.", "❌ Введите целое число от {min} до {max}."),
    "tg_user_prompt": ("Stars/Premium: кому выдать (запрос @username)", "{what}",
                       "👤 Отправьте <b>@username</b> получателя для:\n<b>{what}</b>\n\nАбо натисніть «Собі».",
                       "👤 Отправьте <b>@username</b> получателя для:\n<b>{what}</b>\n\nИли нажмите «Себе»."),
    "tg_user_bad": ("Алерт: неверный @username", "—",
                    "❌ Неверный username. Отправьте @username (5–32 символов: латиница, цифры, _).",
                    "❌ Неверный username. Отправьте @username (5–32 символа: латиница, цифры, _)."),
    "tg_nouser": ("Алерт: у покупателя нет username", "—",
                  "❌ В вашем профиле Telegram не указан username. Отправьте username вручную.",
                  "❌ В вашем профиле Telegram не указан username. Отправьте username вручную."),
    "tg_off": ("Алерт: раздел Stars/Premium выключен", "—", "❌ Цей розділ тимчасово недоступний.",
               "❌ Этот раздел временно недоступен."),
    "tg_confirm": ("Stars/Premium: подтверждение покупки", "{what} {user} {total}",
                   "Підтвердіть покупку:\n\n<b>{what}</b>\nОтримувач: <b>@{user}</b>\nДо сплати: <b>{total}</b>",
                   "Подтвердите покупку:\n\n<b>{what}</b>\nПолучатель: <b>@{user}</b>\nК оплате: <b>{total}</b>"),
    "tg_paid": ("Stars/Premium: оплачено, заявка передана", "{id} {what} {user} {total}",
                "✅ <b>Оплачено!</b> Замовлення №{id}\n\n{what}\nОтримувач: @{user}\nСума: {total}\n\n"
                "⏳ Адміністратор виконає замовлення найближчим часом — ви отримаєте повідомлення.",
                "✅ <b>Оплачено!</b> Заказ №{id}\n\n{what}\nПолучатель: @{user}\nСумма: {total}\n\n"
                "⏳ Администратор выполнит заказ в ближайшее время — вы получите уведомление."),
    "tg_done": ("Stars/Premium: заказ выполнен", "{id} {what} {user} {total}",
                "🎉 Замовлення №{id} виконано!\n\n{what} → @{user}\n\nДякуємо за покупку!",
                "🎉 Заказ №{id} выполнен!\n\n{what} → @{user}\n\nСпасибо за покупку!"),
}


# ── Каталоги аккаунтов (Gmail / Discord / VK / TikTok): карточка, подтверждение, выдача ──
# Цены, наличие, количество и сумма — всегда переменные ({price} {stock_display} {qty} {total}),
# а не жёстко прописанные числа; всё редактируется в админке («Экраны и сообщения»).
_ACC_LINE = "━━━━━━━━━━━━━━━━━━"
_ACC_INFO = ("▫️ Аккаунты могут быть зарегистрированы в 2026 году или ранее.\n"
             "▫️ Зачастую это новореги — аккаунты с недавней регистрацией.\n")
# Общие блоки подтверждения и выдачи (одинаковы для всех платформ).
_ACC_CONFIRM_NOTES = ("▫️ Проверьте выбранный товар и количество.\n"
                      "▫️ Формат выдачи: логин + пароль.\n"
                      "▫️ После подтверждения заказа средства будут списаны с вашего баланса.")
_ACC_REC = ("▫️ Сохраните данные для входа.\n"
            "▫️ Проверьте возможность авторизации в аккаунте.\n"
            "▫️ Если возникнут проблемы, обратитесь в поддержку.")
ACC_PLATFORMS = {
    "gmail": dict(
        label="Gmail", emoji="📧",
        head='<a href="https://gmail.com">Gmail.com</a> | Google-аккаунты',
        type="Свежие Google-аккаунты",
        auth="📲 <b>Авторизация:</b> Без SMS-подтверждения*\n",
        info=_ACC_INFO + "▫️ Формат выдачи: логин + пароль.",
        confirm_item='<a href="https://gmail.com">Gmail.com</a> | Google-аккаунты',
    ),
    "discord": dict(
        label="Discord", emoji="🎮",
        head="Discord | Аккаунты", type="Discord", auth="",
        info=_ACC_INFO + "▫️ Данные для входа предоставляются после покупки.",
        confirm_item="Discord | Аккаунты",
    ),
    "vk": dict(
        label="VK", emoji="🔵",
        head="VK | Аккаунты", type="ВКонтакте", auth="",
        info=_ACC_INFO + "▫️ Данные для входа предоставляются после покупки.",
        confirm_item="VK | Аккаунты",
    ),
    "tiktok": dict(
        label="TikTok", emoji="🔵",
        head="TikTok | Аккаунты", type="TikTok", auth="",
        info=_ACC_INFO + "▫️ Данные для входа предоставляются после покупки.",
        confirm_item="TikTok | Аккаунты",
    ),
}
ACC_CAT_RE = {
    "gmail": r"gmail|гмейл|гмаил|google|гугл",
    "discord": r"discord|дискорд|діскорд",
    "vk": r"(?<!\w)(vk|вк|вконтакте|vkontakte)(?!\w)",
    "tiktok": r"tik\s*-?\s*tok|тик\s*-?\s*ток|тік\s*-?\s*ток",
}


def acc_platform(cat_name):
    """Тип каталога аккаунтов по названию категории (или None)."""
    name = (cat_name or "").casefold()
    for key, rx in ACC_CAT_RE.items():
        if re.search(rx, name):
            return key
    return None


def _acc_texts(key, cfg):
    em, head = cfg["emoji"], cfg["head"]
    prod = (
        f"{em} <b>{head}</b>\n\n"
        f"💎 <b>Тип:</b> {cfg['type']}\n"
        "🔐 <b>Формат выдачи:</b> Логин + пароль\n"
        f"{cfg['auth']}"
        "💰 <b>Цена за 1 шт.:</b> {price}\n"
        "📦 <b>В наличии:</b> {stock_display}\n\n"
        f"{_ACC_LINE}\n\n"
        f"<blockquote>📌 <b>Информация об аккаунтах</b>\n\n{cfg['info']}</blockquote>\n\n"
        "🛒 <b>Выберите количество аккаунтов для покупки:</b>\n\n"
        "👇 <b>Укажите необходимое количество ниже.</b>"
    )
    confirm = (
        "🛒 <b>Подтверждение покупки</b>\n\n"
        f"{em} <b>Товар:</b> {cfg['confirm_item']}\n"
        "📦 <b>Количество:</b> {qty} шт.\n"
        "💰 <b>Цена за 1 шт.:</b> {price}\n"
        "💳 <b>К оплате:</b> {total}\n\n"
        f"{_ACC_LINE}\n\n"
        f"<blockquote>⚠️ <b>Перед покупкой</b>\n\n{_ACC_CONFIRM_NOTES}</blockquote>\n\n"
        "👇 <b>Нажмите кнопку ниже, чтобы подтвердить покупку.</b>"
    )
    bought = (
        "✅ <b>Покупка успешно завершена!</b>\n\n"
        f"{em} <b>{cfg['label']} × " + "{qty}</b>\n"
        "💰 <b>Списано с баланса:</b> {total}\n\n"
        f"{_ACC_LINE}\n\n"
        "🔐 <b>Ваши данные для входа</b>\n"
        "{items}\n\n"
        f"{_ACC_LINE}\n\n"
        f"<blockquote>📋 <b>Рекомендации</b>\n\n{_ACC_REC}</blockquote>\n\n"
        "⚠️ <b>Не передавайте данные третьим лицам.</b>"
    )
    lb = cfg["label"]
    return {
        f"acc_prod_{key}": (f"{lb}: карточка товара", "{price} {stock_display} {title}", prod, prod),
        f"acc_confirm_{key}": (f"{lb}: подтверждение покупки", "{qty} {price} {total} {title}", confirm, confirm),
        f"acc_bought_{key}": (f"{lb}: выдача после покупки", "{qty} {total} {items} {title}", bought, bought),
    }


for _pk, _pc in ACC_PLATFORMS.items():
    DT.update(_acc_texts(_pk, _pc))

# Единственный язык интерфейса — русский.
for _k, _v in list(DT.items()):
    if len(_v) == 4:
        DT[_k] = (_v[0], _v[1], _v[3], _v[3])

TG_KEYS = ["tg_stars", "tg_stars_btn", "tg_prem", "tg_prem_btn", "tg_qty_prompt", "tg_user_prompt",
           "tg_confirm", "tg_paid", "tg_done", "tg_qty_bad", "tg_user_bad", "tg_nouser", "tg_off"]

# key: (название для админки, UA, RU)
DB_ = {
    "shop": ("Меню: Витрина", "🛒 Витрина", "🛒 Витрина"),
    "profile": ("Меню: Профиль", "👤 Профиль", "👤 Профиль"),
    "balance": ("Меню: Баланс", "💼 Баланс", "💼 Баланс"),
    "history": ("Меню: История покупок", "📖 Історія покупок", "📖 История покупок"),
    "admin": ("Меню: Админ-панель", "🛠 Адмін-панель", "🛠 Админ-панель"),
    "support": ("Кнопка поддержки", "🆘 Поддержка", "🆘 Поддержка"),
    "back": ("Кнопка «Назад»", "⬅️ Назад", "⬅️ Назад"),
    "custom_qty": ("Своё количество", "✏️ Своя кількість", "✏️ Своё количество"),
    "pay": ("Купить (подтвердить)", "✅ Купити", "✅ Купить"),
    "cancel": ("Отмена", "✖️ Скасувати", "✖️ Отмена"),
    "topup": ("Пополнить баланс", "➕ Поповнити баланс", "➕ Пополнить баланс"),
    "dep_hist": ("История депозитов", "🧾 Історія депозитів", "🧾 История депозитов"),
    "send_receipt": ("Отправить квитанцию", "🧾 Надіслати квитанцію", "🧾 Отправить квитанцию"),
    "sub_check": ("Я подписался", "✅ Я підписався", "✅ Я подписался"),
    "search": ("Каталог: Поиск", "🔍 Пошук", "🔍 Поиск"),
    "search_reset": ("Каталог: Сбросить поиск", "✖️ Скинути пошук", "✖️ Сбросить поиск"),
    "sort_name": ("Каталог: сортировка по названию", "Назва", "Название"),
    "sort_pop": ("Каталог: сортировка по популярности", "Популярність", "Популярность"),
    "sort_price": ("Каталог: сортировка по цене", "Ціна", "Цена"),
    "req_code": ("Получить код", "📩 Получить код", "📩 Получить код"),
    "code_done": ("Готово (код получен)", "✅ Готово", "✅ Готово"),
    "tg_stars": ("Витрина: Telegram Stars", "⭐ Telegram Stars", "⭐ Telegram Stars"),
    "tg_prem": ("Витрина: Telegram Premium", "💎 Telegram Premium", "💎 Telegram Premium"),
    "tg_self": ("Stars/Premium: получатель — себе", "👤 Собі", "👤 Себе"),
    "tg_other": ("Stars: другое количество", "✏️ Інша кількість", "✏️ Другое количество"),
}

# Единственный язык кнопок — русский.
for _k, _v in list(DB_.items()):
    if len(_v) == 3:
        DB_[_k] = (_v[0], _v[2], _v[2])


async def sync_country_order_ui():
    """Применяет обновлённые системные тексты и исправляет старые названия кнопок в SQLite."""
    fixed_texts = {
        "country_prod": DT["country_prod"][3],
        "order_paid": DT["order_paid"][3],
        "order_next": DT["order_next"][3],
        "order_code": DT["order_code"][3],
        "code_sent": DT["code_sent"][3],
    }
    for key, body in fixed_texts.items():
        await q("INSERT INTO ui_text(key,lang,html) VALUES(?,?,?) "
                "ON CONFLICT(key,lang) DO UPDATE SET html=excluded.html", (key, "ru", body))
    await q("INSERT INTO ui_btn(key,lang,label,emoji_id) VALUES(?,?,?,NULL) "
            "ON CONFLICT(key,lang) DO UPDATE SET label=excluded.label, emoji_id=NULL",
            ("req_code", "ru", "📩 Получить код"))

    # Язык один — русский: всем пользователям ставим ru, мёртвые записи выбора языка чистим.
    await q("UPDATE users SET lang='ru' WHERE lang IS NULL OR lang!='ru'")
    await q("DELETE FROM ui_text WHERE key='choose_lang'")
    await q("DELETE FROM ui_btn WHERE key IN ('lang','lang_ua','lang_ru')")

    # Один раз сбрасываем ранее сохранённые тексты каталогов аккаунтов (acc_*), чтобы применились новые шаблоны.
    # Дальше их можно спокойно менять в админке — повторно перезаписываться они не будут.
    if not await q("SELECT v FROM settings WHERE k='acc_texts_v3'", fetch="one"):
        await q("UPDATE ui_text SET html=NULL WHERE key LIKE 'acc\\_%' ESCAPE '\\'")
        await q("INSERT OR REPLACE INTO settings(k,v) VALUES('acc_texts_v3','1')")

    # В уже настроенных этапах заказа приводим подписи к русскому языку.
    for table, where in (("products", "steps IS NOT NULL AND steps != ''"),
                         ("orders", "status='active' AND steps IS NOT NULL AND steps != ''")):
        rows = await q(f"SELECT id, steps FROM {table} WHERE {where}", fetch="all")
        for row in rows:
            try:
                steps = json.loads(row["steps"] or "[]")
            except (TypeError, ValueError):
                continue
            changed = False
            for item in steps:
                if not isinstance(item, list) or not item or not isinstance(item[0], str):
                    continue
                label = item[0].strip().casefold()
                if label in ("отримати код", "запросити код"):
                    item[0] = "Получить код"
                    changed = True
                elif label == "отримати номер":
                    item[0] = "Получить номер"
                    changed = True
            if changed:
                await q(f"UPDATE {table} SET steps=? WHERE id=?", (json.dumps(steps, ensure_ascii=False), row["id"]))

UI_T, UI_B, SET = {}, {}, {}


async def reload_ui():
    UI_T.clear()
    UI_B.clear()
    SET.clear()
    for x in await q("SELECT * FROM ui_text", fetch="all"):
        UI_T[(x["key"], x["lang"])] = (x["html"], x["mtype"], x["mfile"])
    for x in await q("SELECT * FROM ui_btn", fetch="all"):
        UI_B[(x["key"], x["lang"])] = (x["label"], x["emoji_id"], x["style"])
    for x in await q("SELECT * FROM settings", fetch="all"):
        SET[x["k"]] = x["v"]


def txt(L, key):
    ov = UI_T.get((key, L))
    if ov and ov[0]:
        return ov[0]
    d = DT[key]
    return d[2] if L == "ua" else d[3]


def text_media(L, key):
    ov = UI_T.get((key, L))
    return (ov[1], ov[2]) if ov and ov[1] else (None, None)


def fill(s, **kw):
    return re.sub(r"\{(\w+)\}", lambda m: str(kw[m.group(1)]) if m.group(1) in kw else m.group(0), s)


def parse_amount(text, lo=0.01, hi=1_000_000):
    try:
        v = float((text or "").replace(",", ".").strip())
    except Exception:
        return None
    return round(v, 2) if math.isfinite(v) and lo <= v <= hi else None


def plain(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def tx(L, key, **kw):
    return fill(txt(L, key), **kw)


def alert_text(L, key):
    return plain(txt(L, key))[:190]


def B(L, key, data=None, url=None):
    ov = UI_B.get((key, L))
    d = DB_[key]
    label = ov[0] if ov and ov[0] else (d[1] if L == "ua" else d[2])
    return InlineKeyboardButton(text=label, callback_data=data, url=url,
                                icon_custom_emoji_id=ov[1] if ov else None, style=ov[2] if ov else None)


def mk_btn(label, data=None, emoji_id=None, url=None):
    return InlineKeyboardButton(text=label, callback_data=data, url=url, icon_custom_emoji_id=emoji_id or None)


def btn(text, data=None, url=None):
    return InlineKeyboardButton(text=text, callback_data=data, url=url)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_url():
    u = (SET.get("support") or SUPPORT_ENV or "").lstrip("@")
    return f"https://t.me/{u}" if u else None


def support_row(L):
    u = support_url()
    return [B(L, "support", url=u)] if u else []


def parse_label(m):
    """Текст кнопки + id первого премиум-эмодзи (он станет иконкой кнопки)."""
    text = m.text or m.caption or ""
    emoji_id = None
    for e in (m.entities or m.caption_entities or []):
        if e.type == "custom_emoji":
            emoji_id = e.custom_emoji_id
            b = text.encode("utf-16-le")
            text = (b[:e.offset * 2] + b[(e.offset + e.length) * 2:]).decode("utf-16-le")
            break
    return text.strip(), emoji_id


def extract_media(m):
    if m.animation:
        return "animation", m.animation.file_id
    if m.photo:
        return "photo", m.photo[-1].file_id
    if m.video:
        return "video", m.video.file_id
    return None


# ───────────────────────── Відправка (з фолбеками для преміум-емодзі) ─────────────────────────
def strip_icons(markup):
    if not markup:
        return markup
    return InlineKeyboardMarkup(inline_keyboard=[
        [b.model_copy(update={"icon_custom_emoji_id": None}) for b in row] for row in markup.inline_keyboard])


def strip_tgemoji(s):
    return re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", s, flags=re.S)


async def try_send(bot, chat_id, text, markup=None, mt=None, mf=None):
    sm = strip_icons(markup)
    variants = [(text, markup, ParseMode.HTML), (text, sm, ParseMode.HTML),
                (strip_tgemoji(text), sm, ParseMode.HTML), (plain(text), sm, None)]
    media_fn = {"photo": bot.send_photo, "animation": bot.send_animation, "video": bot.send_video}
    last = None
    if mt in media_fn:
        try:
            if len(text) > 1024:  # подпись ограничена — медиа отдельно, текст ниже
                await media_fn[mt](chat_id, mf)
            else:
                for t, mk, pm in variants:
                    try:
                        return await media_fn[mt](chat_id, mf, caption=t, reply_markup=mk, parse_mode=pm)
                    except TelegramBadRequest as e:
                        last = e
        except TelegramBadRequest as e:  # битый file_id — покажем хотя бы текст
            last = e
    for t, mk, pm in variants:
        try:
            return await bot.send_message(chat_id, t, reply_markup=mk, parse_mode=pm)
        except TelegramBadRequest as e:
            last = e
    raise last


def has_media(msg):
    return bool(msg.photo or msg.animation or msg.video or msg.document or msg.video_note)


async def present(ev, text, markup=None, mt=None, mf=None):
    """Показать экран: редактирует сообщение, а если есть медиа — отправляет заново."""
    bot = ev.bot
    if isinstance(ev, CallbackQuery):
        try:
            await ev.answer()
        except Exception:
            pass
        msg = ev.message
        if isinstance(msg, Message):
            if not mt and not has_media(msg):
                sm = strip_icons(markup)
                for t, mk in [(text, markup), (text, sm), (strip_tgemoji(text), sm)]:
                    try:
                        await msg.edit_text(t, reply_markup=mk)
                        return
                    except TelegramBadRequest as e:
                        if "not modified" in str(e):
                            return
            try:
                await msg.delete()
            except Exception:
                pass
            chat = msg.chat.id
        else:  # старое сообщение, недоступное боту
            chat = ev.from_user.id
    else:
        chat = ev.chat.id
    await try_send(bot, chat, text, markup, mt, mf)


show = present  # админские экраны (без медиа)


async def screen(ev, key, L, markup=None, media=None, **kw):
    mt, mf = media if (media and media[0]) else text_media(L, key)
    await present(ev, fill(txt(L, key), **kw), markup, mt, mf)


async def screen_to(bot, uid, key, L, markup=None, **kw):
    mt, mf = text_media(L, key)
    await try_send(bot, uid, fill(txt(L, key), **kw), markup, mt, mf)


async def lang_of(uid):
    # Интерфейс магазина доступен только на русском языке.
    return "ru"


async def main_menu(ev, uid):
    L = await lang_of(uid)
    rows = [[B(L, "shop", "m:shop")],
            [B(L, "profile", "m:prof"), B(L, "balance", "m:bal")],
            [B(L, "history", "m:hist")]]
    if support_row(L):
        rows.append(support_row(L))
    if await role_of(uid):
        rows.append([B(L, "admin", "a:home")])
    await screen(ev, "welcome", L, kb(rows))


# ───────────────────────── Обязательная подписка ─────────────────────────
SUB_OK = {}


async def missing_channels(bot: Bot, uid):
    if time.time() - SUB_OK.get(uid, 0) < 60:
        return []
    miss = []
    for ch in await q("SELECT * FROM channels", fetch="all"):
        try:
            mm = await bot.get_chat_member(ch["chat_id"], uid)
            if mm.status in ("left", "kicked"):
                miss.append(ch)
        except Exception:
            continue
    if not miss:
        SUB_OK[uid] = time.time()
    return miss


async def reset_state(state, uid):
    """Сбросить FSM; если админ бросил выдачу — освободить заявку."""
    if await state.get_state() == Fulfill.msg.state:
        sd = await state.get_data()
        await qr("UPDATE orders SET claimed_by=NULL WHERE id=? AND claimed_by=?", (sd.get("oid"), uid))
    await state.clear()


KEEP_STATE = {"f:psd", "noop"}


class Gate(BaseMiddleware):
    async def allowed(self, event, data):
        if isinstance(event, Message) and event.chat.type != "private":
            return False
        user = event.from_user
        if not user or user.is_bot:
            return True
        # Язык интерфейса один — русский, поэтому выбор языка пользователю не показываем.
        await q("INSERT OR IGNORE INTO users(id,username,lang,created) VALUES(?,?,'ru',?)", (user.id, user.username, now()))
        await q("UPDATE users SET username=?, lang='ru' WHERE id=?", (user.username, user.id))
        row = await q("SELECT banned FROM users WHERE id=?", (user.id,), "one")
        lang = "ru"
        cd = (event.data or "") if isinstance(event, CallbackQuery) else ""
        role = await role_of(user.id)
        if role in ("owner", "admin"):
            return True
        if SET.get("legacy_uah_pending") == "1":
            try:
                await event.answer("Магазин временно настраивается: идёт переход на USD. Попробуйте чуть позже.", show_alert=True) if isinstance(event, CallbackQuery) else await event.answer("Магазин временно настраивается: идёт переход на USD. Попробуйте чуть позже.")
            except Exception:
                pass
            return False
        if role:
            return True
        if row["banned"]:
            try:
                await event.answer("⛔", show_alert=True) if isinstance(event, CallbackQuery) else await event.answer("⛔")
            except Exception:
                pass
            return False
        if cd == "chk_sub":
            return True
        miss = await missing_channels(data["bot"], user.id)
        if miss:
            rows = [[btn(f"📢 {c['title']}", url=c["link"])] for c in miss if c["link"]]
            rows.append([B(lang, "sub_check", "chk_sub")])
            await screen(event, "sub_required", lang, kb(rows))
            return False
        return True

    async def __call__(self, handler, event, data):
        if not await self.allowed(event, data):
            return
        if isinstance(event, CallbackQuery) and event.data not in KEEP_STATE and data.get("state"):
            await reset_state(data["state"], event.from_user.id)  # любое нажатие кнопки выходит из «режима ввода»
        return await handler(event, data)


class Staff(Filter):
    async def __call__(self, ev):
        return await role_of(ev.from_user.id) is not None


class Full(Filter):
    async def __call__(self, ev):
        return await role_of(ev.from_user.id) in ("owner", "admin")


r = Router()
r.message.outer_middleware(Gate())
r.callback_query.outer_middleware(Gate())
r.message.filter(F.chat.type == "private")  # бот не реагирует в группах


# ───────────────────────── Пользователь: старт ─────────────────────────
@r.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await reset_state(state, m.from_user.id)
    await main_menu(m, m.from_user.id)


@r.callback_query(F.data.startswith("lang:") | (F.data == "m:lang"))
async def old_lang_button(c: CallbackQuery):
    # Кнопки выбора языка из старых сообщений: просто открываем главное меню.
    await main_menu(c, c.from_user.id)


@r.callback_query(F.data == "chk_sub")
async def chk_sub(c: CallbackQuery, bot: Bot):
    SUB_OK.pop(c.from_user.id, None)
    L = await lang_of(c.from_user.id)
    if await missing_channels(bot, c.from_user.id):
        return await c.answer(alert_text(L, "sub_fail"), show_alert=True)
    await main_menu(c, c.from_user.id)


@r.callback_query(F.data == "m:home")
async def go_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await main_menu(c, c.from_user.id)


# ───────────────────────── Telegram Stars / Premium ─────────────────────────
BUYING = set()  # пользователи, у которых прямо сейчас идёт покупка (защита от двойного клика)


class TgS(StatesGroup):
    qty = State()
    user = State()


def calc_total(price, qty):
    """Сумма с математическим округлением до копеек (0.5 -> вверх)."""
    return float((Decimal(str(price)) * Decimal(int(qty))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def num_str(v):
    return format(Decimal(str(v)).normalize(), "f")


def parse_num(text, lo, hi):
    try:
        v = float((text or "").replace(",", ".").strip())
    except Exception:
        return None
    return round(v, 6) if math.isfinite(v) and lo <= v <= hi else None


def parse_int(text, lo, hi):
    t = (text or "").strip()
    if not t.isascii() or not t.isdigit():
        return None
    n = int(t)
    return n if lo <= n <= hi else None


def month_label(L, n):
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        w = "месяц"
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        w = "месяца"
    else:
        w = "месяцев"
    return f"{n} {w}"


def stars_cfg():
    def g(k, d, cast):
        try:
            return cast(SET.get(k) or d)
        except Exception:
            return cast(d)

    mn, mx = g("stars_min", "50", int), g("stars_max", "100000", int)
    mx = max(mx, mn)
    try:
        pres = sorted({int(x) for x in re.findall(r"\d+", SET.get("stars_presets") or "50,100,250,500,1000,5000")})
    except Exception:
        pres = []
    return {"on": SET.get("stars_on", "1") == "1", "price": g("stars_price", "0.01725", float),
            "min": mn, "max": mx, "presets": [n for n in pres if mn <= n <= mx]}


def norm_user(text):
    t = (text or "").strip()
    for pre in ("https://t.me/", "http://t.me/", "t.me/"):
        if t.lower().startswith(pre):
            t = t[len(pre):]
    t = t.split("?")[0].strip("/").lstrip("@")
    return t if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", t) else None


async def warn(ev, text):
    if isinstance(ev, CallbackQuery):
        await ev.answer(text, show_alert=True)
    else:
        await ev.answer(text)


async def tg_rows(L):
    """Кнопки Stars / Premium в начале витрины."""
    rows = []
    if stars_cfg()["on"]:
        rows.append([B(L, "tg_stars", "tg:s")])
    if SET.get("prem_on", "1") == "1" and await q("SELECT 1 FROM premium_plans WHERE active=1 LIMIT 1", fetch="one"):
        rows.append([B(L, "tg_prem", "tg:p")])
    return rows


async def tg_quote(L, k, val):
    """(название, сумма) для Stars (val = количество) или Premium (val = id плана); None если недоступно."""
    if k == "s":
        cfg = stars_cfg()
        if not cfg["on"] or not cfg["min"] <= val <= cfg["max"]:
            return None
        return f"⭐ {val} Telegram Stars", calc_total(cfg["price"], val)
    if k == "p" and SET.get("prem_on", "1") == "1":
        p = await q("SELECT * FROM premium_plans WHERE id=? AND active=1", (val,), "one")
        if p:
            return f"💎 Telegram Premium — {month_label(L, p['months'])}", calc_total(p["price"], 1)
    return None


def tg_back(k):
    return "tg:s" if k == "s" else "tg:p"


@r.callback_query(F.data == "tg:s")
async def tg_stars(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    cfg = stars_cfg()
    if not cfg["on"]:
        return await c.answer(alert_text(L, "tg_off"), show_alert=True)
    rows, row = [], []
    for n in cfg["presets"]:
        row.append(btn(fill(plain(txt(L, "tg_stars_btn")), qty=n, price=money(calc_total(cfg["price"], n))), f"tg:sq:{n}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B(L, "tg_other", "tg:so")])
    rows.append([B(L, "back", "m:shop")])
    await screen(c, "tg_stars", L, kb(rows), min=cfg["min"], max=cfg["max"], price="$" + num_str(cfg["price"]))


@r.callback_query(F.data.startswith("tg:sq:"))
async def tg_sq(c: CallbackQuery, state: FSMContext):
    await ask_recipient(c, state, "s", int(c.data.split(":")[2]))


@r.callback_query(F.data == "tg:so")
async def tg_so(c: CallbackQuery, state: FSMContext):
    L = await lang_of(c.from_user.id)
    cfg = stars_cfg()
    if not cfg["on"]:
        return await c.answer(alert_text(L, "tg_off"), show_alert=True)
    await state.set_state(TgS.qty)
    await screen(c, "tg_qty_prompt", L, kb([[B(L, "cancel", "tg:s")]]), min=cfg["min"], max=cfg["max"])


@r.message(TgS.qty)
async def tg_qty_in(m: Message, state: FSMContext):
    L = await lang_of(m.from_user.id)
    cfg = stars_cfg()
    n = parse_int(m.text, cfg["min"], cfg["max"])
    if n is None:
        return await m.answer(fill(plain(txt(L, "tg_qty_bad")), min=cfg["min"], max=cfg["max"]))
    await ask_recipient(m, state, "s", n)


@r.callback_query(F.data == "tg:p")
async def tg_prem(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    plans = await q("SELECT * FROM premium_plans WHERE active=1 ORDER BY months", fetch="all")
    if SET.get("prem_on", "1") != "1" or not plans:
        return await c.answer(alert_text(L, "tg_off"), show_alert=True)
    rows = [[btn(fill(plain(txt(L, "tg_prem_btn")), months=month_label(L, p["months"]), price=money(p["price"])),
                 f"tg:pp:{p['id']}")] for p in plans]
    rows.append([B(L, "back", "m:shop")])
    await screen(c, "tg_prem", L, kb(rows))


@r.callback_query(F.data.startswith("tg:pp:"))
async def tg_pp(c: CallbackQuery, state: FSMContext):
    await ask_recipient(c, state, "p", int(c.data.split(":")[2]))


async def ask_recipient(ev, state, k, val):
    L = await lang_of(ev.from_user.id)
    qt = await tg_quote(L, k, val)
    if not qt:
        await state.clear()
        return await warn(ev, alert_text(L, "tg_off"))
    await state.set_state(TgS.user)
    await state.update_data(k=k, val=val)
    await screen(ev, "tg_user_prompt", L, kb([[B(L, "tg_self", f"tg:me:{k}:{val}")], [B(L, "cancel", tg_back(k))]]),
                 what=esc(qt[0]))


@r.message(TgS.user)
async def tg_user_in(m: Message, state: FSMContext):
    L = await lang_of(m.from_user.id)
    d = await state.get_data()
    if "k" not in d:
        await state.clear()
        return await main_menu(m, m.from_user.id)
    u = norm_user(m.text)
    if not u:
        return await m.answer(plain(txt(L, "tg_user_bad")))
    await state.clear()
    await tg_confirm(m, L, d["k"], d["val"], u)


@r.callback_query(F.data.startswith("tg:me:"))
async def tg_me(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    _, _, k, val = c.data.split(":")
    if not c.from_user.username:
        return await c.answer(alert_text(L, "tg_nouser"), show_alert=True)
    await tg_confirm(c, L, k, int(val), c.from_user.username)


async def tg_confirm(ev, L, k, val, user):
    qt = await tg_quote(L, k, val)
    if not qt:
        return await warn(ev, alert_text(L, "tg_off"))
    what, total = qt
    await screen(ev, "tg_confirm", L,
                 kb([[B(L, "pay", f"tg:ok:{k}:{val}:{user}"), B(L, "cancel", tg_back(k))]]),
                 what=esc(what), user=esc(user), total=money(total))


async def buy_tg(uid, k, val, user, L):
    async with conn() as c:
        c.row_factory = aiosqlite.Row
        await c.execute("BEGIN IMMEDIATE")
        if k == "s":
            cfg = stars_cfg()
            if not cfg["on"] or not cfg["min"] <= val <= cfg["max"]:
                await c.rollback()
                return "off", None
            total, qty, what, kind = calc_total(cfg["price"], val), val, f"⭐ {val} Telegram Stars", "stars"
        else:
            plan = await (await c.execute("SELECT * FROM premium_plans WHERE id=? AND active=1", (val,))).fetchone()
            if SET.get("prem_on", "1") != "1" or not plan:
                await c.rollback()
                return "off", None
            total, qty, kind = calc_total(plan["price"], 1), 1, "premium"
            what = f"💎 Telegram Premium — {month_label(L, plan['months'])}"
        u = await (await c.execute("SELECT balance FROM users WHERE id=?", (uid,))).fetchone()
        if not u or u["balance"] + 1e-9 < total:
            await c.rollback()
            return "balance", None
        await c.execute("UPDATE users SET balance=ROUND(balance-?,2) WHERE id=?", (total, uid))
        meta = json.dumps({"k": kind, "u": user, "n": val})
        cur = await c.execute(
            "INSERT INTO orders(user_id,product_id,title,qty,total,items,created,status,step,pending,steps,kind)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, None, what, qty, total, f"{what}\nПолучатель: @{user}", now(), "active", 0, 1, meta, "tg"))
        await c.commit()
        return "ok", (cur.lastrowid, total, what)


async def tg_card(o):
    meta = json.loads(o["steps"] or "{}")
    user = meta.get("u", "—")
    b = await q("SELECT username FROM users WHERE id=?", (o["user_id"],), "one")
    text = (f"⭐ <b>Заявка #{o['id']}</b> — {html.escape(o['title'])}\n"
            f"👤 Покупатель: <code>{o['user_id']}</code>{' @' + b['username'] if b and b['username'] else ''}\n"
            f"🎯 Получатель: <b>@{html.escape(user)}</b>\n💰 Оплачено: {money(o['total'])}")
    mk = kb([[btn("🔗 Открыть получателя", url=f"https://t.me/{user}")],
             [btn("✅ Выполнено", f"a:tgd:{o['id']}"), btn("↩️ Вернуть деньги", f"a:rf:{o['id']}")]])
    return text, mk


@r.callback_query(F.data.startswith("tg:ok:"))
async def tg_ok(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    if uid in BUYING:
        return await c.answer()
    BUYING.add(uid)
    try:
        L = await lang_of(uid)
        _, _, k, val, user = c.data.split(":", 4)
        if k not in ("s", "p") or not norm_user(user):
            return await c.answer(plain(txt(L, "tg_user_bad")), show_alert=True)
        st, res = await buy_tg(uid, k, int(val), user, L)
        if st != "ok":
            return await c.answer(alert_text(L, {"balance": "no_balance", "off": "tg_off"}[st]), show_alert=True)
        oid, total, what = res
        o = await q("SELECT * FROM orders WHERE id=?", (oid,), "one")
        text, mk = await request_card(o)
        for sid in await staff_ids():
            try:
                await bot.send_message(sid, text, reply_markup=mk)
            except Exception:
                pass
        rows = [[B(L, "back", "m:home")]]
        if support_row(L):
            rows.append(support_row(L))
        await screen(c, "tg_paid", L, kb(rows), id=oid, what=esc(what), user=esc(user), total=money(total))
    finally:
        BUYING.discard(uid)


@r.callback_query(F.data.startswith("a:tgd:"), Staff())
async def a_tg_done(c: CallbackQuery, bot: Bot):
    oid = int(c.data.split(":")[2])
    if not await qr("UPDATE orders SET status='done', pending=0, claimed_by=NULL WHERE id=? AND status='active' "
                    "AND kind='tg'", (oid,)):
        return await c.answer("Заказ уже закрыт", show_alert=True)
    o = await q("SELECT * FROM orders WHERE id=?", (oid,), "one")
    user = json.loads(o["steps"] or "{}").get("u", "")
    L = await lang_of(o["user_id"])
    try:
        await screen_to(bot, o["user_id"], "tg_done", L, kb([support_row(L)]) if support_row(L) else None,
                        id=oid, what=esc(o["title"]), user=esc(user), total=money(o["total"]))
    except Exception:
        pass
    try:
        await c.message.edit_text(f"{c.message.html_text}\n\n✅ ВЫПОЛНЕНО — {html.escape(c.from_user.full_name)}")
    except Exception:
        pass
    await c.answer("✅ Выполнено, покупатель уведомлён")


# ───────────────────────── Витрина ─────────────────────────
@r.callback_query(F.data == "m:shop")
async def shop(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    rows = await tg_rows(L)
    rows += [[mk_btn(x["name"], f"s:cat:{x['id']}", x["emoji_id"])] for x in await q("SELECT * FROM categories", fetch="all")]
    rows.append([B(L, "back", "m:home")])
    await screen(c, "shop_title", L, kb(rows))


async def acc_kind(pid):
    """gmail / discord / vk / tiktok для товара по его категории, иначе None."""
    row = await q("SELECT c.name AS name FROM products p JOIN categories c ON c.id=p.cat_id WHERE p.id=?",
                  (pid,), "one")
    return acc_platform(row["name"]) if row else None


def is_manual(p):
    return bool(json.loads(p["steps"] or "[]"))


SEARCH = {}
PAGE_SIZE = 20


class Srch(StatesGroup):
    q = State()


async def render_cat(ev, uid, cid, sort="o", dr="a", page=0):
    L = await lang_of(uid)
    cat = await q("SELECT * FROM categories WHERE id=?", (cid,), "one")
    if not cat:
        return
    prods = [dict(x) for x in await q(
        """SELECT p.*, (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) AS n,
                  (SELECT COALESCE(SUM(o.qty),0) FROM orders o WHERE o.product_id=p.id AND o.status!='refunded') AS pop
           FROM products p WHERE p.cat_id=? AND p.active=1""", (cid,), "all")]
    # У каталозі Telegram можна бачити країни й актуальні ціни навіть при нульовому залишку.
    is_telegram_cat = "telegram" in (cat["name"] or "").lower() or "телеграм" in (cat["name"] or "").lower()
    prods = [p for p in prods if is_telegram_cat or is_manual(p) or p["n"] > 0]
    query = SEARCH.get((uid, cid))
    if query:
        prods = [p for p in prods if query.lower() in p["title"].lower()]
    if is_telegram_cat and sort == "o":
        stored_order = await q("SELECT v FROM settings WHERE k='telegram_seed_product_ids'", fetch="one")
        ordered_ids = [int(x) for x in (stored_order["v"].split(",") if stored_order and stored_order["v"] else []) if x.isdigit()]
        order_index = {pid: i for i, pid in enumerate(ordered_ids)}
        prods.sort(key=lambda p: order_index.get(int(p["id"]), len(order_index) + int(p["id"])))
    else:
        if sort == "o":
            sort = "n"
        keyf = {"n": lambda p: re.sub(r"^[^\wА-Яа-яЁё]+", "", p["title"]).lower(),
                "p": lambda p: p["pop"], "c": lambda p: p["price"]}.get(sort)
        prods.sort(key=keyf, reverse=(dr == "d"))
    pages = max(1, (len(prods) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    rows, row = [], []
    for p in prods[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        label = fill(plain(txt(L, "prod_btn")), fire="🔥 " if p["pop"] > 0 else "", title=p["title"],
                     price=f"${p['price']:.2f}")
        row.append(mk_btn(label, f"s:p:{p['id']}", p["emoji_id"]))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    top = [B(L, "search", f"s:srch:{cid}")]
    if query:
        top.append(B(L, "search_reset", f"s:srx:{cid}"))
    rows.append(top)

    def sb(key, code, first_dir):
        nd = ("d" if dr == "a" else "a") if sort == code else first_dir
        b_ = B(L, key, f"s:cat:{cid}:{code}:{nd}:0")
        if sort == code:
            b_ = b_.model_copy(update={"text": b_.text + (" ▲" if dr == "a" else " ▼")})
        return b_

    rows.append([sb("sort_name", "n", "a"), sb("sort_pop", "p", "d"), sb("sort_price", "c", "a")])
    if pages > 1:
        base = f"s:cat:{cid}:{sort}:{dr}:"
        rows.append([btn("«", base + "0"), btn("‹", base + str(max(page - 1, 0))), btn(f"{page + 1}/{pages}", "noop"),
                     btn("›", base + str(min(page + 1, pages - 1))), btn("»", base + str(pages - 1))])
    rows.append([B(L, "back", "m:shop")])
    await screen(ev, "cat_title" if (prods or query) else "empty", L, kb(rows),
                 media=(cat["mtype"], cat["mfile"]), name=html.escape(cat["name"]))


@r.callback_query(F.data.startswith("s:cat:"))
async def shop_cat(c: CallbackQuery, state: FSMContext):
    p = c.data.split(":")
    cid = int(p[2])
    if len(p) == 3:
        SEARCH.pop((c.from_user.id, cid), None)
        await state.clear()
    await render_cat(c, c.from_user.id, cid, p[3] if len(p) > 3 else "o", p[4] if len(p) > 4 else "a",
                     int(p[5]) if len(p) > 5 else 0)


@r.callback_query(F.data.startswith("s:srch:"))
async def s_srch(c: CallbackQuery, state: FSMContext):
    L = await lang_of(c.from_user.id)
    cid = int(c.data.split(":")[2])
    await state.set_state(Srch.q)
    await state.update_data(cid=cid)
    await screen(c, "search_prompt", L, kb([[B(L, "cancel", f"s:cat:{cid}")]]))


@r.message(Srch.q)
async def s_srch_in(m: Message, state: FSMContext):
    cid = (await state.get_data())["cid"]
    await state.clear()
    SEARCH[(m.from_user.id, cid)] = (m.text or "").strip()
    await render_cat(m, m.from_user.id, cid)


@r.callback_query(F.data.startswith("s:srx:"))
async def s_srx(c: CallbackQuery):
    cid = int(c.data.split(":")[2])
    SEARCH.pop((c.from_user.id, cid), None)
    await render_cat(c, c.from_user.id, cid)


@r.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


@r.callback_query(F.data.startswith("s:p:"))
async def shop_prod(c: CallbackQuery, state: FSMContext):
    await state.clear()
    L = await lang_of(c.from_user.id)
    pid = int(c.data.split(":")[2])
    p = await q("SELECT * FROM products WHERE id=? AND active=1", (pid,), "one")
    if not p:
        return await c.answer(alert_text(L, "no_stock"), show_alert=True)
    n = (await q("SELECT COUNT(*) AS n FROM stock WHERE product_id=? AND sold=0", (pid,), "one"))["n"]
    stock = (p["stock_qty"] if p["stock_qty"] >= 0 else tx(L, "on_order")) if is_manual(p) else n
    first = await q("SELECT mode FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT 1", (pid,), "one")
    if first and first["mode"] == "code" and not is_manual(p):  # номера — по одному
        rows = [[B(L, "pay", f"s:c:{pid}:1")], [B(L, "back", f"s:cat:{p['cat_id']}")]]
    elif not is_manual(p) and n <= 0:  # товара нет — кнопки покупки не показываем
        rows = [[B(L, "back", f"s:cat:{p['cat_id']}")]]
    else:
        ks = [k for k in (1, 5, 10, 25) if is_manual(p) or k <= n] or [1]
        rows = [[btn(str(k), f"s:c:{pid}:{k}") for k in ks],
                [B(L, "custom_qty", f"s:q:{pid}")],
                [B(L, "back", f"s:cat:{p['cat_id']}")]]
    country_map = {country_product_key(country_title): plain_name
                   for country_title, _, plain_name in TELEGRAM_COUNTRY_PRODUCTS}
    country_name = country_map.get(country_product_key(p["title"]))
    if country_name:
        stock_display = f"{stock} шт." if isinstance(stock, (int, float)) else str(stock)
        return await screen(
            c, "country_prod", L, kb(rows), media=(p["mtype"], p["mfile"]),
            display_title=html.escape(p["title"]), country=html.escape(country_name),
            price=money(p["price"]), stock_display=html.escape(stock_display))
    acc = None if is_manual(p) else await acc_kind(pid)
    if acc:
        stock_display = f"{stock} шт." if isinstance(stock, (int, float)) else str(stock)
        return await screen(c, f"acc_prod_{acc}", L, kb(rows), media=(p["mtype"], p["mfile"]),
                            title=html.escape(p["title"]), price=money(p["price"]),
                            stock_display=html.escape(stock_display))
    await screen(c, "prod", L, kb(rows), media=(p["mtype"], p["mfile"]), title=html.escape(p["title"]),
                 price=money(p["price"]), stock=stock, descr=(p["descr"] + "\n\n") if p["descr"] else "")


class QtyS(StatesGroup):
    qty = State()


@r.callback_query(F.data.startswith("s:q:"))
async def ask_qty(c: CallbackQuery, state: FSMContext):
    L = await lang_of(c.from_user.id)
    await state.set_state(QtyS.qty)
    await state.update_data(pid=int(c.data.split(":")[2]))
    await screen(c, "qty_prompt", L, kb([[B(L, "back", f"s:p:{c.data.split(':')[2]}")]]))


@r.message(QtyS.qty)
async def got_qty(m: Message, state: FSMContext):
    L = await lang_of(m.from_user.id)
    pid = (await state.get_data()).get("pid")
    n = parse_int(m.text, 1, 1000)
    if n is None or pid is None:
        return await screen(m, "qty_prompt", L, kb([[B(L, "back", f"s:p:{pid}" if pid else "m:shop")]]))
    await state.clear()
    await confirm(m, L, pid, n)


async def confirm(ev, L, pid, qty):
    p = await q("SELECT * FROM products WHERE id=? AND active=1", (pid,), "one")
    if not p:
        return await ev.answer(alert_text(L, "no_stock"))
    if not is_manual(p):  # нельзя оформить больше, чем есть на складе
        have = (await q("SELECT COUNT(*) AS n FROM stock WHERE product_id=? AND sold=0", (pid,), "one"))["n"]
        if qty > have:
            if isinstance(ev, CallbackQuery):
                return await ev.answer(alert_text(L, "no_stock"), show_alert=True)
            return await ev.answer(plain(txt(L, "no_stock")) + f" Доступно: {have} шт.")
    rows = [[B(L, "pay", f"s:ok:{pid}:{qty}"), B(L, "cancel", f"s:p:{pid}")]]
    acc = None if is_manual(p) else await acc_kind(pid)
    kw = dict(title=html.escape(p["title"]), qty=qty, price=money(p["price"]), total=money(p["price"] * qty))
    if acc:
        return await screen(ev, f"acc_confirm_{acc}", L, kb(rows), **kw)
    await screen(ev, "confirm", L, kb(rows), **kw)


@r.callback_query(F.data.startswith("s:c:"))
async def pre_confirm(c: CallbackQuery):
    _, _, pid, qty = c.data.split(":")
    await confirm(c, await lang_of(c.from_user.id), int(pid), int(qty))


def order_kb(L, oid, steps, idx):
    rows = [[mk_btn(steps[idx][0], f"o:req:{oid}", steps[idx][1])]]
    if support_row(L):
        rows.append(support_row(L))
    return kb(rows)


def esc(x):
    return html.escape(str(x))


def fmt_item(L, x):
    if x.get("login"):
        if x["mode"] == "code":
            return tx(L, "item_code", login=esc(x["login"]))
        if x["pw"]:
            return tx(L, "item_pw", login=esc(x["login"]), pw=esc(x["pw"]))
        return tx(L, "item_nopw", login=esc(x["login"]))
    return esc(x.get("data") or "")


def code_kb(L, oid):
    rows = [[B(L, "req_code", f"o:req:{oid}")], [B(L, "code_done", f"o:fin:{oid}")]]
    if support_row(L):
        rows.append(support_row(L))
    return kb(rows)


@r.callback_query(F.data.startswith("s:ok:"))
async def do_buy(c: CallbackQuery):
    uid = c.from_user.id
    if uid in BUYING:  # защита от двойного нажатия «Купить»
        return await c.answer()
    BUYING.add(uid)
    try:
        await _do_buy(c)
    finally:
        BUYING.discard(uid)


async def _do_buy(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    _, _, pid, qty = c.data.split(":")
    qty = int(qty)
    status, res = await buy_atomic(c.from_user.id, int(pid), qty)
    if status in ("balance", "stock", "codeqty"):
        key = {"balance": "no_balance", "stock": "no_stock", "codeqty": "code_one"}[status]
        return await c.answer(alert_text(L, key), show_alert=True)
    if status == "manual":
        oid, total, title, steps = res
        return await screen(c, "order_paid", L, order_kb(L, oid, steps, 0), id=oid,
                            title=html.escape(title), qty=qty, total=money(total))
    oid, total, title, rows = res
    items = "\n\n".join(fmt_item(L, x) for x in rows)
    kw = dict(title=html.escape(title), qty=qty, total=money(total))
    if status == "code":
        return await screen(c, "order_code", L, code_kb(L, oid), id=oid, items=items, title=kw["title"])
    await c.answer()
    try:
        await c.message.delete()  # убираем экран подтверждения, чтобы не купить дважды
    except Exception:
        pass
    menu = kb([[B(L, "back", "m:home")]])
    acc = await acc_kind(int(pid))
    bought_key = f"acc_bought_{acc}" if acc else "bought"
    if len(fill(txt(L, bought_key), items=items, **kw)) > 3800:
        await screen_to(c.bot, c.from_user.id, "bought_file", L, None, **kw)
        await c.bot.send_document(c.from_user.id, BufferedInputFile(plain(items).encode(), f"order_{oid}.txt"),
                                  reply_markup=menu)
    else:
        await screen_to(c.bot, c.from_user.id, bought_key, L, menu, items=items, **kw)


# ───────────────────────── Ручна видача: заявки ─────────────────────────
async def request_card(o):
    if o["kind"] == "tg":
        return await tg_card(o)
    steps = json.loads(o["steps"] or "[]")
    st = steps[o["step"]][0] if o["step"] < len(steps) else "—"
    u = await q("SELECT username FROM users WHERE id=?", (o["user_id"],), "one")
    text = (f"📥 <b>Заявка по заказу #{o['id']}</b>\n👤 <code>{o['user_id']}</code>"
            f"{' @' + u['username'] if u and u['username'] else ''}\n"
            f"🛍 {html.escape(o['title'])} × {o['qty']} ({money(o['total'])})\n"
            f"🔹 Этап {o['step'] + 1}/{len(steps)}: <b>{html.escape(st)}</b>")
    if o["kind"] == "code" and o["items"]:
        text += f"\n📱 {html.escape(o['items'].split(chr(10))[0])}"
    if o["claimed_by"]:
        text += f"\n🙋 Взял: <code>{o['claimed_by']}</code>"
    mk = kb([[btn("✉️ Выдать", f"a:fl:{o['id']}"), btn("↩️ Вернуть деньги", f"a:rf:{o['id']}")]])
    return text, mk


@r.callback_query(F.data.startswith("o:req:"))
async def o_req(c: CallbackQuery, bot: Bot):
    L = await lang_of(c.from_user.id)
    oid = int(c.data.split(":")[2])
    o = await q("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, c.from_user.id), "one")
    if not o or o["status"] != "active":
        return await c.answer()
    if o["pending"]:
        return await c.answer(alert_text(L, "wait_alert"), show_alert=True)
    ts = int(time.time())
    if o["kind"] == "code" and ts - (o["last_req"] or 0) < 120:
        return await c.answer(plain(tx(L, "code_wait", sec=120 - (ts - (o["last_req"] or 0)))), show_alert=True)
    if not await qr("UPDATE orders SET pending=1, last_req=? WHERE id=? AND pending=0 AND status='active'", (ts, oid)):
        return await c.answer()
    o = await q("SELECT * FROM orders WHERE id=?", (oid,), "one")
    text, mk = await request_card(o)
    for sid in await staff_ids():
        try:
            await bot.send_message(sid, text, reply_markup=mk)
        except Exception:
            pass
    await screen(c, "order_wait", L, kb([support_row(L)]) if support_row(L) else None, id=oid)


@r.callback_query(F.data.startswith("o:fin:"))
async def o_fin(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    oid = int(c.data.split(":")[2])
    if not await qr("UPDATE orders SET status='done', pending=0, claimed_by=NULL WHERE id=? AND user_id=? "
                    "AND status='active' AND kind='code'", (oid, c.from_user.id)):
        return await c.answer()
    await screen(c, "order_done", L, kb([support_row(L)]) if support_row(L) else None, id=oid)


@r.callback_query(F.data.regexp(r"^h:\d+$"))
async def hist_item(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    o = await q("SELECT * FROM orders WHERE id=? AND user_id=?", (int(c.data.split(":")[1]), c.from_user.id), "one")
    if not o:
        return await c.answer()
    if o["status"] == "active":
        if o["pending"]:
            return await screen(c, "order_wait", L, kb([support_row(L)]) if support_row(L) else None, id=o["id"])
        if o["kind"] == "code":
            return await screen(c, "code_sent", L, code_kb(L, o["id"]), id=o["id"])
        steps = json.loads(o["steps"] or "[]")
        return await screen(c, "order_next", L, order_kb(L, o["id"], steps, o["step"]), id=o["id"])
    await c.message.answer_document(BufferedInputFile((o["items"] or "—").encode(), f"order_{o['id']}.txt"))
    await c.answer()


# ───────────────────────── Профиль / Баланс / Історія ─────────────────────────
@r.callback_query(F.data == "m:prof")
async def profile(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    u = await q("SELECT balance FROM users WHERE id=?", (c.from_user.id,), "one")
    n = (await q("SELECT COUNT(*) AS n FROM orders WHERE user_id=?", (c.from_user.id,), "one"))["n"]
    await screen(c, "prof_text", L, kb([[B(L, "back", "m:home")]]), id=c.from_user.id, bal=money(u["balance"]), orders=n)


@r.callback_query(F.data == "m:bal")
async def balance(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    u = await q("SELECT balance FROM users WHERE id=?", (c.from_user.id,), "one")
    rows = [[B(L, "topup", "tp:start")], [B(L, "dep_hist", "d:hist")]]
    if support_row(L):
        rows.append(support_row(L))
    rows.append([B(L, "back", "m:home")])
    await screen(c, "bal_text", L, kb(rows), bal=money(u["balance"]))


@r.callback_query(F.data == "d:hist")
async def dep_hist(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    ds = await q("SELECT * FROM deposits WHERE user_id=? AND status!='new' ORDER BY id DESC LIMIT 15",
                 (c.from_user.id,), "all")
    ps = await q("SELECT * FROM payment_intents WHERE user_id=? ORDER BY id DESC LIMIT 15",
                 (c.from_user.id,), "all")
    icon = {"pending": "⏳", "approved": "✅", "rejected": "❌", "paid": "✅", "expired": "⌛", "failed": "❌"}
    lines = []
    for d in ds:
        original = f"{float(d['paid_amount']):.2f} {d['currency']} → " if d['paid_amount'] is not None else ""
        lines.append((d["created"] or "", f"{icon.get(d['status'], '•')} Банк №{d['id']} — {original}{money(d['amount'])} — {d['created']}"))
    method_name = {"cryptobot": "Crypto Bot", "ton": "TON Keeper"}
    for item in ps:
        lines.append((item["created"] or "", f"{icon.get(item['status'], '•')} {method_name.get(item['method'], item['method'])} №{item['id']} — {money(item['usd_amount'])} — {item['status']}"))
    lines.sort(key=lambda x: x[0], reverse=True)
    lst = "\n".join(x[1] for x in lines[:15])
    await screen(c, "dep_hist", L, kb([[B(L, "back", "m:bal")]]), list=lst or plain(txt(L, "no_hist")))


@r.callback_query(F.data == "m:hist")
async def hist(c: CallbackQuery):
    L = await lang_of(c.from_user.id)
    os_ = await q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 20", (c.from_user.id,), "all")
    ic = {"active": "⏳", "done": "✅", "refunded": "↩️"}
    rows = [[btn(f"{ic.get(o['status'], '')} #{o['id']} {o['title']} × {o['qty']} — {money(o['total'])}",
                 f"h:{o['id']}")] for o in os_]
    rows.append([B(L, "back", "m:home")])
    await screen(c, "hist", L, kb(rows), list="" if os_ else plain(txt(L, "no_hist")))


# ───────────────────────── Поповнення ─────────────────────────
class Topup(StatesGroup):
    amount = State()
    receipt = State()
    crypto_amount = State()
    ton_amount = State()


async def crypto_api(method, params=None, token=None):
    """Crypto Pay API wrapper. Tokens are never included in logs or user-facing errors."""
    token = token or SET.get("cryptobot_token", "")
    if not token:
        raise RuntimeError("Crypto Bot API token is not configured")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"https://pay.crypt.bot/api/{method}",
            json=params or {},
            headers={"Crypto-Pay-API-Token": token},
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400 or not data.get("ok"):
                raise RuntimeError(f"Crypto Pay API error ({resp.status})")
            return data.get("result")


async def create_crypto_intent(uid, usd_amount):
    pid = await q(
        "INSERT INTO payment_intents(user_id,method,usd_amount,expected_amount,asset,status,created) "
        "VALUES(?,?,?,?,?,'creating',?)",
        (uid, "cryptobot", usd_amount, usd_amount, "USDT", now()),
    )
    payload = f"vh:{pid}:{uid}"
    try:
        invoice = await crypto_api("createInvoice", {
            "asset": "USDT",
            "amount": f"{usd_amount:.2f}",
            "description": f"{SHOP} balance top-up #{pid}",
            "payload": payload,
            "allow_comments": False,
            "allow_anonymous": False,
            "expires_in": 3600,
        })
        invoice_id = str(invoice["invoice_id"])
        pay_url = invoice.get("bot_invoice_url") or invoice.get("mini_app_invoice_url") or invoice.get("web_app_invoice_url")
        if not pay_url:
            raise RuntimeError("Crypto Pay API returned no invoice URL")
        await q("UPDATE payment_intents SET external_id=?,pay_url=?,payload=?,status='pending' WHERE id=?",
                (invoice_id, pay_url, payload, pid))
        return pid, pay_url
    except Exception:
        await q("UPDATE payment_intents SET status='failed' WHERE id=? AND status='creating'", (pid,))
        logging.exception("Could not create Crypto Pay invoice (intent %s)", pid)
        raise


async def create_ton_intent(uid, usd_amount):
    wallet = (SET.get("ton_wallet") or "").strip()
    key = (SET.get("toncenter_api_key") or "").strip()
    rate = parse_num(SET.get("ton_usd"), 0.000001, 1_000_000_000)
    if not wallet or not key or not rate:
        raise RuntimeError("TON payments are not configured")
    expected = (Decimal(str(usd_amount)) / Decimal(str(rate))).quantize(
        Decimal("0.000000001"), rounding=ROUND_HALF_UP
    )
    # TON links require nanotons as an integer. The displayed amount is rounded to 9 decimals.
    nano = int(expected * Decimal("1000000000"))
    expected = Decimal(nano) / Decimal("1000000000")
    pid = await q(
        "INSERT INTO payment_intents(user_id,method,usd_amount,expected_amount,asset,status,created) "
        "VALUES(?,?,?,?,?,'creating',?)",
        (uid, "ton", usd_amount, float(expected), "TON", now()),
    )
    payload = f"VH{pid}"
    pay_url = f"https://app.tonkeeper.com/transfer/{quote(wallet, safe='')}?amount={nano}&text={quote(payload)}"
    await q("UPDATE payment_intents SET pay_url=?,payload=?,status='pending' WHERE id=?",
            (pay_url, payload, pid))
    return pid, pay_url, float(expected)


async def settle_payment_intent(payment_id, tx_hash=None, bot=None):
    """Atomic once-only credit; duplicate webhook/poll/transactions cannot credit twice."""
    credited = None
    async with conn() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "UPDATE payment_intents SET status='paid',tx_hash=?,paid_at=? "
            "WHERE id=? AND status='pending'",
            (tx_hash, now(), payment_id),
        )
        if cur.rowcount != 1:
            await db.rollback()
            return False
        row = await (await db.execute("SELECT user_id,usd_amount FROM payment_intents WHERE id=?", (payment_id,))).fetchone()
        await db.execute("UPDATE users SET balance=ROUND(balance+?,2) WHERE id=?", (row["usd_amount"], row["user_id"]))
        await db.commit()
        credited = (int(row["user_id"]), float(row["usd_amount"]))
    if bot and credited:
        try:
            await bot.send_message(
                credited[0],
                f"✅ Пополнение успешно! На баланс зачислено <b>{money(credited[1])}</b>.",
                reply_markup=kb([[btn("💰 Мій баланс", "m:bal")]]),
            )
        except Exception:
            logging.exception("Could not send payment confirmation to user %s", credited[0])
    return True


async def crypto_pending(bot, only_id=None):
    token = (SET.get("cryptobot_token") or "").strip()
    if not token:
        return
    sql = "SELECT * FROM payment_intents WHERE method='cryptobot' AND status='pending' AND external_id IS NOT NULL"
    args = ()
    if only_id is not None:
        sql += " AND id=?"
        args = (only_id,)
    pending = await q(sql + " ORDER BY id LIMIT 100", args, "all")
    if not pending:
        return
    ids = ",".join(str(x["external_id"]) for x in pending)
    try:
        invoices = await crypto_api("getInvoices", {"invoice_ids": ids, "count": len(pending)}, token=token)
    except Exception:
        logging.warning("Crypto Pay invoice check temporarily failed", exc_info=True)
        return
    by_id = {str(x.get("invoice_id")): x for x in (invoices or [])}
    for item in pending:
        inv = by_id.get(str(item["external_id"]))
        if not inv:
            continue
        status = inv.get("status")
        if status == "paid":
            await settle_payment_intent(item["id"], f"cryptobot:{item['external_id']}", bot)
        elif status == "expired":
            await q("UPDATE payment_intents SET status='expired' WHERE id=? AND status='pending'", (item["id"],))


def ton_message_text(in_msg):
    if not in_msg:
        return ""
    value = in_msg.get("message")
    if isinstance(value, str) and value.strip():
        return value.replace("\x00", "").strip()
    msg_data = in_msg.get("msg_data") or {}
    # Some TON Center responses return decoded text directly; raw bodies are intentionally
    # not guessed/decoded as comments because their binary format may be a non-text payload.
    value = msg_data.get("text")
    return value.strip() if isinstance(value, str) else ""


async def ton_transactions():
    wallet = (SET.get("ton_wallet") or "").strip()
    key = (SET.get("toncenter_api_key") or "").strip()
    if not wallet or not key:
        return []
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            "https://toncenter.com/api/v2/getTransactions",
            params={"address": wallet, "limit": 100, "archival": "true"},
            headers={"X-API-Key": key},
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400 or not data.get("ok"):
                raise RuntimeError(f"TON Center API error ({resp.status})")
            return data.get("result") or []


async def ton_pending(bot, only_id=None):
    if not SET.get("ton_wallet") or not SET.get("toncenter_api_key"):
        return
    sql = "SELECT * FROM payment_intents WHERE method='ton' AND status='pending'"
    args = ()
    if only_id is not None:
        sql += " AND id=?"
        args = (only_id,)
    pending = await q(sql + " ORDER BY id LIMIT 200", args, "all")
    if not pending:
        return
    try:
        transactions = await ton_transactions()
    except Exception:
        logging.warning("TON Center transaction check temporarily failed", exc_info=True)
        return
    intent_by_comment = {str(x["payload"]): x for x in pending if x["payload"]}
    for tx in transactions:
        msg = tx.get("in_msg") or {}
        comment = ton_message_text(msg)
        intent = intent_by_comment.get(comment)
        if not intent:
            continue
        try:
            received_nano = int(msg.get("value") or 0)
            received_ton = Decimal(received_nano) / Decimal("1000000000")
            needed_ton = Decimal(str(intent["expected_amount"]))
        except Exception:
            continue
        if received_ton < needed_ton:
            continue
        tx_hash = ((tx.get("transaction_id") or {}).get("hash") or msg.get("hash") or "ton")
        await settle_payment_intent(intent["id"], f"ton:{tx_hash}", bot)


async def payment_watchdog(bot):
    """Poll pending invoices/TON transfers so crediting also works after bot restarts."""
    while True:
        try:
            await crypto_pending(bot)
        except Exception:
            logging.exception("Crypto payment worker iteration failed")
        try:
            await ton_pending(bot)
        except Exception:
            logging.exception("TON payment worker iteration failed")
        await asyncio.sleep(12)


@r.callback_query(F.data == "tp:start")
async def tp_start(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = []
    reqs = await q("SELECT * FROM requisites", fetch="all")
    uah_rate = parse_num(SET.get("uah_per_usd"), 0.01, 1_000_000)
    ton_rate = parse_num(SET.get("ton_usd"), 0.000001, 1_000_000_000)
    if SET.get("cryptobot_token"):
        rows.append([btn("🤖 Crypto Bot · USDT", "tp:crypto")])
    if SET.get("ton_wallet") and SET.get("toncenter_api_key") and ton_rate:
        rows.append([btn("💎 TON Keeper · TON", "tp:ton")])
    if reqs and uah_rate:
        rows.append([btn("🏦 Банковская карта · UAH", "tp:uah")])
    rows.append([B(await lang_of(c.from_user.id), "back", "m:bal")])
    if len(rows) == 1:
        return await show(c, "💳 <b>Пополнение временно недоступно.</b> Администратор должен настроить способы оплаты и курсы.", kb(rows))
    await show(c, "💳 <b>Пополнение баланса</b>\n\nВсе цены и баланс в USD. Выберите способ оплаты:", kb(rows))


@r.callback_query(F.data == "tp:uah")
async def tp_uah(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rate = parse_num(SET.get("uah_per_usd"), 0.01, 1_000_000)
    reqs = await q("SELECT * FROM requisites", fetch="all")
    if not reqs or not rate:
        return await c.answer("Банковское пополнение не настроено.", show_alert=True)
    rows = [[mk_btn(f"🏦 {x['bank']}" if not x["emoji_id"] else x["bank"], f"tp:r:{x['id']}", x["emoji_id"])] for x in reqs]
    rows.append([B(await lang_of(c.from_user.id), "back", "tp:start")])
    await show(c, f"🏦 <b>Оплата гривной</b>\n\nКурс администратора: 1 USD = {rate:.4f} UAH.\nБаланс будет зачислен в USD.", kb(rows))


@r.callback_query(F.data.startswith("tp:r:"))
async def tp_req(c: CallbackQuery, state: FSMContext):
    rate = parse_num(SET.get("uah_per_usd"), 0.01, 1_000_000)
    if not rate:
        return await c.answer("Администратор ещё не настроил курс UAH/USD.", show_alert=True)
    req_id = int(c.data.split(":")[2])
    if not await q("SELECT id FROM requisites WHERE id=?", (req_id,), "one"):
        return await c.answer("Реквизиты не найдены.", show_alert=True)
    await state.set_state(Topup.amount)
    await state.update_data(req=req_id)
    await show(c, f"Введите сумму оплаты в UAH (минимум {MIN_TOPUP:.2f} UAH).\nКурс: 1 USD = {rate:.4f} UAH. На баланс поступит сумма в USD.",
               kb([[B(await lang_of(c.from_user.id), "cancel", "tp:start")]]))


@r.message(Topup.amount)
async def tp_amount(m: Message, state: FSMContext):
    L = await lang_of(m.from_user.id)
    paid_amount = parse_amount(m.text, MIN_TOPUP)
    if paid_amount is None:
        return await m.answer(f"Введите корректную сумму в UAH, не менше {MIN_TOPUP:.2f}.")
    rate = parse_num(SET.get("uah_per_usd"), 0.01, 1_000_000)
    if not rate:
        await state.clear()
        return await m.answer("Пополнение в UAH временно отключено: администратор должен настроить курс.")
    credited_usd = round(paid_amount / rate, 2)
    if credited_usd < MIN_TOPUP_USD:
        return await m.answer(f"Эта сумма меньше минимума {money(MIN_TOPUP_USD)} после конвертации. Введите большую сумму UAH.")
    req = await q("SELECT * FROM requisites WHERE id=?", ((await state.get_data())["req"],), "one")
    await state.clear()
    if not req:
        return await m.answer(plain(txt(L, "no_req")))
    dep = await q(
        "INSERT INTO deposits(user_id,amount,req_id,status,created,currency,paid_amount) VALUES(?,?,?,?,?,?,?)",
        (m.from_user.id, credited_usd, req["id"], "new", now(), "UAH", paid_amount),
    )
    await screen(m, "topup_details", L,
                 kb([[B(L, "send_receipt", f"tp:s:{dep}")], [B(L, "cancel", "m:bal")]]),
                 bank=html.escape(req["bank"]), card=html.escape(req["card"]),
                 holder=html.escape(req["holder"] or "—"),
                 amount=html.escape(f"{paid_amount:.2f} UAH (на баланс: {money(credited_usd)})"), id=dep)


@r.callback_query(F.data == "tp:crypto")
async def tp_crypto(c: CallbackQuery, state: FSMContext):
    await state.clear()
    if not SET.get("cryptobot_token"):
        return await c.answer("Crypto Bot не настроен администратором.", show_alert=True)
    await state.set_state(Topup.crypto_amount)
    await show(c, f"Введите сумму пополнения в USD (минимум {money(MIN_TOPUP_USD)}). Оплата будет через Crypto Bot в USDT.",
               kb([[B(await lang_of(c.from_user.id), "cancel", "tp:start")]]))


@r.message(Topup.crypto_amount)
async def tp_crypto_amount(m: Message, state: FSMContext):
    usd = parse_amount(m.text, MIN_TOPUP_USD)
    if usd is None:
        return await m.answer(f"Введите сумму в USD не меньше {money(MIN_TOPUP_USD)}.")
    await state.clear()
    try:
        pid, pay_url = await create_crypto_intent(m.from_user.id, usd)
    except Exception:
        return await m.answer("Не удалось создать счёт Crypto Bot. Проверьте настройки или попробуйте позже.",
                              reply_markup=kb([[btn("⬅️ К балансу", "m:bal")]]))
    await m.answer(
        f"🤖 <b>Рахунок Crypto Bot №{pid}</b>\n\nДо оплати: <b>{money(usd)} USDT</b>\n"
        "После успешной оплаты баланс будет автоматически зачислен в USD. Счёт действует 60 минут.",
        reply_markup=kb([[btn("💎 Оплатить в Crypto Bot", url=pay_url)], [btn("🔄 Проверить оплату", f"tp:check:{pid}")],
                         [btn("⬅️ К балансу", "m:bal")]])
    )


@r.callback_query(F.data == "tp:ton")
async def tp_ton(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rate = parse_num(SET.get("ton_usd"), 0.000001, 1_000_000_000)
    if not SET.get("ton_wallet") or not SET.get("toncenter_api_key") or not rate:
        return await c.answer("TON-оплата не настроена администратором.", show_alert=True)
    await state.set_state(Topup.ton_amount)
    await show(c, f"Введите сумму пополнения в USD (минимум {money(MIN_TOPUP_USD)}).\nКурс администратора: 1 TON = ${rate:.6f}.",
               kb([[B(await lang_of(c.from_user.id), "cancel", "tp:start")]]))


@r.message(Topup.ton_amount)
async def tp_ton_amount(m: Message, state: FSMContext):
    usd = parse_amount(m.text, MIN_TOPUP_USD)
    if usd is None:
        return await m.answer(f"Введите сумму в USD не меньше {money(MIN_TOPUP_USD)}.")
    await state.clear()
    try:
        pid, pay_url, ton_amount = await create_ton_intent(m.from_user.id, usd)
    except Exception:
        return await m.answer("Не удалось создать TON-платёж. Проверьте платёжные настройки или попробуйте позже.",
                              reply_markup=kb([[btn("⬅️ К балансу", "m:bal")]]))
    await m.answer(
        f"💎 <b>TON-поповнення №{pid}</b>\n\nДо оплати: <b>{ton_amount:.9f} TON</b>\n"
        f"Зачисление на баланс: <b>{money(usd)}</b>\nКомментарий платежа должен быть <code>VH{pid}</code> — он добавляется автоматически в ссылке Tonkeeper.\n"
        "После поступления перевода баланс будет зачислен автоматически.",
        reply_markup=kb([[btn("💎 Открыть Tonkeeper", url=pay_url)], [btn("🔄 Проверить оплату", f"tp:check:{pid}")],
                         [btn("⬅️ К балансу", "m:bal")]])
    )


@r.callback_query(F.data.startswith("tp:check:"))
async def tp_check(c: CallbackQuery, bot: Bot):
    pid = int(c.data.split(":")[2])
    item = await q("SELECT * FROM payment_intents WHERE id=? AND user_id=?", (pid, c.from_user.id), "one")
    if not item:
        return await c.answer("Платёж не найден.", show_alert=True)
    if item["status"] == "pending":
        if item["method"] == "cryptobot":
            await crypto_pending(bot, pid)
        elif item["method"] == "ton":
            await ton_pending(bot, pid)
        item = await q("SELECT * FROM payment_intents WHERE id=?", (pid,), "one")
    if item["status"] == "paid":
        return await c.answer(f"Оплата подтверждена: {money(item['usd_amount'])} зачислено.", show_alert=True)
    if item["status"] == "expired":
        return await c.answer("Счёт просрочен. Создайте новый платёж.", show_alert=True)
    if item["status"] == "failed":
        return await c.answer("Счёт не создан. Создайте новый платёж или обратитесь в поддержку.", show_alert=True)
    await c.answer("Платёж пока не найден. Если уже оплатили, дождитесь подтверждения сети и проверьте ещё раз.", show_alert=True)


@r.callback_query(F.data.startswith("tp:s:"))
async def tp_send(c: CallbackQuery, state: FSMContext):
    L = await lang_of(c.from_user.id)
    await state.set_state(Topup.receipt)
    await state.update_data(dep=int(c.data.split(":")[2]))
    await screen(c, "receipt_prompt", L, kb([[B(L, "cancel", "m:bal")]]))


@r.message(Topup.receipt)
async def tp_receipt(m: Message, state: FSMContext, bot: Bot):
    L = await lang_of(m.from_user.id)
    file_id = m.photo[-1].file_id if m.photo else (m.document.file_id if m.document else None)
    if not file_id:
        return await m.answer(plain(txt(L, "receipt_bad")))
    dep_id = (await state.get_data())["dep"]
    await state.clear()
    d = await q("SELECT * FROM deposits WHERE id=? AND user_id=?", (dep_id, m.from_user.id), "one")
    if not d or d["status"] not in ("new", "pending"):
        return
    await q("UPDATE deposits SET status='pending', file_id=? WHERE id=?", (file_id, dep_id))
    req = await q("SELECT * FROM requisites WHERE id=?", (d["req_id"],), "one")
    caption = (f"🧾 <b>Заявка на пополнение №{dep_id}</b>\n"
               f"👤 {html.escape(m.from_user.full_name)} (<code>{m.from_user.id}</code>)"
               f"{' @' + m.from_user.username if m.from_user.username else ''}\n"
               f"💳 Оплачено: {float(d['paid_amount'] or 0):.2f} {d['currency'] or 'UAH'}\n"
               f"💵 Зарахувати: {money(d['amount'])}\n🏦 {html.escape(req['bank']) if req else '—'}")
    markup = kb([[btn("✅ Подтвердить", f"a:dep:ok:{dep_id}"), btn("❌ Отклонить", f"a:dep:no:{dep_id}")]])
    for sid in await staff_ids():
        try:
            if m.photo:
                await bot.send_photo(sid, file_id, caption=caption, reply_markup=markup)
            else:
                await bot.send_document(sid, file_id, caption=caption, reply_markup=markup)
        except Exception:
            pass
    await screen(m, "receipt_sent", L, kb([[B(L, "back", "m:home")]]))


# ───────────────────────── Адмін-панель ─────────────────────────
async def admin_home(ev):
    role = await role_of(ev.from_user.id)
    rows = [[btn("🧾 Заявки на пополнение", "a:deps"), btn("📥 Заявки на выдачу", "a:orders")],
            [btn("📦 Товары и склад", "a:cats")]]
    if role in ("owner", "admin"):
        rows += [[btn("⭐ Telegram Stars / 💎 Premium", "f:tg")],
             [btn("🎨 Оформление (тексты/кнопки/медиа)", "f:ds")],
                 [btn("📊 Статистика", "f:stats"), btn("💰 Выдать баланс", "f:give")],
                 [btn("🔎 Пользователь / бан", "f:usr")],
                 [btn("💳 Реквизиты", "f:reqs"), btn("📢 Обяз. подписка", "f:subs")],
                 [btn("👥 Персонал", "f:staff"), btn("📨 Рассылка", "f:bc")],
                 [btn("💳 Платежи / курсы", "f:payments")],
                 [btn("⚙️ Настройки / поддержка", "f:set")]]
    rows.append([btn("⬅️ В меню", "m:home")])
    title = {"owner": "👑 Владелец", "admin": "🛡 Главный админ", "mod": "🧰 Модератор"}[role]
    await show(ev, f"🛠 <b>Админ-панель {SHOP}</b>\nРоль: {title}", kb(rows))


@r.callback_query(F.data == "a:home", Staff())
async def a_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await admin_home(c)


def back_admin():
    return kb([[btn("⬅️ Админ-панель", "a:home")]])


# ---- Заявки на пополнение
@r.callback_query(F.data == "a:deps", Staff())
async def a_deps(c: CallbackQuery):
    ds = await q("SELECT * FROM deposits WHERE status='pending' ORDER BY id", fetch="all")
    if not ds:
        return await show(c, "Нет ожидающих заявок.", back_admin())
    rows = [[btn(f"№{d['id']} — {money(d['amount'])} USD ({float(d['paid_amount'] or 0):.2f} {d['currency'] or 'UAH'}) — user {d['user_id']}", f"a:dv:{d['id']}")] for d in ds]
    rows.append([btn("⬅️ Админ-панель", "a:home")])
    await show(c, "🧾 <b>Ожидают подтверждения:</b>", kb(rows))


@r.callback_query(F.data.startswith("a:dv:"), Staff())
async def a_dv(c: CallbackQuery, bot: Bot):
    d = await q("SELECT * FROM deposits WHERE id=?", (int(c.data.split(":")[2]),), "one")
    if not d or not d["file_id"]:
        return await c.answer("Не найдено", show_alert=True)
    cap = (f"🧾 Заявка №{d['id']}\n💳 Оплачено: {float(d['paid_amount'] or 0):.2f} {d['currency'] or 'UAH'}\n"
           f"💵 Зарахування: {money(d['amount'])}\n👤 user <code>{d['user_id']}</code>")
    markup = kb([[btn("✅ Подтвердить", f"a:dep:ok:{d['id']}"), btn("❌ Отклонить", f"a:dep:no:{d['id']}")]])
    try:
        await bot.send_photo(c.from_user.id, d["file_id"], caption=cap, reply_markup=markup)
    except Exception:
        await bot.send_document(c.from_user.id, d["file_id"], caption=cap, reply_markup=markup)
    await c.answer()


@r.callback_query(F.data.startswith("a:dep:"), Staff())
async def a_dep_decide(c: CallbackQuery, bot: Bot):
    _, _, act, did = c.data.split(":")
    did = int(did)
    new = "approved" if act == "ok" else "rejected"
    async with conn() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("UPDATE deposits SET status=? WHERE id=? AND status='pending'", (new, did))
        if cur.rowcount == 0:
            await db.rollback()
            return await c.answer("Уже обработана", show_alert=True)
        d = await (await db.execute("SELECT * FROM deposits WHERE id=?", (did,))).fetchone()
        if act == "ok":
            await db.execute("UPDATE users SET balance=ROUND(balance+?,2) WHERE id=?", (d["amount"], d["user_id"]))
        await db.commit()
    L = await lang_of(d["user_id"])
    try:
        if act == "ok":
            await screen_to(bot, d["user_id"], "dep_ok", L, amount=money(d["amount"]))
        else:
            await screen_to(bot, d["user_id"], "dep_no", L, id=did)
    except Exception:
        pass
    mark = "✅ ПОДТВЕРЖДЕНО" if act == "ok" else "❌ ОТКЛОНЕНО"
    try:
        await c.message.edit_caption(caption=f"{html.escape(c.message.caption or '')}\n\n{mark} — {html.escape(c.from_user.full_name)}")
    except Exception:
        pass
    await c.answer(mark)


# ---- Заявки на выдачу (ручные заказы)
class Fulfill(StatesGroup):
    msg = State()


@r.callback_query(F.data == "a:orders", Staff())
async def a_orders(c: CallbackQuery):
    os_ = await q("SELECT * FROM orders WHERE status='active' AND pending=1 ORDER BY id", fetch="all")
    if not os_:
        return await show(c, "Нет заявок на выдачу.", back_admin())
    rows = [[btn(f"#{o['id']} {o['title']} × {o['qty']} — user {o['user_id']}", f"a:ov:{o['id']}")] for o in os_]
    rows.append([btn("⬅️ Админ-панель", "a:home")])
    await show(c, "📥 <b>Ожидают выдачи:</b>", kb(rows))


@r.callback_query(F.data.startswith("a:ov:"), Staff())
async def a_ov(c: CallbackQuery):
    o = await q("SELECT * FROM orders WHERE id=?", (int(c.data.split(":")[2]),), "one")
    if not o or o["status"] != "active" or not o["pending"]:
        return await c.answer("Заявка закрыта", show_alert=True)
    text, mk = await request_card(o)
    await c.message.answer(text, reply_markup=mk)
    await c.answer()


@r.callback_query(F.data.startswith("a:fl:"), Staff())
async def a_fulfill(c: CallbackQuery, state: FSMContext):
    oid = int(c.data.split(":")[2])
    uid = c.from_user.id
    full = await role_of(uid) in ("owner", "admin")
    n = await qr("UPDATE orders SET claimed_by=? WHERE id=? AND status='active' AND pending=1 "
                 "AND (claimed_by IS NULL OR claimed_by=?)", (uid, oid, uid))
    if not n and full:
        n = await qr("UPDATE orders SET claimed_by=? WHERE id=? AND status='active' AND pending=1", (uid, oid))
    if not n:
        return await c.answer("Заявка уже взята другим админом или закрыта", show_alert=True)
    o = await q("SELECT * FROM orders WHERE id=?", (oid,), "one")
    steps = json.loads(o["steps"] or "[]")
    await state.set_state(Fulfill.msg)
    await state.update_data(oid=oid)
    await c.message.answer(
        f"✉️ Заказ #{oid}, этап «{html.escape(steps[o['step']][0])}».\n\nОтправьте сообщение для покупателя "
        "(номер, аккаунт, код — текст, фото или файл). Оно уйдёт как есть.",
        reply_markup=kb([[btn("✖️ Отмена", f"a:fx:{oid}")]]))
    await c.answer()


@r.callback_query(F.data.startswith("a:fx:"), Staff())
async def a_fx(c: CallbackQuery, state: FSMContext):
    await qr("UPDATE orders SET claimed_by=NULL WHERE id=? AND claimed_by=?", (int(c.data.split(":")[2]), c.from_user.id))
    await state.clear()
    await show(c, "Отменено. Заявка снова свободна.", back_admin())


@r.message(Fulfill.msg, Staff())
async def fulfill_in(m: Message, state: FSMContext, bot: Bot):
    oid = (await state.get_data())["oid"]
    o = await q("SELECT * FROM orders WHERE id=?", (oid,), "one")
    if not o or o["status"] != "active" or not o["pending"] or o["claimed_by"] not in (None, m.from_user.id):
        await state.clear()
        return await m.answer("Заявка уже закрыта.", reply_markup=back_admin())
    L = await lang_of(o["user_id"])
    try:
        await bot.copy_message(o["user_id"], m.chat.id, m.message_id)
    except Exception as e:
        return await m.answer(f"❌ Не удалось отправить покупателю: {e}")
    if o["kind"] == "code":  # код отправлен; заказ остаётся открытым для повторного запроса
        items = (o["items"] or "") + "\n" + f"Код: {m.text or m.caption or '[файл]'}"
        await q("UPDATE orders SET pending=0, claimed_by=NULL, items=? WHERE id=?", (items, oid))
        await state.clear()
        await screen_to(bot, o["user_id"], "code_sent", L, code_kb(L, oid), id=oid)
        return await m.answer("✅ Код отправлен покупателю. Повторно он сможет запросить через 2 минуты.",
                              reply_markup=back_admin())
    steps = json.loads(o["steps"] or "[]")
    nxt = o["step"] + 1
    note = f"{steps[o['step']][0]}: {m.text or m.caption or '[файл]'}"
    items = ((o["items"] + "\n") if o["items"] else "") + note
    done = nxt >= len(steps)
    await q("UPDATE orders SET step=?, pending=0, claimed_by=NULL, items=?, status=? WHERE id=?",
            (nxt, items, "done" if done else "active", oid))
    await state.clear()
    if done:
        await screen_to(bot, o["user_id"], "order_done", L, kb([support_row(L)]) if support_row(L) else None, id=oid)
    else:
        await screen_to(bot, o["user_id"], "order_next", L, order_kb(L, oid, steps, nxt), id=oid)
    await m.answer("✅ Отправлено покупателю" + (" — заказ завершён." if done else
                   ". Покупатель получил кнопку следующего этапа."), reply_markup=back_admin())


@r.callback_query(F.data.startswith("a:rf:"), Staff())
async def a_refund(c: CallbackQuery, bot: Bot):
    if await role_of(c.from_user.id) not in ("owner", "admin"):
        return await c.answer("Возврат доступен только админам", show_alert=True)
    oid = int(c.data.split(":")[2])
    if not await qr("UPDATE orders SET status='refunded', pending=0, claimed_by=NULL WHERE id=? AND status='active'", (oid,)):
        return await c.answer("Заказ уже закрыт", show_alert=True)
    o = await q("SELECT * FROM orders WHERE id=?", (oid,), "one")
    await q("UPDATE users SET balance=ROUND(balance+?,2) WHERE id=?", (o["total"], o["user_id"]))
    try:
        await screen_to(bot, o["user_id"], "order_refund", await lang_of(o["user_id"]), id=oid, total=money(o["total"]))
    except Exception:
        pass
    await show(c, f"↩️ Заказ #{oid} возвращён ({money(o['total'])}).", back_admin())


# ---- Товары и склад
class AddStock(StatesGroup):
    data = State()


class NewCat(StatesGroup):
    name = State()


class NewProd(StatesGroup):
    title = State()
    price = State()


@r.callback_query(F.data == "a:cats", Staff())
async def a_cats(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[mk_btn(x["name"], f"a:cat:{x['id']}", x["emoji_id"])] for x in await q("SELECT * FROM categories", fetch="all")]
    if await role_of(c.from_user.id) in ("owner", "admin"):
        rows.append([btn("➕ Категория", "f:addcat")])
    rows.append([btn("⬅️ Админ-панель", "a:home")])
    await show(c, "📦 <b>Категории:</b>", kb(rows))


@r.callback_query(F.data.startswith("a:cat:"), Staff())
async def a_cat(c: CallbackQuery):
    parts = c.data.split(":")
    cid = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    full = await role_of(c.from_user.id) in ("owner", "admin")
    prods = await q("""SELECT p.*, (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) AS n
                       FROM products p WHERE p.cat_id=? AND p.active=1 ORDER BY p.id""", (cid,), "all")
    per = 30
    rows = [[mk_btn(f"{x['title']} | {money(x['price'])} | " + ("🔁 ручн." if is_manual(x) else f"{x['n']} шт."),
                    f"a:p:{x['id']}", x["emoji_id"])] for x in prods[page * per:(page + 1) * per]]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"a:cat:{cid}:{page - 1}"))
    if (page + 1) * per < len(prods):
        nav.append(btn("▶️", f"a:cat:{cid}:{page + 1}"))
    if nav:
        rows.append(nav)
    if full:
        rows += [[btn("➕ Товар", f"f:addprod:{cid}")],
                 [btn("✏️ Название", f"f:cn:{cid}"), btn("🖼 Медиа", f"f:cm:{cid}"), btn("🗑 Медиа", f"f:cmd:{cid}")],
                 [btn("🗑 Удалить категорию", f"f:cdel:{cid}")]]
    rows.append([btn("⬅️ Назад", "a:cats")])
    await show(c, f"Товары категории (всего {len(prods)}):", kb(rows))


@r.callback_query(F.data.startswith("a:p:"), Staff())
async def a_prod(c: CallbackQuery, state: FSMContext):
    await state.clear()
    pid = int(c.data.split(":")[2])
    p = await q("SELECT * FROM products WHERE id=?", (pid,), "one")
    if not p:
        return await show(c, "Товар не найден.", back_admin())
    n = (await q("SELECT COUNT(*) AS n FROM stock WHERE product_id=? AND sold=0", (pid,), "one"))["n"]
    steps = json.loads(p["steps"] or "[]")
    mode = ("🔁 Ручная выдача: " + " → ".join(html.escape(s[0]) for s in steps)) if steps else "📦 Автовыдача со склада"
    rows = [[btn("➕ Добавить аккаунты на склад", f"a:st:{pid}")]]
    if await role_of(c.from_user.id) in ("owner", "admin"):
        rows += [[btn("✏️ Название", f"f:pn:{pid}"), btn("💲 Цена", f"f:price:{pid}")],
                 [btn("📦 Установить количество", f"f:qty:{pid}")],
                 [btn("📱 Редактировать номера на складе", f"a:stocks:{pid}:0")],
                 [btn("📝 Описание", f"f:pd:{pid}"), btn("🖼 Медиа", f"f:pm:{pid}"), btn("🗑 Медиа", f"f:pmd:{pid}")],
                 [btn("🔁 Этапы выдачи", f"f:ps:{pid}"), btn("📦 Автовыдача", f"f:psc:{pid}")],
                 [btn("🧹 Очистить склад", f"f:stc:{pid}"), btn("🗑 Удалить товар", f"f:delp:{pid}")]]
    rows.append([btn("⬅️ Назад", f"a:cat:{p['cat_id']}")])
    shown_n = p["stock_qty"] if is_manual(p) and p["stock_qty"] >= 0 else n
    await show(c, f"<b>{html.escape(p['title'])}</b>\nЦена за единицу: {money(p['price'])}\nНа складе: {shown_n}\n{mode}\n"
                  f"Медиа: {p['mtype'] or 'нет'}", kb(rows))


class StockStep(StatesGroup):
    login = State()
    pw = State()


PW_PROMPT = ("🔑 <b>Пароль</b> для <code>{login}</code>\n\n"
             "• напишите сам пароль — покупатель получит логин + пароль;\n"
             "• <code>-</code> — если пароля и кода нет (выдаётся только логин);\n"
             "• <code>+</code> — если это номер и покупатель будет запрашивать код.")


async def stock_conflict(pid, modes):
    """Номера с кодом нельзя смешивать с обычными товарами в одной позиции."""
    row = await q("SELECT mode FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT 1", (pid,), "one")
    kinds = {md == "code" for md in modes}
    if row:
        kinds.add(row["mode"] == "code")
    return len(kinds) > 1


MIX_MSG = ("⚠️ В одном товаре нельзя смешивать «номера с кодом» и обычные аккаунты. "
           "Создайте отдельный товар (или очистите склад этого).")


def stock_mode(pw):
    if pw == "+":
        return "code", None
    if pw == "-":
        return "none", None
    return "pw", pw


MODE_NAME = {"code": "номер (код по запросу)", "none": "только логин", "pw": "логин + пароль"}


async def show_stock_list(ev, pid, page=0):
    page = max(0, int(page))
    product = await q("SELECT id,title FROM products WHERE id=? AND active=1", (pid,), "one")
    if not product:
        if isinstance(ev, CallbackQuery):
            return await ev.answer("Товар не найден.", show_alert=True)
        return
    total = (await q("SELECT COUNT(*) n FROM stock WHERE product_id=? AND sold=0", (pid,), "one"))["n"]
    per = 10
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages - 1)
    items = await q("SELECT id,login,mode FROM stock WHERE product_id=? AND sold=0 ORDER BY id DESC LIMIT ? OFFSET ?",
                    (pid, per, page * per), "all")
    rows = []
    for item in items:
        login = str(item["login"] or "—").replace("\n", " ")[:34]
        icon = "📲" if item["mode"] == "code" else ("🔐" if item["mode"] == "pw" else "📱")
        rows.append([btn(f"{icon} #{item['id']} · {login}", f"a:stockitem:{item['id']}")])
    if not items:
        rows.append([btn("Склад пуст — добавить номера", f"a:st:{pid}")])
    if pages > 1:
        rows.append([btn("◀️", f"a:stocks:{pid}:{max(0, page-1)}"),
                     btn(f"{page+1}/{pages}", "noop"),
                     btn("▶️", f"a:stocks:{pid}:{min(pages-1, page+1)}")])
    rows.append([btn("➕ Додати номери", f"a:st:{pid}")])
    rows.append([btn("⬅️ К товару", f"a:p:{pid}")])
    await show(ev, f"📱 <b>Склад: {html.escape(product['title'])}</b>\nНепроданных позиций: {total}\nВыберите номер для редактирования:", kb(rows))


@r.callback_query(F.data.startswith("a:stocks:"), Full())
async def a_stocks(c: CallbackQuery):
    parts = c.data.split(":")
    pid, page = int(parts[2]), max(0, int(parts[3]) if len(parts) > 3 else 0)
    await show_stock_list(c, pid, page)


@r.callback_query(F.data.startswith("a:stockitem:"), Full())
async def a_stock_item(c: CallbackQuery):
    sid = int(c.data.split(":")[2])
    item = await q("SELECT s.*,p.title FROM stock s JOIN products p ON p.id=s.product_id WHERE s.id=? AND s.sold=0",
                   (sid,), "one")
    if not item:
        return await c.answer("Позиция не найдена или уже продана.", show_alert=True)
    login = html.escape(str(item["login"] or "—"))
    if item["mode"] == "code":
        pass_text = "код запрашивается отдельно"
    elif item["mode"] == "pw":
        pass_text = html.escape(str(item["pw"] or "—"))
    else:
        pass_text = "без пароля"
    text = (f"📱 <b>Позиция на складе #{sid}</b>\nТовар: {html.escape(item['title'])}\n"
            f"Номер / логин: <code>{login}</code>\nПароль / режим: <code>{pass_text}</code>")
    rows = [[btn("✏️ Изменить номер / логин", f"f:stocklogin:{sid}")],
            [btn("🔑 Изменить пароль / режим", f"f:stockpw:{sid}")],
            [btn("🗑 Удалить позицию", f"f:stockdel:{sid}:{item['product_id']}:0")],
            [btn("⬅️ К списку номеров", f"a:stocks:{item['product_id']}:0")]]
    await show(c, text, kb(rows))


@r.callback_query(F.data.startswith("f:stocklogin:"), Full())
async def f_stock_login(c: CallbackQuery, state: FSMContext):
    sid = int(c.data.split(":")[2])
    item = await q("SELECT id,product_id,login FROM stock WHERE id=? AND sold=0", (sid,), "one")
    if not item:
        return await c.answer("Позиция не найдена или уже продана.", show_alert=True)
    await start_edit(c, state, "stock_login", "Отправьте новый номер телефона / логин:",
                     f"a:stockitem:{sid}", id=sid, pid=item["product_id"])


@r.callback_query(F.data.startswith("f:stockpw:"), Full())
async def f_stock_pw(c: CallbackQuery, state: FSMContext):
    sid = int(c.data.split(":")[2])
    item = await q("SELECT id,product_id FROM stock WHERE id=? AND sold=0", (sid,), "one")
    if not item:
        return await c.answer("Позиция не найдена или уже продана.", show_alert=True)
    await start_edit(c, state, "stock_pw", "Отправьте новый пароль, или «-» (без пароля), или «+» (код запрашивается отдельно):",
                     f"a:stockitem:{sid}", id=sid, pid=item["product_id"])


@r.callback_query(F.data.startswith("f:stockdel:"), Full())
async def f_stock_del_ask(c: CallbackQuery):
    _, _, sid, pid, page = c.data.split(":")
    sid, pid, page = int(sid), int(pid), int(page)
    item = await q("SELECT login FROM stock WHERE id=? AND product_id=? AND sold=0", (sid, pid), "one")
    if not item:
        return await c.answer("Позиция не найдена или уже продана.", show_alert=True)
    shown = html.escape(str(item["login"] or "—"))
    await show(c, f"Удалить со склада номер <code>{shown}</code>? Это действие необратимо.",
               kb([[btn("✅ Так, видалити", f"f:stockdely:{sid}:{pid}:{page}"),
                    btn("✖️ Скасувати", f"a:stockitem:{sid}")]]))


@r.callback_query(F.data.startswith("f:stockdely:"), Full())
async def f_stock_del(c: CallbackQuery):
    _, _, sid, pid, page = c.data.split(":")
    sid, pid, page = int(sid), int(pid), max(0, int(page))
    deleted = await qr("DELETE FROM stock WHERE id=? AND product_id=? AND sold=0", (sid, pid))
    if not deleted:
        return await c.answer("Позиция уже удалена или продана.", show_alert=True)
    await c.answer("Номер удалён со склада.")
    await show_stock_list(c, pid, page)


@r.callback_query(F.data.startswith("a:st:"), Staff())
async def a_stock(c: CallbackQuery):
    pid = int(c.data.split(":")[2])
    await show(c, "➕ <b>Добавление на склад</b>\n\nВыберите способ:",
               kb([[btn("📝 Пошагово (логин → пароль)", f"a:sm:{pid}:1")],
                   [btn("📄 Списком / .txt файлом", f"a:sm:{pid}:2")],
                   [btn("⬅️ К товару", f"a:p:{pid}")]]))


@r.callback_query(F.data.startswith("a:sm:"), Staff())
async def a_sm(c: CallbackQuery, state: FSMContext):
    _, _, pid, mode = c.data.split(":")
    pid = int(pid)
    await state.update_data(pid=pid)
    if mode == "1":
        await state.set_state(StockStep.login)
        await show(c, "📝 <b>Логин товара</b>\nОтправьте номер телефона или почту:",
                   kb([[btn("✅ Готово", f"a:p:{pid}")]]))
    else:
        await state.set_state(AddStock.data)
        await show(c, "📄 Отправьте список (или .txt): <b>одна строка = один товар</b>.\n\n"
                      "<code>логин:пароль</code> — логин + пароль\n<code>логин:-</code> или просто <code>логин</code> "
                      "— без пароля\n<code>номер:+</code> — номер, код по запросу",
                   kb([[btn("✖️ Отмена", f"a:p:{pid}")]]))


@r.message(StockStep.login, Staff())
async def st_login(m: Message, state: FSMContext):
    login = (m.text or "").strip()
    if not login:
        return await m.answer("Нужен текст: номер или почта.")
    await state.update_data(login=login)
    await state.set_state(StockStep.pw)
    await m.answer(PW_PROMPT.format(login=html.escape(login)))


@r.message(StockStep.pw, Staff())
async def st_pw(m: Message, state: FSMContext):
    pw = (m.text or "").strip()
    if not pw:
        return await m.answer("Напишите пароль, «-» или «+».")
    d = await state.get_data()
    mode, pwv = stock_mode(pw)
    if await stock_conflict(d["pid"], [mode]):
        return await m.answer(MIX_MSG)
    await q("INSERT INTO stock(product_id,login,pw,mode) VALUES(?,?,?,?)", (d["pid"], d["login"], pwv, mode))
    n = (await q("SELECT COUNT(*) n FROM stock WHERE product_id=? AND sold=0", (d["pid"],), "one"))["n"]
    await state.set_state(StockStep.login)
    await m.answer(f"✅ Добавлено: {MODE_NAME[mode]}. На складе: {n}.\n\nСледующий логин или «Готово»:",
                   reply_markup=kb([[btn("✅ Готово", f"a:p:{d['pid']}")]]))


@r.message(AddStock.data, Staff())
async def a_stock_in(m: Message, state: FSMContext, bot: Bot):
    if m.document:
        buf = io.BytesIO()
        await bot.download(m.document, destination=buf)
        text = buf.getvalue().decode("utf-8", "ignore")
    else:
        text = m.text or ""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        login, pw = (line.split(":", 1) + ["-"])[:2] if ":" in line else (line, "-")
        login, pw = login.strip(), (pw.strip() or "-")
        if login:
            items.append((login,) + stock_mode(pw))
    if not items:
        return await m.answer("Пусто. Отправьте строки вида логин:пароль.")
    pid = (await state.get_data())["pid"]
    if await stock_conflict(pid, [it[1] for it in items]):
        return await m.answer(MIX_MSG)
    await state.clear()
    async with conn() as db:
        await db.executemany("INSERT INTO stock(product_id,login,mode,pw) VALUES(?,?,?,?)",
                             [(pid, lg, mode, pwv) for lg, mode, pwv in items])
        await db.commit()
    await m.answer(f"✅ Добавлено: {len(items)}", reply_markup=kb([[btn("⬅️ К товару", f"a:p:{pid}")]]))


# ---- Универсальный редактор (один state — разные виды ввода)
class Edit(StatesGroup):
    input = State()
    steps = State()


async def start_edit(c, state, kind, prompt, back, **extra):
    await state.set_state(Edit.input)
    await state.update_data(kind=kind, back=back, **extra)
    await show(c, prompt, kb([[btn("✖️ Отмена", back)]]))


FMT_HINT = ("\n\n<i>Можно использовать форматирование Telegram и премиум-эмодзи — бот сохранит их как есть.</i>")
BTN_HINT = ("\n\n<i>Премиум-эмодзи: поставьте его <b>в начале</b> — оно станет иконкой кнопки. "
            "Обычные эмодзи работают в любом месте.</i>")


@r.message(Edit.input, Full())
async def edit_input(m: Message, state: FSMContext):
    d = await state.get_data()
    kind, back = d["kind"], d["back"]
    if kind == "ui_text":
        if not m.text:
            return await m.answer("Нужен текстовое сообщение.")
        await q("INSERT INTO ui_text(key,lang,html) VALUES(?,?,?) ON CONFLICT(key,lang) DO UPDATE SET html=excluded.html",
                (d["key"], d["L"], m.html_text))
    elif kind == "ui_media":
        med = extract_media(m)
        if not med:
            return await m.answer("Отправьте фото, GIF или видео.")
        await q("INSERT INTO ui_text(key,lang,mtype,mfile) VALUES(?,?,?,?) ON CONFLICT(key,lang) DO UPDATE "
                "SET mtype=excluded.mtype, mfile=excluded.mfile", (d["key"], d["L"], med[0], med[1]))
    elif kind == "ui_btn":
        label, eid = parse_label(m)
        if not label:
            return await m.answer("Нужен текст кнопки (эмодзи можно, но текст тоже нужен).")
        await q("INSERT INTO ui_btn(key,lang,label,emoji_id) VALUES(?,?,?,?) ON CONFLICT(key,lang) DO UPDATE "
                "SET label=excluded.label, emoji_id=excluded.emoji_id", (d["key"], d["L"], label, eid))
    elif kind in ("cat_name", "prod_name"):
        label, eid = parse_label(m)
        if not label:
            return await m.answer("Нужен текст названия.")
        tbl, col = ("categories", "name") if kind == "cat_name" else ("products", "title")
        await q(f"UPDATE {tbl} SET {col}=?, emoji_id=? WHERE id=?", (label, eid, d["id"]))
    elif kind in ("cat_media", "prod_media"):
        med = extract_media(m)
        if not med:
            return await m.answer("Отправьте фото, GIF или видео.")
        tbl = "categories" if kind == "cat_media" else "products"
        await q(f"UPDATE {tbl} SET mtype=?, mfile=? WHERE id=?", (med[0], med[1], d["id"]))
    elif kind == "prod_desc":
        if not m.text:
            return await m.answer("Нужен текст.")
        await q("UPDATE products SET descr=? WHERE id=?", (m.html_text, d["id"]))
    elif kind == "prod_price":
        price = parse_amount(m.text)
        if price is None:
            return await m.answer("Введите число больше 0.")
        await q("UPDATE products SET price=? WHERE id=?", (price, d["id"]))
    elif kind == "stock_login":
        login = (m.text or "").strip()
        if not login or len(login) > 512:
            return await m.answer("Отправьте корректный номер / логин (до 512 символов).")
        changed = await qr("UPDATE stock SET login=? WHERE id=? AND sold=0", (login, d["id"]))
        if not changed:
            return await m.answer("Позиция не найдена или уже продана. Список не изменён.")
    elif kind == "stock_pw":
        pw = (m.text or "").strip()
        if not pw or len(pw) > 512:
            return await m.answer("Отправьте пароль, «-» или «+» (до 512 символов).")
        item = await q("SELECT product_id FROM stock WHERE id=? AND sold=0", (d["id"],), "one")
        if not item:
            return await m.answer("Позиция не найдена или уже продана. Список не изменён.")
        mode, pwv = stock_mode(pw)
        others = await q("SELECT DISTINCT mode FROM stock WHERE product_id=? AND sold=0 AND id<>?",
                         (item["product_id"], d["id"]), "all")
        if any((other["mode"] == "code") != (mode == "code") for other in others):
            return await m.answer(MIX_MSG)
        await q("UPDATE stock SET mode=?,pw=? WHERE id=? AND sold=0", (mode, pwv, d["id"]))
    elif kind == "stars_price":
        v = parse_num(m.text, 0.0001, 100000)
        if v is None:
            return await m.answer("Введите число (можно с долями, например 0.01725).")
        await set_setting("stars_price", num_str(v))
    elif kind in ("stars_min", "stars_max"):
        cfg = stars_cfg()
        n = parse_int(m.text, 1, 10_000_000)
        if n is None or (kind == "stars_min" and n > cfg["max"]) or (kind == "stars_max" and n < cfg["min"]):
            return await m.answer("Введите целое число (минимум не больше максимума).")
        await set_setting(kind, str(n))
    elif kind == "stars_presets":
        nums = sorted({int(x) for x in re.findall(r"\d{1,8}", m.text or "") if int(x) > 0})[:8]
        if not nums:
            return await m.answer("Отправьте числа через запятую, например: 50, 100, 250, 500")
        await set_setting("stars_presets", ",".join(map(str, nums)))
    elif kind == "plan_price":
        price = parse_amount(m.text)
        if price is None:
            return await m.answer("Введите число больше 0.")
        await q("UPDATE premium_plans SET price=? WHERE id=?", (price, d["id"]))
    elif kind == "plan_months":
        n = parse_int(m.text, 1, 120)
        if n is None:
            return await m.answer("Введите число месяцев (1–120).")
        await q("UPDATE premium_plans SET months=? WHERE id=?", (n, d["id"]))
    elif kind == "uah_per_usd":
        rate = parse_num(m.text, 0.01, 1_000_000)
        if rate is None:
            return await m.answer("Введите положительный курс: сколько UAH стоит 1 USD, например 41.50.")
        await set_setting("uah_per_usd", num_str(rate))
    elif kind == "ton_usd":
        rate = parse_num(m.text, 0.000001, 1_000_000_000)
        if rate is None:
            return await m.answer("Введите положительный курс: сколько USD стоит 1 TON, например 5.25.")
        await set_setting("ton_usd", num_str(rate))
    elif kind == "cryptobot_token":
        token = (m.text or "").strip()
        try:
            await m.delete()
        except Exception:
            pass
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{10,}", token):
            return await m.answer("Формат токена не похож на Crypto Pay API token. Настройка не изменена.")
        await set_setting("cryptobot_token", token)
    elif kind == "toncenter_api_key":
        api_key = (m.text or "").strip()
        try:
            await m.delete()
        except Exception:
            pass
        if not 8 <= len(api_key) <= 256 or any(ch.isspace() for ch in api_key):
            return await m.answer("Ключ выглядит некорректно. Настройка не изменена.")
        await set_setting("toncenter_api_key", api_key)
    elif kind == "ton_wallet":
        address = (m.text or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9:_-]{32,128}", address):
            return await m.answer("Введите TON-адрес в raw-формате или friendly-адрес (EQ…/UQ…).")
        await set_setting("ton_wallet", address)
    elif kind == "support":
        u = (m.text or "").strip().replace("https://t.me/", "").replace("t.me/", "").lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", u):
            return await m.answer("Нужен @username (латиница, цифры, _).")
        await q("INSERT INTO settings(k,v) VALUES('support',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (u,))
    await state.clear()
    await reload_ui()
    await m.answer("✅ Сохранено.", reply_markup=kb([[btn("⬅️ Назад", back)]]))


# ---- Полный доступ: категории и товары
@r.callback_query(F.data == "f:addcat", Full())
async def f_addcat(c: CallbackQuery, state: FSMContext):
    await state.set_state(NewCat.name)
    await show(c, "Название новой категории (эмодзи и премиум-эмодзи в начале — ок):" + BTN_HINT)


@r.message(NewCat.name, Full())
async def f_addcat_in(m: Message, state: FSMContext):
    label, eid = parse_label(m)
    if not label:
        return await m.answer("Нужен текст.")
    await state.clear()
    await q("INSERT INTO categories(name,emoji_id) VALUES(?,?)", (label, eid))
    await m.answer("✅ Категория добавлена.", reply_markup=kb([[btn("📦 Категории", "a:cats")]]))


@r.callback_query(F.data.startswith("f:cn:"), Full())
async def f_cn(c, state):
    cid = c.data.split(":")[2]
    await start_edit(c, state, "cat_name", "Новое название категории:" + BTN_HINT, f"a:cat:{cid}", id=int(cid))


@r.callback_query(F.data.startswith("f:cm:"), Full())
async def f_cm(c, state):
    cid = c.data.split(":")[2]
    await start_edit(c, state, "cat_media", "Отправьте фото, GIF или видео для категории:", f"a:cat:{cid}", id=int(cid))


@r.callback_query(F.data.startswith("f:cmd:"), Full())
async def f_cmd(c: CallbackQuery):
    cid = int(c.data.split(":")[2])
    await q("UPDATE categories SET mtype=NULL, mfile=NULL WHERE id=?", (cid,))
    await c.answer("Медиа убрано")


@r.callback_query(F.data.startswith("f:cdel:"), Full())
async def f_cdel_ask(c: CallbackQuery):
    cid = int(c.data.split(":")[2])
    cat = await q("SELECT name FROM categories WHERE id=?", (cid,), "one")
    await show(c, f"🗑 Удалить категорию «{html.escape(cat['name']) if cat else cid}» вместе со всеми её товарами?",
               kb([[btn("✅ Да, удалить", f"f:cdely:{cid}"), btn("✖️ Нет", f"a:cat:{cid}")]]))


@r.callback_query(F.data.startswith("f:cdely:"), Full())
async def f_cdel(c: CallbackQuery):
    cid = int(c.data.split(":")[2])
    await q("UPDATE products SET active=0 WHERE cat_id=?", (cid,))
    await q("DELETE FROM categories WHERE id=?", (cid,))
    await show(c, "🗑 Категория удалена.", kb([[btn("📦 Категории", "a:cats")]]))


@r.callback_query(F.data.startswith("f:addprod:"), Full())
async def f_addprod(c: CallbackQuery, state: FSMContext):
    await state.set_state(NewProd.title)
    await state.update_data(cid=int(c.data.split(":")[2]))
    await show(c, "Название товара (например: <code>Gmail | 2024 | с почтой</code>):" + BTN_HINT)


@r.message(NewProd.title, Full())
async def f_addprod_t(m: Message, state: FSMContext):
    label, eid = parse_label(m)
    if not label:
        return await m.answer("Нужен текст.")
    await state.update_data(title=label, eid=eid)
    await state.set_state(NewProd.price)
    await m.answer("Ціна за 1 шт. у USD (наприклад, 0.75):")


@r.message(NewProd.price, Full())
async def f_addprod_p(m: Message, state: FSMContext):
    price = parse_amount(m.text)
    if price is None:
        return await m.answer("Введите число больше 0.")
    d = await state.get_data()
    await state.clear()
    pid = await q("INSERT INTO products(cat_id,title,price,emoji_id) VALUES(?,?,?,?)",
                  (d["cid"], d["title"], price, d["eid"]))
    await m.answer("✅ Товар создан. Теперь выберите способ выдачи:",
                   reply_markup=kb([[btn("🔁 Этапы выдачи (ручная)", f"f:ps:{pid}")],
                                    [btn("📦 Склад (автовыдача)", f"a:st:{pid}")],
                                    [btn("⬅️ К товару", f"a:p:{pid}")]]))


@r.callback_query(F.data.startswith("f:pn:"), Full())
async def f_pn(c, state):
    pid = c.data.split(":")[2]
    await start_edit(c, state, "prod_name", "Новое название товара:" + BTN_HINT, f"a:p:{pid}", id=int(pid))


class StockQtyEdit(StatesGroup):
    qty = State()


@r.callback_query(F.data.startswith("f:qty:"), Full())
async def f_qty(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":")[2])
    p = await q("SELECT id,steps FROM products WHERE id=?", (pid,), "one")
    if not p:
        return await c.answer("Товар не найден", show_alert=True)
    if not json.loads(p["steps"] or "[]"):
        return await show(c, "Сначала включите ручную выдачу через «Этапы выдачи». Для ручной выдачи задаётся общее количество, без ввода данных каждой позиции.", back_admin())
    await state.update_data(qty_pid=pid)
    await state.set_state(StockQtyEdit.qty)
    await show(c, "Отправьте новое количество товара в наличии (целое число 0 или больше):", kb([[btn("⬅️ Отмена", f"a:p:{pid}")]]))


@r.message(StockQtyEdit.qty, Staff())
async def f_qty_save(m: Message, state: FSMContext):
    raw = (m.text or "").strip()
    if not raw.isdigit():
        return await m.answer("Введите целое число 0 или больше.")
    d = await state.get_data()
    pid, qty = int(d["qty_pid"]), int(raw)
    await q("UPDATE products SET stock_qty=? WHERE id=?", (qty, pid))
    await state.clear()
    await m.answer(f"✅ Остаток установлен: {qty} шт.", reply_markup=back_admin())


@r.callback_query(F.data.startswith("f:price:"), Full())
async def f_price(c, state):
    pid = c.data.split(":")[2]
    await start_edit(c, state, "prod_price", "Новая цена за 1 шт. в USD (например, 0.75):", f"a:p:{pid}", id=int(pid))


@r.callback_query(F.data.startswith("f:pd:"), Full())
async def f_pd(c, state):
    pid = c.data.split(":")[2]
    await start_edit(c, state, "prod_desc", "Описание товара (оно появится в карточке):" + FMT_HINT,
                     f"a:p:{pid}", id=int(pid))


@r.callback_query(F.data.startswith("f:pm:"), Full())
async def f_pm(c, state):
    pid = c.data.split(":")[2]
    await start_edit(c, state, "prod_media", "Отправьте фото, GIF или видео для товара:", f"a:p:{pid}", id=int(pid))


@r.callback_query(F.data.startswith("f:pmd:"), Full())
async def f_pmd(c: CallbackQuery):
    await q("UPDATE products SET mtype=NULL, mfile=NULL WHERE id=?", (int(c.data.split(":")[2]),))
    await c.answer("Медиа убрано")


@r.callback_query(F.data.startswith("f:stc:"), Full())
async def f_stc(c: CallbackQuery):
    pid = int(c.data.split(":")[2])
    n = (await q("SELECT COUNT(*) n FROM stock WHERE product_id=? AND sold=0", (pid,), "one"))["n"]
    await show(c, f"🧹 Удалить со склада все непроданные позиции ({n} шт.)? Это необратимо.",
               kb([[btn("✅ Да, очистить", f"f:stcy:{pid}"), btn("✖️ Нет", f"a:p:{pid}")]]))


@r.callback_query(F.data.startswith("f:stcy:"), Full())
async def f_stcy(c: CallbackQuery):
    pid = int(c.data.split(":")[2])
    n = await qr("DELETE FROM stock WHERE product_id=? AND sold=0", (pid,))
    await show(c, f"🧹 Склад очищен ({n} шт.).", kb([[btn("⬅️ К товару", f"a:p:{pid}")]]))


@r.callback_query(F.data.startswith("f:delp:"), Full())
async def f_delp_ask(c: CallbackQuery):
    pid = int(c.data.split(":")[2])
    p = await q("SELECT title FROM products WHERE id=?", (pid,), "one")
    await show(c, f"🗑 Удалить товар «{html.escape(p['title']) if p else pid}» из витрины?",
               kb([[btn("✅ Да, удалить", f"f:delpy:{pid}"), btn("✖️ Нет", f"a:p:{pid}")]]))


@r.callback_query(F.data.startswith("f:delpy:"), Full())
async def f_delp(c: CallbackQuery):
    await q("UPDATE products SET active=0 WHERE id=?", (int(c.data.split(":")[2]),))
    await show(c, "🗑 Товар удалён из витрины.", kb([[btn("📦 Категории", "a:cats")]]))


# ---- Этапы ручной выдачи
@r.callback_query(F.data.startswith("f:ps:"), Full())
async def f_ps(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":")[2])
    await state.set_state(Edit.steps)
    await state.update_data(pid=pid, steps=[])
    await show(c, "🔁 <b>Этапы заказа</b>\n\nОтправляйте названия кнопок для покупателя <b>по одному сообщению</b>, "
                  "в порядке выполнения заказа. Используйте понятные названия вроде «Получить результат» или «Проверить заказ».\n\n"
                  "Когда закончите — нажмите «Готово»." + BTN_HINT,
               kb([[btn("✅ Готово", "f:psd")], [btn("✖️ Отмена", f"a:p:{pid}")]]))


@r.message(Edit.steps, Full())
async def f_ps_in(m: Message, state: FSMContext):
    label, eid = parse_label(m)
    if not label:
        return await m.answer("Нужен текст кнопки.")
    d = await state.get_data()
    steps = d["steps"] + [[label, eid]]
    await state.update_data(steps=steps)
    await m.answer(f"Этап {len(steps)}: «{html.escape(label)}» добавлен. Ещё или «Готово».",
                   reply_markup=kb([[btn("✅ Готово", "f:psd")]]))


@r.callback_query(F.data == "f:psd", Full())
async def f_psd(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d.get("steps"):
        return await c.answer("Добавьте хотя бы один этап", show_alert=True)
    await q("UPDATE products SET steps=? WHERE id=?", (json.dumps(d["steps"]), d["pid"]))
    await state.clear()
    await show(c, f"✅ Сохранено этапов: {len(d['steps'])}. Товар теперь выдаётся вручную через заявки.",
               kb([[btn("⬅️ К товару", f"a:p:{d['pid']}")]]))


@r.callback_query(F.data.startswith("f:psc:"), Full())
async def f_psc(c: CallbackQuery):
    pid = int(c.data.split(":")[2])
    await q("UPDATE products SET steps=NULL WHERE id=?", (pid,))
    await show(c, "📦 Включена автовыдача со склада.", kb([[btn("⬅️ К товару", f"a:p:{pid}")]]))


# ---- Оформление: тексты, кнопки, медиа
def lname(L):
    return "🇷🇺 RU"


@r.callback_query(F.data == "f:ds", Full())
async def f_ds(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "🎨 <b>Оформление бота</b>\n\nНастройка русских текстов, кнопок и медиа:",
               kb([[btn("🇷🇺 Русский", "f:dl:ru")],
                   [btn("⬅️ Админ-панель", "a:home")]]))


@r.callback_query(F.data.startswith("f:dl:"), Full())
async def f_dl(c: CallbackQuery):
    L = c.data.split(":")[2]
    await show(c, f"🎨 <b>Оформление — {lname(L)}</b>\n\n📝 Тексты и 🖼 медиа экранов, 🔘 названия кнопок "
                  "(с премиум-эмодзи и цветом).\nНазвания категорий и товаров, их фото и описание — в «Товары и склад».",
               kb([[btn("📝 Тексты и медиа экранов", f"f:dt:{L}:0")], [btn("🔘 Кнопки", f"f:db:{L}:0")],
                   [btn("⬅️ Назад", "f:ds")]]))


PER = 8


def pager(keys, page, prefix, L, label_fn, item_prefix, back=None):
    chunk = keys[page * PER:(page + 1) * PER]
    rows = [[btn(label_fn(k), f"{item_prefix}:{L}:{k}")] for k in chunk]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"{prefix}:{L}:{page - 1}"))
    if (page + 1) * PER < len(keys):
        nav.append(btn("▶️", f"{prefix}:{L}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("⬅️ Назад", back or f"f:dl:{L}")])
    return rows


@r.callback_query(F.data.startswith("f:dt:"), Full())
async def f_dt(c: CallbackQuery):
    _, _, L, page = c.data.split(":")
    keys = list(DT)
    rows = pager(keys, int(page), "f:dt", L, lambda k: ("🖼 " if text_media(L, k)[0] else "") +
                 ("✏️ " if UI_T.get((k, L)) and UI_T[(k, L)][0] else "") + DT[k][0], "f:te")
    await show(c, f"📝 <b>Экраны и сообщения — {lname(L)}</b>\n✏️ — текст изменён, 🖼 — есть медиа", kb(rows))


@r.callback_query(F.data.startswith("f:te:"), Full())
async def f_te(c: CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, L, key = c.data.split(":")
    med = text_media(L, key)[0]
    cur = html.escape(txt(L, key)[:1200])
    rows = [[btn("✏️ Изменить текст", f"f:tt:{L}:{key}")],
            [btn("🖼 Фото/GIF/видео", f"f:tm:{L}:{key}"), btn("🗑 Убрать медиа", f"f:tmd:{L}:{key}")],
            [btn("👁 Предпросмотр", f"f:tp:{L}:{key}"), btn("♻️ Сбросить", f"f:tr:{L}:{key}")],
            [btn("⬅️ К списку", f"f:tgt:{L}:0" if key.startswith("tg_") else f"f:dt:{L}:0")]]
    await show(c, f"📝 <b>{DT[key][0]}</b> — {lname(L)}\nПеременные: <code>{html.escape(DT[key][1])}</code>\n"
                  f"Медиа: {med or 'нет'}\n\nСейчас (HTML):\n<pre>{cur}</pre>", kb(rows))


@r.callback_query(F.data.startswith("f:tt:"), Full())
async def f_tt(c, state):
    _, _, L, key = c.data.split(":")
    await start_edit(c, state, "ui_text", f"Отправьте новый текст для «{DT[key][0]}» ({lname(L)}).\n"
                     f"Переменные (вставьте как есть): <code>{html.escape(DT[key][1])}</code>" + FMT_HINT,
                     f"f:te:{L}:{key}", L=L, key=key)


@r.callback_query(F.data.startswith("f:tm:"), Full())
async def f_tm(c, state):
    _, _, L, key = c.data.split(":")
    await start_edit(c, state, "ui_media", f"Отправьте фото, GIF или видео для «{DT[key][0]}» ({lname(L)}):",
                     f"f:te:{L}:{key}", L=L, key=key)


@r.callback_query(F.data.startswith("f:tmd:"), Full())
async def f_tmd(c: CallbackQuery, state: FSMContext):
    _, _, L, key = c.data.split(":")
    await q("UPDATE ui_text SET mtype=NULL, mfile=NULL WHERE key=? AND lang=?", (key, L))
    await reload_ui()
    await f_te(c, state)


@r.callback_query(F.data.startswith("f:tr:"), Full())
async def f_tr(c: CallbackQuery, state: FSMContext):
    _, _, L, key = c.data.split(":")
    await q("DELETE FROM ui_text WHERE key=? AND lang=?", (key, L))
    await reload_ui()
    await f_te(c, state)


@r.callback_query(F.data.startswith("f:tp:"), Full())
async def f_tp(c: CallbackQuery, bot: Bot):
    _, _, L, key = c.data.split(":")
    mt, mf = text_media(L, key)
    await c.answer()
    try:
        await try_send(bot, c.from_user.id, txt(L, key), kb([[btn("⬅️ Назад к экрану", f"f:te:{L}:{key}")]]), mt, mf)
    except Exception as e:
        await c.message.answer(f"Не удалось показать: {e}")


@r.callback_query(F.data.startswith("f:db:"), Full())
async def f_db(c: CallbackQuery):
    _, _, L, page = c.data.split(":")
    rows = pager(list(DB_), int(page), "f:db", L, lambda k: ("✏️ " if (k, L) in UI_B else "") + DB_[k][0], "f:be")
    await show(c, f"🔘 <b>Кнопки — {lname(L)}</b>", kb(rows))


async def render_be(c, L, key):
    ov = UI_B.get((key, L))
    d = DB_[key]
    label = ov[0] if ov and ov[0] else (d[1] if L == "ua" else d[2])
    await show(c, f"🔘 <b>{d[0]}</b> — {lname(L)}\n\nСейчас: <b>{html.escape(label)}</b>\n"
                  f"Премиум-эмодзи: {'да' if ov and ov[1] else 'нет'}\nЦвет: {(ov[2] if ov and ov[2] else 'по умолчанию')}",
               kb([[btn("✏️ Изменить название", f"f:bt:{L}:{key}")], [btn("🎨 Цвет кнопки", f"f:bs:{L}:{key}")],
                   [btn("♻️ Сбросить", f"f:br:{L}:{key}")], [btn("⬅️ К списку", f"f:db:{L}:0")]]))


@r.callback_query(F.data.startswith("f:be:"), Full())
async def f_be(c: CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, L, key = c.data.split(":")
    await render_be(c, L, key)


@r.callback_query(F.data.startswith("f:bt:"), Full())
async def f_bt(c, state):
    _, _, L, key = c.data.split(":")
    await start_edit(c, state, "ui_btn", f"Отправьте новое название кнопки «{DB_[key][0]}» ({lname(L)}):" + BTN_HINT,
                     f"f:be:{L}:{key}", L=L, key=key)


@r.callback_query(F.data.startswith("f:bs:"), Full())
async def f_bs(c: CallbackQuery):
    _, _, L, key = c.data.split(":")
    ov = UI_B.get((key, L))
    cycle = [None, "primary", "success", "danger"]
    cur = ov[2] if ov else None
    nxt = cycle[(cycle.index(cur) + 1) % 4] if cur in cycle else None
    d = DB_[key]
    label = ov[0] if ov and ov[0] else (d[1] if L == "ua" else d[2])
    await q("INSERT INTO ui_btn(key,lang,label,emoji_id,style) VALUES(?,?,?,?,?) ON CONFLICT(key,lang) DO UPDATE "
            "SET style=excluded.style", (key, L, label, ov[1] if ov else None, nxt))
    await reload_ui()
    await render_be(c, L, key)


@r.callback_query(F.data.startswith("f:br:"), Full())
async def f_br(c: CallbackQuery):
    _, _, L, key = c.data.split(":")
    await q("DELETE FROM ui_btn WHERE key=? AND lang=?", (key, L))
    await reload_ui()
    await render_be(c, L, key)


# ---- Платежи и валютные курсы
@r.callback_query(F.data == "f:payments", Full())
async def f_payments(c: CallbackQuery, state: FSMContext):
    await state.clear()
    token_ok = bool(SET.get("cryptobot_token"))
    wallet = (SET.get("ton_wallet") or "").strip()
    key_ok = bool(SET.get("toncenter_api_key"))
    uah_rate = parse_num(SET.get("uah_per_usd"), 0.01, 1_000_000)
    ton_rate = parse_num(SET.get("ton_usd"), 0.000001, 1_000_000_000)
    legacy_pending = SET.get("legacy_uah_pending") == "1"
    text = (
        "💳 <b>Платежі та курси</b>\n\n"
        f"🤖 Crypto Bot API: {'налаштовано ✅' if token_ok else 'не налаштовано ⛔'}\n"
        f"💎 TON Keeper-адреса: {html.escape(wallet) if wallet else 'не задана'}\n"
        f"🔑 TON Center API key: {'налаштовано ✅' if key_ok else 'не налаштовано ⛔'}\n\n"
        f"💱 UAH/USD: {'1 USD = ' + format(uah_rate, '.4f') + ' UAH' if uah_rate else 'не задано (оплата UAH отключена)'}\n"
        f"💎 TON/USD: {'1 TON = $' + format(ton_rate, '.6f') if ton_rate else 'не задано (оплата TON вимкнена)'}\n\n"
        "Внутренний баланс, товары, Stars и Premium оцениваются в USD. Для банковских платежей зачисление рассчитывается по курсу UAH/USD на момент создания заявки. Для TON сумма фиксируется при создании платежа."
    )
    rows = [
        [btn("🔐 Встановити Crypto Bot API token", "f:pay:edit:cryptobot_token")],
        [btn("💎 Встановити TON Keeper-адресу", "f:pay:edit:ton_wallet")],
        [btn("🔑 Встановити TON Center API key", "f:pay:edit:toncenter_api_key")],
        [btn("💱 Курс UAH за 1 USD", "f:pay:edit:uah_per_usd"),
         btn("💎 Курс USD за 1 TON", "f:pay:edit:ton_usd")],
        [btn("🧪 Проверить Crypto Bot", "f:pay:testcrypto"), btn("🧪 Проверить TON Center", "f:pay:testton")],
    ]
    if legacy_pending:
        rows.append([btn("🔄 Конвертувати старі UAH-суми в USD", "f:pay:migrate")])
    rows.append([btn("⬅️ Админ-панель", "a:home")])
    await show(c, text, kb(rows))


@r.callback_query(F.data.startswith("f:pay:edit:"), Full())
async def f_pay_edit(c: CallbackQuery, state: FSMContext):
    kind = c.data.split(":", 3)[3]
    prompts = {
        "cryptobot_token": "Отправьте Crypto Pay API token от @CryptoBot. Сообщение будет удалено после получения.",
        "ton_wallet": "Отправьте адрес TON-кошелька, на который покупатели должны переводить TON.",
        "toncenter_api_key": "Отправьте TON Center API key. Сообщение будет удалено после получения.",
        "uah_per_usd": "Укажите, сколько гривен стоит 1 USD (например, 41.50). Это курс для банковского пополнения.",
        "ton_usd": "Укажите, сколько USD стоит 1 TON (например, 5.25). Это курс для TON-пополнения.",
    }
    if kind not in prompts:
        return await c.answer()
    await start_edit(c, state, kind, prompts[kind], "f:payments")


@r.callback_query(F.data == "f:pay:testcrypto", Full())
async def f_pay_test_crypto(c: CallbackQuery):
    if not SET.get("cryptobot_token"):
        return await c.answer("Токен не задано.", show_alert=True)
    try:
        info = await crypto_api("getMe", {})
        name = html.escape(str(info.get("name") or info.get("app_name") or "Crypto Pay app"))
        await c.answer(f"API отвечает: {name}", show_alert=True)
    except Exception:
        logging.warning("Crypto Pay credentials test failed", exc_info=True)
        await c.answer("Проверка не пройдена. Проверьте токен и доступ к API.", show_alert=True)


@r.callback_query(F.data == "f:pay:testton", Full())
async def f_pay_test_ton(c: CallbackQuery):
    if not SET.get("ton_wallet") or not SET.get("toncenter_api_key"):
        return await c.answer("Спочатку задайте TON-адресу та TON Center API key.", show_alert=True)
    try:
        await ton_transactions()
        await c.answer("TON Center API доступен, история транзакций получена.", show_alert=True)
    except Exception:
        logging.warning("TON Center credentials test failed", exc_info=True)
        await c.answer("Проверка не пройдена. Проверьте адрес, API key и доступ к TON Center.", show_alert=True)


@r.callback_query(F.data == "f:pay:migrate", Full())
async def f_pay_migrate(c: CallbackQuery):
    if SET.get("legacy_uah_pending") != "1":
        return await c.answer("Конвертация уже выполнена или не нужна.", show_alert=True)
    rate = parse_num(SET.get("uah_per_usd"), 0.01, 1_000_000)
    if not rate:
        return await c.answer("Сначала установите курс UAH/USD.", show_alert=True)
    await show(c,
        f"⚠️ <b>Конвертация старых сумм</b>\n\nПо курсу 1 USD = {rate:.4f} UAH будут единоразово пересчитаны балансы пользователей, текущие цены товаров, Stars/Premium, исторические заказы и депозиты.\n\nПроверьте курс и сделайте копию файла базы данных перед продолжением.",
        kb([[btn("✅ Так, конвертувати", "f:pay:migrate:yes")], [btn("✖️ Скасувати", "f:payments")]])
    )


@r.callback_query(F.data == "f:pay:migrate:yes", Full())
async def f_pay_migrate_yes(c: CallbackQuery):
    if SET.get("legacy_uah_pending") != "1":
        return await c.answer("Конвертация уже выполнена.", show_alert=True)
    rate = parse_num(SET.get("uah_per_usd"), 0.01, 1_000_000)
    if not rate:
        return await c.answer("Сначала установите курс UAH/USD.", show_alert=True)
    async with conn() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("UPDATE deposits SET paid_amount=COALESCE(paid_amount,amount), currency=COALESCE(currency,'UAH')")
        for table, column in (("users", "balance"), ("orders", "total"),
                              ("deposits", "amount"), ("premium_plans", "price")):
            await db.execute(f"UPDATE {table} SET {column}=ROUND(COALESCE({column},0)/?,2)", (rate,))
        seed_ids_raw = await (await db.execute("SELECT v FROM settings WHERE k='telegram_seed_product_ids'")).fetchone()
        seed_ids = [int(x) for x in (seed_ids_raw[0].split(",") if seed_ids_raw and seed_ids_raw[0] else []) if x.isdigit()]
        if seed_ids:
            placeholders = ",".join("?" for _ in seed_ids)
            await db.execute(f"UPDATE products SET price=ROUND(COALESCE(price,0)/?,2) WHERE id NOT IN ({placeholders})",
                             (rate, *seed_ids))
        else:
            await db.execute("UPDATE products SET price=ROUND(COALESCE(price,0)/?,2)", (rate,))
        star = await (await db.execute("SELECT v FROM settings WHERE k='stars_price'")).fetchone()
        if star:
            try:
                star_usd = float(star[0]) / rate
                await db.execute("UPDATE settings SET v=? WHERE k='stars_price'", (format(star_usd, ".8f").rstrip("0").rstrip("."),))
            except Exception:
                pass
        await db.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('legacy_uah_pending','0')")
        await db.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('currency_migration_completed','1')")
        await db.commit()
    await reload_ui()
    await show(c, "✅ Старые финансовые суммы конвертированы в USD. Проверьте несколько товаров и балансов.",
               kb([[btn("💳 Платежі / курси", "f:payments")], [btn("⬅️ Админ-панель", "a:home")]]))


# ---- Настройки / поддержка
@r.callback_query(F.data == "f:set", Full())
async def f_set(c: CallbackQuery, state: FSMContext):
    await state.clear()
    u = SET.get("support") or SUPPORT_ENV
    await show(c, f"⚙️ <b>Настройки</b>\n\n🆘 Поддержка: {'@' + u if u else 'не задана'}\n"
                  "При нажатии на «Поддержка» покупателя перебросит в этот аккаунт.",
               kb([[btn("✏️ Изменить @username поддержки", "f:sup")], [btn("💳 Платежи / курсы", "f:payments")], [btn("⬅️ Админ-панель", "a:home")]]))


@r.callback_query(F.data == "f:sup", Full())
async def f_sup(c, state):
    await start_edit(c, state, "support", "Отправьте @username, куда перебрасывать покупателей:", "f:set")


# ---- Статистика
@r.callback_query(F.data == "f:stats", Full())
async def f_stats(c: CallbackQuery):
    u = await q("SELECT COUNT(*) n, COALESCE(SUM(balance),0) b FROM users", fetch="one")
    o = await q("SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM orders WHERE status!='refunded'", fetch="one")
    d = await q("SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE status='approved'", fetch="one")
    auto_d = await q("SELECT COALESCE(SUM(usd_amount),0) s FROM payment_intents WHERE status='paid'", fetch="one")
    d_total = float(d['s'] or 0) + float(auto_d['s'] or 0)
    p = await q("SELECT COUNT(*) n FROM deposits WHERE status='pending'", fetch="one")
    w = await q("SELECT COUNT(*) n FROM orders WHERE status='active' AND pending=1", fetch="one")
    s = await q("SELECT COUNT(*) n FROM stock WHERE sold=0", fetch="one")
    await show(c, f"📊 <b>Статистика</b>\n\n👥 Пользователей: {u['n']}\n💎 Сумма балансов: {money(u['b'])}\n"
                  f"🛒 Заказов: {o['n']} на {money(o['s'])}\n💳 Пополнено всего: {money(d_total)}\n"
                  f"⏳ Пополнений в ожидании: {p['n']}\n📥 Заявок на выдачу: {w['n']}\n"
                  f"📦 Аккаунтов на складе: {s['n']}", back_admin())


# ---- Выдача/снятие баланса
class Give(StatesGroup):
    uid = State()
    amount = State()


@r.callback_query(F.data == "f:give", Full())
async def f_give(c: CallbackQuery, state: FSMContext):
    await state.set_state(Give.uid)
    await show(c, "ID пользователя:")


@r.message(Give.uid, Full())
async def f_give_uid(m: Message, state: FSMContext):
    if not (m.text or "").strip().lstrip("-").isdigit():
        return await m.answer("Нужен числовой ID.")
    if not await q("SELECT 1 FROM users WHERE id=?", (int(m.text),), "one"):
        return await m.answer("Пользователь не найден (он должен был запустить бота).")
    await state.update_data(uid=int(m.text))
    await state.set_state(Give.amount)
    await m.answer("Сумма (отрицательная — списать):")


@r.message(Give.amount, Full())
async def f_give_amt(m: Message, state: FSMContext, bot: Bot):
    amt = parse_amount(m.text, -1_000_000, 1_000_000)
    if not amt:
        return await m.answer("Введите число, не равное нулю (отрицательное — списать).")
    uid = (await state.get_data())["uid"]
    row = await q("SELECT balance FROM users WHERE id=?", (uid,), "one")
    if not row:
        await state.clear()
        return await m.answer("Пользователь не найден.", reply_markup=back_admin())
    if row["balance"] + amt < 0:
        return await m.answer(f"❌ Нельзя списать больше, чем есть на балансе ({money(row['balance'])}). Введите другую сумму:")
    await state.clear()
    await q("UPDATE users SET balance=ROUND(balance+?,2) WHERE id=?", (amt, uid))
    await m.answer(f"✅ Баланс {uid} изменён на {money(amt)}", reply_markup=back_admin())
    if amt > 0:
        try:
            await screen_to(bot, uid, "dep_ok", await lang_of(uid), amount=money(amt))
        except Exception:
            pass


# ---- Поиск пользователя / бан
class UserQ(StatesGroup):
    q = State()


async def user_card(uid):
    u = await q("SELECT * FROM users WHERE id=?", (uid,), "one")
    o = await q("SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM orders WHERE user_id=? AND status!='refunded'", (uid,), "one")
    d = await q("SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE user_id=? AND status='approved'", (uid,), "one")
    text = (f"👤 <b>Пользователь</b> <code>{uid}</code>{' @' + u['username'] if u['username'] else ''}\n"
            f"Регистрация: {u['created']}\n💎 Баланс: {money(u['balance'])}\n"
            f"🛒 Заказов: {o['n']} на {money(o['s'])}\n💳 Пополнено: {money(d['s'])}\n"
            f"Статус: {'🚫 заблокирован' if u['banned'] else '✅ активен'}")
    mk = kb([[btn("✅ Разблокировать" if u["banned"] else "🚫 Заблокировать", f"f:ub:{uid}:{0 if u['banned'] else 1}")],
             [btn("💰 Изменить баланс", f"f:ug:{uid}")], [btn("⬅️ Админ-панель", "a:home")]])
    return text, mk


@r.callback_query(F.data == "f:usr", Full())
async def f_usr(c: CallbackQuery, state: FSMContext):
    await state.set_state(UserQ.q)
    await show(c, "Введите ID или @username пользователя:", kb([[btn("✖️ Отмена", "a:home")]]))


@r.message(UserQ.q, Full())
async def f_usr_in(m: Message, state: FSMContext):
    t = (m.text or "").strip().lstrip("@")
    u = await q("SELECT id FROM users WHERE id=?", (int(t),), "one") if t.isdigit() else \
        await q("SELECT id FROM users WHERE lower(username)=?", (t.lower(),), "one")
    if not u:
        return await m.answer("Не найден (пользователь должен был запускать бота). Попробуйте ещё раз.")
    await state.clear()
    text, mk = await user_card(u["id"])
    await m.answer(text, reply_markup=mk)


@r.callback_query(F.data.startswith("f:ub:"), Full())
async def f_ub(c: CallbackQuery):
    _, _, uid, flag = c.data.split(":")
    if int(uid) in OWNERS:
        return await c.answer("Владельца заблокировать нельзя", show_alert=True)
    await q("UPDATE users SET banned=? WHERE id=?", (int(flag), int(uid)))
    text, mk = await user_card(int(uid))
    await show(c, text, mk)


@r.callback_query(F.data.startswith("f:ug:"), Full())
async def f_ug(c: CallbackQuery, state: FSMContext):
    await state.set_state(Give.amount)
    await state.update_data(uid=int(c.data.split(":")[2]))
    await show(c, "Сумма (отрицательная — списать):", kb([[btn("✖️ Отмена", "a:home")]]))


# ---- Реквизиты
class NewReq(StatesGroup):
    bank = State()
    card = State()
    holder = State()


@r.callback_query(F.data == "f:reqs", Full())
async def f_reqs(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[btn(f"🗑 {x['bank']} • {x['card']}", f"f:rd:{x['id']}")] for x in await q("SELECT * FROM requisites", fetch="all")]
    rows += [[btn("➕ Добавить реквизиты", "f:ra")], [btn("⬅️ Админ-панель", "a:home")]]
    await show(c, "💳 <b>Реквизиты для пополнения</b>\n(нажмите, чтобы удалить)", kb(rows))


@r.callback_query(F.data == "f:ra", Full())
async def f_ra(c: CallbackQuery, state: FSMContext):
    await state.set_state(NewReq.bank)
    await show(c, "Название банка (например: Monobank, PrivatBank):" + BTN_HINT)


@r.message(NewReq.bank, Full())
async def f_ra_bank(m: Message, state: FSMContext):
    label, eid = parse_label(m)
    if not label:
        return await m.answer("Нужен текст.")
    await state.update_data(bank=label, eid=eid)
    await state.set_state(NewReq.card)
    await m.answer("Номер карты:")


@r.message(NewReq.card, Full())
async def f_ra_card(m: Message, state: FSMContext):
    await state.update_data(card=(m.text or "").strip())
    await state.set_state(NewReq.holder)
    await m.answer("Имя получателя (или «-» чтобы пропустить):")


@r.message(NewReq.holder, Full())
async def f_ra_holder(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    holder = "" if (m.text or "").strip() == "-" else (m.text or "").strip()
    await q("INSERT INTO requisites(bank,card,holder,emoji_id) VALUES(?,?,?,?)", (d["bank"], d["card"], holder, d["eid"]))
    await m.answer("✅ Реквизиты добавлены.", reply_markup=kb([[btn("💳 Реквизиты", "f:reqs")]]))


@r.callback_query(F.data.startswith("f:rd:"), Full())
async def f_rd(c: CallbackQuery, state: FSMContext):
    await q("DELETE FROM requisites WHERE id=?", (int(c.data.split(":")[2]),))
    await f_reqs(c, state)


# ---- Обов'язкова підписка
class NewSub(StatesGroup):
    ref = State()


@r.callback_query(F.data == "f:subs", Full())
async def f_subs(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[btn(f"🗑 {x['title']}", f"f:sd:{x['id']}")] for x in await q("SELECT * FROM channels", fetch="all")]
    rows += [[btn("➕ Добавить канал", "f:sa")], [btn("⬅️ Админ-панель", "a:home")]]
    await show(c, "📢 <b>Обязательная подписка</b>\n(нажмите на канал, чтобы убрать)\n\n"
                  "Бот должен быть админом в канале.", kb(rows))


@r.callback_query(F.data == "f:sa", Full())
async def f_sa(c: CallbackQuery, state: FSMContext):
    await state.set_state(NewSub.ref)
    await show(c, "Отправьте @username канала, ссылку t.me/... или ID (-100...).\n"
                  "Для приватного канала — его ID вида <code>-100123...</code>.")


@r.message(NewSub.ref, Full())
async def f_sa_in(m: Message, state: FSMContext, bot: Bot):
    ref = (m.text or "").strip()
    if "t.me/" in ref:
        ref = "@" + ref.split("t.me/")[1].strip("/").split("/")[0].lstrip("@")
    try:
        chat = await bot.get_chat(ref)
        me = await bot.get_chat_member(chat.id, (await bot.me()).id)
        assert me.status in ("administrator", "creator")
    except Exception:
        return await m.answer("❌ Не получилось. Проверьте, что канал существует и бот добавлен в него админом.")
    link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            link = None
    await state.clear()
    await q("INSERT INTO channels(chat_id,title,link) VALUES(?,?,?)", (str(chat.id), chat.title, link))
    await m.answer(f"✅ Добавлено: {html.escape(chat.title)}", reply_markup=kb([[btn("📢 Подписка", "f:subs")]]))


@r.callback_query(F.data.startswith("f:sd:"), Full())
async def f_sd(c: CallbackQuery, state: FSMContext):
    await q("DELETE FROM channels WHERE id=?", (int(c.data.split(":")[2]),))
    await f_subs(c, state)


# ---- Персонал
class NewStaff(StatesGroup):
    uid = State()


@r.callback_query(F.data == "f:staff", Full())
async def f_staff(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[btn(f"🗑 {'🛡 админ' if x['role'] == 'admin' else '🧰 модер'} • {x['user_id']}", f"f:xd:{x['user_id']}")]
            for x in await q("SELECT * FROM roles", fetch="all")]
    rows += [[btn("➕ Главный админ", "f:xa:admin"), btn("➕ Модератор", "f:xa:mod")], [btn("⬅️ Админ-панель", "a:home")]]
    owners = ", ".join(f"<code>{o}</code>" for o in OWNERS)
    await show(c, f"👥 <b>Персонал</b>\n👑 Владельцы: {owners}\n\n"
                  "🛡 Главный админ — полная панель как у владельца (добавляет и снимает только владелец).\n"
                  "🧰 Модератор — заявки на пополнение, заявки на выдачу и склад.", kb(rows))


@r.callback_query(F.data.startswith("f:xa:"), Full())
async def f_xa(c: CallbackQuery, state: FSMContext):
    role = c.data.split(":")[2]
    if role == "admin" and await role_of(c.from_user.id) != "owner":
        return await c.answer("Главных админов добавляет только владелец", show_alert=True)
    await state.set_state(NewStaff.uid)
    await state.update_data(role=role)
    await show(c, "Отправьте Telegram ID пользователя (он должен запустить бота):")


@r.message(NewStaff.uid, Full())
async def f_xa_in(m: Message, state: FSMContext):
    if not (m.text or "").strip().isdigit():
        return await m.answer("Нужен числовой ID.")
    uid = int(m.text)
    role = (await state.get_data())["role"]
    await state.clear()
    if role == "admin" and await role_of(m.from_user.id) != "owner":
        return await m.answer("Главных админов добавляет только владелец.")
    if uid in OWNERS:
        return await m.answer("Это владелец.")
    await q("INSERT OR REPLACE INTO roles(user_id,role) VALUES(?,?)", (uid, role))
    await m.answer(f"✅ {uid} теперь {'главный админ' if role == 'admin' else 'модератор'}.",
                   reply_markup=kb([[btn("👥 Персонал", "f:staff")]]))


@r.callback_query(F.data.startswith("f:xd:"), Full())
async def f_xd(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split(":")[2])
    t = await q("SELECT role FROM roles WHERE user_id=?", (uid,), "one")
    if t and t["role"] == "admin" and await role_of(c.from_user.id) != "owner":
        return await c.answer("Главного админа снимает только владелец", show_alert=True)
    await q("DELETE FROM roles WHERE user_id=?", (uid,))
    await f_staff(c, state)


# ---- Розсилка (copy_message — сохраняет форматирование, премиум-эмодзи, фото, видео)
class Broadcast(StatesGroup):
    text = State()


@r.callback_query(F.data == "f:bc", Full())
async def f_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.text)
    await show(c, "Отправьте сообщение для рассылки (текст, фото, видео, GIF — со своим форматированием):")


@r.message(Broadcast.text, Full())
async def f_bc_in(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    ok = bad = 0
    for u in await q("SELECT id FROM users WHERE banned=0", fetch="all"):
        for attempt in range(2):
            try:
                await bot.copy_message(u["id"], m.chat.id, m.message_id)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                bad += 1
                break
        await asyncio.sleep(0.05)
    await m.answer(f"📨 Готово. Доставлено: {ok}, ошибок: {bad}", reply_markup=back_admin())


async def set_setting(k, v):
    await q("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


# ---- Telegram Stars / Premium (админка)
class NewPlan(StatesGroup):
    months = State()
    price = State()


TG_PROMPTS = {
    "stars_price": "💲 Цена за <b>1 ⭐</b> (можно с долями, например <code>0.01725</code>):",
    "stars_min": "📉 Минимальное количество звёзд за покупку (целое число):",
    "stars_max": "📈 Максимальное количество звёзд за покупку (целое число):",
    "stars_presets": "🔢 Количества для кнопок через запятую (до 8 штук), например: <code>50, 100, 250, 500, 1000, 5000</code>",
}


@r.callback_query(F.data == "f:tg", Full())
async def f_tg(c: CallbackQuery, state: FSMContext):
    await state.clear()
    cfg = stars_cfg()
    plans = await q("SELECT * FROM premium_plans ORDER BY months", fetch="all")
    pon = SET.get("prem_on", "1") == "1"
    text = (f"⭐ <b>Telegram Stars / 💎 Premium</b>\n\n"
            f"⭐ <b>Stars:</b> {'включено ✅' if cfg['on'] else 'выключено ⛔'}\n"
            f"💲 1 ⭐ = ${num_str(cfg['price'])}  (100 ⭐ = {money(calc_total(cfg['price'], 100))}, "
            f"1000 ⭐ = {money(calc_total(cfg['price'], 1000))})\n"
            f"📉 Мин: {cfg['min']} • 📈 Макс: {cfg['max']}\n"
            f"🔢 Кнопки: {', '.join(map(str, cfg['presets'])) or '—'}\n\n"
            f"💎 <b>Premium:</b> {'включено ✅' if pon else 'выключено ⛔'}\n"
            f"Нажмите на план, чтобы изменить цену / срок.\n\n"
            "Заказы приходят персоналу в личку и в «Заявки на выдачу». После выдачи нажмите «✅ Выполнено».")
    rows = [[btn("💲 Цена ⭐", "f:tge:stars_price"), btn("⭐ Stars: " + ("выкл" if cfg["on"] else "вкл"), "f:tgs:stars")],
            [btn("📉 Минимум", "f:tge:stars_min"), btn("📈 Максимум", "f:tge:stars_max")],
            [btn("🔢 Кнопки-количества", "f:tge:stars_presets")],
            [btn("💎 Premium: " + ("выкл" if pon else "вкл"), "f:tgs:prem")]]
    rows += [[btn(f"{'✅' if p['active'] else '⛔'} {p['months']} мес. • {money(p['price'])}", f"f:tpl:{p['id']}")]
             for p in plans]
    rows += [[btn("➕ План Premium", "f:tpa")],
             [btn("📝 Тексты и картинки 🇷🇺", "f:tgt:ru:0")],
             [btn("🔘 Названия кнопок 🇷🇺", "f:db:ru:0")],
             [btn("⬅️ Админ-панель", "a:home")]]
    await show(c, text, kb(rows))


@r.callback_query(F.data.startswith("f:tgt:"), Full())
async def f_tgt(c: CallbackQuery):
    _, _, L, page = c.data.split(":")
    rows = pager(TG_KEYS, int(page), "f:tgt", L, lambda k: (("🖼 " if text_media(L, k)[0] else "") +
                 ("✏️ " if UI_T.get((k, L)) and UI_T[(k, L)][0] else "") + DT[k][0]), "f:te", back="f:tg")
    await show(c, f"📝 <b>Тексты и картинки Stars/Premium — {lname(L)}</b>\n✏️ — текст изменён, 🖼 — есть картинка/GIF/видео\n"
                  "Выберите экран → «🖼 Фото/GIF/видео», чтобы поставить картинку.", kb(rows))


@r.callback_query(F.data.startswith("f:tge:"), Full())
async def f_tge(c: CallbackQuery, state: FSMContext):
    kind = c.data.split(":")[2]
    if kind not in TG_PROMPTS:
        return await c.answer()
    await start_edit(c, state, kind, TG_PROMPTS[kind], "f:tg")


@r.callback_query(F.data.startswith("f:tgs:"), Full())
async def f_tgs(c: CallbackQuery, state: FSMContext):
    key = "stars_on" if c.data.split(":")[2] == "stars" else "prem_on"
    await set_setting(key, "0" if SET.get(key, "1") == "1" else "1")
    await reload_ui()
    await f_tg(c, state)


async def render_plan(c, pid):
    p = await q("SELECT * FROM premium_plans WHERE id=?", (pid,), "one")
    if not p:
        return await show(c, "План не найден.", kb([[btn("⬅️ Назад", "f:tg")]]))
    await show(c, f"💎 <b>План Premium</b>\n\nСрок: <b>{p['months']} мес.</b>\nЦена: <b>{money(p['price'])}</b>\n"
                  f"Статус: {'✅ показывается в боте' if p['active'] else '⛔ скрыт'}",
               kb([[btn("💲 Цена", f"f:tpe:plan_price:{pid}"), btn("📅 Срок (мес.)", f"f:tpe:plan_months:{pid}")],
                   [btn("⛔ Скрыть" if p["active"] else "✅ Показать", f"f:tpt:{pid}")],
                   [btn("🗑 Удалить", f"f:tpd:{pid}")], [btn("⬅️ Назад", "f:tg")]]))


@r.callback_query(F.data.startswith("f:tpl:"), Full())
async def f_tpl(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_plan(c, int(c.data.split(":")[2]))


@r.callback_query(F.data.startswith("f:tpe:"), Full())
async def f_tpe(c: CallbackQuery, state: FSMContext):
    _, _, kind, pid = c.data.split(":")
    prompt = "💲 Новая цена плана:" if kind == "plan_price" else "📅 Новый срок в месяцах (число):"
    await start_edit(c, state, kind, prompt, f"f:tpl:{pid}", id=int(pid))


@r.callback_query(F.data.startswith("f:tpt:"), Full())
async def f_tpt(c: CallbackQuery):
    pid = int(c.data.split(":")[2])
    await q("UPDATE premium_plans SET active=1-active WHERE id=?", (pid,))
    await render_plan(c, pid)


@r.callback_query(F.data.startswith("f:tpd:"), Full())
async def f_tpd(c: CallbackQuery):
    pid = int(c.data.split(":")[2])
    await show(c, "🗑 Удалить этот план?", kb([[btn("✅ Да, удалить", f"f:tpdy:{pid}"), btn("✖️ Нет", f"f:tpl:{pid}")]]))


@r.callback_query(F.data.startswith("f:tpdy:"), Full())
async def f_tpdy(c: CallbackQuery, state: FSMContext):
    await q("DELETE FROM premium_plans WHERE id=?", (int(c.data.split(":")[2]),))
    await f_tg(c, state)


@r.callback_query(F.data == "f:tpa", Full())
async def f_tpa(c: CallbackQuery, state: FSMContext):
    await state.set_state(NewPlan.months)
    await show(c, "📅 Срок нового плана в месяцах (например: 3):", kb([[btn("✖️ Отмена", "f:tg")]]))


@r.message(NewPlan.months, Full())
async def f_tpa_months(m: Message, state: FSMContext):
    n = parse_int(m.text, 1, 120)
    if n is None:
        return await m.answer("Введите число месяцев (1–120).")
    await state.update_data(months=n)
    await state.set_state(NewPlan.price)
    await m.answer(f"💲 Цена плана на {n} мес. в USD:")


@r.message(NewPlan.price, Full())
async def f_tpa_price(m: Message, state: FSMContext):
    price = parse_amount(m.text)
    if price is None:
        return await m.answer("Введите число больше 0.")
    d = await state.get_data()
    await state.clear()
    await q("INSERT INTO premium_plans(months,price) VALUES(?,?)", (d["months"], price))
    await m.answer("✅ План добавлен.", reply_markup=kb([[btn("⭐ Stars / Premium", "f:tg")]]))


# ───────────────────────── Команды и запасной обработчик ─────────────────────────
@r.message(Command("admin"), Staff())
async def cmd_admin(m: Message, state: FSMContext):
    await reset_state(state, m.from_user.id)
    await admin_home(m)


@r.message(StateFilter(None))
async def fallback(m: Message):
    await main_menu(m, m.from_user.id)


# ───────────────────────── Запуск ─────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or not OWNERS:
        raise SystemExit("Заполните BOT_TOKEN и OWNER_IDS в .env")
    await init_db()
    await sync_country_order_ui()
    try:
        # API-ключі з адмінки зберігаються в SQLite; обмежуємо доступ до файлу БД на Unix.
        if os.name == "posix" and os.path.isfile(DB):
            os.chmod(DB, 0o600)
    except OSError:
        logging.warning("Could not restrict database file permissions")
    await reload_ui()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    if not SET.get("support") and not SUPPORT_ENV:  # поддержка по умолчанию = владелец
        try:
            ch = await bot.get_chat(min(OWNERS))
            if ch.username:
                await q("INSERT OR REPLACE INTO settings(k,v) VALUES('support',?)", (ch.username,))
                await reload_ui()
        except Exception:
            pass
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(r)
    await bot.delete_webhook(drop_pending_updates=True)
    payment_task = asyncio.create_task(payment_watchdog(bot))
    try:
        await dp.start_polling(bot)
    finally:
        payment_task.cancel()
        try:
            await payment_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
