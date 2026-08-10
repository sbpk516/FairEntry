"""Secure Nasdaq Data Link snapshot downloader for the Sharadar SFA bundle.

Raw licensed files live below ``data/sharadar`` (gitignored).  API credentials
are read through the existing FairEntry secret loader and are never logged.
Every completed file is accompanied by metadata, byte size, SHA-256 and the
vendor-reported snapshot timestamp so a historical run is reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..adapters.base import get_key

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / "data" / "sharadar"
DEFAULT_TABLES = (
    "SF1",
    "SEP",
    "TICKERS",
    "ACTIONS",
    "DAILY",
    "SFP",
    "SF2",
    "SF3",
    "SF3A",
)


def _require_ok(response: requests.Response, operation: str) -> None:
    """Raise without echoing credential-bearing request or signed URLs."""
    if not response.ok:
        raise RuntimeError(f"{operation} failed with HTTP {response.status_code}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SnapshotDownloader:
    API = "https://data.nasdaq.com/api/v3/datatables/SHARADAR"

    def __init__(
        self,
        root: str | Path = DEFAULT_ROOT,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ):
        self.root = Path(root)
        self.api_key = api_key or get_key("NASDAQ_DATA_LINK_API_KEY", required=True)
        self.session = session or requests.Session()

    def metadata(self, table: str) -> dict:
        response = self.session.get(
            f"{self.API}/{table}/metadata.json",
            params={"api_key": self.api_key},
            timeout=60,
        )
        _require_ok(response, f"{table} metadata request")
        return response.json()["datatable"]

    def _export(
        self, table: str, poll_seconds: int = 10, timeout_seconds: int = 1800
    ) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while True:
            response = self.session.get(
                f"{self.API}/{table}.json",
                params={"qopts.export": "true", "api_key": self.api_key},
                timeout=90,
            )
            _require_ok(response, f"{table} bulk export request")
            export = response.json().get("datatable_bulk_download", {})
            file_info = export.get("file") or {}
            status = str(file_info.get("status") or "").lower()
            if status == "fresh" and file_info.get("link"):
                return file_info
            if status not in {"creating", "regenerating", "queued", ""}:
                raise RuntimeError(f"{table} bulk export failed with status {status!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for {table} bulk export")
            time.sleep(poll_seconds)

    def _download(self, url: str, destination: Path) -> None:
        partial = destination.with_suffix(destination.suffix + ".part")
        with self.session.get(url, stream=True, timeout=(30, 300)) as response:
            _require_ok(response, f"{destination.name} download")
            with partial.open("wb") as fh:
                for block in response.iter_content(8 * 1024 * 1024):
                    if block:
                        fh.write(block)
        os.replace(partial, destination)

    @staticmethod
    def _safe_extract(archive: Path, table_dir: Path) -> list[str]:
        table_dir.mkdir(parents=True, exist_ok=True)
        root = table_dir.resolve()
        names: list[str] = []
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (table_dir / member.filename).resolve()
                if root != target and root not in target.parents:
                    raise ValueError(
                        f"unsafe path in {archive.name}: {member.filename}"
                    )
                if not member.is_dir():
                    zf.extract(member, table_dir)
                    names.append(str(target))
        return names

    def download_snapshot(
        self,
        tables=DEFAULT_TABLES,
        snapshot_id: str | None = None,
        extract: bool = True,
        resume: bool = True,
    ) -> Path:
        snapshot_id = snapshot_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        snapshot = self.root / "snapshots" / snapshot_id
        raw_dir, extracted_dir, metadata_dir = (
            snapshot / "raw",
            snapshot / "extracted",
            snapshot / "metadata",
        )
        for directory in (raw_dir, extracted_dir, metadata_dir):
            directory.mkdir(parents=True, exist_ok=True)
        manifest_path = snapshot / "manifest.json"
        manifest = {
            "format_version": 1,
            "provider": "Nasdaq Data Link / Sharadar SFA",
            "snapshot_id": snapshot_id,
            "started_at": _utcnow(),
            "completed_at": None,
            "tables": {},
        }
        if resume and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        for requested in tables:
            table = requested.upper()
            existing = manifest["tables"].get(table, {})
            archive = raw_dir / f"SHARADAR_{table}.zip"
            if resume and existing.get("status") == "complete" and archive.exists():
                if existing.get("sha256") == _sha256(archive):
                    continue
            meta = self.metadata(table)
            _atomic_json(metadata_dir / f"{table}.json", meta)
            export = self._export(table)
            self._download(export["link"], archive)
            extracted = (
                self._safe_extract(archive, extracted_dir / table) if extract else []
            )
            manifest["tables"][table] = {
                "status": "complete",
                "vendor_snapshot_time": export.get("data_snapshot_time"),
                "metadata_refreshed_at": (meta.get("status") or {}).get("refreshed_at"),
                "archive": str(archive.relative_to(snapshot)),
                "extracted": [str(Path(p).relative_to(snapshot)) for p in extracted],
                "bytes": archive.stat().st_size,
                "sha256": _sha256(archive),
                "columns": [column.get("name") for column in meta.get("columns", [])],
                "completed_at": _utcnow(),
            }
            _atomic_json(manifest_path, manifest)

        manifest["completed_at"] = _utcnow()
        _atomic_json(manifest_path, manifest)
        latest = self.root / "LATEST"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(snapshot_id + "\n", encoding="utf-8")
        return snapshot
