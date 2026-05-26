from collections import Counter, defaultdict
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QPushButton,QLabel,QListWidget,QComboBox,QSplitter,QButtonGroup,QListWidgetItem
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from core.library import scan_library, sort_tag_items, normalize_tag

GROUP_ORDER=["artist","character","copyright","general","meta","parody","language","category","pages"]
GROUP_COLORS={"artist":"#ff3838","character":"#00a000","copyright":"#ff54a7","general":"#004cff","meta":"#ff9900","parody":"#ff54a7","language":"#cc8800","category":"#00aaaa","pages":"#888888"}

class TagsPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main; self.items=[]; self.tag_counts={}; self.tag_groups=defaultdict(Counter); self.current_group="artist"
        lay=QVBoxLayout(self); self.split=QSplitter(Qt.Horizontal); lay.addWidget(self.split,1)

        left=QWidget(); ll=QVBoxLayout(left)
        left_head=QHBoxLayout(); self.all_title=QLabel(); left_head.addWidget(self.all_title,1); self.all_sort=QComboBox(); left_head.addWidget(self.all_sort,0,Qt.AlignRight); ll.addLayout(left_head)
        self.all_search=QLineEdit(); self.all_search.setClearButtonEnabled(True); ll.addWidget(self.all_search); self.all_list=QListWidget(); ll.addWidget(self.all_list,1)

        right=QWidget(); rl=QVBoxLayout(right)
        top=QHBoxLayout(); self.group_title=QLabel(); top.addWidget(self.group_title,0,Qt.AlignLeft)
        self.group_buttons=QButtonGroup(self); self.group_buttons.setExclusive(True); self.group_btns={}
        for g in GROUP_ORDER:
            b=QPushButton(g); b.setCheckable(True); b.clicked.connect(lambda checked=False, group=g:self.set_group(group)); self.group_buttons.addButton(b); self.group_btns[g]=b; top.addWidget(b)
        top.addStretch(1); self.group_sort=QComboBox(); top.addWidget(self.group_sort,0,Qt.AlignRight); rl.addLayout(top)
        self.group_search=QLineEdit(); self.group_search.setClearButtonEnabled(True); rl.addWidget(self.group_search); self.group_list=QListWidget(); rl.addWidget(self.group_list,1)

        self.split.addWidget(left); self.split.addWidget(right); self.split.setSizes([520,620])
        self.refresh_btn=QPushButton(); lay.addWidget(self.refresh_btn)
        for cb in [self.all_sort,self.group_sort]:
            for m in ["count_desc","count_asc","alpha","alpha_desc"]: cb.addItem(m,m)
        self.all_search.textChanged.connect(self.render_all); self.group_search.textChanged.connect(self.render_group); self.all_sort.currentIndexChanged.connect(self.render_all); self.group_sort.currentIndexChanged.connect(self.render_group); self.refresh_btn.clicked.connect(self.refresh_force)
        self.all_list.itemClicked.connect(lambda it:self.main.open_tag_single(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0])); self.all_list.itemDoubleClicked.connect(lambda it:self.main.open_tag_add(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0])); self.group_list.itemClicked.connect(lambda it:self.main.open_tag_single(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0])); self.group_list.itemDoubleClicked.connect(lambda it:self.main.open_tag_add(it.data(Qt.UserRole) or it.text().rsplit("    ",1)[0]))
        self.group_btns[self.current_group].setChecked(True); self.retranslate()
    def _load_siblings(self):
        try:
            from core.vptree import get_all_siblings
            from core.database.connection import get_connection
            conn = get_connection(self.main.settings)
            pairs = get_all_siblings(conn)
            self.sib_table.setRowCount(0)
            for tag, canon in pairs:
                r = self.sib_table.rowCount()
                self.sib_table.insertRow(r)
                self.sib_table.setItem(r, 0, QTableWidgetItem(tag))
                self.sib_table.setItem(r, 1, QTableWidgetItem(canon))
        except Exception as e:
            pass

    def _add_sibling(self):
        tag = self.sib_tag.text().strip().lower()
        canon = self.sib_canon.text().strip().lower()
        if not tag or not canon:
            return
        try:
            from core.vptree import add_sibling
            from core.database.connection import get_connection
            conn = get_connection(self.main.settings)
            add_sibling(conn, tag, canon)
            self.sib_tag.clear(); self.sib_canon.clear()
            self._load_siblings()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Ошибка", str(e))

    def _del_sibling(self):
        row = self.sib_table.currentRow()
        if row < 0:
            return
        tag_it = self.sib_table.item(row, 0)
        if not tag_it:
            return
        try:
            from core.vptree import remove_sibling
            from core.database.connection import get_connection
            conn = get_connection(self.main.settings)
            remove_sibling(conn, tag_it.text())
            self._load_siblings()
        except Exception as e:
            pass

    def retranslate(self):
        t=self.main.t; self.refresh_btn.setText(t("Refresh")); self.all_title.setText(t("All tags")); self.group_title.setText(t("Group") + ":"); self.all_search.setPlaceholderText(t("Search tags")); self.group_search.setPlaceholderText(t("Search tags"))
        for cb in [self.all_sort,self.group_sort]:
            cur=cb.currentData(); cb.blockSignals(True); cb.clear()
            for m in ["count_desc","count_asc","alpha","alpha_desc"]: cb.addItem(t(m),m)
            i=cb.findData(cur or "count_desc"); cb.setCurrentIndex(i if i>=0 else 0); cb.blockSignals(False)
    def refresh(self):
        if not self.items: self.refresh_force()
    def refresh_force(self):
        # SQLite-main path: load tag counters grouped by category directly from DB.
        # Do not load all images just to rebuild tag groups; on 100k+ files that is slow
        # and, with load_details=False, all groups collapse into general/empty.
        try:
            if self.main.settings.get("use_sqlite_index", True):
                from core.database.repository import counts, tag_group_counts
                self.tag_counts, _, _ = counts(self.main.settings)
                self.tag_groups = tag_group_counts(self.main.settings)
                self.items = []
                self.render_all(); self.render_group()
                return
        except Exception as e:
            try:
                print("TAGS SQLITE FALLBACK:", e)
            except Exception:
                pass
        self.items,self.tag_counts,_,_=scan_library(self.main.settings); self.rebuild_groups(); self.render_all(); self.render_group()
    def rebuild_groups(self):
        self.tag_groups=defaultdict(Counter)
        for item in self.items:
            groups=item.get("tag_groups") or {"general":item.get("tags",[])}
            for group,tags in groups.items():
                for tag in tags: self.tag_groups[group][normalize_tag(tag)] += 1
    def set_group(self,group): self.current_group=group; self.render_group()
    def add_tag_item(self,lst,tag,count, group=None):
        # Не используем setItemWidget(QLabel): на больших списках это тяжело и
        # иногда подвешивает LB при кликах по групповым тегам.
        it = QListWidgetItem(f"{tag}    {count}")
        it.setData(Qt.UserRole, tag)
        g = group or self.find_tag_group(tag)
        color = GROUP_COLORS.get(g, GROUP_COLORS.get("general", "#f5f5f5"))
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
