"""query_router.py had trigger phrases for "csv file," "spreadsheet,"
"database," etc, but nothing for JSON -- "what's in the json file" fell
through to generic (unfiltered) matching only.
"""
from app.graph.query_router import infer_categories
from app.models.schemas import FileCategory


def test_json_file_mention_routes_to_json_category():
    result = infer_categories("what's in the json file")
    assert result is not None
    assert FileCategory.JSON_ in result


def test_json_data_phrase_routes_to_json_category():
    result = infer_categories("summarize the json data for me")
    assert result is not None
    assert FileCategory.JSON_ in result
