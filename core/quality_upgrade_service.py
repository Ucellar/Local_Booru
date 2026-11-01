"""Crash-recoverable automatic replacement of inferior canonical media.

The state machine keeps the logical ``image_id`` stable.  It stages and verifies
new bytes, atomically swaps files on the same filesystem, commits canonical DB
metadata and site-MD5 alias snapshots in one SQLite transaction, then deletes
the retired bytes last.  Recovery is deterministic for every intermediate state.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable

from core.database.connection import db
from core.media_revision_service import create_revision_plan, commit_revision_metadata, rebind_media_paths
from core.upgrade_verification import MediaDescriptor, build_verification_plan, compare_quality, ram_budget_allows


class QualityUpgradeRejected(RuntimeError):
    pass


class QualityUpgradeInterrupted(InterruptedError):
    pass


def _md5(path: Path, *, stop_check: Callable[[], bool] | None = None) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        while True:
            if stop_check and stop_check():
                raise QualityUpgradeInterrupted("quality upgrade cancelled")
            chunk = fh.read(4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _format(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def describe_media(path: str | Path, *, stop_check: Callable[[], bool] | None = None) -> MediaDescriptor:
    """Read bounded structural metadata without loading a full video into RAM."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    width = height = 0
    frame_count = 1
    duration = 0.0
    has_alpha = False
    is_animation = False
    is_video = _format(p) in {"mp4", "webm", "mkv", "mov", "avi"}
    if not is_video:
        try:
            from PIL import Image
            with Image.open(p) as image:
                width, height = map(int, image.size)
                frame_count = int(getattr(image, "n_frames", 1) or 1)
                is_animation = frame_count > 1
                has_alpha = "A" in str(image.mode or "") or "transparency" in image.info
                if is_animation:
                    # Bounded metadata walk: duration only, no frame pixel copies.
                    total_ms = 0
                    for index in range(min(frame_count, 10000)):
                        if stop_check and stop_check():
                            raise QualityUpgradeInterrupted("quality upgrade cancelled")
                        image.seek(index)
                        total_ms += int(image.info.get("duration") or 0)
                    duration = total_ms / 1000.0
        except QualityUpgradeInterrupted:
            raise
        except Exception as exc:
            raise QualityUpgradeRejected(f"new media cannot be decoded: {exc}") from exc
    else:
        # Prefer lightweight container metadata when OpenCV is available.  It
        # never retains more than one decoded frame here.
        try:
            import cv2
            cap = cv2.VideoCapture(str(p))
            try:
                if not cap.isOpened():
                    raise QualityUpgradeRejected("video container cannot be opened")
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                duration = frame_count / fps if frame_count > 0 and fps > 0 else 0.0
            finally:
                cap.release()
        except ImportError:
            pass
    return MediaDescriptor(
        path=str(p), md5=_md5(p, stop_check=stop_check), width=width, height=height,
        file_size=int(p.stat().st_size), format=_format(p), frame_count=frame_count,
        duration=duration, has_alpha=has_alpha, is_video=is_video,
        is_animation=is_animation,
    )


def _same_media_identity(old: MediaDescriptor, new: MediaDescriptor, *, identity_basis: str, identity_verified: bool) -> tuple[bool, str]:
    basis = str(identity_basis or "").strip()
    if identity_verified and basis:
        return True, basis
    if old.md5 and new.md5 and old.md5.lower() == new.md5.lower():
        return True, "exact_md5"
    return False, "strong remote identity required"


def _sibling_path(canonical: Path, revision_id: int, kind: str, suffix: str | None = None) -> Path:
    ext = suffix if suffix is not None else canonical.suffix
    return canonical.with_name(f".{canonical.name}.lb-{kind}-{revision_id}{ext}")


def _fsync_file(path: Path) -> None:
    with path.open("rb") as fh:
        os.fsync(fh.fileno())


def _fsync_parent(path: Path) -> None:
    try:
        flags = getattr(os, "O_DIRECTORY", 0)
        if flags:
            fd = os.open(str(path.parent), flags)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except Exception:
        pass


def _mark(settings: dict, revision_id: int, **values: Any) -> None:
    if not values:
        return
    allowed = {
        "status", "staging_path", "retired_path", "swap_state", "verified_identity",
        "verified_quality", "recovery_action", "updated_at", "verified_at", "error",
        "old_file_deleted", "new_md5", "new_path", "new_width", "new_height",
        "new_file_size", "new_format", "quality_basis", "identity_basis", "journal_id", "final_path",
    }
    items = [(k, v) for k, v in values.items() if k in allowed]
    if "updated_at" not in values:
        items.append(("updated_at", int(time.time())))
    sql = "UPDATE media_revisions SET " + ",".join(f"{k}=?" for k, _ in items) + " WHERE revision_id=?"
    with db(settings, write=True) as con:
        con.execute(sql, tuple(v for _, v in items) + (int(revision_id),))


def execute_quality_upgrade(
    settings: dict,
    *,
    image_id: int,
    staged_path: str | Path,
    identity_basis: str,
    identity_verified: bool,
    source_site: str = "",
    source_post_id: str = "",
    stop_check: Callable[[], bool] | None = None,
    trace: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Verify, atomically swap, commit, then physically retire the old bytes."""
    trace = trace or (lambda _m: None)
    settings = dict(settings or {})
    staged_input = Path(staged_path)
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT * FROM images WHERE id=? AND deleted=0", (int(image_id),)).fetchone()
        if not row:
            raise KeyError(f"live image {image_id} not found")
        old_path = Path(str(row["path"]))
        old_md5_db = str(row["hash_md5"] or "").lower()
    if not old_path.is_file():
        raise FileNotFoundError(old_path)
    if not staged_input.is_file():
        raise FileNotFoundError(staged_input)

    old_desc = describe_media(old_path, stop_check=stop_check)
    new_desc = describe_media(staged_input, stop_check=stop_check)
    if old_md5_db and old_desc.md5 != old_md5_db:
        raise QualityUpgradeRejected("canonical file MD5 differs from database; run integrity recovery first")
    identity_ok, resolved_identity = _same_media_identity(
        old_desc, new_desc, identity_basis=identity_basis, identity_verified=identity_verified
    )
    if not identity_ok:
        raise QualityUpgradeRejected(resolved_identity)
    old_plan = build_verification_plan(old_desc, settings)
    new_plan = build_verification_plan(new_desc, settings)
    for plan in (old_plan, new_plan):
        allowed, reason = ram_budget_allows(plan, settings)
        if not allowed:
            raise QualityUpgradeRejected(f"verification RAM guard: {reason}")
    quality = compare_quality(old_desc, new_desc)
    min_confidence = float(settings.get("quality_upgrade_min_confidence", 0.85) or 0.85)
    if quality.decision != "better" or quality.confidence < min_confidence:
        raise QualityUpgradeRejected(
            f"new media is not an automatic upgrade: {quality.decision} confidence={quality.confidence:.3f}; "
            + "; ".join(quality.reasons)
        )

    with db(settings, write=True) as con:
        revision_id = create_revision_plan(
            con, image_id=int(image_id), old_md5=old_desc.md5, old_path=str(old_path),
            new_md5=new_desc.md5, new_path=str(old_path), replacement_reason="quality_upgrade",
            identity_basis=resolved_identity, quality_basis="; ".join(quality.reasons),
            source_site=source_site, source_post_id=source_post_id,
            old_width=old_desc.width, old_height=old_desc.height, old_file_size=old_desc.file_size,
            old_format=old_desc.format, new_width=new_desc.width, new_height=new_desc.height,
            new_file_size=new_desc.file_size, new_format=new_desc.format,
        )
    incoming = _sibling_path(old_path, revision_id, "incoming", staged_input.suffix)
    retired = _sibling_path(old_path, revision_id, "retired", old_path.suffix)
    journal = json.dumps({"canonical": str(old_path), "incoming": str(incoming), "retired": str(retired)}, ensure_ascii=False)
    _mark(
        settings, revision_id, status="staging", staging_path=str(incoming), retired_path=str(retired),
        swap_state="planned", verified_identity=1, verified_quality=1,
        verified_at=int(time.time()), journal_id=journal,
    )
    trace(f"QUALITY UPGRADE DETECTED: image={image_id} revision={revision_id} old={old_desc.width}x{old_desc.height}/{old_desc.md5} new={new_desc.width}x{new_desc.height}/{new_desc.md5}")

    try:
        if stop_check and stop_check():
            raise QualityUpgradeInterrupted("quality upgrade cancelled")
        # Copy into the canonical directory so every rename below is atomic on
        # the same filesystem.  The caller's original staging file is untouched.
        incoming.parent.mkdir(parents=True, exist_ok=True)
        with staged_input.open("rb") as src, incoming.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
            dst.flush(); os.fsync(dst.fileno())
        if _md5(incoming, stop_check=stop_check) != new_desc.md5:
            raise IOError("incoming staging MD5 changed during copy")
        _mark(settings, revision_id, status="verified", swap_state="incoming_ready")
        trace(f"QUALITY UPGRADE STAGED: image={image_id} revision={revision_id} path={incoming}")

        os.replace(str(old_path), str(retired))
        _fsync_parent(old_path)
        _mark(settings, revision_id, status="swapping", swap_state="old_retired")
        trace(f"QUALITY UPGRADE OLD RETIRED: image={image_id} revision={revision_id} path={retired}")

        os.replace(str(incoming), str(old_path))
        _fsync_file(old_path); _fsync_parent(old_path)
        _mark(settings, revision_id, status="swapped", swap_state="new_canonical")
        trace(f"QUALITY UPGRADE SWAPPED: image={image_id} revision={revision_id} canonical={old_path}")

        with db(settings, write=True) as con:
            # Re-read under the write transaction: another operation may not
            # silently rebind this logical image between planning and commit.
            current = con.execute("SELECT path,hash_md5 FROM images WHERE id=?", (int(image_id),)).fetchone()
            if not current or str(current["path"]) != str(old_path) or str(current["hash_md5"] or "").lower() != old_desc.md5:
                raise QualityUpgradeRejected("canonical database row changed during upgrade")
            commit_revision_metadata(
                con, revision_id=revision_id, image_id=image_id,
                new_md5=new_desc.md5, new_path=str(old_path), tombstone_old=True, physical_deleted=False,
            )
            con.execute(
                "UPDATE images SET size_bytes=?,width=?,height=?,mtime_ns=?,is_video=? WHERE id=?",
                (new_desc.file_size, new_desc.width, new_desc.height, int(old_path.stat().st_mtime_ns), 1 if new_desc.is_video else 0, int(image_id)),
            )
            con.execute(
                "UPDATE media_revisions SET swap_state='db_committed',updated_at=? WHERE revision_id=?",
                (int(time.time()), revision_id),
            )
        trace(f"QUALITY UPGRADE COMMITTED: image={image_id} revision={revision_id} md5={new_desc.md5}")

        retired.unlink()
        _fsync_parent(old_path)
        with db(settings, write=True) as con:
            con.execute(
                "UPDATE media_revisions SET old_file_deleted=1,swap_state='retired_deleted',updated_at=? WHERE revision_id=?",
                (int(time.time()), revision_id),
            )
            con.execute(
                "UPDATE retired_media_tombstones SET physical_deleted=1 WHERE revision_id=?",
                (revision_id,),
            )
        trace(f"QUALITY UPGRADE RETIRED DELETED: image={image_id} revision={revision_id} old_md5={old_desc.md5}")
        return {
            "ok": True, "image_id": int(image_id), "revision_id": revision_id,
            "old_md5": old_desc.md5, "new_md5": new_desc.md5,
            "canonical_path": str(old_path), "old_file_deleted": True,
            "quality": asdict(quality),
        }
    except BaseException as exc:
        _mark(settings, revision_id, error=str(exc)[:4000])
        try:
            recover_quality_upgrade(settings, revision_id=revision_id, trace=trace)
        except Exception as recovery_exc:
            _mark(settings, revision_id, recovery_action=f"recovery_failed:{recovery_exc}")
        raise


def recover_quality_upgrade(settings: dict, *, revision_id: int, trace: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Recover one interrupted state machine without guessing from timestamps."""
    trace = trace or (lambda _m: None)
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT * FROM media_revisions WHERE revision_id=?", (int(revision_id),)).fetchone()
        if not row:
            raise KeyError(revision_id)
        row = dict(row)
        image = con.execute("SELECT path,hash_md5 FROM images WHERE id=?", (int(row["image_id"]),)).fetchone()
    if str(row.get("swap_state") or "") == "renaming":
        return _recover_rename(settings, row, dict(image) if image else None, trace)
    canonical = Path(str(row["old_path"] or (image["path"] if image else "")))
    incoming = Path(str(row.get("staging_path") or "")) if row.get("staging_path") else None
    retired = Path(str(row.get("retired_path") or "")) if row.get("retired_path") else None
    old_md5 = str(row.get("old_md5") or "").lower()
    new_md5 = str(row.get("new_md5") or "").lower()
    db_md5 = str(image["hash_md5"] or "").lower() if image else ""

    def digest(path: Path | None) -> str:
        return _md5(path) if path and path.is_file() else ""

    canonical_md5 = digest(canonical)
    retired_md5 = digest(retired)
    incoming_md5 = digest(incoming)
    action = "none"

    if db_md5 == new_md5:
        # DB commit is authoritative: preserve/restore new canonical, then retire old.
        if canonical_md5 != new_md5 and incoming_md5 == new_md5:
            os.replace(str(incoming), str(canonical)); action = "restore_new_from_incoming"
        if digest(canonical) != new_md5:
            raise RuntimeError("committed revision has no recoverable new canonical bytes")
        if retired and retired.is_file() and retired_md5 == old_md5:
            retired.unlink(); action = (action + "+delete_retired").strip("+")
        if incoming and incoming.is_file():
            incoming.unlink(missing_ok=True)
        with db(settings, write=True) as con:
            con.execute("UPDATE media_revisions SET status='committed',old_file_deleted=1,swap_state='retired_deleted',recovery_action=?,updated_at=? WHERE revision_id=?", (action or "finalize_committed", int(time.time()), int(revision_id)))
            con.execute("UPDATE retired_media_tombstones SET physical_deleted=1 WHERE revision_id=?", (int(revision_id),))
    else:
        # DB still references old bytes: rollback filesystem to old canonical.
        if canonical_md5 == new_md5:
            canonical.unlink()
        if retired and retired.is_file() and retired_md5 == old_md5:
            os.replace(str(retired), str(canonical)); action = "restore_old_from_retired"
        elif digest(canonical) != old_md5:
            raise RuntimeError("uncommitted revision has no recoverable old canonical bytes")
        if incoming and incoming.is_file():
            incoming.unlink(missing_ok=True)
        with db(settings, write=True) as con:
            con.execute("UPDATE media_revisions SET status='rolled_back',swap_state='rolled_back',recovery_action=?,updated_at=? WHERE revision_id=?", (action or "rollback_verified", int(time.time()), int(revision_id)))
    _fsync_parent(canonical)
    trace(f"QUALITY UPGRADE RECOVERED: revision={revision_id} action={action or 'none'} db_md5={db_md5}")
    return {"revision_id": int(revision_id), "action": action or "none", "db_md5": db_md5}


# ── (v5.44.13) the name of the new file ────────────────────────────────────────────
_LIBRARY_TAIL = re.compile(r"__[0-9a-f]{12}$", re.I)


def _free_name(folder: Path, name: str) -> Path:
    """``name`` in ``folder``, or ``<stem>_N<ext>`` when a file is there already (another card's: never over it)."""
    target = folder / name
    stem, ext = Path(name).stem, Path(name).suffix
    n = 1
    while target.exists() and n < 10000:
        target = folder / f"{stem}_{n}{ext}"
        n += 1
    return target


def rename_upgraded(settings: dict, *, revision_id: int, new_name: str,
                    trace: Callable[[str], None] | None = None) -> dict[str, Any]:
    """(v5.44.13) The file of a committed upgrade gets the original's name: ``new_name`` is the name it came with (the
    card's ``original_file_name``), the library's name is made of it as every library name is
    (``content_addressed_name``: «<name>__<MD5>.<ext>») — an upgraded sample was «sample_<key>__<old MD5>.jpg» with the
    original's PNG inside.  The plan is written first (``swap_state='renaming'``, ``final_path``), then the file moves
    in its folder (never over another file: ``move_managed``), then the base follows in one transaction
    (``images`` and every journal that names the file: ``rebind_media_paths``); a crash in between is finished at the
    next start (:func:`recover_quality_upgrade`).  {"path": the card's file afterwards, "renamed": it has the new name,
    "final": nothing to try again (the revision is not this card's last, the name is the same), "why": words}."""
    trace = trace or (lambda _m: None)
    from core.services.media_storage_service import content_addressed_name, move_managed
    with db(settings, readonly=True) as con:
        rev = con.execute("SELECT * FROM media_revisions WHERE revision_id=?", (int(revision_id),)).fetchone()
        image = con.execute("SELECT id,path,hash_md5,current_revision_id FROM images WHERE id=? AND deleted=0",
                            (int(rev["image_id"]),)).fetchone() if rev else None
    if not rev or not image:
        return {"path": str(image["path"]) if image else "", "renamed": False, "final": True, "why": "нет карточки"}
    current = Path(str(image["path"]))
    md5 = str(image["hash_md5"] or "").lower()
    if (str(rev["status"] or "") != "committed" or int(image["current_revision_id"] or 0) != int(revision_id)
            or md5 != str(rev["new_md5"] or "").lower()):
        return {"path": str(current), "renamed": False, "final": True, "why": "у карточки уже другой файл"}
    plain = Path(str(new_name or "")).name.strip()
    if not plain or not Path(plain).suffix:
        return {"path": str(current), "renamed": False, "final": True, "why": "нет имени"}
    wanted = content_addressed_name(plain, md5)
    if wanted.lower() == current.name.lower():
        return {"path": str(current), "renamed": True, "final": True, "why": ""}
    if not current.is_file():
        return {"path": str(current), "renamed": False, "final": False, "why": "файла нет на месте"}
    target = _free_name(current.parent, wanted)
    with db(settings, readonly=True) as con:
        while con.execute("SELECT 1 FROM images WHERE path=? LIMIT 1", (str(target),)).fetchone():
            target = _free_name(current.parent, f"{target.stem}_1{target.suffix}")
    _mark(settings, int(revision_id), swap_state="renaming", final_path=str(target))
    try:
        if not move_managed(settings, current, target, operation="quality_upgrade.rename"):
            raise PermissionError("the file is not the library's to move")
    except Exception as exc:  # a file held open (Windows): the card keeps the old name, the next round tries again
        _mark(settings, int(revision_id), swap_state="retired_deleted", final_path=str(current),
              recovery_action=f"rename_failed:{type(exc).__name__}: {exc}"[:500])
        trace(f"QUALITY UPGRADE RENAME FAILED: revision={revision_id} {type(exc).__name__}: {exc}")
        return {"path": str(current), "renamed": False, "final": False, "why": f"{type(exc).__name__}: {exc}"}
    _rename_in_base(settings, rev, current, target, md5, original_name=plain, action="renamed")
    trace(f"QUALITY UPGRADE RENAMED: image={rev['image_id']} revision={revision_id} {current.name} -> {target.name}")
    return {"path": str(target), "renamed": True, "final": True, "why": ""}


def _rename_in_base(settings: dict, rev, old: Path, new: Path, md5: str, *, original_name: str, action: str) -> None:
    st = new.stat()
    with db(settings, write=True) as con:
        con.execute(
            "UPDATE images SET path=?, file_name=?, original_file_name=?, mtime_ns=?, size_bytes=? WHERE id=?",
            (str(new), new.name, original_name, int(st.st_mtime_ns), int(st.st_size), int(rev["image_id"])))
        rebind_media_paths(con, image_id=int(rev["image_id"]), old_path=str(old), new_path=str(new), new_md5=md5)
        con.execute(
            "UPDATE media_revisions SET swap_state='retired_deleted', new_path=?, final_path=?, recovery_action=?, "
            "updated_at=? WHERE revision_id=?",
            (str(new), str(new), action, int(time.time()), int(rev["revision_id"])))


def _recover_rename(settings: dict, row: dict, image: dict | None, trace: Callable[[str], None]) -> dict[str, Any]:
    """A rename cut short: the base follows a file that moved; a file that did not move keeps its name."""
    revision_id = int(row["revision_id"])
    target = Path(str(row.get("final_path") or ""))
    current = Path(str(image["path"])) if image else None
    md5 = str((image or {}).get("hash_md5") or "").lower()
    if current is not None and str(current) == str(target):
        action = "renamed"
    elif (current is not None and str(target) and not current.exists() and target.is_file()
          and _md5(target) == md5):
        original = _LIBRARY_TAIL.sub("", target.stem) + target.suffix
        _rename_in_base(settings, row, current, target, md5, original_name=original, action="rename_finished")
        trace(f"QUALITY UPGRADE RECOVERED: revision={revision_id} action=rename_finished")
        return {"revision_id": revision_id, "action": "rename_finished", "db_md5": md5}
    else:
        action = "rename_not_done"
    with db(settings, write=True) as con:
        con.execute("UPDATE media_revisions SET swap_state='retired_deleted', recovery_action=?, updated_at=? "
                    "WHERE revision_id=?", (action, int(time.time()), revision_id))
    trace(f"QUALITY UPGRADE RECOVERED: revision={revision_id} action={action}")
    return {"revision_id": revision_id, "action": action, "db_md5": md5}


def recover_incomplete_quality_upgrades(settings: dict, *, trace: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    with db(settings, readonly=True) as con:
        rows = con.execute(
            "SELECT revision_id FROM media_revisions WHERE swap_state NOT IN ('none','retired_deleted','rolled_back') ORDER BY revision_id"
        ).fetchall()
    return [recover_quality_upgrade(settings, revision_id=int(row[0]), trace=trace) for row in rows]
