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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# ==========================================
# ⚙️ НАЛАШТУВАННЯ
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN") 
TRUCKBOOK_CHANNEL_ID = 1556226431854583852
DB_FILE = "economy.db"

# ==========================================
# БАЗА ДАНИХ
# ==========================================
def db_connect():
    conn = sqlite3.connect(DB_FILE, timeout=15.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = db_connect(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        truckbook_nick TEXT UNIQUE,
        coins INTEGER DEFAULT 1000,
        mini_coins INTEGER DEFAULT 0,
        games INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        earned INTEGER DEFAULT 0,
        lost INTEGER DEFAULT 0,
        best INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0,
        cpow INTEGER DEFAULT 1,
        cauto INTEGER DEFAULT 0
    )""")
    conn.commit(); conn.close()

# ==========================================
# FASTAPI (САЙТ ТА API)
# ==========================================
app = FastAPI(title="HortCoin API")

# ДОЗВОЛЯЄМО ЗАПИТИ З БУДЬ-ЯКОГО ДОМЕНУ (включаючи GitHub Pages)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False, # Важливо для безпеки та сумісності
    allow_methods=["*"],
    allow_headers=["*"],
)

class LoginReq(BaseModel):
    nick: str

class SyncReq(BaseModel):
    user_id: int
    coins: int
    mini_coins: int
    games: int
    wins: int
    earned: int
    lost: int
    best: int
    clicks: int
    cpow: int
    cauto: int

@app.post("/api/login")
def login(req: LoginReq):
    nick = req.nick.strip()
    if len(nick) < 2:
        raise HTTPException(400, "Мінімум 2 символи")
    
    conn = db_connect(); c = conn.cursor()
    c.execute("SELECT user_id, truckbook_nick, coins, mini_coins, games, wins, earned, lost, best, clicks, cpow, cauto FROM users WHERE LOWER(truckbook_nick) = LOWER(?)", (nick,))
    row = c.fetchone()
    
    if not row:
        c.execute("INSERT INTO users (truckbook_nick) VALUES (?)", (nick,))
        conn.commit()
        uid = c.lastrowid
        conn.close()
        return {"status": "success", "user_id": uid, "nick": nick, "coins": 1000, "mini_coins": 0, "games": 0, "wins": 0, "earned": 0, "lost": 0, "best": 0, "clicks": 0, "cpow": 1, "cauto": 0}
    else:
        conn.close()
        return {
            "status": "success", "user_id": row[0], "nick": row[1], "coins": row[2], 
            "mini_coins": row[3], "games": row[4], "wins": row[5], "earned": row[6], 
            "lost": row[7], "best": row[8], "clicks": row[9], "cpow": row[10], "cauto": row[11]
        }

@app.post("/api/sync")
def sync(req: SyncReq):
    conn = db_connect(); c = conn.cursor()
    c.execute("""UPDATE users SET 
                 coins=?, mini_coins=?, games=?, wins=?, earned=?, lost=?, best=?, clicks=?, cpow=?, cauto=? 
                 WHERE user_id=?""",
              (req.coins, req.mini_coins, req.games, req.wins, req.earned, req.lost, req.best, req.clicks, req.cpow, req.cauto, req.user_id))
    conn.commit(); conn.close()
    return {"status": "success"}

# ==========================================
# DISCORD БОТ
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Бот {bot.user} запущений і синхронізований з базою!")

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
                    c.execute("UPDATE users SET coins = coins + ? WHERE LOWER(truckbook_nick) = LOWER(?)", (coins, nickname))
                    conn.commit()
                conn.close()
    await bot.process_commands(message)

def run_bot():
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("⚠️ Токен Discord не знайдено! Додайте DISCORD_TOKEN у Environment Variables.")

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
