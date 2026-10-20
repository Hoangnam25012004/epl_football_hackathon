"""The Stats Tools catalog (design section 9.4): one definition for collector, agents and MCP."""

import json

import pytest
from helpers import run, view_at

from pundit.orchestration.tools import (
    COMPARE_STAT,
    GET_STAT,
    MAX_EVIDENCE_FOR_LLM,
    CompareStatArgs,
    GetStatArgs,
    catalog,
    maf_tools,
    run_compare_stat,
    run_get_stat,
)
from pundit.stats import REGISTRY


def test_catalog_lists_every_metric() -> None:
    entries = {e["name"]: e for e in catalog()}
    assert set(entries) == {GET_STAT, COMPARE_STAT}
    metric = entries[GET_STAT]["input_schema"]["properties"]["metric"]
    assert metric["enum"] == sorted(REGISTRY)


def test_get_stat_matches_the_registry() -> None:
    view = view_at()
    call = run_get_stat(
        view,
        GetStatArgs.model_validate(
            {
                "metric": "shots",
                "team": "harbor_lions",
                "window": {
                    "type": "second_half",
                    "minutes": None,
                    "start_minute": None,
                    "end_minute": None,
                },
            }
        ),
    )
    assert call.tool == "get_stat" and call.result.value == 15
    assert call.args == {
        "metric": "shots",
        "team": "harbor_lions",
        "window": call.result.window.model_dump(mode="json"),
    }


def test_compare_stat_returns_both_teams() -> None:
    home, away = run_compare_stat(view_at(), CompareStatArgs(metric="corners"))
    assert (home.result.team, away.result.team) == ("harbor_lions", "kestrel_athletic")


def test_maf_tools_truncate_evidence() -> None:
    tools = {t.name: t for t in maf_tools(view_at())}
    assert set(tools) == {GET_STAT, COMPARE_STAT}
    result = run(tools[GET_STAT].invoke(arguments={"metric": "passes", "team": "harbor_lions"}))
    payload = json.loads(result[0].text or "")
    assert payload["value"] == 553 and payload["display"] == "553"
    assert len(payload["evidence_event_ids"]) == MAX_EVIDENCE_FOR_LLM
    assert payload["evidence_total"] == 553
    both = run(tools[COMPARE_STAT].invoke(arguments={"metric": "corners"}))
    pair = json.loads(both[0].text or "")
    assert (pair["home"]["value"], pair["away"]["value"]) == (10, 4)


def test_bad_arguments_are_rejected() -> None:
    with pytest.raises(ValueError):
        GetStatArgs.model_validate({"metric": "shots", "colour": "red"})
    assert MAX_EVIDENCE_FOR_LLM == 10
