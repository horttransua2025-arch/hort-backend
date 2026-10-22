"""
HORT TRANS — Бекенд для мобільного додатку.
Читає/пише в ту саму economy.db, що й бот.
Логи в Discord через Webhooks.
"""
import sqlite3
import time
import random
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="HORT TRANS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "economy.db"

# ⚠️ ВСТАВТЕ ВАШІ URL ВЕБХУКІВ DISCORD
WEBHOOK_EARNINGS = "https://discord.com/api/webhooks/..."  # канал лог-earnings-log
WEBHOOK_ADMIN = "https://discord.com/api/webhooks/..."     # канал адмін-checks

COOLDOWN_SECONDS = 4.5 * 3600
SPOTTING_COOLDOWN = 24 * 3600


# ============================================================
# ХЕЛПЕРИ
# ============================================================
def db_connect():
    conn = sqlite3.connect(DB_FILE, timeout=15.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def send_log(webhook, title, desc, color=0x4CAF50):
    if not webhook or "..." in webhook:
        return
    try:
        requests.post(webhook, json={
            "embeds": [{
                "title": title,
                "description": desc,
                "color": color,
                "footer": {"text": "HORT TRANS Mobile App"}
            }]
        }, timeout=3)
    except Exception as e:
        print(f"Webhook error: {e}")


def ensure_user(cursor, uid):
    cursor.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, 0)", (uid,))


def get_cooldown_remaining(uid, game, custom=None):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT last_used FROM cooldowns WHERE user_id = ? AND game_name = ?", (uid, game))
    row = c.fetchone()
    now = int(time.time())
    conn.close()
    if not row:
        return 0
    cd = custom if custom else COOLDOWN_SECONDS
    return max(0, cd - (now - row[0]))


def set_cooldown(uid, game):
    conn = db_connect()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO cooldowns (user_id, game_name, last_used) VALUES (?, ?, ?)",
              (uid, game, int(time.time())))
    conn.commit()
    conn.close()


def update_coins(uid, amount):
    conn = db_connect()
    c = conn.cursor()
    ensure_user(c, uid)
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, uid))
    conn.commit()
    conn.close()


def get_coins(uid):
    conn = db_connect()
    c = conn.cursor()
    ensure_user(c, uid)
    c.execute("SELECT coins FROM users WHERE user_id = ?", (uid,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else 0


# ============================================================
# МОДЕЛІ
# ============================================================
class LoginReq(BaseModel):
    nick: str


class TransferReq(BaseModel):
    sender_id: int
    receiver_id: int
    amount: int


class GameReq(BaseModel):
    user_id: int
    bet: int = 0
    p1: Optional[str] = None   # choice (red/black/number, rock/scissors, >7/<7/=7, safe/risky, buy/sell)
    p2: Optional[int] = None   # number (mines cell, roulette number, thimbles 1-3)


# ============================================================
# АВТОРИЗАЦІЯ ЗА НІКОМ TRUCKBOOK
# ============================================================
@app.post("/api/login")
def login(req: LoginReq):
    nick = req.nick.strip()
    if not nick:
        raise HTTPException(400, "Введіть нік")
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT user_id, truckbook_nick FROM users WHERE LOWER(truckbook_nick) = LOWER(?)", (nick,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(
            404,
            "Нік не знайдено. Зареєструйтесь у боті командою /реєстрація в каналі #реєстрація"
        )
    return {"user_id": row[0], "nick": row[1]}


# ============================================================
# ПРОФІЛЬ ТА ВОДІЇ
# ============================================================
@app.get("/api/profile/{uid}")
def profile(uid: int):
    conn = db_connect()
    c = conn.cursor()
    ensure_user(c, uid)
    conn.commit()
    c.execute("""SELECT distance, days_in_company, coins, weekly_earnings,
                 truckbook_nick, nickname, crypto_balance
                 FROM users WHERE user_id = ?""", (uid,))
    row = c.fetchone()
    conn.close()
    return {
        "user_id": uid,
        "distance": row[0] or 0,
        "days": row[1] or 0,
        "coins": row[2] or 0,
        "weekly": row[3] or 0,
        "nickname": row[4] or "—",
        "discord_name": row[5] or f"Driver {uid}",
        "crypto": row[6] or 0
    }


@app.get("/api/drivers")
def drivers_list():
    conn = db_connect()
    c = conn.cursor()
    c.execute("""SELECT user_id, COALESCE(truckbook_nick, nickname, 'Driver'),
                 coins, distance, weekly_earnings FROM users
                 WHERE coins > 0 OR distance > 0 OR truckbook_nick IS NOT NULL
                 ORDER BY coins DESC""")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "coins": r[2] or 0,
             "distance": r[3] or 0, "weekly": r[4] or 0} for r in rows]


# ============================================================
# ІСТОРІЯ РЕЙСІВ
# ============================================================
@app.get("/api/trips/{uid}")
def user_trips(uid: int):
    try:
        conn = db_connect()
        c = conn.cursor()
        c.execute("""SELECT distance, reward, tariff, timestamp FROM trip_history
                     WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10""", (uid,))
        rows = c.fetchall()
        conn.close()
        return [{"distance": r[0], "reward": r[1], "tariff": r[2], "time": r[3]} for r in rows]
    except sqlite3.OperationalError:
        return []


@app.get("/api/trips/recent/all")
def global_trips():
    try:
        conn = db_connect()
        c = conn.cursor()
        c.execute("""SELECT user_id, distance, reward, tariff, timestamp FROM trip_history
                     ORDER BY timestamp DESC LIMIT 15""")
        rows = c.fetchall()
        conn.close()
        return [{"user_id": r[0], "distance": r[1], "reward": r[2],
                 "tariff": r[3], "time": r[4]} for r in rows]
    except sqlite3.OperationalError:
        return []


# ============================================================
# ПЕРЕКАЗ
# ============================================================
@app.post("/api/transfer")
def transfer(req: TransferReq):
    if req.amount <= 0:
        raise HTTPException(400, "Сума > 0")
    if req.sender_id == req.receiver_id:
        raise HTTPException(400, "Не можна собі")
    conn = db_connect()
    c = conn.cursor()
    ensure_user(c, req.sender_id)
    ensure_user(c, req.receiver_id)
    c.execute("SELECT coins FROM users WHERE user_id = ?", (req.sender_id,))
    if c.fetchone()[0] < req.amount:
        conn.close()
        raise HTTPException(400, "Недостатньо коїнів")
    c.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (req.amount, req.sender_id))
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (req.amount, req.receiver_id))
    conn.commit()
    conn.close()
    send_log(WEBHOOK_EARNINGS, "🏦 ПЕРЕКАЗ / TRANSFER",
             f"<@{req.sender_id}> ➜ <@{req.receiver_id}>\n**{req.amount} HC**", 0x00BFFF)
    return {"message": f"✅ Передано {req.amount} HC"}


# ============================================================
# ХЕЛПЕР ДЛЯ ІГОР
# ============================================================
def game_precheck(uid, bet, game, custom_cd=None):
    if bet > 0 and get_coins(uid) < bet:
        raise HTTPException(400, "Недостатньо коїнів")
    rem = get_cooldown_remaining(uid, game, custom_cd)
    if rem > 0:
        raise HTTPException(429, f"⏳ Зачекайте {rem//3600}г {(rem%3600)//60}хв")
    if bet > 0:
        set_cooldown(uid, game)


# ============================================================
# ІГРИ
# ============================================================
@app.post("/api/game/casino")
def casino(req: GameReq):
    game_precheck(req.user_id, req.bet, "casino")
    slots = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
    r = [random.choice(slots) for _ in range(3)]
    if r[0] == r[1] == r[2]:
        mult = {"🍒": 3, "🍋": 4, "🍇": 5, "💎": 7, "7️⃣": 10}[r[0]]
        win = req.bet * mult
        update_coins(req.user_id, win - req.bet)
        text, color = f"🎉 JACKPOT! {r} +{win} HC (x{mult})", 0x4CAF50
        send_log(WEBHOOK_EARNINGS, "🎰 КАЗИНО", f"<@{req.user_id}> +{win} HC", 0x4CAF50)
    elif r[0] == r[1] or r[1] == r[2] or r[0] == r[2]:
        win = int(req.bet * 1.5)
        update_coins(req.user_id, win - req.bet)
        text, color = f"💵 {r} +{win} HC", 0x8BC34A
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"📉 {r} -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/blackjack")
def blackjack(req: GameReq):
    game_precheck(req.user_id, req.bet, "blackjack")
    deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4
    random.shuffle(deck)

    def score(h):
        s, aces = sum(h), h.count(11)
        while s > 21 and aces:
            s -= 10
            aces -= 1
        return s

    p = [deck.pop(), deck.pop()]
    d = [deck.pop(), deck.pop()]
    while score(p) < 17:
        p.append(deck.pop())
    while score(d) < 17:
        d.append(deck.pop())
    ps, ds = score(p), score(d)

    if ps > 21:
        update_coins(req.user_id, -req.bet)
        text, color = f"💥 ПЕРЕБІР! {p} ({ps}) / {d} ({ds}) -{req.bet} HC", 0xF44336
    elif ds > 21 or ps > ds:
        update_coins(req.user_id, req.bet)
        text, color = f"🎉 ПЕРЕМОГА! {p} ({ps}) / {d} ({ds}) +{req.bet} HC", 0x4CAF50
        send_log(WEBHOOK_EARNINGS, "🃏 БЛЕКДЖЕК", f"<@{req.user_id}> +{req.bet} HC", 0x4CAF50)
    elif ps < ds:
        update_coins(req.user_id, -req.bet)
        text, color = f"📉 {p} ({ps}) / {d} ({ds}) -{req.bet} HC", 0xF44336
    else:
        text, color = f"🤝 Нічия {p} ({ps}) / {d} ({ds})", 0xFFEB3B
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/thimbles")
def thimbles(req: GameReq):
    if req.p2 not in [1, 2, 3]:
        raise HTTPException(400, "Оберіть 1, 2 або 3")
    game_precheck(req.user_id, req.bet, "thimbles")
    win = random.randint(1, 3)
    if req.p2 == win:
        update_coins(req.user_id, req.bet)
        text, color = f"🎯 №{win}! +{req.bet*2} HC", 0x4CAF50
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"🥛 Мимо, було №{win}. -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/wheel")
def wheel(req: GameReq):
    game_precheck(req.user_id, req.bet, "wheel")
    m = random.choice([0, 0.2, 0.5, 1, 1.5, 2, 3])
    win = int(req.bet * m)
    update_coins(req.user_id, win - req.bet)
    if m > 1:
        color = 0x4CAF50
    elif m == 1:
        color = 0xFFEB3B
    else:
        color = 0xF44336
    return {"result": f"🛞 x{m} → {win-req.bet:+d} HC", "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/road_event")
def road_event(req: GameReq):
    game_precheck(req.user_id, 0, "road_event")
    events = [
        ("⛽ Дешеве паливо", 150, 0x4CAF50),
        ("🦌 Олень", 250, 0x4CAF50),
        ("🚨 Поліція похвалила", 100, 0x4CAF50),
        ("🔧 Поломка", -150, 0xF44336),
        ("🥖 Круасани", -50, 0xFFEB3B),
    ]
    t, rw, col = random.choice(events)
    update_coins(req.user_id, rw)
    return {"result": f"{t} → {rw:+d} HC", "color": col, "balance": get_coins(req.user_id)}


@app.post("/api/game/dice")
def dice(req: GameReq):
    if req.p1 not in [">7", "<7", "=7"]:
        raise HTTPException(400, "Оберіть варіант")
    game_precheck(req.user_id, req.bet, "dice")
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    t = d1 + d2
    win = (req.p1 == ">7" and t > 7) or (req.p1 == "<7" and t < 7) or (req.p1 == "=7" and t == 7)
    if win:
        mult = 5 if req.p1 == "=7" else 2
        r = req.bet * mult
        update_coins(req.user_id, r - req.bet)
        text, color = f"🎲 {d1}+{d2}={t}! +{r} HC (x{mult})", 0x4CAF50
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"🎲 {d1}+{d2}={t}. -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/roulette")
def roulette(req: GameReq):
    if req.p1 not in ["red", "black", "number"]:
        raise HTTPException(400, "Оберіть колір або число")
    game_precheck(req.user_id, req.bet, "roulette")
    reds = [1, 3, 5, 7, 9, 11, 13]
    blacks = [2, 4, 6, 8, 10, 12]
    res = random.randint(0, 13)
    win, mult = False, 2
    if req.p1 == "red" and res in reds:
        win = True
    elif req.p1 == "black" and res in blacks:
        win = True
    elif req.p1 == "number" and req.p2 == res:
        win, mult = True, 14
    if win:
        r = req.bet * mult
        update_coins(req.user_id, r - req.bet)
        text, color = f"🎡 {res}! +{r} HC (x{mult})", 0x4CAF50
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"🎡 {res}. -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/double")
def double(req: GameReq):
    game_precheck(req.user_id, req.bet, "double")
    if random.choice([True, False]):
        update_coins(req.user_id, req.bet)
        text, color = f"🎉 Heads! +{req.bet} HC", 0x4CAF50
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"😵 Tails! -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/mines")
def mines(req: GameReq):
    if not req.p2 or not 1 <= req.p2 <= 9:
        raise HTTPException(400, "Клітинка 1-9")
    game_precheck(req.user_id, req.bet, "mines")
    bombs = set(random.sample(range(9), 4))
    if (req.p2 - 1) not in bombs:
        win = int(req.bet * 1.5)
        update_coins(req.user_id, win - req.bet)
        text, color = f"💎 Скарб! +{win} HC", 0x4CAF50
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"💥 Міна! -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/smuggler")
def smuggler(req: GameReq):
    if req.p1 not in ["safe", "risky"]:
        raise HTTPException(400, "Оберіть маршрут")
    game_precheck(req.user_id, req.bet, "smuggler")
    ok, mult = (random.random() < 0.8, 1.5) if req.p1 == "safe" else (random.random() < 0.5, 3)
    if ok:
        win = int(req.bet * mult)
        update_coins(req.user_id, win - req.bet)
        text, color = f"🚛 Успіх! +{win} HC (x{mult})", 0x4CAF50
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"🚨 Провал! -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/rps")
def rps(req: GameReq):
    if req.p1 not in ["rock", "scissors", "paper"]:
        raise HTTPException(400, "Оберіть жест")
    game_precheck(req.user_id, req.bet, "rps")
    bc = random.choice(["rock", "scissors", "paper"])
    u = req.p1
    if u == bc:
        text, color = f"🤝 Нічия (бот: {bc})", 0xFFEB3B
    elif (u == "rock" and bc == "scissors") or (u == "scissors" and bc == "paper") or (u == "paper" and bc == "rock"):
        update_coins(req.user_id, req.bet)
        text, color = f"🎉 Перемога! (бот: {bc}) +{req.bet} HC", 0x4CAF50
    else:
        update_coins(req.user_id, -req.bet)
        text, color = f"😵 Поразка (бот: {bc}) -{req.bet} HC", 0xF44336
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/russian")
def russian(req: GameReq):
    game_precheck(req.user_id, req.bet, "russian")
    if random.randint(1, 6) == 1:
        update_coins(req.user_id, -req.bet)
        text, color = f"💥 Бах! -{req.bet} HC", 0xF44336
    else:
        win = int(req.bet * 1.2)
        update_coins(req.user_id, win - req.bet)
        text, color = f"🔫 Клац! Вижив! +{win} HC", 0x4CAF50
    return {"result": text, "color": color, "balance": get_coins(req.user_id)}


@app.post("/api/game/crypto")
def crypto(req: GameReq):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT value FROM system_stats WHERE key='crypto_rate'")
    r = c.fetchone()
    if not r:
        rate = 100
        c.execute("INSERT INTO system_stats (key, value) VALUES ('crypto_rate', 100)")
    else:
        rate = r[0]
        c.execute("SELECT value FROM system_stats WHERE key='crypto_last_update'")
        lu = c.fetchone()
        now = int(time.time())
        if not lu or now - lu[0] > 300:
            rate = max(50, min(200, rate + random.randint(-15, 15)))
            c.execute("UPDATE system_stats SET value=? WHERE key='crypto_rate'", (rate,))
            c.execute("INSERT OR REPLACE INTO system_stats (key, value) VALUES ('crypto_last_update', ?)", (now,))
    conn.commit()
    ensure_user(c, req.user_id)
    c.execute("SELECT coins, crypto_balance FROM users WHERE user_id=?", (req.user_id,))
    coins, cb = c.fetchone()
    cb = cb or 0

    if req.p1 == "buy":
        cost = req.bet * rate
        if coins < cost:
            conn.close()
            raise HTTPException(400, f"Треба {cost} HC")
        c.execute("UPDATE users SET coins=coins-?, crypto_balance=crypto_balance+? WHERE user_id=?",
                  (cost, req.bet, req.user_id))
        conn.commit()
        conn.close()
        return {"result": f"📈 Куплено {req.bet} крипти за {cost} HC (курс {rate})",
                "color": 0x4CAF50, "balance": coins - cost}
    elif req.p1 == "sell":
        if cb < req.bet:
            conn.close()
            raise HTTPException(400, f"У вас лише {cb} крипти")
        rev = req.bet * rate
        c.execute("UPDATE users SET coins=coins+?, crypto_balance=crypto_balance-? WHERE user_id=?",
                  (rev, req.bet, req.user_id))
        conn.commit()
        conn.close()
        return {"result": f"📉 Продано {req.bet} крипти за {rev} HC (курс {rate})",
                "color": 0x4CAF50, "balance": coins + rev}
    conn.close()
    raise HTTPException(400, "Оберіть buy/sell")


@app.post("/api/game/spotting")
def spotting(req: GameReq):
    rem = get_cooldown_remaining(req.user_id, "spotting", SPOTTING_COOLDOWN)
    if rem > 0:
        raise HTTPException(429, f"⏳ Наступний споттінг через {rem//3600}г {(rem%3600)//60}хв")
    set_cooldown(req.user_id, "spotting")
    hc = random.randint(5, 10)
    update_coins(req.user_id, hc)
    send_log(WEBHOOK_EARNINGS, "🕵️ СПОТТІНГ", f"<@{req.user_id}> +{hc} HC", 0x00BFFF)
    return {"result": f"🕵️ Споттінг зараховано! +{hc} HC",
            "color": 0x00BFFF, "balance": get_coins(req.user_id)}


if __name__ == "__main__":
    import uvicorn
    print("🚀 HORT TRANS backend: http://localhost:8000")
    print("📖 API docs: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)