_FONT = '"Segoe UI Variable", "Segoe UI", "Inter", sans-serif'



# ── ABYSS ─────────────────────────────────────────────────────────────────────
ABYSS = f"""
QMainWindow,QWidget{{background:#0d0f16;color:#c0c8e0;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#0d0f16;}}
QSplitter > QWidget{{background:#0d0f16;}}
QSplitter::handle{{background:#1a1d28;width:1px;height:1px;}}
QStackedWidget{{background:#0d0f16;}}
#SettingsInner{{background:#0d0f16;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#0d0f16;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#0d0f16;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#2a2f4a;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#0d0f16;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#2a2f4a;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#12151f;border:1px solid #222640;border-radius:8px;padding:6px 10px;color:#c0c8e0;selection-background-color:#5a3fa8;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#7060c0;}}
QSpinBox,QDoubleSpinBox{{background:#12151f;border:1px solid #222640;border-radius:8px;padding:5px 8px;color:#c0c8e0;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#12151f;border:1px solid #222640;border-radius:8px;padding:5px 10px;color:#c0c8e0;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#12151f;border:1px solid #2a2f4a;color:#c0c8e0;selection-background-color:#5a3fa8;border-radius:6px;padding:2px;}}
QPushButton{{background:#151824;border:1px solid #222640;border-radius:8px;padding:7px 16px;color:#a0accc;font-weight:600;}}
QPushButton:hover{{background:#1c2035;border-color:#5a4a90;color:#d0d8f0;}}
QPushButton:pressed{{background:#151824;border-color:#5a4a90;}}
QPushButton:checked{{background:#3a2880;border-color:#7060c0;color:#c0b0ff;}}
QPushButton:disabled{{color:#3a4060;border-color:#1a1d28;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#5a6080;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(100,90,180,0.12);color:#c0c8e0;border-left:3px solid rgba(110,90,200,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(100,90,180,0.08);}}
QPushButton#NavBtn:checked{{background:rgba(110,90,200,0.15);color:#a898f8;border-left:3px solid #7060c0;font-weight:700;}}
QPushButton#PostCtrl{{background:#0f1220;border:1px solid #1e2235;border-radius:8px;padding:0 10px;color:#6070a0;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#181c30;color:#a0b0d0;border-color:#303858;}}
QPushButton#PostCtrl:pressed{{background:#0f1220;}}
QPushButton#ModeBtn{{background:#0f1220;border:1px solid #1e2235;border-radius:8px;padding:6px 12px;color:#5a6080;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#141828;color:#c0c8e0;border-color:#2e3050;}}
QListWidget{{background:#0f1220;border:1px solid #1e2235;border-radius:8px;color:#a0accc;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(100,90,180,0.1);}}
QListWidget::item:selected{{background:#3a2880;color:#c0b0ff;}}
QTableWidget,QTableView{{background:#0f1220;gridline-color:#1e2235;border:1px solid #1e2235;border-radius:8px;color:#c0c8e0;selection-background-color:#3a2880;}}
QHeaderView::section{{background:#0d0f16;color:#5a6080;border:none;border-bottom:1px solid #1e2235;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #1e2235;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#5a6080;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#a0accc;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #2a2f4a;border-radius:5px;background:#12151f;}}
QCheckBox::indicator:checked{{background:#6050c0;border-color:#6050c0;}}
QProgressBar{{background:#12151f;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #5040a0,stop:1 #8060e0);border-radius:3px;}}
QToolTip{{background:#1c2035;color:#c0c8e0;border:1px solid #2a2f4a;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#c0c8e0;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#9080e0;background:transparent;border:none;}}
#Sidebar{{background:#090b12;border-right:1px solid #181a26;}}
#TopBar{{background:#0b0d14;border-bottom:1px solid #181a26;}}
#PostCtrlBar{{background:#090b12;border-top:1px solid #181a26;}}
QMenu{{background:#12151f;border:1px solid #2a2f4a;color:#c0c8e0;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#3a2880;color:#c0b0ff;}}
QMenu::separator{{height:1px;background:#2a2f4a;margin:3px 8px;}}
"""

# ── EMBER ─────────────────────────────────────────────────────────────────────
EMBER = f"""
QMainWindow,QWidget{{background:#100e08;color:#d8c8a0;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#14141e;}}
QSplitter > QWidget{{background:#14141e;}}
QSplitter::handle{{background:#1e1a10;width:1px;height:1px;}}
QStackedWidget{{background:#14141e;}}
#SettingsInner{{background:#14141e;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#14141e;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#100e08;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#3a3018;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#100e08;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#3a3018;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#181408;border:1px solid #2e2818;border-radius:8px;padding:6px 10px;color:#d8c8a0;selection-background-color:#8a6010;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#c07820;}}
QSpinBox,QDoubleSpinBox{{background:#181408;border:1px solid #2e2818;border-radius:8px;padding:5px 8px;color:#d8c8a0;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#181408;border:1px solid #2e2818;border-radius:8px;padding:5px 10px;color:#d8c8a0;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#181408;border:1px solid #3a3018;color:#d8c8a0;selection-background-color:#8a6010;border-radius:6px;padding:2px;}}
QPushButton{{background:#1c1810;border:1px solid #2e2818;border-radius:8px;padding:7px 16px;color:#b09870;font-weight:600;}}
QPushButton:hover{{background:#252010;border-color:#a07020;color:#e0c880;}}
QPushButton:pressed{{background:#1c1810;}}
QPushButton:checked{{background:#6a4808;border-color:#c07820;color:#ffd060;}}
QPushButton:disabled{{color:#4a4020;border-color:#201c10;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#6a5830;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(180,120,20,0.1);color:#d8c8a0;border-left:3px solid rgba(180,120,20,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(180,120,20,0.07);}}
QPushButton#NavBtn:checked{{background:rgba(180,120,20,0.15);color:#ffa020;border-left:3px solid #c07820;font-weight:700;}}
QPushButton#PostCtrl{{background:#141008;border:1px solid #2a2410;border-radius:8px;padding:0 10px;color:#7a6840;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#201a08;color:#c0a860;border-color:#3a3018;}}
QPushButton#PostCtrl:pressed{{background:#141008;}}
QPushButton#ModeBtn{{background:#141008;border:1px solid #2a2410;border-radius:8px;padding:6px 12px;color:#6a5830;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#1a1608;color:#d8c8a0;}}
QListWidget{{background:#141008;border:1px solid #2a2410;border-radius:8px;color:#b09870;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(180,120,20,0.1);}}
QListWidget::item:selected{{background:#6a4808;color:#ffd060;}}
QTableWidget,QTableView{{background:#141008;gridline-color:#2a2410;border:1px solid #2a2410;border-radius:8px;color:#d8c8a0;selection-background-color:#6a4808;}}
QHeaderView::section{{background:#100e08;color:#6a5830;border:none;border-bottom:1px solid #2a2410;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #2a2410;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#6a5830;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#b09870;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #3a3018;border-radius:5px;background:#181408;}}
QCheckBox::indicator:checked{{background:#c07820;border-color:#c07820;}}
QProgressBar{{background:#181408;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8a5808,stop:1 #e09030);border-radius:3px;}}
QToolTip{{background:#1c1810;color:#d8c8a0;border:1px solid #3a3018;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#d8c8a0;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#ffa020;background:transparent;border:none;}}
#Sidebar{{background:#0c0a06;border-right:1px solid #1e1a10;}}
#TopBar{{background:#100e08;border-bottom:1px solid #1e1a10;}}
#PostCtrlBar{{background:#0c0a06;border-top:1px solid #1e1a10;}}
QMenu{{background:#181408;border:1px solid #3a3018;color:#d8c8a0;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#6a4808;color:#ffd060;}}
"""

# ── SLATE ─────────────────────────────────────────────────────────────────────
SLATE = f"""
QMainWindow,QWidget{{background:#1e2028;color:#c8ccd8;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#16181e;}}
QSplitter > QWidget{{background:#16181e;}}
QSplitter::handle{{background:#2a2e3a;width:1px;height:1px;}}
QStackedWidget{{background:#16181e;}}
#SettingsInner{{background:#16181e;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#16181e;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#1e2028;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#3a3e50;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#1e2028;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#3a3e50;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:6px 10px;color:#c8ccd8;selection-background-color:#3a6090;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#5080b0;}}
QSpinBox,QDoubleSpinBox{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:5px 8px;color:#c8ccd8;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:5px 10px;color:#c8ccd8;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#262a36;border:1px solid #3a3e50;color:#c8ccd8;selection-background-color:#3a6090;border-radius:6px;padding:2px;}}
QPushButton{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:7px 16px;color:#9098b0;font-weight:600;}}
QPushButton:hover{{background:#2e3244;border-color:#5080b0;color:#d0d4e8;}}
QPushButton:pressed{{background:#262a36;}}
QPushButton:checked{{background:#284870;border-color:#5080b0;color:#80b0e0;}}
QPushButton:disabled{{color:#484c60;border-color:#282c38;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#585c70;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(80,128,180,0.1);color:#c8ccd8;border-left:3px solid rgba(80,128,180,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(80,128,180,0.07);}}
QPushButton#NavBtn:checked{{background:rgba(80,128,180,0.15);color:#80b0e0;border-left:3px solid #4a80b8;font-weight:700;}}
QPushButton#PostCtrl{{background:#1e2028;border:1px solid #2a2e3a;border-radius:8px;padding:0 10px;color:#686870;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#262a36;color:#a0a8c0;border-color:#3a3e50;}}
QPushButton#PostCtrl:pressed{{background:#1e2028;}}
QPushButton#ModeBtn{{background:#1e2028;border:1px solid #2a2e3a;border-radius:8px;padding:6px 12px;color:#585c70;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#242830;color:#c8ccd8;}}
QListWidget{{background:#1e2028;border:1px solid #2a2e3a;border-radius:8px;color:#9098b0;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(80,128,180,0.1);}}
QListWidget::item:selected{{background:#284870;color:#80b0e0;}}
QTableWidget,QTableView{{background:#1e2028;gridline-color:#2a2e3a;border:1px solid #2a2e3a;border-radius:8px;color:#c8ccd8;selection-background-color:#284870;}}
QHeaderView::section{{background:#1a1e26;color:#585c70;border:none;border-bottom:1px solid #2a2e3a;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #2a2e3a;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#585c70;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#9098b0;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #3a3e50;border-radius:5px;background:#262a36;}}
QCheckBox::indicator:checked{{background:#4a80b8;border-color:#4a80b8;}}
QProgressBar{{background:#262a36;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3a6090,stop:1 #60a0d8);border-radius:3px;}}
QToolTip{{background:#262a36;color:#c8ccd8;border:1px solid #3a3e50;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#c8ccd8;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#80b0e0;background:transparent;border:none;}}
#Sidebar{{background:#191c24;border-right:1px solid #2a2e3a;}}
#TopBar{{background:#1e2028;border-bottom:1px solid #2a2e3a;}}
#PostCtrlBar{{background:#191c24;border-top:1px solid #2a2e3a;}}
QMenu{{background:#262a36;border:1px solid #3a3e50;color:#c8ccd8;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#284870;color:#80b0e0;}}
"""

# ── SAKURA ────────────────────────────────────────────────────────────────────
SAKURA = f"""
QMainWindow,QWidget{{background:#110a12;color:#e0c8d8;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#140820;}}
QSplitter > QWidget{{background:#140820;}}
QSplitter::handle{{background:#1e1020;width:1px;height:1px;}}
QStackedWidget{{background:#140820;}}
#SettingsInner{{background:#140820;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#140820;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#110a12;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#38202e;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#110a12;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#38202e;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#180f1a;border:1px solid #281828;border-radius:8px;padding:6px 10px;color:#e0c8d8;selection-background-color:#802050;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#c04080;}}
QSpinBox,QDoubleSpinBox{{background:#180f1a;border:1px solid #281828;border-radius:8px;padding:5px 8px;color:#e0c8d8;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#180f1a;border:1px solid #281828;border-radius:8px;padding:5px 10px;color:#e0c8d8;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#180f1a;border:1px solid #38202e;color:#e0c8d8;selection-background-color:#802050;border-radius:6px;padding:2px;}}
QPushButton{{background:#1c1020;border:1px solid #281828;border-radius:8px;padding:7px 16px;color:#c090a8;font-weight:600;}}
QPushButton:hover{{background:#251528;border-color:#c04080;color:#f0d0e0;}}
QPushButton:pressed{{background:#1c1020;}}
QPushButton:checked{{background:#601840;border-color:#c04080;color:#ffb0d0;}}
QPushButton:disabled{{color:#4a283a;border-color:#1e1020;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#604858;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(192,64,128,0.1);color:#e0c8d8;border-left:3px solid rgba(192,64,128,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(192,64,128,0.07);}}
QPushButton#NavBtn:checked{{background:rgba(192,64,128,0.15);color:#ff90c0;border-left:3px solid #c04080;font-weight:700;}}
QPushButton#PostCtrl{{background:#130a14;border:1px solid #241428;border-radius:8px;padding:0 10px;color:#7a5068;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#1c1020;color:#c090a8;border-color:#381828;}}
QPushButton#PostCtrl:pressed{{background:#130a14;}}
QPushButton#ModeBtn{{background:#130a14;border:1px solid #241428;border-radius:8px;padding:6px 12px;color:#604858;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#180f1a;color:#e0c8d8;}}
QListWidget{{background:#130a14;border:1px solid #241428;border-radius:8px;color:#c090a8;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(192,64,128,0.1);}}
QListWidget::item:selected{{background:#601840;color:#ffb0d0;}}
QTableWidget,QTableView{{background:#130a14;gridline-color:#241428;border:1px solid #241428;border-radius:8px;color:#e0c8d8;selection-background-color:#601840;}}
QHeaderView::section{{background:#110a12;color:#604858;border:none;border-bottom:1px solid #241428;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #241428;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#604858;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#c090a8;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #38202e;border-radius:5px;background:#180f1a;}}
QCheckBox::indicator:checked{{background:#c04080;border-color:#c04080;}}
QProgressBar{{background:#180f1a;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #902040,stop:1 #e060a0);border-radius:3px;}}
QToolTip{{background:#1c1020;color:#e0c8d8;border:1px solid #38202e;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#e0c8d8;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#ff90c0;background:transparent;border:none;}}
#Sidebar{{background:#0d0810;border-right:1px solid #1e1020;}}
#TopBar{{background:#110a12;border-bottom:1px solid #1e1020;}}
#PostCtrlBar{{background:#0d0810;border-top:1px solid #1e1020;}}
QMenu{{background:#180f1a;border:1px solid #38202e;color:#e0c8d8;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#601840;color:#ffb0d0;}}
"""

# ── PH ────────────────────────────────────────────────────────────────────────

# ── R34 ──────────────────────────────────────────────────────────────────────
R34 = f"""
QMainWindow,QWidget{{background:#a8d99f;color:#111111;font-family:{_FONT};font-size:13px;}}
QSplitter,QStackedWidget{{background:#a8d99f;}}
QWidget#Sidebar{{background:#9fd191;border-right:1px solid #6da36b;}}
QPushButton,QComboBox,QLineEdit,QPlainTextEdit,QTextEdit,QSpinBox,QDoubleSpinBox{{
background:#b7e2af;
color:#111111;
border:1px solid #6da36b;
padding:4px;
}}
QPushButton:hover{{background:#8cc57d;}}
QTreeWidget,QTableWidget,QListWidget{{
background:#b7e2af;
alternate-background-color:#a8d99f;
color:#111111;
border:1px solid #6da36b;
}}
QTableWidget::item{{color:#111111;}}
QTableWidget::item:selected{{color:#111111;background:#8cc57d;}}
QHeaderView::section{{
background:#8cc57d;
color:#111111;
border:1px solid #6da36b;
}}
QCheckBox,QLabel{{color:#111111;}}
a{{color:#0033cc;}}
"""
PH = f"""
QMainWindow,QWidget{{background:#0f0f0f;color:#f0f0f0;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#0a0000;}}
QSplitter > QWidget{{background:#0a0000;}}
QSplitter::handle{{background:#1a1a1a;width:1px;height:1px;}}
QStackedWidget{{background:#0a0000;}}
#SettingsInner{{background:#0a0000;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#0a0000;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#0f0f0f;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#303030;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#0f0f0f;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#303030;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:6px 10px;color:#f0f0f0;selection-background-color:#c07000;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#ff9000;}}
QSpinBox,QDoubleSpinBox{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:5px 8px;color:#f0f0f0;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:5px 10px;color:#f0f0f0;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#1a1a1a;border:1px solid #303030;color:#f0f0f0;selection-background-color:#c07000;border-radius:6px;padding:2px;}}
QPushButton{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:7px 16px;color:#c0c0c0;font-weight:600;}}
QPushButton:hover{{background:#222222;border-color:#ff9000;color:#ffffff;}}
QPushButton:pressed{{background:#1a1a1a;}}
QPushButton:checked{{background:#7a4800;border-color:#ff9000;color:#ffb040;}}
QPushButton:disabled{{color:#404040;border-color:#1a1a1a;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#606060;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(255,144,0,0.08);color:#f0f0f0;border-left:3px solid rgba(255,144,0,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(255,144,0,0.05);}}
QPushButton#NavBtn:checked{{background:rgba(255,144,0,0.12);color:#ff9000;border-left:3px solid #ff9000;font-weight:700;}}
QPushButton#PostCtrl{{background:#111111;border:1px solid #222222;border-radius:8px;padding:0 10px;color:#606060;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#181818;color:#c0c0c0;border-color:#303030;}}
QPushButton#PostCtrl:pressed{{background:#111111;}}
QPushButton#ModeBtn{{background:#111111;border:1px solid #222222;border-radius:8px;padding:6px 12px;color:#606060;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#141414;color:#f0f0f0;}}
QListWidget{{background:#111111;border:1px solid #222222;border-radius:8px;color:#c0c0c0;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(255,144,0,0.08);}}
QListWidget::item:selected{{background:#7a4800;color:#ffb040;}}
QTableWidget,QTableView{{background:#111111;gridline-color:#222222;border:1px solid #222222;border-radius:8px;color:#f0f0f0;selection-background-color:#7a4800;}}
QHeaderView::section{{background:#0f0f0f;color:#606060;border:none;border-bottom:1px solid #222222;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #222222;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#606060;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#c0c0c0;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #303030;border-radius:5px;background:#1a1a1a;}}
QCheckBox::indicator:checked{{background:#ff9000;border-color:#ff9000;}}
QProgressBar{{background:#1a1a1a;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #c07000,stop:1 #ff9000);border-radius:3px;}}
QToolTip{{background:#1a1a1a;color:#f0f0f0;border:1px solid #303030;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#f0f0f0;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#ff9000;background:transparent;border:none;}}
#Sidebar{{background:#080808;border-right:1px solid #1a1a1a;}}
#TopBar{{background:#0f0f0f;border-bottom:1px solid #1a1a1a;}}
#PostCtrlBar{{background:#080808;border-top:1px solid #1a1a1a;}}
QMenu{{background:#1a1a1a;border:1px solid #303030;color:#f0f0f0;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#7a4800;color:#ffb040;}}
"""

# ── LIGHT ─────────────────────────────────────────────────────────────────────
LIGHT = f"""
QMainWindow,QWidget{{background:#f4f5f8;color:#1a1c2a;font-family:{_FONT};font-size:13px;}}
QSplitter::handle{{background:#d8daec;width:1px;height:1px;}}
QScrollArea{{border:none;background:#f4f5f8;}}
#thumb_placeholder{{background:#707070;}}
QLabel#thumb_placeholder{{background:#707070;}}
QAbstractScrollArea{{background:#f4f5f8;}}
QAbstractScrollArea>QWidget{{background:#f4f5f8;border:none;}}
QAbstractScrollArea>QWidget>QWidget{{background:#f4f5f8;border:none;}}
QFrame{{border:none;}}
QScrollBar:vertical{{background:#eceef8;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#b8bcd4;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#eceef8;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#b8bcd4;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:6px 10px;color:#1a1c2a;selection-background-color:#a090e0;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#6050c0;}}
QSpinBox,QDoubleSpinBox{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:5px 8px;color:#1a1c2a;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:5px 10px;color:#1a1c2a;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#ffffff;border:1px solid #c8cce0;color:#1a1c2a;selection-background-color:#a090e0;border-radius:6px;padding:2px;}}
QPushButton{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:7px 16px;color:#3a3c60;font-weight:600;}}
QPushButton:hover{{background:#eceeff;border-color:#8070d0;color:#1a1c2a;}}
QPushButton:pressed{{background:#ffffff;}}
QPushButton:checked{{background:#d8d4f8;border-color:#6050c0;color:#4030a0;}}
QPushButton:disabled{{color:#a0a4c0;border-color:#e0e2f0;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#8088a8;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(96,80,192,0.08);color:#1a1c2a;border-left:3px solid rgba(96,80,192,0.3);}}
QPushButton#NavBtn:pressed{{background:rgba(96,80,192,0.05);}}
QPushButton#NavBtn:checked{{background:rgba(96,80,192,0.1);color:#4030a0;border-left:3px solid #6050c0;font-weight:700;}}
QPushButton#PostCtrl{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:0 10px;color:#6068a0;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#eceeff;color:#1a1c2a;border-color:#8070d0;}}
QPushButton#PostCtrl:pressed{{background:#ffffff;}}
QPushButton#ModeBtn{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:6px 12px;color:#8088a8;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#eceeff;color:#1a1c2a;}}
QListWidget{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;color:#3a3c60;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(96,80,192,0.06);}}
QListWidget::item:selected{{background:#d8d4f8;color:#4030a0;}}
QTableWidget,QTableView{{background:#ffffff;gridline-color:#e0e2f0;border:1px solid #c8cce0;border-radius:8px;color:#1a1c2a;selection-background-color:#d8d4f8;}}
QHeaderView::section{{background:#f4f5f8;color:#8088a8;border:none;border-bottom:1px solid #c8cce0;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #c8cce0;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#8088a8;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#3a3c60;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #b8bcd4;border-radius:5px;background:#ffffff;}}
QCheckBox::indicator:checked{{background:#6050c0;border-color:#6050c0;}}
QProgressBar{{background:#e0e2f0;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6050c0,stop:1 #9080e0);border-radius:3px;}}
QToolTip{{background:#ffffff;color:#1a1c2a;border:1px solid #c8cce0;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#1a1c2a;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#4030a0;background:transparent;border:none;}}
#Sidebar{{background:#eceef8;border-right:1px solid #c8cce0;}}
#TopBar{{background:#f4f5f8;border-bottom:1px solid #c8cce0;}}
#PostCtrlBar{{background:#eceef8;border-top:1px solid #c8cce0;}}
QMenu{{background:#ffffff;border:1px solid #c8cce0;color:#1a1c2a;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#d8d4f8;color:#4030a0;}}
"""

THEMES = {
    "dark": ABYSS, "abyss": ABYSS,
    "ember": EMBER,
    "slate": SLATE, "gray": SLATE,
    "sakura": SAKURA,
    "r34": R34,
    "ph": PH,
    "ph": PH,
    "light": LIGHT,
}

def stylesheet_for(name: str) -> str:
    return THEMES.get(name, ABYSS)

_FONT = '"Segoe UI Variable", "Segoe UI", "Inter", sans-serif'



# ── ABYSS ─────────────────────────────────────────────────────────────────────
ABYSS = f"""
QMainWindow,QWidget{{background:#0d0f16;color:#c0c8e0;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#0d0f16;}}
QSplitter > QWidget{{background:#0d0f16;}}
QSplitter::handle{{background:#1a1d28;width:1px;height:1px;}}
QStackedWidget{{background:#0d0f16;}}
#SettingsInner{{background:#0d0f16;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#0d0f16;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#0d0f16;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#2a2f4a;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#0d0f16;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#2a2f4a;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#12151f;border:1px solid #222640;border-radius:8px;padding:6px 10px;color:#c0c8e0;selection-background-color:#5a3fa8;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#7060c0;}}
QSpinBox,QDoubleSpinBox{{background:#12151f;border:1px solid #222640;border-radius:8px;padding:5px 8px;color:#c0c8e0;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#12151f;border:1px solid #222640;border-radius:8px;padding:5px 10px;color:#c0c8e0;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#12151f;border:1px solid #2a2f4a;color:#c0c8e0;selection-background-color:#5a3fa8;border-radius:6px;padding:2px;}}
QPushButton{{background:#151824;border:1px solid #222640;border-radius:8px;padding:7px 16px;color:#a0accc;font-weight:600;}}
QPushButton:hover{{background:#1c2035;border-color:#5a4a90;color:#d0d8f0;}}
QPushButton:pressed{{background:#151824;border-color:#5a4a90;}}
QPushButton:checked{{background:#3a2880;border-color:#7060c0;color:#c0b0ff;}}
QPushButton:disabled{{color:#3a4060;border-color:#1a1d28;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#5a6080;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(100,90,180,0.12);color:#c0c8e0;border-left:3px solid rgba(110,90,200,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(100,90,180,0.08);}}
QPushButton#NavBtn:checked{{background:rgba(110,90,200,0.15);color:#a898f8;border-left:3px solid #7060c0;font-weight:700;}}
QPushButton#PostCtrl{{background:#0f1220;border:1px solid #1e2235;border-radius:8px;padding:0 10px;color:#6070a0;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#181c30;color:#a0b0d0;border-color:#303858;}}
QPushButton#PostCtrl:pressed{{background:#0f1220;}}
QPushButton#ModeBtn{{background:#0f1220;border:1px solid #1e2235;border-radius:8px;padding:6px 12px;color:#5a6080;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#141828;color:#c0c8e0;border-color:#2e3050;}}
QListWidget{{background:#0f1220;border:1px solid #1e2235;border-radius:8px;color:#a0accc;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(100,90,180,0.1);}}
QListWidget::item:selected{{background:#3a2880;color:#c0b0ff;}}
QTableWidget,QTableView{{background:#0f1220;gridline-color:#1e2235;border:1px solid #1e2235;border-radius:8px;color:#c0c8e0;selection-background-color:#3a2880;}}
QHeaderView::section{{background:#0d0f16;color:#5a6080;border:none;border-bottom:1px solid #1e2235;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #1e2235;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#5a6080;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#a0accc;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #2a2f4a;border-radius:5px;background:#12151f;}}
QCheckBox::indicator:checked{{background:#6050c0;border-color:#6050c0;}}
QProgressBar{{background:#12151f;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #5040a0,stop:1 #8060e0);border-radius:3px;}}
QToolTip{{background:#1c2035;color:#c0c8e0;border:1px solid #2a2f4a;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#c0c8e0;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#9080e0;background:transparent;border:none;}}
#Sidebar{{background:#090b12;border-right:1px solid #181a26;}}
#TopBar{{background:#0b0d14;border-bottom:1px solid #181a26;}}
#PostCtrlBar{{background:#090b12;border-top:1px solid #181a26;}}
QMenu{{background:#12151f;border:1px solid #2a2f4a;color:#c0c8e0;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#3a2880;color:#c0b0ff;}}
QMenu::separator{{height:1px;background:#2a2f4a;margin:3px 8px;}}
"""

# ── EMBER ─────────────────────────────────────────────────────────────────────
EMBER = f"""
QMainWindow,QWidget{{background:#100e08;color:#d8c8a0;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#14141e;}}
QSplitter > QWidget{{background:#14141e;}}
QSplitter::handle{{background:#1e1a10;width:1px;height:1px;}}
QStackedWidget{{background:#14141e;}}
#SettingsInner{{background:#14141e;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#14141e;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#100e08;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#3a3018;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#100e08;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#3a3018;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#181408;border:1px solid #2e2818;border-radius:8px;padding:6px 10px;color:#d8c8a0;selection-background-color:#8a6010;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#c07820;}}
QSpinBox,QDoubleSpinBox{{background:#181408;border:1px solid #2e2818;border-radius:8px;padding:5px 8px;color:#d8c8a0;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#181408;border:1px solid #2e2818;border-radius:8px;padding:5px 10px;color:#d8c8a0;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#181408;border:1px solid #3a3018;color:#d8c8a0;selection-background-color:#8a6010;border-radius:6px;padding:2px;}}
QPushButton{{background:#1c1810;border:1px solid #2e2818;border-radius:8px;padding:7px 16px;color:#b09870;font-weight:600;}}
QPushButton:hover{{background:#252010;border-color:#a07020;color:#e0c880;}}
QPushButton:pressed{{background:#1c1810;}}
QPushButton:checked{{background:#6a4808;border-color:#c07820;color:#ffd060;}}
QPushButton:disabled{{color:#4a4020;border-color:#201c10;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#6a5830;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(180,120,20,0.1);color:#d8c8a0;border-left:3px solid rgba(180,120,20,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(180,120,20,0.07);}}
QPushButton#NavBtn:checked{{background:rgba(180,120,20,0.15);color:#ffa020;border-left:3px solid #c07820;font-weight:700;}}
QPushButton#PostCtrl{{background:#141008;border:1px solid #2a2410;border-radius:8px;padding:0 10px;color:#7a6840;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#201a08;color:#c0a860;border-color:#3a3018;}}
QPushButton#PostCtrl:pressed{{background:#141008;}}
QPushButton#ModeBtn{{background:#141008;border:1px solid #2a2410;border-radius:8px;padding:6px 12px;color:#6a5830;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#1a1608;color:#d8c8a0;}}
QListWidget{{background:#141008;border:1px solid #2a2410;border-radius:8px;color:#b09870;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(180,120,20,0.1);}}
QListWidget::item:selected{{background:#6a4808;color:#ffd060;}}
QTableWidget,QTableView{{background:#141008;gridline-color:#2a2410;border:1px solid #2a2410;border-radius:8px;color:#d8c8a0;selection-background-color:#6a4808;}}
QHeaderView::section{{background:#100e08;color:#6a5830;border:none;border-bottom:1px solid #2a2410;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #2a2410;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#6a5830;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#b09870;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #3a3018;border-radius:5px;background:#181408;}}
QCheckBox::indicator:checked{{background:#c07820;border-color:#c07820;}}
QProgressBar{{background:#181408;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8a5808,stop:1 #e09030);border-radius:3px;}}
QToolTip{{background:#1c1810;color:#d8c8a0;border:1px solid #3a3018;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#d8c8a0;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#ffa020;background:transparent;border:none;}}
#Sidebar{{background:#0c0a06;border-right:1px solid #1e1a10;}}
#TopBar{{background:#100e08;border-bottom:1px solid #1e1a10;}}
#PostCtrlBar{{background:#0c0a06;border-top:1px solid #1e1a10;}}
QMenu{{background:#181408;border:1px solid #3a3018;color:#d8c8a0;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#6a4808;color:#ffd060;}}
"""

# ── SLATE ─────────────────────────────────────────────────────────────────────
SLATE = f"""
QMainWindow,QWidget{{background:#1e2028;color:#c8ccd8;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#16181e;}}
QSplitter > QWidget{{background:#16181e;}}
QSplitter::handle{{background:#2a2e3a;width:1px;height:1px;}}
QStackedWidget{{background:#16181e;}}
#SettingsInner{{background:#16181e;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#16181e;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#1e2028;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#3a3e50;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#1e2028;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#3a3e50;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:6px 10px;color:#c8ccd8;selection-background-color:#3a6090;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#5080b0;}}
QSpinBox,QDoubleSpinBox{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:5px 8px;color:#c8ccd8;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:5px 10px;color:#c8ccd8;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#262a36;border:1px solid #3a3e50;color:#c8ccd8;selection-background-color:#3a6090;border-radius:6px;padding:2px;}}
QPushButton{{background:#262a36;border:1px solid #323646;border-radius:8px;padding:7px 16px;color:#9098b0;font-weight:600;}}
QPushButton:hover{{background:#2e3244;border-color:#5080b0;color:#d0d4e8;}}
QPushButton:pressed{{background:#262a36;}}
QPushButton:checked{{background:#284870;border-color:#5080b0;color:#80b0e0;}}
QPushButton:disabled{{color:#484c60;border-color:#282c38;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#585c70;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(80,128,180,0.1);color:#c8ccd8;border-left:3px solid rgba(80,128,180,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(80,128,180,0.07);}}
QPushButton#NavBtn:checked{{background:rgba(80,128,180,0.15);color:#80b0e0;border-left:3px solid #4a80b8;font-weight:700;}}
QPushButton#PostCtrl{{background:#1e2028;border:1px solid #2a2e3a;border-radius:8px;padding:0 10px;color:#686870;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#262a36;color:#a0a8c0;border-color:#3a3e50;}}
QPushButton#PostCtrl:pressed{{background:#1e2028;}}
QPushButton#ModeBtn{{background:#1e2028;border:1px solid #2a2e3a;border-radius:8px;padding:6px 12px;color:#585c70;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#242830;color:#c8ccd8;}}
QListWidget{{background:#1e2028;border:1px solid #2a2e3a;border-radius:8px;color:#9098b0;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(80,128,180,0.1);}}
QListWidget::item:selected{{background:#284870;color:#80b0e0;}}
QTableWidget,QTableView{{background:#1e2028;gridline-color:#2a2e3a;border:1px solid #2a2e3a;border-radius:8px;color:#c8ccd8;selection-background-color:#284870;}}
QHeaderView::section{{background:#1a1e26;color:#585c70;border:none;border-bottom:1px solid #2a2e3a;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #2a2e3a;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#585c70;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#9098b0;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #3a3e50;border-radius:5px;background:#262a36;}}
QCheckBox::indicator:checked{{background:#4a80b8;border-color:#4a80b8;}}
QProgressBar{{background:#262a36;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3a6090,stop:1 #60a0d8);border-radius:3px;}}
QToolTip{{background:#262a36;color:#c8ccd8;border:1px solid #3a3e50;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#c8ccd8;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#80b0e0;background:transparent;border:none;}}
#Sidebar{{background:#191c24;border-right:1px solid #2a2e3a;}}
#TopBar{{background:#1e2028;border-bottom:1px solid #2a2e3a;}}
#PostCtrlBar{{background:#191c24;border-top:1px solid #2a2e3a;}}
QMenu{{background:#262a36;border:1px solid #3a3e50;color:#c8ccd8;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#284870;color:#80b0e0;}}
"""

# ── SAKURA ────────────────────────────────────────────────────────────────────
SAKURA = f"""
QMainWindow,QWidget{{background:#110a12;color:#e0c8d8;font-family:{_FONT};font-size:13px;}}
QSplitter{{background:#140820;}}
QSplitter > QWidget{{background:#140820;}}
QSplitter::handle{{background:#1e1020;width:1px;height:1px;}}
QStackedWidget{{background:#140820;}}
#SettingsInner{{background:#140820;}}
QScrollArea>QWidget#qt_scrollarea_viewport{{background:#140820;}}
QScrollArea{{border:none;background:transparent;}}
QAbstractScrollArea>QWidget{{background:transparent;}}
QScrollBar:vertical{{background:#110a12;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#38202e;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#110a12;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#38202e;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#180f1a;border:1px solid #281828;border-radius:8px;padding:6px 10px;color:#e0c8d8;selection-background-color:#802050;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#c04080;}}
QSpinBox,QDoubleSpinBox{{background:#180f1a;border:1px solid #281828;border-radius:8px;padding:5px 8px;color:#e0c8d8;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#180f1a;border:1px solid #281828;border-radius:8px;padding:5px 10px;color:#e0c8d8;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#180f1a;border:1px solid #38202e;color:#e0c8d8;selection-background-color:#802050;border-radius:6px;padding:2px;}}
QPushButton{{background:#1c1020;border:1px solid #281828;border-radius:8px;padding:7px 16px;color:#c090a8;font-weight:600;}}
QPushButton:hover{{background:#251528;border-color:#c04080;color:#f0d0e0;}}
QPushButton:pressed{{background:#1c1020;}}
QPushButton:checked{{background:#601840;border-color:#c04080;color:#ffb0d0;}}
QPushButton:disabled{{color:#4a283a;border-color:#1e1020;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#604858;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(192,64,128,0.1);color:#e0c8d8;border-left:3px solid rgba(192,64,128,0.4);}}
QPushButton#NavBtn:pressed{{background:rgba(192,64,128,0.07);}}
QPushButton#NavBtn:checked{{background:rgba(192,64,128,0.15);color:#ff90c0;border-left:3px solid #c04080;font-weight:700;}}
QPushButton#PostCtrl{{background:#130a14;border:1px solid #241428;border-radius:8px;padding:0 10px;color:#7a5068;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#1c1020;color:#c090a8;border-color:#381828;}}
QPushButton#PostCtrl:pressed{{background:#130a14;}}
QPushButton#ModeBtn{{background:#130a14;border:1px solid #241428;border-radius:8px;padding:6px 12px;color:#604858;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#180f1a;color:#e0c8d8;}}
QListWidget{{background:#130a14;border:1px solid #241428;border-radius:8px;color:#c090a8;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(192,64,128,0.1);}}
QListWidget::item:selected{{background:#601840;color:#ffb0d0;}}
QTableWidget,QTableView{{background:#130a14;gridline-color:#241428;border:1px solid #241428;border-radius:8px;color:#e0c8d8;selection-background-color:#601840;}}
QHeaderView::section{{background:#110a12;color:#604858;border:none;border-bottom:1px solid #241428;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #241428;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#604858;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#c090a8;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #38202e;border-radius:5px;background:#180f1a;}}
QCheckBox::indicator:checked{{background:#c04080;border-color:#c04080;}}
QProgressBar{{background:#180f1a;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #902040,stop:1 #e060a0);border-radius:3px;}}
QToolTip{{background:#1c1020;color:#e0c8d8;border:1px solid #38202e;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#e0c8d8;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#ff90c0;background:transparent;border:none;}}
#Sidebar{{background:#0d0810;border-right:1px solid #1e1020;}}
#TopBar{{background:#110a12;border-bottom:1px solid #1e1020;}}
#PostCtrlBar{{background:#0d0810;border-top:1px solid #1e1020;}}
QMenu{{background:#180f1a;border:1px solid #38202e;color:#e0c8d8;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#601840;color:#ffb0d0;}}
"""


# ── LIGHT ─────────────────────────────────────────────────────────────────────
LIGHT = f"""
QMainWindow,QWidget{{background:#f4f5f8;color:#1a1c2a;font-family:{_FONT};font-size:13px;}}
QSplitter::handle{{background:#d8daec;width:1px;height:1px;}}
QScrollArea{{border:none;background:#f4f5f8;}}
#thumb_placeholder{{background:#707070;}}
QLabel#thumb_placeholder{{background:#707070;}}
QAbstractScrollArea{{background:#f4f5f8;}}
QAbstractScrollArea>QWidget{{background:#f4f5f8;border:none;}}
QAbstractScrollArea>QWidget>QWidget{{background:#f4f5f8;border:none;}}
QFrame{{border:none;}}
QScrollBar:vertical{{background:#eceef8;width:7px;border:none;border-radius:4px;}}
QScrollBar::handle:vertical{{background:#b8bcd4;border-radius:4px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
QScrollBar:horizontal{{background:#eceef8;height:7px;border:none;border-radius:4px;}}
QScrollBar::handle:horizontal{{background:#b8bcd4;border-radius:4px;min-width:20px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;}}
QLineEdit,QTextEdit,QPlainTextEdit{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:6px 10px;color:#1a1c2a;selection-background-color:#a090e0;}}
QLineEdit:focus,QTextEdit:focus{{border-color:#6050c0;}}
QSpinBox,QDoubleSpinBox{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:5px 8px;color:#1a1c2a;}}
QSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{width:0;}}
QComboBox{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:5px 10px;color:#1a1c2a;}}
QComboBox::drop-down{{border:none;width:18px;}}
QComboBox QAbstractItemView{{background:#ffffff;border:1px solid #c8cce0;color:#1a1c2a;selection-background-color:#a090e0;border-radius:6px;padding:2px;}}
QPushButton{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:7px 16px;color:#3a3c60;font-weight:600;}}
QPushButton:hover{{background:#eceeff;border-color:#8070d0;color:#1a1c2a;}}
QPushButton:pressed{{background:#ffffff;}}
QPushButton:checked{{background:#d8d4f8;border-color:#6050c0;color:#4030a0;}}
QPushButton:disabled{{color:#a0a4c0;border-color:#e0e2f0;}}
QPushButton#NavBtn{{
    background:transparent;border:none;border-left:3px solid transparent;
    border-radius:0px;padding:9px 12px 9px 13px;color:#8088a8;
    font-weight:600;text-align:left;font-size:13px;}}
QPushButton#NavBtn:hover{{background:rgba(96,80,192,0.08);color:#1a1c2a;border-left:3px solid rgba(96,80,192,0.3);}}
QPushButton#NavBtn:pressed{{background:rgba(96,80,192,0.05);}}
QPushButton#NavBtn:checked{{background:rgba(96,80,192,0.1);color:#4030a0;border-left:3px solid #6050c0;font-weight:700;}}
QPushButton#PostCtrl{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:0 10px;color:#6068a0;font-size:13px;}}
QPushButton#PostCtrl:hover{{background:#eceeff;color:#1a1c2a;border-color:#8070d0;}}
QPushButton#PostCtrl:pressed{{background:#ffffff;}}
QPushButton#ModeBtn{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;padding:6px 12px;color:#8088a8;font-weight:600;font-size:12px;}}
QPushButton#ModeBtn:hover{{background:#eceeff;color:#1a1c2a;}}
QListWidget{{background:#ffffff;border:1px solid #c8cce0;border-radius:8px;color:#3a3c60;outline:none;}}
QListWidget::item{{padding:3px 6px;border-radius:4px;}}
QListWidget::item:hover{{background:rgba(96,80,192,0.06);}}
QListWidget::item:selected{{background:#d8d4f8;color:#4030a0;}}
QTableWidget,QTableView{{background:#ffffff;gridline-color:#e0e2f0;border:1px solid #c8cce0;border-radius:8px;color:#1a1c2a;selection-background-color:#d8d4f8;}}
QHeaderView::section{{background:#f4f5f8;color:#8088a8;border:none;border-bottom:1px solid #c8cce0;padding:6px 10px;font-weight:700;}}
QGroupBox{{border:1px solid #c8cce0;border-radius:10px;margin-top:14px;padding:12px 10px 8px 10px;font-weight:700;color:#8088a8;}}
QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 6px;}}
QCheckBox{{spacing:8px;font-weight:600;color:#3a3c60;}}
QCheckBox::indicator{{width:17px;height:17px;border:2px solid #b8bcd4;border-radius:5px;background:#ffffff;}}
QCheckBox::indicator:checked{{background:#6050c0;border-color:#6050c0;}}
QProgressBar{{background:#e0e2f0;border:none;border-radius:3px;height:5px;color:transparent;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6050c0,stop:1 #9080e0);border-radius:3px;}}
QToolTip{{background:#ffffff;color:#1a1c2a;border:1px solid #c8cce0;padding:5px 9px;border-radius:6px;font-size:12px;}}
QLabel#Title{{font-size:16px;font-weight:700;color:#1a1c2a;background:transparent;border:none;padding:0;}}
QLabel#Logo{{font-size:14px;font-weight:800;color:#4030a0;background:transparent;border:none;}}
#Sidebar{{background:#eceef8;border-right:1px solid #c8cce0;}}
#TopBar{{background:#f4f5f8;border-bottom:1px solid #c8cce0;}}
#PostCtrlBar{{background:#eceef8;border-top:1px solid #c8cce0;}}
QMenu{{background:#ffffff;border:1px solid #c8cce0;color:#1a1c2a;border-radius:8px;padding:4px;}}
QMenu::item{{padding:7px 22px;border-radius:5px;}}
QMenu::item:selected{{background:#d8d4f8;color:#4030a0;}}
"""

THEMES = {
    "dark": ABYSS, "abyss": ABYSS,
    "ember": EMBER,
    "slate": SLATE, "gray": SLATE,
    "sakura": SAKURA,
    "r34": R34,
    "ph": PH,
    "ph": PH,
    "light": LIGHT,
}

def stylesheet_for(name: str) -> str:
    return THEMES.get(name, ABYSS)
