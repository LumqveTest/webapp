import sqlite3
import time
import urllib.parse
import hmac
import hashlib
import re
import requests
from collections import defaultdict

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

# === Конфигурация ===
BOT_TOKEN = "7876588623:AAE4NvelTssLzaXKR_mxXROIj0Ow-iIg9j0"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "1234"

app = FastAPI()
security = HTTPBasic()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# === БД SQLite ===
conn = sqlite3.connect("chat.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT    NOT NULL,
    sender   TEXT    CHECK(sender IN ('user','support')) NOT NULL,
    text     TEXT    NOT NULL,
    ts       DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# === Вспомогательные ===
user_ws: dict[str, WebSocket] = {}
admin_ws: list[WebSocket] = []
last_msg_time = defaultdict(lambda: 0)


def verify_telegram_init_data(init_data: str):
    data = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    hash_to_check = data.pop('hash', '')
    sorted_data = sorted(f"{k}={v}" for k, v in data.items())
    data_check_string = "\n".join(sorted_data)
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(),
                             hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hash, hash_to_check), data


def require_admin_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username,
                                              ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password,
                                              ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные учетные данные",
            headers={"WWW-Authenticate": "Basic"},
        )


# === Маршруты ===
@app.get("/", response_class=HTMLResponse)
async def user_page(request: Request):
    return templates.TemplateResponse("user.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request,
                     auth: HTTPBasicCredentials = Depends(require_admin_auth)):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.websocket("/ws")
async def ws_user(ws: WebSocket):
    await ws.accept()
    user_id = ws.query_params.get("user")
    if not user_id:
        await ws.send_text("Ошибка: user_id не передан")
        await ws.close()
        return
    user_ws[user_id] = ws

    rows = cur.execute(
        "SELECT text FROM messages WHERE user_id=? AND sender='support' ORDER BY id",
        (user_id, )).fetchall()
    for (text, ) in rows:
        await ws.send_text(f"{text}")

    try:
        while True:
            msg = await ws.receive_text()
            now = time.time()
            if now - last_msg_time[user_id] < 1:
                await ws.send_text("⏳ Пожалуйста, не флудите.")
                continue
            last_msg_time[user_id] = now

            cur.execute(
                "INSERT INTO messages(user_id, sender, text) VALUES (?,?,?)",
                (user_id, "user", msg))
            conn.commit()

            for adm in admin_ws:
                await adm.send_text(f"user:{user_id}:{msg}")

            cur.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id=? AND sender='user'",
                (user_id,))
            user_msg_count = cur.fetchone()[0]
        
            if user_msg_count == 1:
                # Это первое сообщение от пользователя — отправляем уведомление
                try:
                    # пример: уведомление в Telegram боту
                    import requests
                    chat_id = re.search(r"\((\d+)\)$", user_id)
                    if chat_id:
                        chat_id = int(chat_id.group(1))
                        requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            json={
                                "chat_id": -1002608973505,  # или другой ID
                                "text": f"🆕 Новый чат от пользователя:\n{user_id}"
                            }
                        )
                except Exception as e:
                    print("Ошибка при отправке уведомления о новом чате:", e)
    except WebSocketDisconnect:
        user_ws.pop(user_id, None)


@app.websocket("/adminws")
async def ws_admin(ws: WebSocket):
    await ws.accept()

    auth = ws.headers.get("authorization")
    if not auth or not auth.startswith("Basic "):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    import base64
    try:
        encoded = auth.split(" ")[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
        if not (secrets.compare_digest(username, ADMIN_USERNAME)
                and secrets.compare_digest(password, ADMIN_PASSWORD)):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    admin_ws.append(ws)
    rows = cur.execute(
        "SELECT user_id, sender, text FROM messages ORDER BY id").fetchall()
    for uid, sender, text in rows:
        await ws.send_text(f"{sender}:{uid}:{text}")

    try:
        while True:
            data = await ws.receive_text()

            if data.startswith("DELETE_CHAT:"):
                _, uid = data.split(":", 1)
                cur.execute("DELETE FROM messages WHERE user_id=?", (uid, ))
                conn.commit()

                if uid in user_ws:
                    await user_ws[uid].send_text(
                        "Ваш чат закрыт, чтобы открыть новый, обновите страницу"
                    )
                    await user_ws[uid].close()
                    user_ws.pop(uid, None)
                    try:
                        m = re.search(r'\((\d+)\)$', uid)
                        chat_id = m.group(1) if m else uid
                        requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            json={
                                "chat_id":
                                int(chat_id),
                                "text":
                                "Саппорт закрыл ваш чат.\nПропишите /start чтобы открыть новый."
                            })
                    except Exception as e:
                        print("Telegram notify error:", e)

                for adm in admin_ws:
                    await ws.send_text(f"CLEAR_CHAT:{uid}")
                continue

            parts = data.split(":", 2)
            if len(parts) != 3 or parts[0] != "support":
                continue
            _, uid, text = parts

            cur.execute(
                "INSERT INTO messages(user_id, sender, text) VALUES (?,?,?)",
                (uid, "support", text))
            conn.commit()

            if uid in user_ws:
                await user_ws[uid].send_text(f"{text}")
            await ws.send_text(f"support:{uid}:{text}")

            try:
                m = re.search(r'\((\d+)\)$', uid)
                chat_id = m.group(1) if m else uid
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id":
                        int(chat_id),
                        "text":
                        "Саппорт ответил вам в чате.\nПропишите /start чтобы посмотреть ответ."
                    })
            except Exception as e:
                print("Telegram notify error:", e)

    except WebSocketDisconnect:
        admin_ws.remove(ws)
