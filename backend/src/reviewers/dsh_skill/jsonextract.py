"""Tolerant extraction of one complete JSON value from a model's final text.

The benchmark contract requires the entire reply to be a single JSON object.
In practice the review model occasionally wraps the object in prose before or
after it (observed in archived runs for dbeaver and google-gemini).  Salvage
the last complete top-level JSON object instead of rejecting the answer, while
never treating a malformed reply as a complete one.
"""
import json


def extract_json_object(text):
    """Return the last complete top-level JSON object substring of ``text``.

    Returns ``None`` when the text contains no balanced ``{...}`` object at the
    top level.  Braces inside string literals are skipped, including escaped
    quotes, so ``say "}"``-style prose cannot corrupt the scan.  The scan keeps
    the *last* balanced object because the answer is the terminal content of a
    reply; an earlier quoted example in prose would otherwise be preferred.
    """
    if not isinstance(text, str):
        return None
    candidates = []
    opening = -1
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            if depth == 0:
                opening = index
            depth += 1
        elif char == '}':
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and opening >= 0:
                candidates.append(text[opening:index + 1])
                opening = -1
    return candidates[-1] if candidates else None


def loads_tolerant(text):
    """Parse ``text`` as JSON, else parse its last top-level JSON object.

    Returns ``None`` only when neither the whole text nor any contained object
    parses; a parsed-but-semantically-invalid value (e.g. a list or string) is
    returned as-is and callers apply their own schema checks.
    """
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    candidate = extract_json_object(text)
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None
