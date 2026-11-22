"""Curated substitutions: ingredient -> [(substitute, advice)].

Substitute names are matched against the catalogue with the normal matcher, so
only products you actually stock are suggested. Order = preference.
"""

SUBSTITUTES: dict[str, list[tuple[str, str]]] = {
    # fats
    "ghee": [("butter", "Use butter 1:1; it browns sooner, so cook on lower heat"),
             ("vegetable oil", "Use oil 1:1 for cooking; you lose the ghee flavour")],
    "butter": [("ghee", "Use ghee 1:1"), ("vegetable oil", "Use oil, a little less than the butter")],
    "coconut oil": [("vegetable oil", "Neutral oil 1:1; less coconut flavour"),
                    ("ghee", "Ghee 1:1 for a richer taste")],
    "vegetable oil": [("coconut oil", "Coconut oil 1:1; adds a coconut note")],
    "cooking oil": [("vegetable oil", "Any neutral oil works"), ("coconut oil", "Adds a coconut note")],
    # dairy
    "coconut milk": [("milk", "Milk 1:1 for a lighter, less coconutty gravy"),
                     ("curd", "Whisked curd for tang; add off the heat so it doesn't split")],
    "milk": [("curd", "Thin curd with water, 1:1")],
    "curd": [("milk", "Milk plus a squeeze of lemon, 1:1")],
    "cream": [("milk", "Milk plus a little butter"), ("butter", "Stir in at the end for richness")],
    "paneer": [("cheese", "Firm cheese cubes, 1:1")],
    "cheese": [("paneer", "Paneer 1:1; milder and doesn't melt")],
    # souring
    "lemon": [("tamarind", "About 1/2 tsp tamarind paste per lemon")],
    "lime": [("lemon", "Lemon 1:1")],
    "tamarind": [("lemon", "Juice of 1 lemon per tbsp tamarind")],
    # pulses
    "toor dal": [("moong dal", "Cooks faster; reduce simmer time"),
                 ("masoor dal", "Cooks faster and goes creamy"),
                 ("chana dal", "Soak first and cook longer")],
    "moong dal": [("toor dal", "Cook longer"), ("masoor dal", "Similar cooking time")],
    "masoor dal": [("moong dal", "Similar cooking time"), ("toor dal", "Cook longer")],
    "chana dal": [("toor dal", "Cooks faster")],
    # spices / heat
    "green chilli": [("red chilli powder", "About 1/4 tsp powder per chilli"),
                     ("black pepper", "Milder heat; use less")],
    "red chilli powder": [("green chilli", "Chop 1 green chilli per 1/2 tsp powder"),
                          ("black pepper", "Milder; use half")],
    "black pepper": [("red chilli powder", "Hotter; use a pinch")],
    "cumin": [("coriander powder", "Less earthy; use a little more")],
    "cinnamon": [("cardamom", "A pinch, for warmth")],
    "bay leaf": [("curry leaves", "A few curry leaves for aroma")],
    "curry leaves": [("bay leaf", "1-2 bay leaves; different but aromatic")],
    # herbs
    "coriander leaves": [("mint leaves", "Different flavour; good in chutneys and raitas")],
    "mint leaves": [("coriander leaves", "Coriander 1:1")],
    # proteins
    "fish": [("prawns", "Prawns cook faster; 4-5 minutes"), ("chicken curry cut", "Needs a longer cook")],
    "prawns": [("fish", "Firm fish pieces, 1:1")],
    "chicken": [("paneer", "Paneer 1:1 for a vegetarian version")],
    # misc
    "almonds": [("cashew nuts", "Cashews 1:1")],
    "peanuts": [("cashew nuts", "Cashews 1:1")],
    "jaggery": [("sugar", "Sugar 1:1")],
    "brown sugar": [("sugar", "White sugar 1:1")],
    "shallots": [("onion", "Onion, about 1:1 by weight")],
    "spring onion": [("onion", "Onion; use less and chop fine")],
    "eggplant": [("potato", "Potato cubes, 1:1")],
}
