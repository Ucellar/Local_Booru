"""Visual parser blueprint editor.

The editor is deliberately JSON-backed: nodes and custom modules are stored
under Local_Booru_Archive/settings/config/parser_blueprints so a power user can
edit them manually or ship them with a future build.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QMimeData
from PySide6.QtGui import QColor, QBrush, QPen, QPainterPath, QPainter, QAction, QPolygonF, QDrag
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QLineEdit, QTextEdit, QPlainTextEdit, QCheckBox,
    QMessageBox, QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsSimpleTextItem,
    QFormLayout, QGroupBox, QInputDialog, QFileDialog, QMenu, QDialog,
    QDialogButtonBox, QTabWidget, QSpinBox, QAbstractItemView, QComboBox,
    QTabBar, QApplication,
)

from core.settings import save_settings
from core.parser_blueprint import (
    BlueprintGraph, BlueprintNode, BlueprintEdge, compile_blueprint,
    default_blueprint, load_active_blueprint, save_active_blueprint,
    registry_with_custom, modules_dir, create_custom_module, validate_graph, analyze_graph,
    builtin_preset_names, list_user_presets, save_preset, load_preset, delete_preset,
)
from core.parser_blueprint.schema import PortSpec
from core.parser_blueprint.storage import sync_graph_with_site_blocks, _apply_wide_standard_layout


PORT_RADIUS = 6
NODE_W = 230
HEADER_H = 32
PORT_H = 25
# Blueprint canvas should feel practically infinite, like Unreal/Node-RED.
# QGraphicsScene is finite internally, so use a very large world and auto-expand it
# if a user somehow drags nodes close to the border.
BLUEPRINT_WORLD_HALF = 1_000_000
BLUEPRINT_EDGE_GUARD = 25_000
BLUEPRINT_EDGE_EXPAND = 250_000



PORT_TYPE_COLORS = {
    "Any": "#94a3b8",
    "Control": "#f8fafc",
    "Exec": "#f8fafc",
    "File": "#94a3b8",
    "FileSet": "#94a3b8",
    "HashInfo": "#60a5fa",
    "SiteMatch": "#22c55e",
    "VerifiedPost": "#22c55e",
    "MatchBundle": "#a78bfa",
    "TagBundle": "#facc15",
    "SourceUrl": "#fb7185",
    "PostUrl": "#fb7185",
    "SaveCommand": "#f97316",
    "Error": "#ef4444",
}


def _port_color(port_type: str, direction: str = "out") -> QColor:
    text = str(port_type or "Any")
    if text in PORT_TYPE_COLORS:
        return QColor(PORT_TYPE_COLORS[text])
    low = text.lower()
    if "error" in low:
        return QColor(PORT_TYPE_COLORS["Error"])
    if "tag" in low:
        return QColor(PORT_TYPE_COLORS["TagBundle"])
    if "hash" in low or "md5" in low:
        return QColor(PORT_TYPE_COLORS["HashInfo"])
    if "url" in low:
        return QColor(PORT_TYPE_COLORS["SourceUrl"])
    if "match" in low or "post" in low:
        return QColor(PORT_TYPE_COLORS["SiteMatch"])
    return QColor("#fbbf24" if direction == "in" else "#60a5fa")


def _is_exec_port(port_type: str, port_name: str = "") -> bool:
    text = (str(port_type or "") + " " + str(port_name or "")).lower()
    return any(x in text for x in ("control", "exec", "miss", "done", "skip", "error"))


def _node_accent_color(spec_color: str, enabled: bool = True) -> QColor:
    color = QColor(spec_color or "#334155")
    if not enabled:
        color = QColor("#475569")
    return color


def _edge_color_for_port(port_type: str, port_name: str = "") -> QColor:
    if _is_exec_port(port_type, port_name):
        return QColor("#e5e7eb")
    return _port_color(port_type, "out")


def _safe_json_loads(text: str, fallback: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return fallback


class PortItem(QGraphicsEllipseItem):
    """Visible draggable pin on a blueprint node.

    Data pins are circles. Exec/control-like pins are pill-shaped, like a
    lightweight Unreal Blueprint execution pin.  The editor still stores the
    same JSON PortSpec, so this is a pure visual/UX layer.
    """

    def __init__(self, node_id: str, port_name: str, direction: str, port_type: str, parent=None):
        self.is_exec = _is_exec_port(port_type, port_name)
        if self.is_exec:
            super().__init__(-8, -5, 16, 10, parent)
        else:
            super().__init__(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2, parent)
        self.node_id = node_id
        self.port_name = port_name
        self.direction = direction
        self.port_type = port_type
        color = _port_color(port_type, direction)
        self.setBrush(QBrush(color))
        self.setPen(QPen(QColor("#020617"), 1.4))
        self.setToolTip(f"{direction}: {port_name} [{port_type}]\nПотяни провод от выхода к входу")
        self.setZValue(8)


class CommentFrameItem(QGraphicsRectItem):
    """Non-persistent visual group frame, Unreal-like comment box."""

    def __init__(self, title: str, rect: QRectF, color: QColor):
        super().__init__(rect)
        self.setZValue(-5)
        self.setPen(QPen(color, 1.4, Qt.DashLine))
        fill = QColor(color)
        fill.setAlpha(20)
        self.setBrush(QBrush(fill))
        label = QGraphicsSimpleTextItem(title, self)
        label.setBrush(QBrush(color))
        label.setScale(1.15)
        label.setPos(rect.left() + 12, rect.top() + 8)


class NodeItem(QGraphicsRectItem):
    def __init__(self, node: BlueprintNode, spec_color: str = "#334155", category: str = ""):
        rows = max(len(node.inputs), len(node.outputs), 1)
        height = max(114, HEADER_H + rows * PORT_H + 58)
        super().__init__(0, 0, NODE_W, height)
        self.node = node
        self.spec_color = spec_color
        self.category = category
        self.port_items: dict[tuple[str, str], PortItem] = {}
        self.setPos(node.x, node.y)
        self.setFlag(QGraphicsRectItem.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.ItemSendsGeometryChanges, True)
        self.setPen(QPen(QColor("#64748b"), 1.15))
        self.setBrush(QBrush(QColor("#0f172a")))
        if not bool(getattr(node, "enabled", True)):
            self.setOpacity(0.62)
        self.setZValue(2)
        self._build_children()

    def _build_children(self):
        enabled = bool(getattr(self.node, "enabled", True))
        accent = _node_accent_color(self.spec_color, enabled)

        # Header strip.
        title_bg = QGraphicsRectItem(0, 0, NODE_W, HEADER_H, self)
        title_bg.setPen(QPen(Qt.NoPen))
        title_bg.setBrush(QBrush(accent))
        title_bg.setZValue(0.1)

        # Thin live-status line under the header.
        status_bg = QGraphicsRectItem(0, HEADER_H, NODE_W, 18, self)
        status_bg.setPen(QPen(Qt.NoPen))
        status_bg.setBrush(QBrush(QColor("#111827") if enabled else QColor("#1f2937")))

        title = QGraphicsSimpleTextItem(self.node.title or self.node.type_id, self)
        title.setBrush(QBrush(QColor("#f8fafc")))
        title.setPos(10, 7)

        type_text = QGraphicsSimpleTextItem(self.node.type_id, self)
        type_text.setBrush(QBrush(QColor("#94a3b8")))
        type_text.setScale(0.78)
        type_text.setPos(10, HEADER_H + 2)

        runtime_text = f"workers:{int(getattr(self.node, 'workers', 1) or 1)}  delay:{int(getattr(self.node, 'min_delay_ms', 0) or 0)}ms"
        if getattr(self.node, "rate_group", ""):
            runtime_text += f"  rate:{getattr(self.node, 'rate_group', '')}"
        if not enabled:
            runtime_text = "SKIP / " + runtime_text
        rt = QGraphicsSimpleTextItem(runtime_text, self)
        rt.setBrush(QBrush(QColor("#38bdf8") if enabled else QColor("#cbd5e1")))
        rt.setScale(0.72)
        rt.setPos(10, HEADER_H + 18)

        # Small node category badge.
        if self.category:
            badge = QGraphicsSimpleTextItem(self.category, self)
            badge.setBrush(QBrush(QColor("#dbeafe")))
            badge.setScale(0.68)
            br = badge.boundingRect()
            badge.setPos(NODE_W - br.width() * 0.68 - 10, 8)

        for i, port in enumerate(self.node.inputs):
            y = HEADER_H + 49 + i * PORT_H
            p = PortItem(self.node.id, port.name, "in", port.type, self)
            p.setPos(0, y)
            self.port_items[("in", port.name)] = p
            t = QGraphicsSimpleTextItem(port.label or port.name, self)
            t.setBrush(QBrush(_port_color(port.type, "in")))
            t.setScale(0.82)
            t.setPos(15, y - 8)
            type_t = QGraphicsSimpleTextItem(port.type, self)
            type_t.setBrush(QBrush(QColor("#64748b")))
            type_t.setScale(0.62)
            type_t.setPos(15, y + 4)
        for i, port in enumerate(self.node.outputs):
            y = HEADER_H + 49 + i * PORT_H
            p = PortItem(self.node.id, port.name, "out", port.type, self)
            p.setPos(NODE_W, y)
            self.port_items[("out", port.name)] = p
            label = port.label or port.name
            t = QGraphicsSimpleTextItem(label, self)
            t.setBrush(QBrush(_port_color(port.type, "out")))
            t.setScale(0.82)
            br = t.boundingRect()
            t.setPos(NODE_W - br.width() * 0.82 - 16, y - 8)
            type_t = QGraphicsSimpleTextItem(port.type, self)
            type_t.setBrush(QBrush(QColor("#64748b")))
            type_t.setScale(0.62)
            br2 = type_t.boundingRect()
            type_t.setPos(NODE_W - br2.width() * 0.62 - 16, y + 4)

        # Footer hint.
        foot = QGraphicsSimpleTextItem("двойной клик: редактировать", self)
        foot.setBrush(QBrush(QColor("#64748b")))
        foot.setScale(0.68)
        foot.setPos(10, self.rect().height() - 18)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        painter.setPen(QPen(QColor("#93a4bb") if self.isSelected() else QColor("#334155"), 2.0 if self.isSelected() else 1.2))
        painter.setBrush(QBrush(QColor("#0f172a")))
        painter.drawRoundedRect(rect, 8, 8)

    def port_scene_pos(self, direction: str, port_name: str) -> QPointF:
        item = self.port_items.get((direction, port_name))
        if not item:
            return self.scenePos() + QPointF(NODE_W / 2, self.rect().height() / 2)
        return item.mapToScene(QPointF(0, 0))


class EdgeItem(QGraphicsPathItem):
    def __init__(self, edge: BlueprintEdge, src: QPointF, dst: QPointF, color: QColor | None = None, *, temporary: bool = False):
        super().__init__()
        self.edge = edge
        if not temporary:
            self.setFlag(QGraphicsPathItem.ItemIsSelectable, True)
        self.setZValue(1 if not temporary else 20)
        pen = QPen(color or QColor("#8b5cf6"), 2.4 if not temporary else 2.0)
        if temporary:
            pen.setStyle(Qt.DashLine)
        pen.setCapStyle(Qt.RoundCap)
        self.setPen(pen)
        self.setPath(self._make_path(src, dst))

    @staticmethod
    def _make_path(src: QPointF, dst: QPointF) -> QPainterPath:
        path = QPainterPath(src)
        dx = max(90.0, abs(dst.x() - src.x()) * 0.45)
        # If dragging backwards, still keep a readable S-curve instead of a sharp line.
        if dst.x() < src.x():
            dx = max(120.0, abs(dst.x() - src.x()) * 0.35)
        c1 = QPointF(src.x() + dx, src.y())
        c2 = QPointF(dst.x() - dx, dst.y())
        path.cubicTo(c1, c2, dst)
        return path


class BlueprintView(QGraphicsView):
    def __init__(self, owner: "ParserBlueprintPage"):
        super().__init__()
        self.owner = owner
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMinimumSize(620, 460)
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: list[EdgeItem] = []
        self.comment_items: list[CommentFrameItem] = []
        self.pending_output: PortItem | None = None
        self.preview_edge: EdgeItem | None = None
        self._panning = False
        self._pan_start = None
        self.setStyleSheet("background:#080d18;border:1px solid #263244;border-radius:8px;")
        self._world_half = BLUEPRINT_WORLD_HALF
        self.setSceneRect(-self._world_half, -self._world_half, self._world_half * 2, self._world_half * 2)
        # Start around the real graph, not in the corner of the giant scene.
        self.centerOn(0, 0)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        super().drawBackground(painter, rect)
        painter.fillRect(rect, QColor("#080d18"))
        for grid, color, width in ((32, "#172033", 1), (160, "#243044", 1)):
            left = int(rect.left()) - (int(rect.left()) % grid)
            top = int(rect.top()) - (int(rect.top()) % grid)
            pen = QPen(QColor(color), width)
            painter.setPen(pen)
            x = left
            while x < rect.right():
                painter.drawLine(x, rect.top(), x, rect.bottom())
                x += grid
            y = top
            while y < rect.bottom():
                painter.drawLine(rect.left(), y, rect.right(), y)
                y += grid

    def ensure_scene_contains_point(self, point: QPointF) -> None:
        """Grow the already huge scene if a node is dragged near the border.

        Users should never hit a hard wall while arranging a parser graph.  The
        scene starts with a million units in every direction, and this keeps the
        canvas effectively unlimited without constantly recalculating layout.
        """
        rect = self.sceneRect()
        if (point.x() > rect.right() - BLUEPRINT_EDGE_GUARD or
            point.x() < rect.left() + BLUEPRINT_EDGE_GUARD or
            point.y() > rect.bottom() - BLUEPRINT_EDGE_GUARD or
            point.y() < rect.top() + BLUEPRINT_EDGE_GUARD):
            new_rect = rect.adjusted(-BLUEPRINT_EDGE_EXPAND, -BLUEPRINT_EDGE_EXPAND, BLUEPRINT_EDGE_EXPAND, BLUEPRINT_EDGE_EXPAND)
            self.setSceneRect(new_rect)

    def _port_at_event(self, event) -> PortItem | None:
        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, PortItem):
            item = item.parentItem()
        return item if isinstance(item, PortItem) else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and event.modifiers() & Qt.AltModifier):
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        item = self._port_at_event(event)
        if isinstance(item, PortItem) and item.direction == "out":
            self.pending_output = item
            src = item.mapToScene(QPointF(0, 0))
            fake_edge = BlueprintEdge(id="preview", source_node=item.node_id, source_port=item.port_name, target_node="", target_port="")
            self.preview_edge = EdgeItem(fake_edge, src, src, _edge_color_for_port(item.port_type, item.port_name), temporary=True)
            self.scene_obj.addItem(self.preview_edge)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self.pending_output is not None and self.preview_edge is not None:
            src = self.pending_output.mapToScene(QPointF(0, 0))
            dst = self.mapToScene(event.position().toPoint())
            self.preview_edge.setPath(EdgeItem._make_path(src, dst))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self._pan_start = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        item = self._port_at_event(event)
        if self.pending_output is not None:
            if self.preview_edge is not None:
                self.scene_obj.removeItem(self.preview_edge)
                self.preview_edge = None
            if isinstance(item, PortItem) and item.direction == "in":
                self.owner.add_edge(self.pending_output, item)
            self.pending_output = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        self.owner.sync_positions_from_scene()
        self.owner.refresh_edges()
        self.owner.update_inspector_from_selection()

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, NodeItem):
            item = item.parentItem()
        if isinstance(item, NodeItem):
            self.owner.open_block_editor(item.node.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        scene_pos = self.mapToScene(event.pos())
        item = self.itemAt(event.pos())
        while item is not None and not isinstance(item, (NodeItem, PortItem)):
            item = item.parentItem()
        if isinstance(item, PortItem):
            item = item.parentItem()
        if isinstance(item, NodeItem):
            menu = QMenu(self)
            edit = menu.addAction("Открыть редактор блока")
            dup = menu.addAction("Дублировать блок")
            toggle = menu.addAction("Выключить блок" if bool(getattr(item.node, "enabled", True)) else "Включить блок")
            delete = menu.addAction("Удалить блок")
            chosen = menu.exec(event.globalPos())
            if chosen == edit:
                self.owner.open_block_editor(item.node.id)
            elif chosen == dup:
                self.owner.duplicate_node(item.node.id, scene_pos)
            elif chosen == toggle:
                self.owner.toggle_node_enabled(item.node.id)
            elif chosen == delete:
                item.setSelected(True)
                self.owner.delete_selected()
            return
        self.owner.open_canvas_context_menu(event.globalPos(), scene_pos)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.owner.delete_selected()
            return
        if event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self.owner.focus_palette_search()
            return
        super().keyPressEvent(event)

    def clear_graph(self):
        self.scene_obj.clear()
        self.node_items = {}
        self.edge_items = []
        self.comment_items = []
        self.preview_edge = None


class DockPanelTabBar(QTabBar):
    """Photoshop-like movable/droppable tab bar for blueprint side panels."""

    MIME = "application/x-local-booru-blueprint-panel"

    def __init__(self, tabs: "DockPanelTabs"):
        super().__init__(tabs)
        self.tabs = tabs
        self._drag_start_pos = None
        self._drag_panel_id: str | None = None
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setUsesScrollButtons(True)

    def mousePressEvent(self, event):
        self._drag_start_pos = event.position().toPoint()
        idx = self.tabAt(self._drag_start_pos)
        self._drag_panel_id = str(self.tabData(idx) or "") if idx >= 0 else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_start_pos is not None and self._drag_panel_id and
            (event.position().toPoint() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(self.MIME, self._drag_panel_id.encode("utf-8"))
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
            self._drag_start_pos = None
            self._drag_panel_id = None
            return
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(self.MIME):
            super().dropEvent(event)
            return
        panel_id = bytes(event.mimeData().data(self.MIME)).decode("utf-8", errors="ignore")
        idx = self.tabAt(event.position().toPoint())
        if idx < 0:
            idx = self.count()
        self.tabs.owner.move_panel(panel_id, self.tabs.area, idx)
        event.acceptProposedAction()


class DockPanelTabs(QTabWidget):
    """A tab group that can accept blueprint panels from other groups.

    It is intentionally simple: tabs can be rearranged inside the group, dragged
    between left/right/bottom groups, or detached into a small floating window.
    """

    def __init__(self, owner: "ParserBlueprintPage", area: str):
        super().__init__()
        self.owner = owner
        self.area = area
        self.setTabBar(DockPanelTabBar(self))
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setAcceptDrops(True)
        self.setTabsClosable(False)
        self.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self._tab_menu)

    def add_panel_tab(self, panel_id: str, widget: QWidget, title: str, index: int | None = None):
        if index is None or index < 0 or index > self.count():
            index = self.count()
        idx = self.insertTab(index, widget, title)
        self.setTabToolTip(idx, "Перетащи вкладку в левую/правую/нижнюю группу или ПКМ для меню")
        self.tabBar().setTabData(idx, panel_id)
        self.setCurrentIndex(idx)
        return idx

    def remove_panel_id(self, panel_id: str) -> QWidget | None:
        for i in range(self.count()):
            if str(self.tabBar().tabData(i) or "") == str(panel_id):
                widget = self.widget(i)
                self.removeTab(i)
                return widget
        return None

    def _tab_menu(self, pos):
        idx = self.tabBar().tabAt(pos)
        if idx < 0:
            return
        panel_id = str(self.tabBar().tabData(idx) or "")
        menu = QMenu(self)
        move_left = menu.addAction("Перенести влево")
        move_right = menu.addAction("Перенести вправо")
        move_bottom = menu.addAction("Перенести вниз")
        detach = menu.addAction("Открепить в окно")
        chosen = menu.exec(self.tabBar().mapToGlobal(pos))
        if chosen == move_left:
            self.owner.move_panel(panel_id, "left")
        elif chosen == move_right:
            self.owner.move_panel(panel_id, "right")
        elif chosen == move_bottom:
            self.owner.move_panel(panel_id, "bottom")
        elif chosen == detach:
            self.owner.float_panel(panel_id)


class FloatingPanelDialog(QDialog):
    def __init__(self, owner: "ParserBlueprintPage", panel_id: str, title: str, widget: QWidget):
        super().__init__(owner)
        self.owner = owner
        self.panel_id = panel_id
        self._closing_from_owner = False
        self.setWindowTitle(title)
        self.resize(460, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(widget)

    def closeEvent(self, event):
        if not self._closing_from_owner:
            self.owner.move_panel(self.panel_id, "right")
        super().closeEvent(event)


class BlockEditorDialog(QDialog):
    """Full-access block console editor with save/cancel shelf."""

    def __init__(self, parent, node: BlueprintNode):
        super().__init__(parent)
        self.setWindowTitle(f"Редактор блока: {node.title}")
        self.resize(760, 680)
        self.node = node
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        main = QWidget(); form = QFormLayout(main)
        self.title_edit = QLineEdit(node.title)
        self.type_edit = QLineEdit(node.type_id)
        self.enabled_cb = QCheckBox("Блок включён")
        self.enabled_cb.setChecked(bool(getattr(node, "enabled", True)))
        self.workers = QSpinBox(); self.workers.setRange(1, 128); self.workers.setValue(max(1, int(getattr(node, "workers", 1) or 1)))
        self.delay = QSpinBox(); self.delay.setRange(0, 86400000); self.delay.setSuffix(" ms"); self.delay.setValue(max(0, int(getattr(node, "min_delay_ms", 0) or 0)))
        self.timeout = QSpinBox(); self.timeout.setRange(1, 86400000); self.timeout.setSuffix(" ms"); self.timeout.setValue(max(1, int(getattr(node, "timeout_ms", 30000) or 30000)))
        self.retries = QSpinBox(); self.retries.setRange(0, 1000); self.retries.setValue(max(0, int(getattr(node, "retry_count", 0) or 0)))
        self.rate_group = QLineEdit(str(getattr(node, "rate_group", "") or ""))
        self.on_disabled = QLineEdit(str(getattr(node, "on_disabled", "skip") or "skip"))
        form.addRow("Название", self.title_edit)
        form.addRow("Тип/action", self.type_edit)
        form.addRow("Включение", self.enabled_cb)
        form.addRow("Потоки блока", self.workers)
        form.addRow("Задержка блока", self.delay)
        form.addRow("Таймаут задачи", self.timeout)
        form.addRow("Повторы", self.retries)
        form.addRow("Группа лимита", self.rate_group)
        form.addRow("Если выключен", self.on_disabled)
        tabs.addTab(main, "Основное")

        self.ports_edit = QPlainTextEdit()
        self.ports_edit.setPlainText(json.dumps({"inputs": [p.to_dict() for p in node.inputs], "outputs": [p.to_dict() for p in node.outputs]}, ensure_ascii=False, indent=2))
        tabs.addTab(self.ports_edit, "Порты JSON")

        self.config_edit = QPlainTextEdit()
        self.config_edit.setPlainText(json.dumps(node.config, ensure_ascii=False, indent=2))
        tabs.addTab(self.config_edit, "Настройки JSON")

        self.full_edit = QPlainTextEdit()
        self.full_edit.setPlainText(json.dumps(node.to_dict(), ensure_ascii=False, indent=2))
        tabs.addTab(self.full_edit, "Полный блок")

        shelf = QDialogButtonBox()
        self.btn_save = shelf.addButton("Сохранить", QDialogButtonBox.AcceptRole)
        self.btn_cancel = shelf.addButton("Отмена", QDialogButtonBox.RejectRole)
        self.btn_check = shelf.addButton("Проверить JSON", QDialogButtonBox.ActionRole)
        self.btn_check.clicked.connect(self._check_json)
        shelf.accepted.connect(self.accept)
        shelf.rejected.connect(self.reject)
        lay.addWidget(shelf)

    def _check_json(self):
        try:
            json.loads(self.config_edit.toPlainText() or "{}")
            ports = json.loads(self.ports_edit.toPlainText() or "{}")
            for item in ports.get("inputs", []) + ports.get("outputs", []):
                PortSpec.from_dict(item)
            json.loads(self.full_edit.toPlainText() or "{}")
            QMessageBox.information(self, "Blueprint", "JSON нормальный")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка JSON", str(e))

    def apply_to_node(self, node: BlueprintNode) -> None:
        node.title = self.title_edit.text().strip() or node.title
        node.type_id = self.type_edit.text().strip() or node.type_id
        node.enabled = bool(self.enabled_cb.isChecked())
        node.workers = int(self.workers.value())
        node.min_delay_ms = int(self.delay.value())
        node.timeout_ms = int(self.timeout.value())
        node.retry_count = int(self.retries.value())
        node.rate_group = self.rate_group.text().strip()
        node.on_disabled = self.on_disabled.text().strip() or "skip"
        node.config = dict(_safe_json_loads(self.config_edit.toPlainText(), {}) or {})
        ports = _safe_json_loads(self.ports_edit.toPlainText(), {}) or {}
        node.inputs = [PortSpec.from_dict(x) for x in (ports.get("inputs", []) or [])]
        node.outputs = [PortSpec.from_dict(x) for x in (ports.get("outputs", []) or [])]
        # Keep runtime fields mirrored in config for manual JSON modules/plugins.
        node.config.update({
            "workers": node.workers,
            "min_delay_ms": node.min_delay_ms,
            "timeout_ms": node.timeout_ms,
            "retry_count": node.retry_count,
            "rate_group": node.rate_group,
            "enabled": node.enabled,
            "on_disabled": node.on_disabled,
        })

class SimpleOrderDialog(QDialog):
    """Compact list-based editor for users who only want to reorder sites/reverse steps."""

    def __init__(self, parent, md5_nodes: list[BlueprintNode], reverse_nodes: list[BlueprintNode]):
        super().__init__(parent)
        self.setWindowTitle("Простой порядок blueprint")
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        info = QLabel("Этот режим меняет тот же blueprint-граф: порядок MD5-сайтов и reverse-ветки. Перетаскивай строки вверх/вниз и нажми «Применить».")
        info.setWordWrap(True)
        info.setStyleSheet("color:#cbd5e1;background:#111827;border:1px solid #334155;border-radius:6px;padding:8px;")
        lay.addWidget(info)
        row = QHBoxLayout()
        lay.addLayout(row, 1)
        self.md5_list = QListWidget(); self.reverse_list = QListWidget()
        for lw in (self.md5_list, self.reverse_list):
            lw.setDragDropMode(QAbstractItemView.InternalMove)
            lw.setDefaultDropAction(Qt.MoveAction)
            lw.setSelectionMode(QAbstractItemView.SingleSelection)
        left = QVBoxLayout(); left.addWidget(QLabel("MD5-сайты")); left.addWidget(self.md5_list, 1)
        right = QVBoxLayout(); right.addWidget(QLabel("Reverse-поиск")); right.addWidget(self.reverse_list, 1)
        row.addLayout(left, 1); row.addLayout(right, 1)
        for node in md5_nodes:
            item = QListWidgetItem(node.title)
            item.setData(Qt.UserRole, node.id)
            self.md5_list.addItem(item)
        for node in reverse_nodes:
            item = QListWidgetItem(node.title)
            item.setData(Qt.UserRole, node.id)
            self.reverse_list.addItem(item)
        buttons = QDialogButtonBox()
        buttons.addButton("Применить", QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def md5_order(self) -> list[str]:
        return [str(self.md5_list.item(i).data(Qt.UserRole)) for i in range(self.md5_list.count())]

    def reverse_order(self) -> list[str]:
        return [str(self.reverse_list.item(i).data(Qt.UserRole)) for i in range(self.reverse_list.count())]


class PresetDialog(QDialog):
    """Load/delete blueprint presets without touching the graph editor canvas."""

    def __init__(self, parent, settings: dict):
        super().__init__(parent)
        self.setWindowTitle("Пресеты blueprint")
        self.resize(520, 520)
        self.settings = settings
        self.selected_preset_id: str | None = None
        lay = QVBoxLayout(self)
        info = QLabel("Пресеты — это готовые blueprint-файлы. Встроенные пресеты удалить нельзя; пользовательские лежат в settings/config/parser_blueprints/presets.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#cbd5e1;background:#111827;border:1px solid #334155;border-radius:6px;padding:8px;")
        lay.addWidget(info)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        self.btn_load = QPushButton("Загрузить")
        self.btn_delete = QPushButton("Удалить пользовательский")
        self.btn_cancel = QPushButton("Закрыть")
        btns.addWidget(self.btn_load); btns.addWidget(self.btn_delete); btns.addStretch(1); btns.addWidget(self.btn_cancel)
        lay.addLayout(btns)
        self.btn_load.clicked.connect(self._load)
        self.btn_delete.clicked.connect(self._delete)
        self.btn_cancel.clicked.connect(self.reject)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for pid, title in builtin_preset_names().items():
            item = QListWidgetItem(f"Встроенный / {title}")
            item.setData(Qt.UserRole, "builtin:" + pid)
            self.list.addItem(item)
        for pid in list_user_presets(self.settings):
            item = QListWidgetItem(f"Пользовательский / {pid}")
            item.setData(Qt.UserRole, pid)
            self.list.addItem(item)

    def _load(self):
        item = self.list.currentItem()
        if not item:
            return
        self.selected_preset_id = str(item.data(Qt.UserRole) or "")
        self.accept()

    def _delete(self):
        item = self.list.currentItem()
        if not item:
            return
        pid = str(item.data(Qt.UserRole) or "")
        if pid.startswith("builtin:"):
            QMessageBox.information(self, "Пресеты", "Встроенный пресет нельзя удалить")
            return
        if QMessageBox.question(self, "Пресеты", f"Удалить пользовательский пресет {pid}?") != QMessageBox.Yes:
            return
        delete_preset(self.settings, pid)
        self.refresh()


class ParserBlueprintPage(QWidget):
    """Editable Unreal-like graph for the parser pipeline."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.settings = main.settings
        self.registry = registry_with_custom(modules_dir(self.settings), self.settings)
        self.graph = sync_graph_with_site_blocks(load_active_blueprint(self.settings), self.settings)
        self.selected_node_id: str | None = None
        self._build_ui()
        self.load_graph(self.graph)

    def _build_ui(self):
        # 1920x1080 is the baseline; keep this page dense enough for Full HD.
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        self.compact_ui = bool(geo and (geo.width() <= 1920 or geo.height() <= 1080))
        root = QVBoxLayout(self)
        root.setContentsMargins(6 if self.compact_ui else 10, 6 if self.compact_ui else 10, 6 if self.compact_ui else 10, 6 if self.compact_ui else 10)
        root.setSpacing(4 if self.compact_ui else 8)
        if self.compact_ui:
            self.setStyleSheet("""
                QPushButton { padding: 3px 6px; min-height: 22px; }
                QLineEdit { min-height: 22px; }
                QGroupBox { margin-top: 8px; }
                QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
            """)

        top = QHBoxLayout()
        self.active_cb = QCheckBox("Парсер работает через blueprint")
        self.active_cb.setChecked(bool(self.settings.get("parser_blueprint_enabled", True)))
        self.active_cb.toggled.connect(self.toggle_active)
        self.name_edit = QLineEdit(self.graph.name)
        self.name_edit.setPlaceholderText("Название blueprint")
        top.addWidget(QLabel("Blueprint:"))
        top.addWidget(self.name_edit, 1)
        top.addWidget(self.active_cb)
        self.full_access_cb = QCheckBox("Можно всё / ответственность пользователя")
        self.full_access_cb.setChecked(bool(self.settings.get("parser_blueprint_full_access", True)))
        self.full_access_cb.toggled.connect(self.toggle_full_access)
        top.addWidget(self.full_access_cb)
        root.addLayout(top)

        warn_text = "Blueprint: можно полностью менять парсер. Предупреждения не блокируют full-access; SQLite, оригиналы и базовая запись защищены приложением."
        if not self.compact_ui:
            warn_text += " Блоки можно двигать, отключать, переподключать, переносить панели редактора как вкладки."
        warn = QLabel(warn_text)
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#fde68a;background:#3b2f0b;border:1px solid #a16207;border-radius:8px;padding:6px;")
        root.addWidget(warn)

        btns = QHBoxLayout()
        btns.setSpacing(4 if self.compact_ui else 6)
        self.btn_simple_order = QPushButton("Порядок" if self.compact_ui else "Простой порядок")
        self.btn_presets = QPushButton("Пресеты")
        self.btn_save_preset = QPushButton("Сохр. пресет" if self.compact_ui else "Сохранить как пресет")
        self.btn_load = QPushButton("↻" if self.compact_ui else "Перезагрузить активный")
        self.btn_save = QPushButton("Сохранить")
        self.btn_validate = QPushButton("Проверить")
        self.btn_compile = QPushButton("План" if self.compact_ui else "Собрать план")
        self.btn_new_module = QPushButton("Модуль" if self.compact_ui else "Новый модуль")
        self.btn_export = QPushButton("JSON" if self.compact_ui else "Экспорт JSON")
        self.btn_center_graph = QPushButton("К графу")
        self.btn_simple_order.clicked.connect(self.open_simple_order)
        self.btn_presets.clicked.connect(self.open_presets)
        self.btn_save_preset.clicked.connect(self.save_as_preset)
        self.btn_load.clicked.connect(self.reload_graph)
        self.btn_save.clicked.connect(self.save_graph)
        self.btn_validate.clicked.connect(lambda: self.validate_graph(show_ok=True))
        self.btn_compile.clicked.connect(self.compile_and_show)
        self.btn_new_module.clicked.connect(self.create_module)
        self.btn_export.clicked.connect(self.export_json)
        self.btn_center_graph.clicked.connect(self.center_on_graph)
        for b in (self.btn_simple_order, self.btn_presets, self.btn_save_preset, self.btn_load, self.btn_save, self.btn_validate, self.btn_compile, self.btn_new_module, self.btn_export, self.btn_center_graph):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)

        # Photoshop-like panel layout: left/right/bottom groups with draggable tabs.
        self._floating_panels: dict[str, FloatingPanelDialog] = {}
        self._panel_titles: dict[str, str] = {}
        self._panel_tabs: dict[str, DockPanelTabs] = {}

        vertical_split = QSplitter(Qt.Vertical)
        root.addWidget(vertical_split, 1)

        split = QSplitter(Qt.Horizontal)
        vertical_split.addWidget(split)

        self.left_tabs = DockPanelTabs(self, "left")
        self.right_tabs = DockPanelTabs(self, "right")
        self.bottom_tabs = DockPanelTabs(self, "bottom")
        self.bottom_tabs.hide()
        self._panel_tabs = {"left": self.left_tabs, "right": self.right_tabs, "bottom": self.bottom_tabs}

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(4, 4, 4, 4)
        left_lay.setSpacing(4)
        self.palette_search = QLineEdit()
        self.palette_search.setPlaceholderText("Поиск блока...  Ctrl+F")
        self.palette_search.textChanged.connect(self.reload_palette)
        left_lay.addWidget(self.palette_search)
        self.palette = QListWidget()
        self.palette.itemDoubleClicked.connect(self.add_palette_node)
        left_lay.addWidget(self.palette, 1)
        hint = QLabel("Двойной клик — добавить. ПКМ по холсту — меню.\nПровод: выход справа → вход слева.\nCtrl+колесо — масштаб, Alt+ЛКМ/СКМ — панорама.\nВкладки панелей можно перетаскивать как в Photoshop.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#94a3b8;")
        left_lay.addWidget(hint)
        self.left_tabs.add_panel_tab("palette", left, "Блоки")
        split.addWidget(self.left_tabs)

        self.view = BlueprintView(self)
        split.addWidget(self.view)

        inspector = QWidget()
        right_lay = QVBoxLayout(inspector)
        right_lay.setContentsMargins(4, 4, 4, 4)
        right_lay.setSpacing(4)
        form_box = QGroupBox("Инспектор блока")
        form = QFormLayout(form_box)
        form.setContentsMargins(6, 12, 6, 6)
        form.setSpacing(4 if self.compact_ui else 6)
        self.node_id_edit = QLineEdit(); self.node_id_edit.setReadOnly(True)
        self.node_type_edit = QLineEdit()
        self.node_title_edit = QLineEdit()
        self.node_inputs_edit = QPlainTextEdit(); self.node_inputs_edit.setMaximumHeight(70 if self.compact_ui else 105)
        self.node_outputs_edit = QPlainTextEdit(); self.node_outputs_edit.setMaximumHeight(70 if self.compact_ui else 105)
        self.node_config_edit = QPlainTextEdit(); self.node_config_edit.setMaximumHeight(95 if self.compact_ui else 145)
        self.node_workers_spin = QSpinBox(); self.node_workers_spin.setRange(1, 128)
        self.node_delay_spin = QSpinBox(); self.node_delay_spin.setRange(0, 86400000); self.node_delay_spin.setSuffix(" ms")
        self.node_timeout_spin = QSpinBox(); self.node_timeout_spin.setRange(1, 86400000); self.node_timeout_spin.setSuffix(" ms")
        self.node_retry_spin = QSpinBox(); self.node_retry_spin.setRange(0, 1000)
        self.node_rate_edit = QLineEdit()
        self.node_enabled_cb = QCheckBox("включён")
        form.addRow("ID", self.node_id_edit)
        form.addRow("Тип", self.node_type_edit)
        form.addRow("Название", self.node_title_edit)
        form.addRow("Входы JSON", self.node_inputs_edit)
        form.addRow("Выходы JSON", self.node_outputs_edit)
        form.addRow("Потоки", self.node_workers_spin)
        form.addRow("Задержка", self.node_delay_spin)
        form.addRow("Таймаут", self.node_timeout_spin)
        form.addRow("Повторы", self.node_retry_spin)
        form.addRow("Rate group", self.node_rate_edit)
        form.addRow("Активен", self.node_enabled_cb)
        form.addRow("Настройки JSON", self.node_config_edit)
        self.btn_apply_node = QPushButton("Применить")
        self.btn_open_editor = QPushButton("Редактор блока")
        self.btn_delete = QPushButton("Удалить выбранное")
        self.btn_apply_node.clicked.connect(self.apply_inspector)
        self.btn_open_editor.clicked.connect(lambda: self.open_block_editor(self.selected_node_id))
        self.btn_delete.clicked.connect(self.delete_selected)
        form.addRow(self.btn_apply_node)
        form.addRow(self.btn_open_editor)
        form.addRow(self.btn_delete)
        right_lay.addWidget(form_box, 1)
        self.right_tabs.add_panel_tab("inspector", inspector, "Инспектор")

        check_panel = QWidget()
        check_lay = QVBoxLayout(check_panel)
        check_lay.setContentsMargins(4, 4, 4, 4)
        check_lay.setSpacing(4)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumWidth(260 if self.compact_ui else 320)
        check_lay.addWidget(self.output, 1)
        self.right_tabs.add_panel_tab("check", check_panel, "Проверка")

        split.addWidget(self.right_tabs)
        split.setSizes([210 if self.compact_ui else 240, 1120, 300 if self.compact_ui else 360])
        vertical_split.addWidget(self.bottom_tabs)
        vertical_split.setSizes([860, 190])
        self._panel_titles = {"palette": "Блоки", "inspector": "Инспектор", "check": "Проверка"}
        self.reload_palette()

    def reload_palette(self):
        self.registry = registry_with_custom(modules_dir(self.settings), self.settings)
        self.palette.clear()
        needle = ""
        try:
            needle = self.palette_search.text().strip().lower()
        except Exception:
            needle = ""
        for spec in sorted(self.registry.values(), key=lambda s: (s.category, s.title)):
            hay = f"{spec.category} {spec.title} {spec.type_id} {spec.description}".lower()
            if needle and needle not in hay:
                continue
            item = QListWidgetItem(f"{spec.category} / {spec.title}")
            item.setData(Qt.UserRole, spec.type_id)
            item.setToolTip((spec.description or "") + f"\n{spec.type_id}")
            self.palette.addItem(item)

    def _remove_panel_widget(self, panel_id: str) -> QWidget | None:
        # Remove from any tab group.
        for tabs in getattr(self, "_panel_tabs", {}).values():
            widget = tabs.remove_panel_id(panel_id)
            if widget is not None:
                return widget
        # Or from a floating dialog.
        dlg = getattr(self, "_floating_panels", {}).pop(panel_id, None)
        if dlg is not None:
            dlg._closing_from_owner = True
            layout = dlg.layout()
            widget = None
            if layout and layout.count() > 0:
                item = layout.takeAt(0)
                widget = item.widget() if item else None
                if widget is not None:
                    widget.setParent(None)
            dlg.hide()
            dlg.deleteLater()
            return widget
        return None

    def _update_panel_visibility(self):
        if hasattr(self, "bottom_tabs"):
            self.bottom_tabs.setVisible(self.bottom_tabs.count() > 0)
        for tabs in getattr(self, "_panel_tabs", {}).values():
            tabs.setVisible(tabs.count() > 0 or tabs is getattr(self, "bottom_tabs", None))

    def move_panel(self, panel_id: str, target_area: str = "right", index: int | None = None):
        """Move a blueprint utility panel between Photoshop-like tab groups."""
        panel_id = str(panel_id or "")
        if panel_id not in getattr(self, "_panel_titles", {}):
            return
        target_area = target_area if target_area in getattr(self, "_panel_tabs", {}) else "right"
        widget = self._remove_panel_widget(panel_id)
        if widget is None:
            return
        title = self._panel_titles.get(panel_id, panel_id)
        self._panel_tabs[target_area].add_panel_tab(panel_id, widget, title, index)
        self._update_panel_visibility()

    def float_panel(self, panel_id: str):
        """Detach a panel into a floating window, then return it to the right tabs on close."""
        panel_id = str(panel_id or "")
        if panel_id not in getattr(self, "_panel_titles", {}):
            return
        widget = self._remove_panel_widget(panel_id)
        if widget is None:
            return
        title = self._panel_titles.get(panel_id, panel_id)
        dlg = FloatingPanelDialog(self, panel_id, title, widget)
        self._floating_panels[panel_id] = dlg
        self._update_panel_visibility()
        dlg.show()

    def load_graph(self, graph: BlueprintGraph):
        self.graph = sync_graph_with_site_blocks(graph, self.settings)
        self.name_edit.setText(graph.name)
        self.view.clear_graph()
        for node in self.graph.nodes:
            spec = self.registry.get(node.type_id)
            item = NodeItem(node, spec.color if spec else "#334155", spec.category if spec else "")
            self.view.node_items[node.id] = item
            self.view.scene_obj.addItem(item)
        self.add_comment_frames()
        self.refresh_edges()
        self.center_on_graph(reset_zoom=len(self.graph.nodes) <= 25)
        self.validate_graph(show_ok=False)

    def beautify_layout(self):
        """Internal/layout tool used by presets; no separate scary UI button."""
        self.sync_positions_from_scene()
        self.graph = _apply_wide_standard_layout(self.graph, force=True)
        self.load_graph(self.graph)
        self.center_on_graph(reset_zoom=True)

    def sync_positions_from_scene(self):
        for node in self.graph.nodes:
            item = self.view.node_items.get(node.id)
            if item:
                pos = item.pos()
                # Keep the canvas effectively infinite: if a user drags a block
                # close to the current giant scene edge, grow the scene instead
                # of letting them feel a hard border.
                try:
                    self.view.ensure_scene_contains_point(pos)
                    self.view.ensure_scene_contains_point(pos + QPointF(NODE_W, max(120, item.rect().height())))
                except Exception:
                    pass
                node.x = float(pos.x())
                node.y = float(pos.y())

    def center_on_graph(self, reset_zoom: bool = False):
        """Move viewport back to the current graph inside the giant canvas."""
        rect = self.view.scene_obj.itemsBoundingRect()
        if not rect.isValid() or rect.isNull():
            self.view.centerOn(0, 0)
            return
        if reset_zoom:
            self.view.resetTransform()
        # Do not zoom-to-fit the whole million-unit world; center only on graph.
        self.view.centerOn(rect.center())

    def refresh_edges(self):
        for item in list(self.view.edge_items):
            self.view.scene_obj.removeItem(item)
        self.view.edge_items = []
        for edge in self.graph.edges:
            src_item = self.view.node_items.get(edge.source_node)
            dst_item = self.view.node_items.get(edge.target_node)
            if not src_item or not dst_item:
                continue
            src = src_item.port_scene_pos("out", edge.source_port)
            dst = dst_item.port_scene_pos("in", edge.target_port)
            src_port = src_item.node.output_port(edge.source_port)
            color = _edge_color_for_port(src_port.type if src_port else "Any", edge.source_port)
            ei = EdgeItem(edge, src, dst, color)
            self.view.scene_obj.addItem(ei)
            self.view.edge_items.append(ei)

    def add_palette_node(self, item: QListWidgetItem):
        type_id = str(item.data(Qt.UserRole) or "")
        n = len(self.graph.nodes) + 1
        self.add_palette_type_at(type_id, QPointF(160 + n * 20, 120 + n * 20))

    def add_palette_type_at(self, type_id: str, scene_pos: QPointF | None = None):
        spec = self.registry.get(type_id)
        if not spec:
            return
        n = len(self.graph.nodes) + 1
        scene_pos = scene_pos or QPointF(160 + n * 20, 120 + n * 20)
        node = BlueprintNode(
            id=f"n{int(time.time() * 1000)}_{n}",
            type_id=type_id,
            title=spec.title,
            x=float(scene_pos.x()),
            y=float(scene_pos.y()),
            config=dict(spec.default_config),
            workers=max(1, int(spec.default_config.get("workers", spec.default_workers) or 1)),
            min_delay_ms=max(0, int(spec.default_config.get("min_delay_ms", spec.default_min_delay_ms) or 0)),
            timeout_ms=max(1, int(spec.default_config.get("timeout_ms", spec.default_timeout_ms) or 30000)),
            retry_count=max(0, int(spec.default_config.get("retry_count", spec.default_retry_count) or 0)),
            rate_group=str(spec.default_config.get("rate_group", spec.rate_group) or ""),
            enabled=bool(spec.default_config.get("enabled", True)),
            on_disabled=str(spec.default_config.get("on_disabled", "skip") or "skip"),
            inputs=list(spec.inputs),
            outputs=list(spec.outputs),
        )
        self.graph.nodes.append(node)
        ni = NodeItem(node, spec.color, spec.category)
        self.view.node_items[node.id] = ni
        self.view.scene_obj.addItem(ni)
        ni.setSelected(True)
        self.selected_node_id = node.id
        self.add_comment_frames()
        self.validate_graph(show_ok=False)

    def add_edge(self, source: PortItem, target: PortItem):
        if source.node_id == target.node_id:
            self.output.setPlainText("Нельзя соединять блок сам с собой")
            return
        edge = BlueprintEdge(
            id=f"e{int(time.time()*1000)}",
            source_node=source.node_id,
            source_port=source.port_name,
            target_node=target.node_id,
            target_port=target.port_name,
        )
        # Replace existing non-multi input connection unless target input is multi.
        node = self.graph.node_map().get(target.node_id)
        port = node.input_port(target.port_name) if node else None
        if port and not port.multi:
            self.graph.edges = [e for e in self.graph.edges if not (e.target_node == target.node_id and e.target_port == target.port_name)]
        self.graph.edges.append(edge)
        self.refresh_edges()
        self.validate_graph(show_ok=False)

    def focus_palette_search(self):
        try:
            self.palette_search.setFocus()
            self.palette_search.selectAll()
        except Exception:
            pass

    def open_canvas_context_menu(self, global_pos, scene_pos: QPointF):
        menu = QMenu(self)
        search_action = menu.addAction("Найти блок в палитре")
        menu.addSeparator()
        categories: dict[str, QMenu] = {}
        specs = sorted(self.registry.values(), key=lambda s: (s.category, s.title))
        for spec in specs:
            cat = categories.get(spec.category)
            if cat is None:
                cat = menu.addMenu(spec.category)
                categories[spec.category] = cat
            act = cat.addAction(spec.title)
            act.setData(spec.type_id)
        chosen = menu.exec(global_pos)
        if not chosen:
            return
        if chosen == search_action:
            self.focus_palette_search()
            return
        type_id = str(chosen.data() or "")
        if type_id:
            self.add_palette_type_at(type_id, scene_pos)

    def duplicate_node(self, node_id: str, scene_pos: QPointF | None = None):
        node = self.graph.node_map().get(str(node_id))
        if not node:
            return
        data = node.to_dict()
        data["id"] = f"n{int(time.time()*1000)}_copy"
        data["title"] = f"{node.title} copy"
        data["x"] = float(scene_pos.x() if scene_pos is not None else node.x + 40)
        data["y"] = float(scene_pos.y() if scene_pos is not None else node.y + 40)
        clone = BlueprintNode.from_dict(data, self.registry)
        self.graph.nodes.append(clone)
        self.load_graph(self.graph)

    def toggle_node_enabled(self, node_id: str):
        node = self.graph.node_map().get(str(node_id))
        if not node:
            return
        node.enabled = not bool(getattr(node, "enabled", True))
        node.config["enabled"] = node.enabled
        self.load_graph(self.graph)

    def add_comment_frames(self):
        # Rebuild only generated visual frames; they are not saved to JSON.
        for item in list(getattr(self.view, "comment_items", [])):
            try:
                self.view.scene_obj.removeItem(item)
            except Exception:
                pass
        self.view.comment_items = []
        groups = {
            "MD5 сайты": ("exact_md5_site", QColor("#22c55e")),
            "Reverse fallback": ("reverse_", QColor("#a78bfa")),
            "Сохранение / Merge": ("save_merge", QColor("#f97316")),
            "Side queues / relay": ("side", QColor("#38bdf8")),
        }
        for title, (kind_marker, color) in groups.items():
            items = []
            for node in self.graph.nodes:
                spec = self.registry.get(node.type_id)
                kind = str(spec.kind if spec else node.config.get("kind", ""))
                category = str(spec.category if spec else "")
                if kind_marker == "save_merge":
                    ok = kind in {"merge_tags", "save_found", "save_no_match"} or category == "Сохранение"
                elif kind_marker == "side":
                    ok = category == "Боковые очереди" or kind in {"rule34_image_key", "md5_relay_all"}
                elif kind_marker == "reverse_":
                    ok = kind.startswith("reverse_")
                else:
                    ok = kind == kind_marker
                if ok and node.id in self.view.node_items:
                    items.append(self.view.node_items[node.id])
            if not items:
                continue
            rect = QRectF()
            for item in items:
                item_rect = item.mapRectToScene(item.rect())
                rect = item_rect if rect.isNull() else rect.united(item_rect)
            rect = rect.adjusted(-34, -54, 34, 34)
            frame = CommentFrameItem(title, rect, color)
            self.view.scene_obj.addItem(frame)
            self.view.comment_items.append(frame)

    def update_inspector_from_selection(self):
        items = [i for i in self.view.scene_obj.selectedItems() if isinstance(i, NodeItem)]
        if not items:
            self.selected_node_id = None
            self.node_id_edit.clear(); self.node_type_edit.clear(); self.node_title_edit.clear()
            self.node_inputs_edit.clear(); self.node_outputs_edit.clear(); self.node_config_edit.clear()
            self.node_workers_spin.setValue(1); self.node_delay_spin.setValue(0); self.node_timeout_spin.setValue(30000); self.node_retry_spin.setValue(0); self.node_rate_edit.clear(); self.node_enabled_cb.setChecked(False)
            return
        node = items[0].node
        self.selected_node_id = node.id
        self.node_id_edit.setText(node.id)
        self.node_type_edit.setText(node.type_id)
        self.node_title_edit.setText(node.title)
        self.node_inputs_edit.setPlainText(json.dumps([p.to_dict() for p in node.inputs], ensure_ascii=False, indent=2))
        self.node_outputs_edit.setPlainText(json.dumps([p.to_dict() for p in node.outputs], ensure_ascii=False, indent=2))
        self.node_workers_spin.setValue(max(1, int(getattr(node, "workers", 1) or 1)))
        self.node_delay_spin.setValue(max(0, int(getattr(node, "min_delay_ms", 0) or 0)))
        self.node_timeout_spin.setValue(max(1, int(getattr(node, "timeout_ms", 30000) or 30000)))
        self.node_retry_spin.setValue(max(0, int(getattr(node, "retry_count", 0) or 0)))
        self.node_rate_edit.setText(str(getattr(node, "rate_group", "") or ""))
        self.node_enabled_cb.setChecked(bool(getattr(node, "enabled", True)))
        self.node_config_edit.setPlainText(json.dumps(node.config, ensure_ascii=False, indent=2))

    def apply_inspector(self):
        if not self.selected_node_id:
            return
        node = self.graph.node_map().get(self.selected_node_id)
        if not node:
            return
        try:
            inputs = [PortSpec.from_dict(x) for x in (_safe_json_loads(self.node_inputs_edit.toPlainText(), []) or [])]
            outputs = [PortSpec.from_dict(x) for x in (_safe_json_loads(self.node_outputs_edit.toPlainText(), []) or [])]
            config = dict(_safe_json_loads(self.node_config_edit.toPlainText(), {}) or {})
        except Exception as e:
            QMessageBox.warning(self, "Ошибка JSON", str(e))
            return
        node.type_id = self.node_type_edit.text().strip() or node.type_id
        node.title = self.node_title_edit.text().strip() or node.title
        node.workers = int(self.node_workers_spin.value())
        node.min_delay_ms = int(self.node_delay_spin.value())
        node.timeout_ms = int(self.node_timeout_spin.value())
        node.retry_count = int(self.node_retry_spin.value())
        node.rate_group = self.node_rate_edit.text().strip()
        node.enabled = bool(self.node_enabled_cb.isChecked())
        node.on_disabled = str(config.get("on_disabled", getattr(node, "on_disabled", "skip")) or "skip")
        node.inputs = inputs
        node.outputs = outputs
        node.config = config
        node.config.update({"workers": node.workers, "min_delay_ms": node.min_delay_ms, "timeout_ms": node.timeout_ms, "retry_count": node.retry_count, "rate_group": node.rate_group, "enabled": node.enabled, "on_disabled": node.on_disabled})
        self.load_graph(self.graph)

    def delete_selected(self):
        selected_nodes = {i.node.id for i in self.view.scene_obj.selectedItems() if isinstance(i, NodeItem)}
        selected_edges = {i.edge.id for i in self.view.scene_obj.selectedItems() if isinstance(i, EdgeItem)}
        if not selected_nodes and not selected_edges:
            return
        self.graph.nodes = [n for n in self.graph.nodes if n.id not in selected_nodes]
        self.graph.edges = [e for e in self.graph.edges if e.id not in selected_edges and e.source_node not in selected_nodes and e.target_node not in selected_nodes]
        self.load_graph(self.graph)

    def validate_graph(self, show_ok: bool = True) -> bool:
        self.sync_positions_from_scene()
        info = analyze_graph(self.graph, self.registry, full_access=bool(self.full_access_cb.isChecked()))
        errors = list(info.get("errors") or [])
        warnings = list(info.get("warnings") or [])
        if errors:
            self.output.setPlainText("ОШИБКИ:\n" + "\n".join(f"- {x}" for x in errors) + ("\n\nПРЕДУПРЕЖДЕНИЯ:\n" + "\n".join(f"- {x}" for x in warnings) if warnings else ""))
            return False
        if show_ok:
            text = "Граф запускаемый. В full-access предупреждения не блокируют запуск."
            if warnings:
                text += "\n\nПРЕДУПРЕЖДЕНИЯ:\n" + "\n".join(f"- {x}" for x in warnings)
            self.output.setPlainText(text)
        return True

    def compile_and_show(self):
        self.sync_positions_from_scene()
        self.graph.name = self.name_edit.text().strip() or self.graph.name
        plan = compile_blueprint(self.graph, self.registry, full_access=bool(self.full_access_cb.isChecked()))
        text = ["ПЛАН ПАРСЕРА:", f"ok={plan.get('ok')}", f"nodes={plan.get('node_count')} edges={plan.get('edge_count')}", "", plan.get("summary", "")]
        if plan.get("errors"):
            text += ["", "ОШИБКИ:"] + [f"- {x}" for x in plan.get("errors")]
        if plan.get("warnings"):
            text += ["", "ПРЕДУПРЕЖДЕНИЯ:"] + [f"- {x}" for x in plan.get("warnings")]
        if plan.get("site_runtime"):
            text += ["", "Потоки/задержки MD5-блоков:"]
            for domain, rt in plan.get("site_runtime", {}).items():
                text.append(f"- {domain}: workers={rt.get('workers')} delay={rt.get('min_delay_ms')}ms rate={rt.get('rate_group')}")
        if plan.get("reverse_runtime"):
            text += ["", "Потоки/задержки reverse-блоков:"]
            for kind, rt in plan.get("reverse_runtime", {}).items():
                text.append(f"- {kind}: workers={rt.get('workers')} delay={rt.get('min_delay_ms')}ms rate={rt.get('rate_group')}")
        if plan.get("site_order"):
            text += ["", "Порядок MD5-сайтов:"] + [f"{i+1}. {x}" for i, x in enumerate(plan.get("site_order"))]
        if plan.get("reverse_order"):
            text += ["", "Reverse-цепь:"] + [f"{i+1}. {x}" for i, x in enumerate(plan.get("reverse_order"))]
        self.output.setPlainText("\n".join(text))

    def save_graph(self):
        self.sync_positions_from_scene()
        self.graph.name = self.name_edit.text().strip() or self.graph.name
        self.graph.active = self.active_cb.isChecked()
        save_active_blueprint(self.graph, self.settings)
        self.settings["parser_blueprint_enabled"] = bool(self.active_cb.isChecked())
        self.settings["parser_blueprint_full_access"] = bool(self.full_access_cb.isChecked())
        save_settings(self.settings)
        self.compile_and_show()
        QMessageBox.information(self, "Blueprint", "Сохранено")

    def reload_graph(self):
        self.reload_palette()
        self.load_graph(load_active_blueprint(self.settings))

    def reset_default(self):
        if QMessageBox.question(self, "Blueprint", "Заменить текущий граф стандартным планом Local Booru? Это не удаляет пользовательские пресеты.") != QMessageBox.Yes:
            return
        self.load_graph(default_blueprint())
        self.output.setPlainText("Загружен стандартный план. Нажми «Сохранить», чтобы сделать его активным.")

    def toggle_active(self, checked: bool):
        self.settings["parser_blueprint_enabled"] = bool(checked)
        save_settings(self.settings)

    def toggle_full_access(self, checked: bool):
        self.settings["parser_blueprint_full_access"] = bool(checked)
        save_settings(self.settings)
        self.validate_graph(show_ok=False)

    def open_block_editor(self, node_id: str | None):
        if not node_id:
            return
        node = self.graph.node_map().get(str(node_id))
        if not node:
            return
        dlg = BlockEditorDialog(self, node)
        if dlg.exec() == QDialog.Accepted:
            try:
                dlg.apply_to_node(node)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка блока", str(e))
                return
            self.load_graph(self.graph)

    def create_module(self):
        type_id, ok = QInputDialog.getText(self, "Новый модуль", "ID модуля латиницей, например custom_my_lookup:")
        if not ok or not type_id.strip():
            return
        title, ok = QInputDialog.getText(self, "Новый модуль", "Название блока:", text=type_id.strip())
        if not ok:
            return
        path = create_custom_module(self.settings, type_id.strip(), title.strip() or type_id.strip())
        self.reload_palette()
        QMessageBox.information(self, "Новый модуль", f"Создан JSON модуля:\n{path}\n\nЕго можно редактировать вручную или через инспектор после добавления на холст.")

    def _kind_of(self, node: BlueprintNode) -> str:
        spec = self.registry.get(node.type_id)
        return str(spec.kind if spec else node.config.get("kind", "custom") or "custom")

    def _md5_nodes_for_order(self) -> list[BlueprintNode]:
        return sorted([n for n in self.graph.nodes if self._kind_of(n) == "exact_md5_site"], key=lambda n: (float(n.x), float(n.y), n.id))

    def _reverse_nodes_for_order(self) -> list[BlueprintNode]:
        return sorted([n for n in self.graph.nodes if self._kind_of(n).startswith("reverse_")], key=lambda n: (float(n.x), float(n.y), n.id))

    def _add_chain_edge(self, src: str, sp: str, dst: str, tp: str):
        if not src or not dst:
            return
        # Avoid exact duplicate edge spam.
        for e in self.graph.edges:
            if e.source_node == src and e.source_port == sp and e.target_node == dst and e.target_port == tp:
                return
        self.graph.edges.append(BlueprintEdge(id=f"e{int(time.time()*1000)}_{len(self.graph.edges)}", source_node=src, source_port=sp, target_node=dst, target_port=tp))

    def _rewire_simple_order(self, md5_order: list[str], reverse_order: list[str]):
        node_map = self.graph.node_map()
        md5_ids = {n.id for n in self._md5_nodes_for_order()}
        rev_ids = {n.id for n in self._reverse_nodes_for_order()}
        preflight = next((n for n in self.graph.nodes if n.type_id == "local_preflight"), None)
        nomatch = next((n for n in self.graph.nodes if n.type_id == "save_no_match"), None)
        # Remove linear order edges only; keep match/variant/merge edges untouched.
        self.graph.edges = [e for e in self.graph.edges if not (
            (preflight and e.source_node == preflight.id and e.source_port == "hash" and e.target_node in md5_ids)
            or (e.source_node in md5_ids and e.source_port == "miss")
            or (e.target_node in md5_ids and e.target_port == "hash" and e.source_node in md5_ids)
            or (e.source_node in rev_ids and e.source_port == "miss")
            or (e.target_node in rev_ids and e.target_port == "miss" and (e.source_node in md5_ids or e.source_node in rev_ids))
        )]
        md5_nodes = [node_map[x] for x in md5_order if x in node_map and x in md5_ids]
        rev_nodes = [node_map[x] for x in reverse_order if x in node_map and x in rev_ids]
        # Re-layout lightly so the graph view matches the list order.
        for i, n in enumerate(md5_nodes):
            n.x = 820
            n.y = 40 + i * 210
        for i, n in enumerate(rev_nodes):
            n.x = 1230 + i * 410
            n.y = 1160
        if preflight and md5_nodes:
            prev = preflight.id; prev_port = "hash"
            for n in md5_nodes:
                self._add_chain_edge(prev, prev_port, n.id, "hash")
                prev = n.id; prev_port = "miss"
            if rev_nodes:
                self._add_chain_edge(prev, prev_port, rev_nodes[0].id, "miss")
            elif nomatch:
                self._add_chain_edge(prev, prev_port, nomatch.id, "miss")
        if rev_nodes:
            for a, b in zip(rev_nodes, rev_nodes[1:]):
                self._add_chain_edge(a.id, "miss", b.id, "miss")
            if nomatch:
                self._add_chain_edge(rev_nodes[-1].id, "miss", nomatch.id, "miss")

    def open_simple_order(self):
        self.sync_positions_from_scene()
        dlg = SimpleOrderDialog(self, self._md5_nodes_for_order(), self._reverse_nodes_for_order())
        if dlg.exec() == QDialog.Accepted:
            self._rewire_simple_order(dlg.md5_order(), dlg.reverse_order())
            self.load_graph(self.graph)
            self.output.setPlainText("Простой порядок применён к тому же blueprint-графу. Нажми «Сохранить», чтобы закрепить изменения.")

    def open_presets(self):
        dlg = PresetDialog(self, self.settings)
        if dlg.exec() == QDialog.Accepted and dlg.selected_preset_id:
            try:
                self.load_graph(load_preset(self.settings, dlg.selected_preset_id))
                self.output.setPlainText(f"Пресет загружен: {dlg.selected_preset_id}\nНажми «Сохранить», чтобы сделать его активным планом парсера.")
            except Exception as e:
                QMessageBox.warning(self, "Пресеты", str(e))

    def save_as_preset(self):
        self.sync_positions_from_scene()
        name, ok = QInputDialog.getText(self, "Сохранить как пресет", "Название пресета:", text=self.name_edit.text().strip() or self.graph.name)
        if not ok or not name.strip():
            return
        self.graph.name = self.name_edit.text().strip() or self.graph.name
        path = save_preset(self.graph, self.settings, name.strip())
        QMessageBox.information(self, "Пресеты", f"Пресет сохранён:\n{path}")

    def export_json(self):
        self.sync_positions_from_scene()
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт blueprint", "parser_blueprint.json", "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(self.graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def refresh(self):
        self.reload_palette()
        self.reload_graph()
