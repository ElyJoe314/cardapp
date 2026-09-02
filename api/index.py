import uuid
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import engine
import storage

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load(room):
    state = storage.get_state(room)
    if not state:
        raise HTTPException(404, "Room not found.")
    return state


def _save(room, state):
    storage.set_state(room, state)


# ---------- request bodies ----------

class CreateRoomBody(BaseModel):
    name: str
    starting_chips: int = 1000
    small_blind: int = 5
    big_blind: int = 10


class JoinBody(BaseModel):
    room: str
    name: str


class RoomPlayerBody(BaseModel):
    room: str
    player_id: str


class ActionBody(BaseModel):
    room: str
    player_id: str
    action: str
    amount: int = 0


# ---------- routes ----------

@app.get("/api/health")
def health():
    return {"ok": True, "storage_backend": storage.BACKEND}


@app.post("/api/create_room")
def create_room(body: CreateRoomBody):
    room = storage.new_room_code()
    state = engine.new_room(room, small_blind=body.small_blind, big_blind=body.big_blind)
    pid = str(uuid.uuid4())
    engine.add_player(state, pid, body.name, chips=body.starting_chips)
    _save(room, state)
    return {"room": room, "player_id": pid}


@app.post("/api/join")
def join(body: JoinBody):
    room = body.room.strip().upper()
    state = _load(room)
    pid = str(uuid.uuid4())
    default_chips = state["players"][0]["chips"] if state["players"] else 1000
    engine.add_player(state, pid, body.name, chips=default_chips)
    _save(room, state)
    return {"room": room, "player_id": pid}


@app.get("/api/state")
def get_state(room: str, player_id: str = ""):
    state = _load(room.strip().upper())
    return engine.public_state(state, viewer_id=player_id or None)


@app.post("/api/start_hand")
def start_hand(body: RoomPlayerBody):
    state = _load(body.room.strip().upper())
    try:
        engine.start_hand(state)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _save(body.room.strip().upper(), state)
    return engine.public_state(state, viewer_id=body.player_id)


@app.post("/api/action")
def action(body: ActionBody):
    room = body.room.strip().upper()
    state = _load(room)
    try:
        engine.apply_action(state, body.player_id, body.action, amount=body.amount)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _save(room, state)
    return engine.public_state(state, viewer_id=body.player_id)


@app.post("/api/next_hand")
def next_hand(body: RoomPlayerBody):
    """Reset from showdown back to waiting so the host can deal again."""
    room = body.room.strip().upper()
    state = _load(room)
    if state["stage"] != "showdown":
        raise HTTPException(400, "Hand is not over yet.")
    engine.reset_to_waiting(state)
    _save(room, state)
    return engine.public_state(state, viewer_id=body.player_id)


@app.post("/api/leave")
def leave(body: RoomPlayerBody):
    room = body.room.strip().upper()
    state = _load(room)
    engine.remove_player(state, body.player_id)
    _save(room, state)
    return {"ok": True}
