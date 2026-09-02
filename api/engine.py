"""
Texas Hold'em engine. Game state is a plain JSON-serializable dict so it can be
stored whole in Redis between requests (Vercel functions are stateless).
"""
import random
import time
from treys import Card, Evaluator, Deck

EVALUATOR = Evaluator()

STAGES = ["waiting", "preflop", "flop", "turn", "river", "showdown"]


def new_room(room_code, small_blind=5, big_blind=10, starting_chips=1000):
    return {
        "room": room_code,
        "players": [],          # list of player dicts, in seating order
        "deck": [],              # list of treys card ints remaining to deal
        "community": [],
        "pot": 0,
        "stage": "waiting",
        "dealer_idx": -1,        # will advance to 0 on first hand
        "turn_idx": None,
        "current_bet": 0,
        "min_raise": big_blind,
        "small_blind": small_blind,
        "big_blind": big_blind,
        "hand_number": 0,
        "log": [],
        "last_aggressor_idx": None,
        "winners_last_hand": [],
        "updated_at": time.time(),
    }


def _new_player(pid, name, chips):
    return {
        "id": pid,
        "name": name,
        "chips": chips,
        "hole": [],           # two treys card ints while hand is active
        "bet": 0,             # chips put in during current betting round
        "total_bet": 0,       # chips put in during whole hand (for side pots)
        "folded": False,
        "all_in": False,
        "in_hand": False,     # dealt into the current hand
        "has_acted": False,   # acted since last bet/raise, for round-end check
        "connected": True,
        "sitting_out": False,
    }


def log(state, msg):
    state["log"].append(msg)
    state["log"] = state["log"][-60:]


def add_player(state, pid, name, chips=None):
    if any(p["id"] == pid for p in state["players"]):
        return
    if chips is None:
        chips = 1000
    state["players"].append(_new_player(pid, name, chips))
    log(state, f"{name} joined the table.")


def remove_player(state, pid):
    p = next((p for p in state["players"] if p["id"] == pid), None)
    if not p:
        return
    if state["stage"] != "waiting" and p["in_hand"] and not p["folded"]:
        p["folded"] = True
        p["sitting_out"] = True
        log(state, f"{p['name']} left and folded.")
        _maybe_end_hand_by_folds(state)
    else:
        state["players"] = [x for x in state["players"] if x["id"] != pid]
        log(state, f"{p['name']} left the table.")


def _active_players(state):
    """Players still seated with chips, eligible for next hand."""
    return [p for p in state["players"] if not p.get("sitting_out")]


def _players_in_hand(state):
    return [p for p in state["players"] if p["in_hand"]]


def _contenders(state):
    """Players still in hand and not folded."""
    return [p for p in state["players"] if p["in_hand"] and not p["folded"]]


def can_start_hand(state):
    eligible = [p for p in _active_players(state) if p["chips"] > 0]
    return state["stage"] == "waiting" and len(eligible) >= 2


def start_hand(state):
    if not can_start_hand(state):
        raise ValueError("Need at least 2 players with chips to start.")

    eligible = [p for p in _active_players(state) if p["chips"] > 0]
    for p in state["players"]:
        p["in_hand"] = p in eligible
        p["hole"] = []
        p["bet"] = 0
        p["total_bet"] = 0
        p["folded"] = not p["in_hand"]
        p["all_in"] = False
        p["has_acted"] = False

    state["community"] = []
    state["pot"] = 0
    state["hand_number"] += 1
    state["winners_last_hand"] = []

    deck = Deck()
    state["deck"] = deck.cards  # list of ints, already shuffled

    n = len(eligible)
    state["dealer_idx"] = (state["dealer_idx"] + 1) % n if state["dealer_idx"] >= 0 else 0
    # map dealer_idx within `eligible` back to index in state["players"]
    order = [state["players"].index(p) for p in eligible]
    dealer_player_idx = order[state["dealer_idx"] % n]

    # deal hole cards, 2 rounds, starting left of dealer
    for _ in range(2):
        for i in range(n):
            idx = order[(state["dealer_idx"] + 1 + i) % n]
            state["players"][idx]["hole"].append(state["deck"].pop())

    sb_idx = order[(state["dealer_idx"] + 1) % n] if n > 2 else order[(state["dealer_idx"] + 1) % n]
    bb_idx = order[(state["dealer_idx"] + 2) % n] if n > 2 else order[state["dealer_idx"]]
    if n == 2:
        # heads-up: dealer posts small blind, other player posts big blind
        sb_idx = dealer_player_idx
        bb_idx = order[(state["dealer_idx"] + 1) % n]

    _post_blind(state, sb_idx, state["small_blind"])
    _post_blind(state, bb_idx, state["big_blind"])

    state["current_bet"] = state["big_blind"]
    state["min_raise"] = state["big_blind"]
    state["last_aggressor_idx"] = bb_idx
    state["stage"] = "preflop"

    first_to_act = order[(order.index(bb_idx) + 1) % n] if n > 2 else sb_idx
    # simpler: first to act is player after big blind, wrapping
    bb_pos = order.index(bb_idx)
    first_to_act = order[(bb_pos + 1) % n]
    state["turn_idx"] = first_to_act

    log(state, f"--- Hand #{state['hand_number']} --- blinds {state['small_blind']}/{state['big_blind']}")
    _skip_to_actionable(state)
    return state


def _post_blind(state, idx, amount):
    p = state["players"][idx]
    amt = min(amount, p["chips"])
    p["chips"] -= amt
    p["bet"] += amt
    p["total_bet"] += amt
    state["pot"] += amt
    if p["chips"] == 0:
        p["all_in"] = True
    log(state, f"{p['name']} posts {'small' if amount==state['small_blind'] else 'big'} blind {amt}")


def _order_from_players(state):
    return list(range(len(state["players"])))


def get_player(state, pid):
    return next((p for p in state["players"] if p["id"] == pid), None)


def valid_actions(state, pid):
    if state["stage"] in ("waiting", "showdown"):
        return []
    idx = state["players"].index(get_player(state, pid)) if get_player(state, pid) else -1
    if idx != state["turn_idx"]:
        return []
    p = state["players"][idx]
    if p["folded"] or p["all_in"]:
        return []
    to_call = state["current_bet"] - p["bet"]
    actions = ["fold"]
    if to_call <= 0:
        actions.append("check")
    else:
        actions.append("call")
    if p["chips"] > to_call:
        actions.append("raise")
    actions.append("all_in")
    return actions


def apply_action(state, pid, action, amount=0):
    p = get_player(state, pid)
    if not p:
        raise ValueError("Player not in game.")
    idx = state["players"].index(p)
    if idx != state["turn_idx"]:
        raise ValueError("Not your turn.")
    if state["stage"] in ("waiting", "showdown"):
        raise ValueError("No active betting round.")

    to_call = state["current_bet"] - p["bet"]

    if action == "fold":
        p["folded"] = True
        log(state, f"{p['name']} folds.")
    elif action == "check":
        if to_call > 0:
            raise ValueError("Cannot check, must call or fold.")
        log(state, f"{p['name']} checks.")
    elif action == "call":
        amt = min(to_call, p["chips"])
        p["chips"] -= amt
        p["bet"] += amt
        p["total_bet"] += amt
        state["pot"] += amt
        if p["chips"] == 0:
            p["all_in"] = True
        log(state, f"{p['name']} calls {amt}.")
    elif action == "all_in":
        amt = p["chips"]
        p["chips"] = 0
        p["bet"] += amt
        p["total_bet"] += amt
        state["pot"] += amt
        p["all_in"] = True
        if p["bet"] > state["current_bet"]:
            raise_amt = p["bet"] - state["current_bet"]
            state["min_raise"] = max(state["min_raise"], raise_amt)
            state["current_bet"] = p["bet"]
            state["last_aggressor_idx"] = idx
            for other in state["players"]:
                if other is not p and other["in_hand"] and not other["folded"] and not other["all_in"]:
                    other["has_acted"] = False
        log(state, f"{p['name']} goes all-in for {amt}.")
    elif action == "raise":
        amount = int(amount)
        total_needed = to_call + amount
        if amount < state["min_raise"]:
            raise ValueError(f"Raise must be at least {state['min_raise']}.")
        if total_needed >= p["chips"]:
            raise ValueError("Not enough chips for that raise; use all_in instead.")
        p["chips"] -= total_needed
        p["bet"] += total_needed
        p["total_bet"] += total_needed
        state["pot"] += total_needed
        state["current_bet"] = p["bet"]
        state["min_raise"] = amount
        state["last_aggressor_idx"] = idx
        for other in state["players"]:
            if other is not p and other["in_hand"] and not other["folded"] and not other["all_in"]:
                other["has_acted"] = False
        log(state, f"{p['name']} raises to {p['bet']}.")
    else:
        raise ValueError("Unknown action.")

    p["has_acted"] = True
    _maybe_end_hand_by_folds(state)
    if state["stage"] != "showdown":
        _advance_turn(state)
    state["updated_at"] = time.time()
    return state


def _maybe_end_hand_by_folds(state):
    contenders = _contenders(state)
    if len(contenders) == 1:
        winner = contenders[0]
        winner["chips"] += state["pot"]
        state["winners_last_hand"] = [{"id": winner["id"], "name": winner["name"], "amount": state["pot"], "hand": None}]
        log(state, f"{winner['name']} wins {state['pot']} (everyone else folded).")
        state["pot"] = 0
        state["stage"] = "showdown"
        for p in state["players"]:
            p["in_hand"] = False


def _betting_round_over(state):
    contenders = [p for p in state["players"] if p["in_hand"] and not p["folded"]]
    still_to_act = [p for p in contenders if not p["all_in"] and not p["has_acted"]]
    if still_to_act:
        return False
    bets = set(p["bet"] for p in contenders if not p["all_in"])
    return len(bets) <= 1


def _advance_turn(state):
    if state["stage"] == "showdown":
        return
    if _betting_round_over(state):
        _advance_stage(state)
        return
    n = len(state["players"])
    idx = state["turn_idx"]
    for _ in range(n):
        idx = (idx + 1) % n
        p = state["players"][idx]
        if p["in_hand"] and not p["folded"] and not p["all_in"]:
            state["turn_idx"] = idx
            return
    _advance_stage(state)


def _skip_to_actionable(state):
    n = len(state["players"])
    idx = state["turn_idx"]
    for _ in range(n):
        p = state["players"][idx]
        if p["in_hand"] and not p["folded"] and not p["all_in"]:
            state["turn_idx"] = idx
            return
        idx = (idx + 1) % n
    _advance_stage(state)


def _advance_stage(state):
    for p in state["players"]:
        p["bet"] = 0
        p["has_acted"] = False
    state["current_bet"] = 0
    state["min_raise"] = state["big_blind"]

    contenders = _contenders(state)
    active_bettors = [p for p in contenders if not p["all_in"]]

    if state["stage"] == "preflop":
        state["deck"].pop()  # burn
        state["community"] += [state["deck"].pop() for _ in range(3)]
        state["stage"] = "flop"
        log(state, f"Flop: {_cards_str(state['community'])}")
    elif state["stage"] == "flop":
        state["deck"].pop()
        state["community"].append(state["deck"].pop())
        state["stage"] = "turn"
        log(state, f"Turn: {_cards_str(state['community'])}")
    elif state["stage"] == "turn":
        state["deck"].pop()
        state["community"].append(state["deck"].pop())
        state["stage"] = "river"
        log(state, f"River: {_cards_str(state['community'])}")
    elif state["stage"] == "river":
        _showdown(state)
        return
    else:
        return

    if len(active_bettors) <= 1:
        # everyone (or all but one) is all-in; auto-deal remaining streets
        _advance_stage(state)
        return

    n = len(state["players"])
    # first to act post-flop = first active player left of dealer
    dealer_player_idx = _current_dealer_player_idx(state)
    idx = dealer_player_idx
    for _ in range(n):
        idx = (idx + 1) % n
        p = state["players"][idx]
        if p["in_hand"] and not p["folded"] and not p["all_in"]:
            state["turn_idx"] = idx
            return
    _advance_stage(state)


def _current_dealer_player_idx(state):
    eligible = [p for p in state["players"] if p["in_hand"]]
    if not eligible:
        return 0
    order = [state["players"].index(p) for p in state["players"] if p["in_hand"]]
    n = len(order)
    return order[state["dealer_idx"] % n]


def _cards_str(cards):
    return " ".join(Card.int_to_str(c) for c in cards)


def _showdown(state):
    contenders = _contenders(state)
    community = state["community"]
    # Deal remaining board cards if hand ended early via all-ins (already handled by advance_stage loop)
    scored = []
    for p in contenders:
        score = EVALUATOR.evaluate(community, p["hole"])
        rank_class = EVALUATOR.get_rank_class(score)
        class_str = EVALUATOR.class_to_string(rank_class)
        scored.append((score, p, class_str))
    scored.sort(key=lambda x: x[0])  # lower score = better hand in treys

    # Side pots: build list of (amount, eligible_player_ids) based on total_bet contributions
    all_players_in_hand = [p for p in state["players"] if p["in_hand"]]
    contributions = sorted(set(p["total_bet"] for p in all_players_in_hand if p["total_bet"] > 0))
    pots = []
    prev = 0
    for level in contributions:
        layer = level - prev
        eligible_ids = [p["id"] for p in all_players_in_hand if p["total_bet"] >= level and not p["folded"]]
        amount = 0
        for p in all_players_in_hand:
            amount += min(max(p["total_bet"] - prev, 0), layer)
        if amount > 0 and eligible_ids:
            pots.append({"amount": amount, "eligible": eligible_ids})
        prev = level

    winners_summary = {}
    for pot in pots:
        eligible_scored = [s for s in scored if s[1]["id"] in pot["eligible"]]
        if not eligible_scored:
            continue
        best_score = eligible_scored[0][0]
        winners = [s for s in eligible_scored if s[0] == best_score]
        share = pot["amount"] // len(winners)
        remainder = pot["amount"] - share * len(winners)
        for i, (score, p, class_str) in enumerate(winners):
            amt = share + (remainder if i == 0 else 0)
            p["chips"] += amt
            entry = winners_summary.setdefault(p["id"], {"id": p["id"], "name": p["name"], "amount": 0, "hand": class_str})
            entry["amount"] += amt

    state["winners_last_hand"] = list(winners_summary.values())
    state["pot"] = 0
    state["stage"] = "showdown"
    for p in state["players"]:
        p["in_hand"] = p["in_hand"]  # keep hole cards visible for showdown display
    for score, p, class_str in scored:
        log(state, f"{p['name']} shows {_cards_str(p['hole'])} ({class_str})")
    for w in state["winners_last_hand"]:
        log(state, f"{w['name']} wins {w['amount']} with {w['hand']}")


def reset_to_waiting(state):
    """Call after showdown once players are ready for the next hand."""
    state["stage"] = "waiting"
    for p in state["players"]:
        p["in_hand"] = False
        p["hole"] = []
        p["folded"] = False
        p["all_in"] = False
        p["bet"] = 0
        p["total_bet"] = 0
    state["community"] = []
    state["turn_idx"] = None


def public_state(state, viewer_id=None):
    """Strip hole cards of other players before sending to a client."""
    out = {k: v for k, v in state.items() if k != "deck"}
    out["players"] = []
    for p in state["players"]:
        pp = {k: v for k, v in p.items() if k != "hole"}
        reveal = (
            p["id"] == viewer_id
            or state["stage"] == "showdown"
            and p["in_hand"]
            and not p["folded"]
        )
        pp["hole"] = [Card.int_to_str(c) for c in p["hole"]] if reveal and p["hole"] else ([None, None] if p["in_hand"] else [])
        out["players"].append(pp)
    out["community"] = [Card.int_to_str(c) for c in state["community"]]
    out["valid_actions"] = valid_actions(state, viewer_id) if viewer_id else []
    out["to_call"] = 0
    if viewer_id:
        p = get_player(state, viewer_id)
        if p:
            out["to_call"] = max(0, state["current_bet"] - p["bet"])
    return out
