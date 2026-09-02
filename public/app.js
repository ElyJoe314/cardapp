const $ = (sel) => document.querySelector(sel);
const $all = (sel) => Array.from(document.querySelectorAll(sel));

const SUITS = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RED_SUITS = new Set(["h", "d"]);

let room = localStorage.getItem("poker_room") || "";
let playerId = localStorage.getItem("poker_player_id") || "";
let pollTimer = null;
let lastStage = null;

// ---------------- Card rendering ----------------

function cardEl(cardStr, faceDown = false) {
  const el = document.createElement("div");
  if (faceDown || !cardStr) {
    el.className = "card back";
    return el;
  }
  const rank = cardStr.slice(0, -1);
  const suitCode = cardStr.slice(-1).toLowerCase();
  const isRed = RED_SUITS.has(suitCode);
  el.className = "card" + (isRed ? " red" : "");
  el.innerHTML = `<div>${rank}</div><div class="suit">${SUITS[suitCode] || suitCode}</div>`;
  return el;
}

// ---------------- Lobby ----------------

$all(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $all(".tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $("#create-form").classList.toggle("hidden", tab !== "create");
    $("#join-form").classList.toggle("hidden", tab !== "join");
    $("#lobby-error").classList.add("hidden");
  });
});

function showError(msg) {
  const e = $("#lobby-error");
  e.textContent = msg;
  e.classList.remove("hidden");
}

$("#create-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = $("#create-name").value.trim();
  const chips = parseInt($("#create-chips").value, 10) || 1000;
  const [sb, bb] = $("#create-blinds").value.split(",").map(Number);
  try {
    const res = await fetch("/api/create_room", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, starting_chips: chips, small_blind: sb, big_blind: bb }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Could not create room.");
    const data = await res.json();
    enterTable(data.room, data.player_id, name);
  } catch (err) {
    showError(err.message);
  }
});

$("#join-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = $("#join-name").value.trim();
  const code = $("#join-code").value.trim().toUpperCase();
  try {
    const res = await fetch("/api/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, room: code }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Could not join room.");
    const data = await res.json();
    enterTable(data.room, data.player_id, name);
  } catch (err) {
    showError(err.message);
  }
});

function enterTable(r, pid, name) {
  room = r;
  playerId = pid;
  localStorage.setItem("poker_room", room);
  localStorage.setItem("poker_player_id", playerId);
  localStorage.setItem("poker_name", name);
  $("#lobby").classList.add("hidden");
  $("#table-screen").classList.remove("hidden");
  $("#room-code").textContent = room;
  startPolling();
}

// pre-fill join code from URL (?room=ABCD) for shared invite links
const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get("room")) {
  $("#join-code").value = urlParams.get("room").toUpperCase();
  $all(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === "join"));
  $("#create-form").classList.add("hidden");
  $("#join-form").classList.remove("hidden");
}

// auto-rejoin if we have a saved session
if (room && playerId) {
  fetch(`/api/state?room=${room}&player_id=${playerId}`)
    .then((res) => {
      if (!res.ok) throw new Error("gone");
      $("#lobby").classList.add("hidden");
      $("#table-screen").classList.remove("hidden");
      $("#room-code").textContent = room;
      startPolling();
    })
    .catch(() => {
      localStorage.removeItem("poker_room");
      localStorage.removeItem("poker_player_id");
    });
}

// ---------------- Polling & rendering ----------------

function startPolling() {
  fetchState();
  pollTimer = setInterval(fetchState, 1000);
}

async function fetchState() {
  try {
    const res = await fetch(`/api/state?room=${room}&player_id=${playerId}`);
    if (!res.ok) {
      clearInterval(pollTimer);
      alert("This table no longer exists.");
      backToLobby();
      return;
    }
    const state = await res.json();
    render(state);
  } catch (err) {
    // transient network hiccup, ignore and retry next tick
  }
}

function backToLobby() {
  localStorage.removeItem("poker_room");
  localStorage.removeItem("poker_player_id");
  location.reload();
}

$("#leave-btn").addEventListener("click", async () => {
  if (!confirm("Leave the table?")) return;
  await fetch("/api/leave", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ room, player_id: playerId }),
  });
  clearInterval(pollTimer);
  backToLobby();
});

$("#copy-room").addEventListener("click", () => {
  const url = `${location.origin}${location.pathname}?room=${room}`;
  navigator.clipboard.writeText(url).then(() => {
    $("#copy-room").textContent = "copied!";
    setTimeout(() => ($("#copy-room").textContent = "copy link"), 1500);
  });
});

$("#log-toggle").addEventListener("click", () => {
  const body = $("#log-body");
  const hidden = body.classList.toggle("hidden");
  $("#log-toggle").textContent = hidden ? "show" : "hide";
});

$("#deal-btn").addEventListener("click", async () => {
  $("#deal-btn").disabled = true;
  try {
    if (lastStage === "showdown") {
      await fetch("/api/next_hand", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room, player_id: playerId }),
      });
    }
    const res = await fetch("/api/start_hand", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room, player_id: playerId }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || "Could not start hand.");
    }
  } finally {
    $("#deal-btn").disabled = false;
    fetchState();
  }
});

$all(".act").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const act = btn.dataset.act;
    const amount = act === "raise" ? parseInt($("#raise-slider").value, 10) : 0;
    btn.disabled = true;
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room, player_id: playerId, action: act, amount }),
      });
      if (!res.ok) {
        const err = await res.json();
        alert(err.detail || "Action failed.");
      }
    } finally {
      fetchState();
    }
  });
});

$("#raise-slider").addEventListener("input", (e) => {
  $("#raise-amount").textContent = e.target.value;
});

function render(state) {
  lastStage = state.stage;
  $("#hand-number").textContent = state.hand_number;

  // community cards
  const commEl = $("#community");
  commEl.innerHTML = "";
  const total = state.stage === "waiting" ? 0 : 5;
  for (let i = 0; i < total; i++) {
    commEl.appendChild(cardEl(state.community[i], !state.community[i]));
  }

  $("#pot").textContent = `Pot ${state.pot}`;

  const stageLabels = {
    waiting: "Waiting for players",
    preflop: "Pre-flop",
    flop: "Flop",
    turn: "Turn",
    river: "River",
    showdown: "Showdown",
  };
  $("#stage-label").textContent = stageLabels[state.stage] || state.stage;

  renderSeats(state);
  renderMyCards(state);
  renderActions(state);
  renderDealBar(state);
  renderLog(state);
}

function renderSeats(state) {
  const container = $("#seats");
  container.innerHTML = "";
  const players = state.players;
  const n = players.length;
  if (n === 0) return;

  let myIdx = players.findIndex((p) => p.id === playerId);
  if (myIdx === -1) myIdx = 0;

  const rx = 44, ry = 40; // percent radii
  for (let i = 0; i < n; i++) {
    const p = players[(myIdx + i) % n];
    const angleDeg = 90 + (i * 360) / n; // "me" (i=0) at bottom
    const rad = (angleDeg * Math.PI) / 180;
    const x = 50 + rx * Math.cos(rad);
    const y = 50 + ry * Math.sin(rad);

    const seat = document.createElement("div");
    seat.className = "seat";
    if (p.folded) seat.classList.add("folded");
    if (state.players.indexOf(p) === state.turn_idx && state.stage !== "waiting" && state.stage !== "showdown") {
      seat.classList.add("turn");
    }
    seat.style.left = `${x}%`;
    seat.style.top = `${y}%`;

    const nameRow = document.createElement("div");
    nameRow.className = "seat-name-row";
    nameRow.innerHTML = `<span>${escapeHtml(p.name)}${p.id === playerId ? " (you)" : ""}</span><span class="seat-chips">${p.chips}</span>`;
    seat.appendChild(nameRow);

    if (state.players.indexOf(p) === state.dealer_idx) {
      const btn = document.createElement("div");
      btn.className = "dealer-btn";
      btn.textContent = "D";
      nameRow.style.position = "relative";
      nameRow.appendChild(btn);
    }

    const winner = (state.winners_last_hand || []).find((w) => w.id === p.id);
    if (winner) {
      const badge = document.createElement("div");
      badge.className = "winner-badge";
      badge.textContent = winner.hand ? `won ${winner.amount} · ${winner.hand}` : `won ${winner.amount}`;
      nameRow.style.position = "relative";
      seat.style.position = "absolute";
      nameRow.appendChild(badge);
    }

    if (p.in_hand && p.hole && p.hole.length) {
      const cardsRow = document.createElement("div");
      cardsRow.className = "seat-cards";
      p.hole.forEach((c) => cardsRow.appendChild(cardEl(c, c === null)));
      seat.appendChild(cardsRow);
    }

    if (p.bet > 0) {
      const betEl = document.createElement("div");
      betEl.className = "seat-bet";
      betEl.textContent = p.bet;
      seat.appendChild(betEl);
    }

    container.appendChild(seat);
  }
}

function renderMyCards(state) {
  const me = state.players.find((p) => p.id === playerId);
  const holder = $("#my-hole");
  holder.innerHTML = "";
  if (me && me.in_hand && me.hole && me.hole.length && me.hole[0]) {
    me.hole.forEach((c) => holder.appendChild(cardEl(c)));
  }
}

function renderActions(state) {
  const valid = state.valid_actions || [];
  const actionsEl = $("#actions");
  const waitingMsg = $("#waiting-msg");
  const isMyTurn = valid.length > 0;
  $all(".act").forEach((btn) => { btn.disabled = false; });

  actionsEl.classList.toggle("hidden", !isMyTurn);
  waitingMsg.classList.toggle("hidden", isMyTurn);
  if (!isMyTurn) {
    if (state.stage === "waiting") waitingMsg.textContent = "Waiting for the host to deal.";
    else if (state.stage === "showdown") waitingMsg.textContent = "Hand's over — check the table.";
    else waitingMsg.textContent = "Waiting for other players…";
  }

  $all(".act").forEach((btn) => {
    btn.classList.toggle("hidden", !valid.includes(btn.dataset.act));
  });
  $(".raise-group").classList.toggle("hidden", !valid.includes("raise"));

  if (valid.includes("raise")) {
    const me = state.players.find((p) => p.id === playerId);
    const minRaiseTotal = state.to_call + state.min_raise;
    const maxRaiseTotal = me.chips - 1 > 0 ? me.chips - 1 : minRaiseTotal;
    const slider = $("#raise-slider");
    slider.min = Math.min(minRaiseTotal, maxRaiseTotal);
    slider.max = Math.max(minRaiseTotal, maxRaiseTotal);
    if (parseInt(slider.value, 10) < slider.min || !slider.value) slider.value = slider.min;
    $("#raise-amount").textContent = slider.value;
  }

  const callBtn = document.querySelector('.act.call');
  if (callBtn) callBtn.textContent = state.to_call ? `Call ${state.to_call}` : "Call";
}

function renderDealBar(state) {
  const bar = $("#deal-bar");
  const btn = $("#deal-btn");
  const eligible = state.players.filter((p) => !p.sitting_out && p.chips > 0).length;

  if (state.stage === "waiting") {
    bar.classList.remove("hidden");
    btn.textContent = "Deal next hand";
    btn.disabled = eligible < 2;
  } else if (state.stage === "showdown") {
    bar.classList.remove("hidden");
    btn.textContent = "Deal next hand";
    btn.disabled = eligible < 2;
  } else {
    bar.classList.add("hidden");
  }
}

function renderLog(state) {
  const body = $("#log-body");
  const wasAtBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 10;
  body.innerHTML = (state.log || []).map((l) => `<div>${escapeHtml(l)}</div>`).join("");
  if (wasAtBottom) body.scrollTop = body.scrollHeight;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
