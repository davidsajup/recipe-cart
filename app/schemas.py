from typing import Literal

from pydantic import BaseModel, Field, field_validator

Unit = Literal["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "count"]


class Ingredient(BaseModel):
    name: str
    quantity: float = Field(gt=0)
    unit: Unit
    staple: bool = False  # salt, oil, etc. - skipped if in the pantry


class ParsedDish(BaseModel):
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


class CartRequest(BaseModel):
    dish: str
    servings: int = Field(default=4, gt=0)
    pantry: list[str] = []
    refresh: bool = False  # ignore the saved parse and ask the model again
    caption: str | None = Field(default=None, max_length=20000)  # pasted recipe text / reel caption


class Suggestion(BaseModel):
    ingredient: str  # the unmatched ingredient this stands in for
    product: str  # catalogue product (or the pantry item you already have)
    note: str
    in_pantry: bool = False
    packs: int = 0
    price: float = 0  # 0 when already in the pantry


class UnmatchedItem(BaseModel):
    """An ingredient the catalogue can't supply, with what's needed to add it."""

    name: str
    needed: str  # e.g. "600.0 g"
    unit: Literal["g", "ml", "count"]
    suggested_pack_size: float


class CartResponse(BaseModel):
    dish: str
    items: list[CartItem]
    skipped_pantry: list[str]
    unmatched: list[str]
    unmatched_items: list[UnmatchedItem] = []
    suggestions: list[Suggestion] = []
    source: Literal["dish", "instagram", "text", "video"] = "dish"
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
