"""Persistent game state. seekdb, not the language model, owns game rules."""
import hashlib
import json
import os
import re
import secrets
from contextlib import contextmanager

import pymysql
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="记仇杂货铺", version="1.0.0")
DB_NAME = os.environ.get("SEEKDB_DATABASE", "dify_grudge_shop")
if not re.fullmatch(r"[A-Za-z0-9_]+", DB_NAME):
    raise RuntimeError("Invalid database name")
TOKEN = os.environ.get("SHOP_API_TOKEN", "")
if len(TOKEN) < 24:
    raise RuntimeError("Set SHOP_API_TOKEN to a random secret of at least 24 characters")


@contextmanager
def connect(database=True):
    conn = pymysql.connect(
        host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEEKDB_PORT", "2881")),
        user=os.environ.get("SEEKDB_USER", "root"),
        password=os.environ.get("SEEKDB_PASSWORD", ""),
        database=DB_NAME if database else None,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5, read_timeout=10, write_timeout=10,
    )
    try:
        yield conn
    finally:
        conn.close()


def initialize():
    with connect(False) as db, db.cursor() as c:
        c.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`")
    with connect() as db, db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS players (
            player_id VARCHAR(64) PRIMARY KEY,
            coins INT NOT NULL DEFAULT 40, trust INT NOT NULL DEFAULT 0,
            turn INT NOT NULL DEFAULT 0, inventory TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS events (
            player_id VARCHAR(64) NOT NULL, request_id VARCHAR(64) NOT NULL,
            fingerprint VARCHAR(64) NOT NULL, turn INT NOT NULL,
            action VARCHAR(16) NOT NULL, memory TEXT NOT NULL,
            response MEDIUMTEXT NOT NULL,
            PRIMARY KEY(player_id, request_id), INDEX by_turn(player_id, turn))""")
        db.commit()


def authorize(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Invalid shop token")


class Turn(BaseModel):
    player_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    action: str = Field(pattern=r"^(visit|prank|help|apologize|buy)$")
    message: str = Field(default="", max_length=500)


ITEMS = ["会打嗝的月亮", "只报喜的闹钟", "装着周五的罐头"]
LABELS = {"visit": "进店看看", "prank": "恶作剧", "help": "帮忙整理", "apologize": "认真道歉", "buy": "买件怪东西"}


def story(previous, action, state, outcome):
    recall = f"上回你{previous[-1]['memory']}，我可记着呢。" if previous else "新面孔！欢迎来到老葛的杂货铺，货不保真，账绝对真。"
    mood = "老葛把防恶作剧头盔戴紧了。" if state["trust"] < 0 else "老葛推过来一把会自己鼓掌的小椅子。"
    return f"{recall}\n\n{mood}{outcome}"


@app.get("/health")
def health():
    with connect() as db, db.cursor() as c:
        c.execute("SELECT VERSION() AS version")
        return {"ok": True, "database": "seekdb", "version": c.fetchone()["version"]}


@app.post("/turn", dependencies=[Depends(authorize)])
def turn(req: Turn):
    fingerprint = hashlib.sha256(json.dumps(req.model_dump(exclude={"request_id"}), sort_keys=True).encode()).hexdigest()
    with connect() as db, db.cursor() as c:
        try:
            c.execute("INSERT IGNORE INTO players(player_id,inventory) VALUES(%s,%s)", (req.player_id, "[]"))
            c.execute("SELECT * FROM players WHERE player_id=%s FOR UPDATE", (req.player_id,))
            state = c.fetchone()
            c.execute("SELECT fingerprint,response FROM events WHERE player_id=%s AND request_id=%s", (req.player_id, req.request_id))
            cached = c.fetchone()
            if cached:
                if cached["fingerprint"] != fingerprint:
                    raise HTTPException(409, "request_id already used for a different turn")
                db.commit()
                return json.loads(cached["response"])
            state["inventory"] = json.loads(state["inventory"])
            c.execute("SELECT turn,action,memory FROM events WHERE player_id=%s ORDER BY turn DESC LIMIT 4", (req.player_id,))
            previous = list(reversed(c.fetchall()))
            action = req.action
            old_trust = state["trust"]
            price = max(12, 20 - state["trust"] * 2)
            if action == "prank":
                state["trust"] -= 2
                state["coins"] += 3
                memory = "把价签换成了谜语，获得 3 枚恶作剧硬币，信任 -2"
            elif action == "help":
                state["trust"] += 1
                state["coins"] += 5
                memory = "帮忙把会飞的价签按回货架，金币 +5，信任 +1"
            elif action == "apologize" and state["coins"] >= 5:
                state["coins"] -= 5
                state["trust"] += 2
                memory = "花 5 金币请老葛喝了道歉汽水，信任 +2"
            elif action == "apologize":
                memory = "想请道歉汽水，但金币不足 5；本次没有扣款"
            elif action == "buy" and len(state["inventory"]) == len(ITEMS):
                memory = "已集齐三件怪东西，老葛授予你终身奇怪顾客称号"
            elif action == "buy" and state["coins"] >= price:
                item = ITEMS[len(state["inventory"])]
                state["coins"] -= price
                state["inventory"].append(item)
                memory = f"用 {price} 金币买到了「{item}」"
            elif action == "buy":
                memory = f"想买怪东西，但不足 {price} 金币；可以帮忙赚取金币"
            else:
                memory = "进店看看，金币和信任不变"
            state["trust"] = max(-5, min(5, state["trust"]))
            if action in ("prank", "help", "apologize"):
                memory += f"（信任实际变化 {state['trust'] - old_trust:+d}，上限 5、下限 -5）"
            state["turn"] += 1
            state["next_price"] = max(12, 20 - state["trust"] * 2)
            state["ending"] = "终身奇怪顾客" if len(state["inventory"]) == 3 else "收集三件怪东西，解锁结局"
            result = {"state": state, "action": LABELS[action], "outcome": memory,
                      "memories": previous, "player_message": req.message,
                      "fallback": story(previous, action, state, memory),
                      "storage": "seekdb", "request_id": req.request_id}
            c.execute("UPDATE players SET coins=%s,trust=%s,turn=%s,inventory=%s WHERE player_id=%s", (
                state["coins"], state["trust"], state["turn"], json.dumps(state["inventory"], ensure_ascii=False), req.player_id))
            c.execute("INSERT INTO events(player_id,request_id,fingerprint,turn,action,memory,response) VALUES(%s,%s,%s,%s,%s,%s,%s)", (
                req.player_id, req.request_id, fingerprint, state["turn"], action, memory, json.dumps(result, ensure_ascii=False)))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


@app.get("/players/{player_id}", dependencies=[Depends(authorize)])
def player(player_id: str):
    with connect() as db, db.cursor() as c:
        c.execute("SELECT * FROM players WHERE player_id=%s", (player_id,))
        row = c.fetchone()
        if row is None:
            raise HTTPException(404, "Player not found")
        row["inventory"] = json.loads(row["inventory"])
        return row


if __name__ == "__main__":
    initialize()
