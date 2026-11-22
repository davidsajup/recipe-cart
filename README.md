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

## Substitutions
When an ingredient isn't in the catalogue, `/cart` returns `suggestions`: stand-ins
from the curated table in `app/substitutions.py` (ghee -> butter, lime -> lemon,
toor dal -> moong/masoor/chana, ...). Only products you actually stock are offered,
priced for the amount needed; anything already in your pantry is listed first as free.
Add rules by editing that table.

## Next steps
1. Make substitutions editable from the catalogue page (they live in code for now).
2. Swap the static page in `app/static/` for Next.js/React if it outgrows one file.
