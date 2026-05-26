import json, time, subprocess, os
from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QListWidgetItem, QFileDialog, QMessageBox
from PySide6.QtCore import QTimer, Qt
from core.paths import DB_DIR

GAMES_DB = DB_DIR / "games.json"
EXE_EXTS = {".exe", ".bat", ".cmd", ".lnk"}


def load_db():
    try:
        if GAMES_DB.exists():
            d=json.loads(GAMES_DB.read_text(encoding='utf-8'))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def save_db(d):
    GAMES_DB.parent.mkdir(parents=True, exist_ok=True)
    GAMES_DB.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')

class GamesPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main; self.items=[]; self.proc=None; self.running_path=None; self.started_at=0
        lay=QVBoxLayout(self)
        top=QHBoxLayout()
        self.choose_btn=QPushButton('Выбрать папку игр')
        self.refresh_btn=QPushButton('Обновить')
        self.launch_btn=QPushButton('Запустить')
        self.stop_btn=QPushButton('Стоп таймер')
        top.addWidget(self.choose_btn); top.addWidget(self.refresh_btn); top.addWidget(self.launch_btn); top.addWidget(self.stop_btn); top.addStretch(1)
        lay.addLayout(top)
        self.info=QLabel('Папка игр не выбрана')
        self.info.setWordWrap(True); lay.addWidget(self.info)
        self.list=QListWidget(); lay.addWidget(self.list,1)
        self.choose_btn.clicked.connect(self.choose_root); self.refresh_btn.clicked.connect(self.refresh); self.launch_btn.clicked.connect(self.launch_selected); self.stop_btn.clicked.connect(self.stop_timer)
        self.timer=QTimer(self); self.timer.timeout.connect(self.poll_proc); self.timer.start(3000)
    def retranslate(self): pass
    def choose_root(self):
        f=QFileDialog.getExistingDirectory(self,'Выбрать папку игр',self.main.settings.get('games_root') or self.main.settings.get('root',''))
        if f:
            self.main.settings['games_root']=f; self.main.save_settings(); self.refresh()
    def refresh(self):
        root=Path(self.main.settings.get('games_root',''))
        self.items=[]; self.list.clear()
        if not root.exists():
            self.info.setText('Папка игр не выбрана или не существует'); return
        db=load_db()
        for p in sorted(root.rglob('*')):
            if p.is_file() and p.suffix.lower() in EXE_EXTS:
                rec=db.get(str(p.resolve()), {})
                runs=int(rec.get('runs',0)); secs=float(rec.get('seconds',0))
                self.items.append(p)
                h=int(secs//3600); m=int((secs%3600)//60)
                li=QListWidgetItem(f'{p.name}    запусков: {runs}    время: {h}ч {m}м')
                li.setToolTip(str(p)); self.list.addItem(li)
        self.info.setText(f'Папка: {root}\nНайдено игр/запускалок: {len(self.items)}')
    def launch_selected(self):
        row=self.list.currentRow()
        if row<0 or row>=len(self.items): return
        p=self.items[row]
        try:
            db=load_db(); key=str(p.resolve()); rec=db.get(key,{})
            rec['runs']=int(rec.get('runs',0))+1; rec['last_start']=time.time(); db[key]=rec; save_db(db)
            self.running_path=p; self.started_at=time.time()
            self.proc=subprocess.Popen([str(p)], cwd=str(p.parent), shell=False)
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self,'Ошибка запуска',str(e))
    def poll_proc(self):
        if self.proc is not None and self.proc.poll() is not None:
            self.stop_timer()
    def stop_timer(self):
        if not self.running_path or not self.started_at: return
        db=load_db(); key=str(self.running_path.resolve()); rec=db.get(key,{})
        rec['seconds']=float(rec.get('seconds',0))+max(0,time.time()-self.started_at); rec['last_stop']=time.time(); db[key]=rec; save_db(db)
        self.proc=None; self.running_path=None; self.started_at=0; self.refresh()
