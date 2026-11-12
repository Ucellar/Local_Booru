"""A card's video played inside the page of the program window (why and how: webui/video_relay.py).

Qt Multimedia decodes the file (its FFmpeg plays MP4, MKV, MOV, AVI, WebM) and plays the sound; every new frame is
made into a JPEG of the size the page shows it at — in a thread of its own, never more than ``FPS`` a second, a
frame that comes while the one before is still being made replaces the waiting one — and handed to the relay, which
the page's picture reads.  The page's buttons come here through the bridge (ui/web_bridge.py ``_do_video``).
The frames come to this object in the window's thread (never a Python call in the decoder's thread: see ``__init__``).
What the player is told — open, play, pause, a place, the sound, stop — it is told from the window's event loop, in Qt
alone (``later``): a call made from Python holds the interpreter's lock, and a decoder thread destroying its objects
takes Qt's lock of a player's object first and then asks for the interpreter (PySide: is ``disconnectNotify``
Python's?) — the window waited for Qt's lock, the decoder for the interpreter, for ever (a check hung so in
``stop``).  Calls queued to the window's thread run in the order they were made.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

from PySide6.QtCore import (Q_ARG, QBuffer, QIODevice, QMetaObject, QObject, QRect, Qt, QThread, QTimer, QUrl, Signal,
                            Slot)

from webui.video_relay import RELAY, fit_size


def silent() -> bool:
    """(v5.44.145) ``LOCAL_BOORU_SILENT=1``: the program's players make no sound — they are not joined to the
    computer's speakers (the volume and «без звука» are kept and said as always).  The checks play real videos with
    the program's real players, and on the owner's computer that was heard (10.11.2026: «питон выдает высокочастотный
    меняющийся звук» — the test clips' tones at half the volume, each time every check was run there):
    ``run_checks.py`` sets it for every check, the checks with a window — for themselves."""
    return os.environ.get("LOCAL_BOORU_SILENT", "").strip().lower() in ("1", "true", "yes", "on")

_LOG = logging.getLogger("local_booru.video")

try:
    from PySide6.QtMultimedia import (QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoFrameFormat,
                                      QVideoSink)
except ImportError:                  # PySide6-Addons without Qt Multimedia
    QAudioOutput = QMediaMetaData = QMediaPlayer = QVideoFrame = QVideoFrameFormat = QVideoSink = None

FPS = 30
NO_PICTURE_AFTER = 8.0      # seconds of play without a single frame of a file with a picture: the player cannot show it
NO_PICTURE = ("Собственный проигрыватель программы не показывает картинку этого видео (например, AV1 он "
              "декодирует только видеокартой, которая это умеет)")
QUALITY = 84
START_TRIES = 4             # the jump to where an episode stopped last time: asked again if it did not take …
START_WAIT = 6.0            # … for this many seconds in all
# A frame for the whole screen is a big JPEG: a computer that cannot make them in time gets smaller ones (the page
# stretches them) — from the time a frame takes, never smaller than SMALLEST pixels
SMALLEST = 960 * 540
SLOW_FRAME = 1.0 / 24       # seconds a frame may take on average (a film or an anime: 24 frames a second) …
FAST_FRAME = 0.45 / 24      # … and below which the frames grow back
# The player's voice-over and subtitle tracks are its properties, not slots: ``later`` cannot set them, and setting
# them from Python is the call the decoder could deadlock with (see above).  A small QML object can — its functions
# are invokable, so the window's event loop calls them with no Python on the stack.  So are the words a video sink
# puts on its frames (``subtitleText``: the full-size player's picture draws them — a subtitle file's cue).
_TRACKS_QML = b"""import QtQml
QtObject {
    property QtObject player
    property QtObject sink
    function audio(n) { player.activeAudioTrack = n }
    function subtitle(n) { player.activeSubtitleTrack = n }
    function text(t) { if (sink) sink.subtitleText = t }
}
"""


class TrackSetter:
    """Sets a QMediaPlayer's voice-over and subtitle tracks (and the words of ``sink``'s frames) from the window's
    event loop (``_TRACKS_QML``)."""

    def __init__(self, player, parent, sink=None):
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        self._engine = QQmlEngine(parent)
        self._component = QQmlComponent(self._engine)
        self._component.setData(_TRACKS_QML, QUrl())
        self._object = self._component.create()
        if self._object is None:
            raise RuntimeError("; ".join(e.toString() for e in self._component.errors()) or "QML не создался")
        self._object.setParent(self._engine)
        self._object.setProperty("player", player)
        if sink is not None:
            self._object.setProperty("sink", sink)

    def audio(self, n: int) -> None:
        QMetaObject.invokeMethod(self._object, "audio", Qt.ConnectionType.QueuedConnection, Q_ARG("QVariant", int(n)))

    def subtitle(self, n: int) -> None:
        QMetaObject.invokeMethod(self._object, "subtitle", Qt.ConnectionType.QueuedConnection,
                                 Q_ARG("QVariant", int(n)))

    def text(self, words: str) -> None:
        QMetaObject.invokeMethod(self._object, "text", Qt.ConnectionType.QueuedConnection,
                                 Q_ARG("QVariant", str(words or "")))


def track_list(items) -> list[dict]:
    """The player's tracks for the page: {"name" — «JAM (русский)», "title", "lang"}."""
    from core import video_tracks
    out = []
    for n, meta in enumerate(items or [], 1):
        title = str(meta.stringValue(QMediaMetaData.Key.Title) or "")
        lang = video_tracks.language_name(str(meta.stringValue(QMediaMetaData.Key.Language) or ""))
        out.append({"name": video_tracks.track_name(title, lang, n), "title": title, "lang": lang})
    return out


def draw_subtitle(image, text: str):
    """The words of the moment over the bottom of the picture: white on a dark box, a size to the picture's."""
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
    if image.format() not in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32,
                              QImage.Format.Format_ARGB32_Premultiplied):
        image = image.convertToFormat(QImage.Format.Format_RGB32)
    width, height = image.width(), image.height()
    font = QFont()
    font.setPixelSize(max(11, round(height / 17)))
    font.setBold(True)
    flags = int(Qt.AlignmentFlag.AlignHCenter.value) | int(Qt.TextFlag.TextWordWrap.value)
    box = QFontMetrics(font).boundingRect(QRect(0, 0, int(width * 0.9), height), flags, text)
    pad_x, pad_y = max(6, height // 70), max(2, height // 160)
    x, y = (width - box.width()) // 2, height - box.height() - max(6, height // 15)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(QRect(x - pad_x, y - pad_y, box.width() + 2 * pad_x, box.height() + 2 * pad_y),
                                pad_x, pad_x)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(font)
        painter.drawText(QRect(x, y, box.width(), box.height()), flags, text)
    finally:
        painter.end()
    return image


def available() -> bool:
    return QMediaPlayer is not None


def deep_frame_image(frame, *, half: bool = False):
    """A frame of 10 or 16 bits a sample (H.264 «Hi10P», H.265 «Main 10», VP9 profile 2) as a picture, or None for
    other frames.  Qt turns such a frame into a picture with the graphics card only; without one (another graphics
    path, an old driver) ``toImage`` gives nothing and the play would show no picture, so it is turned here.
    ``half`` — at half its size (the page shows it that small anyway): four times quicker."""
    if QVideoFrameFormat is None:
        return None
    formats = QVideoFrameFormat.PixelFormat
    kind = frame.pixelFormat()
    if kind not in (formats.Format_YUV420P10, formats.Format_P010, formats.Format_P016):
        return None
    import numpy as np
    from PySide6.QtGui import QImage
    copy = QVideoFrame(frame)
    if not copy.map(QVideoFrame.MapMode.ReadOnly):
        return None
    try:
        width, height = copy.width(), copy.height()
        if width < 2 or height < 2:
            return None
        cw, ch = (width + 1) // 2, (height + 1) // 2

        def plane(n: int, rows: int, cols: int):
            line = copy.bytesPerLine(n)
            data = np.frombuffer(copy.bits(n), dtype=np.uint8, count=line * rows)
            return data.reshape(rows, line)[:, :cols * 2].view("<u2")
        luma = plane(0, height, width)
        if kind == formats.Format_YUV420P10:
            u, v, shift = plane(1, ch, cw), plane(2, ch, cw), 2          # 10 bits in the low bits
        else:
            both = plane(1, ch, cw * 2)
            u, v, shift = both[:, 0::2], both[:, 1::2], 8                # the bits in the high ones (P010, P016)
        if half:                                      # every second sample of the brightness, the colour as it is
            luma = luma[0:2 * ch:2, 0:2 * cw:2]
            height, width = luma.shape
            u, v = u[:height, :width], v[:height, :width]
        else:
            u = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1)[:height, :width]
            v = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1)[:height, :width]
        y = luma.astype(np.float32) / (1 << shift)
        u = u.astype(np.float32) / (1 << shift) - 128.0
        v = v.astype(np.float32) / (1 << shift) - 128.0
        fmt = frame.surfaceFormat()
        full = fmt.colorRange() == QVideoFrameFormat.ColorRange.ColorRange_Full
        space = fmt.colorSpace()
        if space == QVideoFrameFormat.ColorSpace.ColorSpace_BT601 or (
                space == QVideoFrameFormat.ColorSpace.ColorSpace_Undefined and height < 720):
            kr, kb = 0.299, 0.114
        elif space == QVideoFrameFormat.ColorSpace.ColorSpace_BT2020:
            kr, kb = 0.2627, 0.0593
        else:
            kr, kb = 0.2126, 0.0722
        if not full:                                  # the video range: 16–235 and 16–240 in eight bits
            y = (y - 16.0) * (255.0 / 219.0)
            u *= 255.0 / 224.0
            v *= 255.0 / 224.0
        kg = 1.0 - kr - kb
        r = y + 2.0 * (1.0 - kr) * v
        b = y + 2.0 * (1.0 - kb) * u
        g = (y - kr * r - kb * b) / kg
        rgb = np.clip(np.dstack((r, g, b)), 0, 255).astype(np.uint8)
        rgb = np.ascontiguousarray(rgb)
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
        return image.copy()                           # the numbers' memory is gone after this function
    finally:
        copy.unmap()


def later(target, method: str, *args) -> None:
    """A slot of the player (or of its sound) called from the window's event loop, not from Python (see above)."""
    QMetaObject.invokeMethod(target, method, Qt.ConnectionType.QueuedConnection, *args)


class _FrameMaker:
    """Frames into JPEG pictures, in a thread of its own: the decoder hands over the newest frame and never waits."""

    def __init__(self):
        self._cond = threading.Condition()
        self._waiting = None                 # (token, frame) not made yet
        self._last = None                    # (token, frame) made last: made again when the subtitles change
        self._token = ""
        self._size = (960, 540)
        self._closed = False
        self._said = False
        self._dims: dict = {}                # token -> the video's own size, once told to the page
        self._cues = None                    # core.video_tracks.Cues of a subtitle file chosen (drawn by the time)
        self.drawn = ""                      # the words drawn on the newest frame (what a check looks at)
        self.cap = 0                         # the most pixels of a frame (0 — as the page asks): see ``_paced``
        self._spent = 0.0                    # the time a frame takes, averaged
        self._thread = threading.Thread(target=self._run, name="video-frames", daemon=True)
        self._thread.start()

    def set_cues(self, cues) -> None:
        """The subtitles of a file next to the video, drawn over its frames (None — none); the frame shown last is
        made again (a paused video shows the change too)."""
        with self._cond:
            self._cues = cues
            if self._last is not None and self._waiting is None and self._last[0] == self._token:
                self._waiting = self._last
                self._cond.notify()

    def target(self, token: str, width: int = 960, height: int = 540) -> None:
        with self._cond:
            self._token = str(token or "")
            self._size = (int(width or 960), int(height or 540))
            if not self._token:
                self._waiting = None

    def offer(self, frame) -> None:
        """The newest frame (the window's thread hands it over); one still waiting is dropped."""
        with self._cond:
            if not self._token:
                return
            self._waiting = (self._token, QVideoFrame(frame))     # a copy: the decoder's frame is not ours to keep
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify()

    def _run(self) -> None:
        last = 0.0
        while True:
            with self._cond:
                while not self._closed and self._waiting is None:
                    self._cond.wait()
                if self._closed:
                    return
                token, frame = self._waiting
                self._waiting = None
                self._last = (token, frame)
                size = self._size
                cues = self._cues
            pause = last + 1.0 / FPS - time.monotonic()
            if pause > 0:
                time.sleep(pause)
                with self._cond:                 # a newer frame came meanwhile: that one is shown
                    if self._waiting is not None and self._waiting[0] == token:
                        token, frame = self._waiting
                        self._waiting = None
                        self._last = (token, frame)
                        cues = self._cues
            began = time.monotonic()
            try:
                jpeg, dims = self._encode(frame, size, cues)
            except Exception as exc:  # one bad frame is skipped; the next one may be fine
                if not self._said:
                    self._said = True
                    _LOG.warning("video frame not made: %s: %s", type(exc).__name__, exc)
                continue
            last = time.monotonic()
            self._paced(last - began, size)
            if dims and dims != self._dims.get(token):
                self._dims = {token: dims}               # the video's own size: the page fits its place to it
                RELAY.set_state(token, width=dims[0], height=dims[1])
            if jpeg:
                RELAY.publish(token, jpeg)

    def _paced(self, spent: float, size) -> None:
        """The frames keep up with the play: when they take longer than the play gives them (a big frame for the whole
        screen on a slow computer) they get fewer pixels; when they are quick again, more."""
        self._spent = spent if not self._spent else self._spent * 0.8 + spent * 0.2
        asked = max(1, int(size[0]) * int(size[1]))
        current = self.cap or asked
        if self._spent > SLOW_FRAME and current > SMALLEST:
            self.cap = max(SMALLEST, int(current * 0.8))
            self._spent = 0.0                        # judged afresh at the new size
        elif self.cap and self._spent < FAST_FRAME:
            grown = int(self.cap * 1.15)
            self.cap = 0 if grown >= asked else grown

    def _encode(self, frame, size, cues=None) -> tuple[bytes, tuple]:
        """(JPEG of the frame at the page's size — with the subtitles of its moment —, the frame's own (width,
        height)).  The subtitles: a track of the file (Qt hands its words with the frame), else a file's cue."""
        if not frame.isValid():
            return b"", ()
        image = frame.toImage()
        dims = (image.width(), image.height())
        if image.isNull():                           # 10 bits a sample, no graphics card for it
            shown = fit_size(frame.width(), frame.height(), *size)
            image = deep_frame_image(frame, half=shown[0] * 2 <= frame.width())
            if image is None or image.isNull():
                return b"", ()
            dims = (frame.width(), frame.height())
        words = str(frame.subtitleText() or "").strip()
        if not words and cues is not None and frame.startTime() >= 0:
            words = cues.at(frame.startTime() / 1_000_000.0)
        # words are drawn at the size the page shows the frame at: a small video grows to it, the letters stay sharp
        width, height = fit_size(image.width(), image.height(), *size, grow=bool(words))
        cap = self.cap
        if cap and width * height > cap:             # this computer cannot keep up at this size: a smaller frame
            k = (cap / float(width * height)) ** 0.5
            width, height = max(16, int(width * k)), max(16, int(height * k))
        if (width, height) != dims:
            image = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.drawn = words
        if words:
            image = draw_subtitle(image, words)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPG", QUALITY)
        return bytes(buffer.data()), dims


def is_own_stream(path: str) -> bool:
    """(v5.44.106) ``path`` is the program's own address of a stream of «Смотреть» (``core/video_watch``:
    ``http://127.0.0.1:<port>/watch/s/<token>/…``), not a file."""
    return bool(re.match(r"http://(?:127\.0\.0\.1|localhost):\d{1,5}/watch/s/[A-Za-z0-9_-]{8,64}/", str(path or "")))


class InlineVideo(QObject):
    """One video at a time; ``handle`` takes the page's requests (start and the buttons)."""

    _stop_for_move = Signal(object)          # threading.Event: a file is about to be moved by another thread
    # a subtitle file read in a thread of its own: (its cues or the reason it did not read, the choice, the play)
    _cues_ready = Signal(object, str, str)

    def __init__(self, parent, settings: dict, save_settings=None):
        super().__init__(parent)
        self.settings = settings
        self.save_settings = save_settings or (lambda: None)
        self.token = ""
        self.path_key = ""
        self._seek_to = 0.0                      # where to start once the file is open (an episode watched before)
        # … and that it got there: a big file (an MPEG-TS episode) may not take the first jump — it is made again
        self._want_at = 0.0                      # the place the play must reach (0 — reached or given up)
        self._want_tries = 0
        self._want_last = 0.0                    # when the jump was asked last (time.monotonic)
        self._want_until = 0.0                   # till when it is waited for
        self._looping = True
        self._ended = False
        self._letting_go: list = []              # (token, threading.Event | None): told once the player let go
        self._dark_since = 0.0                   # it has played without a single frame since then (0 — not so)
        self._dark_said = False                  # … and the page was told it cannot show this file
        self._av1 = False                        # the file of this play is AV1 (``_learn_av1``)
        self._prefer: dict = {}                  # the voice-over and subtitles chosen for the series before
        self._applied = False                    # … put on this play's tracks once they are known
        self._subfiles: list = []                # core.video_tracks.SubFile next to the video
        # what was chosen for this play (None — as the file has it): Qt does not say when it switched a track
        self._audio_choice: int | None = None    # the place of the voice-over
        self._sub_choice: str | None = None      # "-1" none, "N" a track of the file, "xN" a file next to it
        self._setter = None                      # TrackSetter, made when a file has tracks to choose from
        self._setter_failed = False
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.sink = QVideoSink(self)
        if not silent():                         # (a check: the player keeps its volume and «без звука», unheard)
            self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.sink)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.maker = _FrameMaker()
        # Queued into this (the window's) thread: a Python slot run in the decoder's thread would wait for the
        # interpreter while the window's thread, stopping the player, waits for the decoder — both for ever.
        self.sink.videoFrameChanged.connect(self._frame, Qt.ConnectionType.QueuedConnection)
        self.player.errorOccurred.connect(self._error)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.seekableChanged.connect(self._seekable)
        self.player.tracksChanged.connect(self._tracks, Qt.ConnectionType.QueuedConnection)
        self.player.activeTracksChanged.connect(self._tracks, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._publish)
        self._save_later = QTimer(self)
        self._save_later.setSingleShot(True)
        self._save_later.setInterval(1500)
        self._save_later.timeout.connect(self.save_settings)
        self._stop_for_move.connect(self._stopped_for_move)
        self._cues_ready.connect(self._subtitles_read)
        RELAY.add_releaser(self._release)

    # ── the page's requests ─────────────────────────────────────────────────
    def handle(self, payload: dict) -> None:
        cmd = str(payload.get("cmd") or "")
        if cmd == "start":
            self.start(str(payload["path"]), str(payload["token"]), int(payload.get("w") or 960),
                       int(payload.get("h") or 540), start_at=float(payload.get("from") or 0),
                       loop=payload.get("loop") is not False, prefer=payload.get("prefer"),
                       top=str(payload.get("top") or "") or None)
            return
        if not self.token or payload.get("token") != self.token:
            return
        if cmd == "toggle":
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                later(self.player, "pause")
            else:
                self._ended = False
                later(self.player, "play")
        elif cmd == "play":
            self._ended = False
            later(self.player, "play")
        elif cmd == "pause":
            later(self.player, "pause")
        elif cmd == "seek":
            self._ended = False
            self._want_at = 0.0                  # the person's own jump: the place to start at is not made any more
            self._seek_to = 0.0
            later(self.player, "setPosition", Q_ARG("qint64", int(max(0.0, float(payload.get("value") or 0)) * 1000)))
        elif cmd == "volume":
            self.settings["media_volume"] = max(0, min(100, int(payload.get("value") or 0)))
            self.settings["media_muted"] = False
            self._apply_volume()
            self._save_later.start()
        elif cmd == "mute":
            self.settings["media_muted"] = bool(payload.get("value"))
            self._apply_volume()
            self._save_later.start()
        elif cmd == "size":
            self.maker.target(self.token, int(payload.get("w") or 960), int(payload.get("h") or 540))
        elif cmd == "audio":
            self._set_audio(int(payload.get("value") or 0))
        elif cmd == "subtitle":
            self._set_subtitle(str(payload.get("value") or "-1"))
        elif cmd == "stop":
            self.stop()
            return
        self._publish()

    def start(self, path: str, token: str, width: int, height: int, *, start_at: float = 0.0,
              loop: bool = True, prefer: dict | None = None, top: str | None = None) -> None:
        """Play ``path`` for the page of ``token``; ``start_at`` — from that second (where it stopped last time),
        ``loop`` — again and again (a card's video), else once: the page hears «ended» (an episode: the next one);
        ``prefer`` — the voice-over and subtitles chosen for its series (``core/video_tracks``), ``top`` — the
        series' folder (its folders of subtitles)."""
        from core import video_tracks
        self.stop()
        self.token = token
        self._prefer = dict(prefer) if isinstance(prefer, dict) else {}
        self._applied = False
        self._audio_choice = None
        self._sub_choice = None
        self.maker.set_cues(None)
        # (v5.44.106) an episode of «Смотреть»: the program's own address of its stream (core/video_watch) — no file,
        # no subtitle files beside it, nothing to read of it here
        remote = is_own_stream(path)
        try:
            self._subfiles = [] if remote else video_tracks.subtitle_files(path, top=top)
        except OSError as exc:
            _LOG.info("subtitle files of %s not looked at: %s", path, exc)
            self._subfiles = []
        self.path_key = path if remote else os.path.normcase(os.path.abspath(path))
        self._dark_since, self._dark_said = 0.0, False
        try:                                     # an AV1 file: what it shows tells whether this computer can
            from core import video_probe
            info = None if remote else video_probe.probe(path)
            self._av1 = info is not None and info.video == "av1"
        except Exception as exc:  # the play goes on; nothing is learnt
            _LOG.debug("inline video: codec of %s not read: %s", path, exc)
            self._av1 = False
        self._seek_to = max(0.0, float(start_at or 0))
        self._want_at, self._want_tries, self._want_last, self._want_until = self._seek_to, 0, 0.0, 0.0
        self._looping = bool(loop)
        self._ended = False
        self.player.setLoops(QMediaPlayer.Loops.Infinite if loop else QMediaPlayer.Loops.Once)
        self.maker.target(token, width, height)
        self._apply_volume()
        later(self.player, "setSource", Q_ARG(QUrl, QUrl(path) if remote else QUrl.fromLocalFile(path)))
        later(self.player, "play")
        self._timer.start()
        self._publish()

    def stop(self, *, now: bool = False, done=None) -> None:
        """Stop and let go of the file (Windows cannot move an open file).  The page — and ``done``, a mover waiting
        for the file — hear it once the player has let go; ``now``: in this very call (the exit, a move made by the
        window's own thread, which cannot wait for the event loop)."""
        token, self.token, self.path_key = self.token, "", ""
        self._seek_to = 0.0
        self._want_at = 0.0
        self._ended = False
        self._timer.stop()
        self.maker.target("")
        if not now:
            later(self.player, "stop")
            later(self.player, "setSource", Q_ARG(QUrl, QUrl()))
            self._letting_go.append((token, done))
            QMetaObject.invokeMethod(self, "_let_go", Qt.ConnectionType.QueuedConnection)
            return
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except RuntimeError as exc:              # the player is already gone at exit
            _LOG.debug("inline video stop: %s", exc)
        if token:
            RELAY.end(token)
        if done is not None:
            done.set()

    @Slot()
    def _let_go(self) -> None:
        """(the event loop, after the player's own stop) the page's play is over; a waiting mover may go on."""
        while self._letting_go:
            token, done = self._letting_go.pop(0)
            if token:
                RELAY.end(token)                 # a newer play has a token of its own: it goes on
            if done is not None:
                done.set()

    def close(self) -> None:
        self.stop(now=True)
        self.maker.close()

    # ── internals ───────────────────────────────────────────────────────────
    def _frame(self, frame) -> None:
        if self.token:
            self.maker.offer(frame)

    def _apply_volume(self) -> None:
        try:
            volume = int(self.settings.get("media_volume", 50) or 0)
        except (TypeError, ValueError):
            volume = 50
        later(self.audio, "setVolume", Q_ARG("float", max(0, min(100, volume)) / 100.0))
        later(self.audio, "setMuted", Q_ARG(bool, bool(self.settings.get("media_muted", False))))

    def _status(self, status) -> None:
        """The file is open: the place it should start at; the end of a video played once: the page is told."""
        if not self.token:
            return
        if self._seek_to > 0 and status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self._jump_to_start()
        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self._looping:
            self._ended = True
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._tracks()                       # the file is open: its tracks are its own now
        self._publish()

    def _jump_to_start(self) -> None:
        """The file is open: to the place it should start at (``_seek_to``), once it can be jumped in — Qt drops a
        jump asked before that (``setPosition`` of a player that is not seekable yet does nothing)."""
        if self._seek_to <= 0 or not self.player.isSeekable():
            return
        seek_to, self._seek_to = self._seek_to, 0.0
        duration = self.player.duration() / 1000.0
        if duration > 0 and seek_to >= duration - 1:
            self._want_at = 0.0                  # the place is past the end of this file: from the start
            return
        self._want_tries, self._want_last = 1, time.monotonic()
        self._want_until = self._want_last + START_WAIT
        later(self.player, "setPosition", Q_ARG("qint64", int(seek_to * 1000)))

    def _seekable(self, seekable: bool) -> None:
        if seekable and self.token and self.player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia,
                                                                     QMediaPlayer.MediaStatus.BufferedMedia,
                                                                     QMediaPlayer.MediaStatus.BufferingMedia):
            self._jump_to_start()

    def _check_start(self) -> None:
        """(every publish) the play got to the place it should start at — or the jump is asked again (up to
        ``START_TRIES`` times, ``START_WAIT`` seconds in all): never quietly from the beginning."""
        if self._want_at <= 0 or self._seek_to > 0 or not self._want_tries:
            return
        position = max(0, self.player.position()) / 1000.0
        if position >= self._want_at - 2.0:
            self._want_at = 0.0
            return
        now = time.monotonic()
        if now > self._want_until or self._want_tries >= START_TRIES:
            _LOG.warning("inline video: the place it should start at (%.1f s) was not reached (%.1f s)",
                         self._want_at, position)
            self._want_at = 0.0
            return
        if now - self._want_last >= 0.8 and self.player.isSeekable():
            self._want_tries += 1
            self._want_last = now
            later(self.player, "setPosition", Q_ARG("qint64", int(self._want_at * 1000)))

    # ── voice-overs and subtitles ──────────────────────────────────────────────
    def _tracks_setter(self):
        if self._setter is None and not self._setter_failed:
            try:
                self._setter = TrackSetter(self.player, self)
            except Exception as exc:  # no Qt QML in this installation: the tracks stay as the file has them
                self._setter_failed = True
                _LOG.warning("tracks of the video cannot be chosen: %s", exc)
        return self._setter

    def _loaded(self) -> bool:
        """The file of this play is open: the player's tracks are its own (not those of the file played before)."""
        if not self.path_key:
            return False
        if is_own_stream(self.path_key):                 # (v5.44.106) a stream of «Смотреть»: its address
            same = self.player.source().toString() == self.path_key
        else:
            source = self.player.source().toLocalFile()
            same = bool(source) and os.path.normcase(os.path.abspath(source)) == self.path_key
        return same and \
            self.player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferingMedia,
                                          QMediaPlayer.MediaStatus.BufferedMedia, QMediaPlayer.MediaStatus.EndOfMedia)

    def _tracks(self) -> None:
        """(the event loop) the file's tracks are known or changed: the page gets them; the series' choice is put on
        them once the file is open."""
        from core import video_tracks
        if not self.token:
            return
        loaded = self._loaded()
        audio = track_list(self.player.audioTracks()) if loaded else []
        subtitles = [dict(t, id=str(n)) for n, t in enumerate(track_list(self.player.subtitleTracks()))] if loaded else []
        if (len(audio) > 1 or subtitles) and self._tracks_setter() is None:
            audio, subtitles = [], []            # the file's own tracks cannot be switched here: none offered
        subtitles += [{"id": f"x{n}", "name": f.name, "title": f.name, "lang": "", "file": True}
                      for n, f in enumerate(self._subfiles)]
        if loaded and not self._applied:
            self._applied = True
            voice, words = video_tracks.preferred(self._prefer, audio, subtitles)
            if voice is not None and voice != self.player.activeAudioTrack():
                self._set_audio(voice)
            if words is not None:
                self._set_subtitle(words)
        playing = self._audio_choice if self._audio_choice is not None else \
            (self.player.activeAudioTrack() if loaded else -1)
        active = self.player.activeSubtitleTrack() if loaded else -1
        shown = self._sub_choice if self._sub_choice is not None else str(active if active >= 0 else -1)
        RELAY.set_state(self.token, audio_tracks=audio, audio=playing, subtitles=subtitles, subtitle=shown)

    def _set_audio(self, n: int) -> None:
        setter = self._tracks_setter()
        if setter is not None and n >= 0:
            setter.audio(n)
            self._audio_choice = n
            RELAY.set_state(self.token, audio=n)

    def _set_subtitle(self, choice: str) -> None:
        """"-1" — none; "N" — the file's track N; "xN" — the subtitle file N next to it (drawn by the frames)."""
        from core import video_tracks
        setter = self._tracks_setter()
        if choice.startswith("x") and choice[1:].isdigit() and int(choice[1:]) < len(self._subfiles):
            sub = self._subfiles[int(choice[1:])]
            self._sub_choice = choice
            if setter is not None:
                setter.subtitle(-1)
            token = self.token

            def read() -> None:
                try:
                    got = video_tracks.Cues(video_tracks.read_cues(sub.path))
                except (OSError, ValueError) as exc:
                    _LOG.warning("subtitles %s not read: %s", sub.path, exc)
                    got = f"Субтитры «{sub.name}» не прочитались: {exc}"
                self._cues_ready.emit(got, choice, token)
            threading.Thread(target=read, name="video-subtitles", daemon=True).start()
            RELAY.set_state(self.token, subtitle=choice, subtitle_error="")
            return
        number = int(choice) if choice.isdigit() else -1
        self._sub_choice = str(number)
        self.maker.set_cues(None)
        if setter is not None:
            setter.subtitle(number)
        RELAY.set_state(self.token, subtitle=str(number), subtitle_error="")

    def _subtitles_read(self, got, choice: str, token: str) -> None:
        """(the event loop) a subtitle file read: its cues are drawn — if it is still the one chosen for this play."""
        if token != self.token or choice != self._sub_choice:
            return
        if isinstance(got, str):
            RELAY.set_state(token, subtitle_error=got)
        else:
            self.maker.set_cues(got)

    def _publish(self) -> None:
        if not self.token:
            return
        self._check_start()
        self._check_picture()
        status = self.player.mediaStatus()
        RELAY.set_state(self.token, ended=self._ended,
                        playing=self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                        position=max(0, self.player.position()) / 1000.0,
                        duration=max(0, self.player.duration()) / 1000.0,
                        volume=int(round(self.audio.volume() * 100)), muted=bool(self.audio.isMuted()),
                        ready=status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                                         QMediaPlayer.MediaStatus.BufferingMedia, QMediaPlayer.MediaStatus.EndOfMedia))

    def _check_picture(self) -> None:
        """(every publish) a file with a picture that has played ``NO_PICTURE_AFTER`` seconds without one frame: the
        player cannot show it (AV1 without a graphics card that decodes it — Qt's FFmpeg has no AV1 of its own; it
        says no error).  The page is told — it goes over to the window's browser — and the play pauses."""
        if self._dark_said:
            return
        if RELAY.frames(self.token) > 0:
            self._dark_since = 0.0
            self._learn_av1(True)
            return
        if not self.player.hasVideo():
            self._dark_since = 0.0
            return
        now = time.monotonic()
        if not self._ended:                      # (a short episode played to its end without a frame: at once)
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self._dark_since = 0.0
                return
            if not self._dark_since:
                self._dark_since = now
                return
            if now - self._dark_since < NO_PICTURE_AFTER:
                return
        self._dark_said = True
        _LOG.warning("inline video: no picture after %.0f s of play: %s", now - (self._dark_since or now), self.path_key)
        self._learn_av1(False)
        RELAY.set_state(self.token, error=NO_PICTURE, no_picture=True)
        later(self.player, "pause")

    def _learn_av1(self, shows: bool) -> None:
        """An AV1 file showed its picture here, or did not: kept (``video_av1_own``) — the pages choose the player of
        the next AV1 by it (``core.video_probe.window_player``)."""
        if not self._av1:
            return
        self._av1 = False                        # once a play
        word = "yes" if shows else "no"
        if self.settings.get("video_av1_own") == word:
            return
        _LOG.info("inline video: AV1 %s here", "plays" if shows else "shows no picture")
        self.settings["video_av1_own"] = word
        self._save_later.start()

    def _error(self, _error, text: str) -> None:
        if self.token:
            _LOG.warning("inline video: %s", text)
            RELAY.set_state(self.token, error=str(text or "видео не открылось"))

    def _release(self, key, wait: float) -> None:
        """(any thread) a file is about to be moved: the player lets go of it when it is this one (``key`` None —
        every file)."""
        if not self.token or (key is not None and key != self.path_key):
            return
        if QThread.currentThread() is self.thread():
            self.stop(now=True)                  # the move goes on right after this call
            return
        done = threading.Event()
        self._stop_for_move.emit(done)
        done.wait(max(0.1, float(wait)))

    def _stopped_for_move(self, done) -> None:
        try:
            self.stop(done=done)                 # ``done`` is set once the player has let go
        except Exception:
            done.set()                           # the mover must not wait for nothing
            raise
