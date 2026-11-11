"""Tolerant extraction of one complete JSON value from a model's final text.

The benchmark contract requires the entire reply to be a single JSON object.
In practice the review model occasionally wraps the object in prose before or
after it (observed in archived runs for dbeaver, google-gemini, kestra).
Salvage the last complete top-level JSON object instead of rejecting the
answer, while never treating a malformed reply as a complete one.

Two tiers are needed because the two past failure shapes pull in opposite
directions:

* A balanced-brace scan alone is corrupted by prose that itself contains
  template braces and stray quotes.  The archived kestra case put ``{{ … }}``
  and raw ``"`` characters into the prose preface, which shifted the scan's
  string/brace state so it returned one inner comment object instead of the
  answer; the reply was then rejected as "不是完整 JSON 协议".
* A strict "the object must occupy the whole tail" rule alone rejects replies
  whose object is wrapped in a Markdown fence or followed by a closing remark
  (archived dbeaver/gofr/waveterm finals end the object then close a ```json
  fence).  Those replies are legitimate and must keep being salvaged.

So the primary scan is the strict tail-occupying one, and only when it finds
nothing do we fall back to the legacy balanced-brace scan.
"""
import json


def _last_tail_object(text):
    """Return the last JSON object that starts at a ``{`` and occupies the
    entire remainder of ``text`` (modulo trailing whitespace), or ``None``.

    Each candidate is parsed with a real JSON decoder, so prose braces and
    quotes cannot fake a candidate: ``{{ outputs.output_values.²}}`` in prose
    is not decodable and is skipped.  The last such object wins because the
    answer is the terminal content of a reply.
    """
    decoder = json.JSONDecoder()
    best = None
    start = 0
    while True:
        position = text.find('{', start)
        if position < 0:
            break
        start = position + 1
        try:
            _, end = decoder.raw_decode(text, position)
        except (ValueError, TypeError):
            continue
        if not text[end:].strip():
            best = text[position:end]
    return best


def _last_balanced_object(text):
    """Legacy scan: the last brace-balanced ``{...}`` object at top level.

    Braces inside string literals are skipped, including escaped quotes, so
    ``say "}"``-style prose cannot corrupt the scan.  Kept as the fallback for
    replies that wrap the object in a code fence or add a closing remark after
    it, where the strict tail-occupying scan by design fails.
    """
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


def extract_json_object(text):
    """Return the last complete top-level JSON object substring of ``text``.

    Returns ``None`` when the text contains no balanced ``{...}`` object at the
    top level.  The tail-occupying scan runs first; the legacy balanced scan is
    the fallback for fenced or trailing-prose replies.
    """
    if not isinstance(text, str):
        return None
    found = _last_tail_object(text)
    if found is not None:
        return found
    return _last_balanced_object(text)


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
