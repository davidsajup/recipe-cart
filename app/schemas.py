from typing import Literal

from pydantic import BaseModel, Field, field_validator

Unit = Literal["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "count"]


class Ingredient(BaseModel):
    name: str
    quantity: float = Field(gt=0)
    unit: Unit
    staple: bool = False  # salt, oil, etc. - skipped if in the pantry
    estimated: bool = False  # the source didn't state this amount; the model guessed it


class ParsedDish(BaseModel):
    # False when the source only talks ABOUT a dish (a teaser, a review, "recipe in the comments").
    # Defaults to True so recipes saved before this field existed still load.
    has_recipe: bool = True
    dish: str
    base_servings: int = Field(default=4, gt=0)
    ingredients: list[Ingredient]


class Product(BaseModel):
    name: str
    pack_size: float = Field(gt=0)
    unit: Literal["g", "ml", "count"]
    price: float


class CartItem(BaseModel):
    ingredient: str
    needed: str
    product: str
    packs: int
    price: float
    used_pct: int | None = None  # how much of the bought packs the recipe uses
    left_over: str | None = None  # e.g. "210 g" left in the packs


class CartRequest(BaseModel):
    dish: str
    servings: int | None = Field(default=None, gt=0)  # None: use the recipe's own serving count
    pantry: list[str] = []
    refresh: bool = False  # ignore the saved parse and ask the model again
    caption: str | None = Field(default=None, max_length=20000)  # pasted recipe text / reel caption
    # Rebuild from an already-parsed recipe (change servings, re-price) without re-reading
    # the source. Comes from a previous response's recipe_key.
    recipe_key: str | None = Field(default=None, max_length=200)


class Suggestion(BaseModel):
    ingredient: str  # the unmatched ingredient this stands in for
    product: str  # catalogue product (or the pantry item you already have)
    note: str
    in_pantry: bool = False
    packs: int = 0
    price: float = 0  # 0 when already in the pantry
    used_pct: int | None = None
    left_over: str | None = None


class UnmatchedItem(BaseModel):
    """An ingredient the catalogue can't supply, with what's needed to add it."""

    name: str
    needed: str  # e.g. "600.0 g"
    unit: Literal["g", "ml", "count"]
    suggested_pack_size: float


class Line(BaseModel):
    """One ingredient of the recipe, in recipe order, priced or not."""

    name: str
    need: str  # "500 g", "2 count": what the recipe needs, in a base unit
    need_amount: float
    need_unit: Literal["g", "ml", "count"]
    estimated: bool = False
    status: Literal["priced", "open"]  # open = the catalogue can't supply it
    in_pantry: bool = False  # the request said you already have it
    product: str | None = None
    packs: int = 0
    price: float = 0
    used_pct: int | None = None
    left_over: str | None = None
    suggested_pack_size: float | None = None  # for open lines: prefill for "add to catalogue"
    swaps: list[Suggestion] = []  # for open lines: stand-ins


class CartResponse(BaseModel):
    dish: str
    items: list[CartItem]
    skipped_pantry: list[str]
    unmatched: list[str]
    unmatched_items: list[UnmatchedItem] = []
    suggestions: list[Suggestion] = []
    source: Literal["dish", "instagram", "text", "video"] = "dish"
    lines: list[Line] = []
    recipe_key: str = ""  # send back as CartRequest.recipe_key to rebuild without re-reading
    servings: int = 4
    total: float


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    pack_size: float = Field(gt=0)
    unit: Literal["g", "ml", "count"]
    price: float = Field(ge=0)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class ProductOut(ProductIn):
    id: int
