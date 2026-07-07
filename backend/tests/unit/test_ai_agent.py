"""Tests for AI agent executor configuration."""

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.ai.agent import create_review_agent_executor


def test_agent_executor_hard_limit_is_twenty_iterations() -> None:
    executor = create_review_agent_executor(FakeListChatModel(responses=[]))

    assert executor.max_iterations == 20
