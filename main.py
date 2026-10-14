"""Bilimdon — FastAPI + WebSocket multiplayer quiz.

Ishga tushirish:  uvicorn main:app --host 0.0.0.0 --port 8000
O'qituvchi paneli: http://localhost:8000/host
O'yinchilar:       http://localhost:8000/

Muhit o'zgaruvchilari (ixtiyoriy):
  ADMIN_PASSWORD — o'qituvchi paneliga parol (qo'yilmasa panel ochiq)
  DATABASE_URL   — PostgreSQL (Railway); qo'yilmasa SQLite fayli ishlatiladi
"""
import asyncio
import hashlib
import io
import os
import random
import secrets
import time
from pathlib import Path

import segno
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db

BASE = Path(__file__).parent
STATIC = BASE / "static"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ALLOWED_TIMES = (5, 10, 20, 30, 45, 60, 90, 120)

app = FastAPI(title="Bilimdon")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.init()

GAMES: dict[str, "Game"] = {}


# ======================= Avtorizatsiya =======================
def _token() -> str:
    return hashlib.sha256(f"kahoot::{ADMIN_PASSWORD}".encode()).hexdigest()


def require_admin(x_admin_token: str = Header(default="")):
    if ADMIN_PASSWORD and not secrets.compare_digest(x_admin_token, _token()):
        raise HTTPException(401, "Parol noto‘g‘ri yoki kiritilmagan")


class LoginIn(BaseModel):
    password: str


@app.get("/api/auth")
async def auth_status(x_admin_token: str = Header(default="")):
    need = bool(ADMIN_PASSWORD)
    return {"required": need, "ok": (not need) or secrets.compare_digest(x_admin_token, _token())}


@app.post("/api/login")
async def login(body: LoginIn):
    if not ADMIN_PASSWORD:
        return {"token": ""}
    if not secrets.compare_digest(body.password, ADMIN_PASSWORD):
        raise HTTPException(401, "Parol noto‘g‘ri")
    return {"token": _token()}


# ======================= Viktorinalar (CRUD) =======================
class QuestionIn(BaseModel):
    topic: str = ""
    q: str
    code: str = ""
    answers: list[str]
    correct: int
    time: int = 20
    code_answers: bool = False
    explain: str = ""


class QuizIn(BaseModel):
    title: str
    questions: list[QuestionIn] = []


def clean_quiz(body: QuizIn) -> tuple[str, list[dict]]:
    title = body.title.strip()[:120]
    if not title:
        raise HTTPException(400, "Viktorina nomini kiriting")
    out = []
    for n, q in enumerate(body.questions, 1):
        text = q.q.strip()
        answers = [a.strip()[:120] for a in q.answers if a.strip()]
        if not text:
            raise HTTPException(400, f"{n}-savol matni bo‘sh")
        if not 2 <= len(answers) <= 4:
            raise HTTPException(400, f"{n}-savolda 2 tadan 4 tagacha javob bo‘lishi kerak")
        # bo'sh javoblar olib tashlanganda to'g'ri javob indeksini qayta hisoblaymiz
        raw = [a.strip() for a in q.answers]
        if not (0 <= q.correct < len(raw)) or not raw[q.correct]:
            raise HTTPException(400, f"{n}-savolda to‘g‘ri javob belgilanmagan")
        correct = sum(1 for a in raw[:q.correct] if a)
        item = {"topic": q.topic.strip()[:60], "q": text[:300], "answers": answers,
                "correct": correct, "time": q.time if q.time in ALLOWED_TIMES else 20}
        if q.code.strip():
            item["code"] = q.code.rstrip()[:1500]
        if q.code_answers:
            item["code_answers"] = True
        if q.explain.strip():
            item["explain"] = q.explain.strip()[:300]
        out.append(item)
    return title, out


@app.get("/api/quizzes", dependencies=[Depends(require_admin)])
async def quizzes_list():
    return db.list_all()


@app.get("/api/quizzes/{quiz_id}", dependencies=[Depends(require_admin)])
async def quiz_get(quiz_id: int):
    q = db.get(quiz_id)
    if not q:
        raise HTTPException(404, "Viktorina topilmadi")
    return q


@app.post("/api/quizzes", dependencies=[Depends(require_admin)])
async def quiz_create(body: QuizIn):
    title, qs = clean_quiz(body)
    return {"id": db.create(title, qs)}


@app.put("/api/quizzes/{quiz_id}", dependencies=[Depends(require_admin)])
async def quiz_update(quiz_id: int, body: QuizIn):
    if not db.get(quiz_id):
        raise HTTPException(404, "Viktorina topilmadi")
    title, qs = clean_quiz(body)
    db.update(quiz_id, title, qs)
    return {"ok": True}


@app.post("/api/quizzes/{quiz_id}/copy", dependencies=[Depends(require_admin)])
async def quiz_copy(quiz_id: int):
    q = db.get(quiz_id)
    if not q:
        raise HTTPException(404, "Viktorina topilmadi")
    return {"id": db.create(q["title"] + " (nusxa)", q["questions"])}


@app.delete("/api/quizzes/{quiz_id}", dependencies=[Depends(require_admin)])
async def quiz_delete(quiz_id: int):
    db.delete(quiz_id)
    return {"ok": True}


# ======================= QR kod =======================
@app.get("/api/qr")
async def qr(data: str):
    if len(data) > 300:
        raise HTTPException(400, "Juda uzun")
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=10, border=2, dark="#000", light="#fff")
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


# ======================= O'yin =======================
class Player:
    def __init__(self, name: str, ws: WebSocket):
        self.name = name
        self.ws = ws
        self.score = 0
        self.streak = 0
        self.answer: int | None = None
        self.last_points = 0
        self.connected = True


class Game:
    def __init__(self, pin: str, title: str, questions: list[dict]):
        self.pin = pin
        self.key = secrets.token_urlsafe(16)  # faqat o'qituvchi biladi
        self.title = title
        self.questions = questions
        self.host: WebSocket | None = None
        self.players: dict[str, Player] = {}
        self.state = "lobby"  # lobby | question | result | leaderboard | finished
        self.qi = -1
        self.q_started = 0.0
        self.timer_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.created = time.time()

    @property
    def total(self):
        return len(self.questions)

    async def send_host(self, data: dict):
        if self.host:
            try:
                await self.host.send_json(data)
            except Exception:
                pass

    async def send_player(self, p: Player, data: dict):
        if p.connected:
            try:
                await p.ws.send_json(data)
            except Exception:
                p.connected = False

    async def broadcast_players(self, data: dict):
        await asyncio.gather(*(self.send_player(p, data) for p in list(self.players.values())))

    def ranking(self):
        return sorted(self.players.values(), key=lambda p: p.score, reverse=True)

    def leaderboard(self, n=5):
        return [{"name": p.name, "score": p.score, "streak": p.streak} for p in self.ranking()[:n]]

    async def lobby_update(self):
        await self.send_host({"type": "lobby", "players": [p.name for p in self.players.values() if p.connected]})

    async def next_question(self):
        self.qi += 1
        if self.qi >= self.total:
            await self.finish()
            return
        q = self.questions[self.qi]
        self.state = "question"
        for p in self.players.values():
            p.answer = None
            p.last_points = 0
        meta = {"index": self.qi + 1, "total": self.total, "time": q["time"]}
        await self.send_host({"type": "get_ready", **meta, "topic": q.get("topic", ""), "q": q["q"]})
        await self.broadcast_players({"type": "get_ready", **meta})
        await asyncio.sleep(3)
        self.q_started = time.monotonic()
        payload = {"type": "question", **meta, "q": q["q"], "code": q.get("code"),
                   "answers": q["answers"], "code_answers": q.get("code_answers", False)}
        await self.send_host({**payload, "topic": q.get("topic", "")})
        await self.broadcast_players(payload)
        self.timer_task = asyncio.create_task(self._timer(q["time"], self.qi))

    async def _timer(self, seconds: int, qi: int):
        await asyncio.sleep(seconds)
        if self.state == "question" and self.qi == qi:
            await self.show_result()

    async def answer(self, p: Player, choice: int):
        async with self.lock:
            if self.state != "question" or p.answer is not None:
                return
            q = self.questions[self.qi]
            if not (0 <= choice < len(q["answers"])):
                return
            elapsed = time.monotonic() - self.q_started
            p.answer = choice
            if choice == q["correct"]:
                # Ball formulasi: tez javob = ko'proq ball (500..1000) + ketma-ketlik bonusi
                ratio = min(elapsed / q["time"], 1)
                p.streak += 1
                p.last_points = round(1000 * (1 - ratio / 2)) + min((p.streak - 1) * 100, 500)
                p.score += p.last_points
            else:
                p.streak = 0
                p.last_points = 0
            await self.send_player(p, {"type": "answered"})
            active = [x for x in self.players.values() if x.connected]
            answered = sum(1 for x in active if x.answer is not None)
            await self.send_host({"type": "answer_count", "count": answered, "total": len(active)})
            all_done = answered >= len(active)
        if all_done:
            if self.timer_task:
                self.timer_task.cancel()
            await self.show_result()

    async def show_result(self):
        async with self.lock:
            if self.state != "question":
                return
            self.state = "result"
        q = self.questions[self.qi]
        counts = [0] * len(q["answers"])
        for p in self.players.values():
            if p.answer is not None:
                counts[p.answer] += 1
        await self.send_host({"type": "result", "correct": q["correct"], "counts": counts,
                              "answers": q["answers"], "explain": q.get("explain"),
                              "last": self.qi == self.total - 1})
        ranking = self.ranking()
        for i, p in enumerate(ranking):
            ahead = ranking[i - 1] if i > 0 else None
            await self.send_player(p, {
                "type": "result",
                "correct": p.answer == q["correct"],
                "no_answer": p.answer is None,
                "points": p.last_points, "score": p.score, "streak": p.streak,
                "rank": i + 1,
                "behind": f"{ahead.name} dan {ahead.score - p.score} ball orqada" if ahead else None,
            })

    async def show_leaderboard(self):
        self.state = "leaderboard"
        await self.send_host({"type": "leaderboard", "top": self.leaderboard(5),
                              "last": self.qi == self.total - 1})

    async def finish(self):
        self.state = "finished"
        ranking = self.ranking()
        await self.send_host({"type": "podium", "top": self.leaderboard(10)})
        for i, p in enumerate(ranking):
            await self.send_player(p, {"type": "finished", "rank": i + 1, "score": p.score,
                                       "total_players": len(ranking)})


def cleanup_games():
    """12 soatdan eski o'yinlarni xotiradan o'chiramiz."""
    now = time.time()
    for pin in [p for p, g in GAMES.items() if now - g.created > 12 * 3600]:
        GAMES.pop(pin, None)


# ======================= Sahifalar =======================
@app.get("/")
async def player_page():
    return FileResponse(STATIC / "play.html")


@app.get("/host")
async def host_page():
    return FileResponse(STATIC / "host.html")


@app.get("/host/edit")
async def editor_page():
    return FileResponse(STATIC / "editor.html")


@app.get("/host/game")
async def game_page():
    return FileResponse(STATIC / "game.html")


# ======================= O'yin API =======================
class GameIn(BaseModel):
    quiz_id: int


@app.post("/api/games", dependencies=[Depends(require_admin)])
async def create_game(body: GameIn):
    cleanup_games()
    quiz = db.get(body.quiz_id)
    if not quiz:
        raise HTTPException(404, "Viktorina topilmadi")
    if not quiz["questions"]:
        raise HTTPException(400, "Bu viktorinada savol yo‘q")
    while True:
        pin = str(random.randint(100000, 999999))
        if pin not in GAMES:
            break
    g = Game(pin, quiz["title"], quiz["questions"])
    GAMES[pin] = g
    return {"pin": pin, "key": g.key, "title": g.title, "count": g.total}


@app.get("/api/check/{pin}")
async def check(pin: str):
    g = GAMES.get(pin)
    if not g:
        return JSONResponse({"ok": False, "error": "Bunday PIN topilmadi"}, 404)
    if g.state != "lobby":
        return JSONResponse({"ok": False, "error": "O‘yin allaqachon boshlangan"}, 409)
    return {"ok": True, "title": g.title}


@app.websocket("/ws/host/{pin}")
async def ws_host(ws: WebSocket, pin: str, key: str = ""):
    await ws.accept()
    g = GAMES.get(pin)
    if not g or not secrets.compare_digest(key, g.key):
        await ws.send_json({"type": "error", "msg": "O‘yin topilmadi yoki ruxsat yo‘q"})
        await ws.close()
        return
    g.host = ws
    await ws.send_json({"type": "hello", "pin": pin, "title": g.title, "count": g.total, "state": g.state})
    await g.lobby_update()
    try:
        while True:
            msg = await ws.receive_json()
            act = msg.get("action")
            if act == "kick" and g.state == "lobby":
                p = g.players.pop(msg.get("name", ""), None)
                if p:
                    await g.send_player(p, {"type": "kicked"})
                    try:
                        await p.ws.close()
                    except Exception:
                        pass
                await g.lobby_update()
            elif act == "start" and g.state == "lobby" and g.players:
                g.state = "starting"
                asyncio.create_task(g.next_question())
            elif act == "skip" and g.state == "question":
                if g.timer_task:
                    g.timer_task.cancel()
                await g.show_result()
            elif act == "leaderboard" and g.state == "result":
                await g.show_leaderboard()
            elif act == "next" and g.state in ("result", "leaderboard"):
                g.state = "starting"
                asyncio.create_task(g.next_question())
            elif act == "end":
                await g.finish()
    except WebSocketDisconnect:
        if g.host is ws:
            g.host = None


@app.websocket("/ws/play/{pin}")
async def ws_play(ws: WebSocket, pin: str, name: str = ""):
    await ws.accept()
    g = GAMES.get(pin)
    name = " ".join(name.split())[:20]
    if not g:
        await ws.send_json({"type": "error", "msg": "Bunday PIN topilmadi"})
        await ws.close()
        return
    if not name:
        await ws.send_json({"type": "error", "msg": "Ism kiriting"})
        await ws.close()
        return

    existing = g.players.get(name)
    if existing and existing.connected:
        await ws.send_json({"type": "error", "msg": "Bu ism band, boshqasini tanlang"})
        await ws.close()
        return
    if existing:  # qayta ulanish (telefon ekrani o'chib qolgan bo'lsa)
        player = existing
        player.ws = ws
        player.connected = True
    elif g.state != "lobby":
        await ws.send_json({"type": "error", "msg": "O‘yin allaqachon boshlangan"})
        await ws.close()
        return
    else:
        player = Player(name, ws)
        g.players[name] = player

    await ws.send_json({"type": "joined", "name": name, "state": g.state,
                        "score": player.score, "title": g.title})
    await g.lobby_update()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("action") == "answer":
                try:
                    await g.answer(player, int(msg.get("choice", -1)))
                except (TypeError, ValueError):
                    pass
    except Exception:
        if player.ws is ws:
            player.connected = False
            if g.state == "lobby":
                g.players.pop(name, None)
            await g.lobby_update()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
