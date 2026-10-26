"""本地持久化：单个 JSON 文件，原子写入。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterator

from .model import Timetable

DATA_VERSION = 1


class TimetableStore:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._data: dict[str, Timetable] | None = None

    # ---------------------------------------------------------- 基础读写

    def _load(self) -> dict[str, Timetable]:
        if self._data is not None:
            return self._data
        self._data = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            users = payload.get("users") if isinstance(payload, dict) else None
            if isinstance(users, dict):
                for key, value in users.items():
                    if not isinstance(value, dict):
                        continue
                    try:
                        tt = Timetable.from_dict(value)
                    except (TypeError, ValueError, KeyError):
                        continue
                    if not tt.owner_id:
                        tt.owner_id = key
                    self._data[key] = tt
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            # 文件损坏时保留现场，避免下一次写入把用户数据覆盖掉
            try:
                broken = self.path.with_suffix(self.path.suffix + ".broken")
                os.replace(self.path, broken)
            except OSError:
                pass
        return self._data

    def reload(self) -> None:
        self._data = None
        self._load()

    def save(self) -> None:
        data = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": DATA_VERSION,
            "users": {key: tt.to_dict() for key, tt in data.items()},
        }
        fd, tmp = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # ---------------------------------------------------------- 用户数据

    def get(self, key: str) -> Timetable | None:
        return self._load().get(key)

    def put(self, key: str, tt: Timetable) -> None:
        self._load()[key] = tt

    def delete(self, key: str) -> Timetable | None:
        return self._load().pop(key, None)

    def all(self) -> dict[str, Timetable]:
        return dict(self._load())

    def __iter__(self) -> Iterator[tuple[str, Timetable]]:
        return iter(self._load().items())
