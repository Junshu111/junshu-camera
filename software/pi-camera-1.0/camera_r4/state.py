"""Validated R4 settings; session locks and self timer never survive a restart."""
from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class AppSettings:
    ratio: str = '9:16'
    zoom: int = 1
    rotation: int = 0
    mirror: bool = False
    fullscreen: bool = True
    sound: bool = True
    hdr: bool = False
    beauty: bool = False
    histogram: bool = True
    exposure_mode: str = 'auto'
    ev: float = 0.0
    shutter_us: int = 8000
    iso: int = 400
    iso_max: int = 800
    slowest_us: int = 16667
    wb_mode: str = 'auto'
    wb_kelvin: int = 5200
    wb_tint: int = 0
    self_timer: int = 0
    review_seconds: int = 3
    brightness: int = 60
    ael: bool = False
    locked_exposure_us: int = 0
    locked_gain: float = 0.0

    def __post_init__(self):
        choices = {'ratio': ('9:16', '1:1', '3:4', '16:9'),
                   'zoom': (1, 2), 'rotation': (0, 90, 180, 270),
                   'exposure_mode': ('auto', 'manual'), 'wb_mode': ('auto', 'manual'),
                   'self_timer': (0, 3, 5, 10), 'review_seconds': (0, 3, 10)}
        for name, valid in choices.items():
            value = getattr(self, name)
            if (isinstance(value, bool) or value not in valid
                    or (name in ('zoom', 'rotation', 'self_timer', 'review_seconds') and type(value) is not int)):
                raise ValueError('{} 设置无效'.format(name))
        for name in ('mirror', 'fullscreen', 'sound', 'hdr', 'beauty', 'histogram', 'ael'):
            if type(getattr(self, name)) is not bool:
                raise TypeError('{} 必须是布尔值'.format(name))
        ranges = {'ev': (-3, 3), 'shutter_us': (1, 30000000), 'iso': (100, 6400),
                  'iso_max': (100, 6400), 'slowest_us': (1, 30000000),
                  'wb_kelvin': (2000, 12000), 'wb_tint': (-100, 100),
                  'brightness': (1, 100), 'locked_exposure_us': (0, 30000000),
                  'locked_gain': (0, 64)}
        for name, (low, high) in ranges.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('{} 设置超出范围'.format(name))
            if name not in ('ev', 'locked_gain') and type(value) is not int:
                raise TypeError('{} 必须是整数'.format(name))


SESSION_FIELDS = frozenset(('ael', 'locked_exposure_us', 'locked_gain', 'self_timer'))
SETTINGS_VERSION = 4


def load_settings(path, legacy_path=None):
    path = Path(path).expanduser()
    if not path.exists():
        if legacy_path is None or not Path(legacy_path).expanduser().exists():
            return AppSettings()
        path = Path(legacy_path).expanduser()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('设置文件必须是对象')
    if data.get('version') == SETTINGS_VERSION:
        values = data.get('settings')
    elif data.get('version') == 1:
        values = dict(data.get('framing', {}))
        values['review_seconds'] = data.get('review_seconds', 3)
    else:
        raise ValueError('设置文件版本不支持')
    if not isinstance(values, dict):
        raise ValueError('设置内容无效')
    names = {field.name for field in fields(AppSettings)}
    if set(values) - names:
        raise ValueError('设置文件含未知字段')
    return AppSettings(**{name: value for name, value in values.items() if name not in SESSION_FIELDS})


def save_settings(path, settings):
    if not isinstance(settings, AppSettings):
        raise TypeError('AppSettings is required')
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.r4-settings-pending-', suffix='.json', dir=str(path.parent))
    # A failed pending write is retained for inspection. Never clean up files here.
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump({'version': SETTINGS_VERSION,
                   'settings': {key: value for key, value in asdict(settings).items() if key not in SESSION_FIELDS}},
                  output, ensure_ascii=False, allow_nan=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
