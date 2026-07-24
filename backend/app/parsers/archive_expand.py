"""Archive Agent (L2) — recursive ZIP/TAR expansion.

Unlike every other L2 agent, this one doesn't produce a ParsedDocument
directly: an archive isn't "one file", it's a container for other files
that each need to go through the normal per-file pipeline. So expansion
happens once, up front, at ingest time (see api/routes_ingest.py) —
extracted members are added to the job's file list in place of the
archive itself, then flow through the standard router -> parser pipeline
like any directly-uploaded file.

Only ZIP and TAR(.gz/.bz2/.xz) are supported via the standard library —
no extra dependencies. 7Z and RAR are detected and reported as
unsupported rather than silently skipped (would need py7zr / rarfile,
deliberately not added given how much trouble heavier dependencies have
already caused in this build — see README).

Safety guards against malicious/oversized archives (zip bombs, path
traversal, symlink escapes):
- Path traversal: every member's resolved path is checked to stay inside
  the extraction directory before writing.
- Symlinks: never followed/extracted (tar can encode a symlink pointing
  anywhere on the host filesystem).
- Size/count/depth caps: extraction stops once any limit is hit, with a
  warning rather than a crash.
"""
import logging
import tarfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_DEPTH = 3
MAX_FILES = 500
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB decompressed, in aggregate

_ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz"}
_UNSUPPORTED_SUFFIXES = {".7z", ".rar"}


def is_archive(file_path: str) -> bool:
    suffix = Path(file_path).suffix.lower()
    return suffix in _ARCHIVE_SUFFIXES or suffix in _UNSUPPORTED_SUFFIXES


class _Budget:
    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0

    def take(self, size: int) -> bool:
        if self.files + 1 > MAX_FILES or self.bytes + size > MAX_TOTAL_BYTES:
            return False
        self.files += 1
        self.bytes += size
        return True


def expand_archive(file_path: str, dest_dir: str) -> tuple[list[str], list[str]]:
    """Returns (extracted_file_paths, warnings). Recurses into nested
    archives up to MAX_DEPTH. The top-level archive itself is never
    included in the returned paths -- only what it contained.
    """
    warnings: list[str] = []
    budget = _Budget()
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    extracted = _expand_one(file_path, dest, depth=0, budget=budget, warnings=warnings)
    return extracted, warnings


def _expand_one(file_path: str, dest_dir: Path, depth: int, budget: _Budget, warnings: list[str]) -> list[str]:
    suffix = Path(file_path).suffix.lower()
    if suffix in _UNSUPPORTED_SUFFIXES:
        warnings.append(f"{Path(file_path).name}: {suffix} archives are not supported in this build "
                         f"(would need py7zr/rarfile) — file skipped.")
        return []
    if depth >= MAX_DEPTH:
        warnings.append(f"{Path(file_path).name}: nested archive depth limit ({MAX_DEPTH}) reached — not expanded further.")
        return []

    archive_workdir = dest_dir / f"_extract_{Path(file_path).stem}_{depth}"
    try:
        if zipfile.is_zipfile(file_path):
            members = _extract_zip(file_path, archive_workdir, budget, warnings)
        elif tarfile.is_tarfile(file_path):
            members = _extract_tar(file_path, archive_workdir, budget, warnings)
        else:
            warnings.append(f"{Path(file_path).name}: not a recognized archive format — skipped.")
            return []
    except Exception as exc:  # noqa: BLE001
        logger.exception("Archive extraction failed for %s", file_path)
        warnings.append(f"{Path(file_path).name}: extraction failed ({exc}).")
        return []

    results: list[str] = []
    for member_path in members:
        if is_archive(member_path):
            results.extend(_expand_one(member_path, dest_dir, depth + 1, budget, warnings))
        else:
            results.append(member_path)
    return results


def _safe_join(base: Path, member_name: str) -> Path | None:
    candidate = (base / member_name).resolve()
    base_resolved = base.resolve()
    if base_resolved not in candidate.parents and candidate != base_resolved:
        return None  # path traversal attempt (e.g. "../../etc/passwd")
    return candidate


def _extract_zip(file_path: str, workdir: Path, budget: _Budget, warnings: list[str]) -> list[str]:
    extracted: list[str] = []
    with zipfile.ZipFile(file_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = _safe_join(workdir, info.filename)
            if target is None:
                warnings.append(f"{info.filename}: path traversal attempt in ZIP — skipped.")
                continue
            if not budget.take(info.file_size):
                warnings.append(f"{Path(file_path).name}: archive size/file-count limit reached — remaining members skipped.")
                break
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
            except RuntimeError as exc:  # password-protected member
                warnings.append(f"{info.filename}: could not extract ({exc}) — possibly password-protected.")
                continue
            extracted.append(str(target))
    return extracted


def _extract_tar(file_path: str, workdir: Path, budget: _Budget, warnings: list[str]) -> list[str]:
    extracted: list[str] = []
    with tarfile.open(file_path) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue  # skip symlinks, dirs, devices, etc entirely
            target = _safe_join(workdir, member.name)
            if target is None:
                warnings.append(f"{member.name}: path traversal attempt in TAR — skipped.")
                continue
            if not budget.take(member.size):
                warnings.append(f"{Path(file_path).name}: archive size/file-count limit reached — remaining members skipped.")
                break
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with open(target, "wb") as dst:
                dst.write(src.read())
            extracted.append(str(target))
    return extracted
