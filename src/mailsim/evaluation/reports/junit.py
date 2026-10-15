"""JUnit XML export (one testcase per assertion) for CI systems.

FAIL -> <failure> carrying the expected/actual diff; NOT_APPLICABLE and INCONCLUSIVE ->
<skipped> with the reason. Everything is built with ElementTree (attribute and text
escaping) and stripped of characters XML 1.0 cannot represent, so agent-written message text
can never inject markup or break the document.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # noqa: S405 - building XML, never parsing untrusted input

from mailsim.evaluation.contract import EvaluationReport, Status

_INVALID_XML = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")
MAX_ATTR = 1000


def clean(value: object, limit: int | None = None) -> str:
    text = _INVALID_XML.sub("�", str(value))
    return text[:limit] if limit is not None else text


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def render_junit(report: EvaluationReport) -> bytes:
    outs = report.outcomes
    failures = sum(o.status is Status.FAIL for o in outs)
    skipped = sum(o.status in (Status.NOT_APPLICABLE, Status.INCONCLUSIVE) for o in outs)
    counts = {"tests": str(len(outs)), "failures": str(failures), "errors": "0",
              "skipped": str(skipped)}  # fmt: skip
    root = ET.Element("testsuites", {"name": "mailsim", **counts})
    suite = ET.SubElement(root, "testsuite", {
        "name": clean(f"{report.scenario_key} v{report.scenario_version}", MAX_ATTR),
        "timestamp": report.created_at.isoformat(), **counts,
    })  # fmt: skip
    props = ET.SubElement(suite, "properties")
    for name, value in (
        ("verdict", report.verdict.value),
        ("accuracy", _fmt(report.accuracy.overall)),
        ("checks_enabled", str(report.checks_enabled).lower()),
        ("checks_source", report.checks_source),
        ("answer_key_version", "" if report.answer_key_version is None
         else str(report.answer_key_version)),
        ("agent_build", report.agent_build or ""),
        ("run_id", report.run_id),
        ("evaluation_id", report.evaluation_id),
    ):  # fmt: skip
        ET.SubElement(props, "property", {"name": name, "value": clean(value, MAX_ATTR)})
    for o in outs:
        case = ET.SubElement(suite, "testcase", {
            "classname": clean(f"{report.scenario_key}.{o.category}", MAX_ATTR),
            "name": clean(o.assertion_id, MAX_ATTR),
        })  # fmt: skip
        if o.status is Status.FAIL:
            node = ET.SubElement(case, "failure", {
                "message": clean(o.reason, MAX_ATTR), "type": f"{o.type}.{o.severity}",
            })  # fmt: skip
            node.text = clean(json.dumps(o.diff.model_dump(), indent=2, sort_keys=True,
                                         default=str))  # fmt: skip
        elif o.status is not Status.PASS:
            ET.SubElement(case, "skipped", {"message": clean(f"{o.status.value}: {o.reason}",
                                                             MAX_ATTR)})  # fmt: skip
        if o.evidence:
            out = ET.SubElement(case, "system-out")
            out.text = clean("\n".join(
                f"{e.kind}:{e.ref}" + (f" ({e.note})" if e.note else "") for e in o.evidence
            ))  # fmt: skip
    ET.indent(root)
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
