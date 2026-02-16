from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Iterator, Optional

from .condition_model import ConditionRecord


class ConditionStore:

    def __init__(self) -> None:
        self._records: dict[str, ConditionRecord] = {}
        self._lock = threading.RLock()

    def add(self, record: ConditionRecord) -> bool:
        with self._lock:
            if record.resource_id in self._records:
                return False
            self._records[record.resource_id] = record
            return True

    def get_by_id(self, resource_id: str) -> Optional[ConditionRecord]:
        with self._lock:
            return self._records.get(resource_id)

    def get_all_active(self) -> list[ConditionRecord]:
        with self._lock:
            return [r for r in self._records.values() if not r.is_removed]

    def get_all_removed(self) -> list[ConditionRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.is_removed]

    def soft_remove(self, resource_id: str, reason: str) -> bool:
        with self._lock:
            record = self._records.get(resource_id)
            if record is None or record.is_removed:
                return False
            record.is_removed = True
            record.removal_reason = reason
            record.removal_timestamp = datetime.now(timezone.utc)
            return True

    def iterate(self) -> Iterator[ConditionRecord]:
        with self._lock:
            records = list(self._records.values())
        yield from records

    @property
    def total_count(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if not r.is_removed)

    @property
    def removed_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if r.is_removed)
