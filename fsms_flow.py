"""Поставщик F-SMS: витрина, выдача номера и возвраты магазина.

API: https://f-sms.su/openapi.json
Номер берётся с аккаунта-воркера (логин и пароль). Деньги покупателя и решение
по возврату остаются в этом боте, даже если заявку уже закрыли на стороне F-SMS.
"""
import asyncio
import html
import io
import logging
import re
import time

import aiohttp
import aiosqlite
from yarl import URL
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

log = logging.getLogger("fsms")
FSMS_BASE = "https://f-sms.su"
CANCEL_AFTER = 120
SMS_WAIT = 300
USER_SUPPLIER = "Поставщик №1"


def pe(emoji_id, fallback):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


# Animated Emoji (RestrictedEmoji) и предупреждение из TranslucentPack.
E_OK = pe("5427009714745517609", "✅")
E_BAD = pe("5465665476971471368", "❌")
E_BUBBLE = pe("5465300082628763143", "💬")
E_PHONE = pe("5467539229468793355", "📞")
E_PLANE = pe("5361600266225326825", "✈️")
E_CLOCK = pe("5413704112220949842", "⏰")
E_WARN = pe("5276240711795107620", "⚠️")
E_WAIT = pe("5469741319330996757", "💫")
NO_SIM = (
    f"{pe('5852812849780362931', '❌')} Нет доступных SIM-карт или не рабочее время, "
    f"попробуйте позже!"
)
FAIL_SMS = (
    f"{pe('5852812849780362931', '❌')}Не удалось получить SMS на номер, 300 секунд активации вышло, "
    f"средства были возвращены на баланс!\n"
    f"{pe('5276240711795107620', '⚠️')} Вы можете запросить другой номер!"
)

_g = {}
_clients = {}
_tasks = {}
_locks = {}
_svc_cache = {"at": 0.0, "rows": None}


class FsmsError(Exception):
    pass


class FsmsAdm(StatesGroup):
    login = State()
    password = State()
    price = State()
    title = State()
    emoji = State()
    reason = State()


class FsmsUser(StatesGroup):
    shot = State()


def db(sql, args=(), fetch=None):
    return _g["q"](sql, args, fetch)


def dbw(sql, args=()):
    return _g["qr"](sql, args)


def kb(rows):
    return _g["kb"](rows)


def btn(text, data):
    return _g["btn"](text, data)


def money(v):
    return _g["money"](v)


def clip(text, limit=40):
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def order_lock(oid):
    return _locks.setdefault(oid, asyncio.Lock())


def _walk(obj, depth=0):
    if depth > 3:
        return
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value, depth + 1)
    elif isinstance(obj, list):
        for value in obj[:6]:
            yield from _walk(value, depth + 1)


def _pick(obj, keys):
    for node in _walk(obj):
        for key in keys:
            val = node.get(key)
            if val not in (None, "", [], {}):
                return val
    return None


def _detail(body):
    if not isinstance(body, dict):
        return ""
    detail = body.get("detail")
    if isinstance(detail, str):
        return detail[:180]
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        return str(detail[0].get("msg") or "")[:180]
    return ""


def service_rows(payload):
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        raw = None
        for key in ("services", "items", "data", "result"):
            if isinstance(payload.get(key), list):
                raw = payload[key]
                break
        raw = [] if raw is None else raw
    else:
        raw = []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("id") if item.get("id") is not None else item.get("service_id"))
            price = float(item.get("price") if item.get("price") is not None else item.get("cost") or 0)
        except (TypeError, ValueError):
            continue
        name = item.get("name") or item.get("title")
        if not name:
            continue
        out.append({"id": sid, "name": str(name).strip(), "price": price})
    return out


def count_of(payload):
    if isinstance(payload, bool):
        return 0
    if isinstance(payload, (int, float)):
        return max(0, int(payload))
    try:
        return max(0, int(_pick(payload, ("free", "free_count", "count", "available", "n"))))
    except (TypeError, ValueError):
        return 0


def sid_of(payload):
    val = _pick(payload, ("sid", "session_id"))
    if val is not None:
        return str(val).strip()
    for node in _walk(payload):
        val = node.get("id")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def phone_of(payload):
    return find_phone(payload)


def find_phone(obj, depth=0):
    """Номер из ответа F-SMS. Линия «26» номером не считается."""
    if depth > 5 or obj is None:
        return ""
    if isinstance(obj, dict):
        for key in ("phone", "number", "msisdn"):
            text = str(obj.get(key) or "").strip()
            if len(re.sub(r"\D", "", text)) >= 10:
                return text
        for value in obj.values():
            found = find_phone(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj[:20]:
            found = find_phone(value, depth + 1)
            if found:
                return found
    return ""


def find_sid(obj, depth=0):
    if depth > 5 or obj is None:
        return ""
    if isinstance(obj, dict):
        for key in ("session_id", "sid"):
            val = obj.get(key)
            if val not in (None, "", [], {}):
                return str(val).strip()
        for value in obj.values():
            found = find_sid(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj[:20]:
            found = find_sid(value, depth + 1)
            if found:
                return found
    return ""


def code_of(payload):
    val = _pick(payload, ("code", "sms_code", "otp"))
    if isinstance(val, bool) or val is None:
        return ""
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    text = str(val).strip()
    if text.lower() in ("none", "null", "false"):
        return ""
    return text


async def read_json(resp):
    try:
        return await resp.json(content_type=None)
    except Exception:
        return None


async def drop_client(worker_id):
    client = _clients.pop(worker_id, None)
    if client and not client.closed:
        await client.close()


QUICK_TIMEOUT = aiohttp.ClientTimeout(total=12, sock_connect=5)


def poll_timeout(seconds):
    seconds = max(1, min(int(seconds), 15))
    return seconds, aiohttp.ClientTimeout(total=seconds + 3, sock_connect=5)


async def open_client(login, password):
    client = aiohttp.ClientSession(
        timeout=QUICK_TIMEOUT,
        cookie_jar=aiohttp.CookieJar(),
        headers={"User-Agent": "vh-shop"},
    )
    try:
        async with client.post(
            FSMS_BASE + "/api/login",
            json={"username": login, "password": password},
        ) as resp:
            body = await read_json(resp)
            if resp.status != 200:
                raise FsmsError(_detail(body) or "неверный логин или пароль")
            token = ""
            if isinstance(body, dict):
                token = str(body.get("auth_token") or body.get("token") or body.get("access_token") or "")
            if token:
                client.cookie_jar.update_cookies({"auth_token": token}, URL(FSMS_BASE + "/"))
            if not client.cookie_jar.filter_cookies(URL(FSMS_BASE + "/")):
                raise FsmsError("F-SMS не выдал сессию")
            return client
    except FsmsError:
        await client.close()
        raise
    except Exception as exc:
        await client.close()
        raise FsmsError("нет связи с F-SMS") from exc


async def fsms_client(worker, force=False):
    wid = worker["id"]
    current = _clients.get(wid)
    if current and not force and not current.closed:
        return current
    await drop_client(wid)
    client = await open_client(worker["login"], worker["password"])
    _clients[wid] = client
    return client


async def fsms_call(worker, method, path, *, params=None, json_body=None, form_factory=None, timeout=None):
    last = (0, None)
    for force in (False, True):
        client = await fsms_client(worker, force=force)
        data = form_factory() if form_factory else None
        try:
            async with client.request(
                method, FSMS_BASE + path, params=params, json=json_body, data=data,
                timeout=timeout,
            ) as resp:
                body = await read_json(resp)
                last = (resp.status, body)
                if resp.status == 401 and not force:
                    continue
                return resp.status, body
        except FsmsError:
            raise
        except aiohttp.ClientError as exc:
            raise FsmsError("нет связи с F-SMS") from exc
    return last


async def any_worker():
    return await db("SELECT * FROM fsms_workers ORDER BY id LIMIT 1", fetch="one")


async def public_services(force=False):
    now_ts = time.time()
    if not force and _svc_cache["rows"] is not None and now_ts - _svc_cache["at"] < 30:
        return _svc_cache["rows"]
    worker = await any_worker()
    if not worker:
        raise FsmsError("сначала добавьте воркера")
    status, body = await fsms_call(worker, "GET", "/api/services/public")
    if status != 200:
        raise FsmsError("не удалось получить сервисы F-SMS")
    rows = service_rows(body)
    _svc_cache.update(at=now_ts, rows=rows)
    return rows


async def stock_of(remote_id):
    worker = await any_worker()
    if not worker:
        return 0
    try:
        status, body = await fsms_call(
            worker, "GET", "/api/pool/free-count", params={"service_id": int(remote_id)}
        )
    except FsmsError:
        return 0
    if status != 200:
        log.warning("free-count %s status=%s detail=%s", remote_id, status, _detail(body))
        return 0
    return count_of(body)


BUSY_SQL = "'issued','waiting','missed','coded'"


async def free_workers():
    return await db(
        f"""SELECT w.* FROM fsms_workers w
           WHERE NOT EXISTS (
             SELECT 1 FROM fsms_orders o
             WHERE o.worker_id=w.id AND o.status IN ({BUSY_SQL})
           )
           ORDER BY w.id""",
        fetch="all",
    )


async def cancel_remote(worker, sid):
    status, body = await fsms_call(worker, "POST", f"/sessions/{sid}/cancel")
    code = code_of(body)
    if code:
        return code
    if status == 404:
        log.info("cancel %s already closed", sid)
        return ""
    if status != 200:
        log.warning("cancel %s status=%s detail=%s", sid, status, _detail(body))
        raise FsmsError("не удалось отменить номер у поставщика")
    return ""


async def pull_code(order, timeout=15):
    """await_next — то же действие, что кнопка «Кинул код» на сайте F-SMS."""
    worker = await db("SELECT * FROM fsms_workers WHERE id=?", (order["worker_id"],), "one")
    if not worker or not order["remote_sid"]:
        return ""
    seconds, client_timeout = poll_timeout(timeout)
    status, body = await fsms_call(
        worker, "GET", f"/sessions/{order['remote_sid']}/code",
        params={"timeout": seconds, "poll_interval": 1, "await_next": "true"},
        timeout=client_timeout,
    )
    if status != 200:
        log.warning("code %s status=%s detail=%s", order["id"], status, _detail(body))
        return ""
    return code_of(body)


async def _active_one(worker, path):
    try:
        status, payload = await fsms_call(worker, "GET", path)
    except FsmsError as exc:
        log.warning("active %s: %s", path, exc)
        return "", ""
    if status != 200:
        return "", ""
    return find_sid(payload) or "", find_phone(payload) or ""


async def read_active(worker):
    paths = ("/api/me/active-session", "/api/me/active-sessions")
    tasks = {asyncio.create_task(_active_one(worker, path)) for path in paths}
    sid = phone = ""
    try:
        pending = set(tasks)
        while pending and not (sid and phone):
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                found_sid, found_phone = task.result()
                sid = found_sid or sid
                phone = found_phone or phone
        return sid, phone
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


async def wait_issued(worker, sid, phone):
    """Старт сессии иногда приходит раньше, чем в ней появляется номер."""
    if sid and phone:
        return sid, phone
    for _ in range(10):
        await asyncio.sleep(0.4)
        found_sid, found_phone = await read_active(worker)
        sid = found_sid or sid
        phone = found_phone or phone
        if sid and phone:
            return sid, phone
    return sid, phone


async def acquire(remote_id):
    workers = await free_workers()
    if not workers:
        raise FsmsError("Свободных линий нет, попробуйте через минуту")
    for worker in workers:
        sid, phone = await read_active(worker)
        if not (sid and phone):
            try:
                status, body = await fsms_call(
                    worker, "POST", "/sessions/start", params={"service_id": int(remote_id)}
                )
            except FsmsError as exc:
                log.warning("start worker %s: %s", worker["id"], exc)
                status, body = 0, None
            if status == 200:
                sid = find_sid(body) or sid
                phone = find_phone(body) or phone
                log.info(
                    "start worker %s sid=%s phone_empty=%s phone_type=%s",
                    worker["id"], bool(sid), not phone,
                    type((body or {}).get("phone")).__name__ if isinstance(body, dict) else type(body).__name__,
                )
                if sid and not phone:
                    sid, phone = await wait_issued(worker, sid, phone)
            else:
                log.warning(
                    "start worker %s status=%s detail=%s",
                    worker["id"], status, _detail(body),
                )
                if sid and not phone:
                    sid, phone = await wait_issued(worker, sid, phone)
        if sid and phone:
            return worker, sid, phone
        if sid:
            log.warning("session %s has no phone yet", sid)
    raise FsmsError("F-SMS не выдал номер")


_queue_lock = asyncio.Lock()


def queue_text(name, pos):
    return (
        f"⏳ Все линии заняты.\n\n"
        f"Вы в очереди: <b>{pos}</b>\n"
        f"Сервис: {html.escape(name)}\n\n"
        f"Номер придёт сюда, как только освободится линия."
    )


def queue_kb(qid):
    return kb([[btn("Выйти из очереди", f"z:qout:{qid}")]])


async def queue_pos(qid):
    row = await db("SELECT COUNT(*) AS n FROM fsms_queue WHERE id<=?", (qid,), "one")
    return int(row["n"] or 1)


async def tell_queue(bot, row, text, markup):
    mid = row["msg_id"]
    if mid:
        try:
            await bot.edit_message_text(text, chat_id=row["user_id"], message_id=mid, reply_markup=markup)
            return mid
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return mid
    sent = await bot.send_message(row["user_id"], text, reply_markup=markup)
    await db("UPDATE fsms_queue SET msg_id=? WHERE id=?", (sent.message_id, row["id"]))
    return sent.message_id


async def drop_queue(bot, row, text):
    await db("DELETE FROM fsms_queue WHERE id=?", (row["id"],))
    try:
        await tell_queue(bot, row, text, home_kb())
    except Exception:
        log.exception("queue notice %s", row["id"])


async def join_queue(c, service):
    uid = c.from_user.id
    existing = await db("SELECT * FROM fsms_queue WHERE user_id=? ORDER BY id LIMIT 1", (uid,), "one")
    if existing:
        await db("UPDATE fsms_queue SET service_id=? WHERE id=?", (service["id"], existing["id"]))
        qid = existing["id"]
    else:
        qid = await db(
            "INSERT INTO fsms_queue(user_id, service_id, created) VALUES(?,?,?)",
            (uid, service["id"], _g["now"]()),
        )
    row = await db("SELECT * FROM fsms_queue WHERE id=?", (qid,), "one")
    sent = await replace_cb(c, queue_text(service["name"], await queue_pos(qid)), queue_kb(qid))
    if sent is not None:
        await db("UPDATE fsms_queue SET msg_id=? WHERE id=?", (sent.message_id, row["id"]))


async def leave_queue(c, qid):
    row = await db(
        "SELECT * FROM fsms_queue WHERE id=? AND user_id=?",
        (qid, c.from_user.id), "one",
    )
    if not row:
        return await c.answer("Вас уже нет в очереди", show_alert=True)
    await db("DELETE FROM fsms_queue WHERE id=?", (qid,))
    await replace_cb(c, "Вы вышли из очереди.", home_kb())
    await c.answer("Вы вышли из очереди")


async def serve_queued(bot, row):
    service = await db("SELECT * FROM fsms_services WHERE id=?", (row["service_id"],), "one")
    if not service or not await enabled():
        await drop_queue(bot, row, "Поставщик выключен. Этот запрос из очереди закрыт.")
        return "next"
    user = await db("SELECT balance FROM users WHERE id=?", (row["user_id"],), "one")
    if not user or float(user["balance"]) + 1e-9 < float(service["price"]):
        await drop_queue(bot, row, "❌ Недостаточно средств на балансе. Этот запрос из очереди закрыт.")
        return "next"
    try:
        fresh = await refresh_service(service)
    except FsmsError as exc:
        await drop_queue(bot, row, f"❌ {html.escape(str(exc))}")
        return "next"
    if float(user["balance"]) + 1e-9 < float(fresh["price"]):
        await drop_queue(bot, row, "❌ Недостаточно средств на балансе. Этот запрос из очереди закрыт.")
        return "next"
    if await stock_of(fresh["remote_id"]) <= 0:
        await drop_queue(bot, row, NO_SIM)
        return "next"
    await tell_queue(bot, row, search_text(fresh), kb([]))
    try:
        worker, sid_remote, phone = await acquire(fresh["remote_id"])
    except FsmsError as exc:
        if "линий" in str(exc):
            fresh_row = await db("SELECT * FROM fsms_queue WHERE id=?", (row["id"],), "one")
            if fresh_row:
                await tell_queue(
                    bot, fresh_row,
                    queue_text(fresh["name"], await queue_pos(row["id"])),
                    queue_kb(row["id"]),
                )
            return "busy"
        await drop_queue(
            bot, row,
            NO_SIM if "не выдал" in str(exc) else f"❌ {html.escape(str(exc))}",
        )
        return "next"
    still = await db("SELECT * FROM fsms_queue WHERE id=?", (row["id"],), "one")
    if not still:
        try:
            await cancel_remote(worker, sid_remote)
        except FsmsError:
            log.exception("release after queue leave")
        return "next"
    oid = await remember_order(row["user_id"], fresh, worker, sid_remote, phone)
    if oid is None:
        try:
            await cancel_remote(worker, sid_remote)
        except FsmsError:
            log.exception("release after empty balance")
        await drop_queue(bot, row, "❌ Недостаточно средств на балансе.")
        return "next"
    await db("DELETE FROM fsms_queue WHERE id=?", (row["id"],))
    saved = await db("SELECT service_name, emoji_id FROM fsms_orders WHERE id=?", (oid,), "one")
    text = number_text(
        saved["service_name"], phone,
        fresh["emoji_id"] or saved["emoji_id"], fresh["emoji_char"],
    )
    msg_id = await tell_queue(bot, still, text, number_kb(oid))
    if msg_id:
        await db("UPDATE fsms_orders SET msg_id=? WHERE id=?", (msg_id, oid))
    return "done"


async def pump_queue(bot):
    if _queue_lock.locked():
        return
    async with _queue_lock:
        while await free_workers():
            row = await db("SELECT * FROM fsms_queue ORDER BY id LIMIT 1", fetch="one")
            if not row:
                return
            try:
                result = await serve_queued(bot, row)
            except Exception:
                log.exception("queue serve %s", row["id"])
                return
            if result == "busy":
                return


async def enabled():
    return _g["SET"].get("fsms_enabled") == "1"


async def set_enabled(on):
    await db(
        "INSERT OR REPLACE INTO settings(k,v) VALUES('fsms_enabled',?)",
        ("1" if on else "0",),
    )
    await _g["reload_ui"]()


async def has_supplier(cid):
    row = await db("SELECT 1 AS ok FROM fsms_services WHERE cat_id=? AND cat_id!=0 LIMIT 1", (cid,), "one")
    return bool(row)


async def refresh_service(service, force=False):
    remote = {row["id"]: row for row in await public_services(force=force)}
    src = remote.get(int(service["remote_id"]))
    if not src:
        raise FsmsError("Этого сервиса больше нет у F-SMS")
    price = max(float(service["price"]), float(src["price"]))
    await db(
        "UPDATE fsms_services SET base_price=?, price=? WHERE id=?",
        (src["price"], price, service["id"]),
    )
    return await db("SELECT * FROM fsms_services WHERE id=?", (service["id"],), "one")


def service_icon(service):
    if service["emoji_id"]:
        return pe(service["emoji_id"], service["emoji_char"] or "📲")
    if service["emoji_char"]:
        return service["emoji_char"]
    return "📲"


def search_text(service):
    clock = pe("5276412364458059956", "🕓")
    warn = pe("5276240711795107620", "⚠️")
    wait = pe("6113844439292054570", "⏳")
    return (
        f"{service_icon(service)} Сервис: {html.escape(service['name'])}\n\n"
        f"{clock} Делаем USSD запрос на GSM шлюз, возможна задержка выдачи номера телефона до 5 минут!\n\n"
        f"{warn} SIM могут быть в блоке, система проверяет перед выдачей номера!\n\n"
        f"{wait} Ожидайте, ищем номер…"
    )


def card_text(service, stock, kind):
    title = html.escape(service["name"])
    return (
        f"{service_icon(service)} <b>{title}</b> | Тип: {title}\n\n"
        f"{pe('5278602437001767574', '🔓')} Формат: Номер + код\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Условия:\n"
        f"<blockquote>"
        f"▫️ Новые номера, ранее не использовавшиеся в {title}.\n"
        f"▫️ Регистрация аккаунта выполняется самостоятельно.\n"
        f"▫️ После получения номера пройдите регистрацию в приложении {title}.\n"
        f"▫️ Если номер не подходит, аккаунт заблокирован или уже зарегистрирован — "
        f"нажмите «ЗАБЛОКИРОВАН» и приложите скриншот из приложения."
        f"</blockquote>\n\n"
        f"{pe('5309795500277403547', '💰')} Цена: <b>{money(service['price'])}</b>\n"
        f"{pe('5206702193385700709', '📦')} В наличии: <b>{stock} шт.</b>\n"
        f"{pe('5276422526350681413', '🎁')} Количество: <b>1</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{pe('5231102735817918643', '👇')} Для получения нажмите кнопку"
    )


def phone_parts(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    full = f"+{digits}" if digits else str(phone or "")
    local = digits[1:] if digits.startswith("7") and len(digits) >= 11 else digits
    return full, local


def service_mark(emoji_id, emoji_char):
    if emoji_id:
        return pe(str(emoji_id), emoji_char or "💬")
    if emoji_char:
        return emoji_char
    return "💬"


def number_text(service, phone, emoji_id=None, emoji_char=None):
    full, local = phone_parts(phone)
    ok = pe("5852666765057724209", "✅")
    phone_icon = pe("5253763618926778589", "📞")
    plane = pe("5852830669599674051", "✈️")
    clock = pe("5276412364458059956", "🕓")
    warn = pe("5276240711795107620", "⚠️")
    return (
        f"{ok}НОМЕР ВЫДАН{ok}\n\n"
        f"{service_mark(emoji_id, emoji_char)} Сервис: {html.escape(service)}\n\n"
        f"{phone_icon}Номер: <code>{html.escape(full)}</code>\n"
        f"{plane}Без кода: <code>{html.escape(local)}</code>\n\n"
        f"{clock} Время на активацию 15 минут!\n\n"
        f"{warn}Как только отправите SMS на номер, нажмите кнопку « Получить SMS »"
    )


def wait_text(service, phone, emoji_id=None, emoji_char=None):
    full, local = phone_parts(phone)
    phone_icon = pe("5253763618926778589", "📞")
    plane = pe("5453988307002213357", "✈️")
    swirl = pe("5382178536872223059", "💫")
    return (
        f"{service_mark(emoji_id, emoji_char)} Сервис: {html.escape(service)}\n\n"
        f"{phone_icon}Номер: <code>{html.escape(full)}</code>\n"
        f"{plane}Без кода: <code>{html.escape(local)}</code>\n\n"
        f"{swirl}Ожидаем SMS..."
    )


async def service_face(order):
    emoji_id = order["emoji_id"]
    emoji_char = None
    if order["service_id"]:
        svc = await db(
            "SELECT emoji_id, emoji_char FROM fsms_services WHERE id=?",
            (order["service_id"],), "one",
        )
        if svc:
            emoji_id = svc["emoji_id"] or emoji_id
            emoji_char = svc["emoji_char"]
    return emoji_id, emoji_char


def merge_code(old, new):
    parts = [part.strip() for part in str(old or "").splitlines() if part.strip()]
    fresh = str(new or "").strip()
    if fresh and fresh not in parts:
        parts.append(fresh)
    return "\n".join(parts)


def code_text(service, phone, code, emoji_id=None, emoji_char=None):
    full, local = phone_parts(phone)
    ok = pe("5852666765057724209", "✅")
    phone_icon = pe("5253763618926778589", "📞")
    plane = pe("5453988307002213357", "✈️")
    up = pe("5206401524200145033", "🔼")
    parts = [part.strip() for part in str(code or "").splitlines() if part.strip()]
    shown = "\n".join(
        f"{up} SMS код: <code>{html.escape(part)}</code>" for part in parts
    ) or f"{up} SMS код: —"
    return (
        f"{ok}SMS ПОЛУЧЕН{ok}\n\n"
        f"{service_mark(emoji_id, emoji_char)} Сервис: {html.escape(service)}\n\n"
        f"{phone_icon}Номер: <code>{html.escape(full)}</code>\n"
        f"{plane}Без кода: <code>{html.escape(local)}</code>\n\n"
        f"{shown}"
    )


async def order_code_text(order, code=None, extra=""):
    emoji_id, emoji_char = await service_face(order)
    text = code_text(
        order["service_name"], order["phone"],
        order["code"] if code is None else code,
        emoji_id, emoji_char,
    )
    return text + extra


def number_kb(oid):
    return kb([[btn("Получить SMS", f"z:sent:{oid}"), btn("Отмена", f"z:can:{oid}")]])


def wait_kb(oid):
    return kb([[btn("Отмена", f"z:can:{oid}")]])


def miss_kb(oid):
    return kb([[btn("Отменить", f"z:drop:{oid}"), btn("Повтор код", f"z:again:{oid}")]])


def code_kb(oid):
    return kb([
        [btn("Готово", f"z:ok:{oid}"), btn("Жду еще код", f"z:more:{oid}")],
        [btn("ЗАБЛОКИРОВАН", f"z:blk:{oid}")],
    ])


def home_kb():
    return kb([[_g["B"]("ru", "back", "m:home")]])


async def replace_cb(c, text, markup):
    msg = c.message
    if msg is not None and not _g["has_media"](msg):
        try:
            return await msg.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return msg
    try:
        if msg is not None:
            await msg.delete()
    except Exception:
        pass
    return await c.bot.send_message(c.from_user.id, text, reply_markup=markup)


async def show_order(bot, order, text, markup):
    mid = order["msg_id"]
    if mid:
        try:
            await bot.edit_message_text(text, chat_id=order["user_id"], message_id=mid, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return
    sent = await bot.send_message(order["user_id"], text, reply_markup=markup)
    await db("UPDATE fsms_orders SET msg_id=? WHERE id=?", (sent.message_id, order["id"]))


async def supplier_menu(c, cid):
    if not await has_supplier(cid):
        return await _g["render_cat"](c, c.from_user.id, cid)
    cat = await db("SELECT * FROM categories WHERE id=?", (cid,), "one")
    if not cat:
        return await c.answer("Раздел не найден", show_alert=True)
    icon = _g["ADMIN_ICON"]
    rows = [
        [_g["mk_btn"](USER_SUPPLIER, f"z:fs:{cid}", icon["fsms"])],
        [_g["B"]("ru", "back", "m:shop")],
    ]
    await _g["show"](
        c,
        f"<b>{html.escape(cat['name'])}</b>\n\nВыберите поставщика\nКоличество: 1",
        kb(rows),
    )


def service_btn(row, data=None):
    label = row["name"] or "Сервис"
    icon = row["emoji_id"] or None
    if not icon and row["emoji_char"]:
        label = f"{row['emoji_char']} {label}"
    return _g["mk_btn"](clip(label, 40), data or f"z:get:{row['id']}", icon)


async def open_services(c, cid):
    rows = await db(
        """SELECT s.*, c.emoji_id AS cat_emoji FROM fsms_services s
           LEFT JOIN categories c ON c.id=s.cat_id
           WHERE s.cat_id=? ORDER BY s.name""",
        (cid,), "all",
    )
    if not rows:
        cat = await db("SELECT name FROM categories WHERE id=?", (cid,), "one")
        title = html.escape(cat["name"]) if cat else "Каталог"
        return await _g["show"](
            c, f"<b>{title}</b>\n\nПока пусто.",
            kb([[_g["B"]("ru", "back", "m:shop")]]),
        )
    buttons = [[service_btn(row)] for row in rows]
    buttons.append([_g["B"]("ru", "back", "m:shop")])
    cat = await db("SELECT name FROM categories WHERE id=?", (cid,), "one")
    title = html.escape(cat["name"]) if cat else "Сервис"
    await _g["show"](c, f"<b>{title}</b>\n\nВыберите сервис\nКоличество: 1", kb(buttons))


async def open_card(c, sid):
    service = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
    if not service:
        return await c.answer("Сервис не найден", show_alert=True)
    try:
        await c.answer("Проверяем наличие…")
    except Exception:
        pass
    try:
        service = await refresh_service(service)
    except FsmsError as exc:
        return await replace_cb(c, f"❌ {html.escape(str(exc))}", home_kb())
    stock = await stock_of(service["remote_id"])
    cat = await db("SELECT name FROM categories WHERE id=?", (service["cat_id"],), "one")
    back = f"z:pick:{service['cat_id']}"
    others = await db("SELECT COUNT(*) AS n FROM fsms_services WHERE cat_id=?", (service["cat_id"],), "one")
    if others and others["n"] > 1:
        back = f"z:fs:{service['cat_id']}"
    markup = kb([
        [_g["mk_btn"]("ПОЛУЧИТЬ НОМЕР", f"z:get:{service['id']}", "5206401524200145033")],
        [_g["B"]("ru", "back", back)],
    ])
    await replace_cb(c, card_text(service, stock, cat["name"] if cat else service["name"]), markup)


async def service_label(service):
    name = service["name"] or "Сервис"
    emoji = service["emoji_id"] or None
    return name, emoji


async def remember_order(user_id, service, worker, sid, phone):
    name, emoji = await service_label(service)
    price = float(service["price"])
    conn = _g["conn"]
    async with conn() as dbc:
        dbc.row_factory = aiosqlite.Row
        await dbc.execute("BEGIN IMMEDIATE")
        user = await (await dbc.execute("SELECT balance FROM users WHERE id=?", (user_id,))).fetchone()
        if not user or float(user["balance"]) + 1e-9 < price:
            await dbc.rollback()
            return None
        await dbc.execute("UPDATE users SET balance=ROUND(balance-?,2) WHERE id=?", (price, user_id))
        cur = await dbc.execute(
            """INSERT INTO fsms_orders(
                 user_id, service_id, service_name, emoji_id, remote_id, worker_id, remote_sid,
                 phone, code, price, status, issued_at, created
               ) VALUES(?,?,?,?,?,?,?,?, '', ?, 'issued', ?, ?)""",
            (user_id, service["id"], name, emoji, service["remote_id"], worker["id"],
             sid, phone, price, time.time(), _g["now"]()),
        )
        await dbc.commit()
        return cur.lastrowid


async def give_back(user_id, amount):
    await db("UPDATE users SET balance=ROUND(balance+?,2) WHERE id=?", (amount, user_id))


async def charge_user(user_id, amount):
    conn = _g["conn"]
    async with conn() as dbc:
        await dbc.execute("BEGIN IMMEDIATE")
        user = await (await dbc.execute("SELECT balance FROM users WHERE id=?", (user_id,))).fetchone()
        if not user or float(user["balance"]) + 1e-9 < float(amount):
            await dbc.rollback()
            return False
        await dbc.execute("UPDATE users SET balance=ROUND(balance-?,2) WHERE id=?", (amount, user_id))
        await dbc.commit()
        return True


async def take_number(c, sid):
    uid = c.from_user.id
    buying = _g["BUYING"]
    if uid in buying:
        return await c.answer()
    buying.add(uid)
    worker = sid_remote = phone = None
    try:
        service = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
        if not service or not await enabled():
            return await c.answer("Поставщик выключен", show_alert=True)
        user = await db("SELECT balance FROM users WHERE id=?", (uid,), "one")
        if not user or float(user["balance"]) + 1e-9 < float(service["price"]):
            return await c.answer(_g["alert_text"]("ru", "no_balance"), show_alert=True)
        await c.answer()
        try:
            fresh = await refresh_service(service)
        except FsmsError as exc:
            return await replace_cb(c, f"❌ {html.escape(str(exc))}", home_kb())
        if float(user["balance"]) + 1e-9 < float(fresh["price"]):
            return await replace_cb(c, "❌ Недостаточно средств на балансе.", home_kb())
        if await stock_of(fresh["remote_id"]) <= 0:
            return await replace_cb(c, NO_SIM, home_kb())
        if not await free_workers():
            return await join_queue(c, fresh)
        await replace_cb(c, search_text(fresh), kb([]))
        try:
            worker, sid_remote, phone = await acquire(fresh["remote_id"])
        except FsmsError as exc:
            if "линий" in str(exc):
                return await join_queue(c, fresh)
            raise
        oid = await remember_order(uid, fresh, worker, sid_remote, phone)
        if oid is None:
            try:
                await cancel_remote(worker, sid_remote)
            except FsmsError:
                log.exception("release after empty balance")
            return await replace_cb(c, "❌ Недостаточно средств на балансе.", home_kb())
        saved = await db("SELECT service_name, emoji_id FROM fsms_orders WHERE id=?", (oid,), "one")
        sent = await replace_cb(
            c,
            number_text(saved["service_name"], phone, fresh["emoji_id"] or saved["emoji_id"], fresh["emoji_char"]),
            number_kb(oid),
        )
        if sent is not None:
            await db("UPDATE fsms_orders SET msg_id=? WHERE id=?", (sent.message_id, oid))
    except FsmsError as exc:
        if worker and sid_remote:
            try:
                await cancel_remote(worker, sid_remote)
            except FsmsError:
                log.exception("release after failed issue")
        text = NO_SIM if "не выдал" in str(exc) else f"❌ {html.escape(str(exc))}"
        await replace_cb(c, text, home_kb())
    finally:
        buying.discard(uid)


def spawn(bot, oid):
    task = _tasks.get(oid)
    if task and not task.done():
        return
    _tasks[oid] = asyncio.create_task(watch(bot, oid))


async def show_code(bot, order, code):
    fresh = order
    await show_order(bot, fresh, await order_code_text(fresh, code), code_kb(fresh["id"]))


async def fail_sms(bot, order):
    oid = order["id"]
    had_code = False
    async with order_lock(oid):
        fresh = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
        if not fresh or fresh["status"] != "waiting":
            return
        if (fresh["code"] or "").strip():
            had_code = True
            await dbw(
                "UPDATE fsms_orders SET status='coded' WHERE id=? AND status='waiting'",
                (oid,),
            )
        else:
            code = ""
            try:
                code = await pull_code(fresh, timeout=3)
            except FsmsError:
                code = ""
            if code:
                await db(
                    "UPDATE fsms_orders SET code=?, status='coded' WHERE id=? AND status='waiting'",
                    (merge_code(fresh["code"], code), oid),
                )
                fresh = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
                await show_code(bot, fresh, fresh["code"])
                return
            changed = await dbw(
                "UPDATE fsms_orders SET status='missed' WHERE id=? AND status='waiting'",
                (oid,),
            )
            if not changed:
                return
            await give_back(fresh["user_id"], fresh["price"])
    fresh = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
    if had_code:
        await show_order(
            bot, fresh,
            await order_code_text(fresh, extra=f"\n\n{E_WARN} Следующее SMS не пришло."),
            code_kb(oid),
        )
        return
    await show_order(bot, fresh, FAIL_SMS, miss_kb(oid))


async def watch(bot, oid):
    try:
        while True:
            order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
            if not order or order["status"] != "waiting":
                return
            left = SMS_WAIT - (time.time() - float(order["wait_at"] or time.time()))
            if left <= 0:
                await fail_sms(bot, order)
                return
            started = time.monotonic()
            try:
                code = await pull_code(order, timeout=min(15, max(1, int(left))))
            except FsmsError as exc:
                log.warning("code poll %s: %s", oid, exc)
                await asyncio.sleep(1)
                continue
            if not code:
                if time.monotonic() - started < 1:
                    await asyncio.sleep(0.4)
                continue
            async with order_lock(oid):
                current = await db("SELECT code FROM fsms_orders WHERE id=?", (oid,), "one")
                changed = await dbw(
                    "UPDATE fsms_orders SET code=?, status='coded' WHERE id=? AND status='waiting'",
                    (merge_code(current["code"] if current else "", code), oid),
                )
            if not changed:
                return
            fresh = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
            await show_code(bot, fresh, fresh["code"])
            return
    except Exception:
        log.exception("watch %s", oid)


async def mark_sent(c, oid):
    order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
    if not order or order["user_id"] != c.from_user.id:
        return await c.answer("Заказ не найден", show_alert=True)
    if order["status"] == "waiting":
        spawn(c.bot, oid)
        return await c.answer("Код ещё в пути")
    if order["status"] != "issued":
        return await c.answer("Код по этому номеру уже обработан", show_alert=True)
    changed = await dbw(
        "UPDATE fsms_orders SET status='waiting', wait_at=? WHERE id=? AND status='issued'",
        (time.time(), oid),
    )
    if not changed:
        return await c.answer()
    await c.answer("Ждём SMS")
    emoji_id, emoji_char = await service_face(order)
    sent = await replace_cb(c, wait_text(order["service_name"], order["phone"], emoji_id, emoji_char), wait_kb(oid))
    if sent is not None:
        await db("UPDATE fsms_orders SET msg_id=? WHERE id=?", (sent.message_id, oid))
    spawn(c.bot, oid)


async def finish_order(c, oid):
    order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
    if not order or order["user_id"] != c.from_user.id:
        return await c.answer("Заказ не найден", show_alert=True)
    if order["status"] == "done":
        return await c.answer("Номер уже закрыт")
    if order["status"] != "coded" or not order["code"]:
        return await c.answer("Кнопка доступна после получения кода", show_alert=True)
    changed = await dbw(
        "UPDATE fsms_orders SET status='done' WHERE id=? AND status='coded'",
        (oid,),
    )
    if not changed:
        return await c.answer()
    await replace_cb(
        c,
        await order_code_text(order, extra=f"\n\n{E_OK} Готово"),
        home_kb(),
    )
    await c.answer("Готово")
    await pump_queue(c.bot)


async def wait_more(c, oid):
    order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
    if not order or order["user_id"] != c.from_user.id:
        return await c.answer("Заказ не найден", show_alert=True)
    if order["status"] == "waiting":
        spawn(c.bot, oid)
        return await c.answer("Код ещё в пути")
    if order["status"] != "coded" or not order["code"]:
        return await c.answer("Сначала дождитесь кода", show_alert=True)
    busy = await db(
        "SELECT id FROM fsms_orders WHERE worker_id=? AND status IN ('issued','waiting','missed','coded') AND id!=?",
        (order["worker_id"], oid), "one",
    )
    if busy:
        return await c.answer("Линия уже занята другим номером", show_alert=True)
    changed = await dbw(
        "UPDATE fsms_orders SET status='waiting', wait_at=? WHERE id=? AND status='coded'",
        (time.time(), oid),
    )
    if not changed:
        return await c.answer()
    await c.answer("Ждём ещё SMS")
    emoji_id, emoji_char = await service_face(order)
    sent = await replace_cb(c, wait_text(order["service_name"], order["phone"], emoji_id, emoji_char), kb([]))
    if sent is not None:
        await db("UPDATE fsms_orders SET msg_id=? WHERE id=?", (sent.message_id, oid))
    spawn(c.bot, oid)


async def retry_code(c, oid):
    order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
    if not order or order["user_id"] != c.from_user.id:
        return await c.answer("Заказ не найден", show_alert=True)
    if order["status"] == "waiting":
        spawn(c.bot, oid)
        return await c.answer("Код ещё в пути")
    if order["status"] != "missed":
        return await c.answer("Повтор уже недоступен", show_alert=True)
    if not await charge_user(order["user_id"], order["price"]):
        return await c.answer(_g["alert_text"]("ru", "no_balance"), show_alert=True)
    changed = await dbw(
        "UPDATE fsms_orders SET status='waiting', wait_at=? WHERE id=? AND status='missed'",
        (time.time(), oid),
    )
    if not changed:
        await give_back(order["user_id"], order["price"])
        return await c.answer()
    await c.answer("Ждём SMS")
    emoji_id, emoji_char = await service_face(order)
    sent = await replace_cb(
        c, wait_text(order["service_name"], order["phone"], emoji_id, emoji_char), wait_kb(oid),
    )
    if sent is not None:
        await db("UPDATE fsms_orders SET msg_id=? WHERE id=?", (sent.message_id, oid))
    spawn(c.bot, oid)


async def drop_missed(c, oid):
    async with order_lock(oid):
        order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
        if not order or order["user_id"] != c.from_user.id:
            return await c.answer("Заказ не найден", show_alert=True)
        if order["status"] != "missed":
            return await c.answer("Этот номер уже закрыт", show_alert=True)
        code = ""
        worker = await db("SELECT * FROM fsms_workers WHERE id=?", (order["worker_id"],), "one")
        if worker and order["remote_sid"]:
            try:
                code = await cancel_remote(worker, order["remote_sid"])
            except FsmsError as exc:
                return await c.answer(str(exc), show_alert=True)
        if code:
            await db(
                "UPDATE fsms_orders SET code=?, status='coded' WHERE id=? AND status='missed'",
                (merge_code(order["code"], code), oid),
            )
            fresh = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
            await charge_user(order["user_id"], order["price"])
            await replace_cb(c, await order_code_text(fresh), code_kb(oid))
            return await c.answer("Код получен")
        changed = await dbw(
            "UPDATE fsms_orders SET status='cancelled' WHERE id=? AND status='missed'",
            (oid,),
        )
        if not changed:
            return await c.answer()
    await replace_cb(c, "Номер отменён. Средства возвращены на баланс.", home_kb())
    await c.answer("Номер отменён")
    await pump_queue(c.bot)


async def cancel_order(c, oid):
    async with order_lock(oid):
        order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
        if not order or order["user_id"] != c.from_user.id:
            return await c.answer("Заказ не найден", show_alert=True)
        if order["status"] not in ("issued", "waiting") or order["code"]:
            return await c.answer("Этот номер уже нельзя отменить", show_alert=True)
        left = CANCEL_AFTER - (time.time() - float(order["issued_at"] or 0))
        if left > 0:
            return await c.answer(f"Отмена будет доступна через {int(left) + 1} сек.", show_alert=True)
        worker = await db("SELECT * FROM fsms_workers WHERE id=?", (order["worker_id"],), "one")
        if not worker:
            return await c.answer("Воркер удалён, отмена недоступна. Напишите в поддержку.", show_alert=True)
        await c.answer("Отменяем…")
        try:
            code = await cancel_remote(worker, order["remote_sid"])
        except FsmsError as exc:
            return await replace_cb(c, f"❌ {html.escape(str(exc))}", home_kb())
        if code:
            await db(
                "UPDATE fsms_orders SET code=?, status='coded' WHERE id=? AND status IN ('issued','waiting')",
                (merge_code(order["code"], code), oid),
            )
            fresh = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
            return await replace_cb(c, await order_code_text(fresh), code_kb(oid))
        changed = await dbw(
            """UPDATE fsms_orders SET status='cancelled'
               WHERE id=? AND status IN ('issued','waiting') AND (code IS NULL OR code='')""",
            (oid,),
        )
        if not changed:
            return await replace_cb(c, "Этот номер уже нельзя отменить.", home_kb())
        await give_back(order["user_id"], order["price"])
    await replace_cb(c, "Номер отменён. Средства возвращены на баланс.", home_kb())
    await pump_queue(c.bot)


async def admin_ids():
    ids = set(_g["OWNERS"])
    for row in await db("SELECT user_id, role FROM roles", fetch="all"):
        if row["role"] in ("owner", "admin"):
            ids.add(row["user_id"])
    return ids


async def push_block(order, payload):
    worker = await db("SELECT * FROM fsms_workers WHERE id=?", (order["worker_id"],), "one")
    if not worker:
        return False

    def form():
        data = aiohttp.FormData()
        data.add_field("session_id", order["remote_sid"])
        data.add_field("screenshot", payload, filename="proof.jpg", content_type="image/jpeg")
        return data

    status, body = await fsms_call(worker, "POST", "/api/me/block", form_factory=form)
    if status != 200:
        log.warning("block %s status=%s detail=%s", order["id"], status, _detail(body))
    return status == 200


def refund_caption(row, sent):
    note = "Скриншот передан в F-SMS." if sent else "F-SMS скриншот не принял. Решение магазина всё равно нужно."
    return (
        f"Возврат #{row['rid']}\n"
        f"Сервис: {html.escape(row['service_name'] or '—')}\n"
        f"Номер: <code>{html.escape(row['phone'] or '—')}</code>\n"
        f"Цена: {money(row['price'] or 0)}\n"
        f"Код: <code>{html.escape(row['code'] or '—')}</code>\n\n"
        f"{note}"
    )


def refund_kb(rid):
    return kb([[btn("Вернуть средства", f"a:fsms:ok:{rid}")], [btn("Отклонить", f"a:fsms:no:{rid}")]])


async def deliver_refund(bot, chat_id, row):
    caption = refund_caption(row, row["fsms_sent"])
    markup = refund_kb(row["rid"])
    if row["file_id"]:
        try:
            await bot.send_photo(chat_id, row["file_id"], caption=caption, reply_markup=markup)
            return
        except Exception:
            log.exception("refund photo %s", row["rid"])
    await bot.send_message(chat_id, caption, reply_markup=markup)


async def save_shot(message, oid):
    order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
    if not order or order["user_id"] != message.from_user.id:
        await message.answer("Заказ не найден.")
        return
    if order["status"] != "coded" or not order["code"]:
        await message.answer("Спор доступен только после получения кода.")
        return
    photo = message.photo[-1]
    buf = io.BytesIO()
    tg_file = await message.bot.get_file(photo.file_id)
    await message.bot.download_file(tg_file.file_path, buf)
    try:
        sent = await push_block(order, buf.getvalue())
    except FsmsError as exc:
        log.warning("block upload %s: %s", oid, exc)
        sent = False
    conn = _g["conn"]
    async with conn() as dbc:
        await dbc.execute("BEGIN IMMEDIATE")
        exists = await (await dbc.execute(
            "SELECT id FROM fsms_refunds WHERE order_id=?", (oid,)
        )).fetchone()
        if exists:
            await dbc.rollback()
            await message.answer("Заявка по этому номеру уже отправлена. Статус — в разделе «Возвраты».")
            return
        cur = await dbc.execute(
            """INSERT INTO fsms_refunds(order_id, user_id, file_id, status, fsms_sent, created)
               VALUES(?,?,?,'pending',?,?)""",
            (oid, order["user_id"], photo.file_id, 1 if sent else 0, _g["now"]()),
        )
        rid = cur.lastrowid
        await dbc.commit()
    row = {
        "rid": rid, "service_name": order["service_name"], "phone": order["phone"],
        "price": order["price"], "code": order["code"], "file_id": photo.file_id,
        "fsms_sent": 1 if sent else 0,
    }
    for admin_id in await admin_ids():
        try:
            await deliver_refund(message.bot, admin_id, row)
        except Exception:
            log.exception("notify admin %s", admin_id)
    await message.answer(
        "Заявка на возврат отправлена. Её отдельно рассматривает администратор магазина. "
        "Статус смотрите в разделе «Возвраты».",
        reply_markup=home_kb(),
    )


REF_STATUS = {
    "pending": "На рассмотрении",
    "refunded": "Возвращён",
    "rejected": "Отклонено",
}


def refund_line(row):
    phone = html.escape(row["phone"] or "—")
    status = REF_STATUS.get(row["status"], row["status"])
    line = f"{phone} — {status}"
    if row["status"] != "refunded" and row["reason"]:
        line += f" — {html.escape(row['reason'])}"
    if row["emoji_id"]:
        line = f'<tg-emoji emoji-id="{row["emoji_id"]}">💠</tg-emoji> {line}'
    return line


async def user_refunds(c):
    rows = await db(
        """SELECT r.status, r.reason, o.phone, o.emoji_id
           FROM fsms_refunds r JOIN fsms_orders o ON o.id=r.order_id
           WHERE r.user_id=? ORDER BY r.id DESC LIMIT 30""",
        (c.from_user.id,), "all",
    )
    if not rows:
        text = "Возвратов пока нет."
    else:
        text = "<b>Возвраты</b>\n\n" + "\n".join(refund_line(row) for row in rows)
    await _g["show"](c, text, home_kb())


def fsms_menu_kb(on, pending):
    icon = _g["ADMIN_ICON"]
    toggle = "Выключить поставщика" if on else "Включить поставщика"
    rows = [
        [_g["mk_btn"]("Сервисы", "a:fsms:s", icon["cats"])],
        [_g["mk_btn"]("Воркеры", "a:fsms:w", icon["staff"])],
        [btn(toggle, "a:fsms:on")],
        [_g["mk_btn"](f"Возвраты ({pending})", "a:fsms:r", icon["orders"])],
        [_g["admin_back_btn"]()],
    ]
    return kb(rows)


async def admin_home_fsms(ev):
    on = await enabled()
    pending = await db("SELECT COUNT(*) AS n FROM fsms_refunds WHERE status='pending'", fetch="one")
    workers = await db("SELECT COUNT(*) AS n FROM fsms_workers", fetch="one")
    services = await db("SELECT COUNT(*) AS n FROM fsms_services", fetch="one")
    text = (
        "<b>Поставщик F-SMS</b>\n"
        f"Статус: {'включён' if on else 'выключен'}\n"
        f"Воркеров: {workers['n'] if workers else 0}\n"
        f"Сервисов в магазине: {services['n'] if services else 0}\n"
        f"Возвратов на рассмотрении: {pending['n'] if pending else 0}"
    )
    await _g["show"](ev, text, fsms_menu_kb(on, pending["n"] if pending else 0))


def back_supplier():
    return kb([[btn("К поставщику", "a:fsms")], [_g["admin_back_btn"]()]])


async def admin_workers(c):
    rows = await db("SELECT * FROM fsms_workers ORDER BY id", fetch="all")
    buttons = [[btn(f"{clip(row['login'], 32)} — удалить", f"a:fsms:wd:{row['id']}")] for row in rows]
    buttons.append([btn("Добавить воркера", "a:fsms:wa")])
    buttons.append([btn("К поставщику", "a:fsms")])
    text = "<b>Воркеры F-SMS</b>\nАккаунты, с которых берутся номера и коды."
    if not rows:
        text += "\n\nПока нет ни одного аккаунта."
    await _g["show"](c, text, kb(buttons))


PAGE = 10


def page_of(items, page):
    total = len(items)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = min(max(int(page), 0), pages - 1)
    chunk = items[page * PAGE:(page + 1) * PAGE]
    return chunk, page, pages


def page_row(prefix, page, pages):
    row = []
    if pages > 1 and page > 0:
        row.append(btn("Назад", f"{prefix}:{page - 1}"))
    if pages > 1 and page + 1 < pages:
        row.append(btn("Вперёд", f"{prefix}:{page + 1}"))
    return row


async def admin_services(c):
    added = await db("SELECT COUNT(*) AS n FROM fsms_services WHERE cat_id!=0", fetch="one")
    n = added["n"] if added else 0
    await _g["show"](
        c,
        f"<b>Сервисы F-SMS</b>\n\nВ каталогах: <b>{n}</b>",
        kb([
            [btn("Добавленные", "a:fsms:sl:0")],
            [btn("Добавить", "a:fsms:sadd:0")],
            [btn("К поставщику", "a:fsms")],
        ]),
    )


async def refresh_local_prices(rows):
    try:
        remote = {row["id"]: row for row in await public_services(force=True)}
    except FsmsError:
        return rows
    changed = False
    for item in rows:
        src = remote.get(int(item["remote_id"]))
        if not src:
            continue
        price = max(float(item["price"]), float(src["price"]))
        if abs(price - float(item["price"])) > 1e-9 or abs(float(src["price"]) - float(item["base_price"])) > 1e-9:
            await db(
                "UPDATE fsms_services SET base_price=?, price=? WHERE id=?",
                (src["price"], price, item["id"]),
            )
            changed = True
    if not changed:
        return rows
    return await db(
        """SELECT s.*, c.name AS cat_name FROM fsms_services s
           LEFT JOIN categories c ON c.id=s.cat_id
           WHERE s.cat_id!=0 ORDER BY s.name""",
        fetch="all",
    )


async def added_services(c, page):
    rows = await db(
        """SELECT s.*, c.name AS cat_name FROM fsms_services s
           LEFT JOIN categories c ON c.id=s.cat_id
           WHERE s.cat_id!=0 ORDER BY s.name""",
        fetch="all",
    )
    rows = await refresh_local_prices(rows or [])
    chunk, page, pages = page_of(rows or [], page)
    buttons = [[service_btn(row, f"a:fsms:sv:{row['id']}")] for row in chunk]
    nav = page_row("a:fsms:sl", page, pages)
    if nav:
        buttons.append(nav)
    buttons.append([btn("К сервисам", "a:fsms:s")])
    text = "<b>Добавленные сервисы</b>"
    if not rows:
        text += "\n\nПока ни один сервис не добавлен в каталог."
    elif pages > 1:
        text += f"\n\nСтраница {page + 1} из {pages}"
    await _g["show"](c, text, kb(buttons))


async def available_services(c, page):
    try:
        remote = await public_services(force=True)
    except FsmsError as exc:
        return await _g["show"](c, f"❌ {html.escape(str(exc))}", kb([[btn("К сервисам", "a:fsms:s")]]))
    linked = await db("SELECT remote_id FROM fsms_services WHERE cat_id!=0", fetch="all")
    taken = {int(row["remote_id"]) for row in linked or []}
    fresh = [row for row in remote if int(row["id"]) not in taken]
    chunk, page, pages = page_of(fresh, page)
    buttons = [[btn(
        f"{clip(row['name'], 28)} — {money(row['price'])}",
        f"a:fsms:sd:{row['id']}",
    )] for row in chunk]
    nav = page_row("a:fsms:sadd", page, pages)
    if nav:
        buttons.append(nav)
    buttons.append([btn("К сервисам", "a:fsms:s")])
    text = "<b>Добавить сервис</b>"
    if not fresh:
        text += "\n\nВсе сервисы F-SMS уже добавлены."
    elif pages > 1:
        text += f"\n\nСтраница {page + 1} из {pages}"
    await _g["show"](c, text, kb(buttons))


def emoji_line(row):
    if row["emoji_id"]:
        return "премиум"
    if row["emoji_char"]:
        return row["emoji_char"]
    return "не задан"


async def service_card(c, sid):
    row = await db(
        """SELECT s.*, c.name AS cat_name FROM fsms_services s
           LEFT JOIN categories c ON c.id=s.cat_id WHERE s.id=?""",
        (sid,), "one",
    )
    if not row:
        await c.answer("Сервис не найден", show_alert=True)
        return await admin_services(c)
    listed = int(row["cat_id"] or 0) != 0
    lines = [
        f"<b>{html.escape(row['name'])}</b>",
        f"Закуп F-SMS: {money(row['base_price'])}",
        f"Цена в магазине: {money(row['price'])}",
        f"Смайлик: {html.escape(emoji_line(row))}",
    ]
    if listed:
        lines.append(f"Каталог: {html.escape(row['cat_name'] or '—')}")
        buttons = [
            [btn("Поменять название", f"a:fsms:sn:{sid}")],
            [btn("Поменять цену", f"a:fsms:sp:{sid}")],
            [btn("Поменять смайлик", f"a:fsms:se:{sid}")],
            [btn("Убрать сервис с каталога", f"a:fsms:sx:{sid}")],
            [btn("К добавленным", "a:fsms:sl:0")],
        ]
    else:
        buttons = [
            [btn("Добавить в каталог", f"a:fsms:sk:{sid}")],
            [btn("Поменять цену", f"a:fsms:sp:{sid}")],
            [btn("Поменять смайлик", f"a:fsms:se:{sid}")],
            [btn("К списку", "a:fsms:sadd:0")],
        ]
    await _g["show"](c, "\n".join(lines), kb(buttons))


async def ensure_draft(remote_id):
    row = await db("SELECT * FROM fsms_services WHERE remote_id=?", (remote_id,), "one")
    if row:
        return row
    remote = {item["id"]: item for item in await public_services(force=True)}
    src = remote.get(int(remote_id))
    if not src:
        raise FsmsError("Этого сервиса больше нет у F-SMS")
    await db(
        """INSERT INTO fsms_services(remote_id, name, base_price, price, cat_id)
           VALUES(?,?,?,?,0)""",
        (src["id"], src["name"], src["price"], src["price"]),
    )
    return await db("SELECT * FROM fsms_services WHERE remote_id=?", (remote_id,), "one")


async def pick_category(c, sid):
    row = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
    if not row:
        return await c.answer("Сервис не найден", show_alert=True)
    cats = await db("SELECT id, name, emoji_id FROM categories ORDER BY id", fetch="all")
    if not cats:
        return await _g["show"](
            c, "Сначала создайте каталог в разделе «Каталоги».",
            kb([[btn("К сервису", f"a:fsms:sv:{sid}")]]),
        )
    buttons = [[_g["mk_btn"](cat["name"], f"a:fsms:sky:{sid}:{cat['id']}", cat["emoji_id"])] for cat in cats]
    buttons.append([btn("К сервису", f"a:fsms:sv:{sid}")])
    await _g["show"](c, f"Куда добавить <b>{html.escape(row['name'])}</b>?", kb(buttons))


async def assign_catalog(c, sid, cid):
    row = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
    cat = await db("SELECT id FROM categories WHERE id=?", (cid,), "one")
    if not row or not cat:
        return await c.answer("Сервис или каталог не найден", show_alert=True)
    if int(row["cat_id"] or 0) != 0 and int(row["cat_id"]) != int(cid):
        return await c.answer("Сервис уже стоит в каталоге", show_alert=True)
    await db("UPDATE fsms_services SET cat_id=? WHERE id=?", (cid, sid))
    await c.answer("Сервис добавлен")
    await service_card(c, sid)


def parse_price(text, minimum):
    try:
        value = round(float((text or "").replace(",", ".").strip()), 2)
    except Exception:
        return None
    if value < float(minimum) - 1e-9 or value > 100000:
        return None
    return value


async def pending_count():
    row = await db("SELECT COUNT(*) AS n FROM fsms_refunds WHERE status='pending'", fetch="one")
    return int(row["n"]) if row else 0


async def admin_refunds(c):
    count = await pending_count()
    buttons = []
    if count:
        buttons.append([btn("Рассмотреть", "a:fsms:rv")])
    buttons.append([btn("К поставщику", "a:fsms")])
    await _g["show"](c, f"<b>Возвраты</b>\n\nНа рассмотрении: <b>{count}</b>", kb(buttons))


async def review_next(c):
    row = await db(
        """SELECT r.id AS rid, r.file_id, r.fsms_sent, o.service_name, o.phone, o.price, o.code
           FROM fsms_refunds r JOIN fsms_orders o ON o.id=r.order_id
           WHERE r.status='pending' ORDER BY r.id LIMIT 1""",
        fetch="one",
    )
    if not row:
        await c.answer("Заявок нет", show_alert=True)
        return await admin_refunds(c)
    await c.answer()
    await deliver_refund(c.bot, c.from_user.id, row)


async def approve_refund(c, rid):
    conn = _g["conn"]
    async with conn() as dbc:
        dbc.row_factory = aiosqlite.Row
        await dbc.execute("BEGIN IMMEDIATE")
        cur = await dbc.execute(
            "UPDATE fsms_refunds SET status='refunded', decided_at=? WHERE id=? AND status='pending'",
            (_g["now"](), rid),
        )
        if cur.rowcount == 0:
            await dbc.rollback()
            return await c.answer("Уже обработана", show_alert=True)
        row = await (await dbc.execute(
            """SELECT o.user_id, o.price, o.phone, o.service_name
               FROM fsms_refunds r JOIN fsms_orders o ON o.id=r.order_id WHERE r.id=?""",
            (rid,),
        )).fetchone()
        await dbc.execute(
            "UPDATE users SET balance=ROUND(balance+?,2) WHERE id=?",
            (row["price"], row["user_id"]),
        )
        await dbc.execute("UPDATE fsms_orders SET status='refunded' WHERE id=(SELECT order_id FROM fsms_refunds WHERE id=?)", (rid,))
        await dbc.commit()
    try:
        await c.bot.send_message(
            row["user_id"],
            f"Возврат по номеру <code>{html.escape(row['phone'] or '—')}</code> одобрен. "
            f"На баланс зачислено {money(row['price'])}.",
        )
    except Exception:
        log.exception("notify refund user")
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.answer("Средства возвращены")


async def resume(bot):
    rows = await db("SELECT id FROM fsms_orders WHERE status='waiting'", fetch="all")
    for row in rows or []:
        spawn(bot, row["id"])
    coded = await db(
        """SELECT * FROM fsms_orders
           WHERE status='coded' AND msg_id IS NOT NULL AND IFNULL(code,'')!=''
             AND id IN (
                SELECT MAX(id) FROM fsms_orders WHERE status='coded' GROUP BY user_id
             )""",
        fetch="all",
    )
    for order in coded or []:
        try:
            await show_order(
                bot, order,
                await order_code_text(order),
                code_kb(order["id"]),
            )
        except Exception:
            log.exception("refresh code card %s", order["id"])
    await pump_queue(bot)


async def shutdown():
    for task in list(_tasks.values()):
        task.cancel()
    _tasks.clear()
    for client in list(_clients.values()):
        if not client.closed:
            await client.close()
    _clients.clear()


def register(g):
    global _g
    _g = g
    r = g["r"]
    full = g["Full"]()

    @r.callback_query(F.data.startswith("z:wh:"))
    async def warehouse(c: CallbackQuery, state: FSMContext):
        await state.clear()
        cid = int(c.data.split(":")[2])
        await open_services(c, cid)

    @r.callback_query(F.data.startswith("z:pick:"))
    async def pick(c: CallbackQuery):
        await open_services(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:fs:"))
    async def services(c: CallbackQuery):
        await open_services(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:card:"))
    async def card(c: CallbackQuery):
        await take_number(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:get:"))
    async def get_number(c: CallbackQuery):
        await take_number(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:qout:"))
    async def qout(c: CallbackQuery):
        await leave_queue(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:sent:"))
    async def sent(c: CallbackQuery):
        await mark_sent(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:ok:"))
    async def done(c: CallbackQuery):
        await finish_order(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:more:"))
    async def more(c: CallbackQuery):
        await wait_more(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:hist:"))
    async def hist_open(c: CallbackQuery):
        oid = int(c.data.split(":")[2])
        order = await db(
            "SELECT * FROM fsms_orders WHERE id=? AND user_id=?",
            (oid, c.from_user.id), "one",
        )
        if not order:
            return await c.answer("Заказ не найден", show_alert=True)
        emoji_id, emoji_char = await service_face(order)
        if order["status"] == "issued":
            return await replace_cb(
                c,
                number_text(order["service_name"], order["phone"], emoji_id, emoji_char),
                number_kb(oid),
            )
        if order["status"] == "waiting":
            markup = kb([]) if (order["code"] or "").strip() else wait_kb(oid)
            return await replace_cb(
                c, wait_text(order["service_name"], order["phone"], emoji_id, emoji_char), markup,
            )
        if order["status"] == "missed":
            return await replace_cb(c, FAIL_SMS, miss_kb(oid))
        if order["status"] == "coded" and order["code"]:
            return await replace_cb(c, await order_code_text(order), code_kb(oid))
        if order["status"] == "done" and order["code"]:
            return await replace_cb(
                c, await order_code_text(order, extra=f"\n\n{E_OK} Готово"), home_kb(),
            )
        full, local = phone_parts(order["phone"])
        status = {"cancelled": "Отменён", "refunded": "Возвращён"}.get(order["status"], order["status"])
        await replace_cb(
            c,
            f"{E_BUBBLE} Сервис: {html.escape(order['service_name'] or '')}\n\n"
            f"{E_PHONE} Номер: <code>{html.escape(full)}</code>\n"
            f"{E_PLANE} Без кода: <code>{html.escape(local)}</code>\n\n"
            f"Статус: {html.escape(status)}",
            home_kb(),
        )

    @r.callback_query(F.data.startswith("z:can:"))
    async def cancel(c: CallbackQuery):
        await cancel_order(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:drop:"))
    async def drop(c: CallbackQuery):
        await drop_missed(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:again:"))
    async def again(c: CallbackQuery):
        await retry_code(c, int(c.data.split(":")[2]))

    @r.callback_query(F.data.startswith("z:blk:"))
    async def blocked(c: CallbackQuery, state: FSMContext):
        oid = int(c.data.split(":")[2])
        order = await db("SELECT * FROM fsms_orders WHERE id=?", (oid,), "one")
        if not order or order["user_id"] != c.from_user.id:
            return await c.answer("Заказ не найден", show_alert=True)
        if order["status"] != "coded" or not order["code"]:
            return await c.answer("Кнопка доступна после получения кода", show_alert=True)
        exists = await db("SELECT id FROM fsms_refunds WHERE order_id=?", (oid,), "one")
        if exists:
            return await c.answer("Заявка уже отправлена. Смотрите раздел «Возвраты».", show_alert=True)
        await state.set_state(FsmsUser.shot)
        await state.update_data(oid=oid)
        await c.message.answer(
            "Отправьте скриншот из приложения. Он уйдёт администратору магазина.\n"
            "Без скриншота возврат не рассматривается.",
            reply_markup=kb([[btn("В меню", "m:home")]]),
        )
        await c.answer()

    @r.message(FsmsUser.shot, F.photo)
    async def shot(m: Message, state: FSMContext):
        oid = (await state.get_data()).get("oid")
        await state.clear()
        if not oid:
            return await m.answer("Заявка не найдена. Откройте номер ещё раз.")
        await save_shot(m, int(oid))

    @r.message(FsmsUser.shot)
    async def shot_need(m: Message):
        await m.answer("Нужен скриншот: отправьте фото из приложения.")

    @r.callback_query(F.data == "m:refs")
    async def refs(c: CallbackQuery):
        await user_refunds(c)

    @r.callback_query(F.data == "a:fsms", full)
    async def a_home(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await admin_home_fsms(c)

    @r.callback_query(F.data == "a:fsms:on", full)
    async def a_toggle(c: CallbackQuery):
        await set_enabled(not await enabled())
        await admin_home_fsms(c)

    @r.callback_query(F.data == "a:fsms:w", full)
    async def a_workers(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await admin_workers(c)

    @r.callback_query(F.data == "a:fsms:wa", full)
    async def a_worker_add(c: CallbackQuery, state: FSMContext):
        await state.set_state(FsmsAdm.login)
        await _g["show"](c, "Введите логин аккаунта F-SMS.", kb([[btn("Отмена", "a:fsms:w")]]))

    async def save_worker(m: Message, state: FSMContext, login, password):
        if not login or len(login) > 80 or "\n" in login:
            return await m.answer("Введите логин одним сообщением, без переноса строки.")
        if password == "":
            return await m.answer("Пароль пустой. Добавление отменено.", reply_markup=back_supplier())
        try:
            await m.delete()
        except Exception:
            pass
        try:
            client = await open_client(login, password)
            await client.close()
        except FsmsError as exc:
            await state.set_state(FsmsAdm.password)
            await state.update_data(login=login)
            log.warning("fsms login rejected for %s: %s", login, exc)
            return await m.answer(
                f"❌ {html.escape(str(exc))}\n"
                f"Логин, который ушёл в F-SMS: <code>{html.escape(login)}</code>\n"
                f"Пароль отправлен без изменений. Пришлите его ещё раз или нажмите «Отмена».",
                reply_markup=kb([[btn("Отмена", "a:fsms:w")]]),
            )
        await state.clear()
        exists = await db("SELECT id FROM fsms_workers WHERE login=?", (login,), "one")
        if exists:
            await db("UPDATE fsms_workers SET password=? WHERE id=?", (password, exists["id"]))
            await drop_client(exists["id"])
            note = "Пароль воркера обновлён."
        else:
            await db(
                "INSERT INTO fsms_workers(login, password, created) VALUES(?,?,?)",
                (login, password, _g["now"]()),
            )
            note = "Воркер добавлен."
        await m.answer(note, reply_markup=kb([[btn("К воркерам", "a:fsms:w")]]))

    @r.message(FsmsAdm.login, full, F.text)
    async def a_login(m: Message, state: FSMContext):
        raw = m.text or ""
        if "\n" in raw:
            login, password = raw.split("\n", 1)
            return await save_worker(m, state, login.strip(), password.removesuffix("\r").removesuffix("\n"))
        login = raw.strip()
        if not login or len(login) > 80:
            return await m.answer("Введите логин одним сообщением.")
        await state.set_state(FsmsAdm.password)
        await state.update_data(login=login)
        await m.answer(
            f"Логин: <code>{html.escape(login)}</code>\n"
            f"Теперь пришлите пароль одним сообщением. Пробелы в начале и в конце сохраняются."
        )

    @r.message(FsmsAdm.password, full, F.text)
    async def a_password(m: Message, state: FSMContext):
        password = m.text if m.text is not None else ""
        login = (await state.get_data()).get("login", "")
        await save_worker(m, state, login, password)

    @r.callback_query(F.data.startswith("a:fsms:wd:"), full)
    async def a_worker_del_ask(c: CallbackQuery):
        wid = int(c.data.split(":")[3])
        row = await db("SELECT * FROM fsms_workers WHERE id=?", (wid,), "one")
        if not row:
            return await c.answer("Не найден", show_alert=True)
        busy = await db(
            "SELECT COUNT(*) AS n FROM fsms_orders WHERE worker_id=? AND status IN ('issued','waiting','missed','coded')",
            (wid,), "one",
        )
        if busy and busy["n"]:
            return await c.answer("У воркера есть выданный номер. Дождитесь кода или отмены.", show_alert=True)
        await _g["show"](
            c, f"Удалить воркера <b>{html.escape(row['login'])}</b>?",
            kb([[btn("Удалить", f"a:fsms:wdy:{wid}")], [btn("К воркерам", "a:fsms:w")]]),
        )

    @r.callback_query(F.data.startswith("a:fsms:wdy:"), full)
    async def a_worker_del(c: CallbackQuery):
        wid = int(c.data.split(":")[3])
        await db("DELETE FROM fsms_workers WHERE id=?", (wid,))
        await drop_client(wid)
        await c.answer("Удалён")
        await admin_workers(c)

    @r.callback_query(F.data == "a:fsms:s", full)
    async def a_services(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await admin_services(c)

    @r.callback_query(F.data.startswith("a:fsms:sl:"), full)
    async def a_listed(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await added_services(c, int(c.data.split(":")[3]))

    @r.callback_query(F.data.startswith("a:fsms:sadd:"), full)
    async def a_available(c: CallbackQuery, state: FSMContext):
        await state.clear()
        try:
            await c.answer("Загружаю сервисы…")
        except Exception:
            pass
        await available_services(c, int(c.data.split(":")[3]))

    @r.callback_query(F.data.startswith("a:fsms:sd:"), full)
    async def a_draft(c: CallbackQuery, state: FSMContext):
        await state.clear()
        try:
            row = await ensure_draft(int(c.data.split(":")[3]))
        except FsmsError as exc:
            return await c.answer(str(exc)[:180], show_alert=True)
        await service_card(c, row["id"])

    @r.callback_query(F.data.startswith("a:fsms:sv:"), full)
    async def a_service_view(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await service_card(c, int(c.data.split(":")[3]))

    @r.callback_query(F.data.startswith("a:fsms:sk:"), full)
    async def a_pick_cat(c: CallbackQuery):
        await pick_category(c, int(c.data.split(":")[3]))

    @r.callback_query(F.data.startswith("a:fsms:sky:"), full)
    async def a_assign_cat(c: CallbackQuery):
        _, _, _, sid, cid = c.data.split(":")
        await assign_catalog(c, int(sid), int(cid))

    @r.callback_query(F.data.startswith("a:fsms:sx:"), full)
    async def a_service_del_ask(c: CallbackQuery):
        sid = int(c.data.split(":")[3])
        row = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
        if not row:
            return await c.answer("Не найден", show_alert=True)
        await _g["show"](
            c, f"Убрать <b>{html.escape(row['name'])}</b> из каталога?",
            kb([[btn("Убрать", f"a:fsms:sxy:{sid}")], [btn("Назад", f"a:fsms:sv:{sid}")]]),
        )

    @r.callback_query(F.data.startswith("a:fsms:sxy:"), full)
    async def a_service_del(c: CallbackQuery):
        await db("DELETE FROM fsms_services WHERE id=?", (int(c.data.split(":")[3]),))
        await c.answer("Сервис убран")
        await added_services(c, 0)

    @r.callback_query(F.data.startswith("a:fsms:sp:"), full)
    async def a_price(c: CallbackQuery, state: FSMContext):
        sid = int(c.data.split(":")[3])
        row = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
        if not row:
            return await c.answer("Не найден", show_alert=True)
        await state.set_state(FsmsAdm.price)
        await state.update_data(sid=sid)
        await _g["show"](
            c,
            f"Новая цена для <b>{html.escape(row['name'])}</b>.\n"
            f"Минимум — цена F-SMS: <b>{money(row['base_price'])}</b>. Ниже поставить нельзя.",
            kb([[btn("Отмена", f"a:fsms:sv:{sid}")]]),
        )

    @r.message(FsmsAdm.price, full, F.text)
    async def a_price_in(m: Message, state: FSMContext):
        sid = (await state.get_data()).get("sid")
        row = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one") if sid else None
        if not row:
            await state.clear()
            return await m.answer("Сервис не найден.", reply_markup=back_supplier())
        value = parse_price(m.text, row["base_price"])
        if value is None:
            return await m.answer(f"Введите цену не ниже {money(row['base_price'])}.")
        await state.clear()
        await db("UPDATE fsms_services SET price=? WHERE id=?", (value, sid))
        await m.answer(f"Цена {money(value)} сохранена.", reply_markup=kb([[btn("К сервису", f"a:fsms:sv:{sid}")]]))

    @r.callback_query(F.data.startswith("a:fsms:sn:"), full)
    async def a_title(c: CallbackQuery, state: FSMContext):
        sid = int(c.data.split(":")[3])
        row = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
        if not row:
            return await c.answer("Не найден", show_alert=True)
        await state.set_state(FsmsAdm.title)
        await state.update_data(sid=sid)
        await _g["show"](
            c,
            f"Название для покупателя. Сейчас: <b>{html.escape(row['name'])}</b>",
            kb([[btn("Отмена", f"a:fsms:sv:{sid}")]]),
        )

    @r.message(FsmsAdm.title, full, F.text)
    async def a_title_in(m: Message, state: FSMContext):
        sid = (await state.get_data()).get("sid")
        title, _eid = _g["parse_label"](m)
        title = title.strip()
        await state.clear()
        if not sid or not title or len(title) > 80:
            return await m.answer("Нужно название до 80 символов.", reply_markup=kb([[btn("К сервисам", "a:fsms:s")]]))
        await db("UPDATE fsms_services SET name=? WHERE id=?", (title, sid))
        await m.answer("Название сохранено.", reply_markup=kb([[btn("К сервису", f"a:fsms:sv:{sid}")]]))

    @r.callback_query(F.data.startswith("a:fsms:se:"), full)
    async def a_emoji(c: CallbackQuery, state: FSMContext):
        sid = int(c.data.split(":")[3])
        row = await db("SELECT * FROM fsms_services WHERE id=?", (sid,), "one")
        if not row:
            return await c.answer("Не найден", show_alert=True)
        await state.set_state(FsmsAdm.emoji)
        await state.update_data(sid=sid)
        await _g["show"](
            c,
            f"Смайлик для <b>{html.escape(row['name'])}</b>.\nОбычный или премиум.",
            kb([[btn("Отмена", f"a:fsms:sv:{sid}")]]),
        )

    @r.message(FsmsAdm.emoji, full)
    async def a_emoji_in(m: Message, state: FSMContext):
        sid = (await state.get_data()).get("sid")
        picked = _g["read_button_emoji"](m)
        if not picked:
            return await m.answer("Пришлите один смайлик: обычный или премиум.")
        kind, emoji = picked
        await state.clear()
        if not sid:
            return await m.answer("Сервис не найден.", reply_markup=back_supplier())
        if kind == "premium":
            await db(
                "UPDATE fsms_services SET emoji_id=?, emoji_char=NULL WHERE id=?",
                (emoji, sid),
            )
        else:
            await db(
                "UPDATE fsms_services SET emoji_id=NULL, emoji_char=? WHERE id=?",
                (emoji, sid),
            )
        await m.answer("Смайлик сохранён.", reply_markup=kb([[btn("К сервису", f"a:fsms:sv:{sid}")]]))

    @r.callback_query(F.data == "a:fsms:r", full)
    async def a_refunds(c: CallbackQuery, state: FSMContext):
        await state.clear()
        await admin_refunds(c)

    @r.callback_query(F.data == "a:fsms:rv", full)
    async def a_review(c: CallbackQuery):
        await review_next(c)

    @r.callback_query(F.data.startswith("a:fsms:ok:"), full)
    async def a_ok(c: CallbackQuery):
        await approve_refund(c, int(c.data.split(":")[3]))

    @r.callback_query(F.data.startswith("a:fsms:no:"), full)
    async def a_no(c: CallbackQuery, state: FSMContext):
        rid = int(c.data.split(":")[3])
        row = await db("SELECT id FROM fsms_refunds WHERE id=? AND status='pending'", (rid,), "one")
        if not row:
            return await c.answer("Уже обработана", show_alert=True)
        await state.set_state(FsmsAdm.reason)
        await state.update_data(rid=rid, chat=c.message.chat.id, mid=c.message.message_id)
        await c.message.answer("Напишите причину отказа. Без причины заявка не закроется.")
        await c.answer()

    @r.message(FsmsAdm.reason, full, F.text)
    async def a_reason(m: Message, state: FSMContext):
        data = await state.get_data()
        reason = m.text.strip()
        if not reason:
            return await m.answer("Причина обязательна. Напишите её текстом.")
        reason = reason[:500]
        rid = data.get("rid")
        changed = await dbw(
            "UPDATE fsms_refunds SET status='rejected', reason=?, decided_at=? WHERE id=? AND status='pending'",
            (reason, _g["now"](), rid),
        )
        await state.clear()
        if not changed:
            return await m.answer("Заявка уже обработана.")
        row = await db(
            """SELECT o.user_id, o.phone FROM fsms_refunds r
               JOIN fsms_orders o ON o.id=r.order_id WHERE r.id=?""",
            (rid,), "one",
        )
        if row:
            try:
                await m.bot.send_message(
                    row["user_id"],
                    f"Возврат по номеру <code>{html.escape(row['phone'] or '—')}</code> отклонён.\n"
                    f"Причина: {html.escape(reason)}",
                )
            except Exception:
                log.exception("notify reject")
        if data.get("mid"):
            try:
                await m.bot.edit_message_reply_markup(chat_id=data["chat"], message_id=data["mid"], reply_markup=None)
            except Exception:
                pass
        await m.answer("Отказ сохранён. Покупатель увидит причину в «Возвратах».", reply_markup=back_supplier())
