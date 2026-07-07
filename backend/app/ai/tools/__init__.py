"""AI tool package for file reading, RAG search, and report generation."""

from app.ai.tools.analyze_structure import analyze_project_structure
from app.ai.tools.generate_issue import generate_issue
from app.ai.tools.generate_report import generate_final_report
from app.ai.tools.read_file import read_file_chunk
from app.ai.tools.search_rag import search_coding_standard

AI_REVIEW_TOOLS = [
    analyze_project_structure,
    read_file_chunk,
    search_coding_standard,
    generate_issue,
    generate_final_report,
]

__all__ = [
    "AI_REVIEW_TOOLS",
    "analyze_project_structure",
    "generate_final_report",
    "generate_issue",
    "read_file_chunk",
    "search_coding_standard",
]
