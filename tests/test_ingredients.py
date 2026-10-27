import pytest

from recipe_saver.parse.ingredients import parse_ingredient_line, to_number


@pytest.mark.parametrize("text,expected", [
    ("1/2", 0.5), ("1 1/2", 1.5), ("½", 0.5), ("1½", 1.5), ("1.8", 1.8), ("75/25", None),
])
def test_to_number(text, expected):
    assert to_number(text) == expected


@pytest.mark.parametrize("line,quantity,quantity_max,unit,item,note", [
    ("200g flour", 200, None, "g", "flour", None),
    ("85g (6 tbsp) butter", 85, None, "g", "butter", "6 tbsp"),
    ("1.8KG Boneless Chicken Breast (raw weight)", 1.8, None, "kg", "Boneless Chicken Breast", "raw weight"),
    ("300-400g pasta", 300, 400, "g", "pasta", None),
    ("1-2 handfuls fresh basil", 1, 2, "handful", "fresh basil", None),
    ("-1/2 cup fresh Parmesan cheese, grated", 0.5, None, "cup", "fresh Parmesan cheese", "grated"),
    ("1 cup of cream", 1, None, "cup", "cream", None),
    ("1tsp red chilli flakes", 1, None, "tsp", "red chilli flakes", None),
    ("2 garlic cloves", 2, None, None, "garlic cloves", None),
    ("75/25 or 80/20 ground beef", None, None, None, "75/25 or 80/20 ground beef", None),
    ("Onion(thinly sliced)", None, None, None, "Onion", "thinly sliced"),
])
def test_parse_ingredient_line(line, quantity, quantity_max, unit, item, note):
    ing = parse_ingredient_line(line)
    assert (ing.quantity, ing.quantity_max, ing.unit, ing.item, ing.note) == (
        quantity, quantity_max, unit, item, note)
