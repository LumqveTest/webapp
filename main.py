from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

clients = {}
admins = set()
chat_history = {}

@app.get("/")
async def get_user(request: Request):
    return templates.TemplateResponse("user.html", {"request": request})

@app.get("/admin")
async def get_admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

@app.websocket("/ws/user/{user_id}")
async def user_ws(websocket: WebSocket, user_id: str):
    await websocket.accept()
    clients[user_id] = websocket
    chat_history.setdefault(user_id, [])

    for msg in chat_history[user_id]:
        await websocket.send_text(msg)

    try:
        while True:
            data = await websocket.receive_text()

            if data == "DELETE":
                chat_history.pop(user_id, None)
                await websocket.send_text("❌ Чат был закрыт.")
                break

            message = f"🧑 {data}"
            chat_history[user_id].append(message)

            await websocket.send_text(message)

            for admin_id in admins:
                admin_ws = clients.get(admin_id)
                if admin_ws:
                    await admin_ws.send_text(f"{user_id}:{message}")
    except WebSocketDisconnect:
        clients.pop(user_id, None)

@app.websocket("/ws/admin/{admin_id}")
async def admin_ws(websocket: WebSocket, admin_id: str):
    await websocket.accept()
    clients[admin_id] = websocket
    admins.add(admin_id)

    for user_id, messages in chat_history.items():
        for msg in messages:
            await websocket.send_text(f"{user_id}:{msg}")

    try:
        while True:
            data = await websocket.receive_text()

            if data.startswith("DELETE:"):
                user_id = data.split(":", 1)[1]
                chat_history.pop(user_id, None)
                user_ws = clients.get(user_id)
                if user_ws:
                    await user_ws.send_text("❌ Ваш чат был закрыт оператором")
                continue

            if ":" in data:
                user_id, msg = data.split(":", 1)
                message = f"👩‍💼 {msg}"
                chat_history.setdefault(user_id, []).append(message)

                user_ws = clients.get(user_id)
                if user_ws:
                    await user_ws.send_text(message)

                await websocket.send_text(f"{user_id}:{message}")

    except WebSocketDisconnect:
        clients.pop(admin_id, None)
        admins.discard(admin_id)
