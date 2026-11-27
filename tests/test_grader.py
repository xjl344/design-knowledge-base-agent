import pytest

from src.grader import parse_grading_result, parse_relevant_ids


def test_relevance_parser_filters_unknown_document_ids():
    result = parse_relevant_ids(
        '{"relevant_ids":["D1","D99"]}', {"D1", "D2"}
    )

    assert result == {"D1"}


def test_relevance_parser_rejects_non_json_output():
    with pytest.raises(ValueError):
        parse_relevant_ids("yes", {"D1"})


def test_grading_parser_extracts_sufficiency_and_search_query():
    ids, coverage, missing, query = parse_grading_result(
        '{"relevant_ids":["D1"],"coverage":"partial","missing_aspects":"缺少完整步骤","search_query":"钣金加工 工艺流程"}',
        {"D1", "D2"},
    )
    assert ids == {"D1"}
    assert coverage == "partial"
    assert missing == "缺少完整步骤"
    assert query == "钣金加工 工艺流程"

    # Empty relevant_ids forces insufficient even if model wrote sufficient.
    ids2, coverage2, _, _ = parse_grading_result(
        '{"relevant_ids":[],"coverage":"sufficient","missing_aspects":"","search_query":"MIM 工艺"}',
        {"D1"},
    )
    assert ids2 == set()
    assert coverage2 == "insufficient"

