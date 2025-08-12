#!/usr/bin/env python3
"""
client_hub.py — Unified web UI (Pilots / Pit / Race Control / Marshals / Penalties / Events)
- auto-discovers backend via UDP broadcast and exposes /backend_info
- UI is English, intuitive and responsive (Bootstrap)
Run:
    python client_hub.py
Open: http://<machine-ip>:8080/
"""

import asyncio, json, socket, os
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

DISCOVERY_PORT = int(os.environ.get("DISCOVERY_PORT", 9999))
CLIENT_PORT = int(os.environ.get("CLIENT_HUB_PORT", 8080))

app = FastAPI(title="Race Client Hub")
_best_backend=None; _best_ts=None

def get_local_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try: s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]
    except: ip="127.0.0.1"
    finally: s.close()
    return ip

async def discovery_listener():
    global _best_backend,_best_ts
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
            _best_ts=datetime.utcnow(); print("[client_hub] discovered backend", host, port)

@app.get("/backend_info")
async def backend_info():
    if _best_backend: return JSONResponse(_best_backend)
    return JSONResponse({"host":None})

# Single-page HTML — English UI with good UX
PAGE_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Race Control Hub</title><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>body{background:#071122;color:#e6eef8;font-family:Inter,Arial;padding:18px}.card{background:rgba(255,255,255,0.03);border:none;border-radius:12px;padding:16px}</style></head><body>
<div class="container">
  <div class="d-flex justify-content-between align-items-center mb-3"><h2>Race Control Hub</h2><div><strong>Backend:</strong> <span id="backend">none</span></div></div>
  <div class="row g-3">
    <div class="col-lg-4">
      <div class="card">
        <h5>Pilot Registration</h5>
        <form id="pilotForm"><input id="pFirst" class="form-control mb-2" placeholder="First name" required><input id="pLast" class="form-control mb-2" placeholder="Last name" required><input id="pNum" class="form-control mb-2" placeholder="Car number" required><button class="btn btn-primary w-100">Register Pilot</button></form>
        <pre id="pilotStatus" class="small mt-2"></pre>
      </div>

      <div class="card mt-3">
        <h5>Pit Box</h5>
        <input id="boxId" class="form-control mb-2" placeholder="Box ID (e.g. box-12)"><input id="boxPwd" type="password" class="form-control mb-2" placeholder="Password">
        <button id="createBox" class="btn btn-warning w-100 mb-2">Create / Update Box</button>
        <h6>Send action</h6>
        <select id="pitAction" class="form-select mb-2"><option value="pit_in">Pit In</option><option value="pit_out">Pit Out</option><option value="manual_time">Manual Time</option><option value="message">Message</option></select>
        <input id="pitNote" class="form-control mb-2" placeholder="Note / time">
        <button id="sendPit" class="btn btn-success w-100">Send</button>
        <pre id="pitStatus" class="small mt-2"></pre>
      </div>
    </div>

    <div class="col-lg-8">
      <div class="card">
        <h5>Race Controls & Flags</h5>
        <div class="d-flex gap-2 flex-wrap mb-2">
          <button class="btn btn-warning" data-flag="yellow">Yellow</button><button class="btn btn-danger" data-flag="red">Red</button><button class="btn btn-primary" data-flag="blue">Blue</button><button class="btn btn-dark" data-flag="black-checkered">Checkered</button><button class="btn btn-secondary" data-flag="none">Clear</button>
        </div>
        <div class="d-flex gap-2 mb-3"><button id="startRace" class="btn btn-success">Start Race</button><button id="resetRace" class="btn btn-outline-light">Reset Race</button></div>

        <div class="row g-2 mb-3">
          <div class="col-md-6">
            <h6>Safety Car</h6>
            <div class="d-flex gap-2"><button id="scOn" class="btn btn-dark">SC On</button><button id="scOff" class="btn btn-outline-dark">SC Off</button><button id="scThisLap" class="btn btn-outline-secondary">SC This Lap</button></div>
          </div>
          <div class="col-md-6">
            <h6>Sector Controls</h6>
            <div class="d-flex gap-2">
              <select id="sectorSelect" class="form-select"><option value="1">Sector 1</option><option value="2">Sector 2</option><option value="3">Sector 3</option></select>
              <select id="sectorFlagSelect" class="form-select"><option value="none">None</option><option value="yellow">Yellow</option><option value="red">Red</option><option value="blue">Blue</option></select>
              <button id="setSector" class="btn btn-primary">Set</button>
            </div>
            <div class="form-check mt-2"><input id="marshalIntervene" class="form-check-input" type="checkbox"><label class="form-check-label">Marshal intervene</label></div>
          </div>
        </div>

        <hr>
        <h5>Penalties</h5>
        <div class="row g-2 mb-2">
          <div class="col-md-4"><select id="penPilot" class="form-select"><option value="">Select Pilot</option></select></div>
          <div class="col-md-4"><select id="penType" class="form-select"><option value="time">Time (seconds)</option><option value="drive_through">Drive-through</option><option value="stop_go">Stop & Go</option><option value="time_plus">Add time</option></select></div>
          <div class="col-md-4"><input id="penSeconds" class="form-control" placeholder="Seconds (if time)"></div>
        </div>
        <div class="row g-2 mb-2">
          <div class="col-md-4"><select id="penReason" class="form-select"><option value="tracklimits">Track Limits</option><option value="contact">Contact</option><option value="other">Other</option></select></div>
          <div class="col-md-4"><select id="penWhoHit" class="form-select"><option value="">Who was hit?</option></select></div>
          <div class="col-md-4"><input id="penContact" class="form-control" placeholder="Contact person / radio"></div>
        </div>
        <textarea id="penComment" class="form-control mb-2" placeholder="Free comment"></textarea>
        <div class="d-flex gap-2"><button id="addPenalty" class="btn btn-danger">Add Penalty</button><button id="clearPenalty" class="btn btn-outline-light">Clear</button></div>
        <pre id="penStatus" class="small mt-2"></pre>
      </div>

      <div class="card mt-3">
        <h5>Events (GT / Out-window)</h5>
        <div class="row g-2">
          <div class="col-md-4"><select id="evType" class="form-select"><option value="out_window_open">Out Window Open</option><option value="out_window_close">Out Window Close</option><option value="open_in_this_lap">Open In This Lap</option><option value="gt_incident">GT Incident</option></select></div>
          <div class="col-md-4"><select id="evSector" class="form-select"><option value="">Sector (optional)</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></div>
          <div class="col-md-4"><input id="evNumber" class="form-control" placeholder="Car number (optional)"></div>
        </div>
        <input id="evDetails" class="form-control mt-2" placeholder="Details (free text)"><div class="d-flex gap-2 mt-2"><button id="sendEvent" class="btn btn-primary">Send Event</button></div>
        <pre id="evStatus" class="small mt-2"></pre>
      </div>

    </div>
  </div>
</div>

<script>
let backend=null, ws=null;

async function refreshBackend(){
  try {
    const r=await fetch('/backend_info'); const j=await r.json();
    if(j.host){ backend=j; document.getElementById('backend').textContent=j.host+':'+j.port; connectWS(); fillPilots(j); }
    else document.getElementById('backend').textContent='none';
  } catch(e){ console.error(e); }
}

function connectWS(){
  if(!backend||!backend.ws) return;
  if(ws && ws.readyState===WebSocket.OPEN) return;
  ws=new WebSocket(backend.ws);
  ws.onopen=()=>console.log('ws open');
  ws.onmessage=(ev)=>{ const obj=JSON.parse(ev.data);
    if(obj.type==='state_init' || obj.type==='state_update') { renderState(obj.state); }
    if(obj.type==='penalty_add'){ document.getElementById('penStatus').textContent += '\\nPenalty added: '+ JSON.stringify(obj.payload); }
    if(obj.type==='event'){ document.getElementById('evStatus').textContent += '\\nEvent: '+ JSON.stringify(obj.payload); }
    if(obj.type==='identify_device'){ document.getElementById('evStatus').textContent += '\\nDevice identify: '+ JSON.stringify(obj.payload); }
  };
  ws.onclose=()=>setTimeout(connectWS,1500);
}

async function postToBackend(path, body){
  const b=await fetch('/backend_info').then(r=>r.json());
  if(!b.host) return alert('No backend discovered');
  const r2 = await fetch(b.http + path, { method: 'POST', body: new URLSearchParams(body) });
  return r2;
}

function renderState(s){
  // populate pilot selects
  const ps=document.getElementById('penPilot'), who=document.getElementById('penWhoHit');
  ps.innerHTML='<option value="">Select Pilot</option>'; who.innerHTML='<option value="">Who was hit?</option>';
  s.pilots.forEach(p=>{ ps.innerHTML += `<option value="${p.number}">${p.firstName} ${p.lastName} (#${p.number})</option>`; who.innerHTML += `<option value="${p.number}">${p.firstName} ${p.lastName} (#${p.number})</option>`; });
  // show state in penStatus for context
  document.getElementById('penStatus').textContent = 'Race started: '+s.started+'  Flag: '+s.flag+'  Penalties: '+(s.penalties? s.penalties.length : 0);
}

async function fillPilots(info){
  // will be updated by ws; this ensures initial attempt
  const s = await fetch(info.http + '/api/state').then(r=>r.json()).catch(()=>null);
  if(s) renderState(s);
}

// wire UI
document.querySelectorAll('[data-flag]').forEach(btn=>btn.addEventListener('click', async ()=> { await postToBackend('/api/set_flag',{flag:btn.dataset.flag}); }));
document.getElementById('pilotForm').addEventListener('submit', async (e)=>{ e.preventDefault(); const body={firstName:document.getElementById('pFirst').value, lastName:document.getElementById('pLast').value, number:document.getElementById('pNum').value}; const r=await postToBackend('/api/register_pilot', body); document.getElementById('pilotStatus').textContent = await r.text(); });
document.getElementById('createBox').addEventListener('click', async ()=>{ const body={box_id:document.getElementById('boxId').value, password:document.getElementById('boxPwd').value}; const r=await postToBackend('/api/pitbox/create', body); document.getElementById('pitStatus').textContent = await r.text(); });
document.getElementById('sendPit').addEventListener('click', async ()=>{ const body={box_id:document.getElementById('boxId').value, password:document.getElementById('boxPwd').value, action:document.getElementById('pitAction').value, note:document.getElementById('pitNote').value}; const r=await postToBackend('/api/pitbox/send', body); document.getElementById('pitStatus').textContent = await r.text(); });

document.getElementById('startRace').addEventListener('click', async ()=>{ if(!confirm('Start race?')) return; await postToBackend('/api/start_race',{}); });
document.getElementById('resetRace').addEventListener('click', async ()=>{ if(!confirm('Reset race?')) return; await postToBackend('/api/reset_race',{}); });

document.getElementById('scOn').addEventListener('click', async ()=>{ await postToBackend('/api/safety_car/set',{active:1}); });
document.getElementById('scOff').addEventListener('click', async ()=>{ await postToBackend('/api/safety_car/set',{active:0}); });
document.getElementById('scThisLap').addEventListener('click', async ()=>{ await postToBackend('/api/safety_car/set',{active:1,in_this_lap:1}); });

document.getElementById('setSector').addEventListener('click', async ()=>{ const s=document.getElementById('sectorSelect').value; const f=document.getElementById('sectorFlagSelect').value; const mi=document.getElementById('marshalIntervene').checked?1:0; await postToBackend('/api/sector/set_flag',{sector_id:s, flag:f, marshal_intervene:mi}); });

document.getElementById('addPenalty').addEventListener('click', async ()=>{ const body={target_number:document.getElementById('penPilot').value, penalty_type:document.getElementById('penType').value, amount_seconds:document.getElementById('penSeconds').value || 0, reason:document.getElementById('penReason').value, who_hit:document.getElementById('penWhoHit').value, contact_person:document.getElementById('penContact').value, comment:document.getElementById('penComment').value}; const r=await postToBackend('/api/penalty/add', body); document.getElementById('penStatus').textContent = JSON.stringify(await r.json()); });

document.getElementById('sendEvent').addEventListener('click', async ()=>{ const body={event_type:document.getElementById('evType').value, sector_id:document.getElementById('evSector').value || "", number:document.getElementById('evNumber').value || "", details:document.getElementById('evDetails').value}; const r=await postToBackend('/api/event', body); document.getElementById('evStatus').textContent = JSON.stringify(await r.json()); });

setInterval(refreshBackend,1500);
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def index(): return HTMLResponse(PAGE_HTML)

@app.on_event("startup")
async def startup(): asyncio.create_task(discovery_listener()); print("Client Hub running on port", CLIENT_PORT)

def main(): uvicorn.run("client_hub:app", host="0.0.0.0", port=CLIENT_PORT, reload=False)

if __name__ == "__main__": main()