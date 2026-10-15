"""Report renderers for CI exports and humans: JSON, JUnit XML, Markdown."""

from __future__ import annotations

from collections.abc import Callable

from mailsim.domain.errors import InvalidRequest
from mailsim.evaluation.contract import EvaluationReport
from mailsim.evaluation.reports.json_report import render_json
from mailsim.evaluation.reports.junit import render_junit
from mailsim.evaluation.reports.markdown import render_markdown

RENDERERS: dict[str, Callable[[EvaluationReport], bytes]] = {
    "json": render_json,
    "junit": render_junit,
    "markdown": render_markdown,
}
FORMATS = tuple(RENDERERS)
CONTENT_TYPES = {
    "json": "application/json",
    "junit": "application/xml",
    "markdown": "text/markdown; charset=utf-8",
}


def render(report: EvaluationReport, fmt: str) -> bytes:
    try:
        renderer = RENDERERS[fmt]
    except KeyError:
        raise InvalidRequest(f"unknown report format {fmt!r}; use one of {list(FORMATS)}") from None
    return renderer(report)


__all__ = ["CONTENT_TYPES", "FORMATS", "render", "render_json", "render_junit", "render_markdown"]
