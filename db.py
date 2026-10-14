"""Viktorinalarni saqlash.

DATABASE_URL o'rnatilgan bo'lsa (Railway PostgreSQL) — PostgreSQL ishlatiladi,
aks holda loyiha papkasidagi kahoot.db (SQLite) fayli.
Har bir viktorina bitta qator: id, title, questions (JSON), updated_at.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if USE_PG:
    import psycopg
    from psycopg.rows import dict_row

SQLITE_PATH = Path(os.environ.get("SQLITE_PATH", Path(__file__).parent / "kahoot.db"))


def _conn():
    if USE_PG:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    c = sqlite3.connect(SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c


def _q(sql: str) -> str:
    """SQLite '?' belgilarini PostgreSQL '%s' ga almashtiradi."""
    return sql.replace("?", "%s") if USE_PG else sql


def _run(sql: str, params=(), fetch: str | None = None):
    with _conn() as c:
        cur = c.execute(_q(sql), params)
        if fetch == "one":
            r = cur.fetchone()
            return dict(r) if r else None
        if fetch == "all":
            return [dict(r) for r in cur.fetchall()]
        if fetch == "id":
            r = cur.fetchone() if USE_PG else None
            return r["id"] if USE_PG else cur.lastrowid
        if not USE_PG:
            c.commit()


def init():
    pk = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    _run(f"""CREATE TABLE IF NOT EXISTS quizzes (
        id {pk},
        title TEXT NOT NULL,
        questions TEXT NOT NULL DEFAULT '[]',
        updated_at BIGINT NOT NULL
    )""")
    # Birinchi ishga tushishda namunaviy SQL viktorinasini qo'shamiz
    if not _run("SELECT id FROM quizzes LIMIT 1", fetch="one"):
        from questions import QUESTIONS, TITLE
        create(TITLE, QUESTIONS)


def _row(r):
    if not r:
        return None
    qs = json.loads(r["questions"])
    return {"id": r["id"], "title": r["title"], "questions": qs,
            "count": len(qs), "updated_at": r["updated_at"]}


def list_all():
    rows = _run("SELECT * FROM quizzes ORDER BY updated_at DESC", fetch="all")
    out = []
    for r in rows:
        q = _row(r)
        topics = []
        for x in q["questions"]:
            t = (x.get("topic") or "").strip()
            if t and t not in topics:
                topics.append(t)
        out.append({"id": q["id"], "title": q["title"], "count": q["count"],
                    "topics": topics, "updated_at": q["updated_at"]})
    return out


def get(quiz_id: int):
    return _row(_run("SELECT * FROM quizzes WHERE id = ?", (quiz_id,), fetch="one"))


def create(title: str, questions: list) -> int:
    sql = "INSERT INTO quizzes (title, questions, updated_at) VALUES (?, ?, ?)"
    if USE_PG:
        sql += " RETURNING id"
    return _run(sql, (title, json.dumps(questions, ensure_ascii=False), int(time.time())), fetch="id")


def update(quiz_id: int, title: str, questions: list):
    _run("UPDATE quizzes SET title = ?, questions = ?, updated_at = ? WHERE id = ?",
         (title, json.dumps(questions, ensure_ascii=False), int(time.time()), quiz_id))


def delete(quiz_id: int):
    _run("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
