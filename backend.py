#!/usr/bin/env python3
"""
backend.py — Race Flags Backend (Open-source MIT)

Features:
- FastAPI REST endpoints for race control (no auth per request): flags, sectors, safety car, penalties, pitboxes
- WebSocket broadcast endpoint /ws for clients (client_hub, displays, etc.)
- UDP discovery and peer sync: multiple backend instances auto-discover and sync state
- Events system: generic /api/event for GT events, Out-window open/close, "open_in_this_lap", etc.
- Penalty system stored in SQLite
- Pitboxes with bcrypt-hashed passwords

Run:
    python backend.py

Dependencies:
    pip install fastapi uvicorn websockets bcrypt
"""

import asyncio, json, os, socket, sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import websockets
import bcrypt

# --- Config ---
HTTP_HOST = "0.0.0.0"
HTTP_PORT = int(os.environ.get("BACKEND_PORT", 8000))
DISCOVERY_PORT = int(os.environ.get("DISCOVERY_PORT", 9999))
DISCOVERY_INTERVAL = 2.0
PEER_WS_PATH = "/peer_ws"
DBPATH = os.path.join("data", "race.db")

os.makedirs("data", exist_ok=True)
app = FastAPI(title="Race Flags Backend (Open Source)")

# ---------------- Database ----------------
def init_db():
    conn = sqlite3.connect(DBPATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS race (
        id INTEGER PRIMARY KEY CHECK(id=1),
        started INTEGER DEFAULT 0,
        flag TEXT DEFAULT 'none',
        safety_car INTEGER DEFAULT 0,
        safety_car_this_lap INTEGER DEFAULT 0,
        updated_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS pilots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        firstName TEXT,
        lastName TEXT,
        number TEXT UNIQUE,
        blue_flag INTEGER DEFAULT 0,
        registered_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS pitboxes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        box_id TEXT UNIQUE,
        pwd_hash TEXT,
        last_update TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sectors (
        sector_id INTEGER PRIMARY KEY,
        flag TEXT DEFAULT 'none',
        marshal_intervene INTEGER DEFAULT 0,
        last_update TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS penalties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_number TEXT,
        type TEXT,
        amount_seconds INTEGER,
        reason TEXT,
        who_hit TEXT,
        contact_person TEXT,
        comment TEXT,
        ts TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        payload TEXT,
        ts TEXT
    )""")
    c.execute("INSERT OR IGNORE INTO race (id, started, flag, safety_car, safety_car_this_lap, updated_at) VALUES (1,0,'none',0,0,?)",
              (datetime.utcnow().isoformat(),))
    for s in (1,2,3):
        c.execute("INSERT OR IGNORE INTO sectors (sector_id, flag, marshal_intervene, last_update) VALUES (?, 'none', 0, ?)",
                  (s, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def db_get_state() -> Dict[str, Any]:
    conn = sqlite3.connect(DBPATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    race = c.execute("SELECT started, flag, safety_car, safety_car_this_lap, updated_at FROM race WHERE id=1").fetchone()
    pilots = c.execute("SELECT firstName, lastName, number, blue_flag FROM pilots ORDER BY registered_at").fetchall()
    sectors = c.execute("SELECT sector_id, flag, marshal_intervene, last_update FROM sectors ORDER BY sector_id").fetchall()
    penalties = c.execute("SELECT * FROM penalties ORDER BY ts DESC").fetchall()
    conn.close()
    return {
        "started": bool(race["started"]),
        "flag": race["flag"],
        "safety_car": bool(race["safety_car"]),
        "safety_car_this_lap": bool(race["safety_car_this_lap"]),
        "pilots": [dict(p) for p in pilots],
        "sectors": [dict(s) for s in sectors],
        "penalties": [dict(p) for p in penalties],
        "updated_at": race["updated_at"]
    }

def db_execute(query: str, params=()):
    conn = sqlite3.connect(DBPATH)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    conn.close()

init_db()

# ---------------- WebSocket client manager ----------------
class ClientManager:
    def __init__(self):
        self.clients: List[WebSocket] = []
        self.lock = asyncio.Lock()
    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.clients.append(ws)
    async def disconnect(self, ws: WebSocket):
        async with self.lock:
            if ws in self.clients: self.clients.remove(ws)
    async def broadcast(self, message: Dict[str, Any]):
        text = json.dumps(message)
        dead=[]
        async with self.lock:
            for ws in list(self.clients):
                try: await ws.send_text(text)
                except Exception: dead.append(ws)
            for d in dead:
                if d in self.clients: self.clients.remove(d)

client_mgr = ClientManager()

# ---------------- Peer manager (sync) ----------------
class PeerManager:
    def __init__(self): 
        self.peers={}
        self.lock=asyncio.Lock()
    async def connect_to_peer(self, ws_url: str):
        async with self.lock:
            if ws_url in self.peers: return
            try:
                ws = await websockets.connect(ws_url)
                self.peers[ws_url]=ws
                await ws.send(json.dumps({"cmd":"request_state"}))
                asyncio.create_task(self._reader_task(ws_url, ws))
                print("Connected to peer", ws_url)
            except Exception:
                pass
    async def _reader_task(self, ws_url, ws):
        try:
            async for msg in ws:
                try: obj=json.loads(msg)
                except: continue
                if obj.get("type")=="state_update" and obj.get("state"):
                    await apply_state_from_peer(obj["state"])
                elif obj.get("cmd")=="request_state":
                    await ws.send(json.dumps({"type":"state_update","state": db_get_state()}))
        except Exception: pass
        finally:
            async with self.lock:
                if ws_url in self.peers: del self.peers[ws_url]
    async def broadcast_to_peers(self, message: Dict[str,Any]):
        text=json.dumps(message)
        async with self.lock:
            dead=[]
            for url, ws in list(self.peers.items()):
                try: await ws.send(text)
                except Exception: dead.append(url)
            for d in dead: 
                if d in self.peers: del self.peers[d]

peer_mgr = PeerManager()

async def apply_state_from_peer(state: Dict[str,Any]):
    # simple replace merge: overwrite authoritative tables
    try:
        db_execute("UPDATE race SET started=?, flag=?, safety_car=?, safety_car_this_lap=?, updated_at=? WHERE id=1",
                   (1 if state.get("started") else 0, state.get("flag","none"), 1 if state.get("safety_car") else 0, 1 if state.get("safety_car_this_lap") else 0, datetime.utcnow().isoformat()))
        db_execute("DELETE FROM pilots")
        for p in state.get("pilots",[]): db_execute("INSERT INTO pilots (firstName,lastName,number,blue_flag,registered_at) VALUES (?,?,?,?,?)",
                                                   (p.get("firstName"),p.get("lastName"),p.get("number"),1 if p.get("blue_flag") else 0, datetime.utcnow().isoformat()))
        for sec in state.get("sectors",[]): db_execute("UPDATE sectors SET flag=?, marshal_intervene=?, last_update=? WHERE sector_id=?",
                                                      (sec.get("flag","none"), 1 if sec.get("marshal_intervene") else 0, datetime.utcnow().isoformat(), sec.get("sector_id")))
        new_state = db_get_state()
        await client_mgr.broadcast({"type":"state_update","state":new_state})
    except Exception as e:
        print("apply_state_from_peer error:", e)

# ---------------- UDP discovery ----------------
def get_local_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]
    except: ip="127.0.0.1"
    finally: s.close()
    return ip

async def discovery_announcer():
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
    ip=get_local_ip()
    loop=asyncio.get_event_loop()
    try:
        while True:
            msg=json.dumps({"type":"backend_announce","host":ip,"port":HTTP_PORT})
            await loop.run_in_executor(None, sock.sendto, msg.encode(), ('<broadcast>', DISCOVERY_PORT))
            await asyncio.sleep(DISCOVERY_INTERVAL)
    finally: sock.close()

async def discovery_listener():
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind(('', DISCOVERY_PORT))
    sock.setblocking(False)
    loop=asyncio.get_event_loop()
    while True:
        try:
            data, addr = await loop.run_in_executor(None, sock.recvfrom, 4096)
        except Exception:
            await asyncio.sleep(0.1); continue
        try: obj=json.loads(data.decode())
        except: continue
        if obj.get("type")=="backend_announce":
            host=obj.get("host"); port=obj.get("port")
            if host==get_local_ip() and int(port)==HTTP_PORT: continue
            ws_url=f"ws://{host}:{port}{PEER_WS_PATH}"
            asyncio.create_task(peer_mgr.connect_to_peer(ws_url))

# ---------------- REST API endpoints ----------------
@app.get("/api/state")
async def api_state(): return db_get_state()

@app.post("/api/register_pilot")
async def api_register_pilot(firstName: str = Form(...), lastName: str = Form(...), number: str = Form(...)):
    state=db_get_state()
    if state["started"]: raise HTTPException(status_code=400, detail="Race already started")
    try:
        db_execute("INSERT INTO pilots (firstName,lastName,number,registered_at) VALUES (?,?,?,?)",
                   (firstName,lastName,number,datetime.utcnow().isoformat()))
    except sqlite3.IntegrityError: raise HTTPException(status_code=400, detail="Number already registered")
    new=db_get_state()
    db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)", ("register_pilot", json.dumps({"firstName":firstName,"lastName":lastName,"number":number}), datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"state_update","state":new})
    await peer_mgr.broadcast_to_peers({"type":"state_update","state":new})
    return {"ok":True,"state":new}

@app.post("/api/set_flag")
async def api_set_flag(flag: str = Form(...)):
    db_execute("UPDATE race SET flag=?, updated_at=? WHERE id=1",(flag,datetime.utcnow().isoformat()))
    s=db_get_state(); db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)", ("set_flag", json.dumps({"flag":flag}), datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"flag_change","flag":flag,"state":s}); await peer_mgr.broadcast_to_peers({"type":"flag_change","flag":flag,"state":s})
    return {"ok":True}

@app.post("/api/start_race")
async def api_start_race():
    db_execute("UPDATE race SET started=1, updated_at=? WHERE id=1",(datetime.utcnow().isoformat(),))
    s=db_get_state(); db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)", ("start_race","{}",datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"race_start","state":s}); await peer_mgr.broadcast_to_peers({"type":"race_start","state":s})
    return {"ok":True}

@app.post("/api/reset_race")
async def api_reset_race():
    db_execute("UPDATE race SET started=0, flag='none', safety_car=0, safety_car_this_lap=0, updated_at=? WHERE id=1",(datetime.utcnow().isoformat(),))
    db_execute("DELETE FROM pilots"); 
    for s in (1,2,3): db_execute("UPDATE sectors SET flag='none', marshal_intervene=0, last_update=? WHERE sector_id=?",(datetime.utcnow().isoformat(), s))
    s=db_get_state(); db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)", ("reset_race","{}",datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"reset","state":s}); await peer_mgr.broadcast_to_peers({"type":"reset","state":s})
    return {"ok":True}

@app.post("/api/safety_car/set")
async def api_safety_car(active: int = Form(...), in_this_lap: int = Form(0)):
    db_execute("UPDATE race SET safety_car=?, safety_car_this_lap=?, updated_at=? WHERE id=1",(1 if active else 0, 1 if in_this_lap else 0, datetime.utcnow().isoformat()))
    s=db_get_state(); db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)", ("safety_car", json.dumps({"active":active,"in_this_lap":in_this_lap}), datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"safety_car","state":s}); await peer_mgr.broadcast_to_peers({"type":"safety_car","state":s})
    return {"ok":True}

@app.post("/api/sector/set_flag")
async def api_sector_set_flag(sector_id: int = Form(...), flag: str = Form(...), marshal_intervene: int = Form(0)):
    if sector_id not in (1,2,3): raise HTTPException(status_code=400, detail="sector must be 1..3")
    db_execute("UPDATE sectors SET flag=?, marshal_intervene=?, last_update=? WHERE sector_id=?", (flag, 1 if marshal_intervene else 0, datetime.utcnow().isoformat(), sector_id))
    s=db_get_state(); db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)", ("sector_set", json.dumps({"sector":sector_id,"flag":flag,"marshal_intervene":marshal_intervene}), datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"sector_update","sector_id":sector_id,"state":s}); await peer_mgr.broadcast_to_peers({"type":"sector_update","sector_id":sector_id,"state":s})
    return {"ok":True}

@app.post("/api/pilot/assign_blue")
async def api_assign_blue(number: str = Form(...), assign: int = Form(...)):
    db_execute("UPDATE pilots SET blue_flag=? WHERE number=?",(1 if assign else 0, number))
    s=db_get_state(); await client_mgr.broadcast({"type":"blue_assign","number":number,"assign":bool(assign),"state":s}); await peer_mgr.broadcast_to_peers({"type":"blue_assign","number":number,"assign":bool(assign),"state":s})
    return {"ok":True}

@app.post("/api/pitbox/create")
async def api_pitbox_create(box_id: str = Form(...), password: str = Form(...)):
    if not box_id or not password: raise HTTPException(status_code=400, detail="missing")
    pwd_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try: db_execute("INSERT INTO pitboxes (box_id, pwd_hash, last_update) VALUES (?,?,?)",(box_id,pwd_hash,datetime.utcnow().isoformat()))
    except sqlite3.IntegrityError: db_execute("UPDATE pitboxes SET pwd_hash=?, last_update=? WHERE box_id=?",(pwd_hash,datetime.utcnow().isoformat(),box_id))
    return {"ok":True}

@app.post("/api/pitbox/send")
async def api_pitbox_send(box_id: str = Form(...), password: str = Form(...), action: str = Form(...), note: str = Form(None)):
    conn=sqlite3.connect(DBPATH); conn.row_factory=sqlite3.Row; cur=conn.cursor()
    row=cur.execute("SELECT pwd_hash FROM pitboxes WHERE box_id=?",(box_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(status_code=403, detail="no such box")
    if not bcrypt.checkpw(password.encode(), row["pwd_hash"].encode()): raise HTTPException(status_code=403, detail="bad pwd")
    payload={"box_id":box_id,"action":action,"note":note,"ts":datetime.utcnow().isoformat()}
    db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)",("pit_action", json.dumps(payload), datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"pit_action","payload":payload}); await peer_mgr.broadcast_to_peers({"type":"pit_action","payload":payload})
    return {"ok":True}

# Penalties endpoint
@app.post("/api/penalty/add")
async def api_penalty_add(target_number: str = Form(...), penalty_type: str = Form(...), amount_seconds: Optional[int] = Form(None), reason: Optional[str] = Form(None), who_hit: Optional[str] = Form(None), contact_person: Optional[str] = Form(None), comment: Optional[str] = Form(None)):
    ts = datetime.utcnow().isoformat()
    db_execute("INSERT INTO penalties (target_number,type,amount_seconds,reason,who_hit,contact_person,comment,ts) VALUES (?,?,?,?,?,?,?,?)",
               (target_number, penalty_type, amount_seconds or 0, reason or "", who_hit or "", contact_person or "", comment or "", ts))
    payload={"target_number":target_number,"type":penalty_type,"amount_seconds":amount_seconds or 0,"reason":reason,"who_hit":who_hit,"contact_person":contact_person,"comment":comment,"ts":ts}
    db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)",("penalty_add", json.dumps(payload), ts))
    s=db_get_state(); await client_mgr.broadcast({"type":"penalty_add","payload":payload,"state":s}); await peer_mgr.broadcast_to_peers({"type":"penalty_add","payload":payload,"state":s})
    return {"ok":True,"payload":payload}

# Generic event endpoint (GT events, out-window open/close, open_in_this_lap, etc.)
@app.post("/api/event")
async def api_event(event_type: str = Form(...), sector_id: Optional[int] = Form(None), number: Optional[str] = Form(None), details: Optional[str] = Form(None)):
    ts = datetime.utcnow().isoformat()
    payload={"event_type":event_type,"sector_id":sector_id,"number":number,"details":details,"ts":ts}
    db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)",("event", json.dumps(payload), ts))
    await client_mgr.broadcast({"type":"event","payload":payload}); await peer_mgr.broadcast_to_peers({"type":"event","payload":payload})
    return {"ok":True,"payload":payload}

# Identify display endpoint
@app.post("/api/identify_device")
async def api_identify_device(kind: str = Form(...), sector_id: Optional[int] = Form(None), host: Optional[str] = Form(None)):
    payload={"kind":kind,"sector_id":sector_id,"host":host,"ts":datetime.utcnow().isoformat()}
    db_execute("INSERT INTO actions (type,payload,ts) VALUES (?,?,?)",("identify_device", json.dumps(payload), datetime.utcnow().isoformat()))
    await client_mgr.broadcast({"type":"identify_device","payload":payload}); await peer_mgr.broadcast_to_peers({"type":"identify_device","payload":payload})
    return {"ok":True,"payload":payload}

# WebSocket endpoint for clients
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await client_mgr.connect(ws)
    try:
        await ws.send_text(json.dumps({"type":"state_init","state": db_get_state()}))
        while True:
            data = await ws.receive_text()
            try:
                obj=json.loads(data)
                if obj.get("cmd")=="ping": await ws.send_text(json.dumps({"type":"pong","ts":datetime.utcnow().isoformat()}))
            except Exception: pass
    except WebSocketDisconnect:
        await client_mgr.disconnect(ws)

# Peer WS endpoint
@app.websocket("/peer_ws")
async def peer_ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        async for msg in ws.iter_text():
            try: obj=json.loads(msg)
            except: continue
            if obj.get("cmd")=="request_state": await ws.send_text(json.dumps({"type":"state_update","state": db_get_state()}))
            elif obj.get("type")=="state_update" and obj.get("state"): await apply_state_from_peer(obj["state"])
    except Exception: pass
    finally:
        try: await ws.close()
        except: pass

# Debug index
@app.get("/", response_class=HTMLResponse)
async def index():
    st=db_get_state()
    html=f"<html><body><h3>Race Backend</h3><pre>{json.dumps(st,indent=2)}</pre><p>ws: ws://{get_local_ip()}:{HTTP_PORT}/ws</p></body></html>"
    return HTMLResponse(html)

@app.get("/health")
async def health(): return {"ok":True,"ts":datetime.utcnow().isoformat()}

# Startup tasks: discovery announcer + listener
async def on_start():
    asyncio.create_task(discovery_announcer())
    asyncio.create_task(discovery_listener())

@app.on_event("startup")
async def startup_event():
    print("Backend starting on", HTTP_HOST, HTTP_PORT)
    asyncio.create_task(on_start())

def main():
    uvicorn.run("backend:app", host=HTTP_HOST, port=HTTP_PORT, reload=False)

if __name__ == "__main__":
    main()