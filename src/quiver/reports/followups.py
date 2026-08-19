"""Persistent, explicitly user-controlled follow-up lifecycle."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path
from typing import Callable

from quiver.reports.models import FOLLOW_UP_STATUSES, FollowUp, utc_now
from quiver.reports.store import (
    MalformedReportStateError,
    ReportStore,
    _atomic_write_json,
    _read_json,
)


def _normalized_identity(text: str, project_root: str) -> str:
    normalized_text = re.sub(r"\s+", " ", text).strip().casefold()
    normalized_root = str(Path(project_root).expanduser()).rstrip("/").casefold()
    return f"{normalized_root}\0{normalized_text}"


def stable_follow_up_id(text: str, project_root: str) -> str:
    digest = hashlib.sha256(_normalized_identity(text, project_root).encode("utf-8")).hexdigest()
    return f"fu_{digest[:16]}"


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *incoming]))


class FollowUpLedger:
    def __init__(
        self,
        root: Path | str | None = None,
        clock: Callable[[], str] = utc_now,
        store: ReportStore | None = None,
    ) -> None:
        self.store = store or ReportStore(root=root, clock=clock)
        self.clock = clock

    @property
    def path(self) -> Path:
        return self.store.followups_file

    def _load_index(self) -> dict[str, FollowUp]:
        data = _read_json(self.path, {"version": 1, "follow_ups": []})
        if not isinstance(data, dict) or not isinstance(data.get("follow_ups"), list):
            raise MalformedReportStateError(self.path)
        index: dict[str, FollowUp] = {}
        try:
            for item in data["follow_ups"]:
                if not isinstance(item, dict):
                    raise TypeError
                follow_up = FollowUp.from_dict(item)
                if follow_up.id in index:
                    raise ValueError("duplicate follow-up ID")
                index[follow_up.id] = follow_up
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedReportStateError(self.path) from exc
        return index

    def _save_index(self, index: dict[str, FollowUp]) -> None:
        payload = {
            "version": 1,
            "follow_ups": [item.to_dict() for item in sorted(index.values(), key=lambda x: x.created_at)],
        }
        _atomic_write_json(self.path, payload)

    def get(self, follow_up_id: str) -> FollowUp | None:
        return self._load_index().get(follow_up_id)

    def list(
        self, status: str | None = None, project_root: str | None = None
    ) -> list[FollowUp]:
        if status is not None and status not in FOLLOW_UP_STATUSES:
            raise ValueError(f"invalid follow-up status: {status}")
        items = self._load_index().values()
        if status is not None:
            items = (item for item in items if item.status == status)
        if project_root is not None:
            wanted = str(Path(project_root).expanduser())
            items = (item for item in items if item.project_root == wanted)
        return sorted(items, key=lambda item: (item.status != "open", item.created_at, item.id))

    def add(
        self,
        text: str,
        project_root: str,
        *,
        source_session_ids: list[str] | None = None,
        source_report_ids: list[str] | None = None,
        context: str = "",
        blockers: list[str] | None = None,
        completion_criteria: list[str] | None = None,
    ) -> FollowUp:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("follow-up text must not be empty")
        clean_root = str(Path(project_root).expanduser())
        follow_up_id = stable_follow_up_id(clean_text, clean_root)
        index = self._load_index()
        existing = index.get(follow_up_id)
        if existing is not None:
            merged = replace(
                existing,
                source_session_ids=_merge_unique(existing.source_session_ids, source_session_ids or []),
                source_report_ids=_merge_unique(existing.source_report_ids, source_report_ids or []),
                context=existing.context or context,
                blockers=_merge_unique(existing.blockers, blockers or []),
                completion_criteria=_merge_unique(
                    existing.completion_criteria, completion_criteria or []
                ),
                updated_at=self.clock(),
            )
            index[follow_up_id] = merged
            self._save_index(index)
            return merged

        now = self.clock()
        item = FollowUp(
            id=follow_up_id,
            text=clean_text,
            project_root=clean_root,
            source_session_ids=list(dict.fromkeys(source_session_ids or [])),
            source_report_ids=list(dict.fromkeys(source_report_ids or [])),
            context=context,
            blockers=list(dict.fromkeys(blockers or [])),
            completion_criteria=list(dict.fromkeys(completion_criteria or [])),
            created_at=now,
            updated_at=now,
        )
        index[item.id] = item
        self._save_index(index)
        return item

    def _require(self, index: dict[str, FollowUp], follow_up_id: str) -> FollowUp:
        try:
            return index[follow_up_id]
        except KeyError as exc:
            raise KeyError(f"unknown follow-up: {follow_up_id}") from exc

    def edit(
        self,
        follow_up_id: str,
        *,
        text: str | None = None,
        project_root: str | None = None,
        context: str | None = None,
        blockers: list[str] | None = None,
        completion_criteria: list[str] | None = None,
    ) -> FollowUp:
        index = self._load_index()
        item = self._require(index, follow_up_id)
        if text is not None and not text.strip():
            raise ValueError("follow-up text must not be empty")
        updated = replace(
            item,
            text=text.strip() if text is not None else item.text,
            project_root=(
                str(Path(project_root).expanduser()) if project_root is not None else item.project_root
            ),
            context=context if context is not None else item.context,
            blockers=list(dict.fromkeys(blockers)) if blockers is not None else item.blockers,
            completion_criteria=(
                list(dict.fromkeys(completion_criteria))
                if completion_criteria is not None
                else item.completion_criteria
            ),
            updated_at=self.clock(),
        )
        # IDs are intentionally stable across edits so report references remain valid.
        index[follow_up_id] = updated
        self._save_index(index)
        return updated

    def done(self, follow_up_id: str) -> FollowUp:
        return self._transition(follow_up_id, "done")

    def dismiss(self, follow_up_id: str) -> FollowUp:
        return self._transition(follow_up_id, "dismissed")

    def reopen(self, follow_up_id: str) -> FollowUp:
        return self._transition(follow_up_id, "open")

    def _transition(self, follow_up_id: str, status: str) -> FollowUp:
        index = self._load_index()
        item = self._require(index, follow_up_id)
        now = self.clock()
        updated = replace(
            item,
            status=status,
            updated_at=now,
            completed_at=now if status == "done" else None,
            dismissed_at=now if status == "dismissed" else None,
        )
        index[follow_up_id] = updated
        self._save_index(index)
        return updated

    def suggest_resolution(self, follow_up_id: str, suggestion: str) -> FollowUp:
        """Attach model metadata without changing user-controlled status."""
        index = self._load_index()
        item = self._require(index, follow_up_id)
        now = self.clock()
        updated = replace(
            item,
            resolution_suggested=True,
            resolution_suggestion=suggestion.strip(),
            resolution_suggested_at=now,
            updated_at=now,
        )
        index[follow_up_id] = updated
        self._save_index(index)
        return updated
