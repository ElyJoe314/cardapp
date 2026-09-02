"""
Persists game state as JSON blobs. Uses Upstash Redis (REST API, works over
plain HTTPS so it's fine from Vercel's serverless functions). Falls back to an
in-process dict when Upstash env vars aren't set, so `vercel dev` / local
testing still works (state just won't survive a cold start).
"""
import os
import json
import time
import random
import string

_UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
_UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

_local_store = {}

if _UPSTASH_URL and _UPSTASH_TOKEN:
    import urllib.request

    def _redis_call(*parts):
        url = _UPSTASH_URL.rstrip("/") + "/" + "/".join(urllib.parse.quote(str(p), safe="") for p in parts)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_UPSTASH_TOKEN}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    import urllib.parse

    def get_state(room):
        res = _redis_call("get", f"room:{room}")
        val = res.get("result")
        return json.loads(val) if val else None

    def set_state(room, state):
        _redis_call("set", f"room:{room}", json.dumps(state))

    def delete_state(room):
        _redis_call("del", f"room:{room}")

    BACKEND = "upstash"
else:
    def get_state(room):
        return _local_store.get(room)

    def set_state(room, state):
        _local_store[room] = state

    def delete_state(room):
        _local_store.pop(room, None)

    BACKEND = "local-memory (dev only, not shared across serverless instances)"


def new_room_code():
    return "".join(random.choices(string.ascii_uppercase, k=4))
