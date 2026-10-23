import sqlite3
import time
import random
import os
import json
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List

app = FastAPI(title="HORT TRANS FULL API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_FILE = "economy.db"

def db_connect():
    conn = sqlite3.connect(DB_FILE, timeout=15.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def update_coins(uid, amount):
    conn = db_connect(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, 0)", (uid,))
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, uid))
    conn.commit(); conn.close()

def get_avatar_url(uid):
    avatar_id = (uid >> 22) % 6 if uid else 0
    return f"https://cdn.discordapp.com/embed/avatars/{avatar_id}.png"

# ==========================================
# МОДЕЛІ
# ==========================================
class LoginReq(BaseModel):
    nick: str
class TransferReq(BaseModel):
    sender_id: int
    receiver_id: int
    amount: int
class GameReq(BaseModel):
    user_id: int
    bet: int = 0
    action: Optional[str] = None
    game_data: Optional[dict] = None
class CreateRoomReq(BaseModel):
    host_uid: int
    bet: int

# ==========================================
# API ДЛЯ ПРОФІЛЮ
# ==========================================
@app.post("/api/login")
def login(req: LoginReq):
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT user_id, truckbook_nick FROM users WHERE LOWER(truckbook_nick) = LOWER(?)", (req.nick.strip(),))
    row = c.fetchone(); conn.close()
    if not row: raise HTTPException(404, "Нік не знайдено")
    return {"user_id": row[0], "nick": row[1]}

@app.get("/api/profile/{uid}")
def profile(uid: int):
    conn = db_connect(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, coins) VALUES (?, 0)", (uid,)); conn.commit()
    c.execute("SELECT distance, coins, weekly_earnings, truckbook_nick, nickname FROM users WHERE user_id = ?", (uid,))
    row = c.fetchone(); conn.close()
    return {"user_id": uid, "distance": row[0] or 0, "coins": row[1] or 0, "weekly": row[2] or 0, "nickname": row[3] or "—", "discord_name": row[4] or f"Driver {uid}", "avatar": get_avatar_url(uid)}

@app.get("/api/drivers")
def drivers_list():
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT user_id, COALESCE(truckbook_nick, nickname, 'Driver'), coins, distance FROM users WHERE coins > 0 OR distance > 0 OR truckbook_nick IS NOT NULL ORDER BY coins DESC")
    rows = c.fetchall(); conn.close()
    return [{"id": r[0], "name": r[1], "coins": r[2] or 0, "distance": r[3] or 0, "avatar": get_avatar_url(r[0])} for r in rows]

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

# ==========================================
# ВСІ ІГРИ (ДЛЯ БОТА)
# ==========================================
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
# WEBSOCKETS (МУЛЬТИПЛЕЄР)
# ==========================================
SUITS = ['♠', '♥', '♦', '♣']; RANKS_36 = ['6','7','8','9','10','J','Q','K','A']

class Player:
    def __init__(self, uid, nick, avatar, websocket):
        self.uid = uid; self.nick = nick; self.avatar = avatar
        self.ws = websocket; self.hand = []; self.is_ready = False

class Room:
    def __init__(self, room_id, bet, host_uid):
        self.room_id = room_id; self.bet = bet; self.host_uid = host_uid
        self.players: Dict[int, Player] = {}; self.deck = []
        self.trump = ""; self.table = []; self.status = "waiting"

    def get_player_list(self):
        return [{"id": p.uid, "nick": p.nick, "avatar": p.avatar, "ready": p.is_ready, "cards_count": len(p.hand)} for p in self.players.values()]

    def start_game(self):
        self.deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS_36]
        random.shuffle(self.deck); self.trump = self.deck[-1]['suit']
        for p in self.players.values():
            p.hand = [self.deck.pop() for _ in range(6)]; p.is_ready = False
        self.table = []; self.status = "playing"

rooms: Dict[str, Room] = {}

@app.post("/api/create_room")
def create_room(req: CreateRoomReq):
    room_id = str(random.randint(1000, 9999))
    rooms[room_id] = Room(room_id, req.bet, req.host_uid)
    return {"room_id": room_id}

@app.websocket("/ws/{room_id}/{uid}/{nick}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, uid: int, nick: str):
    await websocket.accept()
    if room_id not in rooms:
        await websocket.send_json({"type": "error", "msg": "Кімнату не знайдено"}); await websocket.close(); return
    room = rooms[room_id]
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
                    for uid_loop, p in room.players.items():
                        await p.ws.send_json({"type": "game_start", "trump": room.trump, "table": room.table, "my_hand": p.hand, "players": room.get_player_list()})
                else:
                    await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})
    except WebSocketDisconnect:
        if uid in room.players:
            del room.players[uid]
            await broadcast(room, {"type": "room_update", "players": room.get_player_list(), "host": room.host_uid})
            if len(room.players) == 0: del rooms[room_id]

async def broadcast(room: Room, message: dict):
    for p in room.players.values():
        try: await p.ws.send_json(message)
        except: pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
