from typing import Optional
from app.models import ResponseRule


def evaluate_auth(
    rule: Optional[ResponseRule], request_headers: dict[str, str]
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Return (auth_status, auth_header, auth_values_count) for a request.

    Status values:
      - None: rule is None, or rule has no `auth` block
      - "ok": header present and value is in rule.auth.values
      - "missing": header absent (case-insensitive name lookup)
      - "invalid": header present but value not in rule.auth.values
                 (or value is empty string)

    `auth_header` and `auth_values_count` are populated only when status
    is "ok" | "missing" | "invalid". They are None when no auth check
    was performed.
    """
    if rule is None or rule.auth is None:
        return None, None, None

    expected_header = rule.auth.header
    lowered_request = {k.lower(): v for k, v in request_headers.items()}

    if expected_header.lower() not in lowered_request:
        return "missing", expected_header, len(rule.auth.values)

    actual = lowered_request[expected_header.lower()]
    if actual not in rule.auth.values:
        return "invalid", expected_header, len(rule.auth.values)

    return "ok", expected_header, len(rule.auth.values)
