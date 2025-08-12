# Race Flags — Mesh-ready Open Source System

Author: Leonardo Galli (open-source)
License: MIT

## Overview

This project provides a small, self-hosted race management system optimized for track usage:

- `backend.py` — authoritative backend (FastAPI). REST + WebSocket. UDP discovery + peer sync (repeaters). Stores state in SQLite. Exposes endpoints to control flags, sectors, safety car, penalties, pit boxes, and generic GT events (out-window open/close, open_in_this_lap, etc.). No authentication for Race Control endpoints (per project requirement); pitboxes require passwords.
- `client_hub.py` — unified web UI (Pilots / Pit / Race Control / Marshals / Penalties / Events). Auto-discovers the nearest backend and connects automatically.
- `display_board.py` — kiosk display for flag/status; can identify itself as semaphore or sector and notifies Race Control.

**Important:** Race Control endpoints are purposely unauthenticated to match the requested behavior. Run on an isolated/trusted network (e.g. the race local network or behind VPN). Pitbox passwords are hashed (bcrypt).

## Features

- UDP discovery (broadcast) on port `9999` so multiple backends and clients discover each other.
- Peer sync: when backends discover each other, they connect and sync state so the network is redundant.
- Full real-time updates via WebSocket (`/ws`) to all clients.
- Penalty system with reason, who hit who, amount (seconds) and type (time, drive-through, stop-go).
- Events API for GT incidents and mechanical events such as `out_window_open`, `out_window_close`, and `open_in_this_lap`.
- Identify displays: displays call `/api/identify_device` to notify Race Control they are a semaphore or sector display.
- Simple, polished Bootstrap UI in English with a penalty workflow and event composer.

## Quick install

1. Requirements:
   - Python 3.10+
   - On each machine: Python packages below

2. Install dependencies:
```bash
python -m pip install fastapi uvicorn websockets bcrypt

	3.	Files & folders:

race-system/
├─ backend.py
├─ client_hub.py
├─ display_board.py
└─ data/   (auto-created SQLite DB)

	4.	Start backend:

python backend.py

	5.	Start client hub (on any machine in the same LAN):

python client_hub.py
# open http://<client_hub_ip>:8080/

	6.	Start display board (kiosk):

python display_board.py
# open http://<display_ip>:8090/

Main REST endpoints (examples)
	•	GET /api/state — get full state
	•	POST /api/register_pilot — firstName, lastName, number
	•	POST /api/set_flag — flag (yellow/red/blue/black-checkered/none)
	•	POST /api/start_race
	•	POST /api/reset_race
	•	POST /api/safety_car/set — active=1|0, in_this_lap=1|0
	•	POST /api/sector/set_flag — sector_id=1|2|3, flag=..., marshal_intervene=0|1
	•	POST /api/pilot/assign_blue — number, assign=1|0
	•	POST /api/pitbox/create — box_id, password
	•	POST /api/pitbox/send — box_id, password, action, note
	•	POST /api/penalty/add — target_number, penalty_type, amount_seconds, reason, who_hit, contact_person, comment
	•	POST /api/event — event_type, sector_id, number, details
	•	POST /api/identify_device — kind=semaphore|sector, sector_id, host

Running multiple backends / repeaters
	•	Backends broadcast {"type":"backend_announce","host":"<ip>","port":8000} via UDP broadcast on port 9999.
	•	When a backend sees another announce, it connects to that peer’s /peer_ws WebSocket and exchanges state — creating a mesh of backends that replicate the race state.
	•	Client hubs and display boards listen to the broadcast and automatically connect to the most-recent backend discovered.

Access Point (RACE) example (Raspberry Pi)

If you deploy repeaters using Raspberry Pi, you can create an Access Point SSID named RACE with a long alphanumeric password.

Sample hostapd.conf:

interface=wlan0
driver=nl80211
ssid=RACE
hw_mode=g
channel=6
wmm_enabled=1
auth_algs=1
wpa=2
wpa_passphrase=R4c3S3cur3P@ssw0rd2025ABC
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP

Notes:
	•	Replace wpa_passphrase with a long alphanumeric password you generate.
	•	Use dnsmasq for DHCP and NAT or bridge to a gateway.
	•	If you require mesh coverage across the circuit, consider batman-adv or 802.11s mesh and bridge hostapd over the mesh interface.

Security & Production notes
	•	The system intentionally exposes control endpoints without authentication. Only use this in a physically controlled or isolated network.
	•	For production: add token-based auth, TLS (nginx reverse proxy + Let’s Encrypt), strict firewall rules, and protect discovery traffic if needed.
	•	This implementation uses a naive peer-sync overwrite strategy. For robust production replication, consider leader election / CRDTs or a single authoritative backend.

Extending
	•	Add browser authentication for Race Control.
	•	Add WebSocket reconnection UI and persistent client identities.
	•	Add telemetry/logging for events to CSV or external database/analytics.

License

MIT — free to use, adapt, and contribute. If you adapt, please preserve this header and attribution.

⸻

Need anything else?

I can:
	•	Add systemd service files for each script
	•	Generate a random strong RACE password and a small script to configure hostapd on Raspberry Pi
	•	Add optional token-based auth for Race Control endpoints
	•	Create a small Node/React dashboard if you prefer SPA

Tell me which of these you want next and I’ll generate it.

---

## Example `hostapd.conf` and quick `dnsmasq` tips

`/etc/hostapd/hostapd.conf`:

interface=wlan0
driver=nl80211
ssid=RACE
hw_mode=g
channel=6
wpa=2
wpa_passphrase=R4c3S3cur3P@ssw0rd2025ABC
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP

`/etc/dnsmasq.conf` snippet:

interface=wlan0
dhcp-range=192.168.50.10,192.168.50.200,12h

Enable IP forwarding / NAT from wlan0 to eth0 if required.

---
