"""
DoomStop backend API

This module implements a simple backend service for the DoomStop app using
FastAPI. The goal of this backend is to provide a lightweight API that
supports core functionality such as retrieving game loops, tracking user
progress and streaks, and generating a basic leaderboard. While it is
designed to be self‑contained and easy to run locally, it can be extended
with a database or authentication mechanism for production use.

Endpoints exposed:

* GET `/loops` – Return the available loops (trivia questions, memes,
  quick‑win prompts) that the client can present to the user.
* POST `/users/{user_id}/loop` – Record the completion of a loop for a
  user. The request body should include the loop ID and whether the
  attempt was successful. The endpoint updates total escapes and
  streak counts.
* GET `/users/{user_id}` – Retrieve statistics for a given user (total
  escapes, today’s escapes, streak count, last escape time).
* GET `/leaderboard` – Return a simple leaderboard sorted by total
  escapes.

This backend stores data in memory for demonstration purposes. In a
production system, you’d likely back these structures with a database
like SQLite or PostgreSQL. For concurrency safety, you may need a
threading lock or async primitives, but the in‑memory approach is
acceptable for this example.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field
import sqlite3
import json


app = FastAPI(title="DoomStop Backend", version="0.2.0")


class TriviaQuestion(BaseModel):
    id: int
    question: str
    options: List[str]
    answer: str


class Loop(BaseModel):
    id: int
    type: str
    content: Dict


class UserStats(BaseModel):
    user_id: str
    join_date: datetime
    total_escapes: int = 0
    today_escapes: int = 0
    last_escape: Optional[datetime] = None
    streak: int = 0


# Sample content for loops. In a real implementation these could be
# pulled from a database or external service and refreshed regularly.
trivia_questions: List[TriviaQuestion] = [
    TriviaQuestion(id=1, question="What is the capital of France?", options=["Paris", "Berlin", "London"], answer="Paris"),
    TriviaQuestion(id=2, question="How many continents are there?", options=["5", "6", "7"], answer="7"),
    TriviaQuestion(id=3, question="What planet is known as the Red Planet?", options=["Mars", "Venus", "Saturn"], answer="Mars"),
    TriviaQuestion(id=4, question="Which ocean is the largest?", options=["Atlantic", "Pacific", "Indian"], answer="Pacific"),
    TriviaQuestion(id=5, question="What gas do plants absorb from the atmosphere?", options=["Oxygen", "Carbon dioxide", "Nitrogen"], answer="Carbon dioxide"),
]

memes: List[str] = [
    "Keep calm and carry on! 😄",
    "Here’s a puppy to brighten your day 🐶",
    "Remember: you are awesome! 💪",
    "Take a deep breath and smile 😊",
    "Life is better when you’re laughing 😂",
]

quick_wins: List[str] = [
    "You drank a glass of water – hydration win! 💧",
    "You stood up and stretched – good for you! 🧘‍♂️",
    "You read a page of a book – knowledge gained 📚",
    "You wrote down one thing you’re grateful for – gratitude boost 🙏",
    "You smiled at a stranger – positivity shared 😊",
]


def get_loop_objects() -> List[Loop]:
    """Construct unified loop objects from the sample content."""
    loops: List[Loop] = []
    for q in trivia_questions:
        loops.append(Loop(id=1000 + q.id, type="trivia", content=q.dict()))
    for idx, m in enumerate(memes, start=1):
        loops.append(Loop(id=2000 + idx, type="meme", content={"text": m}))
    for idx, w in enumerate(quick_wins, start=1):
        loops.append(Loop(id=3000 + idx, type="quick_win", content={"text": w}))
    return loops


# Database setup: we use a lightweight SQLite database for persistence.
# The database file is created in the working directory when the server
# starts. If you deploy the backend to a cloud provider, ensure that
# the underlying storage is durable (e.g. use a mounted volume).
DB_PATH = "doomstop.db"


def get_db_connection():
    """Return a connection to the SQLite database. SQLite3 connections
    are not thread‑safe by default if check_same_thread is True; we
    disable that check because FastAPI may use threads. Each request
    should obtain its own connection.
    """
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    """Initialise the SQLite database with required tables and default data."""
    conn = get_db_connection()
    cur = conn.cursor()
    # Users table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            join_date TEXT,
            total_escapes INTEGER,
            today_escapes INTEGER,
            last_escape TEXT,
            streak INTEGER
        )
        """
    )
    # Loops table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS loops (
            loop_id INTEGER PRIMARY KEY,
            type TEXT,
            content TEXT
        )
        """
    )
    # User loops table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_loops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            loop_id INTEGER,
            success INTEGER,
            timestamp TEXT
        )
        """
    )
    conn.commit()
    # Populate loops table if empty
    cur.execute("SELECT COUNT(*) FROM loops")
    count = cur.fetchone()[0]
    if count == 0:
        for loop in get_loop_objects():
            cur.execute(
                "INSERT INTO loops (loop_id, type, content) VALUES (?, ?, ?)",
                (loop.id, loop.type, json.dumps(loop.content))
            )
        conn.commit()
    conn.close()


# Simple API key for authentication. In a real application, integrate a
# proper authentication system (OAuth2, JWT, etc.). Clients must
# provide this key in the `Authorization` header as `Bearer <API_KEY>` to
# access protected endpoints.
API_KEY = "doomstop-secret-token"


def verify_api_key(authorization: Optional[str] = Header(None)):
    """Dependency to verify the Authorization header for protected endpoints."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ")[1]
    if token != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


# Initialise the database on startup
@app.on_event("startup")
def startup_event():
    init_db()



class LoopCompletionRequest(BaseModel):
    loop_id: int = Field(..., description="The ID of the loop that was completed")
    success: bool = Field(..., description="Whether the user successfully completed the loop")


@app.get("/loops", response_model=List[Loop])
async def get_loops() -> List[Loop]:
    """Return all available loops for clients to pick from.

    This endpoint does not require authentication. It reads loops from
    the database so that administrators can update content without
    redeploying the API. If the database is unavailable, it falls back
    to the statically defined loops in memory.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT loop_id, type, content FROM loops")
        rows = cur.fetchall()
        conn.close()
        loops: List[Loop] = []
        for loop_id, ltype, content_json in rows:
            try:
                content = json.loads(content_json)
            except Exception:
                content = {}
            loops.append(Loop(id=loop_id, type=ltype, content=content))
        # If loops exist in DB return them; otherwise fall back to in-memory
        return loops if loops else get_loop_objects()
    except Exception:
        # In case of any DB error, fall back to static definitions
        return get_loop_objects()


@app.get("/users/{user_id}", response_model=UserStats)
async def get_user_stats(user_id: str, auth: None = Depends(verify_api_key)) -> UserStats:
    """Retrieve stats for a given user, creating the user if necessary.

    This endpoint requires a valid API key in the Authorization header.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    # Fetch user
    cur.execute("SELECT user_id, join_date, total_escapes, today_escapes, last_escape, streak FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        # Create new user record
        now = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO users (user_id, join_date, total_escapes, today_escapes, last_escape, streak) VALUES (?, ?, 0, 0, NULL, 0)",
            (user_id, now)
        )
        conn.commit()
        user = UserStats(user_id=user_id, join_date=datetime.fromisoformat(now), total_escapes=0, today_escapes=0, last_escape=None, streak=0)
    else:
        user = UserStats(
            user_id=row[0],
            join_date=datetime.fromisoformat(row[1]),
            total_escapes=row[2],
            today_escapes=row[3],
            last_escape=datetime.fromisoformat(row[4]) if row[4] else None,
            streak=row[5]
        )
    conn.close()
    return user


@app.post("/users/{user_id}/loop")
async def complete_loop(user_id: str, request: LoopCompletionRequest, auth: None = Depends(verify_api_key)):
    """Record a user’s completion of a loop and update their stats.

    Requires a valid API key. Updates the user record and stores a row
    in the user_loops table for audit purposes.
    """
    now = datetime.utcnow()
    conn = get_db_connection()
    cur = conn.cursor()
    # Ensure user exists
    cur.execute("SELECT user_id, join_date, total_escapes, today_escapes, last_escape, streak FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        # Create new user record
        join_date = now.isoformat()
        cur.execute(
            "INSERT INTO users (user_id, join_date, total_escapes, today_escapes, last_escape, streak) VALUES (?, ?, 0, 0, NULL, 0)",
            (user_id, join_date)
        )
        total_escapes = 0
        today_escapes = 0
        last_escape = None
        streak = 0
    else:
        total_escapes = row[2]
        today_escapes = row[3]
        last_escape = datetime.fromisoformat(row[4]) if row[4] else None
        streak = row[5]

    # Update escape counts
    total_escapes += 1
    if last_escape is None or last_escape.date() != now.date():
        today_escapes = 1
    else:
        today_escapes += 1

    # Update streak only if success flag is true
    if request.success:
        if last_escape is None:
            streak = 1
        else:
            if last_escape.date() == (now.date() - timedelta(days=1)):
                streak += 1
            elif last_escape.date() == now.date():
                # streak stays the same for additional successes on same day
                streak = streak
            else:
                streak = 1
    # Update user record
    cur.execute(
        "UPDATE users SET total_escapes = ?, today_escapes = ?, last_escape = ?, streak = ? WHERE user_id = ?",
        (total_escapes, today_escapes, now.isoformat(), streak, user_id)
    )
    # Insert into user_loops table
    cur.execute(
        "INSERT INTO user_loops (user_id, loop_id, success, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, request.loop_id, 1 if request.success else 0, now.isoformat())
    )
    conn.commit()
    conn.close()
    # Return updated stats
    return {
        "message": "Loop completion recorded",
        "user": {
            "user_id": user_id,
            "join_date": row[1] if row else now.isoformat(),
            "total_escapes": total_escapes,
            "today_escapes": today_escapes,
            "last_escape": now.isoformat(),
            "streak": streak,
        },
    }


@app.get("/leaderboard")
async def get_leaderboard(limit: int = 10, auth: None = Depends(verify_api_key)):
    """Return a simple leaderboard sorted by total escapes.

    Requires a valid API key. You can adjust the `limit` query parameter
    to control how many users are returned.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, total_escapes, today_escapes, last_escape, streak, join_date FROM users ORDER BY total_escapes DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    leaderboard = []
    for row in rows:
        leaderboard.append({
            "user_id": row[0],
            "total_escapes": row[1],
            "today_escapes": row[2],
            "last_escape": row[3],
            "streak": row[4],
            "join_date": row[5],
        })
    return leaderboard
