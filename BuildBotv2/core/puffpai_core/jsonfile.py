"""Small JSON files in the bot folder: read with a default, write atomically.

The Core is the one writer of each file; a lock per process keeps the API's
threads from interleaving a read-modify-write.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def read(path: Path | str, default: Any) -> Any:
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return copy.deepcopy(default)
    except ValueError:
        logger.warning("%s is not valid JSON; using the default", path)
        return copy.deepcopy(default)


def write(path: Path | str, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with LOCK:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
