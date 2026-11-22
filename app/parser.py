"""Dish -> structured ingredients.

Known dishes come from SAMPLES; anything else goes to an LLM that must return
JSON matching ParsedDish. Output is validated and retried once on failure.
"""
import json
import os
from typing import Callable

from pydantic import ValidationError

from .schemas import ParsedDish

DEFAULT_MODEL = "gemini-2.5-flash"

# Takes (system, user) prompts, returns the model's raw text.
Completer = Callable[[str, str], str]

SYSTEM = """You convert a dish name into a grocery ingredient list.
Reply with ONLY a JSON object, no prose or code fences, shaped like:
{"dish": str, "base_servings": int, "ingredients": [
  {"name": str, "quantity": number > 0,
   "unit": one of "g","kg","ml","l","tsp","tbsp","cup","count",
   "staple": bool}]}
Use simple generic ingredient names (e.g. "onion", "coconut milk").
Set staple=true for pantry basics such as salt, oil, sugar, common spices."""

SAMPLES = {
    "fish molee": {
        "dish": "fish molee",
        "base_servings": 4,
        "ingredients": [
            {"name": "fish", "quantity": 500, "unit": "g"},
            {"name": "coconut milk", "quantity": 400, "unit": "ml"},
            {"name": "onion", "quantity": 2, "unit": "count"},
            {"name": "green chilli", "quantity": 4, "unit": "count"},
            {"name": "ginger", "quantity": 30, "unit": "g"},
            {"name": "curry leaves", "quantity": 1, "unit": "count"},
            {"name": "coconut oil", "quantity": 3, "unit": "tbsp", "staple": True},
            {"name": "salt", "quantity": 1, "unit": "tsp", "staple": True},
        ],
    }
}


def gemini_complete(system: str, user: str) -> str:
    from google import genai  # lazy: tests/samples work without a key
    from google.genai import types

    # Pass the key explicitly: the SDK would otherwise prefer a stale GOOGLE_API_KEY.
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=30_000),  # ms; don't hang the UI
    )
    resp = client.models.generate_content(
        model=os.environ.get("RECIPE_CART_MODEL", DEFAULT_MODEL),
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
        ),
    )
    return resp.text or ""


def _extract_json(text: str) -> str:
    """Tolerate stray code fences / prose around the JSON object."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def parse_dish(dish: str, complete: Completer | None = None) -> ParsedDish:
    data = SAMPLES.get(dish.lower().strip())
    if data is not None:
        return ParsedDish.model_validate(data)

    complete = complete or gemini_complete
    user = f"Dish: {dish.strip()}"
    last_error: Exception | None = None
    for _ in range(2):  # first try + one retry
        raw = complete(SYSTEM, user)
        try:
            return ParsedDish.model_validate_json(_extract_json(raw))
        except (ValidationError, json.JSONDecodeError) as e:
            last_error = e
            user = (
                f"Dish: {dish.strip()}\n\nYour previous reply was invalid:\n{e}\n"
                "Return corrected JSON only."
            )
    raise ValueError(f"LLM returned invalid output for '{dish}': {last_error}")
