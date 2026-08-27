"""Persistent, process-safe cache primitives for Lanhu design data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Optional

from filelock import AsyncFileLock, FileLock, Timeout as FileLockTimeout


CACHE_SCHEMA_VERSION = 1
VALID_CACHE_POLICIES = frozenset({"use", "refresh", "bypass"})


def normalize_cache_policy(cache_policy: str) -> str:
    """Validate and normalize the public cache policy argument."""
    normalized = str(cache_policy or "use").strip().lower()
    if normalized not in VALID_CACHE_POLICIES:
        choices = ", ".join(sorted(VALID_CACHE_POLICIES))
        raise ValueError(f"Invalid cache_policy '{cache_policy}'. Expected one of: {choices}")
    return normalized


@dataclass(frozen=True)
class CacheRecord:
    cache_key: str
    namespace: str
    version_id: Optional[str]
    checked_at: float
    fetched_at: float
    payload_path: Optional[Path]
    artifact_path: Optional[Path]
    metadata: dict[str, Any]

    def is_fresh(self, ttl_seconds: float, now: Optional[float] = None) -> bool:
        if ttl_seconds <= 0:
            return True
        current_time = time.time() if now is None else now
        return current_time - self.checked_at < ttl_seconds


@dataclass(frozen=True)
class CacheLockStats:
    waited_for_inflight: bool
    lock_wait_ms: int


class DesignCache:
    """SQLite metadata plus atomic files, coordinated by per-key OS locks."""

    def __init__(
        self,
        root_dir: Path,
        *,
        schema_version: int = CACHE_SCHEMA_VERSION,
        lock_timeout_seconds: float = 330,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.schema_version = schema_version
        self.lock_timeout_seconds = lock_timeout_seconds
        self.db_path = self.root_dir / "cache.sqlite3"
        self.objects_dir = self.root_dir / "objects"
        self.locks_dir = self.root_dir / "locks"
        self.mutation_lock_path = self.locks_dir / "cache-mutations.lock"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS design_cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    version_id TEXT,
                    checked_at REAL NOT NULL,
                    fetched_at REAL NOT NULL,
                    payload_path TEXT,
                    artifact_path TEXT,
                    metadata_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_design_cache_namespace "
                "ON design_cache_entries(namespace)"
            )

    def make_key(self, namespace: str, identity: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            {
                "schema_version": self.schema_version,
                "namespace": namespace,
                "identity": dict(identity),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_namespace(namespace: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", namespace).strip("_")
        return safe or "cache"

    def _row_to_record(self, row: sqlite3.Row) -> Optional[CacheRecord]:
        if row["schema_version"] != self.schema_version:
            return None
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            return None
        return CacheRecord(
            cache_key=row["cache_key"],
            namespace=row["namespace"],
            version_id=row["version_id"],
            checked_at=float(row["checked_at"]),
            fetched_at=float(row["fetched_at"]),
            payload_path=Path(row["payload_path"]) if row["payload_path"] else None,
            artifact_path=Path(row["artifact_path"]) if row["artifact_path"] else None,
            metadata=metadata,
        )

    def get_record(self, namespace: str, identity: Mapping[str, Any]) -> Optional[CacheRecord]:
        cache_key = self.make_key(namespace, identity)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM design_cache_entries WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def load_json(
        self,
        namespace: str,
        identity: Mapping[str, Any],
    ) -> tuple[Optional[CacheRecord], Optional[Any]]:
        record = self.get_record(namespace, identity)
        if not record or not record.payload_path or not record.payload_path.is_file():
            return None, None
        try:
            with record.payload_path.open("r", encoding="utf-8") as payload_file:
                return record, json.load(payload_file)
        except (OSError, ValueError, TypeError):
            return None, None

    def load_artifact(
        self,
        namespace: str,
        identity: Mapping[str, Any],
    ) -> Optional[CacheRecord]:
        record = self.get_record(namespace, identity)
        if not record or not record.artifact_path:
            return None
        try:
            if not record.artifact_path.is_file() or record.artifact_path.stat().st_size <= 0:
                return None
        except OSError:
            return None
        return record

    def _upsert_record(
        self,
        *,
        cache_key: str,
        namespace: str,
        version_id: Optional[str],
        checked_at: float,
        fetched_at: float,
        payload_path: Optional[Path],
        artifact_path: Optional[Path],
        metadata: Optional[Mapping[str, Any]],
    ) -> CacheRecord:
        metadata_dict = dict(metadata or {})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO design_cache_entries (
                    cache_key, namespace, version_id, checked_at, fetched_at,
                    payload_path, artifact_path, metadata_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    namespace = excluded.namespace,
                    version_id = excluded.version_id,
                    checked_at = excluded.checked_at,
                    fetched_at = excluded.fetched_at,
                    payload_path = excluded.payload_path,
                    artifact_path = excluded.artifact_path,
                    metadata_json = excluded.metadata_json,
                    schema_version = excluded.schema_version
                """,
                (
                    cache_key,
                    namespace,
                    version_id,
                    checked_at,
                    fetched_at,
                    str(payload_path) if payload_path else None,
                    str(artifact_path) if artifact_path else None,
                    json.dumps(metadata_dict, ensure_ascii=False, separators=(",", ":")),
                    self.schema_version,
                ),
            )
        record = self.get_record_by_key(cache_key)
        if record is None:
            raise RuntimeError(f"Failed to persist cache record {cache_key}")
        return record

    def get_record_by_key(self, cache_key: str) -> Optional[CacheRecord]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM design_cache_entries WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def store_json(
        self,
        namespace: str,
        identity: Mapping[str, Any],
        payload: Any,
        *,
        version_id: Optional[str],
        checked_at: Optional[float] = None,
        fetched_at: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> CacheRecord:
        cache_key = self.make_key(namespace, identity)
        namespace_dir = self.objects_dir / self._safe_namespace(namespace)
        serialized_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        content_digest = hashlib.sha256(serialized_payload).hexdigest()
        object_digest = hashlib.sha256(
            f"{cache_key}:{content_digest}".encode("ascii")
        ).hexdigest()
        payload_path = namespace_dir / f"{object_digest}.json"
        with self._mutation_lock():
            previous_record = self.get_record_by_key(cache_key)
            self.atomic_write_bytes(payload_path, serialized_payload)
            now = time.time()
            try:
                stored_record = self._upsert_record(
                    cache_key=cache_key,
                    namespace=namespace,
                    version_id=version_id,
                    checked_at=now if checked_at is None else checked_at,
                    fetched_at=now if fetched_at is None else fetched_at,
                    payload_path=payload_path,
                    artifact_path=None,
                    metadata=metadata,
                )
            except Exception:
                # A failed SQLite update must not leave an unreferenced object
                # that looks like a committed cache entry.
                self._unlink_if_unreferenced(payload_path, object_only=True)
                raise

            self._cleanup_displaced_files(previous_record, stored_record)
            return stored_record

    def store_artifact(
        self,
        namespace: str,
        identity: Mapping[str, Any],
        artifact_path: Path,
        *,
        version_id: Optional[str],
        checked_at: Optional[float] = None,
        fetched_at: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> CacheRecord:
        cache_key = self.make_key(namespace, identity)
        resolved_artifact_path = Path(artifact_path).resolve()
        with self._mutation_lock():
            try:
                if (
                    not resolved_artifact_path.is_file()
                    or resolved_artifact_path.stat().st_size <= 0
                ):
                    raise FileNotFoundError(
                        f"Cache artifact is missing or empty: {resolved_artifact_path}"
                    )
            except OSError as exc:
                raise FileNotFoundError(
                    f"Cache artifact is unavailable: {resolved_artifact_path}"
                ) from exc

            previous_record = self.get_record_by_key(cache_key)
            now = time.time()
            stored_record = self._upsert_record(
                cache_key=cache_key,
                namespace=namespace,
                version_id=version_id,
                checked_at=now if checked_at is None else checked_at,
                fetched_at=now if fetched_at is None else fetched_at,
                payload_path=None,
                artifact_path=resolved_artifact_path,
                metadata=metadata,
            )
            self._cleanup_displaced_files(previous_record, stored_record)
            return stored_record

    def touch(
        self,
        namespace: str,
        identity: Mapping[str, Any],
        *,
        checked_at: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[CacheRecord]:
        cache_key = self.make_key(namespace, identity)
        with self._mutation_lock():
            current = self.get_record_by_key(cache_key)
            if current is None:
                return None
            next_metadata = current.metadata if metadata is None else dict(metadata)
            return self._upsert_record(
                cache_key=cache_key,
                namespace=current.namespace,
                version_id=current.version_id,
                checked_at=time.time() if checked_at is None else checked_at,
                fetched_at=current.fetched_at,
                payload_path=current.payload_path,
                artifact_path=current.artifact_path,
                metadata=next_metadata,
            )

    def prune_versions(
        self,
        namespace: str,
        metadata_match: Mapping[str, Any],
        *,
        keep_latest: int = 2,
        minimum_age_seconds: float = 86400,
    ) -> int:
        """Remove old versioned entries while preserving recent/in-use versions."""
        keep_count = max(0, int(keep_latest))
        minimum_age = max(0.0, float(minimum_age_seconds))

        with self._mutation_lock():
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM design_cache_entries "
                    "WHERE namespace = ? AND version_id IS NOT NULL "
                    "ORDER BY fetched_at DESC, checked_at DESC, cache_key DESC",
                    (namespace,),
                ).fetchall()

            matching_records = []
            for row in rows:
                record = self._row_to_record(row)
                if not record:
                    continue
                if all(
                    record.metadata.get(key) == value
                    for key, value in metadata_match.items()
                ):
                    matching_records.append(record)

            now = time.time()
            removable = [
                record
                for record in matching_records[keep_count:]
                if now - record.fetched_at >= minimum_age
            ]
            if not removable:
                return 0

            with self._connect() as connection:
                connection.executemany(
                    "DELETE FROM design_cache_entries WHERE cache_key = ?",
                    [(record.cache_key,) for record in removable],
                )

            # Database deletion commits before file deletion. A crash can leave
            # an orphan, but never a live row pointing at a deleted file.
            for record in removable:
                if record.payload_path:
                    self._unlink_if_unreferenced(record.payload_path, object_only=True)
                if record.artifact_path:
                    self._unlink_if_unreferenced(record.artifact_path)
            return len(removable)

    @contextmanager
    def _mutation_lock(self):
        """Serialize cache row/file mutations across MCP processes."""
        lock = FileLock(
            self.mutation_lock_path,
            timeout=self.lock_timeout_seconds,
        )
        with lock:
            yield

    def _path_is_referenced(self, path: Path) -> bool:
        resolved_path = str(Path(path).resolve())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM design_cache_entries "
                "WHERE payload_path = ? OR artifact_path = ? LIMIT 1",
                (resolved_path, resolved_path),
            ).fetchone()
        return row is not None

    def _unlink_if_unreferenced(self, path: Path, *, object_only: bool = False) -> None:
        try:
            resolved_path = Path(path).resolve()
            if object_only:
                resolved_path.relative_to(self.objects_dir)
            if resolved_path == self.db_path or self.locks_dir in resolved_path.parents:
                return
            if self._path_is_referenced(resolved_path):
                return
            resolved_path.unlink(missing_ok=True)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            pass

    def _cleanup_displaced_files(
        self,
        previous_record: Optional[CacheRecord],
        stored_record: CacheRecord,
    ) -> None:
        if not previous_record:
            return
        if previous_record.payload_path != stored_record.payload_path:
            if previous_record.payload_path:
                self._unlink_if_unreferenced(
                    previous_record.payload_path,
                    object_only=True,
                )
        if previous_record.artifact_path != stored_record.artifact_path:
            if previous_record.artifact_path:
                self._unlink_if_unreferenced(previous_record.artifact_path)

    @asynccontextmanager
    async def lock(
        self,
        namespace: str,
        identity: Mapping[str, Any],
    ) -> AsyncIterator[CacheLockStats]:
        cache_key = self.make_key(namespace, identity)
        lock_path = self.locks_dir / f"{cache_key}.lock"
        lock = AsyncFileLock(lock_path, timeout=self.lock_timeout_seconds)
        started = time.monotonic()
        waited = False
        try:
            try:
                await lock.acquire(timeout=0)
            except FileLockTimeout:
                waited = True
                await lock.acquire(timeout=self.lock_timeout_seconds)
            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            yield CacheLockStats(
                waited_for_inflight=waited,
                lock_wait_ms=elapsed_ms,
            )
        finally:
            if lock.is_locked:
                await lock.release()

    @staticmethod
    def atomic_write_json(target_path: Path, payload: Any) -> None:
        serialized_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        DesignCache.atomic_write_bytes(target_path, serialized_payload)

    def _unlink_object_file(self, path: Path) -> None:
        self._unlink_if_unreferenced(path, object_only=True)

    @staticmethod
    def _matches_existing_bytes(target_path: Path, payload: bytes) -> bool:
        try:
            if not target_path.is_file() or target_path.stat().st_size != len(payload):
                return False
            with target_path.open("rb") as existing_file:
                offset = 0
                while offset < len(payload):
                    existing_chunk = existing_file.read(1024 * 1024)
                    expected_chunk = payload[offset:offset + len(existing_chunk)]
                    if not existing_chunk or existing_chunk != expected_chunk:
                        return False
                    offset += len(existing_chunk)
            return offset == len(payload)
        except OSError:
            return False

    @staticmethod
    def _replace_with_retry(source_path: Path, target_path: Path) -> None:
        for attempt in range(8):
            try:
                os.replace(source_path, target_path)
                return
            except OSError as exc:
                sharing_violation = isinstance(exc, PermissionError) or (
                    getattr(exc, "winerror", None) in {5, 32, 33}
                )
                if not sharing_violation or attempt == 7:
                    raise
                time.sleep(min(0.2, 0.01 * (2 ** attempt)))

    @staticmethod
    def atomic_write_bytes(target_path: Path, payload: bytes) -> None:
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if DesignCache._matches_existing_bytes(target_path, payload):
            return
        temp_path = target_path.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("wb") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            DesignCache._replace_with_retry(temp_path, target_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
