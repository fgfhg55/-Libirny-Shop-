import asyncio
import base64
import json
import logging
import os
import sys
import time

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo)

# --- Настройки из переменных окружения (задаются в панели хостинга) ---
TOKEN = os.getenv("BOT_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "fgfhg55/-Libirny-Shop-"
PAGES_BASE = "https://fgfhg55.github.io/-Libirny-Shop-"
WEB_APP_URL = PAGES_BASE + "/"

OWNER_IDS = [7104234275,6771466053]
ADMINS_DEFAULT = OWNER_IDS + [6771466053]

PRODUCTS_FILE = "products.json"
STREETS_FILE = "streets.json"
ORDERS_FILE = "orders.json"
ADMINS_FILE = "admins.json"
PROMO_FILE = "promocodes.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()
session = None


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def norm_streets(raw):
    out = []
    for s in raw:
        if isinstance(s, str):
            out.append({"name": s, "min": 0, "max": 50})
        elif isinstance(s, dict):
            out.append({"name": s.get("name", "—"),
                        "min": int(s.get("min", s.get("delivery", 0))),
                        "max": int(s.get("max", s.get("delivery", 50)))})
    return out


products = load_json(PRODUCTS_FILE, [])
streets = norm_streets(load_json(STREETS_FILE, [
    {"name": "Российская", "min": 0, "max": 0},
    {"name": "Чоппа", "min": 10, "max": 100},
]))
orders = load_json(ORDERS_FILE, [])
admins = load_json(ADMINS_FILE, ADMINS_DEFAULT)
promo = load_json(PROMO_FILE, {})


def is_admin(uid):
    return uid in admins


def street_names():
    return [s["name"] for s in streets]


def fl_stock(f, st):
    return int((f.get("stock") or {}).get(st, 0))


async def gh_upload(path, data_bytes):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}",
               "Accept": "application/vnd.github+json"}
    try:
        async with session.get(url, headers=headers) as r:
            sha = (await r.json()).get("sha") if r.status == 200 else None
        body = {"message": "bot update " + path,
                "content": base64.b64encode(data_bytes).decode()}
        if sha:
            body["sha"] = sha
        async with session.put(url, headers=headers, json=body) as r:
            if r.status not in (200, 201):
                logging.error("GitHub error: " + await r.text())
                return False
            return True
    except Exception as e:
        logging.error(f"GitHub error: {e}")
        return False


async def push_products():
    return await gh_upload("products.json",
        json.dumps(products, ensure_ascii=False, indent=1).encode("utf-8"))


async def push_streets():
    return await gh_upload("streets.json",
        json.dumps(streets, ensure_ascii=False, indent=1).encode("utf-8"))


async def push_promo():
    return await gh_upload("promocodes.json",
        json.dumps(promo, ensure_ascii=False, indent=1).encode("utf-8"))


async def push_orders():
    return await gh_upload("orders.json",
        json.dumps(orders, ensure_ascii=False, indent=1).encode("utf-8"))


async def download_photo(file_id):
    f = await bot.get_file(file_id)
    url = f"https://api.telegram.org/file/bot{TOKEN}/{f.file_path}"
    async with session.get(url) as r:
        return await r.read()


class AddProduct(StatesGroup):
    street = State()
    photo = State()
    name = State()
    price = State()
    flavors = State()
    stock = State()


class AddStreet(StatesGroup):
    name = State()
    delivery = State()


class Restock(StatesGroup):
    qty = State()


@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    rows = [
        [KeyboardButton(text="🛒 Открыть магазин", web_app=WebAppInfo(url=WEB_APP_URL))],
        [KeyboardButton(text="📦 Каталог"), KeyboardButton(text="🎟 Мои промокоды")],
    ]
    if is_admin(m.from_user.id):
        rows.append([KeyboardButton(text="⚙️ Управление"), KeyboardButton(text="ℹ️ Инфо")])
    else:
        rows.append([KeyboardButton(text="ℹ️ Инфо")])
    await m.answer(
        "✨ Добро пожаловать в Libirny Shop! ✨\n\n"
        "🛒 Магазин — витрина с товарами\n"
        "📦 Каталог — что есть в наличии\n"
        "🎟 Мои промокоды — твои скидки\n"
        "ℹ️ Инфо — доставка и контакты",
        reply_markup=ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True))


@dp.message(Command("id"))
async def id_cmd(m: types.Message):
    await m.answer(f"Твой ID: `{m.from_user.id}`", parse_mode="Markdown")


@dp.message(Command("addadmin"))
async def addadmin_cmd(m: types.Message):
    if m.from_user.id not in OWNER_IDS:
        return
    parts = (m.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await m.answer("Формат: /addadmin 123456789")
    uid = int(parts[1])
    if uid in admins:
        return await m.answer("Этот ID уже админ.")
    admins.append(uid)
    save_json(ADMINS_FILE, admins)
    await m.answer(f"✅ ID {uid} теперь админ.")


@dp.message(F.text == "📦 Каталог")
async def catalog_btn(m: types.Message):
    if not products:
        return await m.answer("Товаров пока нет — скоро добавим!")
    lines = []
    for p in products:
        s = f"• {p['name']} — {p['price']} ₽ ({p.get('cat', '—')}, 📍 {', '.join(p.get('streets', []) or ['—'])})"
        if p.get("flavors"):
            fl = []
            for f in p["flavors"]:
                per = ", ".join(f"{st} ×{fl_stock(f, st)}"
                                for st in p.get("streets", []) if fl_stock(f, st) > 0)
                fl.append(f"{f['name']}: {per or 'нет в наличии'}")
            s += "\n   🎯 " + "; ".join(fl)
        lines.append(s)
    await m.answer("📦 Каталог:\n\n" + "\n".join(lines) +
                   "\n\nЧтобы оформить заказ — жми «🛒 Открыть магазин».")


@dp.message(F.text == "🎟 Мои промокоды")
async def promo_btn(m: types.Message):
    mine = [(c, d) for c, d in promo.items() if d.get("user_id") == m.from_user.id]
    if not mine:
        return await m.answer("🎟 Промокодов пока нет.")
    lines = []
    for c, d in mine:
        mark = "✅ использован" if d.get("used") else "🟢 активен"
        lines.append(f"• `{c}` — скидка {d.get('percent', 0)}% ({mark}, от {d.get('created', '—')})")
    await m.answer("🎟 Твои промокоды:\n\n" + "\n".join(lines) +
                   "\n\nВводи код в корзине сайта — скидка применится сама.",
                   parse_mode="Markdown")


@dp.message(F.text == "ℹ️ Инфо")
async def info_btn(m: types.Message):
    st = "\n".join(f"📍 {s['name']} — " + ("бесплатно" if s["min"] == 0 and s["max"] == 0
                   else f"{s['min']}–{s['max']} ₽ (по расстоянию)") for s in streets)
    await m.answer("ℹ️ Libirny Shop\n\n🚚 Доставка:\n" + (st or "пока не задана") +
                   "\n🏠 Самовывоз с точки наличия — бесплатно\n"
                   "💧 Жижки и 💨 подики — каталог 18+\n❓ Вопросы — просто напиши сюда!")


# --- Заказ с сайта ---
@dp.message(F.web_app_data)
async def web_order(m: types.Message):
    try:
        o = json.loads(m.web_app_data.data)
    except Exception:
        return await m.answer("📦 Пришла кривая заявка — напиши менеджеру вручную.")

    uid = m.from_user.id
    code = (o.get("promo") or "").strip().upper()
    used_promo = None
    promo_ok = False
    d = None

    if code:
        d = promo.get(code)
        if d and not d.get("used") and d.get("user_id") == uid:
            d["used"] = True
            promo_ok = True
            used_promo = f"🎟 Промокод {code} применён: −{d.get('percent')}%"
        else:
            used_promo = f"⚠️ Промокод {code} недействителен (чужой или уже использован)"

    lines = []
    for i in o.get("items", []):
        fl = f" · 🎯 {i['flavor']} ({i.get('street', '—')})" if i.get("flavor") else ""
        lines.append(f"• {i['name']} × {i['qty']} — {i['price']} ₽{fl}")

    if str(o.get("order", "")).startswith("самовывоз"):
        head = f"📦 Новый заказ!\n\n🏠 {o.get('order')}\n\n" + "\n".join(lines)
        dev_line = "🏠 Самовывоз: бесплатно"
    else:
        km = o.get("km")
        km_s = f" ({km} км)" if km is not None else ""
        head = (f"📦 Новый заказ!\n\n🚚 Доставка{km_s}\n📍 Точка отгрузки: {o.get('shop') or '—'}\n"
                f"🏠 Адрес: {o.get('address', '—')}\n\n" + "\n".join(lines))
        dev_line = f"🚚 Доставка: {o.get('delivery', 0)} ₽"

    text = (head + f"\n\n💵 Товары: {o.get('total', 0)} ₽\n{dev_line}\n"
            f"💰 Итого: {o.get('total', 0) + o.get('delivery', 0)} ₽\n"
            f"👤 {o.get('name', '—')}\n📞 {o.get('phone', '—')}")
    if used_promo:
        text += f"\n\n{used_promo}"
    if o.get("note"):
        text += f"\n👅 Примечание: {o['note']}"
    text += f"\n\nОт: {m.from_user.full_name} (ID: {uid})"

    for aid in admins:
        try:
            await bot.send_message(aid, text)
        except Exception:
            pass

    orders.append({"ts": time.strftime("%Y-%m-%d %H:%M"),
                   "type": o.get("order", "доставка"),
                   "address": o.get("address", ""),
                   "total": o.get("total", 0),
                   "delivery": o.get("delivery", 0),
                   "user_id": uid,
                   "name": o.get("name", ""),
                   "phone": o.get("phone", ""),
                   "promo": code if promo_ok else None,
                   "items": o.get("items", [])})
    save_json(ORDERS_FILE, orders)
    await push_orders()
    if promo_ok:
        save_json(PROMO_FILE, promo)
        await push_promo()

    reply = "✅ Заказ принят! Менеджер скоро свяжется с тобой."
    if used_promo:
        reply += "\n" + used_promo
    await m.answer(reply)


# --- Админка ---
@dp.message(F.text == "⚙️ Управление")
async def admin_menu(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="add")],
        [InlineKeyboardButton(text="💊 Пополнить вкус", callback_data="restock")],
        [InlineKeyboardButton(text="📍 Добавить улицу", callback_data="addst")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data="dellist")],
        [InlineKeyboardButton(text="📦 Список товаров", callback_data="prolist")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
    ])
    await m.answer("Панель управления:", reply_markup=kb)


@dp.callback_query(F.data == "add")
async def add_start(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddProduct.street)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📍 {s}", callback_data="pickstreet:" + s)]
        for s in street_names()])
    await c.message.answer("📍 На какой точке товар?", reply_markup=kb)
    await c.answer()


@dp.callback_query(AddProduct.street, F.data.startswith("pickstreet:"))
async def add_street_picked(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(street=c.data.split(":", 1)[1])
    await state.set_state(AddProduct.photo)
    await c.message.answer("📷 Пришли фото товара:")
    await c.answer()


@dp.message(AddProduct.photo, F.photo)
async def add_photo(m: types.Message, state: FSMContext):
    await state.update_data(photo=m.photo[-1].file_id)
    await state.set_state(AddProduct.name)
    await m.answer("✏️ Название товара:")


@dp.message(AddProduct.photo)
async def add_photo_err(m: types.Message):
    await m.answer("Нужно фото! Нажми скрепку и пришли картинку.")


@dp.message(AddProduct.name)
async def add_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddProduct.price)
    await m.answer("💰 Цена (только число, например 500):")


@dp.message(AddProduct.price)
async def add_price(m: types.Message, state: FSMContext):
    try:
        price = int(m.text.strip().replace(" ", ""))
    except ValueError:
        return await m.answer("Это не число. Введи цену цифрами:")
    await state.update_data(price=price)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💧 Жижки", callback_data="setcat:💧 Жижки"),
         InlineKeyboardButton(text="💨 Подики", callback_data="setcat:💨 Подики")]])
    await m.answer("Категория:", reply_markup=kb)


@dp.callback_query(AddProduct.price, F.data.startswith("setcat:"))
async def add_flavors(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(cat=c.data.split(":", 1)[1])
    await state.set_state(AddProduct.flavors)
    await c.message.answer("🎯 Вкусы через запятую (например: Манго, Лёд)\n"
                           "или напиши «-», если товар без вкусов.")
    await c.answer()


@dp.message(AddProduct.flavors)
async def add_flavors_input(m: types.Message, state: FSMContext):
    if m.text.strip() in ("-", "—", "нет"):
        await state.update_data(flavors=[])
        await state.set_state(AddProduct.stock)
        return await m.answer("🔢 Сколько штук в наличии? Одно число (например 5):")
    fl = []
    for part in m.text.replace(";", ",").split(","):
        p = part.strip()
        if p and p not in fl:
            fl.append(p)
    if not fl:
        return await m.answer("Не понял. Вкусы через запятую или «-» без вкусов.")
    await state.update_data(flavors=fl)
    await state.set_state(AddProduct.stock)
    await m.answer("🔢 Сколько каждого вкуса на этой точке?\n"
                   "Одно число (например 3)\nили по-отдельности: Манго=2, Лёд=4")


@dp.message(AddProduct.stock)
async def add_stock(m: types.Message, state: FSMContext):
    data = await state.get_data()
    fls = data.get("flavors", [])
    txt = m.text.strip().replace(" ", "")
    result = []
    try:
        if fls and "=" in txt:
            for part in txt.split(","):
                name, q = part.split("=", 1)
                q = int(q)
                for f in fls:
                    if f.lower() == name.lower().strip():
                        result.append({"name": f, "qty": q})
            for f in fls:
                if not any(r["name"] == f for r in result):
                    result.append({"name": f, "qty": 0})
            if not result:
                return await m.answer("Ни один вкус не совпал. Пример: Манго=2, Лёд=4")
        elif fls:
            q = int(txt)
            result = [{"name": f, "qty": q} for f in fls]
        elif not fls:
            q = int(txt)
            result = []
    except (ValueError, IndexError):
        return await m.answer("Не понял число. Например: 3 или Манго=2, Лёд=4")

    await state.clear()
    street = data.get("street", "—")
    code = "p" + str(int(time.time() * 1000))
    photo_bytes = await download_photo(data["photo"])
    ok_img = await gh_upload(f"images/{code}.jpg", photo_bytes)
    flavors = [{"name": r["name"], "stock": {street: r["qty"]}} for r in result]
    products.append({"code": code, "name": data["name"], "price": data["price"],
                     "cat": data.get("cat", "—"), "street": street,
                     "streets": [street], "flavors": flavors,
                     "img": f"{PAGES_BASE}/images/{code}.jpg"})
    save_json(PRODUCTS_FILE, products)
    ok_json = await push_products()
    fl_txt = "; ".join(f"{r['name']} ×{r['qty']} на «{street}»" for r in result) or "без вкусов"
    if ok_img and ok_json:
        await m.answer(f"✅ «{data['name']}» добавлен на «{street}»!\n🎯 Вкусы: {fl_txt}\n"
                       f"На сайте появится через 1–2 минуты.")
    else:
        await m.answer("❌ Ошибка GitHub. Проверь переменную GITHUB_TOKEN в настройках.")


@dp.callback_query(F.data == "restock")
async def restock_start(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    if not products:
        return await c.message.answer("Нет товаров — добавь через «➕ Добавить товар».")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💊 {p['name']}", callback_data="rst:" + p["code"])]
        for p in products])
    await c.message.answer("Какой товар пополняем?", reply_markup=kb)


@dp.callback_query(F.data.startswith("rst:"))
async def restock_street(c: types.CallbackQuery, state: FSMContext):
    code = c.data.split(":", 1)[1]
    p = next((x for x in products if x.get("code") == code), None)
    if not p:
        return await c.answer("Товар не найден", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📍 {st}", callback_data=f"rstst:{code}:{i}")]
        for i, st in enumerate(p.get("streets", []) or ["—"])])
    await c.message.answer(f"На какой точке пополняем «{p['name']}»?", reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("rstst:"))
async def restock_flavor(c: types.CallbackQuery, state: FSMContext):
    _, code, sidx = c.data.split(":")
    p = next((x for x in products if x.get("code") == code), None)
    if not p:
        return await c.answer("Товар не найден", show_alert=True)
    sts = p.get("streets", []) or ["—"]
    st = sts[int(sidx)] if int(sidx) < len(sts) else "—"
    await state.update_data(code=code, street=st)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{f['name']} ({fl_stock(f, st)} шт)",
                              callback_data=f"rstf:{code}:{int(sidx)}:{i}")]
        for i, f in enumerate(p.get("flavors", []))])
    if not kb.inline_keyboard:
        await state.clear()
        return await c.message.answer(f"У «{p['name']}» нет вкусов — пополнять нечего.")
    await c.message.answer(f"Какой вкус пополняем на «{st}»?", reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("rstf:"))
async def restock_qty_ask(c: types.CallbackQuery, state: FSMContext):
    _, code, sidx, fidx = c.data.split(":")
    await state.update_data(code=code, sidx=int(sidx), fidx=int(fidx))
    await state.set_state(Restock.qty)
    await c.message.answer("На сколько штук пополняем? Например: 5")
    await c.answer()


@dp.message(Restock.qty)
async def restock_qty(m: types.Message, state: FSMContext):
    try:
        n = int(m.text.strip().replace(" ", ""))
    except ValueError:
        return await m.answer("Это не число. Введи количество цифрами:")
    data = await state.get_data()
    p = next((x for x in products if x.get("code") == data["code"]), None)
    if p and p.get("flavors"):
        f = p["flavors"][data["fidx"]]
        f.setdefault("stock", {})
        st = data.get("street", "—")
        f["stock"][st] = fl_stock(f, st) + n
        save_json(PRODUCTS_FILE, products)
        ok = await push_products()
        await m.answer(f"✅ «{f['name']}» у «{p['name']}» на «{st}»: теперь {f['stock'][st]} шт"
                       + ("." if ok else "\n(локально; ошибка GitHub)"))
    await state.clear()


@dp.callback_query(F.data == "addst")
async def addstreet_start(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddStreet.name)
    await c.message.answer("📍 Название улицы (например: Российская):")
    await c.answer()


@dp.message(AddStreet.name)
async def addstreet_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(AddStreet.delivery)
    await m.answer("🚚 Цены доставки: мин и макс через пробел.\n"
                   "Например: 10 100\nБесплатная доставка: 0 0")


@dp.message(AddStreet.delivery)
async def addstreet_finish(m: types.Message, state: FSMContext):
    parts = m.text.strip().replace("₽", "").split()
    try:
        mn, mx = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return await m.answer("Нужно два числа через пробел, например: 10 100")
    data = await state.get_data()
    name = data["name"]
    if not any(s["name"] == name for s in streets):
        streets.append({"name": name, "min": mn, "max": mx})
        save_json(STREETS_FILE, streets)
        ok = await push_streets()
        await m.answer(f"✅ Улица «{name}»: " + ("бесплатно" if mn == 0 and mx == 0
                       else f"доставка {mn}–{mx} ₽")
                       + ("!" if ok else "\n(локально; ошибка GitHub)"))
    else:
        await m.answer("Такая улица уже есть.")
    await state.clear()


@dp.callback_query(F.data == "prolist")
async def prolist(c: types.CallbackQuery):
    await c.answer()
    if not products:
        return await c.message.answer("Товаров пока нет.")
    lines = []
    for p in products:
        s = f"• {p['name']} — {p['price']} ₽ ({p.get('cat', '—')}, 📍 {', '.join(p.get('streets', []) or ['—'])})"
        if p.get("flavors"):
            fl = []
            for f in p["flavors"]:
                per = ", ".join(f"{st} ×{fl_stock(f, st)}"
                                for st in p.get("streets", []) if fl_stock(f, st) > 0)
                fl.append(f"{f['name']}: {per or 'нет'}")
            s += "\n   🎯 " + "; ".join(fl)
        lines.append(s)
    await c.message.answer(f"📦 Всего товаров: {len(products)}\n\n" + "\n".join(lines))


@dp.callback_query(F.data == "dellist")
async def dellist(c: types.CallbackQuery):
    await c.answer()
    if not products:
        return await c.message.answer("Товаров нет.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🗑 {p['name']}", callback_data="del:" + p["code"])]
        for p in products])
    await c.message.answer("Что удалить?", reply_markup=kb)


@dp.callback_query(F.data.startswith("del:"))
async def del_one(c: types.CallbackQuery):
    code = c.data.split(":", 1)[1]
    products[:] = [p for p in products if p["code"] != code]
    save_json(PRODUCTS_FILE, products)
    ok = await push_products()
    await c.message.edit_text("✅ Удалено! Сайт обновится через 1–2 минуты." if ok else "❌ Ошибка GitHub.")


@dp.callback_query(F.data == "stats")
async def stats_cb(c: types.CallbackQuery):
    await c.answer()
    await c.message.answer(build_stats())


def build_stats():
    if not orders:
        return "📊 Заказов пока нет — статистика появится после первых продаж."
    rev = sum(o.get("total", 0) for o in orders)
    dev = sum(o.get("delivery", 0) for o in orders)
    pick = sum(1 for o in orders if str(o.get("type", "")).startswith("самовывоз"))
    by_prod, by_flavor = {}, {}
    for o in orders:
        for it in o.get("items", []):
            p = by_prod.setdefault(it["name"], {"qty": 0, "sum": 0})
            p["qty"] += it.get("qty", 1)
            p["sum"] += it.get("qty", 1) * it.get("price", 0)
            if it.get("flavor"):
                key = f"{it['name']} · {it['flavor']}"
                by_flavor[key] = by_flavor.get(key, 0) + it.get("qty", 1)
    top = "\n".join(f"{i + 1}. {k} — {v['qty']} шт, {v['sum']} ₽"
                    for i, (k, v) in enumerate(sorted(by_prod.items(),
                                                      key=lambda kv: -kv[1]["qty"])[:5]))
    fl = "\n".join(f"• {k} — {v} шт"
                   for k, v in sorted(by_flavor.items(), key=lambda kv: -kv[1])[:7])
    return (f"📊 Статистика\nЗаказов: {len(orders)}\n🚚 Доставка: {len(orders) - pick} · "
            f"🏠 Самовывоз: {pick}\nВыручка: {rev} ₽ + доставка {dev} ₽\n\n"
            f"🔥 Топ товаров:\n{top or '—'}\n\n🎯 Популярные вкусы:\n{fl or '—'}")


async def main():
    global session
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        logging.error("Не задана переменная окружения BOT_TOKEN! Добавь её в настройках хостинга.")
        sys.exit(1)
    if not GITHUB_TOKEN:
        logging.error("Не задана переменная окружения GITHUB_TOKEN! Добавь её в настройках хостинга.")
        sys.exit(1)
    while True:
        session = aiohttp.ClientSession()
        try:
            await dp.start_polling(bot)
        except Exception as e:
            logging.error("Сбой: %s — перезапуск через 10 сек", e)
        try:
            await session.close()
        except Exception:
            pass
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
