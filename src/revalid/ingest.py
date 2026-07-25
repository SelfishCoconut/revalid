"""Structured-report ingestion by schema mapping — no LLM involved (FR-02).

Initial supported format: DefectDojo-style JSON findings export, i.e. a
top-level ``{"findings": [...]}`` array where each entry carries at least
``title`` and ``severity``. Every source entry is preserved verbatim in
``Finding.raw`` so unmapped fields stay auditable.

**No LLM** is the invariant of this module, and it is why this door is the
deterministic, instant, free seeding path for demos and tests. A *stated*
taxonomy is still copied across — ``cvssv3``/``cvssv3_score`` from the DefectDojo
format, plus the revalid-specific ``mitre_techniques`` the manual form supplies —
because copying is not inferring, and both land ``inferred=False``. *Deriving* a
taxonomy the source never stated is the opt-in enrichment pass in
:mod:`revalid.extract`, which the caller runs after mapping (issues #233, #237).
"""

from __future__ import annotations

import json

from revalid.domain import CvssCode, Finding, MitreMapping, Severity

_SEVERITY_ALIASES: dict[str, Severity] = {
    "info": Severity.INFO,
    "informational": Severity.INFO,
    "low": Severity.LOW,
    "medium": Severity.MEDIUM,
    "high": Severity.HIGH,
    "critical": Severity.CRITICAL,
}


class IngestError(ValueError):
    """Raised when an input document cannot be mapped to the internal model."""


def load_defectdojo_export(text: str) -> list[Finding]:
    """Parse a DefectDojo-style JSON export string into domain findings.

    Args:
        text: Raw JSON document.

    Returns:
        Findings in document order.

    Raises:
        IngestError: If the document is not valid JSON or does not map.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError(f"not valid JSON: {exc}") from exc
    return map_defectdojo_export(data)


def map_defectdojo_export(data: object) -> list[Finding]:
    """Map an already-parsed DefectDojo-style export to domain findings.

    Args:
        data: Parsed JSON document; must be an object with a ``findings`` array.

    Returns:
        Findings in document order.

    Raises:
        IngestError: If the document shape or any entry is invalid.
    """
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise IngestError('expected a JSON object with a "findings" array')
    return [_map_finding(item, index) for index, item in enumerate(data["findings"])]


def _map_finding(item: object, index: int) -> Finding:
    """Map one export entry; ``index`` contextualizes error messages."""
    if not isinstance(item, dict):
        raise IngestError(f"finding #{index}: expected an object")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise IngestError(f"finding #{index}: missing or empty 'title'")
    return Finding(
        title=title.strip(),
        severity=_map_severity(item.get("severity"), index),
        description=str(item.get("description") or ""),
        affected_endpoints=_string_tuple(item.get("endpoints"), index, "endpoints"),
        reproduction_steps=_split_steps(item.get("steps_to_reproduce"), index),
        cvss=_map_cvss(item),
        mitre=_map_mitre(item, index),
        raw=item,
    )


def _map_mitre(item: dict[str, object], index: int) -> MitreMapping:
    """Copy **stated** ATT&CK technique ids across, unflagged (FR-19, issue #237).

    ``mitre_techniques`` is a revalid key, not a DefectDojo one — the format has
    no ATT&CK field, so this is the door through which the manual form (and a
    hand-written JSON payload) supplies techniques the operator read in the
    source report. Typed by a person means author-stated, so ``inferred`` is
    ``False``, exactly as the finding editor treats an operator edit (ADR-0037).

    Blank entries are dropped rather than stored, so an empty box yields "no
    taxonomy" rather than a mapping to the empty string.

    Raises:
        IngestError: If the field is present but is not a list of strings.
    """
    value = item.get("mitre_techniques")
    if value is None:
        return MitreMapping()
    techniques = _string_tuple(value, index, "mitre_techniques")
    return MitreMapping(techniques=tuple(t.strip() for t in techniques if t.strip()))


def _map_cvss(item: dict[str, object]) -> CvssCode:
    """Copy a **stated** CVSS vector across, verbatim and unflagged (FR-19, #233).

    Pure schema mapping — no model, so it happens on every import whether or not
    enrichment was requested. A DefectDojo export commonly states ``cvssv3`` (the
    base vector) and ``cvssv3_score``; copying them is free, and copying is not
    inferring, so ``inferred`` stays ``False``: this is what the source claimed.
    An export that states nothing yields an empty code, which the opt-in
    enrichment pass can later fill (flagged as inferred).

    A stated ``cwe`` is deliberately **not** turned into an ATT&CK technique: a
    weakness id is not a technique id, and deriving one from the other needs a
    real mapping, not a rename. Techniques come from :func:`_map_mitre` (the
    operator states them) or from the opt-in enrichment pass (which flags them
    inferred) — never from a renamed CWE.
    """
    vector = item.get("cvssv3") or item.get("cvss_vector")
    if not isinstance(vector, str) or not vector.strip():
        return CvssCode()
    return CvssCode(vector=vector.strip(), base_score=_map_score(item), inferred=False)


def _map_score(item: dict[str, object]) -> float | None:
    """Read a stated CVSS base score, tolerating a numeric string or an absent one."""
    for key in ("cvssv3_score", "cvss_score"):
        value = item.get(key)
        if isinstance(value, bool):  # bool is an int subclass; never a score
            continue
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                continue
    return None


def _map_severity(value: object, index: int) -> Severity:
    """Normalize a source severity label."""
    if isinstance(value, str) and value.strip().lower() in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[value.strip().lower()]
    raise IngestError(
        f"finding #{index}: unknown severity {value!r} "
        f"(expected one of {sorted(_SEVERITY_ALIASES)})"
    )


def _string_tuple(value: object, index: int, field: str) -> tuple[str, ...]:
    """Validate an optional list-of-strings field."""
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise IngestError(f"finding #{index}: '{field}' must be a list of strings")
    return tuple(value)


def _split_steps(value: object, index: int) -> tuple[str, ...]:
    """Split DefectDojo's free-text ``steps_to_reproduce`` into ordered steps."""
    if value is None:
        return ()
    if not isinstance(value, str):
        raise IngestError(f"finding #{index}: 'steps_to_reproduce' must be a string")
    return tuple(line.strip() for line in value.splitlines() if line.strip())
