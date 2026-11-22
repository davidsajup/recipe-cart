# Recipe Cart

Dish name in, grocery cart out. Weekend FastAPI project.

## Run
```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # put your Gemini key in .env
docker compose up -d        # Postgres on localhost:5433
alembic upgrade head        # create the tables
python -m app.seed          # load seed/products.json (safe to re-run)
pytest                      # uses in-memory SQLite, no Postgres needed
uvicorn app.main:app --reload
```
The catalogue lives in Postgres (`products` table). Edit it at
http://127.0.0.1:8000/catalogue (add, edit, delete, search) or via the `/products`
API; the next cart sees changes immediately. Set `ADMIN_TOKEN` in `.env` to require
an `X-Admin-Token` header for writes (the page prompts for it) - do this before
exposing the app beyond localhost. `seed/products.json` is just the seed data. To use another
Postgres, set `DATABASE_URL` in `.env`.

Open http://127.0.0.1:8000 for the UI (API docs at /docs). "fish molee" works
without a key; other dishes are parsed by Gemini (`RECIPE_CART_MODEL` in `.env`
changes the model).

## Dish cache
Dishes parsed by Gemini are saved in the `parsed_dishes` table (keyed by the
lower-cased dish name), so asking for the same dish again is instant and free;
servings and pantry are applied after, so they never matter to the cache. Failed
parses are never saved. If a saved parse looks wrong, tick "Re-ask the AI" in the
UI (or send `"refresh": true` to `/cart`) to replace it.

Gemini calls are retried up to 3 times (1 s, then 3 s backoff) on overload,
rate-limit, server errors, timeouts and dropped connections; bad keys or model
names fail immediately.

## Instagram reels and pasted recipes
Paste a reel/post link (`instagram.com/reel/...`, `/p/...`) into the dish box. The
server reads the post's public caption and Gemini turns it into a cart, cached by
post id so repeats are instant. Instagram often serves a login wall to servers
instead of the caption; then the UI asks you to paste the caption (or any recipe
text) and saves it against that reel. We only read public caption metadata: no
video download, no scraping tricks. Recipe text is untrusted input, so it's
wrapped and the model is told to ignore instructions inside it. When a post gives
no quantities the model estimates them, so the UI flags reel/text carts as approximate.

## Video and audio (reels with no usable caption)
Many recipe reels put the recipe in speech or on-screen text, not the caption. Two ways in:

- **Upload the video** (always available): open "Paste recipe text or upload the video
  instead" in the UI, pick an .mp4/.mov/.webm (up to `MAX_VIDEO_MB`, default 100). Gemini
  reads the speech and on-screen text. The same file is cached by content hash, so a
  repeat upload is instant. Audio-only .m4a also works. Nothing is stored: the temp
  file is deleted, and so is any copy uploaded to Google's Files API.
- **Link download** (optional, OFF by default): set `VIDEO_DOWNLOAD=1`. If the caption has
  no recipe, the server downloads just the reel's audio with yt-dlp, transcribes it with
  Gemini and parses caption + transcript (cheap). If the audio has no recipe (silent reels
  with text overlays) and `VIDEO_FALLBACK=1`, it downloads the video (max 480p) and reads
  that instead. Any failure falls back to "paste the caption or upload the video".

**Read this before enabling the link download.** It is unofficial: Instagram's terms forbid
automated downloading, it frequently needs a logged-in session (this app never sends
cookies or handles Instagram credentials), and it breaks when Instagram changes. How often
it works for real reels is untested here. Only the rebuilt `instagram.com` post URL is
ever passed to yt-dlp, restricted to its Instagram extractor. Uploaded/downloaded media is
sent to Google Gemini; tell your users. Beta limits: 2 media jobs at once and
`VIDEO_MAX_PER_DAY` (default 30) per process, in memory only.

## Substitutions
When an ingredient isn't in the catalogue, `/cart` returns `suggestions`: stand-ins
from the curated table in `app/substitutions.py` (ghee -> butter, lime -> lemon,
toor dal -> moong/masoor/chana, ...). Only products you actually stock are offered,
priced for the amount needed; anything already in your pantry is listed first as free.
Add rules by editing that table.

## Next steps
1. Make substitutions editable from the catalogue page (they live in code for now).
2. Swap the static page in `app/static/` for Next.js/React if it outgrows one file.
