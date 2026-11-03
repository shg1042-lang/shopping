"""쇼핑리스트 데이터를 JSON 파일로 저장하고 불러온다.

- 파일이 유일한 원본이다. 화면은 매 실행마다 파일을 새로 읽는다.
- 변경은 update_items()로 "읽기 → 수정 → 저장"을 잠금 안에서 한 번에 처리한다.
  (여러 탭/세션이 동시에 써도 서로의 변경을 덮어쓰지 않게 하기 위함)
- 저장은 임시 파일에 쓴 뒤 교체하므로, 쓰는 도중 중단되어도 기존 파일이 깨지지 않는다.
"""

import json
import os
import shutil
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

DATA_FILE = Path(__file__).resolve().parent / "shopping_list.json"

NAME_MAX_LEN = 50
QTY_MIN, QTY_MAX = 1, 999

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _backup() -> Path:
    """현재 데이터 파일을 시각이 붙은 이름으로 백업한다. (이전 백업을 덮어쓰지 않음)"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = DATA_FILE.with_name(f"{DATA_FILE.name}.{stamp}.bak")
    shutil.copy2(DATA_FILE, dest)
    return dest


def _write(items: list[dict]) -> None:
    """임시 파일에 쓴 뒤 원자적으로 교체한다."""
    fd, tmp = tempfile.mkstemp(dir=DATA_FILE.parent, prefix=".shopping_list.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATA_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _normalize(raw: list) -> tuple[list[dict], int]:
    """항목 형식을 검사해 정리한다. (정리된 목록, 고치거나 버린 항목 수)를 반환한다."""
    items: list[dict] = []
    seen_ids: set[str] = set()
    problems = 0
    for entry in raw:
        if not isinstance(entry, dict):
            problems += 1
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            problems += 1
            continue

        item_id = entry.get("id")
        if not isinstance(item_id, str) or not item_id or item_id in seen_ids:
            item_id = str(uuid.uuid4())
        seen_ids.add(item_id)

        qty = entry.get("quantity")
        if isinstance(qty, bool) or not isinstance(qty, int):
            qty = QTY_MIN
        qty = min(max(qty, QTY_MIN), QTY_MAX)

        checked = entry.get("checked")
        if not isinstance(checked, bool):
            checked = False

        created_at = entry.get("created_at")
        if not isinstance(created_at, str):
            created_at = _now()

        item = {
            "id": item_id,
            "name": name.strip()[:NAME_MAX_LEN],
            "quantity": qty,
            "checked": checked,
            "created_at": created_at,
        }
        if item != entry:
            problems += 1
        items.append(item)
    return items, problems


def load_items() -> tuple[list[dict], str | None]:
    """파일에서 목록을 불러온다. (목록, 경고 문구 또는 None)을 반환한다.

    - 파일이 없으면 빈 목록.
    - 파일이 손상되었으면 백업 후 빈 목록으로 초기화하고 경고를 돌려준다.
    - 일부 항목만 잘못되었으면 백업 후 정리한 목록을 저장하고 경고를 돌려준다.
      (정리 결과를 바로 저장하므로 경고는 한 번만 나온다)
    """
    with _LOCK:
        if not DATA_FILE.exists():
            return [], None
        try:
            with DATA_FILE.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                raise ValueError("데이터 형식이 리스트가 아닙니다.")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            bak = _backup()
            _write([])
            return [], f"데이터 파일이 손상되어 빈 목록으로 시작합니다. 기존 파일은 '{bak.name}'에 백업했습니다."

        items, problems = _normalize(raw)
        if problems:
            bak = _backup()
            _write(items)
            return items, (
                f"데이터 파일에서 잘못된 항목 {problems}개를 정리했습니다. "
                f"원본은 '{bak.name}'에 백업했습니다."
            )
        return items, None


def save_items(items: list[dict]) -> None:
    """목록 전체를 파일로 저장한다."""
    with _LOCK:
        _write(items)


def update_items(fn: Callable[[list[dict]], Any]) -> tuple[list[dict], Any, str | None]:
    """잠금 안에서 최신 목록을 읽고 fn(items)로 수정한 뒤 저장한다.

    fn은 목록을 직접 수정하고 원하는 값을 반환한다.
    반환값: (저장된 목록, fn의 반환값, 불러올 때의 경고)
    """
    with _LOCK:
        items, warning = load_items()
        result = fn(items)
        _write(items)
        return items, result, warning
