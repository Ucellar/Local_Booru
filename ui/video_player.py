"""The video player of the new gallery (inside the program window).

Qt WebEngine cannot play H.264 MP4 (the codecs are not in its license), so videos are played the way
the old post page played them: with mpv when it is installed, otherwise with Qt Multimedia (its
FFmpeg backend plays MP4, MKV, MOV, AVI, WebM).  The player takes the place of the web page while
it is open; Esc closes it, ← → go to the previous / next post of the same search.
It takes over from the page's player (⛶): from the same moment, paused or playing, with the same voice-over and
subtitles — and gives the moment back when it is left (``left``): the page's player goes on from there.  An episode
is played once (the next one comes after it), what was watched is kept as it goes (``on_progress``), and
«Продолжить просмотр 12:34» brings it back to the furthest place reached when it is further than 15 s ahead.
A file with several voice-overs or subtitles (a «мультирелиз» MKV) and subtitle files next to it
(``core/video_tracks``): menus «Озвучка» and «Субтитры» over the video; an episode's choice is kept for its series.
mpv shows a subtitle file itself; with Qt Multimedia its cue of the moment is put on the frames (the picture draws
it as it draws a track of the file).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from core import video_library
from PySide6.QtCore import Q_ARG, QTimer, Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

_LOG = logging.getLogger("local_booru.video")
PROGRESS_EVERY = 10.0       # seconds of playing between two notes of what was watched (an episode)

try:
    import mpv as _mpv
except Exception as _mpv_error:  # python-mpv raises OSError/ImportError when libmpv is missing
    _mpv = None
    _LOG.info("mpv is not available (%s); Qt Multimedia plays videos", _mpv_error)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
except ImportError:
    QAudioOutput = QMediaPlayer = QVideoWidget = None


def _fmt(seconds: float) -> str:
    s = max(0, int(seconds or 0))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class _QtBackend:
    """Qt Multimedia: QMediaPlayer with a video widget, endless loop.  The player is told what to do from the window's
    event loop, not from Python (``ui.video_inline.later``: a decoder thread and the window could wait for each other
    for ever); ``stop(now=True)`` — at once, before files are moved."""

    def __init__(self, owner):
        self.widget = QVideoWidget(owner)
        self.widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.player = QMediaPlayer(owner)
        self.audio = QAudioOutput(owner)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.widget)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.errorOccurred.connect(lambda _e, text: owner.show_error(text))
        # queued: the window's thread, never the decoder's (ui/video_inline.py)
        self.player.tracksChanged.connect(owner.tracks_changed, Qt.ConnectionType.QueuedConnection)
        self.player.activeTracksChanged.connect(owner.tracks_changed, Qt.ConnectionType.QueuedConnection)
        self.player.mediaStatusChanged.connect(owner.tracks_changed, Qt.ConnectionType.QueuedConnection)
        self._setter = None
        self._setter_failed = False

    def _tracks_setter(self):
        if self._setter is None and not self._setter_failed:
            try:
                from ui.video_inline import TrackSetter
                self._setter = TrackSetter(self.player, self.widget, sink=self.widget.videoSink())
            except Exception as exc:  # no Qt QML in this installation: the tracks stay as the file has them
                self._setter_failed = True
                _LOG.warning("tracks of the video cannot be chosen: %s", exc)
        return self._setter

    def seekable(self) -> bool:
        """Qt drops a jump asked before the file can be jumped in (``setPosition`` does nothing then)."""
        return bool(self.player.isSeekable())

    def loaded(self, path: str) -> bool:
        """``path`` is open: the player's tracks are its own (not those of the file played before)."""
        source = self.player.source().toLocalFile()
        return bool(source) and os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(path)) \
            and self.player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia,
                                              QMediaPlayer.MediaStatus.BufferingMedia,
                                              QMediaPlayer.MediaStatus.BufferedMedia, QMediaPlayer.MediaStatus.EndOfMedia)

    def tracks(self) -> tuple[list, int, list, int]:
        """(voice-overs, the one playing, subtitle tracks of the file, the one shown or -1); each {"name", "title",
        "lang"}.  None of them when they cannot be switched here."""
        from ui.video_inline import track_list
        audio = track_list(self.player.audioTracks())
        subtitles = track_list(self.player.subtitleTracks())
        if (len(audio) > 1 or subtitles) and self._tracks_setter() is None:
            return [], -1, [], -1
        return audio, self.player.activeAudioTrack(), subtitles, self.player.activeSubtitleTrack()

    def set_audio(self, n: int) -> None:
        setter = self._tracks_setter()
        if setter is not None:
            setter.audio(n)

    def set_subtitle(self, n: int) -> None:
        setter = self._tracks_setter()
        if setter is not None:
            setter.subtitle(n)

    def show_file(self, sub) -> bool:
        """A subtitle file: Qt Multimedia cannot open one — the owner puts its cues on the frames (``set_text``)."""
        return False

    def set_text(self, words: str) -> None:
        setter = self._tracks_setter()
        if setter is not None:
            setter.text(words)

    def load(self, path: str, loop: bool = True) -> None:
        from ui.video_inline import later
        self.player.setLoops(QMediaPlayer.Loops.Infinite if loop else QMediaPlayer.Loops.Once)
        later(self.player, "setSource", Q_ARG(QUrl, QUrl.fromLocalFile(path)))
        later(self.player, "play")

    def pause(self) -> None:
        from ui.video_inline import later
        later(self.player, "pause")

    def ended(self) -> bool:
        return self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia

    def stop(self, now: bool = False) -> None:
        if now:
            self.player.stop()
            self.player.setSource(QUrl())
            return
        from ui.video_inline import later
        later(self.player, "stop")
        later(self.player, "setSource", Q_ARG(QUrl, QUrl()))

    def toggle(self) -> None:
        from ui.video_inline import later
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        later(self.player, "pause" if playing else "play")

    def playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def position(self) -> tuple[float, float]:
        return self.player.position() / 1000.0, self.player.duration() / 1000.0

    def seek(self, seconds: float) -> None:
        from ui.video_inline import later
        later(self.player, "setPosition", Q_ARG("qint64", int(max(0.0, seconds) * 1000)))

    def set_volume(self, volume: int, muted: bool) -> None:
        from ui.video_inline import later
        later(self.audio, "setVolume", Q_ARG("float", max(0, min(100, int(volume))) / 100.0))
        later(self.audio, "setMuted", Q_ARG(bool, bool(muted)))


class _MpvBackend:
    """mpv drawn into a native child window (what the old post page used)."""

    def __init__(self, owner):
        self.widget = QWidget(owner)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.widget.setStyleSheet("background:#000;")
        self.player = _mpv.MPV(wid=int(self.widget.winId()), vo="gpu", hwdec="auto", loop="inf",
                               keep_open=True, keepaspect=True, msg_level="all=no")

    def load(self, path: str, loop: bool = True) -> None:
        self.player.loop_file = "inf" if loop else "no"
        self.player.play(path)
        self.player.pause = False

    def pause(self) -> None:
        self.player.pause = True

    def ended(self) -> bool:
        return bool(self.player.eof_reached)

    def stop(self, now: bool = False) -> None:
        self.player.command("stop")

    def toggle(self) -> None:
        self.player.pause = not self.player.pause

    def playing(self) -> bool:
        return not bool(self.player.pause)

    def position(self) -> tuple[float, float]:
        return float(self.player.time_pos or 0.0), float(self.player.duration or 0.0)

    def seek(self, seconds: float) -> None:
        self.player.seek(max(0.0, seconds), reference="absolute")

    def set_volume(self, volume: int, muted: bool) -> None:
        self.player.volume = max(0, min(100, int(volume)))
        self.player.mute = bool(muted)

    def seekable(self) -> bool:
        return True

    def loaded(self, path: str) -> bool:
        playing = str(self.player.path or "")
        return bool(playing) and os.path.normcase(os.path.abspath(playing)) == os.path.normcase(os.path.abspath(path)) \
            and bool(self.player.track_list)

    def tracks(self) -> tuple[list, int, list, int]:
        """As ``_QtBackend.tracks``: from mpv's list of tracks (the subtitle files it was given are not the file's)."""
        from core import video_tracks
        items = [t for t in (self.player.track_list or []) if isinstance(t, dict)]
        found = {}
        for kind in ("audio", "sub"):
            names, ids, shown = [], [], -1
            for n, track in enumerate((t for t in items if t.get("type") == kind and not t.get("external")), 1):
                title = str(track.get("title") or "")
                lang = video_tracks.language_name(str(track.get("lang") or ""))
                names.append({"name": video_tracks.track_name(title, lang, n), "title": title, "lang": lang})
                ids.append(track.get("id"))
                if track.get("selected"):
                    shown = n - 1
            found[kind] = (names, ids, shown)
        self._ids = {kind: found[kind][1] for kind in found}
        return found["audio"][0], found["audio"][2], found["sub"][0], found["sub"][2]

    def set_audio(self, n: int) -> None:
        ids = getattr(self, "_ids", {}).get("audio") or []
        if 0 <= n < len(ids):
            self.player.aid = ids[n]

    def set_subtitle(self, n: int) -> None:
        ids = getattr(self, "_ids", {}).get("sub") or []
        self.player.sid = ids[n] if 0 <= n < len(ids) else "no"

    def show_file(self, sub) -> bool:
        """mpv shows a subtitle file itself, with its own styles: given once, chosen again by its track."""
        for track in self.player.track_list or []:
            if (isinstance(track, dict) and track.get("type") == "sub" and track.get("external")
                    and os.path.normcase(str(track.get("external-filename") or "")) == os.path.normcase(sub.path)):
                self.player.sid = track.get("id")
                return True
        self.player.command("sub-add", sub.path, "select", sub.name)
        return True

    def set_text(self, words: str) -> None:
        """(mpv draws a subtitle file itself)"""


class VideoPlayer(QWidget):
    """Full-size player page: video, a bar with play/pause, position, volume, previous/next, close."""

    closed = Signal()
    navigate = Signal(int)          # -1 previous post, +1 next post
    left = Signal(float, float, bool, bool)     # closed or played to its end: (position, length, playing, ended)
    # a subtitle file read in a thread of its own: (its cues or the reason it did not read, the choice, the file played)
    _cues_ready = Signal(object, str, str)

    def __init__(self, parent, settings: dict, save_settings=None):
        super().__init__(parent)
        self.settings = settings
        self.save_settings = save_settings or (lambda: None)
        self.setObjectName("VideoPlayer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            "#VideoPlayer{background:#050505;} #VideoPlayer QLabel{color:#e6e6e6;}"
            "#VideoPlayer QPushButton{color:#e6e6e6;background:#1d1d1f;border:1px solid #333;border-radius:4px;"
            "padding:5px 12px;font-size:14px;} #VideoPlayer QPushButton:hover{border-color:#888;}"
            "#VideoPlayer QComboBox{color:#e6e6e6;background:#1d1d1f;border:1px solid #333;border-radius:4px;"
            "padding:4px 8px;min-width:110px;} #VideoPlayer QComboBox:hover{border-color:#888;}"
            "#VideoPlayer QComboBox QAbstractItemView{color:#e6e6e6;background:#1d1d1f;"
            "selection-background-color:#3d3d40;}"
            "#VideoPlayer QPushButton#ResumeButton{background:#e6e6e6;color:#111;border-color:#e6e6e6;font-weight:bold;}"
            "#VideoPlayer QPushButton#ResumeButton:hover{background:#ffffff;}")
        self.backend = None
        self._seeking = False
        self._prefer: dict = {}              # the voice-over and subtitles chosen for the series before
        self._applied = False                # … put on this play's tracks once they are known
        self._on_choice = None               # (kind, value): a choice made here is kept for the series
        self._subfiles: list = []            # core.video_tracks.SubFile next to the video
        # what was chosen for this play (None — as the file has it): Qt does not say when it switched a track
        self._audio_choice: int | None = None
        self._sub_choice: str | None = None  # "-1" none, "N" a track of the file, "xN" a file next to it
        self._cues = None                    # core.video_tracks.Cues of a file chosen (Qt Multimedia: put on frames)
        self._shown_text = ""
        self._text_ticks = 0
        self._path = ""
        self._tracks_key = None
        self._mpv_tracks = None              # what mpv's tracks were last time (mpv is looked at, it tells nothing)
        # taking over from the page's player and giving the moment back
        self._seek_to = 0.0                  # where to go once the file is open (the page's moment)
        self._pause_after = False            # … and stay paused there (the page's player was paused)
        self._furthest = 0.0                 # the furthest place reached: «Продолжить просмотр»
        self._resume_used = False            # «Продолжить просмотр» pressed: not offered again till it is reached
        self._loop = True                    # a card's video goes round; an episode is played once
        self._on_progress = None             # (position, length, ended): what was watched is kept
        self._said_at = 0.0                  # when it was kept last (every ``PROGRESS_EVERY`` seconds of playing)
        self._ended = False
        self._error = QLabel("", self)
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error.setWordWrap(True)
        self._error.hide()

        self.title = QLabel("", self)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        close = QPushButton("Закрыть  ✕", self)
        close.setToolTip("Вернуться к странице (Esc)")
        close.clicked.connect(self.close_player)
        self.audio_label = QLabel("Озвучка", self)
        self.audio_box = QComboBox(self)
        self.audio_box.setToolTip("Озвучка: дорожки звука в файле видео")
        self.audio_box.activated.connect(self._audio_chosen)
        self.subs_label = QLabel("Субтитры", self)
        self.subs_box = QComboBox(self)
        self.subs_box.setToolTip("Субтитры: дорожки в файле видео и файлы субтитров рядом с ним")
        self.subs_box.activated.connect(self._subs_chosen)
        for widget in (self.audio_label, self.audio_box, self.subs_label, self.subs_box):
            widget.setFocusPolicy(Qt.FocusPolicy.ClickFocus if widget in (self.audio_box, self.subs_box)
                                  else Qt.FocusPolicy.NoFocus)
            widget.hide()
        self.subs_note = QLabel("", self)
        self.subs_note.setStyleSheet("color:#f0a8a8;")
        self.subs_note.hide()
        self.resume_btn = QPushButton("", self)
        self.resume_btn.setObjectName("ResumeButton")
        self.resume_btn.setToolTip("Вернуться туда, где вы остановились")
        self.resume_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.resume_btn.clicked.connect(self._resume_clicked)
        self.resume_btn.hide()
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.resume_btn)
        top.addWidget(self.subs_note)
        top.addWidget(self.audio_label)
        top.addWidget(self.audio_box)
        top.addSpacing(8)
        top.addWidget(self.subs_label)
        top.addWidget(self.subs_box)
        top.addSpacing(8)
        top.addWidget(close)

        self.video_area = QVBoxLayout()
        self.video_area.addWidget(self._error, 1)

        self.prev_btn = QPushButton("‹", self)
        self.prev_btn.setToolTip("Предыдущая (←)")
        self.prev_btn.clicked.connect(lambda: (self.keep_progress(), self.navigate.emit(-1)))
        self.play_btn = QPushButton("❚❚", self)
        self.play_btn.setToolTip("Пауза / пуск (пробел)")
        self.play_btn.clicked.connect(self.toggle)
        self.next_btn = QPushButton("›", self)
        self.next_btn.setToolTip("Следующая (→)")
        self.next_btn.clicked.connect(lambda: (self.keep_progress(), self.navigate.emit(1)))
        self.time = QLabel("0:00 / 0:00", self)
        self.seek = QSlider(Qt.Orientation.Horizontal, self)
        self.seek.setRange(0, 1000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.mute_btn = QPushButton("🔊", self)
        self.mute_btn.setToolTip("Звук (E)")
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.volume = QSlider(Qt.Orientation.Horizontal, self)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(110)
        self.volume.setValue(int(self.settings.get("media_volume", 50) or 0))
        self.volume.valueChanged.connect(self._volume_changed)
        bar = QHBoxLayout()
        for w in (self.prev_btn, self.play_btn, self.next_btn):
            bar.addWidget(w)
        bar.addWidget(self.time)
        bar.addWidget(self.seek, 1)
        bar.addWidget(self.mute_btn)
        bar.addWidget(self.volume)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.addLayout(top)
        lay.addLayout(self.video_area, 1)
        lay.addLayout(bar)

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._cue_timer = QTimer(self)               # a subtitle file's cue of the moment (Qt Multimedia)
        self._cue_timer.setInterval(100)
        self._cue_timer.timeout.connect(self._show_cue)
        self._cues_ready.connect(self._subtitles_read)

    # ── backend ──────────────────────────────────────────────────────────────
    def _ensure_backend(self) -> bool:
        if self.backend is not None:
            return True
        if _mpv is not None and bool(self.settings.get("video_use_mpv", True)):
            try:
                self.backend = _MpvBackend(self)
            except Exception as exc:  # libmpv present but failing (GPU, version): use Qt instead
                _LOG.warning("mpv could not start (%s); using Qt Multimedia", exc)
                self.backend = None
        if self.backend is None and QMediaPlayer is not None:
            self.backend = _QtBackend(self)
        if self.backend is None:
            return False
        self.backend.widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.backend.widget.mousePressEvent = lambda e: self.toggle() if e.button() == Qt.MouseButton.LeftButton else None
        self.video_area.insertWidget(0, self.backend.widget, 1)
        return True

    def backend_name(self) -> str:
        return {"_MpvBackend": "mpv", "_QtBackend": "qt"}.get(type(self.backend).__name__, "")

    # ── public ───────────────────────────────────────────────────────────────
    def play(self, path: str, title: str = "", *, prefer: dict | None = None, on_choice=None,
             top: str | None = None, start_at: float = 0.0, paused: bool = False, resume: float = 0.0,
             on_progress=None, loop: bool = True) -> bool:
        """Play ``path``; ``prefer`` — the voice-over and subtitles chosen for its series before (put on its tracks
        once they are known), ``on_choice(kind, value)`` — told of a choice made here ("audio" / "subtitle"),
        ``top`` — the series' folder (its folders of subtitles).  Taking over from the page's player: ``start_at`` —
        its moment, ``paused`` — it was paused, ``resume`` — the furthest place reached («Продолжить просмотр»);
        ``on_progress(position, length, ended)`` — what was watched (an episode), ``loop`` — round and round (a card's
        video) or once."""
        from core import video_tracks
        self._error.hide()
        if not self._ensure_backend():
            self.show_error("Нет проигрывателя: не найдены ни mpv, ни Qt Multimedia.")
            return False
        self._forget_tracks()
        self._path = path
        self._prefer = dict(prefer) if isinstance(prefer, dict) else {}
        self._on_choice = on_choice
        self._seek_to = max(0.0, float(start_at or 0.0))
        self._pause_after = bool(paused)
        self._furthest = max(self._seek_to, float(resume or 0.0))
        self._resume_used = False
        self._loop = bool(loop)
        self._on_progress = on_progress
        self._said_at = time.monotonic()
        self._ended = False
        try:
            self._subfiles = video_tracks.subtitle_files(path, top=top)
        except OSError as exc:
            _LOG.info("subtitle files of %s not looked at: %s", path, exc)
            self._subfiles = []
        self.backend.widget.show()
        self.title.setText(title)
        self._apply_volume()
        self.backend.load(path, self._loop)
        self.play_btn.setText("▶" if self._pause_after else "❚❚")
        self._timer.start()
        self.setFocus()
        self.tracks_changed()                        # subtitle files are known at once; the file's tracks later
        return True

    def stop(self, now: bool = False) -> None:
        """``now`` — the file let go in this very call (files are moved right after it)."""
        self._timer.stop()
        self._forget_tracks()
        self._seek_to, self._pause_after = 0.0, False
        self._on_progress = None
        self.resume_btn.hide()
        if self.backend is not None:
            try:
                self.backend.stop(now=now)
            except Exception as exc:  # the player may already be gone at exit
                _LOG.debug("stop failed: %s", exc)

    def close_player(self) -> None:
        """Esc, ✕ or the end of an episode: what was watched is kept, the moment goes back to the page (``left``)."""
        position, length, playing = self.moment()
        ended = self._ended
        if self._path:
            self._tell_progress(length if ended else position, length, ended)
        self.stop()
        self.closed.emit()
        self.left.emit(float(length if ended else position), float(length), bool(playing), bool(ended))

    def moment(self) -> tuple[float, float, bool]:
        """(the place, the length, playing) of what is played now."""
        if self.backend is None or not self._path:
            return 0.0, 0.0, False
        try:
            position, length = self.backend.position()
            return float(position or 0.0), float(length or 0.0), bool(self.backend.playing())
        except Exception as exc:  # the player is already gone
            _LOG.debug("the player's moment: %s", exc)
            return 0.0, 0.0, False

    def keep_progress(self) -> None:
        """What was watched is kept now (the player is about to be left for another episode)."""
        position, length, _playing = self.moment()
        if self._path:
            self._tell_progress(position, length, False)

    def toggle(self) -> None:
        if self.backend is None:
            return
        playing = self.backend.playing()
        self.backend.toggle()
        self.play_btn.setText("▶" if playing else "❚❚")
        if playing:                                  # paused: what was watched is kept at once
            self.keep_progress()

    def toggle_mute(self) -> None:
        self.settings["media_muted"] = not bool(self.settings.get("media_muted", False))
        self._apply_volume()
        self.save_settings()

    def show_error(self, text: str) -> None:
        self._error.setText(f"Видео не открылось: {text}")
        self._error.show()
        if self.backend is not None:
            self.backend.widget.hide()

    # ── voice-overs and subtitles ────────────────────────────────────────────
    def _forget_tracks(self) -> None:
        self._cue_timer.stop()
        if self._cues is not None or self._shown_text:
            self._cues = None
            self._shown_text = ""
            if self.backend is not None:
                self.backend.set_text("")
        self._audio_choice = None
        self._sub_choice = None
        self._applied = False
        self._tracks_key = None
        self._mpv_tracks = None
        self._subfiles = []
        self._path = ""
        self.subs_note.hide()
        for widget in (self.audio_label, self.audio_box, self.subs_label, self.subs_box):
            widget.hide()

    def tracks_changed(self, *_signal) -> None:
        """(the event loop) the file's tracks are known or changed: the menus; the series' choice is put on them
        once the file is open."""
        from core import video_tracks
        if self.backend is None or not self._path:
            return
        audio, playing, inside, shown, loaded = [], -1, [], -1, False
        try:
            loaded = self.backend.loaded(self._path)
            if loaded:
                audio, playing, inside, shown = self.backend.tracks()
        except Exception as exc:  # mpv gone or a player that cannot say: no menus
            _LOG.warning("tracks of the video not read: %s", exc)
            audio, playing, inside, shown, loaded = [], -1, [], -1, False
        subtitles = [dict(t, id=str(n)) for n, t in enumerate(inside)]
        subtitles += [{"id": f"x{n}", "name": f.name, "title": f.name, "lang": "", "file": True}
                      for n, f in enumerate(self._subfiles)]
        if loaded and not self._applied:
            self._applied = True
            voice, words = video_tracks.preferred(self._prefer, audio, subtitles)
            if voice is not None and voice != playing:
                self.backend.set_audio(voice)
                self._audio_choice = voice
            if words is not None:
                self._set_subtitle(words)
        if self._audio_choice is not None:
            playing = self._audio_choice
        key = (tuple(t["name"] for t in audio), tuple((t["id"], t["name"]) for t in subtitles))
        if key != self._tracks_key:
            self._tracks_key = key
            self.audio_box.clear()
            for track in audio:
                self.audio_box.addItem(track["name"], track)
            self.subs_box.clear()
            self.subs_box.addItem("Без субтитров", {"id": "-1"})
            for track in subtitles:
                self.subs_box.addItem(track["name"] + (" (файл)" if track.get("file") and inside else ""), track)
        self.audio_box.setCurrentIndex(playing if 0 <= playing < len(audio) else -1)
        current = self._sub_choice if self._sub_choice is not None else str(shown if shown >= 0 else -1)
        self.subs_box.setCurrentIndex(max(0, next((n for n in range(self.subs_box.count())
                                                   if (self.subs_box.itemData(n) or {}).get("id") == current), 0)))
        for widget in (self.audio_label, self.audio_box):
            widget.setVisible(len(audio) > 1)
        for widget in (self.subs_label, self.subs_box):
            widget.setVisible(bool(subtitles))

    def _audio_chosen(self, index: int) -> None:
        track = self.audio_box.itemData(index) or {}
        if self.backend is None or not track:
            return
        self.backend.set_audio(index)
        self._audio_choice = index
        self._tell("audio", {k: track.get(k, "") for k in ("name", "title", "lang")})
        self.setFocus()

    def _subs_chosen(self, index: int) -> None:
        track = self.subs_box.itemData(index) or {}
        if self.backend is None or not track:
            return
        choice = str(track.get("id") or "-1")
        self._set_subtitle(choice)
        self._tell("subtitle", {"off": True} if choice == "-1" else dict(
            {k: track.get(k, "") for k in ("name", "title", "lang")}, **({"file": True} if track.get("file") else {})))
        self.setFocus()

    def _tell(self, kind: str, value: dict) -> None:
        if self._on_choice is not None:
            try:
                self._on_choice(kind, value)
            except Exception as exc:  # the choice is still made; only not kept for the next episode
                _LOG.warning("the choice of %s not kept: %s", kind, exc)

    def _set_subtitle(self, choice: str) -> None:
        """"-1" — none; "N" — the file's track N; "xN" — the subtitle file N next to it."""
        from core import video_tracks
        self.subs_note.hide()
        self._cue_timer.stop()
        self._cues = None
        if self._shown_text:
            self._shown_text = ""
            self.backend.set_text("")
        if choice.startswith("x") and choice[1:].isdigit() and int(choice[1:]) < len(self._subfiles):
            sub = self._subfiles[int(choice[1:])]
            self._sub_choice = choice
            self.backend.set_subtitle(-1)
            try:
                if self.backend.show_file(sub):              # mpv: it shows the file itself
                    return
            except Exception as exc:  # mpv refused the file: say so, like a file that does not read
                self._subtitle_trouble(sub.name, f"{sub.path}: {exc}")
                return
            path = self._path

            def read() -> None:
                try:
                    got = video_tracks.Cues(video_tracks.read_cues(sub.path))
                except (OSError, ValueError) as exc:
                    got = f"{sub.path}: {exc}"
                self._cues_ready.emit(got, choice, path)
            threading.Thread(target=read, name="video-subtitles", daemon=True).start()
            return
        number = int(choice) if choice.isdigit() else -1
        self._sub_choice = str(number)
        self.backend.set_subtitle(number)

    def _subtitle_trouble(self, name: str, why: str) -> None:
        _LOG.warning("subtitles «%s» not shown: %s", name, why)
        self.subs_note.setText(f"Субтитры «{name}» не прочитались")
        self.subs_note.setToolTip(why)
        self.subs_note.show()

    def _subtitles_read(self, got, choice: str, path: str) -> None:
        """(the event loop) a subtitle file read: its cues go on the frames — if it is still the one chosen."""
        if path != self._path or choice != self._sub_choice:
            return
        if isinstance(got, str):
            sub = self._subfiles[int(choice[1:])] if int(choice[1:]) < len(self._subfiles) else None
            self._subtitle_trouble(sub.name if sub is not None else choice, got)
            return
        self._cues = got
        self._text_ticks = 0
        self._cue_timer.start()
        self._show_cue()

    def _show_cue(self) -> None:
        """The cue of the moment put on the frames (again now and then: the player may clear it on its own)."""
        if self.backend is None or self._cues is None:
            return
        words = self._cues.at(self.backend.position()[0])
        self._text_ticks += 1
        if words != self._shown_text or (words and self._text_ticks % 10 == 0):
            self._shown_text = words
            self.backend.set_text(words)

    # ── internals ────────────────────────────────────────────────────────────
    def _apply_volume(self) -> None:
        muted = bool(self.settings.get("media_muted", False))
        self.mute_btn.setText("🔇" if muted else "🔊")
        if self.backend is not None:
            self.backend.set_volume(int(self.settings.get("media_volume", 50) or 0), muted)

    def _volume_changed(self, value: int) -> None:
        self.settings["media_volume"] = int(value)
        self._apply_volume()

    def _seek_released(self) -> None:
        self._seeking = False
        if self.backend is not None:
            _pos, duration = self.backend.position()
            if duration > 0:
                self.backend.seek(duration * self.seek.value() / 1000.0)

    def _tick(self) -> None:
        if self.backend is None:
            return
        if isinstance(self.backend, _MpvBackend):        # mpv tells nothing to the window: its tracks are looked at
            try:
                key = tuple((t.get("id"), t.get("type"), bool(t.get("selected")))
                            for t in (self.backend.player.track_list or []) if isinstance(t, dict))
            except Exception as exc:  # mpv shutting down
                _LOG.debug("mpv tracks: %s", exc)
                key = None
            if key is not None and key != self._mpv_tracks:
                self._mpv_tracks = key
                self.tracks_changed()
        pos, duration = self.backend.position()
        if self._path and (self._seek_to > 0 or self._pause_after) and self._loaded():
            if self._seek_to > 0 and self.backend.seekable():    # the page's moment, once the file is open
                self.backend.seek(self._seek_to)
                pos, self._seek_to = self._seek_to, 0.0
            if self._pause_after:
                self._pause_after = False
                self.backend.pause()
        self.time.setText(f"{_fmt(pos)} / {_fmt(duration)}")
        if duration > 0 and not self._seeking:
            self.seek.blockSignals(True)
            self.seek.setValue(int(1000 * pos / duration))
            self.seek.blockSignals(False)
        if not self._path or self._seek_to > 0:
            return
        playing = self.backend.playing()
        if playing and not self._seeking:
            if pos >= self._furthest - 1:
                self._resume_used = False            # the place is reached: offered again after a jump back
            self._furthest = max(self._furthest, pos)
        self._show_resume(pos, duration)
        if playing and time.monotonic() - self._said_at >= PROGRESS_EVERY:
            self._tell_progress(pos, duration, False)
        if not self._loop and duration > 0 and not self._ended and self.backend.ended():
            self._ended = True                       # an episode played to its end: the page goes on to the next
            QTimer.singleShot(0, self.close_player)

    def _loaded(self) -> bool:
        try:
            return bool(self.backend.loaded(self._path))
        except Exception as exc:  # mpv shutting down
            _LOG.debug("loaded: %s", exc)
            return False

    def _show_resume(self, pos: float, duration: float) -> None:
        """«Продолжить просмотр 12:34»: the furthest place reached, when it is more than ``RESUME_GAP`` seconds
        ahead (and not at the very end)."""
        ahead = (not self._resume_used and self._furthest - pos > video_library.RESUME_GAP
                 and (duration <= 0 or self._furthest < duration - video_library.RESUME_END))
        if ahead:
            text = f"▶  Продолжить просмотр {_fmt(self._furthest)}"
            if self.resume_btn.text() != text:
                self.resume_btn.setText(text)
        self.resume_btn.setVisible(ahead)

    def _resume_clicked(self) -> None:
        if self.backend is not None and self._furthest > 0:
            self.backend.seek(self._furthest)
            self._resume_used = True
            self.resume_btn.hide()
            self.setFocus()

    def _tell_progress(self, position: float, length: float, ended: bool) -> None:
        self._said_at = time.monotonic()
        if self._on_progress is None or length <= 0:
            return
        try:
            self._on_progress(float(position), float(length), bool(ended))
        except Exception as exc:  # what was watched is not kept this time; the play goes on
            _LOG.warning("what was watched not kept: %s", exc)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close_player()
        elif key == Qt.Key.Key_Space:
            self.toggle()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_A):
            self.keep_progress()
            self.navigate.emit(-1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_D):
            self.keep_progress()
            self.navigate.emit(1)
        elif key in (Qt.Key.Key_E, Qt.Key.Key_M):
            self.toggle_mute()
        elif key == Qt.Key.Key_J and self.backend is not None:
            self.backend.seek(self.backend.position()[0] - 5)
        elif key == Qt.Key.Key_L and self.backend is not None:
            self.backend.seek(self.backend.position()[0] + 5)
        else:
            super().keyPressEvent(event)
