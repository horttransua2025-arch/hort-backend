import discord
from discord.ext import commands
import sqlite3
import time
import random
import os
import re
import json
import threading
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List

# ==========================================
# ⚙️ НАЛАШТУВАННЯ
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN") # Береться з Render Environment Variables
TRUCKBOOK_CHANNEL_ID = 1556226431854583852
CHANNEL_EARNINGS_LOG = 1556226134322978836
DB_FILE = "economy.db"

# ==========================================
# БАЗА ДАНИХ
# ==========================================
def db_connect():
    conn = sqlite3.connect(DB_FILE, timeout=15.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def get_avatar_url(uid):
    avatar_id = (uid >> 22) % 6 if uid else 0
    return f"https://cdn.discordapp.com/embed/avatars/{avatar_id}.png"

def update_coins(uid, amount):
    conn = db_connect(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, 0)", (uid,))
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, uid))
    conn.commit(); conn.close()

# ==========================================
# FASTAPI (САЙТ ТА API)
# ==========================================
app = FastAPI(title="HORT TRANS API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class LoginReq(BaseModel): nick: str
class TransferReq(BaseModel): sender_id: int; receiver_id: int; amount: int
class GameReq(BaseModel): user_id: int; bet: int = 0; action: Optional[str] = None; game_data: Optional[dict] = None
class CreateRoomReq(BaseModel): host_uid: int; bet: int

@app.post("/api/login")
def login(req: LoginReq):
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT user_id, truckbook_nick FROM users WHERE LOWER(truckbook_nick) = LOWER(?)", (req.nick.strip(),))
    row = c.fetchone(); conn.close()
    if not row: raise HTTPException(404, "Нік не знайдено в базі")
    return {"user_id": row[0], "nick": row[1]}

@app.get("/api/profile/{uid}")
def profile(uid: int):
    conn = db_connect(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, 0)", (uid,)); conn.commit()
    # ПОВЕРТАЄМО ВЕСЬ ПРОФІЛЬ
    c.execute("SELECT distance, days_in_company, coins, weekly_earnings, truckbook_nick, nickname FROM users WHERE user_id = ?", (uid,))
    row = c.fetchone(); conn.close()
    return {
        "user_id": uid, 
        "distance": row[0] or 0, 
        "days": row[1] or 0, 
        "coins": row[2] or 0, 
        "weekly": row[3] or 0, 
        "nickname": row[4] or "—", 
        "discord_name": row[5] or f"Driver {uid}", 
        "avatar": get_avatar_url(uid)
    }

@app.get("/api/trips/{uid}")
def user_trips(uid: int):
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT distance, reward, tariff, timestamp FROM trip_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10", (uid,))
    rows = c.fetchall(); conn.close()
    return [{"distance": r[0], "reward": r[1], "tariff": r[2], "time": r[3]} for r in rows]

@app.post("/api/transfer")
def transfer(req: TransferReq):
    if req.amount <= 0 or req.sender_id == req.receiver_id: raise HTTPException(400, "Помилка")
    conn = db_connect(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, 0)", (req.sender_id,))
    c.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, 0)", (req.receiver_id,))
    c.execute("SELECT coins FROM users WHERE user_id = ?", (req.sender_id,))
    if c.fetchone()[0] < req.amount: conn.close(); raise HTTPException(400, "Недостатньо коїнів")
    c.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (req.amount, req.sender_id))
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (req.amount, req.receiver_id))
    conn.commit(); conn.close()
    return {"message": f"✅ Передано {req.amount} HC"}

# --- ІГРИ ---
@app.post("/api/game/casino")
def casino(req: GameReq):
    slots = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
    r = [random.choice(slots) for _ in range(3)]
    win = req.bet * {"🍒": 3, "🍋": 4, "🍇": 5, "💎": 7, "7️⃣": 10}[r[0]] if r[0] == r[1] == r[2] else (int(req.bet * 1.5) if (r[0] == r[1] or r[1] == r[2] or r[0] == r[2]) else 0)
    update_coins(req.user_id, win - req.bet)
    return {"reels": r, "win": win, "bet": req.bet}

@app.post("/api/game/dice")
def dice(req: GameReq):
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    t = d1 + d2
    win = req.bet * (5 if req.action == "=7" else 2) if ((req.action == ">7" and t > 7) or (req.action == "<7" and t < 7) or (req.action == "=7" and t == 7)) else 0
    update_coins(req.user_id, win - req.bet)
    return {"d1": d1, "d2": d2, "total": t, "win": win, "bet": req.bet}

@app.post("/api/game/mines")
def mines(req: GameReq):
    chosen = int(req.action)
    bombs = random.sample(range(9), 4)
    win = int(req.bet * 1.5) if chosen not in bombs else 0
    update_coins(req.user_id, win - req.bet)
    return {"chosen": chosen, "bombs": bombs, "win": win, "bet": req.bet}

@app.post("/api/game/rps")
def rps(req: GameReq):
    bc = random.choice(["rock", "scissors", "paper"])
    u = req.action
    win = req.bet if u == bc else (req.bet * 2 if (u=="rock" and bc=="scissors") or (u=="scissors" and bc=="paper") or (u=="paper" and bc=="rock") else 0)
    update_coins(req.user_id, win - req.bet)
    return {"user_choice": u, "bot_choice": bc, "win": win, "bet": req.bet}

@app.post("/api/game/thimbles")
def thimbles(req: GameReq):
    win_cup = random.randint(1, 3)
    win = req.bet * 2 if int(req.action) == win_cup else 0
    update_coins(req.user_id, win - req.bet)
    return {"win_cup": win_cup, "chosen": int(req.action), "win": win, "bet": req.bet}

@app.post("/api/game/wheel")
def wheel(req: GameReq):
    m = random.choice([0, 0.2, 0.5, 1, 1.5, 2, 3])
    win = int(req.bet * m)
    update_coins(req.user_id, win - req.bet)
    return {"multiplier": m, "win": win, "bet": req.bet}

@app.post("/api/game/roulette")
def roulette(req: GameReq):
    reds = [1, 3, 5, 7, 9, 11, 13]; blacks = [2, 4, 6, 8, 10, 12]
    res = random.randint(0, 13)
    win = req.bet * 14 if (req.action == "number" and req.game_data.get('num') == res) else (req.bet * 2 if ((req.action == "red" and res in reds) or (req.action == "black" and res in blacks)) else 0)
    update_coins(req.user_id, win - req.bet)
    return {"number": res, "color": "red" if res in reds else ("black" if res in blacks else "green"), "win": win, "bet": req.bet}

@app.post("/api/game/double")
def double(req: GameReq):
    side = random.choice(["heads", "tails"])
    win = req.bet if req.action == side else 0
    update_coins(req.user_id, win - req.bet)
    return {"side": side, "win": win, "bet": req.bet}

@app.post("/api/game/russian")
def russian(req: GameReq):
    bullet = random.randint(1, 6) == 1
    win = 0 if bullet else int(req.bet * 1.2)
    update_coins(req.user_id, win - req.bet)
    return {"bullet": bullet, "win": win, "bet": req.bet}

@app.post("/api/game/smuggler")
def smuggler(req: GameReq):
    is_safe = req.action == "safe"
    success = random.random() < (0.8 if is_safe else 0.5)
    win = int(req.bet * (1.5 if is_safe else 3)) if success else 0
    update_coins(req.user_id, win - req.bet)
    return {"success": success, "win": win, "bet": req.bet}

@app.post("/api/game/blackjack")
def blackjack(req: GameReq):
    SUITS = ['♠', '♥', '♦', '♣']; RANKS = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']
    def score(h):
        s = sum([10 if c['rank'] in ['J','Q','K'] else (11 if c['rank']=='A' else int(c['rank'])) for c in h])
        a = sum([1 for c in h if c['rank']=='A'])
        while s > 21 and a > 0: s -= 10; a -= 1
        return s
    if req.action == "start":
        deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS] * 4; random.shuffle(deck)
        p_hand = [deck.pop(), deck.pop()]; d_hand = [deck.pop(), deck.pop()]
        return {"status":"playing", "p_hand":p_hand, "d_hand":[d_hand[0], {"rank":"?","suit":"?"}], "p_score":score(p_hand), "d_score":"?", "deck":deck, "bet":req.bet}
    elif req.action == "hit":
        deck = req.game_data['deck']; p_hand = req.game_data['p_hand'] + [deck.pop()]
        if score(p_hand) > 21:
            update_coins(req.user_id, -req.bet)
            return {"status":"finished", "p_hand":p_hand, "d_hand":req.game_data['d_hand'], "p_score":score(p_hand), "d_score":"?", "result":f"Перебір! -{req.bet} HC", "win": 0, "bet": req.bet}
        return {"status":"playing", "p_hand":p_hand, "d_hand":req.game_data['d_hand'], "p_score":score(p_hand), "d_score":"?", "deck":deck, "bet":req.bet}
    elif req.action == "stand":
        deck = req.game_data['deck']; d_hand = req.game_data['d_hand']
        if d_hand[1]['rank'] == "?": d_hand[1] = deck.pop()
        while score(d_hand) < 17: d_hand.append(deck.pop())
        ps, ds = score(req.game_data['p_hand']), score(d_hand)
        if ds > 21 or ps > ds: update_coins(req.user_id, req.bet); res = f"🎉 +{req.bet} HC"; win_amt = req.bet
        elif ps < ds: update_coins(req.user_id, -req.bet); res = f"📉 -{req.bet} HC"; win_amt = 0
        else: res = "🤝 Нічия"; win_amt = req.bet
        return {"status":"finished", "p_hand":req.game_data['p_hand'], "d_hand":d_hand, "p_score":ps, "d_score":ds, "result":res, "win": win_amt, "bet": req.bet}

# ==========================================
# МУЛЬТИПЛЕЄР (ДУРЕНЬ - ПОВНА ЛОГІКА)
# ==========================================
SUITS = ['♠', '♥', '♦', '♣']; RANKS_36 = ['6','7','8','9','10','J','Q','K','A']
def can_beat(a, d, t): return (d['suit'] == a['suit'] and RANKS_36.index(d['rank']) > RANKS_36.index(a['rank'])) or (d['suit'] == t and a['suit'] != t)

class Player:
    def __init__(self, uid, nick, avatar, websocket):
        self.uid = uid; self.nick = nick; self.avatar = avatar
        self.ws = websocket; self.hand = []; self.is_ready = False

class Room:
    def __init__(self, room_id, bet, host_uid):
        self.room_id = room_id; self.bet = bet; self.host_uid = host_uid
        self.players: Dict[int, Player] = {}; self.deck = []
        self.trump = ""; self.table = []; self.status = "waiting"
        self.turn_uid = None # Хто зараз ходить (атакує)
        self.defender_uid = None # Хто зараз захищається

    def get_player_list(self):
        return [{"id": p.uid, "nick": p.nick, "avatar": p.avatar, "ready": p.is_ready, "cards_count": len(p.hand)} for p in self.players.values()]

    def start_game(self):
        self.deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS_36]
        random.shuffle(self.deck); self.trump = self.deck[-1]['suit']
        for p in self.players.values():
            p.hand = [self.deck.pop() for _ in range(6)]; p.is_ready = False
        self.table = []; self.status = "playing"
        self.turn_uid = list(self.players.keys())[0]
        self.defender_uid = list(self.players.keys())[1]

rooms: Dict[str, Room] = {}

@app.post("/api/create_room")
def create_room(req: CreateRoomReq):
    room_id = str(random.randint(1000, 9999))
    rooms[room_id] = Room(room_id, req.bet, req.host_uid)
    return {"room_id": room_id}

@app.websocket("/ws/{room_id}/{uid}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, uid: int):
    await websocket.accept()
    if room_id not in rooms: await websocket.close(); return
    room = rooms[room_id]
    
    # Отримуємо дані гравця з бази
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT COALESCE(truckbook_nick, nickname, 'Driver') FROM users WHERE user_id=?", (uid,))
    row = c.fetchone(); conn.close()
    nick = row[0] if row else f"Driver {uid}"
    
    player = Player(uid, nick, get_avatar_url(uid), websocket)
    room.players[uid] = player
    
    await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})
    
    try:
        while True:
            data = await websocket.receive_text(); msg = json.loads(data)
            
            if msg['type'] == 'ready':
                player.is_ready = True
                if len(room.players) >= 2 and all(p.is_ready for p in room.players.values()):
                    room.start_game()
                    await broadcast_game_state(room)
                else:
                    await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})
            
            elif msg['type'] == 'attack':
                if uid == room.turn_uid and room.status == "playing":
                    card_idx = msg['card_idx']
                    if card_idx < len(player.hand):
                        card = player.hand.pop(card_idx)
                        room.table.append({"attack": card, "defend": None})
                        await broadcast_game_state(room)
                        
            elif msg['type'] == 'defend':
                if uid == room.defender_uid and room.status == "playing" and room.table and room.table[-1]['defend'] is None:
                    card_idx = msg['card_idx']
                    if card_idx < len(player.hand):
                        card = player.hand[card_idx]
                        if can_beat(room.table[-1]['attack'], card, room.trump):
                            player.hand.pop(card_idx)
                            room.table[-1]['defend'] = card
                            await broadcast_game_state(room)
                            
            elif msg['type'] == 'take':
                if uid == room.defender_uid and room.status == "playing":
                    # Забирає всі карти зі столу
                    for pair in room.table:
                        player.hand.append(pair['attack'])
                        if pair['defend']: player.hand.append(pair['defend'])
                    room.table = []
                    # Добираємо карти з колоди
                    while len(player.hand) < 6 and room.deck: player.hand.append(room.deck.pop())
                    # Хід переходить до наступного
                    room.turn_uid = room.defender_uid
                    room.defender_uid = room.get_next_player(room.defender_uid)
                    await broadcast_game_state(room)
                    
            elif msg['type'] == 'pass':
                if uid == room.turn_uid and room.status == "playing":
                    # Перевіряємо чи всі відбиті
                    if all(p['defend'] is not None for p in room.table):
                        room.table = [] # Скидаємо в відбій
                        # Добираємо карти
                        for p in room.players.values():
                            while len(p.hand) < 6 and room.deck: p.hand.append(room.deck.pop())
                        # Хід переходить до захисника
                        room.turn_uid = room.defender_uid
                        room.defender_uid = room.get_next_player(room.defender_uid)
                        await broadcast_game_state(room)

    except WebSocketDisconnect:
        if uid in room.players: del room.players[uid]
        if len(room.players) == 0: del rooms[room_id]
        else: await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})

async def broadcast(room: Room, message: dict):
    for p in room.players.values():
        try: await p.ws.send_json(message)
        except: pass

async def broadcast_game_state(room: Room):
    for uid, p in room.players.items():
        await p.ws.send_json({
            "type": "game_update", 
            "trump": room.trump, 
            "table": room.table, 
            "my_hand": p.hand, 
            "players": room.get_player_list(),
            "turn_uid": room.turn_uid,
            "defender_uid": room.defender_uid
        })

# ==========================================
# DISCORD БОТ
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready(): print(f"✅ Бот {bot.user} запущений!")

@bot.event
async def on_message(message):
    if message.author == bot.user: return
    if message.channel.id == TRUCKBOOK_CHANNEL_ID and message.embeds:
        for embed in message.embeds:
            nickname = embed.author.name.strip() if embed.author and embed.author.name else ""
            full_text = (embed.description or "") + "\n" + "\n".join([f.value for f in embed.fields if f.value])
            dist_match = re.search(r"Прийнята відстань:\s*([\d\s,\.]+)\s*km", full_text, re.IGNORECASE)
            stat_match = re.search(r"Статистика:\s*(.+)", full_text, re.IGNORECASE)
            
            if nickname and dist_match and stat_match:
                distance = int(re.sub(r"[\s,\.]", "", dist_match.group(1)))
                tariff = stat_match.group(1).strip()
                rate = 15 if "wot" in tariff.lower() else (10 if "реал" in tariff.lower() else 5)
                coins = max(1, int((distance / 1000) * rate))
                
                conn = db_connect(); c = conn.cursor()
                c.execute("SELECT user_id FROM users WHERE LOWER(truckbook_nick) = LOWER(?)", (nickname,))
                user_row = c.fetchone()
                
                if user_row:
                    uid = user_row[0]
                    c.execute("UPDATE users SET distance = distance + ?, coins = coins + ?, weekly_earnings = weekly_earnings + ? WHERE user_id = ?", (distance, coins, coins, uid))
                    c.execute("INSERT INTO trip_history (user_id, distance, reward, tariff, timestamp) VALUES (?, ?, ?, ?, ?)", (uid, distance, coins, tariff, int(time.time())))
                    conn.commit()
                    
                    log_channel = bot.get_channel(CHANNEL_EARNINGS_LOG)
                    if log_channel:
                        log_embed = discord.Embed(title="📦 РЕЙС ЗАРАХОВАНО", color=0x4CAF50)
                        log_embed.description = f"Водій **{nickname}** здав рейс!\n🛣️ {distance:,} км\n💰 +{coins} HC"
                        await log_channel.send(embed=log_embed)
                conn.close()
    await bot.process_commands(message)

def run_bot():
    if TOKEN: bot.run(TOKEN)
    else: print("⚠️ Токен Discord не знайдено!")

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
