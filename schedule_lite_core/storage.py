"""本地持久化：单个 JSON 文件，原子写入。"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .model import Timetable

DATA_VERSION = 1

logger = logging.getLogger(__name__)


class TimetableStore:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._data: dict[str, Timetable] | None = None
        self._quarantined = False
        # 无法解析但必须原样保留的记录：save() 会把它们写回文件，
        # 否则「跳过一条坏记录」等于下一次写入时把该用户永久删掉。
        self._orphans: dict[str, dict] = {}

    # ---------------------------------------------------------- 基础读写

    def _quarantine(self, reason: str) -> None:
        """把整份读不了的文件移走留档，绝不覆盖上一次的备份。"""
        try:
            if not self.path.exists():
                return
            base = self.path.with_suffix(self.path.suffix + ".broken")
            target = (
                base
                if not base.exists()
                else base.with_name(
                    f"{base.name}.{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                )
            )
            os.replace(self.path, target)
            logger.warning("课表存档异常（%s），已隔离到 %s", reason, target)
        except OSError:
            pass

    def _load(self) -> dict[str, Timetable]:
        if self._data is not None:
            return self._data
        self._data = {}
        self._orphans = {}
        self._quarantined = False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except FileNotFoundError:
            return self._data
        except (ValueError, OSError):
            self._quarantine("JSON 损坏")
            self._quarantined = True
            return self._data

        users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(users, dict):
            # 结构合法但形状不对（如把备份数组放到了存档路径）：不能当作
            # 「零个用户」，否则下一次 save() 会清空全盘且没有任何现场。
            self._quarantine("users 字段结构不对")
            self._quarantined = True
            return self._data

        for key, value in users.items():
            if not isinstance(value, dict):
                logger.warning("跳过课表记录 %s：不是对象", key)
                continue
            try:
                tt = Timetable.from_dict(value)
            except Exception as exc:  # noqa: BLE001 - 坏数据形态不定，不能因一条记录中断加载
                logger.warning("保留无法解析的课表记录 %s：%s", key, exc)
                self._orphans[str(key)] = copy.deepcopy(value)
                continue
            if not tt.owner_id:
                tt.owner_id = key
            self._data[key] = tt
        return self._data

    def reload(self) -> None:
        self._data = None
        self._load()

    def save(self) -> None:
        data = self._load()
        if self._quarantined:
            # 原文件已被隔离，此时内存里是空的：写空表会把全盘用户清掉。
            # 拒绝写入，让调用方/管理员先检查 .broken 留档。
            raise ValueError(f"课表存档已被隔离，拒绝覆盖：{self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        users = copy.deepcopy(self._orphans)
        users.update({key: tt.to_dict() for key, tt in data.items()})
        payload = {
            "version": DATA_VERSION,
            "users": users,
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
        self._orphans.pop(key, None)

    def delete(self, key: str) -> Timetable | None:
        self._orphans.pop(key, None)
        return self._load().pop(key, None)

    def replace(self, items: dict[str, Timetable]) -> None:
        """整表替换（恢复备份用），保持对象引用有效。"""
        data = self._load()
        data.clear()
        data.update(items)
        self._orphans.clear()

    def all(self) -> dict[str, Timetable]:
        return dict(self._load())

    def __iter__(self) -> Iterator[tuple[str, Timetable]]:
        return iter(self._load().items())
