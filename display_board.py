#!/usr/bin/env python3
"""
display_board.py — Kiosk / tabellone
- auto-discovers backend via UDP and connects via WebSocket
- shows global flag, safety car, 3 sectors, penalties summary
- provides Identify buttons: I'm Semaphore / I'm Sector 1/2/3 (calls /api/identify_device)
Run:
    python display_board.py
Open: http://<device-ip>:8090/
"""

import asyncio, json, socket, os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

DISCOVERY_PORT = int(os.environ.get("DISCOVERY_PORT", 9999))
DISPLAY_PORT = int(os.environ.get("DISPLAY_PORT", 8090))

app = FastAPI(title="Display Board")
_best_backend=None

def get_local_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try: s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]
    except: ip="127.0.0.1"
    finally: s.close()
    return ip

async def discovery_listener():
    global _best_backend
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
            _best_backend={"host":host,"port":port,"ws":f"ws://{host}:{port}/ws","http":f"http://{host}:{port}"}
            print("[display] discovered backend", host, port)

@app.get("/backend_info")
async def backend_info():
    if _best_backend: return JSONResponse(_best_backend)
    return JSONResponse({"host":None})

PAGE_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Display Board</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{background:#071122;color:#e6eef8;padding:12px}.flag{font-size:48px;font-weight:800;padding:18px;border-radius:10px;text-align:center}.flag-none{background:#e9ecef;color:#222}.flag-yellow{background:#FFEE58;color:#222}.flag-red{background:#FF6B6B;color:#fff}.flag-blue{background:#6EA8FE;color:#07203a}.flag-check{background:black;color:white;background-image: linear-gradient(45deg,#000 25%, #fff 25%, #fff 50%, #000 50%, #000 75%, #fff 75%, #fff 100%);background-size:28px 28px}</style></head><body>
<div class="container"><h3>Display Board</h3><div>Backend: <span id="backend">none</span></div>
<div class="card p-3 my-3"><div class="row"><div class="col-md-6"><div>GLOBAL FLAG</div><div id="globalFlag" class="flag flag-none">—</div><div>Safety Car: <span id="sc">no</span></div></div><div class="col-md-6"><div>SECTORS</div><div id="sectors" class="mt-2"></div></div></div></div>
<div class="card p-3"><h5>Identify device</h5><div class="d-flex gap-2"><button id="asSemaphore" class="btn btn-outline-light">I'm Semaphore</button><button id="asSector1" class="btn btn-outline-light">I'm Sector 1</button><button id="asSector2" class="btn btn-outline-light">I'm Sector 2</button><button id="asSector3" class="btn btn-outline-light">I'm Sector 3</button></div><div id="identifyStatus" class="mt-2 small"></div></div>
<div class="card p-3 mt-3"><h5>Penalties Snapshot</h5><div id="penSnapshot" class="small"></div></div>
</div>
<script>
let backend=null, ws=null;
async function fetchBackend(){ try{ const r=await fetch('/backend_info'); const j=await r.json(); if(j.host){ backend=j; document.getElementById('backend').textContent=j.host+':'+j.port; connectWS(); }else document.getElementById('backend').textContent='none'; }catch(e){} }
function connectWS(){ if(!backend||!backend.ws) return; if(ws && ws.readyState===WebSocket.OPEN) return; ws=new WebSocket(backend.ws); ws.onmessage=(ev)=>{ const obj=JSON.parse(ev.data); if(obj.type==='state_init'||obj.type==='state_update') renderState(obj.state); if(obj.type==='penalty_add'||obj.type==='penalty_add') updatePenalties(obj.payload); }; ws.onclose=()=>setTimeout(connectWS,1500); }
function renderState(s){ const f=s.flag||'none'; const gf=document.getElementById('globalFlag'); gf.textContent=f.toUpperCase(); gf.className='flag '+(f==='yellow'?'flag-yellow':f==='red'?'flag-red':f==='blue'?'flag-blue':f==='black-checkered'?'flag-check':'flag-none'); document.getElementById('sc').textContent=s.safety_car?'YES':'NO'; let secHtml=''; s.sectors.forEach(sec=>{ secHtml+=`<div class="p-2 mb-2" style="background:rgba(255,255,255,0.03);border-radius:8px">Sector ${sec.sector_id}: <strong>${sec.flag}</strong> ${sec.marshal_intervene?'<span style="color:#ff8080">• MARSHAL</span>':''}</div>`; }); document.getElementById('sectors').innerHTML=secHtml; updatePenList(s.penalties||[]); }
function updatePenList(list){ if(!list || list.length===0) { document.getElementById('penSnapshot').textContent='No penalties'; return; } let out=''; list.slice(0,6).forEach(p=> out+=`#${p.target_number} • ${p.type} • ${p.reason} • ${p.amount_seconds ? p.amount_seconds+'s' : ''} • ${p.comment || ''}\\n`); document.getElementById('penSnapshot').textContent=out; }
async function identify(kind, sector){ if(!backend) return alert('No backend'); const body=new URLSearchParams({kind:kind, sector_id: sector||'', host: location.hostname}); const r=await fetch(backend.http + '/api/identify_device',{method:'POST', body}); document.getElementById('identifyStatus').textContent='Identified: '+kind+(sector?' '+sector:''); }
document.getElementById('asSemaphore').addEventListener('click', ()=>identify('semaphore','')); document.getElementById('asSector1').addEventListener('click', ()=>identify('sector',1)); document.getElementById('asSector2').addEventListener('click', ()=>identify('sector',2)); document.getElementById('asSector3').addEventListener('click', ()=>identify('sector',3));
setInterval(fetchBackend,1500);
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def page(): return HTMLResponse(PAGE_HTML)

@app.on_event("startup")
async def startup(): asyncio.create_task(discovery_listener()); print("Display board running on port", DISPLAY_PORT)

def main(): uvicorn.run("display_board:app", host="0.0.0.0", port=DISPLAY_PORT, reload=False)

if __name__ == "__main__": main()