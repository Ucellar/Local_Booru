from pathlib import Path
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLineEdit,QPushButton,QFileDialog,QLabel,QSpinBox,QMessageBox,QHBoxLayout,QComboBox,QCheckBox,QDialog,QSlider,QTextEdit,QListWidget,QSplitter,QListWidgetItem,QCompleter,QApplication,QListView,QAbstractSpinBox,QGroupBox,QSizePolicy,QDoubleSpinBox,QTableWidget,QTableWidgetItem,QColorDialog,QTabBar,QAbstractItemView
from PySide6.QtCore import Qt, QStringListModel, QSortFilterProxyModel, QEvent
from PySide6.QtGui import QPixmap, QColor, QKeySequence
from PIL import Image
from core.settings import save_settings
from core.tagger import result_output_base
from core.paths import CACHE_FILE
import shutil
import json
import re
from core.tag_utils import normalize_tag


class ContainsFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pattern = ""

    def setFilterFixedString(self, pattern):
        self._pattern = (pattern or "").lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._pattern:
            return True
        idx = self.sourceModel().index(source_row, 0, source_parent)
        text = str(self.sourceModel().data(idx) or "").lower()
        return self._pattern in text


class LogoCropDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent); self.path=Path(path); self.setWindowTitle("Logo crop editor"); self.resize(720,520)
        lay=QVBoxLayout(self); self.preview=QLabel(); self.preview.setAlignment(Qt.AlignCenter); self.preview.setMinimumHeight(260); lay.addWidget(self.preview,1)
        self.left=QSlider(Qt.Horizontal); self.left.setRange(0,100); self.top=QSlider(Qt.Horizontal); self.top.setRange(0,100); self.zoom=QSlider(Qt.Horizontal); self.zoom.setRange(100,300); self.zoom.setValue(100)
        form=QFormLayout(); form.addRow("Left",self.left); form.addRow("Top",self.top); form.addRow("Zoom",self.zoom); lay.addLayout(form)
        row=QHBoxLayout(); self.apply=QPushButton("Apply crop"); self.cancel=QPushButton("Cancel"); row.addStretch(1); row.addWidget(self.apply); row.addWidget(self.cancel); lay.addLayout(row)
        self.left.valueChanged.connect(self.update_preview); self.top.valueChanged.connect(self.update_preview); self.zoom.valueChanged.connect(self.update_preview); self.apply.clicked.connect(self.accept); self.cancel.clicked.connect(self.reject); self.update_preview()
    def crop_box(self):
        img=Image.open(self.path); w,h=img.size; target=3/1; zoom=self.zoom.value()/100
        cw=int(w/zoom); ch=int(cw/target)
        if ch>h:
            ch=int(h/zoom); cw=int(ch*target)
        cw=max(1,min(w,cw)); ch=max(1,min(h,ch)); x=int((w-cw)*self.left.value()/100); y=int((h-ch)*self.top.value()/100); return x,y,x+cw,y+ch
    def update_preview(self):
        try:
            img=Image.open(self.path).convert("RGB"); crop=img.crop(self.crop_box()).resize((480,160)); tmp=Path("logo_crop_preview_tmp.png"); crop.save(tmp); pix=QPixmap(str(tmp)); self.preview.setPixmap(pix.scaled(self.preview.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        except Exception: pass
    def save_crop(self):
        img=Image.open(self.path).convert("RGB"); crop=img.crop(self.crop_box()).resize((480,160)); out=Path("logo.png"); crop.save(out); return str(out)

class TagCategoryDialog(QDialog):
    DEFAULT_ORDER = ["artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid", "parody", "language", "category", "pages"]
    DEFAULT_COLORS = {"artist":"#ff3838","contributor":"#e67e22","character":"#00a000","copyright":"#ff54a7","species":"#22a6b3","general":"#004cff","meta":"#ff9900","lore":"#9b59b6","invalid":"#7f8c8d","parody":"#ff54a7","language":"#cc8800","category":"#00aaaa","pages":"#888888"}
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Цвета и порядок групп тегов")
        self.resize(470, 500)
        order = list(settings.get("tag_group_order") or self.DEFAULT_ORDER)
        for group in self.DEFAULT_ORDER:
            if group not in order:
                order.append(group)
        colors = dict(self.DEFAULT_COLORS); colors.update(settings.get("tag_group_colors") or {})
        lay = QVBoxLayout(self)
        hint = QLabel("Зажми группу левой кнопкой мыши и перетащи выше или ниже.\nДвойной клик или кнопка «Изменить цвет» меняют её цвет.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        for group in order:
            it = QListWidgetItem(group)
            it.setData(Qt.UserRole, colors.get(group, "#888888"))
            it.setForeground(QColor(colors.get(group, "#888888")))
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda _it: self.choose_color())
        lay.addWidget(self.list, 1)
        row=QHBoxLayout(); color=QPushButton("Изменить цвет"); reset=QPushButton("Сбросить порядок"); ok=QPushButton("Сохранить"); cancel=QPushButton("Отмена")
        row.addWidget(color); row.addWidget(reset); row.addStretch(1); row.addWidget(ok); row.addWidget(cancel); lay.addLayout(row)
        color.clicked.connect(self.choose_color); reset.clicked.connect(self._reset_order); ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
    def choose_color(self):
        it=self.list.currentItem()
        if not it: return
        c=QColorDialog.getColor(QColor(it.data(Qt.UserRole) or "#888888"), self, "Цвет категории")
        if c.isValid():
            it.setData(Qt.UserRole, c.name()); it.setForeground(c)
    def _reset_order(self):
        existing={self.list.item(i).text(): self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        self.list.clear()
        for group in self.DEFAULT_ORDER:
            it=QListWidgetItem(group); color=str(existing.get(group) or self.DEFAULT_COLORS.get(group,"#888888")); it.setData(Qt.UserRole,color); it.setForeground(QColor(color)); self.list.addItem(it)
    def values(self):
        order=[]; colors={}
        for i in range(self.list.count()):
            it=self.list.item(i); group=it.text(); order.append(group); colors[group]=str(it.data(Qt.UserRole) or "#888888")
        return order, colors


class HotkeysDialog(QDialog):
    LABELS = [("previous", "Предыдущий файл"), ("next", "Следующий файл"), ("favorite", "Избранное"), ("fit", "Вписать изображение"), ("volume", "Звук"), ("back", "Назад в галерею"), ("fullscreen", "Полный экран"), ("zoom_in", "Увеличить"), ("zoom_out", "Уменьшить"), ("zoom_reset", "Сбросить масштаб")]
    DEFAULTS = {"previous":"A","next":"D","favorite":"F","fit":"W","volume":"E","back":"Q","fullscreen":"F11","zoom_in":"+","zoom_out":"-","zoom_reset":"0"}
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.setWindowTitle("Горячие клавиши просмотрщика"); self.resize(420, 420)
        lay=QFormLayout(self); self.edits={}
        values=dict(self.DEFAULTS); values.update(settings.get("hotkeys") or {})
        for key, label in self.LABELS:
            edit=QLineEdit(str(values.get(key, self.DEFAULTS[key]))); edit.setMaximumWidth(130); self.edits[key]=edit; lay.addRow(label+":", edit)
        row=QHBoxLayout(); ok=QPushButton("Сохранить"); cancel=QPushButton("Отмена"); row.addStretch(1); row.addWidget(ok); row.addWidget(cancel); lay.addRow(row); ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
    def values(self):
        values={}
        for key, edit in self.edits.items():
            raw=edit.text().strip() or self.DEFAULTS[key]
            values[key]=QKeySequence(raw).toString() or raw
        return values


class SafeModuleList(QListWidget):
    """Drag&drop list that only moves existing sidebar modules; it never overwrites an item."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def _is_own_source(self, source):
        return source in (getattr(self.owner, "primary", None), getattr(self.owner, "extra", None))

    def dragEnterEvent(self, event):
        if self._is_own_source(event.source()):
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if self._is_own_source(event.source()):
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        event.ignore()

    def dropEvent(self, event):
        source = event.source()
        if not self._is_own_source(source):
            event.ignore()
            return
        source_row = source.currentRow()
        if source_row < 0:
            event.ignore()
            return
        pos = event.position().toPoint()
        under = self.itemAt(pos)
        target_row = self.row(under) if under is not None else self.count()
        if under is not None and pos.y() > self.visualItemRect(under).center().y():
            target_row += 1
        if source is self and source_row < target_row:
            target_row -= 1
        if source is self and source_row == target_row:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        item = source.takeItem(source_row)
        if item is None:
            event.ignore()
            return
        self.insertItem(max(0, min(target_row, self.count())), item)
        self.setCurrentItem(item)
        self.owner._selected_from(self)
        event.setDropAction(Qt.MoveAction)
        event.accept()


class InterfaceModulesDialog(QDialog):
    """Настройка бокового меню: свободное дерево страниц без жёстких 4 режимов."""
    MODULES = [
        ("Tagger", "Парсер", "apt"), ("ParserBlueprint", "Blueprint", "apt"), ("NO_MATCH", "Брак", "apt"),
        ("Overview", "Обзор", "gallery"), ("Gallery", "Галерея", "gallery"),
        ("Trash", "Удалено", "gallery"), ("Diagnostics", "Диагностика", "gallery"),
        ("Tags", "Теги", "gallery"), ("Manga", "Манга", "gallery"),
        ("Games", "Игры", "gallery"), ("DLER", "Граббер", "adp"),
        ("Subs", "Подписки", "adp"), ("Duplicates", "Дубли", "duplicates"),
    ]
    WORKSPACES = [("apt", "Парсер"), ("gallery", "Галерея"), ("adp", "Граббер"), ("duplicates", "Дубли")]

    ROLE_KEY = Qt.UserRole
    ROLE_WORKSPACE = Qt.UserRole + 1
    ROLE_VISIBLE = Qt.UserRole + 2
    ROLE_PARENT = Qt.UserRole + 3

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Разделы интерфейса")
        self.resize(860, 660)
        self._module_by_key = {key: (label, ws) for key, label, ws in self.MODULES}
        lay = QVBoxLayout(self)
        info = QLabel(
            "Свободная настройка интерфейса: любая страница может быть корнем или подпунктом любой другой страницы.\n"
            "Пример: Дубли → внутри Обзора, Граббер → внутри Манги, Blueprint → внутри Парсера. "
            "Пока родитель свернут, его подпункты в боковом меню не показываются."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self.free_nav = QCheckBox("Свободная навигация без 4 жёстких режимов")
        self.free_nav.setChecked(bool(settings.get("interface_free_navigation", True)))
        self.free_nav.setToolTip("Если включено, боковое меню строится как одно свободное дерево страниц. Рабочие режимы Парсер/Галерея/Граббер/Дубли больше не фильтруют страницы.")
        self.free_nav.stateChanged.connect(self._free_nav_changed)
        lay.addWidget(self.free_nav)

        columns = QHBoxLayout()
        self.primary = self._make_list("Основные разделы")
        self.extra = self._make_list("Дополнительно")
        lbox = QVBoxLayout(); lbox.addWidget(QLabel("Основные разделы")); lbox.addWidget(self.primary, 1)
        rbox = QVBoxLayout(); rbox.addWidget(QLabel("Дополнительно — можно свернуть")); rbox.addWidget(self.extra, 1)
        columns.addLayout(lbox, 1); columns.addLayout(rbox, 1)
        lay.addLayout(columns, 1)

        controls = QHBoxLayout()
        self.toggle_visible = QPushButton("Скрыть / показать выбранный")
        self.toggle_visible.clicked.connect(self._toggle_selected_visibility)
        controls.addWidget(self.toggle_visible); controls.addStretch(1)
        lay.addLayout(controls)

        workrow = QHBoxLayout()
        self.workspace_label = QLabel("Старый режим страницы:")
        self.workspace_label.setToolTip("Используется только если выключена свободная навигация.")
        workrow.addWidget(self.workspace_label)
        self.workspace = QComboBox()
        for key, title in self.WORKSPACES:
            self.workspace.addItem(title, key)
        self.workspace.currentIndexChanged.connect(self._workspace_changed)
        workrow.addWidget(self.workspace)
        workrow.addSpacing(18)
        workrow.addWidget(QLabel("Вложить в страницу:"))
        self.parent_combo = QComboBox()
        self.parent_combo.setMinimumWidth(210)
        self.parent_combo.currentIndexChanged.connect(self._parent_changed)
        workrow.addWidget(self.parent_combo)
        workrow.addStretch(1)
        lay.addLayout(workrow)

        hint = QLabel(
            "В свободной навигации режимов нет: видимость определяется только деревом страниц. "
            "Циклы запрещены технически, иначе дерево станет бесконечным."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8a91a8;")
        lay.addWidget(hint)

        self.auto_hide = QCheckBox("Убирать переключатель режимов, если осталась одна используемая группа")
        self.auto_hide.setChecked(bool(settings.get("auto_hide_single_workspace", True)))
        self.extra_collapsed = QCheckBox("Сворачивать «Дополнительно» при запуске")
        self.extra_collapsed.setChecked(bool(settings.get("interface_extra_collapsed", True)))
        lay.addWidget(self.auto_hide); lay.addWidget(self.extra_collapsed)

        row = QHBoxLayout()
        reset = QPushButton("Сбросить")
        ok = QPushButton("Сохранить")
        cancel = QPushButton("Отмена")
        row.addWidget(reset); row.addStretch(1); row.addWidget(ok); row.addWidget(cancel)
        lay.addLayout(row)
        self._initial_settings = json.loads(json.dumps(settings))
        reset.clicked.connect(self._reset); ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        self.primary.itemSelectionChanged.connect(lambda: self._selected_from(self.primary))
        self.extra.itemSelectionChanged.connect(lambda: self._selected_from(self.extra))
        self._populate(settings)
        self._free_nav_changed()

    def _free_nav_changed(self):
        enabled = bool(getattr(self, "free_nav", None) and self.free_nav.isChecked())
        try:
            self.workspace.setEnabled(not enabled)
            self.workspace_label.setEnabled(not enabled)
        except Exception:
            pass

    def _make_list(self, _label):
        return SafeModuleList(self)

    def _new_item(self, key, label, workspace, visible=True, parent_key=""):
        item = QListWidgetItem()
        item.setData(self.ROLE_KEY, key)
        item.setData(self.ROLE_WORKSPACE, workspace)
        item.setData(self.ROLE_VISIBLE, bool(visible))
        item.setData(self.ROLE_PARENT, str(parent_key or ""))
        item.setFlags((item.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
        self._render_item(item)
        return item

    def _render_item(self, item):
        key = str(item.data(self.ROLE_KEY) or "")
        label = self._module_by_key.get(key, (key, "gallery"))[0]
        visible = bool(item.data(self.ROLE_VISIBLE))
        parent = str(item.data(self.ROLE_PARENT) or "")
        prefix = "↳ " if parent else ""
        suffix = ""
        if parent in self._module_by_key:
            suffix = f"  → внутри: {self._module_by_key[parent][0]}"
        text = f"{prefix}{label}{suffix}"
        item.setText(text if visible else f"[скрыт]  {text}")
        item.setForeground(QColor("#8a91a8") if visible else QColor("#5a6070"))

    def _toggle_selected_visibility(self):
        item = self._selected_item()
        if not item:
            return
        key = str(item.data(self.ROLE_KEY) or "")
        item.setData(self.ROLE_VISIBLE, not bool(item.data(self.ROLE_VISIBLE)))
        self._render_item(item)

    def _populate(self, settings):
        self.primary.clear(); self.extra.clear()
        cfg = settings.get("interface_modules") or {}
        order = list(settings.get("interface_module_order") or [])
        defaults = [key for key, _, _ in self.MODULES]
        ordered = [key for key in order if key in defaults] + [key for key in defaults if key not in order]
        for key in ordered:
            label, default_ws = self._module_by_key[key]
            cur = cfg.get(key, {}) if isinstance(cfg, dict) else {}
            workspace = str(cur.get("workspace", default_ws))
            visible = bool(cur.get("visible", True))
            extra = bool(cur.get("extra", False))
            parent_key = str(cur.get("parent", "") or "")
            if parent_key == key or parent_key not in self._module_by_key:
                parent_key = ""
            (self.extra if extra else self.primary).addItem(self._new_item(key, label, workspace, visible, parent_key))
        if self.primary.count():
            self.primary.setCurrentRow(0)
        self._refresh_parent_combo()

    def _selected_item(self):
        return self.primary.currentItem() or self.extra.currentItem()

    def _selected_from(self, owner):
        item = owner.currentItem()
        if not item:
            return
        other = self.extra if owner is self.primary else self.primary
        other.blockSignals(True); other.clearSelection(); other.blockSignals(False)
        idx = self.workspace.findData(str(item.data(self.ROLE_WORKSPACE) or "gallery"))
        self.workspace.blockSignals(True); self.workspace.setCurrentIndex(max(0, idx)); self.workspace.blockSignals(False)
        self._refresh_parent_combo()

    def _workspace_changed(self):
        item = self._selected_item()
        if item:
            item.setData(self.ROLE_WORKSPACE, str(self.workspace.currentData() or "gallery"))

    def _refresh_parent_combo(self):
        item = self._selected_item()
        self.parent_combo.blockSignals(True)
        self.parent_combo.clear()
        self.parent_combo.addItem("Нет — отдельная страница", "")
        if item:
            key = str(item.data(self.ROLE_KEY) or "")
            current_parent = str(item.data(self.ROLE_PARENT) or "")
            for other_key, label, _ws in self.MODULES:
                if other_key == key:
                    continue
                self.parent_combo.addItem(label, other_key)
            idx = self.parent_combo.findData(current_parent)
            self.parent_combo.setCurrentIndex(max(0, idx))
        self.parent_combo.blockSignals(False)

    def _parent_changed(self):
        item = self._selected_item()
        if not item:
            return
        parent = str(self.parent_combo.currentData() or "")
        key = str(item.data(self.ROLE_KEY) or "")
        if parent == key:
            parent = ""
        item.setData(self.ROLE_PARENT, parent)
        # Legacy 4-workspace mode needs a child to follow the parent workspace.
        # Free navigation intentionally does not care about workspaces.
        if parent in self._module_by_key and not bool(self.free_nav.isChecked()):
            item.setData(self.ROLE_WORKSPACE, self._module_by_key[parent][1])
            idx = self.workspace.findData(self._module_by_key[parent][1])
            self.workspace.blockSignals(True); self.workspace.setCurrentIndex(max(0, idx)); self.workspace.blockSignals(False)
        self._render_item(item)

    def _reset(self):
        self.primary.clear(); self.extra.clear()
        for key, label, ws in self.MODULES:
            self.primary.addItem(self._new_item(key, label, ws, True, ""))
        self.free_nav.setChecked(True)
        self.auto_hide.setChecked(True)
        self.extra_collapsed.setChecked(True)
        self._free_nav_changed()
        self.primary.setCurrentRow(0)
        self._refresh_parent_combo()

    def _current_keys(self):
        keys = []
        for lst in (self.primary, self.extra):
            for row in range(lst.count()):
                keys.append(str(lst.item(row).data(self.ROLE_KEY) or ""))
        return keys

    def _items_by_key(self):
        out = {}
        for lst in (self.primary, self.extra):
            for row in range(lst.count()):
                item = lst.item(row)
                out[str(item.data(self.ROLE_KEY) or "")] = item
        return out

    def _has_parent_cycle(self, key, items):
        seen = set()
        parent = str(items[key].data(self.ROLE_PARENT) or "") if key in items else ""
        while parent:
            if parent == key or parent in seen:
                return True
            seen.add(parent)
            if parent not in items:
                return False
            parent = str(items[parent].data(self.ROLE_PARENT) or "")
        return False

    def _validate_structure(self, repair=True):
        expected = [key for key, _label, _ws in self.MODULES]
        keys = self._current_keys()
        missing = [key for key in expected if key not in keys]
        duplicated = sorted({key for key in keys if keys.count(key) > 1})
        unknown = [key for key in keys if key not in expected]
        items = self._items_by_key()
        cycles = [key for key in expected if key in items and self._has_parent_cycle(key, items)]
        bad_parent = []
        for key, item in items.items():
            parent = str(item.data(self.ROLE_PARENT) or "")
            if parent and parent not in expected:
                bad_parent.append(key)
        if not missing and not duplicated and not unknown and not cycles and not bad_parent and len(keys) == len(expected):
            return True
        if repair:
            self._populate(self._initial_settings)
            QMessageBox.warning(
                self, "Разделы интерфейса",
                "Перетаскивание или вложение повредило список разделов.\n"
                "Изменения НЕ сохранены, список восстановлен из последних корректных настроек.\n\n"
                f"Пропали: {', '.join(missing) or 'нет'}\n"
                f"Дубли: {', '.join(duplicated) or 'нет'}\n"
                f"Циклы вложения: {', '.join(cycles) or 'нет'}"
            )
        return False

    def accept(self):
        if self._validate_structure(repair=True):
            super().accept()

    def values(self):
        if not self._validate_structure(repair=False):
            raise ValueError("Повреждён список разделов интерфейса; сохранение отменено")
        result = {}; order = []; visible_any = False
        for lst, is_extra in ((self.primary, False), (self.extra, True)):
            for row in range(lst.count()):
                item = lst.item(row)
                key = str(item.data(self.ROLE_KEY))
                label, default_ws = self._module_by_key[key]
                visible = bool(item.data(self.ROLE_VISIBLE)); visible_any = visible_any or visible; order.append(key)
                parent = str(item.data(self.ROLE_PARENT) or "")
                cfg = {"visible": visible, "workspace": str(item.data(self.ROLE_WORKSPACE) or default_ws), "extra": is_extra}
                if parent:
                    cfg["parent"] = parent
                result[key] = cfg
        if not visible_any:
            # Do not hard-code Gallery anymore; just keep the first configured page visible.
            first = order[0] if order else "Gallery"
            result.setdefault(first, {})["visible"] = True
            result[first].setdefault("workspace", self._module_by_key.get(first, ("", "gallery"))[1])
            result[first].setdefault("extra", False)
        return result, order, bool(self.auto_hide.isChecked()), bool(self.extra_collapsed.isChecked()), bool(self.free_nav.isChecked())


class SettingsPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main
        lay=QVBoxLayout(self); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)
        # Scroll area — all content inside, works on any screen size
        from PySide6.QtWidgets import QScrollArea
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._inner = QWidget()
        self._inner.setAutoFillBackground(True)
        self._inner.setObjectName("SettingsInner")

        self.root=QLineEdit(); self.choose=QPushButton(); self.choose.clicked.connect(self.choose_root); row=QHBoxLayout(); row.addWidget(self.root,1); row.addWidget(self.choose)
        self.flaresolverr_url=QLineEdit(); self.flaresolverr_url.setPlaceholderText("http://localhost:8191  (оставь пустым если не используешь)")
        self.fs_test_inline=QPushButton("Проверить"); self.fs_test_inline.clicked.connect(self._test_flaresolverr); self.fs_test_inline.setMaximumWidth(100)
        _fs_row=QHBoxLayout(); _fs_row.addWidget(self.flaresolverr_url,1); _fs_row.addWidget(self.fs_test_inline)
        self.lang=QComboBox(); self.lang.setObjectName("SettingsCombo"); self.lang.setView(QListView()); self.lang.addItem("Русский","ru"); self.lang.addItem("English","en")
        self.appearance=QComboBox(); self.appearance.setObjectName("SettingsCombo"); self.appearance.setView(QListView()); self.appearance.addItem("Abyss (тёмный синий)","abyss"); self.appearance.addItem("Ember (тёмный янтарный)","ember"); self.appearance.addItem("Slate (нейтральный серый)","slate"); self.appearance.addItem("Sakura (тёмно-розовый)","sakura"); self.appearance.addItem("PornHub (чёрный+оранжевый)","pornhub"); self.appearance.addItem("R34 / Old web green","r34"); self.appearance.addItem("R34 Dark / Old web night","r34dark"); self.appearance.addItem("Windows 95","win95"); self.appearance.addItem("Light (светлый)","light")
        self.title=QLineEdit(); self.logo=QLineEdit(); self.logo_fit=QComboBox(); self.logo_fit.addItem("Crop","crop"); self.logo_fit.addItem("Contain","contain"); self.logo_choose=QPushButton(); self.logo_choose.clicked.connect(self.choose_logo); self.logo_crop=QPushButton(); self.logo_crop.clicked.connect(self.crop_logo)
        lrow=QHBoxLayout(); lrow.addWidget(self.logo,1); lrow.addWidget(self.logo_choose); lrow.addWidget(self.logo_crop)
        self.output_dir=QLineEdit(); self.choose_output=QPushButton("..."); self.choose_output.clicked.connect(self.choose_output_dir); self.connect_archive_btn = QPushButton("Подключить существующий архив..."); self.connect_archive_btn.clicked.connect(self.connect_existing_archive); outrow=QHBoxLayout(); outrow.addWidget(self.output_dir,1); outrow.addWidget(self.choose_output); outrow.addWidget(self.connect_archive_btn)
        # Storage is no longer a separate user decision: a connected archive always uses
        # Local_Booru_Archive/output + Local_Booru_Archive/settings.  Keep widgets
        # hidden only for backward-compatible load/save code.
        self.separate_settings = QCheckBox("Хранить служебные данные в Local_Booru_Archive/settings")
        self.settings_storage_dir = QLineEdit(); self.settings_storage_dir.setPlaceholderText("будет создано внутри выбранного Local_Booru_Archive")
        self.settings_storage_dir.setReadOnly(True)
        self.separate_settings.setVisible(False); self.settings_storage_dir.setVisible(False)
        storage_row = QHBoxLayout(); storage_row.addWidget(self.separate_settings); storage_row.addWidget(self.settings_storage_dir,1)
        self.debug_logging=QCheckBox()
        self.debug_logging.setChecked(False)

        self.cols=QSpinBox(); self.cols.setRange(1,24); self.rows=QSpinBox(); self.rows.setRange(1,20); self.card=QSpinBox(); self.card.setRange(100,700); self.ignore_numeric=QCheckBox(); self.show_preview=QCheckBox(); self.error_console=QCheckBox(); self.max_console_lines=QSpinBox(); self.max_console_lines.setRange(200,20000); self.max_console_lines.setSingleStep(100); self.manga_root=QLineEdit(); self.choose_manga=QPushButton("..."); self.choose_manga.clicked.connect(self.choose_manga_root); mrow=QHBoxLayout(); mrow.addWidget(self.manga_root,1); mrow.addWidget(self.choose_manga)
        self.games_root=QLineEdit(); self.choose_games=QPushButton("..."); self.choose_games.clicked.connect(self.choose_games_root); grow=QHBoxLayout(); grow.addWidget(self.games_root,1); grow.addWidget(self.choose_games)
        # Bare checkbox fields: show only the indicator, never a painted tail.
        for _cb in (self.debug_logging, self.ignore_numeric, self.show_preview, self.error_console):
            _cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            _cb.setFixedSize(23, 23)
        for _spin in (self.cols, self.rows, self.card, self.max_console_lines):
            _spin.setMaximumWidth(180)
        self.lang.setMaximumWidth(360)
        self.appearance.setMaximumWidth(420)
        self.logo_fit.setMaximumWidth(240)
        self.title.setMaximumWidth(620)
        self.form = QFormLayout()
        self.form.setContentsMargins(6, 5, 6, 5)
        self.form.setHorizontalSpacing(8)
        self.form.setVerticalSpacing(5)
        _ilay = QVBoxLayout(self._inner)
        _ilay.setContentsMargins(6, 6, 6, 6)
        _ilay.setSpacing(5)
        self.form_rows=[]
        for key, w, tip in [
            ("Images folder", row, "tip_root"),
            ("Language", self.lang, "tip_language"),
            ("Appearance", self.appearance, "tip_appearance"),
            ("Title", self.title, "tip_title"),
            ("Logo path", lrow, "tip_logo"),
            ("Logo fit", self.logo_fit, "tip_logo_fit"),
            ("Columns", self.cols, "tip_columns"),
            ("Rows/page", self.rows, "tip_rows"),
            ("Card height", self.card, "tip_card"),
            ("Debug logging", self.debug_logging, "tip_debug_logging"),
            ("Output folder", outrow, "tip_output_folder"),
            ("Хранение настроек", storage_row, "tip_output_folder"),
            ("Ignore numeric tags", self.ignore_numeric, "tip_numeric"),
            ("Search preview", self.show_preview, "tip_preview"),
            ("Console line limit", self.max_console_lines, "tip_console_limit"),
            ("Error console", self.error_console, "tip_error_console"),
            ("Manga folder", mrow, "tip_manga_root"),
            ("Папка игр", grow, "tip_games_root")
        ]:
            self.add_tip_row(key, w, tip)
        # Wrap form and buttons in scroll area for any screen size
        from PySide6.QtWidgets import QScrollArea as _SA
        _scroll = _SA(); _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(_SA.Shape.NoFrame)
        _inner = __import__("PySide6.QtWidgets",fromlist=["QWidget"]).QWidget()
        self.section_title = QLabel("Основные")
        self.section_title.setObjectName("SettingsSectionTitle")
        self.section_title.setStyleSheet("font-size:18px;font-weight:800;margin:4px 0 2px 2px")
        self.section_description = QLabel("")
        self.section_description.setWordWrap(True)
        self.section_description.setStyleSheet("color:#8991a8;margin:0 0 8px 2px")
        _ilay.addWidget(self.section_title)
        _ilay.addWidget(self.section_description)
        _ilay.addLayout(self.form)

        # FlareSolverr is not useful for Danbooru, but the current Ascii2D
        # fallback can still use it. Keep it available without wasting space on
        # the main settings screen.
        self.advanced_btn = QPushButton("Дополнительные настройки")
        self.advanced_btn.setCheckable(True)
        self.advanced_box = QGroupBox()
        _advanced_form = QFormLayout(self.advanced_box)
        _advanced_form.setContentsMargins(6, 6, 6, 6)
        _advanced_form.setSpacing(5)
        _advanced_form.addRow("FlareSolverr (Ascii2D / совместимость)", _fs_row)
        self.large_download_count = QSpinBox(); self.large_download_count.setRange(1, 1000000); self.large_download_count.setSuffix(" файлов"); self.large_download_count.setMaximumWidth(180)
        self.disk_reserve_gb = QDoubleSpinBox(); self.disk_reserve_gb.setRange(0.0, 1000.0); self.disk_reserve_gb.setDecimals(1); self.disk_reserve_gb.setSuffix(" ГБ резерва"); self.disk_reserve_gb.setMaximumWidth(180)
        _advanced_form.addRow("Предупреждать о большой загрузке от:", self.large_download_count)
        _advanced_form.addRow("Свободное место не опускать ниже:", self.disk_reserve_gb)
        self.advanced_box.setVisible(False)
        self.advanced_btn.toggled.connect(self.advanced_box.setVisible)
        _ilay.addWidget(self.advanced_btn)
        _ilay.addWidget(self.advanced_box)

        # Global actions are created here, but inserted into a fixed footer below
        # the scroll area. They must not move together with whichever subsection
        # happens to be open.
        self.save_btn = QPushButton()
        self.save_btn.clicked.connect(self.save)
        self.instruction_btn = QPushButton("Инструкция")
        self.instruction_btn.clicked.connect(self.show_instruction)

        self.maintenance_box = QGroupBox("Обслуживание библиотеки")
        _maintenance = QVBoxLayout(self.maintenance_box)
        _maintenance.setContentsMargins(6, 8, 6, 6)
        _maintenance.setSpacing(4)
        _mrow1 = QHBoxLayout()
        self.rebuild_sql_btn=QPushButton("Пересобрать SQLite-индекс")
        self.rebuild_sql_btn.clicked.connect(self.rebuild_sql_index)
        self.rebuild_vptree_btn = QPushButton("Пересобрать VP-tree")
        self.rebuild_vptree_btn.clicked.connect(self._rebuild_vptree)
        _mrow1.addWidget(self.rebuild_sql_btn, 1); _mrow1.addWidget(self.rebuild_vptree_btn, 1)
        _maintenance.addLayout(_mrow1)
        _mrow2 = QHBoxLayout()
        self.sql_optimize_btn=QPushButton("Оптимизировать SQLite")
        self.sql_optimize_btn.clicked.connect(self.optimize_sqlite)
        self.sql_stats_btn=QPushButton("Статистика SQLite")
        self.sql_stats_btn.clicked.connect(self.show_sqlite_stats)
        _mrow2.addWidget(self.sql_optimize_btn, 1); _mrow2.addWidget(self.sql_stats_btn, 1)
        _maintenance.addLayout(_mrow2)
        _mrow2b = QHBoxLayout()
        self.sql_backup_btn = QPushButton("Создать backup SQLite")
        self.sql_backup_btn.clicked.connect(self.force_sqlite_backup)
        self.sql_vacuum_btn = QPushButton("Сжать SQLite (VACUUM)")
        self.sql_vacuum_btn.clicked.connect(self.vacuum_sqlite)
        _mrow2b.addWidget(self.sql_backup_btn, 1); _mrow2b.addWidget(self.sql_vacuum_btn, 1)
        _maintenance.addLayout(_mrow2b)
        _mrow3 = QHBoxLayout()
        self.integrity_check_btn=QPushButton("Проверить целостность")
        self.integrity_check_btn.clicked.connect(self.check_library_integrity)
        self.integrity_repair_btn=QPushButton("Починить безопасные ошибки")
        self.integrity_repair_btn.clicked.connect(self.repair_library_integrity)
        _mrow3.addWidget(self.integrity_check_btn, 1); _mrow3.addWidget(self.integrity_repair_btn, 1)
        _maintenance.addLayout(_mrow3)
        _mrow4 = QHBoxLayout()
        self.repair_thumbs_btn = QPushButton("Проверить файлы / достроить превью")
        self.repair_thumbs_btn.clicked.connect(self.repair_missing_thumbnails)
        self.relocate_root_btn = QPushButton("Найти перенесённый архив")
        self.relocate_root_btn.clicked.connect(self.relocate_library_root)
        _mrow4.addWidget(self.repair_thumbs_btn, 1); _mrow4.addWidget(self.relocate_root_btn, 1)
        _maintenance.addLayout(_mrow4)
        _mrow4b = QHBoxLayout()
        self.sqlite_cache_mb = QSpinBox(); self.sqlite_cache_mb.setRange(8, 512); self.sqlite_cache_mb.setSuffix(" МБ SQLite-кэша")
        self.sqlite_checkpoint_exit = QCheckBox("WAL checkpoint при закрытии")
        _mrow4b.addWidget(QLabel("SQLite cache:")); _mrow4b.addWidget(self.sqlite_cache_mb); _mrow4b.addWidget(self.sqlite_checkpoint_exit); _mrow4b.addStretch(1)
        _maintenance.addLayout(_mrow4b)
        _mrow5 = QHBoxLayout()
        self.repair_e621_btn = QPushButton("Исправить загрязнённые e621-теги")
        self.repair_e621_btn.clicked.connect(self.repair_e621_tags)
        self.legacy_sidecars_btn = QPushButton("Импортировать старые sidecar в SQLite")
        self.legacy_sidecars_btn.clicked.connect(self.import_legacy_sidecars)
        _mrow5.addWidget(self.repair_e621_btn, 1); _mrow5.addWidget(self.legacy_sidecars_btn, 1)
        _maintenance.addLayout(_mrow5)
        _mrow6 = QHBoxLayout()
        self.recheck_general_categories_btn = QPushButton("Переразложить ВСЕ general-only теги")
        self.recheck_general_categories_btn.setToolTip("Найдёт все уже сохранённые source-наборы Danbooru/Gelbooru/rule34/e621/ATF, где все теги лежат в general, и заново запросит категории у каждого сайта. Лимита 10000 больше нет. Новые теги не добавляются, только исправляются категории существующих тегов.")
        self.recheck_general_categories_btn.clicked.connect(self.recheck_general_only_categories)
        _mrow6.addWidget(self.recheck_general_categories_btn, 1)
        _mrow6.addStretch(1)
        _maintenance.addLayout(_mrow6)
        self.sql_status=QLabel("")
        self.sql_status.setWordWrap(True)
        self.sql_status.setVisible(False)
        _maintenance.addWidget(self.sql_status)
        _ilay.addWidget(self.maintenance_box)

        # Settings are split into compact topic cards.  Do not return to the
        # old "every action and every checkbox in one box" layout: at 1080p it
        # was unreadable and made unrelated controls look like one workflow.
        self.library_policy_box = QGroupBox("Новые файлы и корзина")
        _library_policy = QVBoxLayout(self.library_policy_box)
        _library_policy.setContentsMargins(10, 10, 10, 10); _library_policy.setSpacing(8)
        _wrow1 = QHBoxLayout()
        self.imports_to_inbox = QCheckBox("После скачивания добавлять в «Новые»")
        self.inbox_hours = QSpinBox(); self.inbox_hours.setRange(1, 24 * 365); self.inbox_hours.setSuffix(" ч до архива")
        self.inbox_hours.setMaximumWidth(170)
        _wrow1.addWidget(self.imports_to_inbox); _wrow1.addWidget(self.inbox_hours); _wrow1.addStretch(1)
        _library_policy.addLayout(_wrow1)
        _wrow_policy = QHBoxLayout()
        self.deleted_reimport = QComboBox()
        self.deleted_reimport.addItem("Не скачивать обратно", "skip")
        self.deleted_reimport.addItem("Вернуть в «Новые»", "return_inbox")
        self.trash_days = QComboBox()
        self.trash_days.addItem("Очищать корзину только вручную", 0)
        self.trash_days.addItem("Очищать корзину через 7 дней", 7)
        self.trash_days.addItem("Очищать корзину через 30 дней", 30)
        _wrow_policy.addWidget(QLabel("Если снова найден удалённый файл:")); _wrow_policy.addWidget(self.deleted_reimport)
        _wrow_policy.addWidget(self.trash_days); _wrow_policy.addStretch(1)
        _library_policy.addLayout(_wrow_policy)
        _ilay.addWidget(self.library_policy_box)

        self.library_transfer_box = QGroupBox("Перенос и экспорт данных")
        _library_transfer = QVBoxLayout(self.library_transfer_box)
        _library_transfer.setContentsMargins(10, 10, 10, 10); _library_transfer.setSpacing(8)
        _wrow3 = QHBoxLayout()
        self.archive_stats_btn = QPushButton("Статистика архива")
        self.archive_stats_btn.clicked.connect(self.show_archive_stats)
        self.metadata_export_btn = QPushButton("Экспорт тегов и источников")
        self.metadata_export_btn.clicked.connect(self.export_metadata_dialog)
        _wrow3.addWidget(self.archive_stats_btn); _wrow3.addWidget(self.metadata_export_btn); _wrow3.addStretch(1)
        _library_transfer.addLayout(_wrow3)
        _wrow_profile = QHBoxLayout()
        self.settings_export_btn = QPushButton("Экспорт профиля настроек")
        self.settings_import_btn = QPushButton("Импорт профиля настроек")
        self.settings_include_secrets = QCheckBox("Включить логины / API-ключи в экспорт")
        self.settings_export_btn.clicked.connect(self.export_settings_profile)
        self.settings_import_btn.clicked.connect(self.import_settings_profile)
        _wrow_profile.addWidget(self.settings_export_btn); _wrow_profile.addWidget(self.settings_import_btn); _wrow_profile.addWidget(self.settings_include_secrets); _wrow_profile.addStretch(1)
        _library_transfer.addLayout(_wrow_profile)

        _light_backup_hint = QLabel("Лёгкая резервная копия сохраняет SQLite, настройки и ручные метаданные. Медиа и кэш не копируются.")
        _light_backup_hint.setWordWrap(True)
        _library_transfer.addWidget(_light_backup_hint)
        _backup_row1 = QHBoxLayout()
        self.light_backup_enabled = QCheckBox("Включить лёгкие резервные копии")
        self.light_backup_on_exit = QCheckBox("При закрытии")
        self.light_backup_interval = QSpinBox(); self.light_backup_interval.setRange(1, 168); self.light_backup_interval.setSuffix(" ч")
        self.light_backup_keep = QSpinBox(); self.light_backup_keep.setRange(1, 100); self.light_backup_keep.setSuffix(" копий")
        _backup_row1.addWidget(self.light_backup_enabled); _backup_row1.addWidget(self.light_backup_on_exit)
        _backup_row1.addWidget(QLabel("Интервал:")); _backup_row1.addWidget(self.light_backup_interval)
        _backup_row1.addWidget(QLabel("Хранить:")); _backup_row1.addWidget(self.light_backup_keep); _backup_row1.addStretch(1)
        _library_transfer.addLayout(_backup_row1)
        _backup_row2 = QHBoxLayout()
        self.light_backup_dir = QLineEdit(); self.light_backup_dir.setPlaceholderText("Папка на внешнем SSD / другом диске")
        self.light_backup_choose_btn = QPushButton("Выбрать папку")
        self.light_backup_now_btn = QPushButton("Создать копию сейчас")
        self.light_backup_include_cookies = QCheckBox("Включать cookies")
        self.light_backup_choose_btn.clicked.connect(self.choose_light_backup_dir)
        self.light_backup_now_btn.clicked.connect(self.create_light_backup_now)
        _backup_row2.addWidget(self.light_backup_dir, 1); _backup_row2.addWidget(self.light_backup_choose_btn); _backup_row2.addWidget(self.light_backup_now_btn); _backup_row2.addWidget(self.light_backup_include_cookies)
        _library_transfer.addLayout(_backup_row2)
        _ilay.addWidget(self.library_transfer_box)

        self.preview_cache_box = QGroupBox("Превью и кэш")
        _preview_cache = QVBoxLayout(self.preview_cache_box)
        _preview_cache.setContentsMargins(10, 10, 10, 10); _preview_cache.setSpacing(8)
        _wrow2 = QHBoxLayout()
        self.thumb_cleanup_exit = QCheckBox("Очищать лишний кэш превью при закрытии")
        self.thumb_keep_recent = QSpinBox(); self.thumb_keep_recent.setRange(50, 10000); self.thumb_keep_recent.setSingleStep(50); self.thumb_keep_recent.setSuffix(" последних превью")
        self.thumb_keep_recent.setMaximumWidth(190)
        _wrow2.addWidget(self.thumb_cleanup_exit); _wrow2.addWidget(self.thumb_keep_recent); _wrow2.addStretch(1)
        _preview_cache.addLayout(_wrow2)
        _wrow_thumb_perf = QHBoxLayout()
        self.thumb_quality = QComboBox()
        self.thumb_quality.addItem("Низкое качество", 1)
        self.thumb_quality.addItem("Среднее качество", 2)
        self.thumb_quality.addItem("Высокое качество", 3)
        self.thumb_memory_items = QSpinBox(); self.thumb_memory_items.setRange(50, 2000); self.thumb_memory_items.setSingleStep(50); self.thumb_memory_items.setSuffix(" превью в памяти")
        self.thumb_threads = QSpinBox(); self.thumb_threads.setRange(1, 6); self.thumb_threads.setSuffix(" потока")
        self.thumb_prefetch = QCheckBox("Предзагружать соседние страницы")
        _wrow_thumb_perf.addWidget(self.thumb_quality); _wrow_thumb_perf.addWidget(self.thumb_memory_items); _wrow_thumb_perf.addWidget(self.thumb_threads); _wrow_thumb_perf.addWidget(self.thumb_prefetch); _wrow_thumb_perf.addStretch(1)
        _preview_cache.addLayout(_wrow_thumb_perf)
        _cache_actions = QHBoxLayout()
        self.cache_info_btn = QPushButton("Размер кэша / очистка")
        self.cache_info_btn.clicked.connect(self.show_cache_tools)
        _cache_actions.addWidget(self.cache_info_btn); _cache_actions.addStretch(1)
        _preview_cache.addLayout(_cache_actions)
        _ilay.addWidget(self.preview_cache_box)

        self.gallery_display_box = QGroupBox("Отображение и управление")
        _gallery_display = QVBoxLayout(self.gallery_display_box)
        _gallery_display.setContentsMargins(10, 10, 10, 10); _gallery_display.setSpacing(8)
        _wrow5 = QHBoxLayout()
        self.tag_categories_btn = QPushButton("Цвета / порядок тегов")
        self.tag_categories_btn.clicked.connect(self.configure_tag_categories)
        self.hotkeys_btn = QPushButton("Горячие клавиши")
        self.hotkeys_btn.clicked.connect(self.configure_hotkeys)
        self.interface_modules_btn = QPushButton("Разделы интерфейса")
        self.interface_modules_btn.clicked.connect(self.configure_interface_modules)
        _wrow5.addWidget(self.tag_categories_btn); _wrow5.addWidget(self.hotkeys_btn); _wrow5.addWidget(self.interface_modules_btn); _wrow5.addStretch(1)
        _gallery_display.addLayout(_wrow5)
        _wrow6 = QHBoxLayout()
        self.hide_single_char_tags = QCheckBox("Скрывать теги из одного символа")
        self.hide_technical_tags = QCheckBox("Скрывать технический мусор")
        self.hide_meta_tags = QCheckBox("Скрывать meta-теги")
        self.hide_rating_tags = QCheckBox("Скрывать rating-теги")
        _wrow6.addWidget(self.hide_single_char_tags); _wrow6.addWidget(self.hide_technical_tags)
        _wrow6.addWidget(self.hide_meta_tags); _wrow6.addWidget(self.hide_rating_tags); _wrow6.addStretch(1)
        _gallery_display.addLayout(_wrow6)
        _ilay.addWidget(self.gallery_display_box)

        self.grabber_subs_box = QGroupBox("Граббер и подписки")
        _grabber_subs = QVBoxLayout(self.grabber_subs_box)
        _grabber_subs.setContentsMargins(10, 10, 10, 10); _grabber_subs.setSpacing(8)
        self.grabber_hide_existing = QCheckBox("Скрывать уже скачанные в граббере")
        self.grabber_prefetch_originals = QCheckBox("Граббер: заранее скачивать оригиналы в временный кэш")
        self.grabber_prefetch_originals.setToolTip("Если включено, карточки граббера качают не только миниатюру, но и оригинал в временный кэш. При сохранении файл берётся из кэша и быстро переносится/копируется в архив с тегами и источниками. Может резко увеличить расход места и трафика.")
        self.grabber_include_protected_sites = QCheckBox("Граббер: включать защищённые сайты с PoW/проверками")
        self.grabber_include_protected_sites.setToolTip("Совместимость со старыми настройками. ATF API-карточки теперь включены по умолчанию через /posts.json; тяжёлое фоновое скачивание оригиналов с защищённых сайтов всё ещё отключается отдельной настройкой.")
        self.grabber_stream_cards = QCheckBox("Граббер: показывать карточки по одной сразу во время запроса")
        self.grabber_stream_cards.setToolTip("Экспериментально. По умолчанию выключено: интерфейс получает один готовый пакет на запрос, чтобы не пересобирать Qt-сетку сотни раз.")
        _cache_row = QHBoxLayout()
        _cache_row.addWidget(QLabel("Лимит кэша превью граббера (МБ):"))
        self.grabber_cache_limit = QSpinBox()
        self.grabber_cache_limit.setRange(0, 10000)
        self.grabber_cache_limit.setSuffix(" МБ")
        self.grabber_cache_limit.setToolTip("Кэш граббера временный: очищается при запуске/закрытии. Лимит ограничивает размер текущей сессии; 0 = без лимита.")
        _cache_row.addWidget(self.grabber_cache_limit)
        _cache_row.addStretch(1)
        _quality_row = QHBoxLayout()
        _quality_row.addWidget(QLabel("Открытие карточки в граббере:"))
        self.grabber_open_quality = QComboBox()
        self.grabber_open_quality.addItem("Маленький файл / ~25%", "small_25")
        self.grabber_open_quality.addItem("Средний файл / ~50%", "medium_50")
        self.grabber_open_quality.addItem("Оригинал / 100%", "original_100")
        self.grabber_open_quality.setToolTip("Влияет только на просмотр по клику в граббере. Кнопка «Скачать» всегда сохраняет оригинал 100% в архив.")
        _quality_row.addWidget(self.grabber_open_quality)
        _quality_row.addStretch(1)
        _ram_row = QHBoxLayout()
        _ram_row.addWidget(QLabel("RAM-кэш граббера:"))
        self.grabber_metadata_ram_cache = QSpinBox()
        self.grabber_metadata_ram_cache.setRange(0, 4096)
        self.grabber_metadata_ram_cache.setSuffix(" МБ метаданные")
        self.grabber_metadata_ram_cache.setToolTip("Временный ОЗУ-кэш для тегов/MD5/source/post JSON граббера. Парсерный кэш хранится отдельно на диске.")
        self.grabber_image_ram_cache = QSpinBox()
        self.grabber_image_ram_cache.setRange(0, 8192)
        self.grabber_image_ram_cache.setSuffix(" МБ картинки")
        self.grabber_image_ram_cache.setToolTip("Временный ОЗУ-кэш уже декодированных миниатюр граббера для быстрых перерисовок. 0 = выключить.")
        _ram_row.addWidget(self.grabber_metadata_ram_cache)
        _ram_row.addWidget(self.grabber_image_ram_cache)
        _ram_row.addStretch(1)
        self.grabber_blocklist = QTextEdit()
        self.grabber_blocklist.setPlaceholderText("Блоклист тегов для граббера и подписок. Через пробел, запятую или новую строку. Не влияет на локальную галерею и уже сохранённые файлы.")
        self.grabber_blocklist.setFixedHeight(120)
        _grabber_hint = QLabel("Этот блоклист используется только при онлайн-просмотре, подписках и скачивании из граббера. Локальная галерея и поиск по архиву не фильтруются.")
        _grabber_hint.setWordWrap(True)
        _grabber_subs.addWidget(self.grabber_hide_existing)
        _grabber_subs.addWidget(self.grabber_prefetch_originals)
        _grabber_subs.addWidget(self.grabber_include_protected_sites)
        _grabber_subs.addWidget(self.grabber_stream_cards)
        _grabber_subs.addLayout(_cache_row)
        _grabber_subs.addLayout(_quality_row)
        _grabber_subs.addLayout(_ram_row)
        _grabber_subs.addWidget(QLabel("Блоклист тегов:"))
        _grabber_subs.addWidget(self.grabber_blocklist)
        _grabber_subs.addWidget(_grabber_hint)
        _ilay.addWidget(self.grabber_subs_box)

        self.developer_tools_box = QGroupBox("Логи и служебные инструменты")
        _developer_tools = QVBoxLayout(self.developer_tools_box)
        _developer_tools.setContentsMargins(10, 10, 10, 10); _developer_tools.setSpacing(8)
        _wrow_perf = QHBoxLayout()
        self.performance_slow_ms = QSpinBox(); self.performance_slow_ms.setRange(25, 10000); self.performance_slow_ms.setSingleStep(25); self.performance_slow_ms.setSuffix(" мс")
        _wrow_perf.addWidget(QLabel("Записывать медленные операции дольше:")); _wrow_perf.addWidget(self.performance_slow_ms); _wrow_perf.addStretch(1)
        _developer_tools.addLayout(_wrow_perf)
        self.developer_preload_md5_index = QCheckBox("Держать MD5-индекс в ОЗУ для граббера")
        self.developer_preload_md5_index.setToolTip("Ускоряет проверку дублей при скачивании из граббера. Загружает из SQLite только MD5→путь, а не весь кэш/метаданные. Если ОЗУ мало — можно выключить.")
        _developer_tools.addWidget(self.developer_preload_md5_index)
        self.developer_grabber_md5_cache_enabled = QCheckBox("Local Reverse Index: сохранять посты граббера в отдельную SQLite")
        self.developer_grabber_md5_cache_enabled.setToolTip("Опциональный локальный индекс в settings/db/grabber_local_reverse_index.sqlite: граббер сохраняет source, теги и категории. Превью сюда не пишутся: они остаются только в ограниченном кэше UI. Эта SQLite нужна для офлайн-повторного использования парсером по exact MD5. pHash/визуальные совпадения автоматически не записываются как source-proof.")
        _developer_tools.addWidget(self.developer_grabber_md5_cache_enabled)
        self.grabber_exact_md5_fanout = QCheckBox("Перед скачиванием искать exact MD5 на всех включённых сайтах")
        self.grabber_exact_md5_fanout.setToolTip("Если файл найден на нескольких booru с тем же MD5, скачивается один оригинал, а источники и теги собираются со всех exact-MD5 постов.")
        _developer_tools.addWidget(self.grabber_exact_md5_fanout)
        self.grabber_visual_hash_merge = QCheckBox("Схлопывать визуально одинаковые карточки граббера")
        self.grabber_visual_hash_merge.setToolTip("Второй уровень после MD5: если разные сайты показывают визуально одинаковую картинку, граббер оставляет одну карточку. При разных MD5 это только UI-скрытие дубля; источники/теги в базу не склеиваются автоматически.")
        _developer_tools.addWidget(self.grabber_visual_hash_merge)
        _wrow_visual = QHBoxLayout()
        self.grabber_visual_hash_distance = QSpinBox(); self.grabber_visual_hash_distance.setRange(0, 16); self.grabber_visual_hash_distance.setSingleStep(1)
        self.grabber_visual_hash_distance.setToolTip("Максимальная pHash-дистанция для визуального схлопывания в граббере. 0 = только полностью одинаковый pHash; 4 = строгий режим для ATF/e621/rule34 зеркал.")
        _wrow_visual.addWidget(QLabel("Порог visual merge pHash:")); _wrow_visual.addWidget(self.grabber_visual_hash_distance); _wrow_visual.addStretch(1)
        _developer_tools.addLayout(_wrow_visual)
        self.developer_filesystem_duplicate_fallback = QCheckBox("Legacy-проверка дублей через обход файлов")
        self.developer_filesystem_duplicate_fallback.setToolTip("Медленный запасной режим: после скачивания обходит файлы архива и сравнивает точные дубли. На больших архивах лучше держать выключенным и полагаться на SQLite/MD5-индекс.")
        _developer_tools.addWidget(self.developer_filesystem_duplicate_fallback)
        _wrow4 = QHBoxLayout()
        self.logs_btn = QPushButton("Открыть папку логов")
        self.logs_btn.clicked.connect(self.open_logs_folder)
        self.diagnostics_btn = QPushButton("Собрать отчёт об ошибке")
        self.diagnostics_btn.clicked.connect(self.create_diagnostic_report)
        _wrow4.addWidget(self.logs_btn); _wrow4.addWidget(self.diagnostics_btn); _wrow4.addStretch(1)
        _developer_tools.addLayout(_wrow4)
        _ilay.addWidget(self.developer_tools_box)

        self.threading_box = QGroupBox("Потоки и локальные очереди")
        _threading = QVBoxLayout(self.threading_box)
        _threading.setContentsMargins(10, 10, 10, 10); _threading.setSpacing(8)
        _thread_hint = QLabel(
            "Эти настройки относятся только к локальной работе без интернета: сканирование, MD5/SHA1, pHash, превью, видео-кадры, локальные индексы и фоновые задачи. "
            "Сетевые сайты всё равно живут в отдельных rate-limited очередях, чтобы не словить бан/429."
        )
        _thread_hint.setWordWrap(True)
        _threading.addWidget(_thread_hint)

        def _thread_spin(maximum=64, suffix=" потоков"):
            sp = QSpinBox(); sp.setRange(1, int(maximum)); sp.setSuffix(suffix); sp.setMaximumWidth(190); return sp

        self.local_total_workers = _thread_spin(64)
        self.local_scan_workers = _thread_spin(16)
        self.local_hash_workers = _thread_spin(32)
        self.local_image_workers = _thread_spin(32)
        self.local_video_workers = _thread_spin(8)
        self.local_db_read_workers = _thread_spin(16)
        self.local_tagger_workers = _thread_spin(16)
        self.local_thumb_workers = _thread_spin(16)
        self.local_thumb_pregen_workers = _thread_spin(16)
        self.local_background_workers = _thread_spin(16)
        self.local_preflight_enabled = QCheckBox("Греть локальный hash/pHash-кэш параллельно с парсером")
        self.local_preflight_phash = QCheckBox("В preflight считать pHash/видео-кадр, а не только MD5")
        self.visual_nomatch_classify_enabled = QCheckBox("Во время парсинга присваивать NO_MATCH статус real/booru")
        self.visual_nomatch_classify_enabled.setToolTip("Строго локальная сортировка без API: real = фото/камера, booru = рисунок/3D/рендер. Основной режим — встроенная локальная CLIP-модель из папки программы.")
        self.visual_nomatch_backend = QComboBox()
        self.visual_nomatch_backend.addItem("Локальный AI CLIP (без интернета)", "clip_local")
        self.visual_nomatch_backend.addItem("Старая эвристика v1", "heuristic")
        self.visual_nomatch_backend.setToolTip("CLIP ничего не отправляет в интернет. В нормальной сборке модель идёт вместе с программой в models/clip. Поле папки ниже — только ручное переопределение для отладки.")
        self.visual_nomatch_clip_model_dir = QLineEdit(); self.visual_nomatch_clip_model_dir.setPlaceholderText("Пусто = встроенная модель программы: models/clip")
        self.visual_nomatch_clip_model_btn = QPushButton("Переопределить")
        self.visual_nomatch_clip_model_btn.clicked.connect(self.choose_visual_clip_model_dir)
        _clip_row = QHBoxLayout(); _clip_row.addWidget(self.visual_nomatch_clip_model_dir, 1); _clip_row.addWidget(self.visual_nomatch_clip_model_btn)
        self.visual_nomatch_device = QComboBox()
        self.visual_nomatch_device.addItem("Авто GPU/CPU", "auto")
        self.visual_nomatch_device.addItem("Только CPU", "cpu")
        self.visual_nomatch_device.addItem("CUDA если есть", "cuda")
        self.visual_nomatch_ai_min_confidence = QDoubleSpinBox(); self.visual_nomatch_ai_min_confidence.setRange(0.50, 0.95); self.visual_nomatch_ai_min_confidence.setSingleStep(0.01); self.visual_nomatch_ai_min_confidence.setDecimals(2); self.visual_nomatch_ai_min_confidence.setMaximumWidth(190)
        self.visual_nomatch_ai_min_margin = QDoubleSpinBox(); self.visual_nomatch_ai_min_margin.setRange(0.00, 0.50); self.visual_nomatch_ai_min_margin.setSingleStep(0.01); self.visual_nomatch_ai_min_margin.setDecimals(2); self.visual_nomatch_ai_min_margin.setMaximumWidth(190)
        self.visual_nomatch_ai_fallback_heuristic = QCheckBox("Если AI-модель не найдена, использовать старую эвристику")
        self.visual_nomatch_ai_fallback_heuristic.setToolTip("По умолчанию выключено, чтобы не засорять real/booru плохой эвристикой. Если выключено и модели нет — файл остаётся [вид ?] только во Все.")
        self.visual_nomatch_auto_download_model = QCheckBox("Если CLIP-модели нет, скачать её автоматически при запуске")
        self.visual_nomatch_auto_download_model.setToolTip("Скачивает openai/clip-vit-base-patch32 один раз в settings/models/clip. Это только загрузка модели; ваши картинки не отправляются.")
        self.visual_nomatch_workers = _thread_spin(8)
        self.visual_nomatch_real_threshold = QDoubleSpinBox(); self.visual_nomatch_real_threshold.setRange(0.10, 0.90); self.visual_nomatch_real_threshold.setSingleStep(0.01); self.visual_nomatch_real_threshold.setDecimals(2); self.visual_nomatch_real_threshold.setMaximumWidth(190)

        _thread_form = QFormLayout()
        _thread_form.setContentsMargins(0, 0, 0, 0); _thread_form.setSpacing(6)
        _thread_form.addRow("Общий максимум локальных потоков:", self.local_total_workers)
        _thread_form.addRow("Сканирование файлов:", self.local_scan_workers)
        _thread_form.addRow("MD5/SHA1/размеры:", self.local_hash_workers)
        _thread_form.addRow("pHash/изображения/превью:", self.local_image_workers)
        _thread_form.addRow("Видео/GIF кадры:", self.local_video_workers)
        _thread_form.addRow("SQLite чтение/локальные индексы:", self.local_db_read_workers)
        _thread_form.addRow("Legacy-парсер: файлов одновременно:", self.local_tagger_workers)
        _thread_form.addRow("Превью UI:", self.local_thumb_workers)
        _thread_form.addRow("Предсоздание превью:", self.local_thumb_pregen_workers)
        _thread_form.addRow("Фоновые задачи:", self.local_background_workers)
        _thread_form.addRow("NO_MATCH real/booru:", self.visual_nomatch_workers)
        _thread_form.addRow("Классификатор NO_MATCH:", self.visual_nomatch_backend)
        _thread_form.addRow("Папка CLIP-модели (необязательно):", _clip_row)
        _thread_form.addRow("Устройство AI:", self.visual_nomatch_device)
        _thread_form.addRow("AI min confidence:", self.visual_nomatch_ai_min_confidence)
        _thread_form.addRow("AI min margin:", self.visual_nomatch_ai_min_margin)
        _thread_form.addRow("Порог старой эвристики:", self.visual_nomatch_real_threshold)
        _threading.addLayout(_thread_form)
        _threading.addWidget(self.local_preflight_enabled)
        _threading.addWidget(self.local_preflight_phash)
        _threading.addWidget(self.visual_nomatch_classify_enabled)
        _threading.addWidget(self.visual_nomatch_auto_download_model)
        _threading.addWidget(self.visual_nomatch_ai_fallback_heuristic)
        _thread_note = QLabel(
            "Важно: общий максимум — это потолок для одной локальной службы, а не сумма всех потоков процесса. "
            "SQLite-запись всё равно остаётся сериализованной, чтобы не ломать БД."
        )
        _thread_note.setWordWrap(True)
        _threading.addWidget(_thread_note)
        _ilay.addWidget(self.threading_box)

        # This is a global action displayed in the fixed footer, not a setting.
        self.changelog_btn = QPushButton("Что нового")
        self.changelog_btn.clicked.connect(self.show_changelog)

        self.danger_box = QGroupBox("Опасные действия")
        _danger_lay = QVBoxLayout(self.danger_box)
        _danger_lay.setContentsMargins(8, 8, 8, 8)
        _danger_lay.setSpacing(6)
        self.danger=QLabel("Удаление данных")
        self.danger.setStyleSheet("font-size:16px;font-weight:900;color:#ff3838;margin-top:12px")
        _danger_lay.addWidget(self.danger)

        self.tag_cleanup_info = QLabel(
            "Удаление по тегу или источнику. Сначала выбери вариант и нажми «Показать связанные файлы». "
            "Показанные результаты перемещаются в «Удалено» и могут быть восстановлены. Исходная папка не затрагивается."
        )
        self.tag_cleanup_info.setWordWrap(True)
        _danger_lay.addWidget(self.tag_cleanup_info)

        self.tag_cleanup_scope = QComboBox()
        self.tag_cleanup_scope.addItem("Все результаты", "all")
        self.tag_cleanup_scope.addItem("Только результаты парсера", "tagger")
        self.tag_cleanup_scope.addItem("Только результаты загрузчика", "downloader")
        self.tag_cleanup_scope.addItem("Только найденные", "found")
        self.tag_cleanup_scope.addItem("Только не найденные", "no_match")
        self.tag_cleanup_kind = QComboBox()
        self.tag_cleanup_kind.addItem("Тег", "tag")
        self.tag_cleanup_kind.addItem("Source", "source")
        self.tag_cleanup_kind.currentIndexChanged.connect(self.refresh_cleanup_candidates)
        self.tag_cleanup_scope.currentIndexChanged.connect(self.refresh_cleanup_candidates)

        self.tag_cleanup_query = QLineEdit()
        self.tag_cleanup_query.setPlaceholderText("начни писать и выбери тег/source из списка")
        self.cleanup_candidate_model = QStringListModel(self)
        self.cleanup_candidate_proxy = ContainsFilterProxyModel(self)
        self.cleanup_candidate_proxy.setSourceModel(self.cleanup_candidate_model)
        self.cleanup_completer = QCompleter(self.cleanup_candidate_proxy, self)
        self.cleanup_completer.setCaseSensitivity(Qt.CaseInsensitive)
        try:
            self.cleanup_completer.setFilterMode(Qt.MatchContains)
        except Exception:
            pass
        self.cleanup_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.tag_cleanup_query.setCompleter(self.cleanup_completer)
        self.tag_cleanup_query.textEdited.connect(self.update_cleanup_completion)

        self.tag_cleanup_find_btn = QPushButton("Показать связанные файлы")
        self.tag_cleanup_find_btn.clicked.connect(self.find_tag_cleanup_matches)
        _target_row = QHBoxLayout()
        _target_row.addWidget(self.tag_cleanup_scope, 2)
        _target_row.addWidget(self.tag_cleanup_kind, 1)
        _target_row.addWidget(self.tag_cleanup_query, 4)
        _target_row.addWidget(self.tag_cleanup_find_btn, 2)
        _danger_lay.addLayout(_target_row)

        self.tag_cleanup_list = QListWidget()
        self.tag_cleanup_list.setMaximumHeight(120)
        self.tag_cleanup_list.setVisible(False)
        _danger_lay.addWidget(self.tag_cleanup_list)

        self.tag_cleanup_delete_btn = QPushButton("Переместить показанные файлы в «Удалено»")
        self.tag_cleanup_delete_btn.setEnabled(False)
        self.tag_cleanup_delete_btn.setStyleSheet("QPushButton{background:#7f1d1d;border:1px solid #ff3838;color:white;font-weight:900}QPushButton:disabled{background:#2a2020;color:#777}")
        self.tag_cleanup_delete_btn.clicked.connect(self.delete_tag_cleanup_matches)
        self.tag_cleanup_delete_btn.setVisible(False)
        _danger_lay.addWidget(self.tag_cleanup_delete_btn)

        self.output_cleanup_info = QLabel(
            "Или очистить раздел целиком. Удаляются результаты и их записи в базе, исходная папка не затрагивается."
        )
        self.output_cleanup_info.setWordWrap(True)
        _danger_lay.addWidget(self.output_cleanup_info)

        _parser_delete_row = QHBoxLayout()
        self.delete_mode = QComboBox()
        self.delete_mode.addItem("Все результаты парсера", "all")
        self.delete_mode.addItem("Только найденные/частичные", "found")
        self.delete_mode.addItem("Только не найденные", "no_match")
        self.delete_results_btn = QPushButton("Удалить результаты парсера")
        self.delete_results_btn.clicked.connect(self.delete_tags)
        _parser_delete_row.addWidget(self.delete_mode, 1)
        _parser_delete_row.addWidget(self.delete_results_btn)
        _danger_lay.addLayout(_parser_delete_row)

        _downloader_delete_row = QHBoxLayout()
        self.downloader_delete_mode = QComboBox()
        self.downloader_delete_mode.addItem("Все результаты загрузчика", "all")
        self.downloader_delete_mode.addItem("Только найденные", "found")
        self.downloader_delete_mode.addItem("Только частичные", "partial_match")
        self.downloader_delete_mode.addItem("Только не найденные", "no_match")
        self.delete_downloader_btn = QPushButton("Удалить результаты загрузчика")
        self.delete_downloader_btn.clicked.connect(self.delete_downloader_results)
        _downloader_delete_row.addWidget(self.downloader_delete_mode, 1)
        _downloader_delete_row.addWidget(self.delete_downloader_btn)
        _danger_lay.addLayout(_downloader_delete_row)

        self.video_note=QLabel()  # Текст перенесён в инструкцию, здесь больше не занимает место.

        # ── NUKE: delete everything ───────────────────────────────────────────
        _cleanup_btn = QPushButton("Удалить записи без файлов")
        _cleanup_btn.setToolTip("Убирает из БД записи на файлы которые уже удалены с диска")
        def _do_cleanup():
            try:
                from core.services.library_service import cleanup_missing_records
                n = cleanup_missing_records(self.main.settings)
                QMessageBox.information(self, "Готово", f"Удалено {n} записей без файлов.")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", str(e))
        _cleanup_btn.clicked.connect(_do_cleanup)
        _danger_lay.addWidget(_cleanup_btn)

        _nuke_label = QLabel("")
        _nuke_label.setVisible(False)

        _nuke_info = QLabel(
            "Полный сброс тестовой библиотеки: удаляет рабочую галерею, теги, источники, кэш и базу данных. "
            "Исходный архив защищён и не изменяется. Для подтверждения введи слово DELETE."
        )
        _nuke_info.setWordWrap(True)
        _danger_lay.addWidget(_nuke_info)

        _nuke_row = QHBoxLayout()
        self.nuke_confirm_input = QLineEdit()
        self.nuke_confirm_input.setPlaceholderText("введи DELETE для разблокировки")
        self.nuke_confirm_input.setMaximumWidth(260)

        self.nuke_subs_check = QCheckBox("включая подписки")
        self.nuke_subs_check.setChecked(True)
        self.nuke_subs_check.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.nuke_btn = QPushButton("Удалить всё")
        self.nuke_btn.setEnabled(False)
        self.nuke_btn.setStyleSheet(
            "QPushButton{background:#7f0000;border:2px solid #ff3838;color:white;"
            "font-weight:900;font-size:13px;padding:4px 12px}"
            "QPushButton:enabled{background:#b91c1c}"
            "QPushButton:disabled{background:#2a1010;color:#555}"
        )

        def _nuke_check(txt):
            self.nuke_btn.setEnabled(txt.strip() == "DELETE")
        self.nuke_confirm_input.textChanged.connect(_nuke_check)
        self.nuke_btn.clicked.connect(self._nuke_everything)

        _nuke_row.addWidget(self.nuke_confirm_input)
        _nuke_row.addWidget(self.nuke_subs_check)
        _nuke_row.addWidget(self.nuke_btn)
        _nuke_row.addStretch()
        _danger_lay.addLayout(_nuke_row)
        _ilay.addWidget(self.danger_box)
        # ─────────────────────────────────────────────────────────────────────

        _ilay.addStretch(1)
        # Use Qt-painted popup views, not platform-native combo popups.
        # Native popups can keep old theme/shadow colors and create black bars
        # on R34/Win95 after restart or theme switching.
        try:
            for _cb in self.findChildren(QComboBox):
                _cb.setView(QListView())
                _cb.setMaxVisibleItems(12)
        except Exception:
            pass
        self._scroll.setWidget(self._inner)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        # Settings subsections live in a horizontal top tab strip, matching the
        # diagnostics page; do not create a second sidebar inside Settings.
        self._sections = [
            ("Основные", "Внешний вид, язык и подпись приложения."),
            ("Библиотека", "Исходный архив только читается; рабочую галерею можно пересобирать."),
            ("Галерея и превью", "Сетка, кэш, фильтры тегов и экспорт метаданных."),
            ("Граббер / Подписки", "Онлайн-галерея сайтов, подписки и общий блоклист скачивания."),
            ("Обслуживание", "Проверки, индексы и безопасный ремонт рабочей библиотеки."),
            ("Удаление и сброс", "Опасные действия только над рабочей библиотекой."),
            ("Для разработчика", "Логи, консоль, совместимость и внутренние параметры."),
        ]
        self.section_tabs = QTabBar()
        self.section_tabs.setObjectName("SettingsSectionTabs")
        self.section_tabs.setExpanding(False)
        self.section_tabs.setDrawBase(True)
        self.section_tabs.setElideMode(Qt.ElideRight)
        for title, _desc in self._sections:
            self.section_tabs.addTab(title)
        self.section_tabs.currentChanged.connect(self._show_settings_section)
        # The selected tab already provides the heading; keep only its useful hint below.
        self.section_title.setVisible(False)
        lay.addWidget(self.section_tabs)
        lay.addWidget(self._scroll, 1)

        # Fixed footer: global page actions never travel with the currently
        # selected settings subsection or with the scroll position.
        self.settings_footer = QWidget()
        self.settings_footer.setObjectName("SettingsFooter")
        _footer = QHBoxLayout(self.settings_footer)
        _footer.setContentsMargins(10, 8, 10, 8)
        _footer.setSpacing(8)
        _footer.addWidget(self.instruction_btn)
        _footer.addWidget(self.changelog_btn)
        _footer.addStretch(1)
        self.save_btn.setMinimumWidth(190)
        self.save_btn.setObjectName("PrimarySettingsAction")
        _footer.addWidget(self.save_btn)
        lay.addWidget(self.settings_footer)
        self.load_values(); self.retranslate(); self.apply_tips(); self.refresh_cleanup_candidates()
        self.section_tabs.setCurrentIndex(0)
        self._show_settings_section(0)
        self._install_wheel_guards()

    def _show_settings_section(self, row):
        """Show one readable settings subsection; keep technical controls out of the ordinary path."""
        if row < 0 or row >= len(getattr(self, "_sections", [])):
            row = 0
        title, description = self._sections[row]
        self.section_title.setText(title)
        self.section_description.setText(description)
        form_groups = {
            "Основные": {"Language", "Appearance", "Title", "Logo path", "Logo fit"},
            "Библиотека": {"Images folder", "Output folder", "Manga folder", "Папка игр"},
            "Галерея и превью": {"Columns", "Rows/page", "Card height", "Ignore numeric tags", "Search preview"},
            "Для разработчика": {"Debug logging", "Console line limit", "Error console"},
        }
        visible_keys = form_groups.get(title, set())
        for index, (_label, key, _widget, _tip) in enumerate(getattr(self, "form_rows", [])):
            show = key in visible_keys
            try:
                self.form.setRowVisible(index, show)
            except Exception:
                _label.setVisible(show)
                item = self.form.itemAt(index, QFormLayout.FieldRole)
                if item and item.widget():
                    item.widget().setVisible(show)
        # Topic cards: each subsection shows only related values/actions.
        self.library_policy_box.setVisible(title == "Библиотека")
        self.library_transfer_box.setVisible(title == "Библиотека")
        self.preview_cache_box.setVisible(title == "Галерея и превью")
        self.gallery_display_box.setVisible(title == "Галерея и превью")
        self.grabber_subs_box.setVisible(title == "Граббер / Подписки")
        self.maintenance_box.setVisible(title == "Обслуживание")
        self.threading_box.setVisible(False)
        self.danger_box.setVisible(title == "Удаление и сброс")
        self.developer_tools_box.setVisible(title == "Для разработчика")
        is_developer = title == "Для разработчика"
        self.advanced_btn.setVisible(is_developer)
        self.advanced_box.setVisible(is_developer and self.advanced_btn.isChecked())
        # Footer actions remain visible for every subsection without scrolling.
        try:
            self._scroll.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def _install_wheel_guards(self):
        """Mouse wheel scrolls the settings page, not values under the cursor."""
        for widget_type in (QComboBox, QAbstractSpinBox, QSlider):
            for widget in self.findChildren(widget_type):
                widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and isinstance(watched, (QComboBox, QAbstractSpinBox, QSlider)):
            try:
                bar = self._scroll.verticalScrollBar()
                delta = event.angleDelta().y()
                bar.setValue(bar.value() - delta)
                event.accept()
                return True
            except Exception:
                return True
        return super().eventFilter(watched, event)

    def add_tip_row(self, label_key, widget, tip_key):
        lab=QLabel(self.main.t(label_key)+"  ?")
        lab.setToolTip(self.main.t(tip_key))
        _tc = self.main.settings.get("appearance","abyss")
        _label_colors = {"light": ("#1a1c2a","#5060d0"), "dark": ("#c0c8e0","#6c85e0"), "abyss": ("#c0c8e0","#6c85e0"),
                         "r34": ("#111111","#3a7a35"), "r34dark": ("#d6e4d3","#7fb06f"), "win95": ("#000000","#000080"), "windows95": ("#000000","#000080"),
                         "ph": ("#f5f5f5","#ff9000"), "pornhub": ("#f5f5f5","#ff9000"),
                         "ember": ("#c8b090","#c87040"), "slate": ("#b0c8d0","#5a8a9f"),
                         "sakura": ("#e0b0d0","#d060a0")}
        _lc, _hc = _label_colors.get(_tc, ("#c0c8e0","#6c85e0"))
        lab.setStyleSheet(f"QLabel{{font-weight:700;min-width:165px;color:{_lc};}} QLabel:hover{{color:{_hc};}}")
        lab.setMinimumWidth(165)
        if hasattr(widget, "setToolTip"):
            widget.setToolTip(self.main.t(tip_key))
        self.form.addRow(lab, widget)
        self.form_rows.append((lab,label_key,widget,tip_key))
    def set_tip(self, widget, key): widget.setToolTip(self.main.t(key))
    def apply_tips(self):
        self.set_tip(self.root,"tip_root"); self.set_tip(self.ignore_numeric,"tip_numeric"); self.set_tip(self.cols,"tip_root"); self.set_tip(self.rows,"tip_root")
    def retranslate(self):
        self.apply_theme_style(self.main.settings.get("appearance", "abyss"))
        t=self.main.t; self.choose.setText(t("Choose")); self.logo_choose.setText(t("Choose")); self.logo_crop.setText(t("Crop logo")); self.save_btn.setText(t("Save settings")); self.danger.setText("Удаление данных" if self.main.settings.get("language","ru") == "ru" else "Data deletion")


        self.apply_tips()
        for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
            lab.setText(t(label_key) + "  ?")
            lab.setToolTip(t(tip_key))

            if hasattr(w, "setToolTip"):
                w.setToolTip(t(tip_key))
    def apply_theme_style(self, theme_name=None):
        """Refresh only settings-page inline labels/palette after theme switch.

        Important: do NOT style QScrollArea children with broad selectors here.
        Broad local selectors such as ``QScrollArea > QWidget > QWidget`` override the
        global theme QSS for QLineEdit/QComboBox/QSpinBox and create the "masked
        lines" bug after restart. The real widget styling must stay in
        ui/styles/themes.py.
        """
        theme_name = theme_name or self.main.settings.get("appearance", "abyss")
        colors = {
            "light": ("#f4f5f8", "#1a1c2a", "#5060d0"),
            "dark": ("#0d0f16", "#c0c8e0", "#6c85e0"),
            "abyss": ("#0d0f16", "#c0c8e0", "#6c85e0"),
            "r34": ("#a8d99f", "#111111", "#3a7a35"),
            "r34dark": ("#10150f", "#d6e4d3", "#7fb06f"),
            "win95": ("#c0c0c0", "#000000", "#000080"),
            "windows95": ("#c0c0c0", "#000000", "#000080"),
            "ph": ("#0f0f0f", "#f5f5f5", "#ff9000"),
            "pornhub": ("#0f0f0f", "#f5f5f5", "#ff9000"),
            "ember": ("#120f09", "#c8b090", "#c87040"),
            "slate": ("#16181e", "#b0c8d0", "#5a8a9f"),
            "sakura": ("#10070d", "#e0b0d0", "#d060a0"),
        }
        bg, fg, hover = colors.get(theme_name, colors["abyss"])
        try:
            from PySide6.QtGui import QPalette, QColor
            for w in (self, self._inner, self._scroll, self._scroll.viewport()):
                if w is None:
                    continue
                pal = w.palette()
                pal.setColor(QPalette.ColorRole.Window, QColor(bg))
                pal.setColor(QPalette.ColorRole.Base, QColor(bg))
                pal.setColor(QPalette.ColorRole.AlternateBase, QColor(bg))
                pal.setColor(QPalette.ColorRole.Text, QColor(fg))
                pal.setColor(QPalette.ColorRole.WindowText, QColor(fg))
                pal.setColor(QPalette.ColorRole.ButtonText, QColor(fg))
                w.setPalette(pal)
                w.setAutoFillBackground(True)
            self._inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self._inner.setStyleSheet(f"QWidget#SettingsInner{{background:{bg}; color:{fg}; border:none;}}")
            self._scroll.setStyleSheet("QScrollArea{border:none; background:transparent;}")
            self._scroll.viewport().setStyleSheet("")
        except Exception:
            pass
        try:
            for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
                lab.setStyleSheet(f"QLabel{{font-weight:700;min-width:165px;color:{fg};background:transparent;}} QLabel:hover{{color:{hover};}}")
        except Exception:
            pass
        try:
            # Force combo popups to recreate/repolish the non-native list views.
            for cb in self.findChildren(QComboBox):
                cb.setMaxVisibleItems(12)
                view = cb.view()
                if view is not None:
                    view.setPalette(cb.palette())
                    view.setStyleSheet("")
                    view.update()
        except Exception:
            pass

    def _release_open_media_before_delete(self):
        try:
            if hasattr(self.main, "release_open_media_handles"):
                self.main.release_open_media_handles()
            QApplication.processEvents()
        except Exception:
            pass

    def _remove_tree_after_preview_release(self, target, attempts=6):
        """Remove generated data after Qt/Windows has released preview handles.

        A GIF shown by QMovie can keep its file handle alive for a short moment
        even after it is removed from the label.  Destructive UI actions should
        wait and retry transient Windows sharing violations rather than leaving
        a half-cleared generated library behind.
        """
        import time
        target = Path(target)
        for attempt in range(max(1, int(attempts))):
            try:
                shutil.rmtree(target)
                return
            except OSError as exc:
                transient = getattr(exc, "winerror", None) in (32, 33) or getattr(exc, "errno", None) in (13,)
                if not transient or attempt >= attempts - 1:
                    raise
                self._release_open_media_before_delete()
                QApplication.processEvents()
                time.sleep(0.08 * (attempt + 1))

    def _nuke_everything(self):
        include_subs = self.nuke_subs_check.isChecked()
        extra = " и папку подписок" if include_subs else ""
        msg = ("Это удалит ВСЮ РАБОЧУЮ галерею, теги, источники, кэш и базу данных" + extra + "."
               + chr(10) + "Исходный архив не будет изменён: из него Local Booru может только читать и копировать."
               + chr(10) + chr(10) + "Это действие НЕОБРАТИМО для рабочей выдачи. Продолжить?")
        reply = QMessageBox.question(
            self, "ВЫ ТОЧНО УВЕРЕНЫ?", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._release_open_media_before_delete()
        import shutil
        from pathlib import Path
        from core.paths import result_output_base
        from core.database.connection import db_path

        settings = self.main.settings
        from core.paths import result_output_base as _rob
        from core.source_protection import require_managed_media_mutation
        out = _rob(settings)
        deleted = []
        errors = []

        # Generated output is disposable; original archive is not. Never use a
        # raw setting path as a deletion fallback because it may be the source.
        for bucket in ("found", "partial_match", "no_match", "downloads", "trash"):
            target = out / bucket
            if target.exists():
                try:
                    if not require_managed_media_mutation(settings, target, "reset_generated_library"):
                        errors.append(str(target) + ": заблокировано защитой исходного архива")
                        continue
                    self._remove_tree_after_preview_release(target)
                    deleted.append(str(target))
                except Exception as e:
                    errors.append(str(target) + ": " + str(e))

        if include_subs:
            subs_dir = out / "subscriptions"
            if subs_dir.exists():
                try:
                    self._remove_tree_after_preview_release(subs_dir)
                    deleted.append(str(subs_dir))
                except Exception as e:
                    errors.append(str(e))
            try:
                from core.subscriptions import SUBS_FILE
                from core.subscription_engine.seed_cache import SEED_CACHE_DB, OLD_JSON_FILE
                for state_file in (SUBS_FILE, SEED_CACHE_DB, Path(str(SEED_CACHE_DB) + "-wal"), Path(str(SEED_CACHE_DB) + "-shm"), OLD_JSON_FILE):
                    if Path(state_file).exists():
                        Path(state_file).unlink()
                        deleted.append(str(state_file))
            except Exception as e:
                errors.append("Подписки: " + str(e))

        # SQLite in WAL mode can keep the database locked on Windows. Close
        # pooled handles first and remove WAL/SHM sidecars with the main DB.
        try:
            from core.database import connection as _dbc
            _dbc.close_pooled_connections()
            db = Path(db_path(settings))
            for db_file in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
                if db_file.exists():
                    db_file.unlink()
                    deleted.append(str(db_file))
            _dbc._INIT_DONE.clear()
        except Exception as e:
            errors.append("БД: " + str(e))

        # The button says that cache is deleted, so also remove the persistent
        # thumbnail/parser cache rather than only per-bucket sidecars.
        try:
            from core.paths import CACHE_DIR
            if Path(CACHE_DIR).exists():
                self._remove_tree_after_preview_release(CACHE_DIR)
                Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
                deleted.append(str(CACHE_DIR))
        except Exception as e:
            errors.append("Кэш: " + str(e))
        self.nuke_confirm_input.clear()
        summary = "Удалено " + str(len(deleted)) + " объектов."
        if errors:
            summary += chr(10) + "Ошибки:" + chr(10) + chr(10).join(errors)
        QMessageBox.information(self, "Готово", summary)

        try:
            gallery = getattr(self.main, "gallery_page", None)
            if gallery and hasattr(gallery, "refresh"):
                gallery.refresh()
        except Exception:
            pass


    def load_values(self):
        s=self.main.settings; self.root.setText(s.get("root","C:/Local_Booru_Input")); self.title.setText(s.get("theme_title","Local Booru")); self.logo.setText(s.get("logo_path","")); self.cols.setValue(int(s.get("columns",8))); self.rows.setValue(int(s.get("rows_per_page",4))); self.card.setValue(int(s.get("card_height",220))); self.ignore_numeric.setChecked(bool(s.get("ignore_numeric_tags",False))); self.show_preview.setChecked(bool(s.get("show_search_preview", True))); self.error_console.setChecked(bool(s.get("enable_error_console", True))); self.max_console_lines.setValue(int(s.get("max_console_lines",2500))); self.manga_root.setText(s.get("manga_root",""))
        self.flaresolverr_url.setText(s.get("flaresolverr_url","")); self.games_root.setText(s.get("games_root","")); self.output_dir.setText(s.get("output_dir",""))
        self.separate_settings.setChecked(bool(s.get("separate_settings_storage", False))); self.settings_storage_dir.setText(str(s.get("settings_storage_dir", "") or ""))
        try:
            from core.paths import USING_SEPARATE_STORAGE
            if USING_SEPARATE_STORAGE:
                self.separate_settings.setChecked(True)
                self.separate_settings.setEnabled(False)
                self.separate_settings.setToolTip("Архив уже подключён. Чтобы сменить его, используй кнопку «Подключить существующий архив...».")
            else:
                self.separate_settings.setEnabled(True)
                self.separate_settings.setToolTip("")
        except Exception:
            pass
        self.large_download_count.setValue(int(s.get("large_download_warning_count", 1000) or 1000)); self.disk_reserve_gb.setValue(float(s.get("disk_free_reserve_gb", 2.0) or 2.0))
        self.lang.setCurrentIndex(max(0,self.lang.findData(s.get("language","ru")))); self.appearance.setCurrentIndex(max(0,self.appearance.findData(s.get("appearance","dark")))); self.logo_fit.setCurrentIndex(max(0,self.logo_fit.findData(s.get("logo_fit","crop"))))
        self.imports_to_inbox.setChecked(bool(s.get("imports_to_inbox", True))); self.inbox_hours.setValue(int(s.get("inbox_auto_archive_hours", 24) or 24)); self.thumb_cleanup_exit.setChecked(bool(s.get("thumb_cleanup_on_exit", True))); self.thumb_keep_recent.setValue(int(s.get("thumb_keep_recent", 500) or 500))
        self.thumb_quality.setCurrentIndex(max(0, self.thumb_quality.findData(int(s.get("thumb_quality_scale", 2) or 2))))
        self.thumb_memory_items.setValue(int(s.get("thumb_memory_items", 400) or 400)); self.thumb_threads.setValue(int(s.get("thumb_threads", s.get("local_thumb_workers", 4)) or 4)); self.thumb_prefetch.setChecked(bool(s.get("thumb_prefetch_pages", True))); self.performance_slow_ms.setValue(int(s.get("performance_slow_ms", 100) or 100)); self.developer_preload_md5_index.setChecked(bool(s.get("developer_preload_md5_index", True))); self.developer_grabber_md5_cache_enabled.setChecked(bool(s.get("grabber_disk_metadata_cache_enabled", s.get("developer_grabber_md5_cache_enabled", True)))); self.grabber_exact_md5_fanout.setChecked(bool(s.get("grabber_exact_md5_fanout", True))); self.grabber_visual_hash_merge.setChecked(bool(s.get("grabber_visual_hash_merge", False))); self.grabber_visual_hash_distance.setValue(int(s.get("grabber_visual_hash_distance", 3) or 3)); self.developer_filesystem_duplicate_fallback.setChecked(bool(s.get("developer_filesystem_duplicate_fallback", False)))
        self.local_total_workers.setValue(8); self.local_scan_workers.setValue(2); self.local_hash_workers.setValue(4); self.local_image_workers.setValue(4); self.local_video_workers.setValue(2); self.local_db_read_workers.setValue(2); self.local_tagger_workers.setValue(4); self.local_thumb_workers.setValue(4); self.local_thumb_pregen_workers.setValue(1); self.local_background_workers.setValue(4); self.visual_nomatch_workers.setValue(2); self.visual_nomatch_real_threshold.setValue(float(s.get("visual_nomatch_real_threshold", 0.34) or 0.34)); self.visual_nomatch_classify_enabled.setChecked(bool(s.get("visual_nomatch_classify_enabled", True))); self.visual_nomatch_backend.setCurrentIndex(max(0, self.visual_nomatch_backend.findData(str(s.get("visual_nomatch_backend", "clip_local") or "clip_local")))); self.visual_nomatch_clip_model_dir.setText(str(s.get("visual_nomatch_clip_model_dir", "") or "")); self.visual_nomatch_auto_download_model.setChecked(bool(s.get("visual_nomatch_auto_download_model", True))); self.visual_nomatch_device.setCurrentIndex(max(0, self.visual_nomatch_device.findData(str(s.get("visual_nomatch_device", "auto") or "auto")))); self.visual_nomatch_ai_min_confidence.setValue(float(s.get("visual_nomatch_ai_min_confidence", 0.62) or 0.62)); self.visual_nomatch_ai_min_margin.setValue(float(s.get("visual_nomatch_ai_min_margin", 0.12) or 0.12)); self.visual_nomatch_ai_fallback_heuristic.setChecked(bool(s.get("visual_nomatch_ai_fallback_heuristic", False))); self.local_preflight_enabled.setChecked(bool(s.get("local_preflight_enabled", True))); self.local_preflight_phash.setChecked(bool(s.get("local_preflight_phash", True)))
        self.sqlite_cache_mb.setValue(int(s.get("sqlite_cache_mb", 40) or 40)); self.sqlite_checkpoint_exit.setChecked(bool(s.get("sqlite_checkpoint_on_exit", True)))
        self.light_backup_enabled.setChecked(bool(s.get("light_backup_enabled", False))); self.light_backup_on_exit.setChecked(bool(s.get("light_backup_on_exit", True)))
        self.light_backup_interval.setValue(int(s.get("light_backup_interval_hours", 24) or 24)); self.light_backup_keep.setValue(int(s.get("light_backup_keep_last", 10) or 10))
        self.light_backup_dir.setText(str(s.get("light_backup_dir", "") or "")); self.light_backup_include_cookies.setChecked(bool(s.get("light_backup_include_cookies", False)))
        self.hide_single_char_tags.setChecked(bool(s.get("hide_single_char_tags", True))); self.hide_technical_tags.setChecked(bool(s.get("hide_technical_tags", True))); self.hide_meta_tags.setChecked(bool(s.get("hide_meta_tags", False))); self.hide_rating_tags.setChecked(bool(s.get("hide_rating_tags", False)))
        self.grabber_hide_existing.setChecked(bool(s.get("grabber_preview_hide_existing", True)))
        self.grabber_prefetch_originals.setChecked(bool(s.get("grabber_preview_prefetch_originals", False)))
        self.grabber_include_protected_sites.setChecked(bool(s.get("grabber_include_protected_sites", False)))
        self.grabber_stream_cards.setChecked(bool(s.get("grabber_preview_stream_cards", False)))
        self.grabber_cache_limit.setValue(int(s.get("grabber_cache_limit_mb", 200) or 200))
        self.grabber_open_quality.setCurrentIndex(max(0, self.grabber_open_quality.findData(s.get("grabber_open_quality", "medium_50"))))
        self.grabber_metadata_ram_cache.setValue(int(s.get("grabber_metadata_ram_cache_mb", 128) or 128))
        self.grabber_image_ram_cache.setValue(int(s.get("grabber_image_ram_cache_mb", 256) or 256))
        self.grabber_blocklist.setPlainText(str(s.get("grabber_subscriptions_blocklist") or s.get("downloader_blocklist") or ""))
        self.deleted_reimport.setCurrentIndex(max(0, self.deleted_reimport.findData(s.get("deleted_reimport_policy", "skip"))))
        self.trash_days.setCurrentIndex(max(0, self.trash_days.findData(int(s.get("trash_auto_purge_days", 0) or 0))))
    def choose_root(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.root.text())
        if f: self.root.setText(f)
    def choose_manga_root(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.manga_root.text() or self.root.text())
        if f: self.manga_root.setText(f)

    def _connect_detected_archive(self, archive_root):
        from core.paths import connect_existing_archive
        target = connect_existing_archive(archive_root)
        if target is None:
            return False
        self.separate_settings.setChecked(True)
        self.settings_storage_dir.setText(str(target))
        self.output_dir.setText(str(target.parent / "output"))
        try:
            self.main.settings["_workspace_switch_pending"] = True
            self.main.settings["separate_settings_storage"] = True
            self.main.settings["settings_storage_dir"] = str(target)
            self.main.settings["output_dir"] = str(target.parent / "output")
        except Exception:
            pass
        QMessageBox.information(
            self,
            "Архив подключён",
            "Найдены существующие настройки Local Booru:\n"
            f"{target / 'config' / 'app_settings.json'}\n\n"
            "Указатель архива уже переключён. Даже если нажать «Сохранить» "
            "до перезапуска, путь больше не откатится к старому архиву.\n\n"
            "Перезапусти программу — после запуска будут загружены настройки, "
            "база и кэш этого архива.",
        )
        return True

    def connect_existing_archive(self):
        start = self.output_dir.text().strip() or self.root.text()
        f = QFileDialog.getExistingDirectory(self, "Выбери Local_Booru_Archive", start)
        if not f:
            return
        if not self._connect_detected_archive(Path(f)):
            QMessageBox.warning(
                self,
                "Архив не найден",
                "В выбранной папке не найден архив Local Booru.\n\n"
                "Можно выбрать: папку архива, её output, её settings, "
                "или папку, внутри которой лежит Local_Booru_Archive.\n\n"
                "Минимум нужен settings/config/app_settings.json "
                "или settings/db/local_booru_index.sqlite3.",
            )

    def choose_output_dir(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.output_dir.text() or self.root.text())
        if f:
            from core.paths import ensure_output_base, suggested_settings_storage_dir, SETTINGS_FILE, normalize_archive_settings_root
            safe = ensure_output_base(f, self.root.text())
            existing_root = normalize_archive_settings_root(f) or normalize_archive_settings_root(safe.parent)
            try:
                is_current = bool(existing_root and existing_root.resolve() == Path(SETTINGS_FILE).parents[1].resolve())
            except Exception:
                is_current = False
            if existing_root is not None and not is_current:
                answer = QMessageBox.question(
                    self,
                    "Найден существующий архив",
                    "В выбранной папке уже есть база/настройки Local Booru.\n\n"
                    "Подключить этот архив вместо создания/перезаписи настроек?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                if answer == QMessageBox.Yes:
                    self._connect_detected_archive(existing_root.parent)
                    return
            self.output_dir.setText(str(safe))
            # Any new Local_Booru_Archive selection is a unified archive by
            # definition: output and settings are two fixed branches of one root.
            if safe.name.lower() == "output":
                self.separate_settings.setChecked(True)
                preview = dict(self.main.settings); preview["output_dir"] = str(safe)
                self.settings_storage_dir.setText(str(suggested_settings_storage_dir(preview)))
            elif self.separate_settings.isChecked():
                preview = dict(self.main.settings); preview["output_dir"] = str(safe)
                self.settings_storage_dir.setText(str(suggested_settings_storage_dir(preview)))
            QMessageBox.information(self, "Output", f"Файлы будут складываться в:\n{safe}")

    def choose_settings_storage_dir(self):
        start = self.settings_storage_dir.text().strip() or self.output_dir.text().strip() or self.root.text()
        f = QFileDialog.getExistingDirectory(self, "Папка настроек / базы / кэша", start)
        if f:
            self.settings_storage_dir.setText(str(Path(f) / "settings" if Path(f).name.lower() == "local_booru_archive" else Path(f)))
            self.separate_settings.setChecked(True)

    def choose_visual_clip_model_dir(self):
        start = self.visual_nomatch_clip_model_dir.text().strip() or self.settings_storage_dir.text().strip() or self.output_dir.text().strip() or self.root.text()
        f = QFileDialog.getExistingDirectory(self, "Папка локальной CLIP-модели", start)
        if f:
            self.visual_nomatch_clip_model_dir.setText(str(Path(f)))

    def choose_games_root(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.games_root.text() or self.root.text())
        if f: self.games_root.setText(f)

    def choose_logo(self):
        f,_=QFileDialog.getOpenFileName(self,self.main.t("Choose"),self.logo.text(),"Images (*.png *.jpg *.jpeg *.webp)")
        if f: self.logo.setText(f)
    def crop_logo(self):
        if not Path(self.logo.text()).exists(): return
        dlg=LogoCropDialog(self.logo.text(), self)
        dlg.setWindowTitle(self.main.t("Logo crop editor")); dlg.apply.setText(self.main.t("Apply crop"));
        if dlg.exec():
            self.logo.setText(dlg.save_crop()); self.logo_fit.setCurrentIndex(max(0,self.logo_fit.findData("crop")))
    def save(self):
        s=self.main.settings; old_separate = bool(s.get("separate_settings_storage", False)); old_dir = str(s.get("settings_storage_dir", "") or "")
        s["root"]=self.root.text(); s["language"]=self.lang.currentData(); s["appearance"]=self.appearance.currentData(); s["theme_title"]=self.title.text(); s["logo_path"]=self.logo.text(); s["logo_fit"]=self.logo_fit.currentData(); s["columns"]=self.cols.value(); s["rows_per_page"]=self.rows.value(); s["items_per_page"]=self.cols.value()*self.rows.value(); s["card_height"]=self.card.value(); s["ignore_numeric_tags"]=self.ignore_numeric.isChecked(); s["show_search_preview"]=self.show_preview.isChecked(); s["enable_error_console"]=self.error_console.isChecked(); s["max_console_lines"]=self.max_console_lines.value(); s["manga_root"]=self.manga_root.text(); s["games_root"]=self.games_root.text(); s["output_dir"]=self.output_dir.text(); s["flaresolverr_url"]=self.flaresolverr_url.text().strip(); s["imports_to_inbox"]=self.imports_to_inbox.isChecked(); s["inbox_auto_archive_hours"]=self.inbox_hours.value(); s["thumb_cleanup_on_exit"]=self.thumb_cleanup_exit.isChecked(); s["thumb_keep_recent"]=self.thumb_keep_recent.value(); s["deleted_reimport_policy"]=self.deleted_reimport.currentData(); s["trash_auto_purge_days"]=int(self.trash_days.currentData() or 0); s["hide_single_char_tags"]=self.hide_single_char_tags.isChecked(); s["hide_technical_tags"]=self.hide_technical_tags.isChecked(); s["hide_meta_tags"]=self.hide_meta_tags.isChecked(); s["hide_rating_tags"]=self.hide_rating_tags.isChecked()
        s["separate_settings_storage"] = True; s["settings_storage_dir"] = self.settings_storage_dir.text().strip(); s["large_download_warning_count"] = self.large_download_count.value(); s["disk_free_reserve_gb"] = self.disk_reserve_gb.value()
        s["grabber_preview_hide_existing"] = self.grabber_hide_existing.isChecked(); s["grabber_preview_prefetch_originals"] = self.grabber_prefetch_originals.isChecked(); s["grabber_include_protected_sites"] = self.grabber_include_protected_sites.isChecked(); s["grabber_preview_stream_cards"] = self.grabber_stream_cards.isChecked(); s["grabber_cache_limit_mb"] = self.grabber_cache_limit.value(); s["grabber_open_quality"] = self.grabber_open_quality.currentData() or "medium_50"; s["grabber_metadata_ram_cache_mb"] = self.grabber_metadata_ram_cache.value(); s["grabber_image_ram_cache_mb"] = self.grabber_image_ram_cache.value(); s["grabber_subscriptions_blocklist"] = self.grabber_blocklist.toPlainText().strip(); s["downloader_blocklist"] = s["grabber_subscriptions_blocklist"]
        s["thumb_quality_scale"] = int(self.thumb_quality.currentData() or 2); s["thumb_memory_items"] = self.thumb_memory_items.value(); s["thumb_threads"] = self.thumb_threads.value(); s["thumb_prefetch_pages"] = self.thumb_prefetch.isChecked(); s["performance_slow_ms"] = self.performance_slow_ms.value(); s["developer_preload_md5_index"] = self.developer_preload_md5_index.isChecked(); s["developer_grabber_md5_cache_enabled"] = self.developer_grabber_md5_cache_enabled.isChecked(); s["grabber_disk_metadata_cache_enabled"] = self.developer_grabber_md5_cache_enabled.isChecked(); s["grabber_exact_md5_fanout"] = self.grabber_exact_md5_fanout.isChecked(); s["grabber_visual_hash_merge"] = self.grabber_visual_hash_merge.isChecked(); s["grabber_visual_hash_distance"] = self.grabber_visual_hash_distance.value(); s["developer_filesystem_duplicate_fallback"] = self.developer_filesystem_duplicate_fallback.isChecked()
        # Fixed mid-PC local worker defaults; no user-facing thread tuning.
        fixed_workers = {"local_total_workers": 8, "local_scan_workers": 2, "local_hash_workers": 4, "local_image_workers": 4, "local_video_workers": 2, "local_db_read_workers": 2, "local_tagger_workers": 4, "local_thumb_workers": 4, "local_thumb_pregen_workers": 1, "local_background_workers": 4, "visual_nomatch_workers": 2}
        s.update(fixed_workers)
        s["visual_nomatch_backend"] = self.visual_nomatch_backend.currentData() or "clip_local"; s["visual_nomatch_clip_model_dir"] = self.visual_nomatch_clip_model_dir.text().strip(); s["visual_nomatch_auto_download_model"] = self.visual_nomatch_auto_download_model.isChecked(); s["visual_nomatch_device"] = self.visual_nomatch_device.currentData() or "auto"; s["visual_nomatch_ai_min_confidence"] = float(self.visual_nomatch_ai_min_confidence.value()); s["visual_nomatch_ai_min_margin"] = float(self.visual_nomatch_ai_min_margin.value()); s["visual_nomatch_ai_fallback_heuristic"] = self.visual_nomatch_ai_fallback_heuristic.isChecked(); s["visual_nomatch_real_threshold"] = float(self.visual_nomatch_real_threshold.value()); s["visual_nomatch_classify_enabled"] = self.visual_nomatch_classify_enabled.isChecked(); s["local_preflight_enabled"] = True; s["local_preflight_phash"] = True; s["tagger_parallel_workers"] = fixed_workers["local_tagger_workers"]; s["thumb_threads"] = fixed_workers["local_thumb_workers"]; s["thumb_pregen_workers"] = fixed_workers["local_thumb_pregen_workers"]; s["task_max_workers"] = fixed_workers["local_background_workers"]
        s["sqlite_cache_mb"] = self.sqlite_cache_mb.value(); s.setdefault("sqlite_wal_limit_mb", 512); s["sqlite_temp_store"] = str(s.get("sqlite_temp_store") or "FILE"); s["sqlite_checkpoint_on_exit"] = self.sqlite_checkpoint_exit.isChecked()
        s["light_backup_enabled"] = self.light_backup_enabled.isChecked(); s["light_backup_dir"] = self.light_backup_dir.text().strip(); s["light_backup_on_exit"] = self.light_backup_on_exit.isChecked(); s["light_backup_interval_hours"] = self.light_backup_interval.value(); s["light_backup_keep_last"] = self.light_backup_keep.value(); s["light_backup_include_cookies"] = self.light_backup_include_cookies.isChecked()
        if s["separate_settings_storage"]:
            from core.paths import suggested_settings_storage_dir
            # A portable Local_Booru_Archive has one fixed private branch next
            # to output; do not let UI settings split it across drives again.
            s["settings_storage_dir"] = str(suggested_settings_storage_dir(s)); self.settings_storage_dir.setText(s["settings_storage_dir"])
        save_settings(s)
        # save_settings canonicalizes both paths when a portable archive is
        # active or has just been selected. Reflect that immediately in the UI.
        self.separate_settings.setChecked(bool(s.get("separate_settings_storage", False)))
        self.settings_storage_dir.setText(str(s.get("settings_storage_dir", "") or ""))
        self.output_dir.setText(str(s.get("output_dir", "") or ""))
        try:
            from core.thumb_service import ThumbnailService
            ThumbnailService.instance().configure(max_threads=s["thumb_threads"], memory_items=s["thumb_memory_items"])
        except Exception:
            pass
        self.main.gallery_page.items=[]; self.main.tags_page.items=[]; self.main.apply_theme(); self.main.retranslate()
        msg = self.main.t("Settings saved")
        if bool(s.get("_workspace_switch_pending", False)):
            msg += "\n\nАктивный архив переключён в указателе пути. Перезапусти программу, чтобы загрузились SQLite, настройки, кэш и cookies нового архива. Текущий старый архив не перезаписан."
        elif old_separate != s["separate_settings_storage"] or old_dir != s["settings_storage_dir"]:
            msg += "\n\nПосле перезапуска будут использоваться Local_Booru_Archive/output и Local_Booru_Archive/settings. Старые данные скопированы безопасно; полная конфигурация в Documents больше не создаётся."
        QMessageBox.information(self,self.main.t("Saved"),msg)

    def _fmt_bytes(self, value):
        value = float(value or 0)
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if value < 1024 or unit == "ТБ":
                return f"{value:.2f} {unit}"
            value /= 1024

    def show_cache_tools(self):
        from core.library_lifecycle import folder_size
        from core.paths import CACHE_DIR
        cache = Path(CACHE_DIR) / "thumbs"
        size = folder_size(cache)
        ret = QMessageBox.question(self, "Кэш превью", f"Размер кэша превью: {self._fmt_bytes(size)}\n\nОчистить весь кэш? Он будет создан заново при открытии галереи.", QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            try:
                if cache.exists(): shutil.rmtree(cache)
                cache.mkdir(parents=True, exist_ok=True)
                QMessageBox.information(self, "Кэш превью", "Кэш очищен.")
            except Exception as e:
                QMessageBox.warning(self, "Кэш превью", str(e))

    def show_archive_stats(self):
        try:
            from core.library_lifecycle import library_stats
            st = library_stats(self.main.settings)
            text = (f"Всего файлов: {st['files']}\nИзображений: {st['images']}\nВидео: {st['videos']}\n"
                    f"Новые: {st['inbox']}\nУдалено: {st['trash']}\n"
                    f"С тегами: {st['tagged']}\nС источниками: {st['sourced']}\n"
                    f"Размер архива: {self._fmt_bytes(st['bytes'])}\nРазмер кэша: {self._fmt_bytes(st['cache_bytes'])}\nРазмер базы: {self._fmt_bytes(st['db_bytes'])}")
            QMessageBox.information(self, "Статистика архива", text)
        except Exception as e:
            QMessageBox.warning(self, "Статистика", str(e))

    def export_metadata_dialog(self):
        current_ids = []
        try:
            current_ids = self.main.gallery_page.current_result_image_ids()
        except Exception:
            current_ids = []
        use_current = False
        if current_ids:
            answer = QMessageBox.question(
                self, "Экспорт тегов и источников",
                f"Экспортировать текущую выдачу галереи ({len(current_ids)} файлов)?\n\nНажми «Нет», чтобы экспортировать всю библиотеку.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes
            )
            if answer == QMessageBox.Cancel:
                return
            use_current = answer == QMessageBox.Yes
        path, selected = QFileDialog.getSaveFileName(self, "Экспорт тегов и источников", "local_booru_metadata.json", "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        fmt = "csv" if str(path).lower().endswith(".csv") or "CSV" in selected else "json"
        try:
            from core.library_lifecycle import export_metadata
            n = export_metadata(self.main.settings, path, fmt, image_ids=current_ids if use_current else None)
            scope = "текущей выдачи" if use_current else "всей библиотеки"
            QMessageBox.information(self, "Экспорт", f"Экспортировано файлов из {scope}: {n}\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Экспорт", str(e))

    def export_settings_profile(self):
        include_secrets = bool(self.settings_include_secrets.isChecked())
        if include_secrets:
            if QMessageBox.warning(self, "Экспорт настроек", "В архив попадут логины и API-ключи. Не отправляй этот ZIP посторонним. Продолжить?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт профиля настроек", "Local_Booru_settings_profile.zip", "ZIP (*.zip)")
        if not path:
            return
        try:
            from core.settings_bundle import export_profile
            out = export_profile(self.main.settings, path, include_secrets=include_secrets)
            note = "с логинами/API-ключами" if include_secrets else "без логинов/API-ключей"
            QMessageBox.information(self, "Экспорт настроек", f"Профиль сохранён ({note}):\n{out}\n\nSQLite и медиа в этот архив не входят.")
        except Exception as exc:
            QMessageBox.warning(self, "Экспорт настроек", str(exc))

    def import_settings_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт профиля настроек", "", "Профиль ZIP/JSON (*.zip *.json)")
        if not path:
            return
        if QMessageBox.question(self, "Импорт настроек", "Импортировать настройки из профиля?\n\nТекущие настройки будут сохранены в backup. База и медиа не изменяются. Для смены папки хранения данных может потребоваться перезапуск.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            from core.settings_bundle import import_profile
            result = import_profile(path, self.main.settings, apply=True)
            self.main.settings.clear(); self.main.settings.update(result["settings"])
            self.load_values(); self.main.apply_theme(); self.main.retranslate()
            QMessageBox.information(self, "Импорт настроек", f"Профиль применён.\nBackup предыдущих настроек:\n{result.get('backup','')}\n\nЕсли менялась папка хранения базы/кэша, перезапусти приложение.")
        except Exception as exc:
            QMessageBox.warning(self, "Импорт настроек", str(exc))

    def open_logs_folder(self):
        from core.paths import LOGS_DIR
        try:
            import os, subprocess, sys
            if hasattr(os, "startfile"): os.startfile(str(LOGS_DIR))
            elif sys.platform == "darwin": subprocess.Popen(["open", str(LOGS_DIR)])
            else: subprocess.Popen(["xdg-open", str(LOGS_DIR)])
        except Exception as e:
            QMessageBox.warning(self, "Логи", str(e))

    def choose_light_backup_dir(self):
        start = self.light_backup_dir.text().strip() or self.output_dir.text().strip() or self.root.text()
        folder = QFileDialog.getExistingDirectory(self, "Папка для лёгких резервных копий", start)
        if folder:
            self.light_backup_dir.setText(folder)
            self.light_backup_enabled.setChecked(True)

    def create_light_backup_now(self):
        # Pull values from widgets without forcing a full settings save dialog.
        s = self.main.settings
        s["light_backup_enabled"] = self.light_backup_enabled.isChecked()
        s["light_backup_dir"] = self.light_backup_dir.text().strip()
        s["light_backup_on_exit"] = self.light_backup_on_exit.isChecked()
        s["light_backup_interval_hours"] = self.light_backup_interval.value()
        s["light_backup_keep_last"] = self.light_backup_keep.value()
        s["light_backup_include_cookies"] = self.light_backup_include_cookies.isChecked()
        try:
            from core.light_backup import create_light_backup
            result = create_light_backup(s, reason="manual", force=True)
            if not result.get("created"):
                raise RuntimeError(result.get("error") or result.get("reason") or "копия не создана")
            save_settings(s)
            QMessageBox.information(self, "Резервная копия", f"Копия создана:\n{result.get('path')}\n\nРазмер: {int(result.get('bytes', 0)) // 1024} КБ")
        except Exception as exc:
            QMessageBox.warning(self, "Резервная копия", str(exc))

    def show_changelog(self):
        QMessageBox.information(self, "Что нового", "v30 Danbooru JSON-only и корректные API-запросы\n\n- официальный Danbooru берёт теги только из posts.json / posts/<id>.json, без HTML-fallback\n- для Danbooru используется User-Agent LocalBooru вместо имитации браузера в API-запросах\n- если API заблокирован Cloudflare/403, источник честно пропускается без попытки разобрать страницу как теги\n- граббер и подписки используют ту же безопасную логику для официального Danbooru\n\nv29 e621 JSON-only и очистка загрязнённых тегов\n\n- e621/e926 берут теги только из posts.json, без HTML-панели со счётчиками и Uploaded by the artist\n- восстановлены официальные категории contributor / species / lore / invalid\n- обслуживание чистит *_Uploaded_by_the_artist вместе со *_3.0k / *_4.2m\n- e926 получил те же API-ограничения, что e621\n\nv28 просмотрщик, e621 и интерфейс\n\n- открытый пост декодируется с исходным соотношением сторон, без кропа карточного превью\n- исправлен e621 species и очистка тегов со счётчиками\n- «Удалено» получило предпросмотр, теги и источники\n- ПКМ по тегу позволяет выбрать свой цвет\n- клик по тегу всегда открывает Галерею\n- из дубликатора убран шум по одному только разрешению\n- настройка разделов находится только в Настройках\n\nv27 навигация и темы\n\n- переход между страницами через открытый пост больше не пересобирает скрытую галерею\n- для «Удалено» используется нормальная иконка корзины\n- галочки в тёмных темах стали контрастными\n\nv26 исправления просмотрщика и модульного интерфейса\n\n- ПКМ в галерее: удаление в корзину с подтверждением\n- переход назад между страницами в просмотрщике\n- безопасное вписывание медиа целиком по умолчанию\n- species и очистка e621-счётчиков в тегах\n- настройка видимости и групп модулей интерфейса\n\nv25 завершение большого прохода\n\n- очистка загрузчика и подписок теперь идёт через «Удалено»\n- защита от возврата окончательно удалённого MD5 в граббере\n- Новые автоматически уходят в Архив во время работы программы\n- экспорт текущей выдачи и скрытие технических тегов\n- точечная очистка превью вместо сброса всего кэша\n\nv24: хранилище, диагностика, категории и горячие клавиши\nv23: Корзина / Новые / дубликатор / поиск")

    def repair_missing_thumbnails(self):
        self.sql_status.setVisible(True); self.sql_status.setText("Проверка файлов и достройка превью запущена...")
        self.repair_thumbs_btn.setEnabled(False)
        try:
            from core.maintenance_tasks import repair_missing_thumbnails
            task = self.main.task_manager.submit(
                repair_missing_thumbnails, dict(self.main.settings or {}),
                on_progress=lambda msg: self.sql_status.setText(str(msg)),
                on_result=lambda result: self._maintenance_thumb_done(result),
                on_error=lambda err: self._maintenance_thumb_error(err),
            )
            self._thumb_task = task
        except Exception as exc:
            self.repair_thumbs_btn.setEnabled(True); QMessageBox.warning(self, "Превью", str(exc))

    def _maintenance_thumb_done(self, result):
        self.repair_thumbs_btn.setEnabled(True)
        self.sql_status.setText(f"Превью: проверено={result.get('checked',0)}, создано/готово={result.get('created',0)}, отсутствует={result.get('missing',0)}, ошибок={result.get('errors',0)}")

    def _maintenance_thumb_error(self, error):
        self.repair_thumbs_btn.setEnabled(True); self.sql_status.setText("Ошибка обслуживания: " + str(error))

    def relocate_library_root(self):
        chosen = QFileDialog.getExistingDirectory(self, "Выбери новое расположение архива", self.output_dir.text() or self.root.text())
        if not chosen: return
        try:
            from core.library_lifecycle import relocate_missing_library_paths
            preview = relocate_missing_library_paths(self.main.settings, chosen, apply=False)
            if not preview.get("found"):
                QMessageBox.information(self, "Перенос архива", "Совпадающие потерянные файлы в выбранной папке не найдены.")
                return
            ans = QMessageBox.question(self, "Перенос архива", f"Найдено файлов по старым относительным путям: {preview['found']}\n\nОбновить пути в базе?", QMessageBox.Yes | QMessageBox.No)
            if ans == QMessageBox.Yes:
                done = relocate_missing_library_paths(self.main.settings, chosen, apply=True)
                self.output_dir.setText(str(done.get("new_base", chosen)))
                QMessageBox.information(self, "Перенос архива", f"Обновлено путей: {done.get('updated',0)}\nСохрани настройки, чтобы использовать эту папку дальше.")
        except Exception as exc:
            QMessageBox.warning(self, "Перенос архива", str(exc))

    def create_diagnostic_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "Собрать отчёт об ошибке", "Local_Booru_diagnostics.zip", "ZIP (*.zip)")
        if not path: return
        try:
            from core.diagnostics import create_diagnostic_zip
            out = create_diagnostic_zip(self.main.settings, path)
            QMessageBox.information(self, "Диагностика", f"Отчёт создан:\n{out}\n\nКлючи, логины и cookies в настройки отчёта не записываются.")
        except Exception as exc:
            QMessageBox.warning(self, "Диагностика", str(exc))

    def recheck_general_only_categories(self):
        if QMessageBox.question(
            self,
            "Переразложить general-only теги",
            "Найти ВСЕ уже сохранённые Danbooru/Gelbooru/rule34/e621/ATF source-наборы, где все теги записаны как general, и заново запросить категории у сайтов?\n\n"
            "Старого лимита 10000 больше нет: операция пойдёт по всем найденным general-only наборам.\n"
            "Оригинальные файлы не трогаются. Новые теги не добавляются. Будет создан backup SQLite.\n"
            "На большой базе это может идти долго, потому что часть запросов сетевые.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.sql_status.setVisible(True)
        self.sql_status.setText("Поиск general-only тегов для перераскладки...")
        self.recheck_general_categories_btn.setEnabled(False)
        try:
            from core.maintenance_tasks import recheck_general_only_tag_categories
            self._general_category_task = self.main.task_manager.submit(
                recheck_general_only_tag_categories,
                dict(self.main.settings or {}),
                limit=0,
                name="maintenance.general_only_categories",
                on_progress=lambda msg: self.sql_status.setText(str(msg)),
                on_result=lambda result: self._general_category_recheck_done(result),
                on_error=lambda err: self._general_category_recheck_error(err),
            )
        except Exception as exc:
            self.recheck_general_categories_btn.setEnabled(True)
            QMessageBox.warning(self, "Категории тегов", str(exc))

    def _general_category_recheck_done(self, result):
        self.recheck_general_categories_btn.setEnabled(True)
        result = dict(result or {})
        if result.get("error"):
            self.sql_status.setText("Ошибка перераскладки: " + str(result.get("error")))
            QMessageBox.warning(self, "Категории тегов", str(result.get("error")))
            return
        msg = (
            f"General-only перераскладка завершена.\n"
            f"Найдено source-наборов: {result.get('found', 0)}\n"
            f"Проверено быстрым проходом: {result.get('processed', 0)}\n"
            f"Исправлено быстрым проходом: {result.get('fast_fixed', 0)}\n"
            f"Отправлено в сетевую проверку: {result.get('network_queued', 0)}\n"
            f"Проверено сетью: {result.get('network_checked', 0)}\n"
            f"Исправлено наборов всего: {result.get('fixed', 0)}\n"
            f"Обновлено категорий тегов: {result.get('updated', 0)}\n"
            f"Без найденных категорий: {result.get('no_classified', 0)}\n"
            f"Файлов не найдено на диске: {result.get('missing', 0)}\n"
            f"Ошибок: {result.get('errors', 0)}"
        )
        backup = str(result.get("backup") or "")
        if backup:
            msg += f"\n\nBackup SQLite:\n{backup}"
        samples = result.get("samples") or []
        if samples:
            msg += "\n\nПримеры лога:\n" + "\n".join(str(x) for x in samples[:8])
        self.sql_status.setText(msg.replace("\n", "  |  "))
        try: self.main.tags_page.refresh_force()
        except Exception: pass
        try: self.main.gallery_page.refresh_force()
        except Exception: pass
        QMessageBox.information(self, "Категории тегов", msg)

    def _general_category_recheck_error(self, error):
        self.recheck_general_categories_btn.setEnabled(True)
        self.sql_status.setText("Ошибка перераскладки категорий: " + str(error))
        QMessageBox.warning(self, "Категории тегов", str(error))

    def repair_e621_tags(self):
        if QMessageBox.question(self, "Исправить e621-теги", "Исправить сохранённые e621-теги вида horse_231k / canine_3.0k / yourumi_Uploaded_by_the_artist и восстановить категории из сохранённых JSON-данных?\n\nРезервная копия базы будет создана автоматически.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            from core.library_lifecycle import force_backup_database
            from core.database.maintenance import repair_e621_tag_metadata
            backup = force_backup_database(self.main.settings, "repair_e621_tags")
            if not backup:
                QMessageBox.warning(self, "e621-теги", "Не удалось создать резервную копию базы. Исправление отменено.")
                return
            result = repair_e621_tag_metadata(self.main.settings)
            try: self.main.tags_page.refresh_force(); self.main.gallery_page.refresh_force()
            except Exception: pass
            QMessageBox.information(self, "e621-теги", f"Исправлено загрязнённых связей: {result.get('renamed_links', 0)}\nИсправлено категорий: {result.get('species_fixed', 0)}\nЗатронуто файлов: {result.get('images', 0)}\n\nЕсли у старого файла не сохранился JSON поста, категорию можно окончательно восстановить повторным тегированием этого файла.")
        except Exception as exc:
            QMessageBox.warning(self, "e621-теги", str(exc))

    def configure_interface_modules(self):
        dlg = InterfaceModulesDialog(self.main.settings, self)
        if dlg.exec():
            try:
                values = dlg.values()
                if len(values) == 5:
                    modules, order, auto_hide, extra_collapsed, free_navigation = values
                else:
                    modules, order, auto_hide, extra_collapsed = values
                    free_navigation = True
            except ValueError as exc:
                QMessageBox.warning(self, "Разделы интерфейса", str(exc))
                return
            self.main.settings["interface_modules"] = modules
            self.main.settings["interface_module_order"] = order
            self.main.settings["auto_hide_single_workspace"] = auto_hide
            self.main.settings["interface_extra_collapsed"] = extra_collapsed
            self.main.settings["interface_free_navigation"] = free_navigation
            save_settings(self.main.settings)
            try:
                self.main.apply_interface_modules()
            except Exception as exc:
                QMessageBox.warning(self, "Модули интерфейса", str(exc)); return
            QMessageBox.information(self, "Разделы интерфейса", "Свободная структура страниц применена.")

    def configure_tag_categories(self):
        dlg = TagCategoryDialog(self.main.settings, self)
        if dlg.exec():
            order, colors = dlg.values(); self.main.settings["tag_group_order"] = order; self.main.settings["tag_group_colors"] = colors
            try: self.main.tags_page.reload_category_configuration()
            except Exception: pass
            try: self.main.gallery_page._render_page_tags()
            except Exception: pass
            QMessageBox.information(self, "Категории тегов", "Изменения применены. Нажми «Сохранить настройки», чтобы оставить их после перезапуска.")

    def configure_hotkeys(self):
        dlg = HotkeysDialog(self.main.settings, self)
        if dlg.exec():
            self.main.settings["hotkeys"] = dlg.values()
            QMessageBox.information(self, "Горячие клавиши", "Сохранены в текущих настройках. Для уже открытого просмотрщика клавиши обновятся после его повторного открытия.")

    def _run_maintenance(self):
        from core.stability import run_file_maintenance
        log_lines = []
        def _log(m): log_lines.append(m)
        stats = run_file_maintenance(self.main.settings, log=_log, max_check=99999)
        msg = (f"Проверено: {stats['checked']} файлов\n"
               f"Отсутствует: {stats['missing']} (помечены удалёнными)\n"
               f"Ошибок: {stats['errors']}")
        QMessageBox.information(self, "File Maintenance", msg)

    def _test_flaresolverr(self):
        from core.flaresolverr import FlareSolverrClient
        url = self.flaresolverr_url.text().strip() or "http://localhost:8191"
        client = FlareSolverrClient(url)
        if client.is_running():
            QMessageBox.information(self, "FlareSolverr",
                f"✅ FlareSolverr доступен на {url}\n\n"
                "Теперь ascii2d и другие CF-сайты будут работать через реальный Chrome.")
        else:
            QMessageBox.warning(self, "FlareSolverr",
                f"❌ FlareSolverr недоступен на {url}\n\n"
                "Установка:\n"
                "1. Скачай с github.com/FlareSolverr/FlareSolverr/releases\n"
                "2. Распакуй и запусти flaresolverr.exe\n"
                "3. Или: docker run -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest\n"
                "4. Укажи URL выше и нажми Сохранить настройки")

    def _backup_db(self):
        from core.stability import maybe_backup_db
        backup = maybe_backup_db(self.main.settings, max_backups=10)
        if backup:
            from pathlib import Path
            QMessageBox.information(self, "Резервная копия",
                f"Бэкап создан:\n{Path(backup).name}\n{Path(backup).stat().st_size//1024} KB")
        else:
            QMessageBox.information(self, "Резервная копия",
                "Бэкап уже создавался в последние 24 часа.\nДля принудительного — удали файлы в Local_Booru_Archive/settings/output/backups/db/")

    def _rebuild_vptree(self):
        from core.database.connection import get_connection
        from core.vptree import VPTree
        from PySide6.QtWidgets import QProgressDialog, QApplication
        conn = get_connection(self.main.settings)
        dlg = QProgressDialog("Пересборка VP-tree...", "Отмена", 0, 100, self)
        dlg.setWindowTitle("VP-tree")
        dlg.setMinimumDuration(0)
        dlg.show()
        def progress(done, total):
            if total > 0:
                dlg.setValue(int(100 * done / total))
            QApplication.processEvents()
        count = VPTree(conn).rebuild(progress_cb=progress)
        dlg.setValue(100)
        QMessageBox.information(self, "VP-tree", f"Готово! Проиндексировано: {count} файлов.")

    def rebuild_sql_index(self):
        self.rebuild_sql_btn.setEnabled(False)
        self.sql_status.setVisible(True); self.sql_status.setText("SQLite: индексация запущена...")
        try:
            from core.services.index_service import rebuild_index
            settings = dict(self.main.settings or {})
            task = self.main.task_manager.submit(
                rebuild_index,
                settings,
                force=True,
                with_md5=False,
                on_progress=lambda msg: self.sql_status.setText("SQLite: " + str(msg)),
                on_result=self._sql_index_done,
                on_error=self._sql_index_error,
            )
            self._sql_index_task = task
        except Exception as e:
            self.rebuild_sql_btn.setEnabled(True)
            self.sql_status.setText(f"SQLite ERROR: {e}")

    def _sql_index_done(self, result):
        self.rebuild_sql_btn.setEnabled(True)
        self.sql_status.setText(
            "SQLite готов: "
            f"scanned={result.get('scanned', 0)} "
            f"indexed={result.get('indexed', 0)} "
            f"skipped={result.get('skipped', 0)} "
            f"removed={result.get('removed', 0)}"
        )
        try:
            self.main.gallery_page.items = []
            self.main.gallery_page.refresh_force()
        except Exception:
            pass

    def _sql_index_error(self, text):
        self.rebuild_sql_btn.setEnabled(True)
        self.sql_status.setText("SQLite ERROR:\n" + str(text)[-3000:])

    def import_legacy_sidecars(self):
        answer = QMessageBox.question(
            self, "Импорт старых метаданных",
            "Импортировать старые .tags/.sources sidecar-файлы в SQLite?\n\n"
            "Это отдельная миграция для старых библиотек. После неё живой режим использует только SQLite.\n"
            "Перед импортом будет создана резервная копия базы.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.legacy_sidecars_btn.setEnabled(False)
        self.sql_status.setVisible(True); self.sql_status.setText("Sidecar → SQLite: импорт запущен...")
        try:
            from core.services.index_service import import_legacy_sidecar_metadata
            self._legacy_import_task = self.main.task_manager.submit(
                import_legacy_sidecar_metadata, dict(self.main.settings or {}),
                name="legacy-sidecar-import",
                on_progress=lambda msg: self.sql_status.setText("Sidecar → SQLite: " + str(msg)),
                on_result=self._legacy_sidecars_done,
                on_error=self._legacy_sidecars_error,
            )
        except Exception as e:
            self.legacy_sidecars_btn.setEnabled(True)
            self.sql_status.setText(f"Sidecar → SQLite ERROR: {e}")

    def _legacy_sidecars_done(self, result):
        self.legacy_sidecars_btn.setEnabled(True)
        self.sql_status.setText(
            "Sidecar импортированы в SQLite: "
            f"indexed={result.get('indexed', 0)} scanned={result.get('scanned', 0)}; "
            f"backup={result.get('backup', '') or 'нет'}"
        )
        try:
            self.main.gallery_page.refresh_force()
        except Exception:
            pass

    def _legacy_sidecars_error(self, text):
        self.legacy_sidecars_btn.setEnabled(True)
        self.sql_status.setText("Sidecar → SQLite ERROR:\n" + str(text)[-3000:])

    def show_instruction(self):
        dlg = InstructionDialog(self.main, self)
        dlg.exec()


    def _cleanup_roots_for_scope(self, scope):
        out = result_output_base(self.main.settings)
        roots = []
        tagger_buckets = ["found", "partial_match", "no_match"]
        downloader_buckets = ["found", "partial_match", "no_match"]
        if scope in ("all", "tagger", "found", "no_match"):
            for b in tagger_buckets:
                if scope == "found" and b == "no_match":
                    continue
                if scope == "no_match" and b != "no_match":
                    continue
                roots.append(out / b)
        if scope in ("all", "downloader", "found", "no_match"):
            for b in downloader_buckets:
                if scope == "found" and b == "no_match":
                    continue
                if scope == "no_match" and b != "no_match":
                    continue
                roots.append(out / "downloads" / b)
        return roots

    def _tags_from_file(self, f):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        if f.suffix.lower() == ".json":
            try:
                data = json.loads(text)
                found = []
                def walk(x):
                    if isinstance(x, dict):
                        for v in x.values():
                            walk(v)
                    elif isinstance(x, (list, tuple)):
                        for v in x:
                            walk(v)
                    elif isinstance(x, str):
                        found.append(x)
                walk(data)
                return found
            except Exception:
                pass
        return re.split(r"[\s,;]+", text)

    def _sources_from_file(self, f):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        found = []
        if f.suffix.lower() == ".json":
            try:
                data = json.loads(text)
                def walk(x):
                    if isinstance(x, dict):
                        for k, v in x.items():
                            if "source" in str(k).lower() or "url" in str(k).lower():
                                if isinstance(v, str):
                                    found.append(v)
                            walk(v)
                    elif isinstance(x, (list, tuple)):
                        for v in x:
                            walk(v)
                    elif isinstance(x, str):
                        if "://" in x or "." in x:
                            found.append(x)
                walk(data)
            except Exception:
                pass
        for m in re.findall(r"https?://[^\s\"'<>]+", text):
            found.append(m)
        for line in text.splitlines():
            line = line.strip()
            if line and ("." in line or "source" in line.lower()):
                found.append(line)
        clean = []
        seen = set()
        for s in found:
            s = str(s).strip().strip(",;")
            if not s:
                continue
            if s not in seen:
                seen.add(s)
                clean.append(s)
        return clean

    def _media_for_stem(self, bucket_dir, stem):
        media = bucket_dir / "media"
        if not media.exists():
            return []
        return [f for f in media.iterdir() if f.is_file() and f.stem == stem]

    def _cleanup_process_events(self):
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _candidate_values(self, kind):
        scope = self.tag_cleanup_scope.currentData() or "all"
        try:
            if kind == "source":
                from core.services.library_service import candidate_sources
                return candidate_sources(self.main.settings, scope)
            from core.services.library_service import candidate_tags
            return candidate_tags(self.main.settings, scope)
        except Exception as e:
            QMessageBox.warning(self, "SQLite", f"Не удалось прочитать варианты из SQLite:\n{e}")
            return []


    def refresh_cleanup_candidates(self):
        kind = self.tag_cleanup_kind.currentData() or "tag"
        vals = self._candidate_values(kind)
        self._cleanup_candidates = vals
        self.cleanup_candidate_model.setStringList(vals)
        self.update_cleanup_completion()

    def update_cleanup_completion(self):
        try:
            text = self.tag_cleanup_query.text().strip()
            self.cleanup_candidate_proxy.setFilterFixedString(text)
            self.cleanup_completer.setCompletionPrefix(text)
            if text:
                self.cleanup_completer.complete()
        except Exception:
            pass

    def _selected_cleanup_value(self):
        q = self.tag_cleanup_query.text().strip()
        if not q:
            return ""
        # For tags we compare normalized values, for source exact text is kept.
        kind = self.tag_cleanup_kind.currentData() or "tag"
        if kind == "tag":
            qn = normalize_tag(q)
            vals = getattr(self, "_cleanup_candidates", None) or self._candidate_values(kind)
            for v in vals:
                if normalize_tag(v).lower() == qn.lower():
                    return normalize_tag(v)
            return qn
        return q

    def _find_tag_cleanup_matches(self):
        value = self._selected_cleanup_value()
        if not value:
            return []
        kind = self.tag_cleanup_kind.currentData() or "tag"
        scope = self.tag_cleanup_scope.currentData() or "all"
        try:
            if kind == "source":
                from core.services.library_service import find_images_by_source
                rows = find_images_by_source(self.main.settings, value, scope)
            else:
                from core.services.library_service import find_images_by_tag
                rows = find_images_by_tag(self.main.settings, value, scope)
            return [{"id": r["id"], "path": r["path"], "file_name": r["file_name"], "bucket": r["bucket"], "size_bytes": int(r.get("size_bytes", 0) if isinstance(r, dict) else r["size_bytes"] or 0), "hits": [value]} for r in rows]
        except Exception as e:
            QMessageBox.warning(self, "SQLite", f"Ошибка поиска связанных файлов:\n{e}")
            return []


    def find_tag_cleanup_matches(self):
        if not hasattr(self, "_cleanup_candidates"):
            self.refresh_cleanup_candidates()
        self.tag_cleanup_find_btn.setEnabled(False)
        self.tag_cleanup_find_btn.setText("Сканирование...")
        self.tag_cleanup_list.clear()
        self.tag_cleanup_list.setVisible(False)
        self.tag_cleanup_delete_btn.setEnabled(False)
        self.tag_cleanup_delete_btn.setVisible(False)
        try:
            self._cleanup_process_events()
            self._tag_cleanup_matches = self._find_tag_cleanup_matches()
        finally:
            self.tag_cleanup_find_btn.setEnabled(True)
            self.tag_cleanup_find_btn.setText("Показать связанные файлы")

        value = self._selected_cleanup_value()
        kind_label = "source" if (self.tag_cleanup_kind.currentData() == "source") else "tag"
        total = len(self._tag_cleanup_matches)
        shown_limit = 500
        self.tag_cleanup_list.setUpdatesEnabled(False)
        try:
            if total > shown_limit:
                self.tag_cleanup_list.addItem(f"Найдено: {total}. Показаны первые {shown_limit}, удаление применится ко всем найденным.")
            for m in self._tag_cleanup_matches[:shown_limit]:
                media_name = m.get("file_name") or Path(m.get("path", "")).name
                hit = ", ".join(str(x) for x in m.get("hits", [])[:3])
                self.tag_cleanup_list.addItem(f'{media_name}  |  {m.get("bucket", "")}  |  {kind_label}: {hit}')
        finally:
            self.tag_cleanup_list.setUpdatesEnabled(True)
        has_matches = bool(self._tag_cleanup_matches)
        self.tag_cleanup_list.setVisible(has_matches)
        self.tag_cleanup_delete_btn.setEnabled(has_matches)
        self.tag_cleanup_delete_btn.setVisible(has_matches)
        if not self._tag_cleanup_matches and value:
            QMessageBox.information(self, self.main.t("Done"), f"Связанных файлов не найдено для: {value}")

    def delete_tag_cleanup_matches(self):
        matches = getattr(self, "_tag_cleanup_matches", None)
        if matches is None:
            matches = self._find_tag_cleanup_matches()
        value = self._selected_cleanup_value()
        kind_label = "source" if (self.tag_cleanup_kind.currentData() == "source") else "tag"
        if not value or not matches:
            QMessageBox.information(self, self.main.t("Done"), "Ничего не найдено.")
            return
        total_bytes = sum(int(m.get("size_bytes") or 0) for m in matches)
        if QMessageBox.warning(
            self,
            self.main.t("Confirm"),
            f"Переместить в «Удалено» {len(matches)} файлов, связанных с {kind_label}: {value!r}?\n"
            f"Общий размер: {self._fmt_bytes(total_bytes)}\n\n"
            "Файлы можно будет восстановить из раздела «Удалено». Перед операцией создаётся резервная копия базы.",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return
        self._release_open_media_before_delete()
        try:
            if self.tag_cleanup_kind.currentData() == "source":
                from core.services.library_service import delete_by_source
                result = delete_by_source(self.main.settings, value, scope=self.tag_cleanup_scope.currentData() or "all", delete_files=True)
            else:
                from core.services.library_service import delete_by_tag
                result = delete_by_tag(self.main.settings, value, scope=self.tag_cleanup_scope.currentData() or "all", delete_files=True)
            deleted = result.get("deleted_files", result.get("files", 0))
            errors = result.get("errors", 0)
            records = result.get("deleted_records", result.get("records", 0))
        except Exception as e:
            QMessageBox.warning(self, "SQLite", f"Ошибка удаления из SQLite:\n{e}")
            return
        self.tag_cleanup_list.clear()
        self.tag_cleanup_list.setVisible(False)
        self.tag_cleanup_delete_btn.setEnabled(False)
        self.tag_cleanup_delete_btn.setVisible(False)
        self._tag_cleanup_matches = []
        try:
            self.main.gallery_page.items = []
            self.main.tags_page.items = []
            self.main.gallery_page.refresh_force()
        except Exception:
            pass
        self.refresh_cleanup_candidates()
        QMessageBox.information(self, self.main.t("Done"), f"Перемещено записей в «Удалено»: {records}\nПеремещено файлов: {deleted}\nОшибки: {errors}")

    def _refresh_after_delete(self):
        try:
            self.main.gallery_page.refresh_force()
        except Exception:
            pass
        try:
            if hasattr(self.main.tags_page, "refresh"):
                self.main.tags_page.refresh()
        except Exception:
            pass
        try:
            self.refresh_cleanup_candidates()
        except Exception:
            pass

    def _delete_leftovers_in_buckets(self, base: Path, buckets: list[str]) -> tuple[int, int]:
        """Unindexed files are never removed automatically.

        SQLite-backed files go to Trash; deleting unknown disk files here would
        bypass restoration and the backup preview. A later maintenance pass can
        index them first.
        """
        return 0, 0


    def delete_downloader_results(self):
        """Delete downloader results from disk and SQLite in one operation."""
        mode = self.downloader_delete_mode.currentData() or "all"
        disk_buckets = ["found", "partial_match", "no_match"] if mode == "all" else [mode]
        db_map = {
            "found": "downloaded_found",
            "partial_match": "downloaded_partial_match",
            "no_match": "downloaded_no_match",
        }
        db_buckets = [db_map[b] for b in disk_buckets if b in db_map] + (["downloaded"] if mode == "all" else [])
        msg = (
            "Удалить результаты загрузчика?\n\n"
            f"Разделы: {', '.join(disk_buckets)}\n"
            "Файлы будут перемещены в «Удалено» и останутся доступными для восстановления."
        )
        if QMessageBox.warning(self, self.main.t("Confirm"), msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._release_open_media_before_delete()
        try:
            from core.services.library_service import delete_by_buckets
            result = delete_by_buckets(self.main.settings, db_buckets, delete_files=True)
            extra_deleted, extra_errors = self._delete_leftovers_in_buckets(result_output_base(self.main.settings) / "downloads", disk_buckets)
            deleted = int(result.get("deleted_files", 0)) + extra_deleted
            records = int(result.get("deleted_records", 0))
            errors = int(result.get("errors", 0)) + extra_errors
            self._refresh_after_delete()
            QMessageBox.information(self, self.main.t("Done"), f"Перемещено записей в «Удалено»: {records}\nПеремещено файлов: {deleted}\nОшибки: {errors}")
        except Exception as e:
            QMessageBox.warning(self, "Удаление", f"Ошибка удаления результатов загрузчика:\n{e}")

    def delete_tags(self):
        """Delete parser output safely and keep SQLite consistent."""
        mode = self.delete_mode.currentData() or "all"
        buckets = {
            "found": ["found", "partial_match"],
            "no_match": ["no_match"],
            "all": ["found", "partial_match", "no_match"],
        }.get(mode, ["found", "partial_match", "no_match"])
        msg = (
            "Удалить выбранные результаты парсера?\n\n"
            f"Разделы: {', '.join(buckets)}\n"
            "Файлы будут перемещены в «Удалено» и останутся доступными для восстановления."
        )
        if QMessageBox.warning(self, self.main.t("Confirm"), msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._release_open_media_before_delete()
        try:
            from core.services.library_service import delete_by_buckets
            result = delete_by_buckets(self.main.settings, buckets, delete_files=True)
            extra_deleted, extra_errors = self._delete_leftovers_in_buckets(result_output_base(self.main.settings), buckets)
            deleted = int(result.get("deleted_files", 0)) + extra_deleted
            records = int(result.get("deleted_records", 0))
            errors = int(result.get("errors", 0)) + extra_errors
            try:
                if CACHE_FILE.exists():
                    CACHE_FILE.unlink()
            except Exception:
                pass
            self._refresh_after_delete()
            QMessageBox.information(self, self.main.t("Done"), f"Перемещено записей в «Удалено»: {records}\nПеремещено файлов: {deleted}\nОшибки: {errors}")
        except Exception as e:
            QMessageBox.warning(self, "Удаление", f"Ошибка удаления результатов парсера:\n{e}")


    def check_library_integrity(self):
        try:
            from core.services.library_service import check_integrity, unfinished_operations
            res = check_integrity(self.main.settings)
            unfinished = unfinished_operations(self.main.settings, limit=20)
            counts = res.get("counts", {})
            text = [f"Проблем найдено: {res.get('total', 0)}"]
            if counts:
                text += [f"{k}: {v}" for k, v in sorted(counts.items())]
            if unfinished:
                text.append(f"Незавершённых операций в journal: {len(unfinished)}")
            self.sql_status.setText("Integrity: " + "; ".join(text))
            QMessageBox.information(self, "Integrity", "\n".join(text))
        except Exception as e:
            self.sql_status.setText("Integrity check error: " + str(e))

    def repair_library_integrity(self):
        try:
            from core.services.library_service import repair_integrity
            res = repair_integrity(self.main.settings)
            fixed = res.get("fixed", {})
            after = res.get("after", {})
            text = (
                f"Помечено отсутствующих файлов как deleted: {fixed.get('missing_marked_deleted', 0)}\n"
                f"Удалено orphan-строк: {fixed.get('orphan_rows_removed', 0)}\n"
                f"Осталось проблем: {after.get('total', 0)}"
            )
            self.sql_status.setText("Repair: " + text.replace("\n", "; "))
            QMessageBox.information(self, "Repair", text)
        except Exception as e:
            self.sql_status.setText("Integrity repair error: " + str(e))

    def optimize_sqlite(self):
        try:
            from core.database.maintenance import optimize
            res = optimize(self.main.settings)
            self.sql_status.setVisible(True)
            self.sql_status.setText("SQLite: ANALYZE / PRAGMA optimize выполнены. " + str(res.get("db", "")))
        except Exception as e:
            self.sql_status.setVisible(True); self.sql_status.setText("SQLite optimize error: " + str(e))

    def show_sqlite_stats(self):
        try:
            from core.database.maintenance import stats, storage_report
            data = stats(self.main.settings); storage = storage_report(self.main.settings)
            mib = lambda value: float(value or 0) / (1024 * 1024)
            text = ("SQLite v{version}: {size:.2f} MB; свободных страниц: {free} ({reclaim:.2f} MB); "
                    "images={images}, tags={tags}, sources={sources}").format(
                version=storage.get("schema_version", "?"), size=mib(storage.get("size_bytes")),
                free=storage.get("freelist_pages", 0), reclaim=mib(storage.get("reclaimable_bytes")),
                images=data.get("images", 0), tags=data.get("tags", 0), sources=data.get("sources", 0))
            self.sql_status.setVisible(True); self.sql_status.setText(text)
        except Exception as e:
            self.sql_status.setVisible(True); self.sql_status.setText("SQLite stats error: " + str(e))

    def force_sqlite_backup(self):
        try:
            from core.database.maintenance import force_backup
            out = force_backup(self.main.settings, "manual_sqlite_backup")
            if not out:
                raise RuntimeError("Не удалось создать backup SQLite")
            self.sql_status.setVisible(True); self.sql_status.setText("Backup SQLite создан: " + str(out))
            QMessageBox.information(self, "Backup SQLite", "Резервная копия создана:\n" + str(out))
        except Exception as e:
            QMessageBox.warning(self, "Backup SQLite", str(e))

    def vacuum_sqlite(self):
        tagger = getattr(self.main, "pages", {}).get("Tagger")
        if tagger is not None and getattr(tagger, "worker", None) is not None and tagger.worker.isRunning():
            QMessageBox.warning(self, "VACUUM SQLite", "Останови парсер перед сжатием SQLite. VACUUM блокирует рабочую базу на время операции.")
            return
        try:
            from core.database.maintenance import storage_report
            before = storage_report(self.main.settings)
            reclaim_mb = float(before.get("reclaimable_bytes", 0) or 0) / (1024 * 1024)
            size_mb = float(before.get("size_bytes", 0) or 0) / (1024 * 1024)
            message = (f"Сжать рабочую SQLite?\n\nРазмер: {size_mb:.2f} MB\n"
                       f"Потенциально освобождается: {reclaim_mb:.2f} MB\n\n"
                       "Перед операцией будет создан backup. Основной архив не затрагивается.")
            if QMessageBox.question(self, "VACUUM SQLite", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            from core.database.maintenance import vacuum
            result = vacuum(self.main.settings, make_backup=True)
            freed = float(result.get("reclaimed_bytes", 0) or 0) / (1024 * 1024)
            self.sql_status.setVisible(True); self.sql_status.setText(f"VACUUM завершён. Освобождено: {freed:.2f} MB; backup: {result.get('backup','')}")
            QMessageBox.information(self, "VACUUM SQLite", f"Готово. Освобождено: {freed:.2f} MB\nBackup: {result.get('backup','')}")
        except Exception as e:
            QMessageBox.warning(self, "VACUUM SQLite", str(e))



class InstructionDialog(QDialog):
    SECTIONS = {
        "Быстрый старт": """1. Укажи папку с картинками в «Настройки → Папка с картинками»
2. Открой «Парсер» → войди на нужные сайты (кнопка «Войти (все)»)
3. Сохрани куки и нажми СТАРТ
4. Готово — теги появятся в «Галерея»

Danbooru может не работать из-за Cloudflare. Сейчас это необязательный источник:
  • если сайт отвечает — он используется
  • если возвращает 403 — парсер продолжает работу через другие сайты""",

        "Поиск в Галерее": """Строка поиска поддерживает:

── Теги (как обычно) ──────────────────
  fox_girl    — найти все с этим тегом
  -loli       — исключить тег
  tag1 tag2   — оба тега одновременно
  tag1/tag2   — любой из двух тегов в одной выдаче

── Быстрые фильтры Local Booru ─────────
  size:+50mb         — размер от 50 МБ
  size:-500kb        — размер до 500 КБ
  size:0.1mb-5mb    — размер от 0.1 до 5 МБ
  width:+1920       — ширина от 1920
  height:+1080      — высота от 1080
  rating:+4         — рейтинг от 4
  duration:+60s     — видео длиннее минуты

── Числовой поиск (человеческий язык) ─
  размер файла больше 50мб
  ширина больше 2000
  высота меньше 720
  рейтинг не меньше 4
  длительность больше 30 секунд
  длительность больше 1 минуты
  размер файла меньше 2гб

По-английски тоже работает:
  filesize > 100mb
  width > 1920
  rating >= 4
  duration < 60

Можно совмещать теги и числовой поиск:
  fox_girl размер файла больше 2мб

Прямо под строкой поиска появится превью:
  [размер файла] [больше] [50 MB]

Фильтр «★+» в галерее — только файлы с рейтингом.""",

        "Рейтинг ★★★★★": """В посте внизу — 5 звёздочек.
Кликни чтобы поставить оценку (1-5).
Повторный клик на ту же звезду — снять оценку.
Рейтинг сохраняется в базе данных.

Фильтрация по рейтингу:
  В Галерее выбери «★+» / «★★+» / «★★★+» и т.д.""",

        "Перед поиском тегов": """1. Проверь cookies для сайтов через br34
2. Укажи папку с картинками
3. Укажи output-папку (программа создаст Local_Booru_Archive/output)
4. Не перемещай файлы во время поиска
5. Для долгого прогона: лимит строк консоли + пауза между запросами

MD5-поиск работает только если файл уже был на booru с таким именем.
IQDB и SauceNAO находят по содержимому — работают почти всегда.""",

        "Парсер / Сайты": """Система поиска тегов:
  1. MD5 по filename (мгновенно)
  2. MD5 реального файла (быстро)
  3. IQDB (поиск по изображению, бесплатно)
  4. ascii2d (если включён)
  5. SauceNAO (нужен API ключ)
  6. TinEye (если включён, запускается после SauceNAO)

Авторизация сайтов:
  rule34.xxx использует login / API key / User ID из таблицы сайтов
  Для сайтов с cookies можно использовать br34 или импорт cookies.txt

Danbooru:
  Cloudflare может вернуть 403 даже после входа. Это известное ограничение сайта,
  поэтому Danbooru не должен останавливать поиск по другим источникам.""",

        "VP-tree (похожие файлы)": """В Настройках → «Пересобрать VP-tree»
Строит индекс для быстрого поиска похожих изображений.
Используется в разделе «Дубли».

Нужно пересобрать после добавления новых файлов.
Время: ~1-2 мин на 30 000 файлов.""",

        "Управление": """Галерея:
  A / D        — предыдущий / следующий пост
  F            — добавить в избранное
  W            — переключить подгонку (ширина / высота)
  E            — вкл/выкл звук видео
  F11          — полный экран
  +  /  -      — увеличить / уменьшить
  0            — сбросить масштаб
  Колесо мыши  — на изображении: пред/след пост
  Клик по видео — пауза / продолжить
  При переходе на другой пост воспроизведение видео останавливается.

Поиск:
  Enter / Обновить — применить фильтр
  Рандом           — случайный пост""",

        "Папки и данные": """Общий корень:            Local_Booru_Archive/
Медиа/архив:             Local_Booru_Archive/output/
Настройки:               Local_Booru_Archive/settings/config/
SQLite:                  Local_Booru_Archive/settings/db/
Кэш:                     Local_Booru_Archive/settings/cache/
Куки для сайтов:         Local_Booru_Archive/settings/output/runtime/browser_cookies/

Рабочие данные не копируются в Documents. Для поиска архива новой версией
хранится только указатель пути: %LOCALAPPDATA%/Local_Booru/workspace_pointer.json.
Если архив не подхватился: Настройки → Подключить существующий архив...

Программу можно обновить заменой файлов — данные не потеряются.""",

        "Ошибки": """JS/CSP/iframe ошибки в консоли — норма, от самих сайтов, не влияют на работу.

Важные ошибки:
  MD5 BLOCK:  Cloudflare блокирует — нужны куки
  MD5 ERROR:  проблема с API сайта
  IQDB ERROR: временно недоступен
  Python traceback — реальный баг, можно скопировать и сообщить

Лог ошибок: Local_Booru_Archive/settings/output/logs/errors.log""",
    }
    def __init__(self, main, parent=None):
        super().__init__(parent); self.main=main; self.setWindowTitle('Инструкция'); self.resize(900,650)
        lay=QVBoxLayout(self); split=QSplitter(Qt.Horizontal); lay.addWidget(split,1)
        self.list=QListWidget(); self.text=QTextEdit(); self.text.setReadOnly(True)
        split.addWidget(self.list); split.addWidget(self.text); split.setSizes([260,640])
        for name in self.SECTIONS:
            self.list.addItem(QListWidgetItem(name))
        self.list.currentRowChanged.connect(self.show_section)
        self.list.setCurrentRow(0)
    def show_section(self, row):
        keys=list(self.SECTIONS.keys())
        if 0 <= row < len(keys):
            k=keys[row]
            self.text.setPlainText(k+'\n\n'+self.SECTIONS[k])
