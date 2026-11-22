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

## Providers (Swiggy Instamart adapter)
`app/providers/` lets the cart be priced from a live grocery provider instead of the built-in
catalogue. **Real Instamart access is not connected**: it needs approval from Swiggy and each
customer signing in with their own Swiggy account (OAuth 2.1 + PKCE), which needs user accounts,
token storage and HTTPS first. What exists today is the adapter plus a **demo mode** (below) that
runs the whole customer flow against a simulated Instamart.

- `base.py` provider-neutral `Offer` / `Location` / `Provider`; `quantity.py` reads pack sizes
  from free text ("500 g", "2 x 250 g", "Pack of 6", "1 L"); unreadable sizes are shown to the
  customer but never priced on a guess.
- `instamart.py` JSON-RPC client for Swiggy's MCP endpoint, written from their builder docs:
  results from `structuredContent`; errors mapped (401 -> sign in again, 400, 429 with
  Retry-After, 5xx, `success:false`); retries 500 ms doubling, 5 attempts, 30 s budget; **placing
  an order is never auto-retried**; the token and arguments are never logged. Addresses, search
  and cart building, plus **guarded** checkout and UPI payment: `checkout` refuses unless the
  customer explicitly confirmed, re-reads the cart and refuses if the amount they saw changed,
  and is never retried; the UPI poll confirms the order exactly once.
- `cart.py` builds the same `CartResponse` as the catalogue (same pack gauges, leftovers and
  unmatched handling), one search per distinct ingredient, respecting the provider's own ranking,
  with priced alternatives per line. `selections_from_cart` turns lines into cart items.
- `own.py` wraps the built-in catalogue as a provider (a test proves it prices identically).
- `fake_instamart.py` an offline stand-in built from the documented shapes. **Its products and
  size strings are invented**: Swiggy's docs publish no sample responses, so capture real ones
  from staging before trusting the pack-size parser.

### Demo mode (simulated Instamart)
Set `DEMO_INSTAMART=1` and restart. The start screen gets a **Prices from** switch
(Our catalogue | Instamart demo). With Instamart selected you get the full customer flow, so you
can record the demo video Swiggy asks for with their application:
suggested product per ingredient with a **Change** picker (other sizes and brands, including ones
whose size we couldn't read and that you must check yourself), a **Review order** screen
(items and prices as the provider sees them, bill, delivery address, UPI or cash, and the sentence
"Your order will be delivered to ... You'll pay ... by ..."), a **Place order** button, UPI
polling and an order-placed screen. "Powered by Instamart" appears on every screen and a "Demo"
banner makes clear nothing is real. The demo shares ONE in-memory cart and order list across the
whole server, so use it for a single presenter only. No real order is ever placed. The brand
mark is a placeholder; use Swiggy's official logo asset per their brand guidelines before launch.

Set `SWIGGY_MCP_BASE_URL` to point at another endpoint (default: staging, `/im`). Swiggy's rules
that shape the product: show "Powered by Instamart" with every result and cart, let the customer
choose variants and confirm before any order, don't scrape the catalogue, don't persist personal
data longer than needed.

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

## The app ("mise en place")
Open http://127.0.0.1:8000. Paste a reel link or name a dish, then **check what you have**:

- Every ingredient is a line in recipe order with a **Buy | Have** toggle. Tapping Have drops it
  out of the cart and the total updates instantly. What you have is remembered in your browser
  (localStorage) for next time; "Forget" clears it.
- Quantities the source didn't state are marked in **yellow** (the model flags them as
  `estimated`). Dish-name recipes are typical amounts and aren't marked.
- Each priced line has a **pack gauge**: how much of the bought pack(s) the recipe uses and
  what's left over (grams and millilitres are treated as interchangeable, as in the matcher).
- Ingredients the catalogue can't supply stay in place with a dashed outline. **Price it** opens a
  prefilled form; **Swap for** suggests stand-ins you stock.
- The servings stepper re-prices instantly from the saved recipe (no new AI call). A new recipe
  starts at its own serving count.
- **Copy shopping list** puts a plain-text list on the clipboard (items you have are left out).
- Phone layout: cards, thumb-sized controls and a pinned total bar.

API additions behind it (the older `items` / `unmatched` / `suggestions` fields are unchanged):
`/cart` returns `lines` (one per ingredient, recipe order: `status` priced|open, `estimated`,
`used_pct`, `left_over`, `swaps`, ...), `servings` and a `recipe_key`. Send that key back as
`recipe_key` to re-price without re-reading the source; `servings` is optional.

The front end is plain JS + CSS in `app/static/` (`app.js`, `styles.css`); fonts load from Google
Fonts at runtime.

## Adding missing ingredients
Ingredients the catalogue can't supply stay in the list with a **Price it** button. It opens a
form prefilled with a name, a realistic pack size (the smallest usual shelf size that covers the
recipe) and the unit; you enter the price, and the cart rebuilds with the new product included
(from the saved recipe, no new AI call). If `ADMIN_TOKEN` is set, the page asks for it once per
browser session.

## Substitutions
When an ingredient isn't in the catalogue, `/cart` returns `suggestions`: stand-ins
from the curated table in `app/substitutions.py` (ghee -> butter, lime -> lemon,
toor dal -> moong/masoor/chana, ...). Only products you actually stock are offered,
priced for the amount needed; anything already in your pantry is listed first as free.
Add rules by editing that table.

## Next steps
1. Make substitutions editable from the catalogue page (they live in code for now).
2. Swap the static page in `app/static/` for Next.js/React if it outgrows one file.
