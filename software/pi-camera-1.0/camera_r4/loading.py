"""New Cam launch cover and first-live-frame handoff."""
import struct
import time
from pathlib import Path


def logo_dimensions(path):
    header = Path(path).read_bytes()[:26]
    if (len(header) != 26 or header[:8] != b'\x89PNG\r\n\x1a\n' or
            header[8:16] != b'\x00\x00\x00\rIHDR' or header[24:26] != b'\x08\x02'):
        raise ValueError('New Cam logo must be an 8-bit RGB PNG')
    width, height = struct.unpack('>II', header[16:24])
    if (width, height) != (480, 800):
        raise ValueError('New Cam logo must be 480x800')
    return width, height


def create_loading_window(path):
    """Create a black full-screen-capable Qt widget without rewriting the PNG."""
    logo_dimensions(path)
    from PyQt5.QtCore import Qt, QRectF
    from PyQt5.QtGui import QColor, QPainter, QPixmap
    from PyQt5.QtWidgets import QWidget

    class LoadingWindow(QWidget):
        def __init__(self, image_path):
            super().__init__()
            self.setWindowTitle('New Cam 1.0')
            self.image = QPixmap(str(image_path))
            if self.image.isNull() or (self.image.width(),self.image.height()) != (480,800):
                raise ValueError('New Cam logo could not be decoded')
            self.setAttribute(Qt.WA_AcceptTouchEvents, False)
            self.resize(480,800)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#000000'))
            scale = min(self.width()/480, self.height()/800)
            width, height = 480*scale, 800*scale
            target = QRectF((self.width()-width)/2, (self.height()-height)/2, width, height)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawPixmap(target, self.image, QRectF(0,0,480,800))
            painter.end()

    return LoadingWindow(path)


class StartupGate:
    """Present camera only when an actual preview is prepared, then release cover."""
    def __init__(self, window, camera, splash, app, timeout_seconds=45):
        self.window=window
        self.camera=camera
        self.splash=splash
        self.app=app
        self.timeout_seconds=timeout_seconds
        self.started_at=time.monotonic()
        self.shown=False
        self.failed=False
        self.finished=False
        self.failure_reason=None
        if hasattr(self.app,'setQuitOnLastWindowClosed'):
            self.app.setQuitOnLastWindowClosed(False)

    def poll(self):
        if self.finished:
            return
        if self.failed:
            if self.window.exit_complete:
                self.finished=True
                self.splash.close()
                self.app.exit(1)
            return
        if self.window.exit_complete:
            self.finished=True
            self.splash.close()
            self.app.exit(0)
            return
        frame=self.camera.snapshot()
        reason=frame.error if frame.error else ('startup timeout' if time.monotonic()-self.started_at>=self.timeout_seconds else None)
        if reason:
            self.failed=True
            self.failure_reason=str(reason)
            self.window.page='error'
            self.window.message='相机启动失败：'+self.failure_reason
            self.window.showFullScreen()
            self.shown=True
            self.splash.close()
            self.window.request_exit(confirmed=True)
            return
        if not self.shown and self.window.first_preview_ready:
            self.window.on_first_preview_painted=self.first_frame_painted
            self.window.showFullScreen()
            self.shown=True

    def first_frame_painted(self):
        if self.finished or self.failed:
            return
        self.finished=True
        self.splash.close()
        if hasattr(self.app,'setQuitOnLastWindowClosed'):
            self.app.setQuitOnLastWindowClosed(True)
