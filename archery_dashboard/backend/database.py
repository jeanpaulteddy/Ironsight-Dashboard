# backend/database.py
import aiosqlite
import os
import json
import shutil
from typing import Optional, List, Dict, Any
from datetime import datetime
import config

async def get_db():
    """Get database connection"""
    db_path = os.path.join(os.path.dirname(__file__), config.DATABASE_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return await aiosqlite.connect(db_path)

async def init_db():
    """Initialize database with schema"""
    db = await get_db()
    try:
        # Enable foreign keys
        await db.execute("PRAGMA foreign_keys = ON")

        # Create sessions table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time REAL NOT NULL,
                end_time REAL,
                arrows_per_end INTEGER NOT NULL,
                num_ends INTEGER NOT NULL,
                total_score INTEGER DEFAULT 0,
                total_arrows INTEGER DEFAULT 0,
                is_complete BOOLEAN DEFAULT 0,
                notes TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)

        # Create shots table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                end_number INTEGER NOT NULL,
                shot_number INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                r REAL NOT NULL,
                score INTEGER NOT NULL,
                is_x BOOLEAN DEFAULT 0,
                screenshot_path TEXT,
                posture_score REAL,
                posture_messages TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)

        # Create indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON sessions(start_time DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_complete ON sessions(is_complete)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_shots_session_id ON shots(session_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_shots_session_end ON shots(session_id, end_number)")

        await db.commit()
        print("[DB] Database initialized successfully")
    finally:
        await db.close()

async def create_session(arrows_per_end: int, num_ends: int, notes: str = None) -> int:
    """Create a new session and return session_id"""
    import time
    db = await get_db()
    try:
        cursor = await db.execute("""
            INSERT INTO sessions (start_time, arrows_per_end, num_ends, notes)
            VALUES (?, ?, ?, ?)
        """, (time.time(), arrows_per_end, num_ends, notes))

        session_id = cursor.lastrowid
        await db.commit()
        print(f"[DB] Created session {session_id}: {arrows_per_end} arrows/end × {num_ends} ends")
        return session_id
    finally:
        await db.close()

async def save_shot(
    session_id: int,
    end_number: int,
    shot_number: int,
    timestamp: float,
    x: float,
    y: float,
    r: float,
    score: int,
    is_x: bool,
    screenshot_path: Optional[str] = None,
    posture_score: Optional[float] = None,
    posture_messages: Optional[List[str]] = None
) -> int:
    """Save a shot to the database and return shot_id"""
    db = await get_db()
    try:
        # Convert posture messages to JSON if provided
        posture_json = json.dumps(posture_messages) if posture_messages else None

        cursor = await db.execute("""
            INSERT INTO shots (
                session_id, end_number, shot_number, timestamp,
                x, y, r, score, is_x,
                screenshot_path, posture_score, posture_messages
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, end_number, shot_number, timestamp,
            x, y, r, score, is_x,
            screenshot_path, posture_score, posture_json
        ))

        shot_id = cursor.lastrowid
        await db.commit()
        return shot_id
    finally:
        await db.close()

async def complete_session(session_id: int, end_time: float, total_score: int, total_arrows: int):
    """Mark a session as complete"""
    db = await get_db()
    try:
        await db.execute("""
            UPDATE sessions
            SET end_time = ?, is_complete = 1, total_score = ?, total_arrows = ?
            WHERE id = ?
        """, (end_time, total_score, total_arrows, session_id))

        await db.commit()
        print(f"[DB] Session {session_id} marked as complete: {total_score} points, {total_arrows} arrows")
    finally:
        await db.close()

async def get_session(session_id: int) -> Optional[Dict[str, Any]]:
    """Get a session with all its shots"""
    db = await get_db()
    try:
        # Get session metadata
        async with db.execute("""
            SELECT id, start_time, end_time, arrows_per_end, num_ends,
                   total_score, total_arrows, is_complete, notes, created_at
            FROM sessions
            WHERE id = ?
        """, (session_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            session = {
                "id": row[0],
                "start_time": row[1],
                "end_time": row[2],
                "arrows_per_end": row[3],
                "num_ends": row[4],
                "total_score": row[5],
                "total_arrows": row[6],
                "is_complete": bool(row[7]),
                "notes": row[8],
                "created_at": row[9]
            }

        # Get all shots for this session
        shots = []
        async with db.execute("""
            SELECT id, end_number, shot_number, timestamp, x, y, r, score, is_x,
                   screenshot_path, posture_score, posture_messages
            FROM shots
            WHERE session_id = ?
            ORDER BY end_number, shot_number
        """, (session_id,)) as cursor:
            async for row in cursor:
                # Parse posture messages from JSON
                posture_messages = json.loads(row[11]) if row[11] else None

                shots.append({
                    "id": row[0],
                    "end_number": row[1],
                    "shot_number": row[2],
                    "timestamp": row[3],
                    "x": row[4],
                    "y": row[5],
                    "r": row[6],
                    "score": row[7],
                    "is_x": bool(row[8]),
                    "screenshot_path": row[9],
                    "posture_score": row[10],
                    "posture_messages": posture_messages
                })

        session["shots"] = shots
        return session
    finally:
        await db.close()

async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    start_date: Optional[float] = None,
    end_date: Optional[float] = None,
    complete_only: bool = False
) -> Dict[str, Any]:
    """List sessions with filtering and pagination"""
    db = await get_db()
    try:
        # Build WHERE clause
        where_clauses = []
        params = []

        if start_date:
            where_clauses.append("start_time >= ?")
            params.append(start_date)

        if end_date:
            where_clauses.append("start_time <= ?")
            params.append(end_date)

        if complete_only:
            where_clauses.append("is_complete = 1")

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Get total count
        count_query = f"SELECT COUNT(*) FROM sessions {where_sql}"
        async with db.execute(count_query, params) as cursor:
            row = await cursor.fetchone()
            total = row[0]

        # Get sessions
        sessions = []
        query = f"""
            SELECT id, start_time, end_time, arrows_per_end, num_ends,
                   total_score, total_arrows, is_complete, notes, created_at
            FROM sessions
            {where_sql}
            ORDER BY start_time DESC
            LIMIT ? OFFSET ?
        """
        params_with_limit = params + [limit, offset]

        async with db.execute(query, params_with_limit) as cursor:
            async for row in cursor:
                sessions.append({
                    "id": row[0],
                    "start_time": row[1],
                    "end_time": row[2],
                    "arrows_per_end": row[3],
                    "num_ends": row[4],
                    "total_score": row[5],
                    "total_arrows": row[6],
                    "is_complete": bool(row[7]),
                    "notes": row[8],
                    "created_at": row[9]
                })

        return {"sessions": sessions, "total": total}
    finally:
        await db.close()

async def delete_session(session_id: int):
    """Delete a session and its associated screenshots"""
    db = await get_db()
    try:
        # Get screenshot paths before deleting
        screenshots = []
        async with db.execute("""
            SELECT screenshot_path
            FROM shots
            WHERE session_id = ? AND screenshot_path IS NOT NULL
        """, (session_id,)) as cursor:
            async for row in cursor:
                screenshots.append(row[0])

        # Delete from database (CASCADE will delete shots)
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()

        # Delete screenshot files
        screenshots_dir = os.path.join(os.path.dirname(__file__), config.SCREENSHOTS_DIR)
        session_dir = os.path.join(screenshots_dir, f"session_{session_id}")

        if os.path.exists(session_dir):
            shutil.rmtree(session_dir)
            print(f"[DB] Deleted screenshot directory: {session_dir}")

        print(f"[DB] Deleted session {session_id} and {len(screenshots)} screenshots")
    finally:
        await db.close()

async def get_session_stats(session_id: int) -> Optional[Dict[str, Any]]:
    """Get statistics for a session"""
    session = await get_session(session_id)
    if not session:
        return None

    shots = session["shots"]
    if not shots:
        return {
            "session_id": session_id,
            "avg_score": 0,
            "x_count": 0,
            "score_distribution": {},
            "avg_r": 0,
            "ends": []
        }

    # Calculate statistics
    total_score = sum(s["score"] for s in shots)
    avg_score = total_score / len(shots) if shots else 0
    x_count = sum(1 for s in shots if s["is_x"])
    avg_r = sum(s["r"] for s in shots) / len(shots) if shots else 0

    # Score distribution
    score_distribution = {}
    for s in shots:
        score_key = "X" if s["is_x"] else str(s["score"])
        score_distribution[score_key] = score_distribution.get(score_key, 0) + 1

    # Per-end stats
    ends_stats = []
    ends_dict = {}
    for shot in shots:
        end_num = shot["end_number"]
        if end_num not in ends_dict:
            ends_dict[end_num] = []
        ends_dict[end_num].append(shot)

    for end_num in sorted(ends_dict.keys()):
        end_shots = ends_dict[end_num]
        end_score = sum(s["score"] for s in end_shots)
        ends_stats.append({
            "end": end_num,
            "arrows": len(end_shots),
            "score": end_score,
            "avg_score": end_score / len(end_shots) if end_shots else 0
        })

    return {
        "session_id": session_id,
        "avg_score": round(avg_score, 2),
        "x_count": x_count,
        "score_distribution": score_distribution,
        "avg_r": round(avg_r, 4),
        "ends": ends_stats
    }
