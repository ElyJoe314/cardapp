# Home Game — Texas Hold'em for you and your friends

A real-time-ish, no-download poker table you can host on Vercel. Python
(FastAPI) backend, plain HTML/CSS/JS frontend, no build step.

## How "real-time" works here

Vercel's Python functions are stateless serverless functions — no
WebSockets, no shared memory between requests. So instead of a live socket
connection, every player's browser polls `/api/state` once a second and the
UI updates from that. It feels real-time for a card game (nobody needs
sub-100ms latency to see a fold) and it's far simpler to deploy reliably.

Because state has to live somewhere between requests, game state is stored
in **Upstash Redis** (a free serverless Redis you connect straight from the
Vercel dashboard).

## 1. Set up Upstash Redis (2 minutes)

1. In your Vercel dashboard, go to your project → **Storage** → **Create
   Database** → choose **Upstash** → **Redis**.
2. Connect it to this project. Vercel will automatically add two
   environment variables to your project: `UPSTASH_REDIS_REST_URL` and
   `UPSTASH_REDIS_REST_TOKEN`. That's the only setup required — the code
   already reads those two variables (see `api/storage.py`).

If you skip this step, the app still runs, but each serverless invocation
may hit a different backend instance with no shared memory, so players
will see the table disappear or reset randomly. Don't skip it for real use.

## 2. Deploy

```bash
npm i -g vercel      # if you don't have the CLI
cd poker-app
vercel               # first deploy, follow prompts
vercel --prod        # deploy to your production URL
```

Or just push this folder to a GitHub repo and import it in the Vercel
dashboard — same result, and every push redeploys automatically.

## 3. Play

1. Open your deployed URL. One person clicks **Start a table**, sets the
   starting chip stack and blinds, and gets a 4-letter room code.
2. They tap **copy link** and send it to friends (or just read out the
   code) — everyone else uses **Join a table**.
3. Once 2+ people are seated, anyone can click **Deal next hand**. Turns,
   the pot, and the board update for everyone within about a second.

Player identity is just a random ID stored in each browser's
`localStorage` — there's no login. Closing the tab and reopening the same
link on the same device rejoins the same seat; opening it on a different
device joins as a new player.

## Local development

```bash
pip install -r requirements.txt fastapi uvicorn --break-system-packages
cd api && uvicorn index:app --reload --port 8000
```

Then open `public/index.html` with a simple static server (e.g. `npx
serve public`) pointed at that API — or just use `vercel dev` from the
project root, which serves both together the same way production does.
Without Upstash env vars set, storage falls back to an in-memory dict
(`api/storage.py`), which is fine for local testing but won't survive a
serverless cold start in production.

## Game notes

- Standard No-Limit Texas Hold'em: blinds, fold/check/call/raise/all-in,
  side pots for all-in situations, best-5-of-7 hand evaluation (via the
  `treys` library).
- Chip stacks persist hand-to-hand within a table. There's no re-buy
  button in the UI yet — a player at 0 chips just sits out until you add
  one (easy extension in `api/index.py` / `engine.py`).
- One table per room code. Nothing stops you from running several rooms
  at once for different games.

## Files

```
api/
  index.py      FastAPI routes (create/join/state/action/etc.)
  engine.py     Pure poker logic: dealing, betting rounds, side pots, showdown
  storage.py    Upstash Redis read/write (local dict fallback for dev)
public/
  index.html    Lobby + table markup
  style.css     Felt-table visual design
  app.js        Polling loop, rendering, action handling
vercel.json     Routes /api/* to the Python function, everything else static
requirements.txt
```
