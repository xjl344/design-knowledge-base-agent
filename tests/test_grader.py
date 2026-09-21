import pytest

from src.grader import parse_relevant_ids


def test_relevance_parser_filters_unknown_document_ids():
    result = parse_relevant_ids(
        '{"relevant_ids":["D1","D99"]}', {"D1", "D2"}
    )

    assert result == {"D1"}


def test_relevance_parser_rejects_non_json_output():
    with pytest.raises(ValueError):
        parse_relevant_ids("yes", {"D1"})

