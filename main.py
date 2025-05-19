from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

clients = {}
chat_history = {}

@app.get("/")
async def get_user(request: Request):
    return templates.TemplateResponse("user.html", {"request": request})

@app.get("/admin")
async def get_admin(request: Request):
    return HTMLResponse("<h1>Админ панель</h1>")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    query = websocket.url.query
    user_id = None
    if "user=" in query:
        user_id = query.split("user=")[-1]
    else:
        await websocket.close()
        return
    clients[user_id] = websocket
    chat_history.setdefault(user_id, [])
    for msg in chat_history[user_id]:
        await websocket.send_text(msg)
    try:
        while True:
            data = await websocket.receive_text()
            message = f"🧑 {data}"
            chat_history[user_id].append(message)
            await websocket.send_text(message)
            for cid, sock in clients.items():
                if cid.startswith("admin_") and cid != user_id:
                    await sock.send_text(f"{user_id}:{message}")
    except WebSocketDisconnect:
        clients.pop(user_id, None)
