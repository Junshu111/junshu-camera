"""Durable, verified JPEG commits and explicit, fingerprinted deletion plans."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import uuid

from PIL import Image, UnidentifiedImageError


@dataclass(frozen=True)
class PhotoRecord:
    path: Path
    width: int
    height: int
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class DeletePlan:
    paths: tuple
    total_bytes: int
    fingerprints: tuple


class DeleteConfirmationRequired(ValueError):
    pass


class DeleteFailed(OSError):
    """Files removed before an I/O failure remain removed; the UI must refresh."""
    def __init__(self, removed_paths, failed_path, cause):
        self.removed_paths = tuple(removed_paths)
        self.failed_path = Path(failed_path)
        super().__init__('删除未全部完成；已完成{}个，失败文件{}：{}'.format(
            len(self.removed_paths), self.failed_path.name, cause))


class CommitUncertain(OSError):
    """Complete JPEG exists, but directory sync did not confirm durability."""
    def __init__(self, path, cause):
        self.path = Path(path)
        super().__init__('JPEG已写入，但目录同步失败；保存结果未确认：{}；{}'.format(path, cause))


def read_photo(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('照片必须是普通文件且不可为符号链接')
    # Opening once with O_NOFOLLOW also protects the final component from a symlink swap.
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('照片必须是普通文件')
        with Image.open(source) as image:
            if image.format != 'JPEG':
                raise ValueError('File is not JPEG')
            image.load()
            width, height = image.size
        source.seek(0)
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(65536), b''):
            digest.update(block)
        count = os.fstat(source.fileno()).st_size
    return PhotoRecord(path, width, height, count, digest.hexdigest())


class JPEGStore:
    def __init__(self, directory, *, unlink=None):
        raw = Path(directory).expanduser()
        if raw.is_symlink():
            raise ValueError('照片目录不可为符号链接')
        self.directory = raw.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._unlink = unlink if unlink is not None else Path.unlink

    def commit(self, image, metadata=None):
        with self._lock:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            destination = self.directory / ('JC_{}_{}.JPG'.format(stamp, uuid.uuid4().hex))
            if destination.exists():
                raise FileExistsError(str(destination))
            fd, temporary = tempfile.mkstemp(prefix='.pending-', suffix='.tmp', dir=str(self.directory))
            pending = Path(temporary)
            # Failed files stay available as .pending-* and are excluded from the gallery.
            with os.fdopen(fd, 'wb') as output:
                kwargs = {}
                if metadata:
                    exif = Image.Exif()
                    exif[270] = json.dumps(metadata, ensure_ascii=True, default=str)
                    kwargs['exif'] = exif
                image.convert('RGB').save(output, format='JPEG', quality=95, **kwargs)
                output.flush()
                os.fsync(output.fileno())
            checked = read_photo(pending)
            os.rename(str(pending), str(destination))
            try:
                directory_fd = os.open(str(self.directory), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as error:
                raise CommitUncertain(destination, error) from error
            return PhotoRecord(destination, checked.width, checked.height, checked.byte_count, checked.sha256)

    def list_photos(self):
        result = []
        for path in self.directory.iterdir():
            if not path.name.startswith('JC_') or path.suffix.upper() not in ('.JPG', '.JPEG') or path.is_symlink() or not path.is_file():
                continue
            try:
                result.append(read_photo(path))
            except (UnidentifiedImageError, ValueError):
                continue
            except OSError as error:
                if error.errno is not None:
                    raise
                continue
        return sorted(result, key=lambda photo: (photo.path.stat().st_mtime_ns, photo.path.name), reverse=True)

    def _validated_photo(self, path):
        path = Path(path).expanduser()
        if not path.is_absolute():
            path = self.directory / path
        # Resolve only the parent: resolving the file itself would conceal symlinks.
        if path.parent.resolve() != self.directory or path.is_symlink():
            raise ValueError('只可删除照片目录内的普通照片')
        if not path.name.startswith('JC_') or path.suffix.upper() not in ('.JPG', '.JPEG'):
            raise ValueError('不可删除非照片文件')
        return read_photo(self.directory / path.name)

    def _fingerprint(self, photo):
        info = photo.path.stat(follow_symlinks=False)
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, photo.sha256)

    def plan_delete(self, paths):
        with self._lock:
            records = [self._validated_photo(path) for path in paths]
            canonical = tuple(record.path for record in records)
            if not canonical or len(set(canonical)) != len(canonical):
                raise ValueError('删除清单必须非空且不可重复')
            return DeletePlan(canonical, sum(record.byte_count for record in records),
                              tuple(self._fingerprint(record) for record in records))

    def delete(self, plan, confirmed=False):
        if confirmed is not True:
            raise DeleteConfirmationRequired('删除前必须展示文件、容量和保留数量并明确确认')
        if not isinstance(plan, DeletePlan):
            raise TypeError('DeletePlan is required')
        with self._lock:
            records = [self._validated_photo(path) for path in plan.paths]
            fingerprints = tuple(self._fingerprint(record) for record in records)
            if (not records or len(set(plan.paths)) != len(plan.paths)
                    or fingerprints != plan.fingerprints
                    or sum(record.byte_count for record in records) != plan.total_bytes):
                raise ValueError('照片已变动，请重新确认删除清单')
            removed = []
            for record in records:
                try:
                    self._unlink(record.path)
                except OSError as error:
                    raise DeleteFailed(removed, record.path, error) from error
                removed.append(record.path)
            return removed
