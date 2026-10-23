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
TOKEN = os.getenv("DISCORD_TOKEN") 
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
    c.execute("INSERT OR IGNORE INTO users (user_id, coins, mini_coins) VALUES (?, 0, 0)", (uid,))
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
class MiniReq(BaseModel): user_id: int; mini_coins: int

@app.post("/api/login")
def login(req: LoginReq):
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT user_id, truckbook_nick FROM users WHERE LOWER(truckbook_nick) = LOWER(?)", (req.nick.strip(),))
    row = c.fetchone(); conn.close()
    if not row: raise HTTPException(404, "Нік не знайдено в базі")
    return {"status": "success", "user_id": row[0], "nick": row[1]}

@app.get("/api/profile/{uid}")
def profile(uid: int):
    conn = db_connect(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, coins, mini_coins) VALUES (?, 0, 0)", (uid,)); conn.commit()
    c.execute("""SELECT distance, days_in_company, coins, weekly_earnings, truckbook_nick, nickname, 
                 COALESCE(mini_coins, 0), COALESCE(games_played, 0), COALESCE(games_won, 0) 
                 FROM users WHERE user_id = ?""", (uid,))
    row = c.fetchone(); conn.close()
    return {
        "status": "success", "user_id": uid, 
        "distance": row[0] or 0, "days_in_company": row[1] or 0, 
        "coins": row[2] or 0, "weekly_earnings": row[3] or 0, 
        "truckbook_nick": row[4] or "—", "nickname": row[5] or f"Driver {uid}", 
        "mini_coins": row[6], "games_played": row[7], "games_won": row[8],
        "avatar": get_avatar_url(uid),
        # Заглушки для красивої статистики
        "total_earned": row[7] * 100, "total_lost": 0, "wot_battles": 0, "wot_wins": 0,
        "distance_wot": 0, "distance_real": row[0], "distance_race": 0
    }

@app.post("/api/update_mini")
def update_mini(req: MiniReq):
    conn = db_connect(); c = conn.cursor()
    c.execute("UPDATE users SET mini_coins = ? WHERE user_id = ?", (req.mini_coins, req.user_id))
    conn.commit(); conn.close()
    return {"status": "success"}

@app.post("/api/exchange_mini")
def exchange_mini(req: MiniReq):
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT mini_coins, coins FROM users WHERE user_id = ?", (req.user_id,))
    row = c.fetchone()
    if not row or row[0] < req.mini_coins: conn.close(); raise HTTPException(400, "Недостатньо міні-HC")
    new_mini = row[0] - req.mini_coins
    new_coins = row[1] + (req.mini_coins // 1000)
    c.execute("UPDATE users SET mini_coins = ?, coins = ? WHERE user_id = ?", (new_mini, new_coins, req.user_id))
    conn.commit(); conn.close()
    return {"status": "success", "new_coins": new_coins, "new_mini_coins": new_mini}

@app.post("/api/update_balance")
def update_balance(req: GameReq):
    # Оновлення статистики ігор
    conn = db_connect(); c = conn.cursor()
    c.execute("UPDATE users SET games_played = COALESCE(games_played, 0) + 1, games_won = COALESCE(games_won, 0) + ? WHERE user_id = ?", (1 if req.bet > 0 else 0, req.user_id))
    conn.commit(); conn.close()
    return {"status": "success"}

# --- ІГРИ ---
@app.post("/api/game/casino")
def casino(req: GameReq):
    slots = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
    r = [random.choice(slots) for _ in range(3)]
    win = req.bet * {"🍒": 3, "🍋": 4, "🍇": 5, "💎": 7, "7️⃣": 10}[r[0]] if r[0] == r[1] == r[2] else (int(req.bet * 1.5) if (r[0] == r[1] or r[1] == r[2] or r[0] == r[2]) else 0)
    update_coins(req.user_id, win - req.bet)
    return {"reels": r, "win": win, "bet": req.bet, "balance": 0} # Баланс оновиться через профіль

@app.post("/api/game/dice")
def dice(req: GameReq):
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    t = d1 + d2
    win = req.bet * (5 if req.action == "=7" else 2) if ((req.action == ">7" and t > 7) or (req.action == "<7" and t < 7) or (req.action == "=7" and t == 7)) else 0
    update_coins(req.user_id, win - req.bet)
    return {"d1": d1, "d2": d2, "win": win, "bet": req.bet}

@app.post("/api/game/mines")
def mines(req: GameReq):
    chosen = int(req.action)
    bombs = random.sample(range(25), 4)
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
            return {"status":"finished", "p_hand":p_hand, "d_hand":req.game_data['d_hand'], "p_score":score(p_hand), "d_score":"?", "result":f"Перебір! -{req.bet} HC", "win": 0}
        return {"status":"playing", "p_hand":p_hand, "d_hand":req.game_data['d_hand'], "p_score":score(p_hand), "d_score":"?", "deck":deck, "bet":req.bet}
    elif req.action == "stand":
        deck = req.game_data['deck']; d_hand = req.game_data['d_hand']
        if d_hand[1]['rank'] == "?": d_hand[1] = deck.pop()
        while score(d_hand) < 17: d_hand.append(deck.pop())
        ps, ds = score(req.game_data['p_hand']), score(d_hand)
        if ds > 21 or ps > ds: update_coins(req.user_id, req.bet); res = f"🎉 +{req.bet} HC"; win_amt = req.bet
        elif ps < ds: update_coins(req.user_id, -req.bet); res = f"📉 -{req.bet} HC"; win_amt = 0
        else: res = "🤝 Нічия"; win_amt = req.bet
        return {"status":"finished", "p_hand":req.game_data['p_hand'], "d_hand":d_hand, "p_score":ps, "d_score":ds, "result":res, "win": win_amt}

# ==========================================
# МУЛЬТИПЛЕЄР (ДУРЕНЬ)
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
        self.turn_uid = None; self.defender_uid = None

    def get_player_list(self):
        return [{"id": p.uid, "nick": p.nick, "avatar": p.avatar, "ready": p.is_ready, "cards_count": len(p.hand)} for p in self.players.values()]

    def get_next_player(self, current_uid):
        uids = list(self.players.keys())
        return uids[(uids.index(current_uid) + 1) % len(uids)]

    def start_game(self):
        self.deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS_36]
        random.shuffle(self.deck); self.trump = self.deck[-1]['suit']
        for p in self.players.values():
            p.hand = [self.deck.pop() for _ in range(6)]; p.is_ready = False
        self.table = []; self.status = "playing"
        self.turn_uid = list(self.players.keys())[0]
        self.defender_uid = self.get_next_player(self.turn_uid)

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
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT COALESCE(truckbook_nick, nickname, 'Driver') FROM users WHERE user_id=?", (uid,))
    row = c.fetchone(); conn.close()
    player = Player(uid, row[0] if row else f"Driver {uid}", get_avatar_url(uid), websocket)
    room.players[uid] = player
    await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})
    try:
        while True:
            data = await websocket.receive_text(); msg = json.loads(data)
            if msg['type'] == 'ready':
                player.is_ready = True
                if len(room.players) >= 2 and all(p.is_ready for p in room.players.values()):
                    room.start_game(); await broadcast_game_state(room)
                else: await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})
            elif msg['type'] == 'attack' and uid == room.turn_uid:
                card = player.hand.pop(msg['card_idx'])
                room.table.append({"attack": card, "defend": None})
                await broadcast_game_state(room)
            elif msg['type'] == 'defend' and uid == room.defender_uid and room.table and not room.table[-1]['defend']:
                if can_beat(room.table[-1]['attack'], player.hand[msg['card_idx']], room.trump):
                    room.table[-1]['defend'] = player.hand.pop(msg['card_idx'])
                    await broadcast_game_state(room)
            elif msg['type'] == 'take' and uid == room.defender_uid:
                for pair in room.table:
                    player.hand.append(pair['attack'])
                    if pair['defend']: player.hand.append(pair['defend'])
                room.table = []; 
                while len(player.hand) < 6 and room.deck: player.hand.append(room.deck.pop())
                room.turn_uid, room.defender_uid = room.defender_uid, room.get_next_player(room.defender_uid)
                await broadcast_game_state(room)
            elif msg['type'] == 'pass' and uid == room.turn_uid and all(p['defend'] for p in room.table):
                room.table = []
                for p in room.players.values():
                    while len(p.hand) < 6 and room.deck: p.hand.append(room.deck.pop())
                room.turn_uid, room.defender_uid = room.defender_uid, room.get_next_player(room.defender_uid)
                await broadcast_game_state(room)
    except WebSocketDisconnect:
        if uid in room.players: del room.players[uid]
        if len(room.players) == 0: del rooms[room_id]
        else: await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})

async def broadcast(room, msg):
    for p in room.players.values():
        try: await p.ws.send_json(msg)
        except: pass

async def broadcast_game_state(room):
    for p in room.players.values():
        await p.ws.send_json({"type": "game_update", "trump": room.trump, "table": room.table, "my_hand": p.hand, "players": room.get_player_list(), "turn_uid": room.turn_uid, "defender_uid": room.defender_uid})

# ==========================================
# БОТ
# ==========================================
intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready(): print(f"✅ Бот {bot.user} готовий!")
@bot.event
async def on_message(message):
    if message.author == bot.user: return
    if message.channel.id == TRUCKBOOK_CHANNEL_ID and message.embeds:
        for embed in message.embeds:
            nickname = embed.author.name.strip() if embed.author and embed.author.name else ""
            full_text = (embed.description or "") + "\n" + "\n".join([f.value for f in embed.fields if f.value])
            dist_match = re.search(r"Прийнята відстань:\s*([\d\s,\.]+)\s*km", full_text, re.IGNORECASE)
            if nickname and dist_match:
                distance = int(re.sub(r"[\s,\.]", "", dist_match.group(1)))
                coins = max(1, int((distance / 1000) * 15))
                conn = db_connect(); c = conn.cursor()
                c.execute("SELECT user_id FROM users WHERE LOWER(truckbook_nick) = LOWER(?)", (nickname,))
                if c.fetchone():
                    c.execute("UPDATE users SET distance = distance + ?, coins = coins + ?, weekly_earnings = weekly_earnings + ? WHERE LOWER(truckbook_nick) = LOWER(?)", (distance, coins, coins, nickname))
                    conn.commit()
                conn.close()
    await bot.process_commands(message)

if __name__ == "__main__":
    # Додаємо колонки в БД, якщо їх немає
    conn = db_connect(); c = conn.cursor()
    try:
        c.execute("ALTER TABLE users ADD COLUMN mini_coins INTEGER DEFAULT 0")
        c.execute("ALTER TABLE users ADD COLUMN games_played INTEGER DEFAULT 0")
        c.execute("ALTER TABLE users ADD COLUMN games_won INTEGER DEFAULT 0")
        conn.commit()
    except: pass
    conn.close()

    threading.Thread(target=lambda: bot.run(TOKEN) if TOKEN else None, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
