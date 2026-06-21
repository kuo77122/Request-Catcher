import re
import yaml
from pathlib import Path
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


def match_rule(method: str, path: str) -> ResponseRule | None:
    for rule in _rules:
        if rule.method and rule.method.upper() != method.upper():
            continue
        if rule.path and rule.path == path:
            return rule
        if rule.path_pattern and re.search(rule.path_pattern, path):
            return rule
    return None
