from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QSizePolicy, QMessageBox
from PySide6.QtCore import Qt, QSize, QUrl, QTimer
from PySide6.QtGui import QPixmap, QDesktopServices, QMovie, QImageReader, QShortcut, QKeySequence
from core.favorites import load_favorites, save_favorites
from core.library import normalize_tag, find_sidecar, clean_tags
from core.image_safe import safe_thumbnail_path

# Try MPV first (best quality), fallback to Qt multimedia
try:
    import mpv as _mpv_module
    _MPV_AVAILABLE = True
except Exception:
    _mpv_module = None
    _MPV_AVAILABLE = False

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget
    _QT_MEDIA_AVAILABLE = True
except Exception:
    QMediaPlayer = None
    QAudioOutput = None
    QVideoWidget = None
    _QT_MEDIA_AVAILABLE = False



class StarRating(QWidget):
    """Clickable 1-5 star rating widget."""
    rating_changed = __import__("PySide6.QtCore", fromlist=["Signal"]).Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout
        self._rating = 0
        self._hover = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._stars = []
        for i in range(1, 6):
            btn = QPushButton("★")
            btn.setFixedSize(32, 32)
            btn.setObjectName("PostCtrl")
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;"
                "color:#404060;font-size:20px;padding:0;margin:0;}"
                "QPushButton:hover{color:#806030;}")
            btn.setToolTip(f"{i} {'звезда' if i == 1 else 'звезды' if i < 5 else 'звёзд'}")
            btn.clicked.connect(lambda _, n=i: self._set(n))
            btn.enterEvent = lambda e, n=i: self._on_hover(n)
            btn.leaveEvent = lambda e: self._on_hover(0)
            lay.addWidget(btn)
            self._stars.append(btn)
        self._refresh()

    def _set(self, n: int):
        # Click same rating = clear
        self._rating = 0 if self._rating == n else n
        self._refresh()
        self.rating_changed.emit(self._rating)

    def _on_hover(self, n: int):
        self._hover = n
        self._refresh()

    def _refresh(self):
        active = self._hover or self._rating
        for i, btn in enumerate(self._stars, 1):
            if i <= active:
                btn.setStyleSheet(
                    "QPushButton{background:transparent;border:none;"
                    "color:#f0c040;font-size:20px;padding:0;margin:0;}"
                    "QPushButton:hover{color:#ffd060;background:transparent;border:none;}")
            else:
                btn.setStyleSheet(
                    "QPushButton{background:transparent;border:none;"
                    "color:#404060;font-size:20px;padding:0;margin:0;}"
                    "QPushButton:hover{color:#806030;background:transparent;border:none;}")

    def set_rating(self, n: int):
        self._rating = max(0, min(5, n))
        self._refresh()

    def rating(self) -> int:
        return self._rating


class TagButton(QPushButton):
    def __init__(self, tag, single, dbl):
        super().__init__(normalize_tag(tag))
        self.tag = normalize_tag(tag)
        self.single = single
        self.dbl = dbl

    def mousePressEvent(self, e):
        self.single(self.tag)

    def mouseDoubleClickEvent(self, e):
        self.dbl(self.tag)


class PostPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.index = 0
        self.fit = "height"
        self.player = None
        self.audio = None
        self.video_widget = None
        self.video_active = False
        self.gif_movie = None
        self.context = []
        self.setFocusPolicy(Qt.StrongFocus)

        from PySide6.QtWidgets import QSlider, QFrame
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── Body (tags + image) ───────────────────────────────────────────
        body = QHBoxLayout()
        lay.addLayout(body, 1)

        self.tags_scroll = QScrollArea()
        self.tags_scroll.setWidgetResizable(True)
        self.tags_holder = QWidget()
        self.tags_lay = QVBoxLayout(self.tags_holder)
        self.tags_lay.setContentsMargins(8, 8, 8, 8)
        self.tags_scroll.setWidget(self.tags_holder)
        self.tags_scroll.setMinimumWidth(240)
        self.tags_scroll.setMaximumWidth(320)

        self.img_scroll = QScrollArea()
        self.img_scroll.setWidgetResizable(False)
        self.img_scroll.setAlignment(Qt.AlignCenter)

        self.img = QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setMinimumSize(1, 1)
        self.img.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.img_scroll.setWidget(self.img)

        body.addWidget(self.tags_scroll)
        body.addWidget(self.img_scroll, 1)

        # ── Bottom control bar ────────────────────────────────────────────
        from PySide6.QtWidgets import QSlider as _QSlider

        # Seek bar (hidden by default, shown for video)
        self._seek_bar = _QSlider(Qt.Horizontal)
        self._seek_bar.setRange(0, 0)
        self._seek_bar.setValue(0)
        self._seek_bar.setVisible(False)
        self._seek_bar.setFixedHeight(20)
        self._seek_bar.setCursor(Qt.PointingHandCursor)
        self._seek_bar.setStyleSheet(
            "QSlider{margin:0 0px;}"
            "QSlider::groove:horizontal{background:#1e2235;height:6px;border-radius:3px;margin:0;}"
            "QSlider::handle:horizontal{background:#9070e0;width:16px;height:16px;"
            "margin:-5px 0;border-radius:8px;}"
            "QSlider::sub-page:horizontal{background:#6b4fbb;border-radius:3px;}")
        self._seek_dragging = False
        self._seek_was_playing = False

        def _do_seek(val: int, resume: bool = False):
            """Seek player to val (ms), optionally resume playback."""
            if not self.player:
                return
            self._seek_bar.blockSignals(True)
            self._seek_bar.setValue(val)
            self._seek_bar.blockSignals(False)
            self.player.setPosition(val)
            if resume and self._seek_was_playing:
                self.player.play()

        def _seek_mouse_press(e):
            """Click anywhere on the seek bar = instant seek."""
            from PySide6.QtCore import Qt as _Qt
            if e.button() != _Qt.LeftButton:
                return
            total = self._seek_bar.maximum() - self._seek_bar.minimum()
            if total <= 0:
                return
            ratio = e.position().x() / max(1, self._seek_bar.width())
            val = int(self._seek_bar.minimum() + total * max(0.0, min(1.0, ratio)))
            # Save play state, seek, keep paused while finger is down
            if self.player:
                self._seek_was_playing = (
                    self.player.playbackState() == QMediaPlayer.PlayingState)
                if self._seek_was_playing:
                    self.player.pause()
            self._seek_dragging = True
            _do_seek(val, resume=False)

        def _seek_mouse_release(e):
            from PySide6.QtCore import Qt as _Qt
            if e.button() != _Qt.LeftButton:
                return
            self._seek_dragging = False
            _do_seek(self._seek_bar.value(), resume=True)

        def _slider_pressed():
            self._seek_dragging = True
            if self.player:
                self._seek_was_playing = (
                    self.player.playbackState() == QMediaPlayer.PlayingState)
                if self._seek_was_playing:
                    self.player.pause()

        def _slider_moved(val):
            _do_seek(val, resume=False)

        def _slider_released():
            self._seek_dragging = False
            _do_seek(self._seek_bar.value(), resume=True)

        self._seek_bar.mousePressEvent = _seek_mouse_press
        self._seek_bar.mouseReleaseEvent = _seek_mouse_release
        self._seek_bar.sliderPressed.connect(_slider_pressed)
        self._seek_bar.sliderMoved.connect(_slider_moved)
        self._seek_bar.sliderReleased.connect(_slider_released)
        lay.addWidget(self._seek_bar)

        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setVisible(False)
        self._time_label.setStyleSheet("color:#666;font-size:11px;margin:0 8px;")
        self._time_label.setAlignment(Qt.AlignRight)

        ctrl = QWidget()
        ctrl.setFixedHeight(44)
        ctrl.setObjectName("PostCtrlBar")
        ctrl_lay = QHBoxLayout(ctrl)
        ctrl_lay.setContentsMargins(8, 4, 8, 4)
        ctrl_lay.setSpacing(4)

        def _cb(h=36):
            b = QPushButton()
            b.setObjectName("PostCtrl")
            b.setFixedHeight(h)
            return b

        def _icon_btn(icon_name: str, w: int = 38, h: int = 36, fallback: str = "") -> QPushButton:
            """PostCtrl button with icon from assets."""
            b = _cb(h)
            b.setFixedWidth(w)
            try:
                from pathlib import Path as _P
                _base = _P(__file__).parent.parent / "assets" / "icons"
                _theme = getattr(self.main, "settings", {}).get("appearance", "dark")
                _light = _theme == "light"
                _suffix = "_dark" if _light else ""
                for _n in [f"{icon_name}{_suffix}", icon_name]:
                    _p = _base / f"{_n}.ico"
                    if _p.exists():
                        from PySide6.QtGui import QIcon
                        from PySide6.QtCore import QSize
                        _ico = QIcon(str(_p))
                        if not _ico.isNull():
                            b.setIcon(_ico)
                            b.setIconSize(QSize(18, 18))
                            return b
            except Exception:
                pass
            if fallback:
                b.setText(fallback)
            return b

        self.back = _icon_btn("back", w=44, fallback="←")
        self.prev = _icon_btn("post_prev", w=40, fallback="←")
        self.fit_btn = _icon_btn("fit_h", w=40, fallback="▭")
        self.play_btn = _cb(); self.play_btn.setFixedWidth(38); self.play_btn.setVisible(False)
        self.fav = _icon_btn("favorite", w=40, fallback="♥")
        self.next = _icon_btn("post_next", w=40, fallback="→")

        # Volume: vertical popup slider
        self.volume_btn = _icon_btn("volume", w=40, fallback="🔊")
        self._vol_popup = QWidget(self, Qt.Popup)
        self._vol_popup.setFixedSize(44, 140)
        self._vol_popup.setStyleSheet(
            "background:#1a1d26;border:1px solid #2e3347;border-radius:10px;")
        vp_lay = QVBoxLayout(self._vol_popup)
        vp_lay.setContentsMargins(8, 10, 8, 10)
        self._vol_slider = QSlider(Qt.Vertical)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setStyleSheet(
            "QSlider::groove:vertical{background:#2e3347;width:6px;border-radius:3px;}"
            "QSlider::handle:vertical{background:#9070e0;width:16px;height:16px;"
            "margin:-5px -5px;border-radius:8px;}"
            "QSlider::add-page:vertical{background:#6b4fbb;border-radius:3px;}"
            "QSlider::sub-page:vertical{background:#2e3347;border-radius:3px;}")
        vp_lay.addWidget(self._vol_slider)
        self._vol_label = QLabel("80%")
        self._vol_label.setAlignment(Qt.AlignCenter)
        self._vol_label.setStyleSheet("color:#c9cdd6;font-size:11px;")
        vp_lay.addWidget(self._vol_label)
        self._vol_slider.valueChanged.connect(self._on_vol_changed)

        # zoom + fullscreen buttons
        self.zoom_in_btn  = _icon_btn("zoom_in",  w=36, fallback="+")
        self.zoom_out_btn = _icon_btn("zoom_out", w=36, fallback="-")
        self.zoom_in_btn.setToolTip("Zoom in  (scroll up)")
        self.zoom_out_btn.setToolTip("Zoom out (scroll down)")
        self.zoom_in_btn.clicked.connect(self._zoom_in)
        self.zoom_out_btn.clicked.connect(self._zoom_out)

        self.fullscreen_btn = _icon_btn("fullscreen", w=36, fallback="⛶")
        self.fullscreen_btn.setToolTip("Fullscreen (F11)")
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)

        self.star_rating = StarRating()
        self.star_rating.rating_changed.connect(self._on_rating_changed)

        ctrl_lay.addWidget(self.back)
        ctrl_lay.addWidget(self._time_label)
        ctrl_lay.addStretch(2)
        ctrl_lay.addWidget(self.prev)
        ctrl_lay.addSpacing(8)
        ctrl_lay.addWidget(self.zoom_out_btn)
        ctrl_lay.addWidget(self.fit_btn)
        ctrl_lay.addWidget(self.zoom_in_btn)
        ctrl_lay.addSpacing(4)
        ctrl_lay.addWidget(self.star_rating)
        ctrl_lay.addWidget(self.fav)
        ctrl_lay.addWidget(self.volume_btn)
        ctrl_lay.addWidget(self.fullscreen_btn)
        ctrl_lay.addSpacing(8)
        ctrl_lay.addWidget(self.next)
        ctrl_lay.addStretch(2)
        lay.addWidget(ctrl)

        self.back.clicked.connect(self.back_to_gallery)
        self.prev.clicked.connect(self.prev_post)
        self.next.clicked.connect(self.next_post)
        self.fav.clicked.connect(self.toggle_fav)
        self.fit_btn.clicked.connect(self.toggle_fit)
        self.volume_btn.clicked.connect(self._toggle_vol_popup)
        self.play_btn.clicked.connect(self.toggle_video)
        self.setup_shortcuts()
        self.retranslate()

    def setup_shortcuts(self):
        def add(key, func):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(func)
            return sc

        self._shortcuts = [
            add("A", lambda: self.prev_post() if self.prev.isVisible() else None),
            add("D", lambda: self.next_post() if self.next.isVisible() else None),
            add("F", self.toggle_fav),
            add("W", self.toggle_fit),
            add("E", self.toggle_volume),
            add("Q", self.back_to_gallery),
            add("F11", self._toggle_fullscreen),
            add("+", self._zoom_in),
            add("-", self._zoom_out),
            add("0", self._zoom_reset),
        ]

    _zoom_factor: float = 1.0

    def _zoom_in(self):
        self._zoom_factor = min(8.0, self._zoom_factor * 1.25)
        self._apply_zoom()

    def _zoom_out(self):
        self._zoom_factor = max(0.1, self._zoom_factor / 1.25)
        self._apply_zoom()

    def _zoom_reset(self):
        self._zoom_factor = 1.0
        self._apply_zoom()

    def _apply_zoom(self):
        try:
            item = self.item()
            if item and not item.get("is_video"):
                path = item.get("path", "")
                if path:
                    self.render_img(
                        __import__("pathlib").Path(path),
                        item,
                        zoom=self._zoom_factor,
                    )
        except Exception:
            pass

    def _toggle_fullscreen(self):
        win = self.window()
        if win.isFullScreen():
            win.showNormal()
            try:
                from PySide6.QtGui import QIcon
                from PySide6.QtCore import QSize
                from pathlib import Path as _P
                _p = _P(__file__).parent.parent / "assets" / "icons" / "fullscreen.ico"
                if _p.exists():
                    self.fullscreen_btn.setIcon(QIcon(str(_p)))
                    self.fullscreen_btn.setIconSize(QSize(18,18))
            except Exception:
                self.fullscreen_btn.setText("⛶")
            self.fullscreen_btn.setToolTip("Fullscreen (F11)")
        else:
            win.showFullScreen()
            try:
                from PySide6.QtGui import QIcon
                from PySide6.QtCore import QSize
                from pathlib import Path as _P
                _p = _P(__file__).parent.parent / "assets" / "icons" / "fullscreen_exit.ico"
                if _p.exists():
                    self.fullscreen_btn.setIcon(QIcon(str(_p)))
                    self.fullscreen_btn.setIconSize(QSize(18,18))
            except Exception:
                self.fullscreen_btn.setText("⊡")
            self.fullscreen_btn.setToolTip("Exit fullscreen (F11)")

    def _on_rating_changed(self, rating: int):
        """Save rating to DB when user clicks stars."""
        if self._current_image_id is None:
            return
        try:
            from core.database.connection import get_connection
            conn = get_connection(self.main.settings)
            conn.execute("UPDATE images SET rating=? WHERE id=?",
                        (rating, self._current_image_id))
            conn.commit()
        except Exception:
            pass

    def _load_rating(self, item: dict):
        """Load rating from DB for current image."""
        try:
            from core.database.connection import get_connection
            conn = get_connection(self.main.settings)
            # Get image_id by path
            path = item.get("path", "")
            row = conn.execute(
                "SELECT id, rating FROM images WHERE path=?", (path,)
            ).fetchone()
            if row:
                self._current_image_id = row[0]
                self.star_rating.set_rating(row[1] or 0)
            else:
                self._current_image_id = None
                self.star_rating.set_rating(0)
        except Exception:
            self._current_image_id = None
            self.star_rating.set_rating(0)

    def wheelEvent(self, event):
        """Wheel on image area = navigate posts; elsewhere = normal scroll."""
        # Only navigate if cursor is over the image viewer
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        img_rect = self.img_scroll.geometry()
        if img_rect.contains(pos):
            delta = event.angleDelta().y()
            if delta < 0:
                self.next_post()
            elif delta > 0:
                self.prev_post()
            event.accept()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        key = event.key()

        if key == Qt.Key_A:
            if self.prev.isVisible():
                self.prev_post()
            return

        if key == Qt.Key_D:
            if self.next.isVisible():
                self.next_post()
            return

        if key == Qt.Key_F:
            self.toggle_fav()
            return

        if key == Qt.Key_W:
            self.toggle_fit()
            return

        if key == Qt.Key_E:
            self.toggle_volume()
            return

        if key == Qt.Key_Q:
            self.back_to_gallery()
            return

        super().keyPressEvent(event)

    def stop_video(self):
        if self.player:
            try:
                self.player.stop()
                self.player.setSource(QUrl())
            except Exception:
                pass
        if self.gif_movie:
            try:
                self.gif_movie.stop()
            except Exception:
                pass
        self.gif_movie = None
        self.video_active = False


    def back_to_gallery(self):
        self.stop_video()
        self.main.go("Gallery")

    def hideEvent(self, event):
        self.stop_video()
        super().hideEvent(event)

    def retranslate(self):
        # back/prev/next use icons - just set tooltips
        self.back.setToolTip(self.main.t("Back to Gallery"))
        self.prev.setToolTip(self.main.t("Prev"))
        self.next.setToolTip(self.main.t("Next"))
        # Reload icons with correct theme
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _light = self.main.settings.get("appearance", "dark") == "light"
            _sfx = "_dark" if _light else ""
            _sz = QSize(18, 18)
            for btn, name in [
                (self.back, "back"),
                (self.prev, "post_prev"),
                (self.next, "post_next"),
            ]:
                for _n in [f"{name}{_sfx}", name]:
                    _p = _base / f"{_n}.ico"
                    if _p.exists():
                        _ico = QIcon(str(_p))
                        if not _ico.isNull():
                            btn.setIcon(_ico)
                            btn.setIconSize(_sz)
                            btn.setText("")
                            break
        except Exception:
            pass
        # fit_btn: show current mode as icon (⬛ = width, ▬ = height)
        # Update fit button icon based on current mode
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _theme = self.main.settings.get("appearance", "dark")
            _sfx = "_dark" if _theme == "light" else ""
            _fit_ico = f"fit_w{_sfx}" if self.fit == "width" else f"fit_h{_sfx}"
            for _n in [_fit_ico, _fit_ico.replace(_sfx, "")]:
                _p = _base / f"{_n}.ico"
                if _p.exists():
                    _ico = QIcon(str(_p))
                    if not _ico.isNull():
                        self.fit_btn.setIcon(_ico)
                        self.fit_btn.setIconSize(QSize(18, 18))
                        self.fit_btn.setText("")
                        break
            else:
                self.fit_btn.setText("◻" if self.fit == "width" else "▭")
        except Exception:
            self.fit_btn.setText("◻" if self.fit == "width" else "▭")
        self.fit_btn.setToolTip(self.main.t("Fit Width") if self.fit == "height" else self.main.t("Fit Height"))
        self.play_btn.setText("▶")
        self.play_btn.setToolTip("Play/Pause")
        self.render_volume_button()
        if self.main.gallery_page._batch:
            self.render_fav(Path(self.item()["path"]))

    def item(self):
        ctx = self.context or self.main.gallery_page._batch
        return ctx[self.index]

    def set_post(self, idx, context=None):
        self.stop_video()
        self.setFocus(Qt.OtherFocusReason)
        if context:
            # keep post navigation in the current gallery order/filter/sort
            self.context = list(context)
            current_path = str(Path(self.main.gallery_page._batch[idx]["path"])) if idx < len(self.main.gallery_page._batch) else None
            for i, item in enumerate(self.context):
                if str(Path(item.get("path", ""))) == current_path:
                    self.index = i
                    break
            else:
                self.index = max(0, min(idx, len(self.context) - 1))
        else:
            self.context = self.main.gallery_page._batch
            self.index = idx
        self.fit = "height"
        self.render()

    def showEvent(self, event):
        super().showEvent(event)
        # Ensure window is not off-screen after show
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen().availableGeometry()
            win = self.window()
            geo = win.geometry()
            if not screen.intersects(geo):
                win.setGeometry(
                    max(screen.x(), min(geo.x(), screen.right() - geo.width())),
                    max(screen.y(), min(geo.y(), screen.bottom() - geo.height())),
                    min(geo.width(), screen.width()),
                    min(geo.height(), screen.height()),
                )
        except Exception:
            pass

    def render(self):
        item = self.item()
        path = Path(item["path"])
        is_vid = bool(item.get("is_video"))
        # seek bar and play btn only for video
        self._seek_bar.setVisible(False)
        self._time_label.setVisible(False)
        self.play_btn.setVisible(False)  # video: click to pause; no separate button shown
        self.fit_btn.setVisible(not is_vid)
        self.render_media(path, item)
        self.render_tags(item)
        self.render_fav(path)
        ctx = self.context or self.main.gallery_page._batch
        gp = self.main.gallery_page
        per = gp._per_page()
        maxp = max(1, (gp._sql_total + per - 1) // per)
        has_next_page = gp._page < maxp
        self.prev.setVisible(self.index > 0)
        self.next.setVisible(self.index < len(ctx) - 1 or has_next_page)

    def ensure_image_widget(self):
        self.img_scroll.setWidgetResizable(False)
        if self.img_scroll.widget() is not self.img:
            self.img_scroll.takeWidget()
            self.img_scroll.setWidget(self.img)
        self.video_active = False

    def _init_mpv_player(self):
        """Create MPV player embedded in a QWidget container."""
        if self._mpv_player is not None:
            return True
        try:
            container = QWidget()
            container.setMinimumSize(200, 150)
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            container.setStyleSheet("background:black;")

            # MPV needs the native window ID
            wid = int(container.winId())

            player = _mpv_module.MPV(
                wid=wid,
                vo="gpu",
                hwdec="auto",
                loop="inf",
                keep_open=True,
                # Quiet
                msg_level="all=no",
            )
            player.volume = int(self.main.settings.get("media_volume", 80))
            player.mute = bool(self.main.settings.get("media_muted", False))

            # Click = pause
            _post = self
            def _vid_click(e):
                from PySide6.QtCore import Qt as _Qt
                if e.button() == _Qt.LeftButton:
                    _post.toggle_video()
            container.mousePressEvent = _vid_click

            self._mpv_player = player
            self._mpv_container = container

            # Timer to update seek bar
            self._mpv_timer = QTimer(self)
            self._mpv_timer.setInterval(500)
            self._mpv_timer.timeout.connect(self._mpv_update_seekbar)
            return True
        except Exception as e:
            self._mpv_player = None
            self._mpv_container = None
            if hasattr(self, 'append_log'):
                pass
            return False

    def _mpv_update_seekbar(self):
        """Update seek bar from MPV position."""
        try:
            if self._mpv_player is None:
                return
            pos = self._mpv_player.time_pos or 0.0
            dur = self._mpv_player.duration or 0.0
            if dur > 0:
                self._seek_bar.blockSignals(True)
                self._seek_bar.setValue(int(pos * 1000))
                self._seek_bar.blockSignals(False)
                def fmt(s):
                    s = int(s)
                    return f"{s//60}:{s%60:02d}"
                self._time_label.setText(f"{fmt(pos)} / {fmt(dur)}")
        except Exception:
            pass

    def ensure_video_widget(self):
        # Prefer MPV
        if _MPV_AVAILABLE:
            if self._init_mpv_player():
                self.img_scroll.setWidgetResizable(True)
                if self.img_scroll.widget() is not self._mpv_container:
                    self.img_scroll.takeWidget()
                    self.img_scroll.setWidget(self._mpv_container)
                    self._mpv_container.show()
                self.video_active = True
                return True

        # Fallback: Qt multimedia
        if not _QT_MEDIA_AVAILABLE:
            return False
        if self.video_widget is None:
            self.video_widget = QVideoWidget()
            self.video_widget.setMinimumSize(200, 150)
            self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            try:
                self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
            except Exception:
                pass
            _post = self
            def _vid_click(e):
                from PySide6.QtCore import Qt as _Qt
                if e.button() == _Qt.LeftButton:
                    _post.toggle_video()
            self.video_widget.mousePressEvent = _vid_click
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video_widget)
            self.player.mediaStatusChanged.connect(self.on_media_status)
            self.player.durationChanged.connect(self._on_duration_changed)
            self.player.positionChanged.connect(self._on_position_changed)
        self.img_scroll.setWidgetResizable(True)
        if self.img_scroll.widget() is not self.video_widget:
            self.img_scroll.takeWidget()
            self.img_scroll.setWidget(self.video_widget)
        self.video_active = True
        return True

    def render_media(self, path, item=None):
        if item and item.get("is_video"):
            self.render_video(path)
        elif path.suffix.lower() == ".gif":
            self.render_gif(path)
        else:
            self.render_img(path, item)

    def render_video(self, path):
        self.stop_video()
        if not self.ensure_video_widget():
            self.ensure_image_widget()
            self.img.setText("VIDEO\n" + path.name + "\n(install python-mpv for video)")
            self.img.adjustSize()
            return

        if _MPV_AVAILABLE and self._mpv_player is not None:
            # MPV playback
            vol = int(self.main.settings.get("media_volume", 80))
            muted = bool(self.main.settings.get("media_muted", False))
            self._mpv_player.volume = vol
            self._mpv_player.mute = muted
            self._mpv_player.play(str(path))
            # Setup seek bar with duration from MPV (async)
            self._seek_bar.setRange(0, 0)
            self._seek_bar.setVisible(True)
            self._time_label.setVisible(True)
            self._time_label.setText("0:00 / ?:??")
            self._mpv_timer.start()
            # Get duration after short delay
            def _get_dur():
                try:
                    dur = self._mpv_player.duration or 0.0
                    if dur > 0:
                        self._seek_bar.setRange(0, int(dur * 1000))
                    else:
                        QTimer.singleShot(500, _get_dur)
                except Exception:
                    pass
            QTimer.singleShot(300, _get_dur)
        else:
            # Qt multimedia fallback
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            try:
                self.player.setLoops(QMediaPlayer.Infinite)
            except Exception:
                pass
            self.apply_volume()
            self.player.play()
            self._seek_bar.setVisible(True)
            self._time_label.setVisible(True)

    def render_gif(self, path):
        self.stop_video()
        self.ensure_image_widget()
        self.img.clear()

        movie = QMovie(str(path))
        if not movie.isValid():
            self.render_img(path)
            return

        area = self.img_scroll.viewport().size()
        if area.width() < 20 or area.height() < 20:
            area = QSize(1000, 700)

        max_w = max(50, area.width() - 24)
        max_h = max(50, area.height() - 24)

        reader = QImageReader(str(path))
        base = reader.size()
        if not base.isValid() or base.width() <= 0 or base.height() <= 0:
            base = QSize(max_w, max_h)

        if self.fit == "width":
            ratio = max_w / max(1, base.width())
            target = QSize(max_w, max(1, int(base.height() * ratio)))
        else:
            target = base.scaled(QSize(max_w, max_h), Qt.KeepAspectRatio)

        movie.setCacheMode(QMovie.CacheAll)
        movie.setScaledSize(target)
        self.img.setMovie(movie)
        self.img.setFixedSize(target)
        self.gif_movie = movie
        movie.start()

    def toggle_video(self):
        if not self.item().get("is_video") or not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶")
        else:
            self.player.play()
            self.play_btn.setText("⏸")

    def on_media_status(self, status):
        if QMediaPlayer is None or not self.player:
            return
        try:
            if status == QMediaPlayer.EndOfMedia and not self._seek_dragging:
                # Loop: restart from beginning
                self._seek_bar.blockSignals(True)
                self._seek_bar.setValue(0)
                self._seek_bar.blockSignals(False)
                self.player.setPosition(0)
                QTimer.singleShot(50, self.player.play)
        except Exception:
            pass

    def apply_volume(self):
        muted = bool(self.main.settings.get("media_muted", False))
        vol_pct = int(self.main.settings.get("media_volume", 80))
        if _MPV_AVAILABLE and getattr(self, '_mpv_player', None):
            try:
                self._mpv_player.volume = vol_pct
                self._mpv_player.mute = muted
            except Exception:
                pass
        if self.audio:
            try:
                self.audio.setMuted(muted)
                self.audio.setVolume(vol_pct / 100.0)
            except Exception:
                pass
        self.render_volume_button()

    def render_volume_button(self):
        muted = bool(self.main.settings.get("media_muted", True))
        self.volume_btn.setToolTip("Звук выкл" if muted else "Звук вкл")
        vol = int(self.main.settings.get("media_volume", 50))
        self._vol_slider.blockSignals(True)
        self._vol_slider.setValue(0 if muted else vol)
        self._vol_slider.blockSignals(False)
        self._vol_label.setText("0%" if muted else f"{vol}%")
        # Update volume icon
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _light = self.main.settings.get("appearance", "dark") == "light"
            _sfx = "_dark" if _light else ""
            _ico_name = "volume_off" if muted else "volume"
            for _n in [f"{_ico_name}{_sfx}", _ico_name]:
                _p = _base / f"{_n}.ico"
                if _p.exists():
                    _ico = QIcon(str(_p))
                    if not _ico.isNull():
                        self.volume_btn.setIcon(_ico)
                        self.volume_btn.setIconSize(QSize(18, 18))
                        self.volume_btn.setText("")
                        return
        except Exception:
            pass
        self.volume_btn.setText("")  # icon is set above; no emoji text

    def _on_duration_changed(self, dur: int):
        self._seek_bar.setRange(0, max(1, dur))
        self._seek_bar.setVisible(dur > 0)
        self._time_label.setVisible(dur > 0)
        self._update_time_label(0, dur)

    def _on_position_changed(self, pos: int):
        if not self._seek_dragging:
            self._seek_bar.blockSignals(True)
            self._seek_bar.setValue(pos)
            self._seek_bar.blockSignals(False)
        dur = self.player.duration() if self.player else 0
        self._update_time_label(pos, dur)

    def _update_time_label(self, pos: int, dur: int):
        def fmt(ms):
            s = ms // 1000
            return f"{s//60}:{s%60:02d}"
        self._time_label.setText(f"{fmt(pos)} / {fmt(dur)}")

    def _on_seek(self, val: int):
        pass  # handled on release

    def _on_seek_release(self):
        self._seek_dragging = False
        val = self._seek_bar.value()
        if _MPV_AVAILABLE and getattr(self, '_mpv_player', None):
            try:
                self._mpv_player.seek(val / 1000.0, "absolute")
            except Exception:
                pass
            return
        if self.player:
            self.player.setPosition(val)

    def _toggle_vol_popup(self):
        if self._vol_popup.isVisible():
            self._vol_popup.hide()
            return
        btn_pos = self.volume_btn.mapToGlobal(self.volume_btn.rect().topLeft())
        popup_x = btn_pos.x() - (self._vol_popup.width() - self.volume_btn.width()) // 2
        popup_y = btn_pos.y() - self._vol_popup.height() - 4
        self._vol_popup.move(popup_x, popup_y)
        self._vol_popup.show()

    def _on_vol_changed(self, val: int):
        self._vol_label.setText(f"{val}%")
        self.main.settings["media_volume"] = val
        self.main.settings["media_muted"] = (val == 0)
        try:
            if self.audio:
                self.audio.setMuted(val == 0)
                self.audio.setVolume(val / 100.0)
        except Exception:
            pass
        # Icon updated via render_volume_button
        self.render_volume_button()

    def toggle_volume(self):
        self.main.settings["media_muted"] = not bool(self.main.settings.get("media_muted", True))
        self.main.save_settings()
        self.apply_volume()

    def render_img(self, path, item=None, zoom: float = None):
        self.stop_video()
        self.ensure_image_widget()
        self.img.clear()
        area = self.img_scroll.viewport().size()
        if area.width() < 20 or area.height() < 20:
            area = QSize(1000, 700)
        max_w = max(50, area.width() - 24)
        max_h = max(50, area.height() - 24)

        # Never QPixmap-load giant originals directly into UI memory.
        # Build/load a bounded preview first.
        _zoom = zoom if zoom is not None else getattr(self, "_zoom_factor", 1.0)
        target_w = max_w if self.fit == "width" else max_w
        target_h = max(50, int(max_h * 2)) if self.fit == "width" else max_h
        thumb = safe_thumbnail_path(path, target_w, target_h)
        raw = QPixmap(thumb) if thumb else QPixmap(str(path))
        if raw.isNull():
            self.img.setText(path.name)
            self.img.adjustSize()
            return
        if self.fit == "width":
            pix = raw.scaledToWidth(int(max_w * _zoom), Qt.SmoothTransformation)
        else:
            pix = raw.scaled(int(max_w * _zoom), int(max_h * _zoom),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img.setPixmap(pix)
        self.img.resize(pix.size())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.main.gallery_page._batch:
            return
        current = self.item()
        path = Path(current["path"])
        if current.get("is_video"):
            return
        if path.suffix.lower() == ".gif":
            self.render_gif(path)
        else:
            self.render_img(path, current)

    def clear_tags(self):
        while self.tags_lay.count():
            it = self.tags_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()


    def _current_sidecars(self, item=None):
        item = item or self.item()
        path = Path(item["path"])
        settings = self.main.settings
        tag_txt = find_sidecar(path, settings.get("tags_suffix", ".tags.txt"), "tags")
        src_txt = find_sidecar(path, settings.get("sources_suffix", ".sources.txt"), "sources")
        tag_json = path.with_suffix(".tags.json")
        try:
            if path.parent.name == "media" and path.parent.parent.name in ("found", "partial_match", "no_match"):
                tag_json = path.parent.parent / "tags" / (path.stem + ".tags.json")
        except Exception:
            pass
        return path, tag_txt, src_txt, tag_json

    def remove_tag_from_current(self, tag):
        tag = normalize_tag(tag)
        item = self.item()
        path, tag_txt, src_txt, tag_json = self._current_sidecars(item)

        groups = item.get("tag_groups") or {"general": item.get("tags", [])}
        new_groups = {}
        removed = False
        for group, tags in groups.items():
            kept = []
            for t in tags or []:
                if normalize_tag(t).lower() == tag.lower():
                    removed = True
                    continue
                kept.append(normalize_tag(t))
            new_groups[group] = kept

        if not removed:
            return

        new_tags = []
        for xs in new_groups.values():
            for t in xs:
                if t and t not in new_tags:
                    new_tags.append(t)

        try:
            if tag_txt:
                tag_txt.parent.mkdir(parents=True, exist_ok=True)
                tag_txt.write_text(", ".join(new_tags), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Tag", f"Не смог записать теги:\n{e}")
            return

        try:
            import json
            if tag_json:
                tag_json.parent.mkdir(parents=True, exist_ok=True)
                tag_json.write_text(json.dumps(new_groups, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        item["tags"] = new_tags
        item["tag_groups"] = new_groups
        self.render_tags(item)

    def remove_source_from_current(self, source_url, source_host=""):
        item = self.item()
        path, tag_txt, src_txt, tag_json = self._current_sidecars(item)
        if not src_txt.exists():
            return
        try:
            lines = src_txt.read_text(encoding="utf-8", errors="ignore").splitlines()
            source_url = str(source_url or "")
            source_host = str(source_host or "")
            kept = []
            for line in lines:
                if source_url and source_url in line:
                    continue
                if source_host and source_host in line:
                    continue
                kept.append(line)
            src_txt.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            item["sources"] = [s for s in item.get("sources", []) if str(s.get("url","")) != source_url and str(s.get("host","")) != source_host]
            item["source_hosts"] = [s.get("host","") for s in item.get("sources", [])]
            self.render_tags(item)
        except Exception as e:
            QMessageBox.warning(self, "Source", f"Не смог удалить источник:\n{e}")


    def render_tags(self, item):
        TAG_COLORS = {
            "artist":    "#ff4040",   # red
            "character": "#3399ff",   # blue (not green, good contrast with artist)
            "copyright": "#c050a0",   # dark pink (20-30% darker than artist-adjacent pink)
            "general":   "#7090c0",   # muted blue-grey
            "meta":      "#cc8800",   # amber
            "parody":    "#a040b0",   # purple
            "language":  "#558866",   # muted green
            "category":  "#4499aa",   # teal
        }
        self.clear_tags()
        title = QLabel(self.main.t("Tags"))
        title.setStyleSheet("font-size:14px;font-weight:800;margin-bottom:4px")
        self.tags_lay.addWidget(title)
        groups = item.get("tag_groups") or {"general": item.get("tags", [])}
        for g in ["artist","character","copyright","general","meta","parody","language","category"]:
            if not groups.get(g):
                continue
            color = TAG_COLORS.get(g, "#888888")
            lab = QLabel(g)
            lab.setStyleSheet(
                f"font-size:12px;font-weight:800;color:{color};"
                "margin-top:8px;margin-bottom:1px;padding-left:2px")
            self.tags_lay.addWidget(lab)
            for tag in groups[g]:
                btn = TagButton(tag, self.main.open_tag_single, self.main.open_tag_add)
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;border:none;"
                    f"border-radius:4px;padding:1px 6px;color:{color};font-size:12px;"
                    f"font-weight:500;text-align:left;margin:0;}}"
                    f"QPushButton:hover{{background:{color}20;}}"
                )
                self.tags_lay.addWidget(btn)
        src_lbl = QLabel("Источники")
        src_lbl.setStyleSheet("font-size:12px;font-weight:800;margin-top:10px;color:#888;")
        self.tags_lay.addWidget(src_lbl)

        # Try raw_metadata first (exact post URL)
        shown_urls = set()
        try:
            from core.database.connection import get_connection
            conn = get_connection(self.main.settings)
            path = item.get("path", "")
            row = conn.execute(
                """SELECT rm.post_url, rm.file_url, rm.site
                   FROM raw_metadata rm
                   JOIN images i ON i.id = rm.image_id
                   WHERE i.path = ?""", (path,)
            ).fetchone()
            if row:
                post_url, file_url, site = row
                if post_url:
                    shown_urls.add(post_url)
                    b = QPushButton(f"↗ Открыть пост ({site or 'source'})")
                    b.setStyleSheet(
                        "QPushButton{background:#0d1a2e;border:1px solid #1e4080;"
                        "border-radius:6px;padding:4px 8px;color:#7ab4ff;font-size:12px;}"
                        "QPushButton:hover{background:#102040;border-color:#4080cc;}")
                    u = post_url
                    b.clicked.connect(lambda _=False, url=u: QDesktopServices.openUrl(QUrl(url)))
                    self.tags_lay.addWidget(b)
                if file_url and file_url != post_url:
                    shown_urls.add(file_url)
                    b2 = QPushButton("↗ Прямая ссылка на файл")
                    b2.setStyleSheet(
                        "QPushButton{background:transparent;border:1px solid #333;"
                        "border-radius:5px;padding:2px 7px;color:#7aadff;font-size:11px;}"
                        "QPushButton:hover{border-color:#7aadff;}")
                    u2 = file_url
                    b2.clicked.connect(lambda _=False, url=u2: QDesktopServices.openUrl(QUrl(url)))
                    self.tags_lay.addWidget(b2)
        except Exception:
            pass

        # Fallback: sources table
        for s in item.get("sources", []):
            u = s.get("url", "")
            if u and u not in shown_urls:
                shown_urls.add(u)
                host = s.get("host", "source")
                b = QPushButton(f"↗ {host}")
                b.setStyleSheet(
                    "QPushButton{background:transparent;border:1px solid #333;"
                    "border-radius:5px;padding:2px 7px;color:#7aadff;font-size:11px;}"
                    "QPushButton:hover{border-color:#7aadff;}")
                b.clicked.connect(lambda _=False, url=u: QDesktopServices.openUrl(QUrl(url)))
                self.tags_lay.addWidget(b)

        if not shown_urls and not item.get("sources"):
            lbl = QLabel("нет источников")
            lbl.setStyleSheet("color:#505070;font-size:11px;")
            self.tags_lay.addWidget(lbl)

        self.tags_lay.addStretch(1)

    def render_fav(self, path):
        _is_fav = str(path) in load_favorites()
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _theme = self.main.settings.get("appearance", "dark")
            _sfx = "_dark" if _theme == "light" else ""
            _ico_name = f"favorite_on{_sfx}" if _is_fav else f"favorite{_sfx}"
            _p = _base / f"{_ico_name}.ico"
            if not _p.exists():
                _p = _base / f"favorite_on.ico" if _is_fav else _base / "favorite.ico"
            if _p.exists():
                _ico = QIcon(str(_p))
                if not _ico.isNull():
                    self.fav.setIcon(_ico)
                    self.fav.setIconSize(QSize(18, 18))
                    self.fav.setText("")
                    return
        except Exception:
            pass
        self.fav.setText("❤" if _is_fav else "🤍")

    def toggle_fav(self):
        p = str(Path(self.item()["path"]))
        favs = load_favorites()
        favs.remove(p) if p in favs else favs.add(p)
        save_favorites(favs)
        self.render_fav(Path(p))

    def toggle_fit(self):
        self.fit = "width" if self.fit == "height" else "height"
        # Update fit button icon based on current mode
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _theme = self.main.settings.get("appearance", "dark")
            _sfx = "_dark" if _theme == "light" else ""
            _fit_ico = f"fit_w{_sfx}" if self.fit == "width" else f"fit_h{_sfx}"
            for _n in [_fit_ico, _fit_ico.replace(_sfx, "")]:
                _p = _base / f"{_n}.ico"
                if _p.exists():
                    _ico = QIcon(str(_p))
                    if not _ico.isNull():
                        self.fit_btn.setIcon(_ico)
                        self.fit_btn.setIconSize(QSize(18, 18))
                        self.fit_btn.setText("")
                        break
            else:
                self.fit_btn.setText("◻" if self.fit == "width" else "▭")
        except Exception:
            self.fit_btn.setText("◻" if self.fit == "width" else "▭")
        self.fit_btn.setToolTip(self.main.t("Fit Width") if self.fit == "height" else self.main.t("Fit Height"))
        path = Path(self.item()["path"])
        if path.suffix.lower() == ".gif":
            self.render_gif(path)
        else:
            self.render_img(path, self.item())

    def prev_post(self):
        if self.index > 0:
            self.index -= 1
            self.fit = "height"
            self.render()

    def next_post(self):
        ctx = self.context or self.main.gallery_page._batch
        if self.index < len(ctx) - 1:
            self.index += 1
            self.fit = "height"
            self.render()
        else:
            self._load_next_gallery_page()

    def _load_next_gallery_page(self):
        gp = self.main.gallery_page
        per = gp._per_page()
        maxp = max(1, (gp._sql_total + per - 1) // per)
        if gp._page >= maxp:
            return  # already on last page
        from core.database.repository import search_items, enrich_items
        next_page = gp._page + 1
        offset = (next_page - 1) * per
        batch = search_items(
            self.main.settings,
            query=gp._last_filter.get("q", ""),
            source=gp._last_filter.get("src", "all"),
            bucket=gp._last_filter.get("bucket", "all"),
            limit=per,
            offset=offset,
            order=gp._last_filter.get("order", "path"),
        ) or []
        if not batch:
            return
        # Update gallery page state
        gp._page = next_page
        gp._batch = batch
        gp._clear_grid()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, gp._render_page)
        # Go to first item of new page immediately (batch is ready)
        try:
            enrich_items(self.main.settings, [batch[0]])
        except Exception:
            pass
        self.context = list(batch)
        self.index = 0
        self.fit = "height"
        self.render()