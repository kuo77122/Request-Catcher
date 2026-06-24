import json
import re
import yaml
from pathlib import Path
from typing import Optional
from app.models import ResponseRule

_config_path = Path("responses.yaml")
_rules: list[ResponseRule] = []


def load_config(path: str | Path | None = None) -> list[ResponseRule]:
    global _config_path, _rules
    if path:
        _config_path = Path(path)
    if not _config_path.exists():
        _rules = []
        return _rules
    raw = _config_path.read_text()
    data = yaml.safe_load(raw) or {}
    _rules = [ResponseRule(**r) for r in data.get("rules", [])]
    return _rules


def reload_config() -> list[ResponseRule]:
    return load_config()


def get_rules() -> list[ResponseRule]:
    return _rules


def match_rule(
    method: str,
    path: str,
    query_params: dict[str, str],
    body_str: Optional[str],
) -> Optional[ResponseRule]:
    for rule in _rules:
        if rule.method and rule.method.upper() != method.upper():
            continue
        path_matched = False
        if rule.path and rule.path == path:
            path_matched = True
        elif rule.path_pattern and re.search(rule.path_pattern, path):
            path_matched = True
        if not path_matched:
            continue
        if not _match_conditions(rule, query_params, body_str):
            continue
        return rule
    return None


def _match_conditions(
    rule: ResponseRule,
    query_params: dict[str, str],
    body_str: Optional[str],
) -> bool:
    if not rule.match:
        return True
    body = _parse_body_object(body_str)
    for key, expected in rule.match.items():
        if body is not None and key in body:
            candidate = body[key]
        elif key in query_params:
            candidate = query_params[key]
        else:
            return False
        if str(candidate) != str(expected):
            return False
    return True


def _parse_body_object(body_str: Optional[str]) -> Optional[dict]:
    if not body_str:
        return None
    try:
        body = json.loads(body_str)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    return body
