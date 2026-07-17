import hashlib
import json
from collections.abc import Mapping


def canonical_json(payload: object) -> str:
    return json.dumps(
        _json_plain(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _json_plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_plain(item) for item in value]
    return value
