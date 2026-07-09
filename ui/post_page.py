from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QSizePolicy, QMessageBox, QMenu, QComboBox, QApplication
from PySide6.QtCore import Qt, QSize, QUrl, QTimer
from PySide6.QtGui import QPixmap, QDesktopServices, QMovie, QImageReader, QShortcut, QKeySequence, QFontMetrics
from core.favorites import load_favorites, set_favorite
from core.library import normalize_tag, clean_tags
from core.tag_utils import tag_display_color
from core.image_safe import safe_thumbnail_path, configure_pillow

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
    """Clickable tag row that never widens the post sidebar.

    Full names remain available through the tooltip and click actions; only the
    visible label is elided when a booru tag is longer than the fixed sidebar.
    """
    def __init__(self, tag, single, dbl):
        super().__init__()
        self.tag = normalize_tag(tag)
        self.single = single
        self.dbl = dbl
        self.setToolTip(self.tag)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self._set_elided_text(205)

    def _set_elided_text(self, available_width=None):
        width = int(available_width if available_width is not None else self.width() - 14)
        width = max(24, width)
        self.setText(QFontMetrics(self.font()).elidedText(self.tag, Qt.ElideRight, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_elided_text()

    def mousePressEvent(self, e):
        self.single(self.tag)

    def mouseDoubleClickEvent(self, e):
        self.dbl(self.tag)


class PostPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.index = 0
        self.fit = "contain"
        self.player = None
        self.audio = None
        self.video_widget = None
        self.video_active = False
        self.gif_movie = None
        self.context = []
        self._page_history: list[tuple[int, list[dict]]] = []
        self._current_image_id: int | None = None  # for star rating
        self._mpv_player = None
        self._mpv_container = None
        self._mpv_timer = None
        self._tag_source_mode = "all"
        self._return_workspace = "Gallery"
        self._online_preview_context = False
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
        # Keep the media viewport stable: very long tags must never resize the
        # post sidebar. TagButton elides visible names and keeps full text in a tooltip.
        self.tags_scroll.setFixedWidth(250)
        self.tags_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tags_holder.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.img_scroll = QScrollArea()
        self.img_scroll.setWidgetResizable(False)
        self.img_scroll.setAlignment(Qt.AlignCenter)

        self.img = QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setMinimumSize(1, 1)
        self.img.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.img.setContextMenuPolicy(Qt.CustomContextMenu)
        self.img.customContextMenuRequested.connect(self._show_media_context_menu)
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
                _light = _theme in ("light", "r34", "win95", "windows95")
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

        keys = {"previous":"A", "next":"D", "favorite":"F", "fit":"W", "volume":"E", "back":"Q", "fullscreen":"F11", "zoom_in":"+", "zoom_out":"-", "zoom_reset":"0"}
        try:
            keys.update(self.main.settings.get("hotkeys") or {})
        except Exception:
            pass
        self._shortcuts = [
            add(keys["previous"], lambda: self.prev_post() if self.prev.isVisible() else None),
            add(keys["next"], lambda: self.next_post() if self.next.isVisible() else None),
            add(keys["favorite"], self.toggle_fav),
            add(keys["fit"], self.toggle_fit),
            add(keys["volume"], self.toggle_volume),
            add(keys["back"], self.back_to_gallery),
            add(keys["fullscreen"], self._toggle_fullscreen),
            add(keys["zoom_in"], self._zoom_in),
            add(keys["zoom_out"], self._zoom_out),
            add(keys["zoom_reset"], self._zoom_reset),
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

    def _ensure_rating_image_id(self, item: dict | None = None):
        """Return/create SQLite image id for the current post.

        Rating used to silently do nothing when the post was opened from a
        context that had no already-loaded SQLite id.  This helper makes rating
        independent from gallery enrichment: use item['id'] if present, otherwise
        find/create the image row by path.
        """
        try:
            item = item or self.item()
            if not item or item.get("_online_preview") or getattr(self, "_online_preview_context", False):
                return None
            if item.get("id") is not None:
                return int(item.get("id"))
            path = item.get("path", "")
            if not path:
                return None
            from core.services.metadata_service import image_id_for_path
            return image_id_for_path(self.main.settings, path, create=True, status="manual_rating")
        except Exception as e:
            import logging
            logging.getLogger("local_booru").error("Rating image id error: %s", e)
            return None

    def _on_rating_changed(self, rating: int):
        """Save rating to DB when user clicks stars."""
        image_id = self._current_image_id or self._ensure_rating_image_id()
        if image_id is None:
            return
        try:
            from core.services.metadata_service import set_rating
            set_rating(self.main.settings, int(image_id), int(rating))
            self._current_image_id = int(image_id)
            try:
                # keep current in-memory item consistent for current session
                self.item()["rating"] = int(rating)
            except Exception:
                pass
        except Exception as e:
            import logging
            logging.getLogger("local_booru").error("Rating save error: %s", e)

    def _load_rating(self, item: dict):
        """Load rating from DB for current image."""
        try:
            image_id = self._ensure_rating_image_id(item)
            if image_id is None:
                self._current_image_id = None
                self.star_rating.set_rating(0)
                return
            from core.services.metadata_service import get_rating
            self._current_image_id = int(image_id)
            self.star_rating.set_rating(get_rating(self.main.settings, int(image_id)))
        except Exception as e:
            import logging
            logging.getLogger("local_booru").error("Rating load error: %s", e)
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
        """Stop every playback backend before another post is rendered.

        MPV is the preferred backend, while ``self.player`` only exists for the
        Qt multimedia fallback.  Stopping only the Qt player left MPV audio
        playing after navigating from a video to an image.
        """
        if self._mpv_timer:
            try:
                self._mpv_timer.stop()
            except Exception:
                pass
        if self._mpv_player is not None:
            try:
                self._mpv_player.stop()
            except Exception:
                try:
                    self._mpv_player.command("stop")
                except Exception:
                    pass
        if self.player:
            try:
                self.player.stop()
                self.player.setSource(QUrl())
            except Exception:
                pass
        if self.gif_movie:
            old_movie = self.gif_movie
            try:
                old_movie.stop()
            except Exception:
                pass
            try:
                # QLabel retains a QMovie reference after stop(); on Windows
                # that is enough to keep the GIF file locked.
                self.img.setMovie(None)
                self.img.clear()
            except Exception:
                pass
            try:
                old_movie.deleteLater()
            except Exception:
                pass
        self.gif_movie = None
        self.video_active = False
        try:
            self._seek_bar.setVisible(False)
            self._seek_bar.setRange(0, 0)
            self._time_label.setVisible(False)
            self._time_label.setText("0:00 / 0:00")
        except Exception:
            pass


    def release_media_handles(self):
        """Release viewers before a managed media file is moved or deleted."""
        self.stop_video()
        try:
            self.img.setMovie(None)
            self.img.clear()
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass

    def back_to_gallery(self):
        self.stop_video()
        target = str(getattr(self, "_return_workspace", "Gallery") or "Gallery")
        self.main.go(target)
        if target == "Gallery":
            try:
                gp = self.main.gallery_page
                if getattr(gp, "_viewer_page_dirty", False):
                    QTimer.singleShot(0, gp.render_after_viewer_navigation)
            except Exception:
                pass

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
            _light = self.main.settings.get("appearance", "dark") in ("light", "r34", "win95", "windows95")
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
        # fit_btn: contain is safe default; width is explicit manual fit-to-width mode
        # Update fit button icon based on current mode
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _theme = self.main.settings.get("appearance", "dark")
            _sfx = "_dark" if _theme in ("light", "r34", "win95", "windows95") else ""
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
        self.fit_btn.setToolTip(self.main.t("Fit Width") if self.fit == "contain" else self.main.t("Fit Height"))
        self.play_btn.setText("▶")
        self.play_btn.setToolTip("Play/Pause")
        self.render_volume_button()
        if self.main.gallery_page._batch:
            self.render_fav(Path(self.item()["path"]))

    def item(self):
        ctx = self.context or self.main.gallery_page._batch
        return ctx[self.index]

    def set_post(self, idx, context=None, tag_source=None):
        self.stop_video()
        self.setFocus(Qt.OtherFocusReason)
        self._page_history = []
        self._zoom_factor = 1.0
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
        self.fit = "contain"
        self._return_workspace = "Gallery"
        self._online_preview_context = False
        if tag_source is not None:
            self._tag_source_mode = str(tag_source or "all").lower().replace("www.", "")
        # The tag source selected in the post viewer is a persistent viewing
        # mode. Opening another post must enrich metadata in that same mode,
        # otherwise the selector still says "gelbooru.com" while the body
        # silently renders the union from all sites.
        self._enrich_current()
        self.render()

    def set_online_posts(self, context, idx=0, tag_source=None, return_workspace="DLER"):
        """Open temporary online-grabber posts in the normal Post viewer.

        These items are not library media yet: no rating/favorite DB writes,
        Back returns to the grabber, and metadata comes from the online API
        payload already attached to the context.
        """
        self.stop_video()
        self.setFocus(Qt.OtherFocusReason)
        self._page_history = []
        self._zoom_factor = 1.0
        self.context = list(context or [])
        self.index = max(0, min(int(idx or 0), max(0, len(self.context) - 1)))
        self.fit = "contain"
        self._return_workspace = str(return_workspace or "DLER")
        self._online_preview_context = True
        self._tag_source_mode = str(tag_source or "all").lower().replace("www.", "")
        self.render()


    def showEvent(self, event):
        super().showEvent(event)
        # v334: Do not pull the main window back to the primary monitor when
        # opening the Post page from Gallery.  Older code clamped against
        # QGuiApplication.primaryScreen(), so a window placed on a secondary
        # monitor was treated as "off-screen" and moved to monitor #1.
        # Only recover the window if it is outside every available screen.
        try:
            from PySide6.QtGui import QGuiApplication
            win = self.window()
            geo = win.frameGeometry()
            screens = list(QGuiApplication.screens() or [])
            for scr in screens:
                if scr.availableGeometry().intersects(geo):
                    return

            # The window is genuinely off all monitors. Restore it to the
            # best-known current screen instead of blindly using primary.
            screen = QGuiApplication.screenAt(geo.center())
            if screen is None:
                try:
                    handle = win.windowHandle()
                    screen = handle.screen() if handle is not None else None
                except Exception:
                    screen = None
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            w = min(geo.width(), avail.width())
            h = min(geo.height(), avail.height())
            x = max(avail.left(), min(geo.x(), avail.right() - w + 1))
            y = max(avail.top(), min(geo.y(), avail.bottom() - h + 1))
            win.setGeometry(x, y, w, h)
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
        online = bool(item.get("_online_preview") or getattr(self, "_online_preview_context", False))
        self.fav.setVisible(not online)
        self.star_rating.setVisible(not online)
        if not online:
            self.render_fav(path)
            self._load_rating(item)
        else:
            self._current_image_id = None
            try:
                self.star_rating.set_rating(0)
            except Exception:
                pass
        ctx = self.context or self.main.gallery_page._batch
        if online:
            has_prev_page = False
            has_next_page = False
        else:
            gp = self.main.gallery_page
            per = gp._per_page()
            maxp = max(1, (gp._sql_total + per - 1) // per)
            has_next_page = gp._page < maxp
            # Button visibility must use the same navigation rules as wheel/hotkeys.
            # When a post is opened directly on page > 1 there is no in-viewer history,
            # but a previous SQL page still exists and must be reachable.
            has_prev_page = bool(self._page_history) or gp._page > 1
        self.prev.setVisible(self.index > 0 or has_prev_page)
        self.next.setVisible(self.index < len(ctx) - 1 or has_next_page)
        if online:
            self._schedule_online_preview_load()

    def _schedule_online_preview_load(self):
        """Ask DownloaderPage to load the selected preview quality for online posts.

        Opening from the grabber and navigating between online posts share the
        same Post page.  Every online placeholder must start its own 25/50/100%
        preview load; otherwise next/previous shows only the loading card.
        """
        try:
            item = self.item()
            if not isinstance(item, dict):
                return
            if not bool(item.get("_online_preview") or getattr(self, "_online_preview_context", False)):
                return
            if not item.get("_online_loading_preview"):
                return
            dp = getattr(self.main, "downloader_page", None)
            if dp is None or not hasattr(dp, "request_online_post_preview"):
                return
            QTimer.singleShot(0, lambda it=item: dp.request_online_post_preview(it))
        except Exception:
            pass

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
            container.setContextMenuPolicy(Qt.CustomContextMenu)
            container.customContextMenuRequested.connect(self._show_media_context_menu)

            # MPV needs the native window ID
            wid = int(container.winId())

            player = _mpv_module.MPV(
                wid=wid,
                vo="gpu",
                hwdec="auto",
                loop="inf",
                keep_open=True,
                keepaspect=True,
                panscan=0.0,
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
            self.video_widget.setContextMenuPolicy(Qt.CustomContextMenu)
            self.video_widget.customContextMenuRequested.connect(self._show_media_context_menu)
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

    def _render_online_loading_placeholder(self, item=None, message=None):
        """Show a deliberate online-grabber loading state instead of a tiny thumb.

        The grabber post view must not make a 180px/preview thumbnail look like
        the final opened image.  It opens immediately with this placeholder, then
        DownloaderPage replaces the path when the configured 25/50/100% preview
        file finishes downloading into the temporary grabber cache.
        """
        self.stop_video()
        self.ensure_image_widget()
        self.img.clear()
        quality = ""
        try:
            cand = (item or {}).get("_preview_candidate") or {}
            quality = str(cand.get("open_quality_label") or cand.get("open_quality") or "")
        except Exception:
            quality = ""
        if not quality:
            quality = "выбранное качество"
        text = str(message or "Загрузка предпросмотра…")
        self.img.setText(f"{text}\n{quality}\n\nПКМ → Скачать уже доступно; скачивание сохранит оригинал.")
        self.img.setMinimumSize(640, 360)
        self.img.setAlignment(Qt.AlignCenter)
        self.img.adjustSize()
        self._seek_bar.setVisible(False)
        self._time_label.setVisible(False)

    def render_media(self, path, item=None):
        # One unconditional teardown point: prevents audio/video from a previous
        # post surviving navigation, including video -> video transitions.
        self.stop_video()
        if item and (item.get("_online_preview") or getattr(self, "_online_preview_context", False)) and item.get("_online_loading_preview"):
            self._render_online_loading_placeholder(item)
            return
        if not Path(path).exists():
            if item and (item.get("_online_preview") or getattr(self, "_online_preview_context", False)):
                self._render_online_loading_placeholder(item, "Загрузка предпросмотра…" if item.get("_online_loading_preview") else "Предпросмотр ещё не загружен")
                return
            self.ensure_image_widget()
            self.img.clear()
            self.img.setText(f"Файл отсутствует\n{Path(path).name}\n\n{path}")
            self.img.setMinimumSize(520, 220)
            self.img.setAlignment(Qt.AlignCenter)
            self.img.adjustSize()
            self._seek_bar.setVisible(False)
            self._time_label.setVisible(False)
            return
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
        if not self.item().get("is_video"):
            return
        if self._mpv_player is not None and self.video_active:
            try:
                self._mpv_player.pause = not bool(self._mpv_player.pause)
                return
            except Exception:
                pass
        if not self.player:
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
            _light = self.main.settings.get("appearance", "dark") in ("light", "r34", "win95", "windows95")
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

    def _online_thumb_fallback_path(self, item, current_path=""):
        try:
            if not (item and (item.get("_online_preview") or getattr(self, "_online_preview_context", False))):
                return ""
            cand = item.get("_preview_candidate") or {}
            thumb = str(cand.get("thumb_path") or item.get("thumb_path") or "")
            if thumb and thumb != str(current_path or "") and Path(thumb).exists():
                return thumb
        except Exception:
            pass
        return ""

    def render_img(self, path, item=None, zoom: float = None):
        if not Path(path).exists():
            fallback = self._online_thumb_fallback_path(item, path)
            if fallback:
                return self.render_img(Path(fallback), dict(item or {}, _preview_candidate={}), zoom=zoom)
            self.ensure_image_widget()
            self.img.clear()
            self.img.setText(f"Файл отсутствует\n{Path(path).name}\n\n{path}")
            self.img.setMinimumSize(520, 220)
            self.img.setAlignment(Qt.AlignCenter)
            self.img.adjustSize()
            return
        """Render an opened image from its original aspect ratio, never from a stale card cache.

        Gallery thumbnails are deliberately tiny and asynchronous.  Reusing a cached card
        preview here could display the wrong/cropped shape in the full post view.  The
        viewer instead decodes a bounded preview directly from the original media.
        """
        self.stop_video()
        self.ensure_image_widget()
        self.img.clear()
        area = self.img_scroll.viewport().size()
        if area.width() < 20 or area.height() < 20:
            area = QSize(1000, 700)
        max_w = max(50, area.width() - 24)
        max_h = max(50, area.height() - 24)
        factor = max(0.1, float(zoom if zoom is not None else getattr(self, "_zoom_factor", 1.0)))

        reader = QImageReader(str(path))
        try:
            reader.setAutoTransform(True)
        except Exception:
            pass
        source_size = reader.size()
        if not source_size.isValid() or source_size.width() <= 0 or source_size.height() <= 0:
            # Safe fallback still keeps the full aspect ratio.
            thumb = safe_thumbnail_path(path, max_w, max_h)
            raw = QPixmap(thumb) if thumb else QPixmap(str(path))
            if raw.isNull():
                fallback = self._online_thumb_fallback_path(item, path)
                if fallback:
                    return self.render_img(Path(fallback), dict(item or {}, _preview_candidate={}), zoom=zoom)
                self.img.setText(path.name); self.img.adjustSize(); return
            source_size = raw.size()
            if self.fit == "width":
                target = QSize(int(max_w * factor), max(1, int(source_size.height() * max_w * factor / max(1, source_size.width()))))
            else:
                target = source_size.scaled(QSize(int(max_w * factor), int(max_h * factor)), Qt.KeepAspectRatio)
            pix = raw.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            if self.fit == "width":
                target_w = max(1, int(max_w * factor))
                target = QSize(target_w, max(1, int(source_size.height() * target_w / max(1, source_size.width()))))
            else:
                target = source_size.scaled(QSize(max(1, int(max_w * factor)), max(1, int(max_h * factor))), Qt.KeepAspectRatio)
            try:
                reader.setScaledSize(target)
            except Exception:
                pass
            image = reader.read()
            pix = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
            if pix.isNull():
                thumb = safe_thumbnail_path(path, target.width(), target.height())
                pix = QPixmap(thumb) if thumb else QPixmap(str(path)).scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if pix.isNull():
            fallback = self._online_thumb_fallback_path(item, path)
            if fallback:
                return self.render_img(Path(fallback), dict(item or {}, _preview_candidate={}), zoom=zoom)
            self.img.setText(path.name); self.img.adjustSize(); return
        self.img.setPixmap(pix)
        self.img.setFixedSize(pix.size())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.main.gallery_page._batch and not getattr(self, "_online_preview_context", False):
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


    def remove_tag_from_current(self, tag):
        tag = normalize_tag(tag)
        item = self.item()
        path = Path(item["path"])
        try:
            from core.services.metadata_service import remove_media_tag_link
            if not remove_media_tag_link(self.main.settings, path, tag, self._tag_source_mode):
                raise RuntimeError("тег или файл не найден в SQLite")
            from core.services.library_service import enrich_items
            enrich_items(self.main.settings, [item], tag_source=self._tag_source_mode)
        except Exception as e:
            QMessageBox.warning(self, "Тег", f"Не смог удалить тег из базы:\n{e}")
            return
        self.render_tags(item)


    def remove_source_from_current(self, source_url, source_host=""):
        item = self.item()
        path = Path(item["path"])
        try:
            from core.services.metadata_service import remove_media_source_link
            if not remove_media_source_link(self.main.settings, path, source_url, source_host):
                return
            item["sources"] = [s for s in item.get("sources", []) if str(s.get("url", "")) != str(source_url or "") and str(s.get("host", "")) != str(source_host or "")]
            item["source_hosts"] = [s.get("host", "") for s in item.get("sources", [])]
            from core.services.library_service import enrich_items
            enrich_items(self.main.settings, [item], tag_source=self._tag_source_mode)
            self.render_tags(item)
        except Exception as e:
            QMessageBox.warning(self, "Source", f"Не смог удалить источник из базы:\n{e}")


    def _select_tag_source(self, host):
        self._tag_source_mode = str(host or "all")
        try:
            item = self.item()
            # Online grabber posts already carry tag_groups_by_source in memory.
            # Re-enriching them from SQLite can wipe/ignore temporary visual
            # variant sources such as "booru.allthefallen.moe (похожий)".
            if item.get("_online_preview") or getattr(self, "_online_preview_context", False):
                self.render_tags(item)
                return
            from core.services.library_service import enrich_items
            enrich_items(self.main.settings, [item], tag_source=self._tag_source_mode)
            self.render_tags(item)
        except Exception:
            pass

    def _search_grabber_from_post_tag(self, tag, *, add=False):
        tag = normalize_tag(tag)
        if not tag:
            return
        try:
            dp = getattr(self.main, "downloader_page", None)
            if dp is None:
                return
            if add:
                parts = str(dp.preview_query.text() or "").split()
                if tag not in parts:
                    dp.preview_query.setText((str(dp.preview_query.text() or "").strip() + " " + tag).strip())
            else:
                dp.preview_query.setText(tag)
            # Online-post tags must continue the search inside the Grabber,
            # not jump to the local Gallery tag search.
            if hasattr(self.main, "go"):
                self.main.go("DLER")
            try:
                dp.preview_query.setFocus()
            except Exception:
                pass
            dp.search_online_preview()
        except Exception:
            pass

    def render_tags(self, item):
        TAG_COLORS = {
            "artist":    "#ff4040",   # red
            "contributor": "#e67e22", # e621 contributors
            "character": "#3399ff",   # blue (not green, good contrast with artist)
            "copyright": "#c050a0",   # dark pink (20-30% darker than artist-adjacent pink)
            "species":   "#22a6b3",   # e621 species
            "general":   "#7090c0",   # muted blue-grey
            "meta":      "#cc8800",   # amber
            "lore":      "#9b59b6",   # e621 lore
            "invalid":   "#7f8c8d",   # e621 invalid
            "parody":    "#a040b0",   # purple
            "language":  "#558866",   # muted green
            "category":  "#4499aa",   # teal
        }
        self.clear_tags()
        title = QLabel(self.main.t("Tags"))
        title.setStyleSheet("font-size:14px;font-weight:800;margin-bottom:4px")
        self.tags_lay.addWidget(title)
        per_source = item.get("tag_groups_by_source") or {}
        hosts = sorted(str(host) for host in per_source if str(host))
        wanted = str(self._tag_source_mode or "all")
        # Keep the chosen provenance scope while paging. If the next post has
        # no tags from that site, show an empty scope instead of silently
        # returning to the union and making unrelated sites look selected.
        if hosts or wanted != "all":
            source_label = QLabel("Показывать теги источника:")
            source_label.setStyleSheet("color:#888;font-size:11px;margin-top:2px;")
            self.tags_lay.addWidget(source_label)
            selector = QComboBox()
            selector.addItem("Все источники", "all")
            for host in hosts:
                selector.addItem(host, host)
            if wanted != "all" and wanted not in hosts:
                selector.addItem(f"{wanted} (нет тегов у этого файла)", wanted)
            pos = selector.findData(wanted)
            selector.setCurrentIndex(pos if pos >= 0 else 0)
            selector.currentIndexChanged.connect(lambda _idx, box=selector: self._select_tag_source(box.currentData()))
            self.tags_lay.addWidget(selector)
            if wanted != "all" and wanted not in hosts:
                no_tags = QLabel("У этого файла нет тегов выбранного источника.")
                no_tags.setWordWrap(True)
                no_tags.setStyleSheet("color:#888;font-size:11px;margin:3px 2px 5px 2px;")
                self.tags_lay.addWidget(no_tags)
        def _norm_groups(raw):
            out = {}
            if isinstance(raw, dict):
                for group, values in raw.items():
                    gname = str(group or "general")
                    vals = []
                    for value in values or []:
                        tag = normalize_tag(value)
                        if tag and tag not in vals:
                            vals.append(tag)
                    if vals:
                        out[gname] = vals
            return out

        base_groups = _norm_groups(item.get("tag_groups") or {"general": item.get("tags", [])})
        # The selector must actually change the rendered tag set.  This is
        # especially important for online grabber posts where exact sources and
        # visual-only sources can legitimately carry different ATF/e621 tags.
        if wanted != "all":
            groups = _norm_groups(per_source.get(wanted) or {})
        else:
            groups = base_groups
        for g in ["artist","contributor","character","copyright","species","general","meta","lore","invalid","parody","language","category"]:
            if not groups.get(g):
                continue
            color = TAG_COLORS.get(g, "#888888")
            lab = QLabel(g)
            lab.setStyleSheet(
                f"font-size:12px;font-weight:800;color:{color};"
                "margin-top:8px;margin-bottom:1px;padding-left:2px")
            self.tags_lay.addWidget(lab)
            for tag in groups[g]:
                tag_color = tag_display_color(tag, g, self.main.settings, TAG_COLORS)
                online_tag_mode = bool(
                    item.get("_online_preview")
                    or item.get("_preview_candidate")
                    or getattr(self, "_online_preview_context", False)
                    or str(getattr(self, "_return_workspace", "") or "") == "DLER"
                )
                if online_tag_mode:
                    btn = TagButton(
                        tag,
                        lambda t, self=self: self._search_grabber_from_post_tag(t, add=False),
                        lambda t, self=self: self._search_grabber_from_post_tag(t, add=True),
                    )
                else:
                    btn = TagButton(tag, self.main.open_tag_single, self.main.open_tag_add)
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;border:none;"
                    f"border-radius:4px;padding:1px 6px;color:{tag_color};font-size:12px;"
                    f"font-weight:500;text-align:left;margin:0;}}"
                    f"QPushButton:hover{{background:{tag_color}20;}}"
                )
                self.tags_lay.addWidget(btn)
        src_lbl = QLabel("Источники")
        src_lbl.setStyleSheet("font-size:12px;font-weight:800;margin-top:10px;color:#888;")
        self.tags_lay.addWidget(src_lbl)

        # Try raw_metadata first (exact post URL)
        shown_urls = set()
        try:
            from core.services.metadata_service import raw_metadata_for_path
            path = item.get("path", "")
            row = raw_metadata_for_path(self.main.settings, path)
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
                # Direct CDN/file URLs are intentionally not shown as sources.
                # The visible source list must contain only original post pages;
                # file_url remains stored in raw metadata for technical download
                # history/deduplication.
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
        _is_fav = str(path) in load_favorites(self.main.settings)
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _theme = self.main.settings.get("appearance", "dark")
            _sfx = "_dark" if _theme in ("light", "r34", "win95", "windows95") else ""
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

    def _show_media_context_menu(self, point):
        try:
            item = self.item()
            path = Path(item["path"])
        except Exception:
            return
        sender = self.sender()
        global_point = sender.mapToGlobal(point) if sender is not None and hasattr(sender, "mapToGlobal") else self.mapToGlobal(point)

        if bool(item.get("_online_preview") or getattr(self, "_online_preview_context", False)):
            menu = QMenu(self)
            download_action = menu.addAction("Скачать")
            open_action = menu.addAction("Открыть пост в браузере")
            copy_action = menu.addAction("Скопировать ссылку поста")
            chosen = menu.exec(global_point)
            cand = item.get("_preview_candidate") or {}
            post_urls = list(cand.get("post_urls") or [s.get("url") for s in item.get("sources", []) if s.get("url")])
            post_url = post_urls[0] if post_urls else ""
            if chosen == download_action:
                try:
                    self.main.downloader_page.download_preview_candidate(cand)
                except Exception as exc:
                    QMessageBox.warning(self, "Скачать", str(exc))
            elif chosen == open_action and post_url:
                QDesktopServices.openUrl(QUrl(post_url))
            elif chosen == copy_action and post_url:
                QApplication.clipboard().setText(post_url)
            return

        menu = QMenu(self)
        delete_action = menu.addAction("Удалить (в корзину)")
        chosen = menu.exec(global_point)
        if chosen != delete_action:
            return
        if QMessageBox.question(
            self, "Удалить файл",
            f"Переместить в корзину?\n\n{path.name}\n\nФайл можно будет восстановить в разделе «Удалено».",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            from core.library_lifecycle import trash_media_paths
            self.release_media_handles()
            result = trash_media_paths(self.main.settings, [path], reason="post_context_delete", make_backup=True)
            if result.get("error") or int(result.get("errors", 0) or 0):
                raise RuntimeError(result.get("error") or "Файл занят другим процессом и не был перемещён в корзину.")
            self.stop_video()
            self.main.gallery_page.refresh_force()
            try:
                self.main.trash_page.refresh()
            except Exception:
                pass
            self.main.go("Gallery")
        except Exception as exc:
            QMessageBox.warning(self, "Удаление", str(exc))

    def toggle_fav(self):
        p = str(Path(self.item()["path"]))
        enabled = p not in load_favorites(self.main.settings)
        # Write only the current item. Rewriting the entire favorites set was
        # unnecessary and could leave the gallery with stale paging state.
        set_favorite(self.main.settings, p, enabled)
        self.render_fav(Path(p))
        try:
            self.main.gallery_page.favorite_updated(p, enabled)
        except Exception:
            pass

    def toggle_fit(self):
        self.fit = "width" if self.fit == "contain" else "contain"
        # Update fit button icon based on current mode
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _base = _P(__file__).parent.parent / "assets" / "icons"
            _theme = self.main.settings.get("appearance", "dark")
            _sfx = "_dark" if _theme in ("light", "r34", "win95", "windows95") else ""
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
        self.fit_btn.setToolTip(self.main.t("Fit Width") if self.fit == "contain" else self.main.t("Fit Height"))
        path = Path(self.item()["path"])
        if path.suffix.lower() == ".gif":
            self.render_gif(path)
        else:
            self.render_img(path, self.item())

    def prev_post(self):
        if getattr(self, "_online_preview_context", False) and self.index <= 0:
            return
        if self.index > 0:
            self.index -= 1
            self.fit = "contain"
            self._zoom_factor = 1.0
            self._enrich_current()
            self.render()
        elif self._page_history:
            # Return through pages previously crossed while this viewer was open.
            page_number, previous_context = self._page_history.pop()
            self.context = list(previous_context)
            self.index = len(self.context) - 1
            gp = self.main.gallery_page
            gp.adopt_viewer_page(page_number, previous_context)
            self.fit = "contain"
            self._zoom_factor = 1.0
            self._enrich_current()
            self.render()
        elif self.main.gallery_page._page > 1:
            # Directly opening the first post of page 2+ has no page_history.
            # Fetch the previous SQL page so the Back button matches wheel/hotkeys.
            self._load_prev_gallery_page()

    def next_post(self):
        ctx = self.context or self.main.gallery_page._batch
        if self.index < len(ctx) - 1:
            self.index += 1
            self.fit = "contain"
            self._zoom_factor = 1.0
            self._enrich_current()
            self.render()
        else:
            if not getattr(self, "_online_preview_context", False):
                self._load_next_gallery_page()

    def _enrich_current(self):
        """Load full metadata for current item (tags, sources etc.)."""
        if getattr(self, "_online_preview_context", False):
            return
        try:
            ctx = self.context or self.main.gallery_page._batch
            if 0 <= self.index < len(ctx):
                from core.services.library_service import enrich_items
                enrich_items(self.main.settings, [ctx[self.index]], tag_source=self._tag_source_mode)
        except Exception:
            pass

    def _load_prev_gallery_page(self):
        gp = self.main.gallery_page
        if gp._page <= 1:
            return
        per = gp._per_page()
        from core.services.library_service import search_items, enrich_items
        previous_page = gp._page - 1
        offset = (previous_page - 1) * per
        batch = search_items(
            self.main.settings,
            query=gp._last_filter.get("q", ""),
            source=gp._last_filter.get("src", "all"),
            bucket=gp._last_filter.get("bucket", "all"),
            limit=per,
            offset=offset,
            order=gp._last_filter.get("order", "path"),
            extra_where=getattr(gp, "_last_extra_where", None),
            extra_params=getattr(gp, "_last_extra_params", None),
        ) or []
        if not batch:
            return
        gp.adopt_viewer_page(previous_page, batch)
        try:
            enrich_items(self.main.settings, [batch[-1]], tag_source=self._tag_source_mode)
        except Exception:
            pass
        self.context = list(batch)
        self.index = len(batch) - 1
        self.fit = "contain"
        self._zoom_factor = 1.0
        self.render()

    def _load_next_gallery_page(self):
        gp = self.main.gallery_page
        per = gp._per_page()
        maxp = max(1, (gp._sql_total + per - 1) // per)
        if gp._page >= maxp:
            return  # already on last page
        from core.services.library_service import search_items, enrich_items
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
            extra_where=getattr(gp, "_last_extra_where", None),
            extra_params=getattr(gp, "_last_extra_params", None),
        ) or []
        if not batch:
            return
        # Save a real page history so repeated previous-navigation can return
        # through page 3 -> page 2 -> page 1 rather than only one hop.
        self._page_history.append((gp._page, list(self.context or gp._batch)))
        # Keep gallery state in sync without painting its hidden thumbnails.
        # Rebuilding the grid while the full post is visible caused a visible
        # pause every time navigation crossed a page boundary.
        gp.adopt_viewer_page(next_page, batch)
        # Go to first item of new page immediately (batch is ready)
        try:
            enrich_items(self.main.settings, [batch[0]], tag_source=self._tag_source_mode)
        except Exception:
            pass
        self.context = list(batch)
        self.index = 0
        self.fit = "contain"
        self._zoom_factor = 1.0
        self.render()