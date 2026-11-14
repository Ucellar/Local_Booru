"""Content-based numbering of a managed nhentai copy, with recoverable original bytes and short resumable rounds.

The caller checks the configured source/library boundary. Here the exact gallery prefix, links and occupied names
are checked too. Verified transaction backups are retained; only a complete unambiguous mapping changes names.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path
from core.file_safety import atomic_write_bytes

_LOCK = threading.RLock()
SECONDS = 12.0
MAX_PAGES = 2500


def _plain(path: Path) -> None:
    """Refuse symlinks and Windows junctions, including dangling links."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise OSError("ссылки вместо страниц или служебных папок не изменяются")


def _folder(folder: Path, number: int | None = None) -> None:
    prefix = rf"^nhentai-{int(number)}(?!\d)" if number is not None else r"^nhentai-\d+(?!\d)"
    if not re.match(prefix, folder.name, re.I) or not folder.is_dir():
        raise OSError("нумерация меняется только в копии nhentai с тем же номером галереи")
    for path in (folder, folder / ".lb", folder / ".lb" / "page-order"):
        _plain(path)


def _save(path: Path, data: dict) -> None:
    _plain(path)
    _plain(path.with_name(path.name + ".part"))
    atomic_write_bytes(path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))


def _read(path: Path) -> dict:
    _plain(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise OSError("журнал сверки страниц повреждён — файлы сохранены без изменений") from exc
    if not isinstance(data, dict):
        raise OSError("неверный журнал сверки страниц")
    return data


def _digest(path: Path) -> str:
    _plain(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _name(name: str) -> str:
    if not name or Path(name).name != name or name in (".", "..") or any(c in name for c in '/\\:') or \
            name != name.rstrip(". "):
        raise ValueError("неверное имя страницы")
    return name


def _move(source: Path, dest: Path) -> None:
    """Publish complete bytes without replacing an occupied name, on either Windows or POSIX."""
    _plain(source)
    _plain(dest)
    if os.name == "nt":
        source.rename(dest)                 # Windows rename refuses an existing name.
    else:
        os.link(source, dest)               # POSIX rename would overwrite a file arriving meanwhile.
        source.unlink()


def _backup(source: Path, dest: Path, digest: str) -> None:
    with source.open("rb") as src, dest.open("xb") as out:
        shutil.copyfileobj(src, out)
        out.flush()
        os.fsync(out.fileno())
    shutil.copystat(source, dest)
    if _digest(dest) != digest:
        raise OSError("резервная копия страницы не совпала")


def recover(folder: Path) -> bool:
    """Restore original names after a crash, only if all current files still contain known bytes."""
    folder = Path(folder)
    with _LOCK:
        _folder(folder)
        manifest = folder / ".lb" / "page-order.json"
        data = _read(manifest)
        if data.get("state") != "moving":
            return False
        base = folder / ".lb" / "page-order" / _name(str(data.get("transaction") or ""))
        for path in (base, base / "backup"):
            _plain(path)
        entries = data.get("files") or []
        if not entries:
            raise OSError("пустой журнал переименования страниц")
        allowed: dict = {}
        for row in entries:
            old, new = _name(row["old"]), _name(row["new"])
            if _digest(base / "backup" / old) != row["sha256"]:
                raise OSError("резервная копия страницы не совпала — восстановление остановлено")
            for name in (old, new):
                allowed.setdefault(name.casefold(), set()).add(row["sha256"])
        existing = []
        for path in folder.iterdir():
            hashes = allowed.get(path.name.casefold())
            if hashes is None:
                continue
            _plain(path)
            if path.exists():
                if not path.is_file() or _digest(path) not in hashes:
                    raise OSError("страница изменилась после начала переименования — она сохранена без изменений")
                existing.append((path.name, path))
        rescue = base / ("interrupted-" + uuid.uuid4().hex)
        rescue.mkdir()
        for name, path in existing:
            if _digest(path) not in allowed[name.casefold()]:
                raise OSError("страница изменилась во время восстановления — она сохранена без изменений")
            _move(path, rescue / name)
        for row in entries:
            temp = rescue / ("restore-" + uuid.uuid4().hex)
            _backup(base / "backup" / row["old"], temp, row["sha256"])
            _move(temp, folder / row["old"])
        _save(manifest, {**data, "state": "restored"})
        return True


def normalize(folder: Path, mapping: dict, *, digests: dict | None = None, stop_check=None) -> bool:
    """Apply a complete mapping; original bytes survive both errors and process termination."""
    folder = Path(folder)
    stop = stop_check or (lambda: False)
    with _LOCK:
        if stop():
            return False
        recover(folder)
        mapping = {_name(str(old)): _name(str(new)) for old, new in mapping.items()}
        if len({n.casefold() for n in mapping}) != len(mapping) or \
                len({n.casefold() for n in mapping.values()}) != len(mapping):
            raise ValueError("две страницы получили один номер")
        occupied = {p.name.casefold(): p.name for p in folder.iterdir()}
        originals = {n.casefold() for n in mapping}
        for old, new in mapping.items():
            _plain(folder / old)
            _plain(folder / new)
            if not (folder / old).is_file():
                raise OSError("страница не найдена")
            if new.casefold() in occupied and new.casefold() not in originals:
                raise OSError("имя страницы занято другим файлом")
            if digests is not None and _digest(folder / old) != digests.get(old):
                raise OSError("страница изменилась после сверки — копия сохранена без изменений")
        if not any(old != new for old, new in mapping.items()):
            return False
        if stop():
            return False
        transaction = uuid.uuid4().hex
        base = folder / ".lb" / "page-order" / transaction
        backups, staged = base / "backup", base / "staged"
        backups.mkdir(parents=True)
        staged.mkdir()
        entries = []
        for old, new in mapping.items():
            if stop():
                return False
            digest = _digest(folder / old)
            if digests is not None and digest != digests.get(old):
                raise OSError("страница изменилась после сверки — копия сохранена без изменений")
            _backup(folder / old, backups / old, digest)
            entries.append({"old": old, "new": new, "sha256": digest})
        manifest = folder / ".lb" / "page-order.json"
        data = {"state": "moving", "transaction": transaction, "files": entries}
        if stop():
            return False
        _save(manifest, data)                 # Durable before the first original name changes.
        try:
            for row in entries:
                if _digest(folder / row["old"]) != row["sha256"]:
                    raise OSError("страница изменилась во время подготовки")
                if stop():
                    raise InterruptedError("сверка остановлена")
                _move(folder / row["old"], staged / row["old"])
            for row in entries:
                if stop():
                    raise InterruptedError("сверка остановлена")
                _move(staged / row["old"], folder / row["new"])
            if stop():
                raise InterruptedError("сверка остановлена")
            _save(manifest, {**data, "state": "complete"})
        except InterruptedError:
            recover(folder)
            return False
        except OSError:
            recover(folder)
            raise
        return True


def match(marks: dict, remote: dict, limit: int) -> dict:
    from core.parser_manga_pages import flat
    from core.tagger.hashing import phash_distance
    out = {}
    for name, mark in marks.items():
        if not mark or flat(mark):
            return {}
        ranked = sorted((phash_distance(mark, other), int(number)) for number, other in remote.items()
                        if other and not flat(other))
        if not ranked or ranked[0][0] > limit or len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 3:
            return {}
        out[name] = ranked[0][1]
    return out if len(set(out.values())) == len(out) else {}


def prepare(folder: Path, number: int, total: int, page_mark, limit: int, *, stop_check=None) -> dict:
    from core import manga_detect as MD, manga_download as D, parser_manga as PM
    folder = Path(folder)
    stop = stop_check or (lambda: False)
    deadline = time.monotonic() + SECONDS
    if total <= 0 or total > MAX_PAGES:
        return {"state": "unmatched", "words": "слишком большая галерея для автоматической сверки нумерации"}
    with _LOCK:
        _folder(folder, number)
        if stop():
            return {"state": "pending", "words": "сверка остановлена"}
        recover(folder)
        paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in MD.IMAGES and
                       p.stem.lower() not in MD.ASIDE)
        kinds = set(D.KINDS.values()) | {".jpeg", ".avif"}
        if len(paths) < 2 or len(paths) > total or any(not p.is_file() or p.suffix.lower() not in kinds for p in paths):
            return {"state": "unmatched", "words": "недостаточно однозначных страниц для сверки нумерации"}
        if any(p.name != ".lb" and p.is_dir() for p in folder.iterdir()):
            return {"state": "unmatched", "words": "страницы в подпапках — нумерация сохранена"}
        for path in paths:
            _plain(path)
        signature = [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in paths]
        cache_file = folder / ".lb" / "page-matches.json"
        cached = _read(cache_file)
        if cached.get("gallery") != number or cached.get("total") != total or cached.get("files") != signature:
            cached = {"gallery": number, "total": total, "files": signature, "local": {}, "remote": {}, "digests": {}}
        local, remote, digests = (cached.setdefault(key, {}) for key in ("local", "remote", "digests"))
        try:
            for path in paths:
                if stop() or time.monotonic() > deadline:
                    return {"state": "pending", "words": "сверяю нумерацию страниц по содержимому"}
                if path.name not in local or path.name not in digests:
                    content = path.read_bytes()
                    digests[path.name] = hashlib.sha256(content).hexdigest()
                    local[path.name] = PM._phash(content)
            for page in range(1, total + 1):
                if remote.get(str(page)):
                    continue
                if stop() or time.monotonic() > deadline:
                    return {"state": "pending", "words": f"сверяю нумерацию страниц: {len(remote)} из {total}"}
                mark = page_mark(page)
                if not mark:
                    return {"state": "unmatched", "words": "страница сайта недоступна или не различима — нумерация сохранена"}
                remote[str(page)] = mark
            if stop() or time.monotonic() > deadline:
                return {"state": "pending", "words": "сверяю нумерацию страниц по содержимому"}
            numbered = match(local, remote, min(limit, 6))
            if not numbered:
                return {"state": "unmatched", "words": "не все страницы удалось однозначно сопоставить — копия сохранена"}
            current = [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(folder.iterdir())
                       if p.suffix.lower() in MD.IMAGES and p.stem.lower() not in MD.ASIDE]
            if current != signature:
                return {"state": "pending", "words": "состав копии изменился — сверю страницы заново"}
            width = max(3, len(str(total)))
            mapping = {name: f"{page:0{width}d}{Path(name).suffix.lower()}" for name, page in numbered.items()}
            normalize(folder, mapping, digests=digests, stop_check=stop)
            if stop():
                return {"state": "pending", "words": "сверка остановлена"}
            return {"state": "ready", "words": "страницы сопоставлены по содержимому"}
        finally:
            _save(cache_file, cached)
