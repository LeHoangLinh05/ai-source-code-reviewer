"""AI tool package for report generation."""

from app.ai.tools.common import tool_validation_error_observation
from app.ai.tools.generate_report import generate_final_report

AI_REPORT_TOOLS = [generate_final_report]

for ai_tool in AI_REPORT_TOOLS:
    ai_tool.handle_validation_error = tool_validation_error_observation

__all__ = [
    "AI_REPORT_TOOLS",
    "generate_final_report",
]
