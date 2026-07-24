"""Tests for AI review orchestration helpers."""

from app.ai.roadmap.catalog_service import (
    ROADMAP_RULE_CATALOG_TOOL_NAME,
    _loaded_roadmap_rule_ids,
)


def test_loaded_roadmap_rule_ids_include_catalog_trace() -> None:
    documents: list[object] = [
        {
            "tool_name": ROADMAP_RULE_CATALOG_TOOL_NAME,
            "output": {
                "status": "ok",
                "results": [
                    {
                        "metadata": {
                            "doc_type": "roadmap_rule",
                            "rule_id": "RC-W1-10",
                        }
                    },
                    {
                        "metadata": {
                            "doc_type": "roadmap_rule",
                            "rule_id": "RC-W1-11",
                        }
                    },
                ],
            },
        }
    ]

    assert _loaded_roadmap_rule_ids(documents) == {"RC-W1-10", "RC-W1-11"}
