from collections import Counter, defaultdict
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QPushButton,QLabel,QListWidget,QComboBox,QSplitter,QButtonGroup,QListWidgetItem,QMenu,QColorDialog
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush
from core.library import scan_library, sort_tag_items, normalize_tag
from core.tag_utils import tag_display_color

GROUP_ORDER=["artist","contributor","character","copyright","species","general","meta","lore","invalid","parody","language","category","pages"]
GROUP_COLORS={"artist":"#ff3838","contributor":"#e67e22","character":"#00a000","copyright":"#ff54a7","species":"#22a6b3","general":"#004cff","meta":"#ff9900","lore":"#9b59b6","invalid":"#7f8c8d","parody":"#ff54a7","language":"#cc8800","category":"#00aaaa","pages":"#888888"}

def _sqlite_tag_groups_worker(settings, source="all", progress=None, stop_check=None):
    """Heavy GROUP BY for the Tags page; executed outside the UI thread."""
    if progress:
        progress("Загрузка списка тегов из SQLite…")
    if stop_check and stop_check():
        return None
    from core.services.library_service import tag_group_counts, counters
    groups = tag_group_counts(settings, source=source)
    _tag_counts, source_counts, _extra = counters(settings)
    return {"groups": groups, "sources": sorted(source_counts)}



class TagsPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main; self.items=[]; self.tag_counts={}; self.tag_groups=defaultdict(Counter); self.current_group="artist"; self._loaded=False; self._dirty=True; self._active_task=None; self._loaded_source=""
        lay=QVBoxLayout(self); self.split=QSplitter(Qt.Horizontal); lay.addWidget(self.split,1)

        left=QWidget(); ll=QVBoxLayout(left)
        left_head=QHBoxLayout(); self.all_title=QLabel(); left_head.addWidget(self.all_title,1); self.all_sort=QComboBox(); left_head.addWidget(self.all_sort,0,Qt.AlignRight); ll.addLayout(left_head)
        source_row=QHBoxLayout(); self.tag_source_label=QLabel("Источник:"); self.tag_source=QComboBox(); self.tag_source.addItem("Все источники", "all"); source_row.addWidget(self.tag_source_label); source_row.addWidget(self.tag_source, 1); ll.addLayout(source_row)
        self.all_search=QLineEdit(); self.all_search.setClearButtonEnabled(True); ll.addWidget(self.all_search); self.all_list=QListWidget(); ll.addWidget(self.all_list,1)

        right=QWidget(); rl=QVBoxLayout(right)
        top=QHBoxLayout(); self.group_title=QLabel(); top.addWidget(self.group_title,0,Qt.AlignLeft)
        self.group_order = list(self.main.settings.get("tag_group_order") or GROUP_ORDER)
        for _g in GROUP_ORDER:
            if _g not in self.group_order: self.group_order.append(_g)
        self.group_colors = dict(GROUP_COLORS); self.group_colors.update(self.main.settings.get("tag_group_colors") or {})
        self.group_buttons_layout = top
        self.group_buttons=QButtonGroup(self); self.group_buttons.setExclusive(True); self.group_btns={}
        for g in self.group_order:
            b=QPushButton(g); b.setCheckable(True); b.clicked.connect(lambda checked=False, group=g:self.set_group(group)); self.group_buttons.addButton(b); self.group_btns[g]=b; top.addWidget(b)
        top.addStretch(1); self.group_sort=QComboBox(); top.addWidget(self.group_sort,0,Qt.AlignRight); rl.addLayout(top)
        self.group_search=QLineEdit(); self.group_search.setClearButtonEnabled(True); rl.addWidget(self.group_search); self.group_list=QListWidget(); rl.addWidget(self.group_list,1)

        for _lst in (self.all_list, self.group_list):
            _lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            _lst.setTextElideMode(Qt.ElideRight)
            _lst.setContextMenuPolicy(Qt.CustomContextMenu)
            _lst.customContextMenuRequested.connect(lambda pos, lst=_lst: self._tag_color_context_menu(lst, pos))
        self.split.addWidget(left); self.split.addWidget(right); self.split.setSizes([520,620])
        self.loading_status=QLabel(""); self.loading_status.setVisible(False); lay.addWidget(self.loading_status)
        self.refresh_btn=QPushButton(); lay.addWidget(self.refresh_btn)
        for cb in [self.all_sort,self.group_sort]:
            for m in ["count_desc","count_asc","alpha","alpha_desc"]: cb.addItem(m,m)
        self.all_search.textChanged.connect(self.render_all); self.group_search.textChanged.connect(self.render_group); self.all_sort.currentIndexChanged.connect(self.render_all); self.group_sort.currentIndexChanged.connect(self.render_group); self.refresh_btn.clicked.connect(self.refresh_force); self.tag_source.currentIndexChanged.connect(self._source_changed)
        self.all_list.itemClicked.connect(lambda it:self.main.open_tag_single(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0])); self.all_list.itemDoubleClicked.connect(lambda it:self.main.open_tag_add(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0])); self.group_list.itemClicked.connect(lambda it:self.main.open_tag_single(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0])); self.group_list.itemDoubleClicked.connect(lambda it:self.main.open_tag_add(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0]))
        self.group_btns[self.current_group].setChecked(True); self.retranslate()
    def retranslate(self):
        t=self.main.t; self.refresh_btn.setText(t("Refresh")); self.all_title.setText(t("All tags")); self.group_title.setText(t("Group") + ":"); self.all_search.setPlaceholderText(t("Search tags")); self.group_search.setPlaceholderText(t("Search tags")); self.tag_source_label.setText("Источник тегов:")
        for cb in [self.all_sort,self.group_sort]:
            cur=cb.currentData(); cb.blockSignals(True); cb.clear()
            for m in ["count_desc","count_asc","alpha","alpha_desc"]: cb.addItem(t(m),m)
            i=cb.findData(cur or "count_desc"); cb.setCurrentIndex(i if i>=0 else 0); cb.blockSignals(False)
    def reload_category_configuration(self):
        self.group_order = list(self.main.settings.get("tag_group_order") or GROUP_ORDER)
        for g in GROUP_ORDER:
            if g not in self.group_order:
                self.group_order.append(g)
        self.group_colors = dict(GROUP_COLORS); self.group_colors.update(self.main.settings.get("tag_group_colors") or {})
        for button in list(self.group_btns.values()):
            self.group_buttons.removeButton(button)
            self.group_buttons_layout.removeWidget(button)
            button.deleteLater()
        self.group_btns = {}
        for g in self.group_order:
            b = QPushButton(g); b.setCheckable(True); b.clicked.connect(lambda checked=False, group=g: self.set_group(group))
            self.group_buttons.addButton(b); self.group_btns[g] = b; self.group_buttons_layout.addWidget(b)
        if self.current_group not in self.group_btns:
            self.current_group = self.group_order[0] if self.group_order else "general"
        if self.current_group in self.group_btns:
            self.group_btns[self.current_group].setChecked(True)
        self.refresh_force()

    def _source_changed(self):
        self._dirty = True
        self.refresh_force()

    def refresh(self):
        # Opening the Tags tab must be instant after its first load.  In SQLite
        # mode self.items is intentionally empty, so checking `not self.items`
        # rebuilt thousands of QListWidget rows on every visit.
        if not self._loaded or self._dirty:
            self.refresh_force()

    def invalidate(self):
        # Called when another page has changed tag/source data.  The existing
        # list remains usable until the user returns to Tags or presses Refresh.
        self._dirty = True

    def refresh_force(self):
        # A full tag GROUP BY became visible as a 1–2 second UI freeze at ~10k
        # found images.  Keep the cache, but rebuild it in TaskManager so the
        # interface remains responsive on the first open and explicit refresh.
        if self._active_task is not None:
            return
        if self.main.settings.get("use_sqlite_index", True):
            self.loading_status.setText("Загрузка тегов в фоне…")
            self.loading_status.setVisible(True)
            self.refresh_btn.setEnabled(False)
            selected_source = str(self.tag_source.currentData() or "all")
            self._requested_source = selected_source
            self._active_task = self.main.task_manager.submit(
                _sqlite_tag_groups_worker, dict(self.main.settings or {}), selected_source, name="tags-global-counts",
                on_progress=lambda message: self.loading_status.setText(str(message)),
                on_result=self._sqlite_groups_ready, on_error=self._sqlite_groups_error,
                on_finished=self._sqlite_groups_finished,
            )
            return
        self.items,self.tag_counts,_,_=scan_library(self.main.settings); self.rebuild_groups(); self._loaded=True; self._dirty=False; self.render_all(); self.render_group()

    def _sqlite_groups_ready(self, result):
        if result is None:
            return
        groups = result.get("groups", {}) if isinstance(result, dict) else result
        hosts = result.get("sources", []) if isinstance(result, dict) else []
        current = str(self.tag_source.currentData() or "all")
        self.tag_source.blockSignals(True)
        self.tag_source.clear(); self.tag_source.addItem("Все источники", "all")
        for host in hosts:
            self.tag_source.addItem(str(host), str(host))
        idx = self.tag_source.findData(current)
        self.tag_source.setCurrentIndex(idx if idx >= 0 else 0)
        self.tag_source.blockSignals(False)
        self.tag_groups = groups
        self.tag_counts = {}
        for _counter in self.tag_groups.values():
            for _tag, _count in _counter.items():
                self.tag_counts[_tag] = self.tag_counts.get(_tag, 0) + int(_count)
        self.items = []
        self._loaded_source = str(getattr(self, "_requested_source", "all"))
        self._loaded = True; self._dirty = (str(self.tag_source.currentData() or "all") != self._loaded_source)
        self.render_all(); self.render_group()

    def _sqlite_groups_error(self, error):
        self.loading_status.setText("Не удалось загрузить теги: " + str(error).splitlines()[-1])
        self.loading_status.setVisible(True)

    def _sqlite_groups_finished(self):
        self._active_task = None
        self.refresh_btn.setEnabled(True)
        if self._loaded:
            self.loading_status.setVisible(False)
        if self._dirty:
            QTimer.singleShot(0, self.refresh_force)
    def rebuild_groups(self):
        self.tag_groups=defaultdict(Counter)
        for item in self.items:
            groups=item.get("tag_groups") or {"general":item.get("tags",[])}
            for group,tags in groups.items():
                for tag in tags: self.tag_groups[group][normalize_tag(tag)] += 1
    def set_group(self,group): self.current_group=group; self.render_group()
    def _tag_color_context_menu(self, lst, point):
        item = lst.itemAt(point)
        if item is None:
            return
        tag = str(item.data(Qt.UserRole) or "").strip()
        if not tag:
            return
        menu = QMenu(self)
        set_color = menu.addAction("Выбрать цвет тега...")
        clear_color = menu.addAction("Сбросить цвет тега")
        action = menu.exec(lst.viewport().mapToGlobal(point))
        if action == set_color:
            selected = QColorDialog.getColor(QColor(item.foreground().color()), self, f"Цвет тега: {tag}")
            if selected.isValid():
                colors = dict(self.main.settings.get("tag_colors") or {})
                colors[normalize_tag(tag).lower()] = selected.name()
                self.main.settings["tag_colors"] = colors
                self.main.save_settings()
        elif action == clear_color:
            colors = dict(self.main.settings.get("tag_colors") or {})
            colors.pop(normalize_tag(tag).lower(), None)
            self.main.settings["tag_colors"] = colors
            self.main.save_settings()
        else:
            return
        self.render_all(); self.render_group()
        try: self.main.gallery_page._render_page_tags()
        except Exception: pass
        try:
            if self.main.stack.currentWidget() is self.main.post_page:
                self.main.post_page.render_tags(self.main.post_page.item())
        except Exception: pass

    def add_tag_item(self,lst,tag,count, group=None):
        # Не используем setItemWidget(QLabel): на больших списках это тяжело и
        # иногда подвешивает LB при кликах по групповым тегам.
        it = QListWidgetItem(f"{tag}    {count}")
        it.setData(Qt.UserRole, tag)
        g = group or self.find_tag_group(tag)
        colors = dict(GROUP_COLORS); colors.update(self.main.settings.get("tag_group_colors") or {})
        color = tag_display_color(tag, g, self.main.settings, colors)
        it.setToolTip(f"{g}: {tag}")
        it.setForeground(QBrush(QColor(color)))
        lst.addItem(it)

    def find_tag_group(self, tag):
        for g,counter in self.tag_groups.items():
            if tag in counter:
                return g
        return "general"
    def render_all(self):
        q=self.all_search.text().lower().strip(); mode=self.all_sort.currentData() or "count_desc"; self.all_list.clear()
        for tag,count in sort_tag_items(self.tag_counts.items(),mode):
            if q and q not in tag.lower(): continue
            self.add_tag_item(self.all_list,tag,count)
    def render_group(self):
        q=self.group_search.text().lower().strip(); mode=self.group_sort.currentData() or "count_desc"; self.group_list.clear()
        for tag,count in sort_tag_items(self.tag_groups.get(self.current_group,{}).items(),mode):
            if q and q not in tag.lower(): continue
            self.add_tag_item(self.group_list,tag,count,self.current_group)
