"""JSON was previously excluded from _STRUCTURED_CATEGORIES -- an
uploaded JSON file got zero support from chat's text-to-SQL branch even
though json_parser.py already produces the same TableBlock shape
CSV/Excel do.
"""
from app.agents.domain_managers import _STRUCTURED_CATEGORIES
from app.models.schemas import FileCategory


def test_json_is_a_structured_category():
    assert FileCategory.JSON_ in _STRUCTURED_CATEGORIES
