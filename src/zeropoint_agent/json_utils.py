import json
from pathlib import Path


def json_dumps(obj, normalize: bool = False) -> str:
    """Return a JSON string.

    By default keys are not sorted so that any issues in SQL->JSON
    generation are visible. Set `normalize=True` only when normalizing
    user-provided or merged objects is desired.
    """
    if normalize:
        return json.dumps(obj, sort_keys=True, indent=2, separators=(",", ": "), ensure_ascii=False)
    return json.dumps(obj, indent=2, separators=(",", ": "), ensure_ascii=False)


def atomic_write_json(path: Path, obj) -> None:
    """Write JSON atomically to avoid partial files.

    Writes to a temp file then renames into place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json_dumps(obj))
    tmp.replace(path)
