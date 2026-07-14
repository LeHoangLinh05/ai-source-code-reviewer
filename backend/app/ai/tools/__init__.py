"""AI tool package for file reading, RAG search, and report generation."""

from app.ai.tools.analyze_structure import analyze_project_structure
from app.ai.tools.common import tool_validation_error_observation
from app.ai.tools.generate_issue import generate_issue
from app.ai.tools.generate_report import generate_final_report
from app.ai.tools.read_file import read_file_chunk
from app.ai.tools.search_code import search_code, search_code_semantic
from app.ai.tools.search_knowledge import search_knowledge_base

AI_REVIEW_TOOLS = [
    analyze_project_structure,
    read_file_chunk,
    search_code,
    search_knowledge_base,
    generate_issue,
]
AI_REPORT_TOOLS = [generate_final_report]

for ai_tool in [*AI_REVIEW_TOOLS, *AI_REPORT_TOOLS]:
    ai_tool.handle_validation_error = tool_validation_error_observation

__all__ = [
    "AI_REPORT_TOOLS",
    "AI_REVIEW_TOOLS",
    "analyze_project_structure",
    "generate_final_report",
    "generate_issue",
    "read_file_chunk",
    "search_code",
    "search_code_semantic",
    "search_knowledge_base",
]
