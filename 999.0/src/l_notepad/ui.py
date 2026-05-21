# -*- coding: utf-8 -*-

from __future__ import annotations

import subprocess
import sys
import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime

from pathlib import Path

from PySide6 import QtCore, QtGui, QtUiTools, QtWidgets

from .api_client import ApiError, NotepadApi, NoteDto


SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_MODELS_URL = "https://api.siliconflow.cn/v1/models"
SILICONFLOW_PRICING_URL = "https://www.siliconflow.com/pricing"
DEFAULT_SILICONFLOW_MODEL = "Qwen/Qwen2.5-7B-Instruct"
DEFAULT_MODEL_PRESETS = [
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",
    "deepseek-ai/DeepSeek-V4-Flash",
    "Pro/zai-org/GLM-4.7",
]
SILICONFLOW_API_KEY = os.environ.get(
    "SILICONFLOW_API_KEY",
    "sk-gzwtmzfhglvibdbvrttmsuuqsyyjxghxlxzdhubdefmshqoi",
)
ASK_AI_ITEM_ID = "__ask_ai__"
ASK_AI_SESSION_PREFIX = "__ask_ai_session__:"
_SILICONFLOW_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass(frozen=True)
class ModelPrice:
    input_per_m: float
    output_per_m: float
    currency: str = "$"


def _load_stylesheet() -> str:
    """从文件加载样式表"""
    style_path = Path(__file__).resolve().parent / "style.qss"
    if style_path.exists():
        return style_path.read_text(encoding="utf-8")
    return ""


@dataclass
class UiState:
    current_note_id: int | None = None
    dirty: bool = False


@dataclass
class AiSession:
    session_id: str
    title: str
    messages: list[dict[str, str]]
    draft_prompt: str = ""
    streaming_text: str = ""
    in_flight: bool = False


class AiBridge(QtCore.QObject):
    chunk = QtCore.Signal(int, str, str)
    finished = QtCore.Signal(int, str, bool, str, str)


class ModelBridge(QtCore.QObject):
    finished = QtCore.Signal(bool, object)


class PriceBridge(QtCore.QObject):
    finished = QtCore.Signal(bool, object)


class _LineNumberArea(QtWidgets.QWidget):
    """Side widget that draws line numbers for a LineNumberTextEdit."""

    def __init__(self, editor: "LineNumberTextEdit") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        self._editor.line_number_area_paint(event)


class LineNumberTextEdit(QtWidgets.QTextEdit):
    """QTextEdit with a line-number gutter on the left side."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)
        self.setAcceptDrops(True)
        self._line_number_area = _LineNumberArea(self)
        self._line_bg = QtGui.QColor("#0B1020")
        self._line_fg = QtGui.QColor("#4B5563")
        self._line_fg_current = QtGui.QColor("#89DDFF")
        self.document().blockCountChanged.connect(self._update_line_number_area_width)
        self.verticalScrollBar().valueChanged.connect(self._line_number_area.update)
        self.textChanged.connect(self._line_number_area.update)
        self.cursorPositionChanged.connect(self._line_number_area.update)
        self._update_line_number_area_width()

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.document().blockCount()))))
        fm = self.fontMetrics()
        return 10 + fm.horizontalAdvance("9") * digits + 6

    def _update_line_number_area_width(self) -> None:
        w = self.line_number_area_width()
        self.setViewportMargins(w, 0, 0, 0)
        self._line_number_area.setGeometry(
            QtCore.QRect(0, 0, w, self.viewport().height())
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        cr = self.contentsRect()
        w = self.line_number_area_width()
        self._line_number_area.setGeometry(
            QtCore.QRect(cr.left(), cr.top(), w, cr.height())
        )

    def line_number_area_paint(self, event) -> None:
        painter = QtGui.QPainter(self._line_number_area)
        painter.fillRect(event.rect(), self._line_bg)

        block = self.document().begin()
        block_num = 1
        top_offset = self.verticalScrollBar().value()
        current_block_num = self.textCursor().blockNumber() + 1

        while block.isValid():
            layout = block.layout()
            if layout is None:
                block = block.next()
                block_num += 1
                continue
            block_top = layout.position().y() - top_offset + self.contentsMargins().top()
            block_height = layout.boundingRect().height()
            if block_top > event.rect().bottom():
                break
            if block_top + block_height >= event.rect().top():
                if block_num == current_block_num:
                    painter.setPen(self._line_fg_current)
                else:
                    painter.setPen(self._line_fg)
                painter.setFont(self.font())
                paint_rect = QtCore.QRectF(
                    0, block_top, self._line_number_area.width() - 6, block_height
                )
                painter.drawText(
                    paint_rect,
                    QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignTop,
                    str(block_num),
                )
            block = block.next()
            block_num += 1
        painter.end()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.FontChange:
            self._update_line_number_area_width()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = self.createStandardContextMenu()
        # 检测右键位置是否有图片
        cursor = self.cursorForPosition(event.pos())
        fmt = cursor.charFormat()
        if fmt.isImageFormat():
            img_name = fmt.toImageFormat().name()
            url = QtCore.QUrl(img_name)
            local_path = url.toLocalFile() if url.isLocalFile() else img_name
            if local_path and Path(local_path).is_file():
                menu.addSeparator()
                act_edit = menu.addAction("用画图编辑图片")
                act_open_folder = menu.addAction("在资源管理器中显示")
                act_edit.triggered.connect(
                    lambda _=False, p=local_path: subprocess.Popen(
                        ["mspaint", p], creationflags=0x00000008  # DETACHED_PROCESS
                    )
                )
                act_open_folder.triggered.connect(
                    lambda _=False, p=local_path: subprocess.Popen(
                        ["explorer", "/select,", p.replace("/", "\\")]
                    )
                )
        menu.exec(event.globalPos())


class TerminalCodeHighlighter(QtGui.QSyntaxHighlighter):
    """Terminal-inspired highlighting for Python/log snippets."""

    def __init__(self, document: QtGui.QTextDocument) -> None:
        super().__init__(document)
        self._rules = [
            (r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b", self._fmt("#FF5C8A", bold=True)),
            (r"\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]", self._fmt("#8BE9FD")),
            (r"\[[0-9]{4}-[0-9]{2}-[0-9]{2}[^\]]*\]", self._fmt("#B6E880")),
            (r"\[arg[0-9]+\]", self._fmt("#BD93F9", bold=True)),
            (r"\b(File|Traceback|Exception|Error)\b", self._fmt("#FF6B6B", bold=True)),
            (r"\b(fn|def|class|return|if|else|elif|for|while|try|except|with|as|import|from|in|is|not|and|or|None|True|False)\b", self._fmt("#82AAFF")),
            (r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()", self._fmt("#7FDBCA")),
            (r"\b\d+(?:\.\d+)?\b", self._fmt("#F78C6C")),
            (r"(?<!\w)[A-Za-z]:[\\/][^\s,\)\]]+", self._fmt("#C3E88D")),
            (r"(?<!\w)/(?:[^\s,\)\]]+/)+[^\s,\)\]]+", self._fmt("#C3E88D")),
            (r"@[A-Za-z_][A-Za-z0-9_]*", self._fmt("#FFCB6B")),
            (r"#[^\n]*", self._fmt("#637777", italic=True)),
        ]
        self._string_rules = [
            (r'"(?:\\.|[^"\\])*"', self._fmt("#ECC48D")),
            (r"'(?:\\.|[^'\\])*'", self._fmt("#ECC48D")),
        ]
        self._bracket_format = self._fmt("#89DDFF")

    @staticmethod
    def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QtGui.QTextCharFormat:
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QtGui.QColor(color))
        if bold:
            fmt.setFontWeight(QtGui.QFont.Weight.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        for pattern, fmt in self._rules:
            self._apply_regex(pattern, text, fmt)
        for pattern, fmt in self._string_rules:
            self._apply_regex(pattern, text, fmt)
        for idx, ch in enumerate(text):
            if ch in "{}[]()":
                self.setFormat(idx, 1, self._bracket_format)

    def _apply_regex(self, pattern: str, text: str, fmt: QtGui.QTextCharFormat) -> None:
        expr = QtCore.QRegularExpression(pattern)
        match = expr.match(text)
        while match.hasMatch():
            start = match.capturedStart()
            length = match.capturedLength()
            if start >= 0 and length > 0:
                self.setFormat(start, length, fmt)
            match = expr.match(text, start + max(length, 1))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, api: NotepadApi, restart_callback=None, hotkey_interval_callback=None) -> None:
        super().__init__()
        self.api = api
        self._restart_callback = restart_callback
        self._hotkey_interval_callback = hotkey_interval_callback
        self.state = UiState()
        self._allow_close = False
        self._settings = QtCore.QSettings("Lugwit", "l_notepad_pc")
        self._favorite_order: list[int] = []
        self._last_open_note_id: int | None = None
        self._selected_ai_model = DEFAULT_SILICONFLOW_MODEL
        self._model_prices: dict[str, ModelPrice] = {}
        self._ai_input_tokens = 0
        self._ai_output_tokens = 0
        self._ai_stream_text = ""
        self._text_font_size = 10
        self._ask_ai_mode = False
        self._ai_sessions: dict[str, AiSession] = {}
        self._current_ai_session_id: str | None = None
        self._ai_request_seq = 0
        self._active_ai_request_id: int | None = None
        self._in_selection_changed = False
        self._ai_bridge = AiBridge()
        self._ai_bridge.chunk.connect(self._on_ai_chunk)
        self._ai_bridge.finished.connect(self._on_ai_finished)
        self._model_bridge = ModelBridge()
        self._model_bridge.finished.connect(self._on_models_loaded)
        self._price_bridge = PriceBridge()
        self._price_bridge.finished.connect(self._on_prices_loaded)
        self.setStyleSheet(_load_stylesheet())
        self.setWindowTitle("L Notepad")
        self.resize(980, 640)
        icon_path = Path(__file__).resolve().parent / "static" / "favicon.svg"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self._load_settings()
        self._restore_window_state()

        ui_path = Path(__file__).resolve().parent / "main_window.ui"
        loader = QtUiTools.QUiLoader(self)
        ui_file = QtCore.QFile(str(ui_path))
        if not ui_file.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError(f"Failed to open ui file: {ui_path}")
        try:
            loaded = loader.load(ui_file, self)
        finally:
            ui_file.close()
        if loaded is None:
            raise RuntimeError(f"Failed to load ui file: {ui_path}")
        self.setCentralWidget(loaded.findChild(QtWidgets.QWidget, "centralwidget"))

        self.tabs = self.findChild(QtWidgets.QTabWidget, "tabs")
        self.search_edit = self.findChild(QtWidgets.QLineEdit, "search_edit")
        self.notes_list = self.findChild(QtWidgets.QListWidget, "notes_list")
        self.title_edit = self.findChild(QtWidgets.QLineEdit, "title_edit")
        self.model_combo = self.findChild(QtWidgets.QComboBox, "model_combo")
        self.btn_refresh_models = self.findChild(QtWidgets.QPushButton, "btn_refresh_models")
        self.label_input_tokens = self.findChild(QtWidgets.QLabel, "label_input_tokens")
        self.label_output_tokens = self.findChild(QtWidgets.QLabel, "label_output_tokens")
        self.label_cost = self.findChild(QtWidgets.QLabel, "label_cost")
        self.label_price_source = self.findChild(QtWidgets.QLabel, "label_price_source")
        self.content_edit = self.findChild(QtWidgets.QTextEdit, "content_edit")
        self.ai_answer_edit = self.findChild(QtWidgets.QTextEdit, "ai_answer_edit")
        self.ai_tabs = self.findChild(QtWidgets.QTabWidget, "ai_tabs")
        self.btn_new = self.findChild(QtWidgets.QPushButton, "btn_new")
        self.btn_save = self.findChild(QtWidgets.QPushButton, "btn_save")
        self.btn_delete = self.findChild(QtWidgets.QPushButton, "btn_delete")
        self.btn_refresh = self.findChild(QtWidgets.QPushButton, "btn_refresh")
        self.btn_favorite = self.findChild(QtWidgets.QPushButton, "btn_favorite")
        self.btn_ai_ask = self.findChild(QtWidgets.QPushButton, "btn_ai_ask")
        self.btn_restart = self.findChild(QtWidgets.QPushButton, "btn_restart")
        self.btn_ctrl_interval = self.findChild(QtWidgets.QPushButton, "btn_ctrl_interval")
        self.log_view = self.findChild(QtWidgets.QTextEdit, "log_view")
        splitter = self.findChild(QtWidgets.QSplitter, "splitter_main")
        if splitter is not None:
            splitter.setStretchFactor(0, 0)
            splitter.setStretchFactor(1, 1)

        required_widgets = [
            self.tabs,
            self.search_edit,
            self.notes_list,
            self.title_edit,
            self.model_combo,
            self.btn_refresh_models,
            self.label_input_tokens,
            self.label_output_tokens,
            self.label_cost,
            self.label_price_source,
            self.content_edit,
            self.ai_answer_edit,
            self.btn_new,
            self.btn_save,
            self.btn_delete,
            self.btn_refresh,
            self.btn_favorite,
            self.btn_ai_ask,
            self.btn_restart,
            self.btn_ctrl_interval,
            self.log_view,
            self.ai_tabs,
        ]
        if any(w is None for w in required_widgets):
            raise RuntimeError("main_window.ui missing required widget objectName(s)")

        # 用带行号的 LineNumberTextEdit 替换 .ui 加载的普通 QTextEdit
        old_content_edit = self.content_edit
        parent_layout = old_content_edit.parentWidget().layout() if old_content_edit.parentWidget() else None
        new_content_edit = LineNumberTextEdit(old_content_edit.parentWidget())
        new_content_edit.setObjectName("CodeEditor")
        if parent_layout is not None:
            idx = parent_layout.indexOf(old_content_edit)
            parent_layout.removeWidget(old_content_edit)
            old_content_edit.hide()
            old_content_edit.deleteLater()
            parent_layout.insertWidget(idx, new_content_edit)
        self.content_edit = new_content_edit

        # 初始化 .ui 文件中的 "问AI" item data
        if self.notes_list.count() > 0:
            first_item = self.notes_list.item(0)
            if first_item.text().startswith("问AI"):
                first_item.setData(QtCore.Qt.ItemDataRole.UserRole, ASK_AI_ITEM_ID)
                font = first_item.font()
                font.setBold(True)
                first_item.setFont(font)
                first_item.setFlags(first_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsDragEnabled)
                first_item.setSizeHint(QtCore.QSize(0, 36))

        self.search_edit.textChanged.connect(self._apply_filter)
        self.notes_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.notes_list.itemDoubleClicked.connect(self._rename_note_from_item)
        self.notes_list.model().rowsMoved.connect(self._on_notes_rows_moved)

        # AI 标签页设置
        self.ai_tabs.tabCloseRequested.connect(self._on_ai_tab_close_requested)
        self.ai_tabs.currentChanged.connect(self._on_ai_tab_changed)
        # 为标签栏添加右键菜单
        self.ai_tabs.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.ai_tabs.customContextMenuRequested.connect(self._on_ai_tabs_context_menu)
        # 设置关闭按钮图标（X符号）
        self._setup_ai_tab_close_buttons()

        self.title_edit.textEdited.connect(self._mark_dirty)
        self.model_combo.clear()
        self.model_combo.addItems(DEFAULT_MODEL_PRESETS)
        self.model_combo.setCurrentText(self._selected_ai_model)
        self.model_combo.currentTextChanged.connect(self._on_ai_model_changed)
        self.btn_refresh_models.clicked.connect(self._refresh_ai_models)

        self.content_edit.textChanged.connect(self._mark_dirty)
        self.content_edit.textChanged.connect(self._update_realtime_token_stats)
        self._content_highlighter = TerminalCodeHighlighter(self.content_edit.document())

        self.ai_answer_edit.setObjectName("AiAnswerViewer")
        self._ai_answer_highlighter = TerminalCodeHighlighter(self.ai_answer_edit.document())

        self.btn_save.setObjectName("PrimaryButton")
        self.btn_ai_ask.setObjectName("PrimaryButton")
        self.btn_delete.setObjectName("DangerButton")

        self.btn_new.clicked.connect(self._new_note)
        self.btn_save.clicked.connect(self._save_note)
        self.btn_delete.clicked.connect(self._delete_note)
        self.btn_refresh.clicked.connect(self.refresh_notes)
        self.btn_favorite.clicked.connect(self._toggle_favorite_current)
        self.btn_ai_ask.clicked.connect(self._ask_ai)
        self.btn_restart.clicked.connect(self._restart_app)
        self.btn_ctrl_interval.clicked.connect(self._configure_ctrl_double_interval)

        self.log_view.setObjectName("LogViewer")
        self._log_highlighter = TerminalCodeHighlighter(self.log_view.document())
        self._apply_text_font_size()
        for editor in (self.content_edit, self.ai_answer_edit, self.log_view):
            editor.installEventFilter(self)
            editor.viewport().installEventFilter(self)

        self.status = self.statusBar()
        self._refresh_official_prices()
        # 恢复 AI 标签页
        self._restore_ai_tabs()
        self.refresh_notes()
        self.append_log("日志窗口已初始化")
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._save_settings)

    def refresh_notes(self) -> None:
        try:
            notes = self.api.list_notes()
        except ApiError as e:
            self._show_error(str(e))
            return

        current_id = self.state.current_note_id
        if current_id is None:
            current_id = self._last_open_note_id
        query = self.search_edit.text().strip()
        notes_sorted = self._sort_notes(notes)
        self.notes_list.blockSignals(True)
        # 保留固定的"问AI" item（从.ui文件加载），只清除动态添加的笔记items
        for i in range(self.notes_list.count() - 1, -1, -1):
            item = self.notes_list.item(i)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) != ASK_AI_ITEM_ID:
                self.notes_list.takeItem(i)
        # AI sessions 不再显示在左侧列表中，而是在右侧标签页中显示
        for n in notes_sorted:
            if query and query.lower() not in n.title.lower():
                continue
            title = f"※ {n.title}" if self._is_favorite(n.id) else n.title
            item = QtWidgets.QListWidgetItem(f"{title}\n{n.updated_at}")
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, n.id)
            item.setToolTip(f"#{n.id}  {n.updated_at}")
            item.setSizeHint(QtCore.QSize(0, 34))
            self.notes_list.addItem(item)
        self.notes_list.blockSignals(False)

        # AI 会话不再在列表中显示，只选择问AI按钮（第0行）或笔记
        if self._ask_ai_mode:
            self.notes_list.setCurrentRow(0)  # 选择问AI按钮
        elif current_id is not None:
            self._select_note_id(current_id)
        elif self.notes_list.count() > 1:
            self.notes_list.setCurrentRow(1)  # 跳过问AI按钮，选择第一个笔记
        else:
            self._set_editor(None)
        self._update_favorite_button_label()

    def _apply_filter(self) -> None:
        # lightweight local filter; refresh keeps the list consistent
        self.refresh_notes()

    def _select_note_id(self, note_id: int) -> None:
        for i in range(self.notes_list.count()):
            item = self.notes_list.item(i)
            item_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if item_id == ASK_AI_ITEM_ID:
                continue
            if isinstance(item_id, str) and item_id.startswith(ASK_AI_SESSION_PREFIX):
                continue
            if int(item_id) == int(note_id):
                self.notes_list.setCurrentRow(i)
                return

    def _select_ai_session_id(self, session_id: str) -> None:
        target = f"{ASK_AI_SESSION_PREFIX}{session_id}"
        for i in range(self.notes_list.count()):
            item = self.notes_list.item(i)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == target:
                self.notes_list.setCurrentRow(i)
                return

    def _on_selection_changed(self) -> None:
        if self._in_selection_changed:
            return
        self._in_selection_changed = True
        try:
            self._on_selection_changed_inner()
        finally:
            self._in_selection_changed = False

    def _on_selection_changed_inner(self) -> None:
        items = self.notes_list.selectedItems()
        item_id = items[0].data(QtCore.Qt.ItemDataRole.UserRole) if items else None
        if item_id == ASK_AI_ITEM_ID:
            self._auto_save_note("切换到问AI前")
            session = self._new_ai_session(select=True)
            self._set_ai_editor(session.session_id)
            self.refresh_notes()
            # AI 会话不再在列表中显示，不需要选择
            return
        # AI 会话不再在列表中，不再处理 ASK_AI_SESSION_PREFIX

        if self.state.dirty:
            self._auto_save_note("切换日志前")

        if not items:
            self._set_editor(None)
            return

        self._ask_ai_mode = False
        note_id = int(items[0].data(QtCore.Qt.ItemDataRole.UserRole))
        try:
            note = self.api.get_note(note_id)
        except ApiError as e:
            self._show_error(str(e))
            return
        self._set_editor(note)
        self._last_open_note_id = note.id
        self._save_settings()
        self._update_favorite_button_label()

    def _new_note(self) -> None:
        if self._ask_ai_mode:
            session = self._new_ai_session(select=True)
            self.refresh_notes()
            self._select_ai_session_id(session.session_id)
            return
        self._ask_ai_mode = False
        if self.state.dirty and not self._confirm_discard():
            return
        self._set_editor(None)
        self.title_edit.setText("未命名")
        self.content_edit.clear()
        self.state.current_note_id = None
        self.state.dirty = True
        self._update_title()

    def _save_note(self) -> None:
        if self._ask_ai_mode:
            self._ask_ai()
            return
        title = self.title_edit.text().strip() or "未命名"
        content = self._get_content_html()
        try:
            if self.state.current_note_id is None:
                note = self.api.create_note(title=title, content=content)
                self.state.current_note_id = note.id
            else:
                note = self.api.update_note(self.state.current_note_id, title=title, content=content)
        except ApiError as e:
            self._show_error(str(e))
            return

        self.state.dirty = False
        self.status.showMessage(f"已保存：#{note.id}", 2500)
        self.refresh_notes()
        self._update_title()

    def _auto_save_note(self, reason: str) -> None:
        if self._ask_ai_mode or not self.state.dirty:
            return
        title = self.title_edit.text().strip() or "未命名"
        content = self._get_content_html()
        try:
            if self.state.current_note_id is None:
                note = self.api.create_note(title=title, content=content)
            else:
                note = self.api.update_note(self.state.current_note_id, title=title, content=content)
        except ApiError as e:
            self.status.showMessage(f"自动保存失败：{e}", 5000)
            self.append_log(f"{reason} 自动保存失败：{e}")
            return

        self.state.current_note_id = note.id
        self._last_open_note_id = note.id
        self.state.dirty = False
        self.refresh_notes()
        self._select_note_id(note.id)
        self._update_title()
        self._save_settings()
        self.status.showMessage(f"{reason} 已自动保存：#{note.id}", 2500)
        self.append_log(f"{reason} 自动保存日志文件：#{note.id}")

    def _delete_note(self) -> None:
        if self._ask_ai_mode:
            return
        if self.state.current_note_id is None:
            return
        note_id = self.state.current_note_id
        ret = QtWidgets.QMessageBox.question(self, "删除笔记", f"确定删除笔记 #{note_id}？")
        if ret != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.delete_note(note_id)
        except ApiError as e:
            self._show_error(str(e))
            return
        self.state.current_note_id = None
        self.state.dirty = False
        self.refresh_notes()
        self.status.showMessage("已删除", 2500)
        self._update_title()

    def _restart_app(self) -> None:
        try:
            self._auto_save_note("重启前")
            self._allow_close = True
            self._save_settings()
            if self._restart_callback is not None:
                self._restart_callback()
            else:
                subprocess.Popen([sys.executable, "-m", "l_notepad.local_main"])
            self.close()
            QtWidgets.QApplication.quit()
        except Exception as exc:
            self._allow_close = False
            self._show_error(f"重启失败: {exc}")

    def _toggle_favorite_current(self) -> None:
        if self._ask_ai_mode:
            return
        if self.state.current_note_id is None:
            return
        note_id = int(self.state.current_note_id)
        if note_id in self._favorite_order:
            self._favorite_order = [x for x in self._favorite_order if x != note_id]
            self.status.showMessage("已取消置顶/收藏", 2000)
        else:
            self._favorite_order.insert(0, note_id)
            self.status.showMessage("已置顶/收藏", 2000)
        self._save_settings()
        self.refresh_notes()
        self._select_note_id(note_id)
        self._update_favorite_button_label()

    def _on_notes_rows_moved(self, *_args) -> None:
        first = self.notes_list.item(0) if self.notes_list.count() else None
        if first is not None and first.data(QtCore.Qt.ItemDataRole.UserRole) != ASK_AI_ITEM_ID:
            self.refresh_notes()
            return
        # Drag-drop only updates ordering of already-favorited items.
        display_ids: list[int] = []
        for i in range(self.notes_list.count()):
            item = self.notes_list.item(i)
            note_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if note_id is None or note_id == ASK_AI_ITEM_ID:
                continue
            display_ids.append(int(note_id))
        fav_set = set(self._favorite_order)
        if not fav_set:
            return
        reordered_favs = [x for x in display_ids if x in fav_set]
        if reordered_favs:
            self._favorite_order = reordered_favs
            self._save_settings()

    def _rename_note_from_item(self, item: QtWidgets.QListWidgetItem) -> None:
        if item.data(QtCore.Qt.ItemDataRole.UserRole) == ASK_AI_ITEM_ID:
            return
        note_id = int(item.data(QtCore.Qt.ItemDataRole.UserRole))
        try:
            note = self.api.get_note(note_id)
        except ApiError as e:
            self._show_error(str(e))
            return

        new_title, ok = QtWidgets.QInputDialog.getText(
            self,
            "重命名日志文件",
            "新名称：",
            text=note.title,
        )
        if not ok:
            return
        new_title = (new_title or "").strip()
        if not new_title or new_title == note.title:
            return

        try:
            updated = self.api.update_note(note_id, title=new_title, content=note.content)
        except ApiError as e:
            self._show_error(str(e))
            return

        self.state.current_note_id = updated.id
        self.state.dirty = False
        self.refresh_notes()
        self.status.showMessage(f"已重命名：#{updated.id}", 2500)

    def _set_ai_editor(self, session_id: str | None = None) -> None:
        self._ask_ai_mode = True
        self.state.current_note_id = None
        self.state.dirty = False

        # 切换到 AI 模式：显示 ai_tabs，隐藏普通编辑器
        self.ai_tabs.show()
        self.content_edit.hide()
        self.ai_answer_edit.hide()

        session = self._ensure_ai_session(session_id)
        self._current_ai_session_id = session.session_id

        # 检查是否已有对应 session 的标签页
        tab_index = -1
        for i in range(self.ai_tabs.count()):
            widget = self.ai_tabs.widget(i)
            if widget and widget.property("session_id") == session.session_id:
                tab_index = i
                break

        # 如果没有找到对应标签页，创建一个
        if tab_index < 0:
            self._create_ai_tab(session)
        else:
            # 切换到已有标签页
            self.ai_tabs.setCurrentIndex(tab_index)
            # 更新标签页内容
            self._update_ai_tab_content(session)

        self.title_edit.blockSignals(True)
        self.title_edit.setText(session.title)
        self.title_edit.blockSignals(False)

        self.btn_ai_ask.setEnabled(not session.in_flight)
        self.btn_ai_ask.setText("请求中..." if session.in_flight else "问AI")
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_favorite.setEnabled(False)
        self.btn_new.setEnabled(True)
        self._update_title()
        self._update_realtime_token_stats()

    def _update_ai_tab_content(self, session: AiSession) -> None:
        """更新指定会话的标签页内容"""
        content_edit = self._get_ai_tab_content_edit(session.session_id)
        answer_edit = self._get_ai_tab_answer_edit(session.session_id)

        if content_edit:
            content_edit.blockSignals(True)
            if session.draft_prompt:
                content_edit.setPlainText(session.draft_prompt)
            else:
                content_edit.setPlainText(
                    '在这里输入问题，然后点击"问AI"。\n\n'
                    '支持多会话与上下文记忆。\n'
                    '模型：千问 / SiliconFlow。\n'
                )
            content_edit.blockSignals(False)

        if answer_edit:
            answer_edit.setPlainText(self._render_ai_session_text(session))

    def _ask_ai(self) -> None:
        if not self._ask_ai_mode or not self._current_ai_session_id:
            return
        session = self._ai_sessions.get(self._current_ai_session_id)
        if session is None:
            return
        # 使用当前标签页的内容编辑器
        content_edit = self._get_ai_tab_content_edit(self._current_ai_session_id)
        if content_edit is None:
            return
        prompt = content_edit.toPlainText().strip()
        model = self.model_combo.currentText().strip() or DEFAULT_SILICONFLOW_MODEL
        if not prompt:
            self.status.showMessage("请输入问题", 2500)
            return
        if not SILICONFLOW_API_KEY:
            self.status.showMessage("未配置 SiliconFlow API Key", 5000)
            return

        self._ai_request_seq += 1
        request_id = self._ai_request_seq
        self._active_ai_request_id = request_id
        session.in_flight = True
        session.streaming_text = ""
        session.draft_prompt = prompt
        self.btn_ai_ask.setEnabled(False)
        self.btn_ai_ask.setText("请求中...")
        self._ai_input_tokens = self._estimate_tokens(prompt)
        self._ai_output_tokens = 0
        self._ai_stream_text = ""
        # 更新标签页的回答显示
        answer_edit = self._get_ai_tab_answer_edit(self._current_ai_session_id)
        if answer_edit:
            answer_edit.setPlainText(self._render_ai_session_text(session))
        self._update_token_labels()
        self.status.showMessage(f"正在请求模型：{model}", 2500)
        self.append_log(f"问AI请求已发送，模型：{model}，会话：{session.title}")

        def _worker() -> None:
            history = session.messages[-20:] if session.messages else []
            payload = {
                "model": model,
                "stream": True,
                "messages": [
                    {"role": "system", "content": "你是一个简洁、可靠的中文助手。"},
                ] + history + [{"role": "user", "content": prompt}],
            }
            req = urllib.request.Request(
                SILICONFLOW_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                },
                method="POST",
            )
            try:
                with _SILICONFLOW_OPENER.open(req, timeout=60) as resp:
                    chunks: list[str] = []
                    for raw in resp:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_text = line[5:].strip()
                        if data_text == "[DONE]":
                            break
                        try:
                            data = json.loads(data_text)
                        except Exception:
                            continue
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        chunk = str(delta.get("content", ""))
                        if chunk:
                            chunks.append(chunk)
                            self._ai_bridge.chunk.emit(request_id, session.session_id, chunk)
                    content = "".join(chunks).strip()
                if not content:
                    content = "[接口返回为空]"
                self._ai_bridge.finished.emit(request_id, session.session_id, True, content, prompt)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
                self._ai_bridge.finished.emit(
                    request_id,
                    session.session_id,
                    False,
                    f"HTTPError {exc.code}: {body}",
                    prompt,
                )
            except Exception as exc:
                self._ai_bridge.finished.emit(request_id, session.session_id, False, repr(exc), prompt)

        threading.Thread(target=_worker, daemon=True).start()

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        ascii_words = len(re.findall(r"[A-Za-z0-9_]+", text))
        punct = len(re.findall(r"[^\sA-Za-z0-9_\u4e00-\u9fff]", text))
        return max(1, chinese_chars + ascii_words + max(0, punct // 2))

    def _update_realtime_token_stats(self) -> None:
        if not self._ask_ai_mode:
            return
        # 使用当前标签页的内容编辑器
        content_edit = self._get_ai_tab_content_edit(self._current_ai_session_id)
        if content_edit is None:
            return
        if self._current_ai_session_id and self._current_ai_session_id in self._ai_sessions:
            self._ai_sessions[self._current_ai_session_id].draft_prompt = content_edit.toPlainText()
        self._ai_input_tokens = self._estimate_tokens(content_edit.toPlainText())
        self._update_token_labels()

    def _current_model_price(self) -> ModelPrice | None:
        model = self.model_combo.currentText().strip()
        candidates = [model, model.split("/")[-1]]
        normalized = {self._normalize_model_name(x): x for x in self._model_prices}
        for candidate in candidates:
            if candidate in self._model_prices:
                return self._model_prices[candidate]
            key = self._normalize_model_name(candidate)
            if key in normalized:
                return self._model_prices[normalized[key]]
        return None

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", model.lower())

    def _update_token_labels(self) -> None:
        self.label_input_tokens.setText(f"输入: {self._ai_input_tokens} tokens")
        self.label_output_tokens.setText(f"输出: {self._ai_output_tokens} tokens")
        price = self._current_model_price()
        if price is None:
            self.label_cost.setText("费用: 价格未知")
            return
        input_cost = self._ai_input_tokens / 1_000_000 * price.input_per_m
        output_cost = self._ai_output_tokens / 1_000_000 * price.output_per_m
        total = input_cost + output_cost
        self.label_cost.setText(
            f"费用: {price.currency}{total:.6f} "
            f"(入 {price.currency}{input_cost:.6f} / 出 {price.currency}{output_cost:.6f})"
        )

    def _on_ai_model_changed(self, model: str) -> None:
        model = model.strip()
        if not model:
            return
        self._selected_ai_model = model
        self._settings.setValue("ai/model", model)
        self._settings.sync()
        self.append_log(f"AI模型已选择：{model}")

    def _refresh_ai_models(self) -> None:
        if not SILICONFLOW_API_KEY:
            self.status.showMessage("未配置 SiliconFlow API Key", 5000)
            return
        self.btn_refresh_models.setEnabled(False)
        self.btn_refresh_models.setText("刷新中...")
        self.status.showMessage("正在读取硅基模型列表...", 2500)

        def _worker() -> None:
            url = f"{SILICONFLOW_MODELS_URL}?type=text&sub_type=chat"
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}"},
                method="GET",
            )
            try:
                with _SILICONFLOW_OPENER.open(req, timeout=30) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                data = json.loads(text)
                models = []
                prices: dict[str, ModelPrice] = {}
                for item in data.get("data", []):
                    model_id = str(item.get("id", "")).strip()
                    if model_id:
                        models.append(model_id)
                        price = self._extract_price_from_model_item(item)
                        if price is not None:
                            prices[model_id] = price
                if not models:
                    raise RuntimeError(f"模型列表为空: {text[:500]}")
                self._model_bridge.finished.emit(True, {"models": sorted(set(models)), "prices": prices})
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
                self._model_bridge.finished.emit(False, f"HTTPError {exc.code}: {body}")
            except Exception as exc:
                self._model_bridge.finished.emit(False, repr(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_models_loaded(self, ok: bool, payload: object) -> None:
        self.btn_refresh_models.setEnabled(True)
        self.btn_refresh_models.setText("刷新模型")
        if not ok:
            self.status.showMessage("读取模型列表失败", 5000)
            self.append_log(f"读取模型列表失败：{payload}")
            return
        if isinstance(payload, dict):
            raw_models = payload.get("models", [])
            raw_prices = payload.get("prices", {})
            if isinstance(raw_prices, dict):
                self._model_prices.update(raw_prices)
        else:
            raw_models = payload
        models = [str(x) for x in raw_models if str(x).strip()]
        current = self.model_combo.currentText().strip() or self._selected_ai_model
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current in models:
            self.model_combo.setCurrentText(current)
        else:
            self.model_combo.setCurrentText(models[0])
        self.model_combo.blockSignals(False)
        self._on_ai_model_changed(self.model_combo.currentText())
        self.status.showMessage(f"已读取 {len(models)} 个模型", 3000)
        self.append_log(f"硅基模型列表已刷新：{len(models)} 个")
        self._update_token_labels()

    def _extract_price_from_model_item(self, item: dict) -> ModelPrice | None:
        def _number(value) -> float | None:
            if value is None:
                return None
            if isinstance(value, (int, float)):
                return float(value)
            match = re.search(r"\d+(?:\.\d+)?", str(value))
            return float(match.group(0)) if match else None

        containers = [item]
        for key in ("pricing", "price", "billing"):
            value = item.get(key)
            if isinstance(value, dict):
                containers.append(value)
        input_value = output_value = None
        for data in containers:
            input_value = input_value or _number(
                data.get("input")
                or data.get("input_price")
                or data.get("prompt")
                or data.get("prompt_price")
            )
            output_value = output_value or _number(
                data.get("output")
                or data.get("output_price")
                or data.get("completion")
                or data.get("completion_price")
            )
        if input_value is None or output_value is None:
            return None
        return ModelPrice(input_per_m=input_value, output_per_m=output_value)

    def _refresh_official_prices(self) -> None:
        def _worker() -> None:
            try:
                req = urllib.request.Request(SILICONFLOW_PRICING_URL, method="GET")
                with _SILICONFLOW_OPENER.open(req, timeout=30) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
                prices = self._parse_official_pricing_page(html)
                if not prices:
                    raise RuntimeError("官网价格页未解析到文本模型价格")
                self._price_bridge.finished.emit(True, prices)
            except Exception as exc:
                self._price_bridge.finished.emit(False, repr(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _parse_official_pricing_page(self, text: str) -> dict[str, ModelPrice]:
        prices: dict[str, ModelPrice] = {}
        compact = re.sub(r"\s+", " ", text)
        pattern = re.compile(
            r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.\-\[\] ]{2,80}),\s*"
            r"(?P<context>\d+(?:\.\d+)?K),\s*"
            r"(?P<input>\d+(?:\.\d+)?)(?:,\s*(?P<cached>\d+(?:\.\d+)?))?,\s*"
            r"(?P<output>\d+(?:\.\d+)?)\s+\[Details\]",
            re.IGNORECASE,
        )
        for match in pattern.finditer(compact):
            name = match.group("name").strip()
            try:
                prices[name] = ModelPrice(
                    input_per_m=float(match.group("input")),
                    output_per_m=float(match.group("output")),
                    currency="$",
                )
            except Exception:
                continue
        return prices

    def _on_prices_loaded(self, ok: bool, payload: object) -> None:
        if not ok:
            self.label_price_source.setText("价格: 官网读取失败")
            self.append_log(f"官网价格读取失败：{payload}")
            return
        if isinstance(payload, dict):
            self._model_prices.update(payload)
        self.label_price_source.setText(f"价格: 硅基官网 {len(self._model_prices)} 个")
        self.append_log(f"已从硅基官网读取价格：{len(self._model_prices)} 个")
        self._update_token_labels()

    def _on_ai_finished(self, request_id: int, session_id: str, ok: bool, message: str, prompt: str) -> None:
        session = self._ai_sessions.get(session_id)
        if session is None:
            return
        session.in_flight = False
        if request_id == self._active_ai_request_id:
            self._active_ai_request_id = None
        if self._current_ai_session_id == session_id:
            self.btn_ai_ask.setEnabled(True)
            self.btn_ai_ask.setText("问AI")
        prefix = "AI回答" if ok else "问AI失败"
        if ok:
            session.messages.append({"role": "user", "content": prompt})
            session.messages.append({"role": "assistant", "content": message})
            session.draft_prompt = ""
            session.streaming_text = ""
            self._ai_output_tokens = self._estimate_tokens(message)
            if self._current_ai_session_id == session_id:
                # 清空当前标签页的内容编辑器
                content_edit = self._get_ai_tab_content_edit(session_id)
                if content_edit:
                    content_edit.blockSignals(True)
                    content_edit.clear()
                    content_edit.blockSignals(False)
                # 更新回答显示
                answer_edit = self._get_ai_tab_answer_edit(session_id)
                if answer_edit:
                    answer_edit.setPlainText(self._render_ai_session_text(session))
        else:
            session.streaming_text = f"{prefix}:\n{message}"
            if self._current_ai_session_id == session_id:
                answer_edit = self._get_ai_tab_answer_edit(session_id)
                if answer_edit:
                    answer_edit.setPlainText(self._render_ai_session_text(session))
        self._update_token_labels()
        self.status.showMessage(prefix, 3500)
        self.append_log(f"{prefix}（{session.title}）")
        self._save_settings()

    def _on_ai_chunk(self, request_id: int, session_id: str, chunk: str) -> None:
        if request_id != self._active_ai_request_id:
            return
        session = self._ai_sessions.get(session_id)
        if session is None:
            return
        session.streaming_text += chunk
        if self._current_ai_session_id == session_id:
            self._ai_stream_text = session.streaming_text
            self._ai_output_tokens = self._estimate_tokens(session.streaming_text)
            # 使用标签页的回答编辑器
            answer_edit = self._get_ai_tab_answer_edit(session_id)
            if answer_edit:
                answer_edit.setPlainText(self._render_ai_session_text(session))
                cursor = answer_edit.textCursor()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                answer_edit.setTextCursor(cursor)
            self._update_token_labels()

    def _set_editor(self, note: NoteDto | None) -> None:
        self._ask_ai_mode = False
        self._current_ai_session_id = None
        # 切换到普通笔记模式：隐藏 ai_tabs，显示普通编辑器
        self.ai_tabs.hide()
        self.content_edit.show()
        self.ai_answer_edit.hide()
        self.ai_answer_edit.clear()
        self.btn_ai_ask.setEnabled(False)
        self.btn_save.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_favorite.setEnabled(True)
        self.title_edit.blockSignals(True)
        self.content_edit.blockSignals(True)
        if note is None:
            self.title_edit.setText("")
            self.content_edit.clear()
            self.state.current_note_id = None
        else:
            self.title_edit.setText(note.title)
            self._set_content_html(note.content)
            self.state.current_note_id = note.id
        self.title_edit.blockSignals(False)
        self.content_edit.blockSignals(False)
        self.state.dirty = False
        self._update_title()
        self._update_token_labels()

    def _mark_dirty(self) -> None:
        if self._ask_ai_mode:
            self._update_realtime_token_stats()
            return
        if not self.state.dirty:
            self.state.dirty = True
            self._update_title()

    def _confirm_discard(self) -> bool:
        ret = QtWidgets.QMessageBox.question(self, "未保存更改", "当前笔记有未保存更改，是否丢弃？")
        return ret == QtWidgets.QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._auto_save_note("窗口关闭")
        if self._allow_close:
            self._save_settings()
            event.accept()
            return
        # UX requirement: close button hides to system tray (no taskbar entry).
        self._save_settings()
        self.hide()
        event.ignore()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.WindowDeactivate:
            self._auto_save_note("窗口失去焦点")
        super().changeEvent(event)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        text_targets = {
            self.content_edit,
            self.ai_answer_edit,
            self.log_view,
            self.content_edit.viewport(),
            self.ai_answer_edit.viewport(),
            self.log_view.viewport(),
        }
        if watched in text_targets and event.type() == QtCore.QEvent.Type.Wheel:
            if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta:
                    step = 1 if delta > 0 else -1
                    self._set_text_font_size(self._text_font_size + step)
                    event.accept()
                return True
        # 图片粘贴：拦截 content_edit 的 Ctrl+V
        if (
            watched is self.content_edit
            and event.type() == QtCore.QEvent.Type.KeyPress
            and event.matches(QtGui.QKeySequence.StandardKey.Paste)
        ):
            clipboard = QtWidgets.QApplication.clipboard()
            mime = clipboard.mimeData()
            if mime and mime.hasImage():
                img = QtGui.QImage(mime.imageData())
                if not img.isNull():
                    self._paste_image_to_editor(self.content_edit, img)
                    return True
            if mime and mime.hasUrls():
                handled = False
                for url in mime.urls():
                    path = url.toLocalFile()
                    if path and self._is_image_file(path):
                        self._insert_image_file_to_editor(self.content_edit, path)
                        handled = True
                if handled:
                    return True
        # 图片拖拽到 content_edit
        if watched is self.content_edit.viewport():
            if event.type() == QtCore.QEvent.Type.DragEnter:
                mime = event.mimeData()
                if mime and (mime.hasImage() or self._mime_has_image_urls(mime)):
                    event.acceptProposedAction()
                    return True
            if event.type() == QtCore.QEvent.Type.Drop:
                mime = event.mimeData()
                if mime and mime.hasImage():
                    img = QtGui.QImage(mime.imageData())
                    if not img.isNull():
                        self._paste_image_to_editor(self.content_edit, img)
                        return True
                if mime and mime.hasUrls():
                    handled = False
                    for url in mime.urls():
                        path = url.toLocalFile()
                        if path and self._is_image_file(path):
                            self._insert_image_file_to_editor(self.content_edit, path)
                            handled = True
                    if handled:
                        return True
        return super().eventFilter(watched, event)

    # ── 图片辅助方法 ──────────────────────────────────────────────

    @staticmethod
    def _is_image_file(path: str) -> bool:
        return Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}

    @staticmethod
    def _mime_has_image_urls(mime) -> bool:
        if not mime or not mime.hasUrls():
            return False
        for url in mime.urls():
            p = url.toLocalFile()
            if p and Path(p).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}:
                return True
        return False

    def _images_dir(self) -> Path:
        root = Path(__file__).resolve().parent / "notepad_list" / "_images"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _save_image(self, img: QtGui.QImage, ext: str = "png") -> str | None:
        images_dir = self._images_dir()
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = images_dir / name
        if not img.save(str(save_path)):
            self.append_log(f"图片保存失败：{save_path}")
            return None
        self.append_log(f"图片已保存：{save_path.name}")
        return str(save_path)

    def _paste_image_to_editor(self, editor: QtWidgets.QTextEdit, img: QtGui.QImage) -> None:
        saved = self._save_image(img)
        if not saved:
            return
        self._do_insert_image(editor, saved)

    def _insert_image_file_to_editor(self, editor: QtWidgets.QTextEdit, file_path: str) -> None:
        import shutil
        src = Path(file_path)
        if not src.is_file():
            return
        images_dir = self._images_dir()
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{src.suffix}"
        dest = images_dir / name
        try:
            shutil.copy2(str(src), str(dest))
        except Exception as exc:
            self.append_log(f"复制图片失败：{exc}")
            return
        self.append_log(f"图片已复制：{dest.name}")
        self._do_insert_image(editor, str(dest))

    def _do_insert_image(self, editor: QtWidgets.QTextEdit, abs_path: str) -> None:
        url = QtCore.QUrl.fromLocalFile(abs_path)
        doc = editor.document()
        doc.addResource(
            QtGui.QTextDocument.ResourceType.ImageResource,
            url,
            QtGui.QImage(abs_path),
        )
        cursor = editor.textCursor()
        img_fmt = QtGui.QTextImageFormat()
        img_fmt.setName(url.toString())
        # 限制图片最大宽度为编辑器宽度的 90%
        source_img = QtGui.QImage(abs_path)
        if not source_img.isNull():
            max_w = editor.viewport().width() * 0.9
            if source_img.width() > max_w:
                img_fmt.setWidth(max_w)
                img_fmt.setHeight(source_img.height() * max_w / source_img.width())
        cursor.insertImage(img_fmt)
        cursor.insertText("\n")
        editor.setTextCursor(cursor)
        self._mark_dirty()

    def _get_content_html(self) -> str:
        return self.content_edit.toHtml()

    def _set_content_html(self, content: str) -> None:
        if self._looks_like_html(content):
            self.content_edit.setHtml(content)
            self._reload_local_images(self.content_edit)
        else:
            self.content_edit.setPlainText(content)

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        s = (text or "").strip()
        return s.startswith("<!DOCTYPE") or s.startswith("<html") or "<img " in s[:2000]

    def _reload_local_images(self, editor: QtWidgets.QTextEdit) -> None:
        doc = editor.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if fmt.isImageFormat():
                        img_fmt = fmt.toImageFormat()
                        name = img_fmt.name()
                        url = QtCore.QUrl(name)
                        local = url.toLocalFile() if url.isLocalFile() else name
                        if local and Path(local).is_file():
                            doc.addResource(
                                QtGui.QTextDocument.ResourceType.ImageResource,
                                url,
                                QtGui.QImage(local),
                            )
                it += 1
            block = block.next()

    def _set_text_font_size(self, size: int) -> None:
        size = max(8, min(28, int(size)))
        if size == self._text_font_size:
            return
        self._text_font_size = size
        self._apply_text_font_size()
        self._settings.setValue("ui/text_font_size", str(size))
        self._settings.sync()
        self.status.showMessage(f"日志字体大小：{size}", 1500)

    def _apply_text_font_size(self) -> None:
        font = QtGui.QFont("Cascadia Mono", self._text_font_size)
        for editor in (self.content_edit, self.ai_answer_edit, self.log_view):
            editor.setFont(font)
            editor.document().setDefaultFont(font)

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "错误", message)

    def _configure_ctrl_double_interval(self) -> None:
        current_raw = self._settings.value("hotkey/double_ctrl_max_gap_sec", "0.15")
        try:
            current = float(str(current_raw))
        except Exception:
            current = 0.15
        value, ok = QtWidgets.QInputDialog.getDouble(
            self,
            "设置 Ctrl 双击间隔",
            "Ctrl 双击最大间隔（秒）:",
            current,
            0.08,
            1.00,
            2,
        )
        if not ok:
            return
        value = max(0.08, min(1.00, float(value)))
        self._settings.setValue("hotkey/double_ctrl_max_gap_sec", f"{value:.2f}")
        self._settings.sync()
        if self._hotkey_interval_callback is not None:
            try:
                self._hotkey_interval_callback(value)
            except Exception as exc:
                self.append_log(f"更新 Ctrl 双击间隔失败: {exc}")
        self.status.showMessage(f"Ctrl 双击间隔已设置为 {value:.2f}s", 3000)
        self.append_log(f"Ctrl 双击间隔已更新: {value:.2f}s")

    def _update_title(self) -> None:
        suffix = " *" if self.state.dirty else ""
        cur = f"#{self.state.current_note_id}" if self.state.current_note_id else "新建"
        self.setWindowTitle(f"L Notepad - {cur}{suffix}")

    @QtCore.Slot()
    def show_from_hotkey(self) -> None:
        self.append_log("收到快捷键触发，尝试显示到前台")
        self._bring_to_front()

    def _bring_to_front(self) -> None:
        self.showNormal()
        self.setWindowState(
            (self.windowState() & ~QtCore.Qt.WindowState.WindowMinimized)
            | QtCore.Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            hwnd_topmost = -1
            hwnd_notopmost = -2
            swp_nomove = 0x0002
            swp_nosize = 0x0001
            swp_showwindow = 0x0040
            flags = swp_nomove | swp_nosize | swp_showwindow
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, flags)
            user32.SetWindowPos(hwnd, hwnd_notopmost, 0, 0, 0, 0, flags)
            user32.SetForegroundWindow(hwnd)
            self.append_log("已调用 Windows 前台显示逻辑")
        except Exception:
            self.append_log("Windows 前台显示逻辑失败")

    @QtCore.Slot(str)
    def append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {message}")

    def _sort_notes(self, notes: list[NoteDto]) -> list[NoteDto]:
        fav_rank = {nid: idx for idx, nid in enumerate(self._favorite_order)}
        fav = [n for n in notes if self._is_favorite(n.id)]
        fav.sort(key=lambda n: fav_rank.get(int(n.id), 10**9))
        other = [n for n in notes if not self._is_favorite(n.id)]
        return fav + other

    def _is_favorite(self, note_id: int | None) -> bool:
        if note_id is None:
            return False
        return int(note_id) in self._favorite_order

    def _update_favorite_button_label(self) -> None:
        if self._is_favorite(self.state.current_note_id):
            self.btn_favorite.setText("取消※置顶")
        else:
            self.btn_favorite.setText("※ 置顶/收藏")

    def _load_settings(self) -> None:
        fav_raw = self._settings.value("ui/favorites", "[]")
        last_raw = self._settings.value("ui/last_note_id", "")
        model_raw = self._settings.value("ai/model", DEFAULT_SILICONFLOW_MODEL)
        ai_sessions_raw = self._settings.value("ai/sessions", "[]")
        ai_current_raw = self._settings.value("ai/current_session_id", "")
        font_size_raw = self._settings.value("ui/text_font_size", "10")
        try:
            parsed = json.loads(str(fav_raw)) if fav_raw is not None else []
            self._favorite_order = [int(x) for x in parsed]
        except Exception:
            self._favorite_order = []
        try:
            self._last_open_note_id = int(last_raw) if str(last_raw).strip() else None
        except Exception:
            self._last_open_note_id = None
        model = str(model_raw or "").strip()
        self._selected_ai_model = model or DEFAULT_SILICONFLOW_MODEL
        self._ai_sessions = {}
        self._current_ai_session_id = None
        try:
            raw_sessions = json.loads(str(ai_sessions_raw)) if ai_sessions_raw is not None else []
            if isinstance(raw_sessions, list):
                for item in raw_sessions:
                    if not isinstance(item, dict):
                        continue
                    sid = str(item.get("session_id", "")).strip()
                    if not sid:
                        continue
                    title = str(item.get("title", "")).strip() or f"问AI {sid[-4:]}"
                    messages = item.get("messages", [])
                    parsed_messages: list[dict[str, str]] = []
                    if isinstance(messages, list):
                        for msg in messages:
                            if not isinstance(msg, dict):
                                continue
                            role = str(msg.get("role", "")).strip()
                            content = str(msg.get("content", "")).strip()
                            if role in {"user", "assistant"} and content:
                                parsed_messages.append({"role": role, "content": content})
                    draft_prompt = str(item.get("draft_prompt", ""))
                    self._ai_sessions[sid] = AiSession(
                        session_id=sid,
                        title=title,
                        messages=parsed_messages,
                        draft_prompt=draft_prompt,
                    )
        except Exception:
            self._ai_sessions = {}
        cur = str(ai_current_raw or "").strip()
        if cur and cur in self._ai_sessions:
            self._current_ai_session_id = cur
        try:
            self._text_font_size = max(8, min(28, int(str(font_size_raw))))
        except Exception:
            self._text_font_size = 10

    def _restore_ai_tabs(self) -> None:
        """从保存的会话恢复 AI 标签页"""
        for sess in self._sorted_ai_sessions():
            self._create_ai_tab(sess)
        # 如果有当前会话，切换到对应标签页
        if self._current_ai_session_id:
            for i in range(self.ai_tabs.count()):
                widget = self.ai_tabs.widget(i)
                if widget and widget.property("session_id") == self._current_ai_session_id:
                    self.ai_tabs.setCurrentIndex(i)
                    break

    def _save_settings(self) -> None:
        try:
            self._settings.setValue("ui/favorites", json.dumps(self._favorite_order))
            self._settings.setValue(
                "ui/last_note_id",
                "" if self._last_open_note_id is None else str(self._last_open_note_id),
            )
            self._settings.setValue("ai/model", self.model_combo.currentText().strip() or self._selected_ai_model)
            ai_sessions_data = []
            for sess in self._sorted_ai_sessions():
                ai_sessions_data.append(
                    {
                        "session_id": sess.session_id,
                        "title": sess.title,
                        "messages": sess.messages[-100:],
                        "draft_prompt": sess.draft_prompt,
                    }
                )
            self._settings.setValue("ai/sessions", json.dumps(ai_sessions_data, ensure_ascii=False))
            self._settings.setValue("ai/current_session_id", self._current_ai_session_id or "")
            self._settings.setValue("ui/text_font_size", str(self._text_font_size))
            self._settings.setValue("window/geometry", self.saveGeometry())
            self._settings.sync()
        except Exception:
            pass

    def _ensure_ai_session(self, session_id: str | None = None) -> AiSession:
        if session_id and session_id in self._ai_sessions:
            return self._ai_sessions[session_id]
        if self._current_ai_session_id and self._current_ai_session_id in self._ai_sessions:
            return self._ai_sessions[self._current_ai_session_id]
        return self._new_ai_session(select=True)

    def _new_ai_session(self, select: bool = True) -> AiSession:
        sid = str(QtCore.QDateTime.currentMSecsSinceEpoch())
        title = f"问AI {len(self._ai_sessions) + 1}"
        sess = AiSession(session_id=sid, title=title, messages=[])
        self._ai_sessions[sid] = sess
        if select:
            self._current_ai_session_id = sid
        # 创建标签页
        self._create_ai_tab(sess)
        self._save_settings()
        return sess

    def _create_ai_tab(self, session: AiSession) -> None:
        """为 AI 会话创建一个标签页（从 .ui 模板克隆）"""
        # 获取模板 widget（第0个标签页）
        template_widget = self.ai_tabs.widget(0)
        if template_widget is None:
            # 回退：如果模板不存在，动态创建
            tab_widget = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab_widget)
            tab_layout.setSpacing(10)
            tab_layout.setContentsMargins(10, 10, 10, 10)
            content_edit = QtWidgets.QTextEdit()
            content_edit.setObjectName("CodeEditor")
            content_edit.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
            content_edit.setPlaceholderText('在这里输入问题，然后点击"问AI"。\n\n支持多会话与上下文记忆。\n模型：千问 / SiliconFlow。')
            ai_answer_edit = QtWidgets.QTextEdit()
            ai_answer_edit.setObjectName("AiAnswerViewer")
            ai_answer_edit.setReadOnly(True)
            ai_answer_edit.setPlaceholderText("AI 回答会显示在这里（支持多会话上下文）")
            tab_layout.addWidget(content_edit, 1)
            tab_layout.addWidget(ai_answer_edit, 2)
        else:
            # 克隆模板
            tab_widget = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab_widget)
            tab_layout.setSpacing(10)
            tab_layout.setContentsMargins(10, 10, 10, 10)

            # 克隆输入框
            template_content = template_widget.findChild(QtWidgets.QTextEdit, "ai_tab_content_edit_template")
            content_edit = QtWidgets.QTextEdit()
            content_edit.setObjectName("CodeEditor")
            content_edit.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
            if template_content:
                content_edit.setPlaceholderText(template_content.placeholderText())
            else:
                content_edit.setPlaceholderText('在这里输入问题，然后点击"问AI"。\n\n支持多会话与上下文记忆。\n模型：千问 / SiliconFlow。')

            # 克隆回答框
            template_answer = template_widget.findChild(QtWidgets.QTextEdit, "ai_tab_answer_edit_template")
            ai_answer_edit = QtWidgets.QTextEdit()
            ai_answer_edit.setObjectName("AiAnswerViewer")
            ai_answer_edit.setReadOnly(True)
            if template_answer:
                ai_answer_edit.setPlaceholderText(template_answer.placeholderText())
            else:
                ai_answer_edit.setPlaceholderText("AI 回答会显示在这里（支持多会话上下文）")

            # 添加到布局（比例 1:2）
            tab_layout.addWidget(content_edit, 1)
            tab_layout.addWidget(ai_answer_edit, 2)

        # 存储 session_id 到 widget
        tab_widget.setProperty("session_id", session.session_id)
        tab_widget.setProperty("content_edit", content_edit)
        tab_widget.setProperty("ai_answer_edit", ai_answer_edit)

        # 连接文本变化信号
        content_edit.textChanged.connect(lambda: self._on_ai_tab_text_changed(session.session_id))

        # 添加到标签页
        index = self.ai_tabs.addTab(tab_widget, session.title)
        self.ai_tabs.setCurrentIndex(index)

        # 为新标签设置关闭按钮
        self._setup_ai_tab_close_button(index)

    def _setup_ai_tab_close_button(self, index: int) -> None:
        """为指定索引的AI标签页设置关闭按钮"""
        tab_bar = self.ai_tabs.tabBar()
        if index < 0 or index >= tab_bar.count():
            return
        # 创建自定义关闭按钮（样式从 style.qss 加载）
        close_btn = QtWidgets.QPushButton("×", tab_bar)
        close_btn.setToolTip("关闭标签")
        # 连接到关闭槽
        def make_handler(idx: int):
            def handler():
                self._on_ai_tab_close_requested(idx)
            return handler
        close_btn.clicked.connect(make_handler(index))
        # 设置为标签的右按钮
        tab_bar.setTabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide, close_btn)

    def _on_ai_tab_text_changed(self, session_id: str) -> None:
        """AI 标签页文本变化时更新统计"""
        if session_id == self._current_ai_session_id:
            self._update_realtime_token_stats()

    def _get_current_ai_tab_widget(self) -> QtWidgets.QWidget | None:
        """获取当前 AI 标签页的 widget"""
        if self.ai_tabs.count() == 0:
            return None
        return self.ai_tabs.currentWidget()

    def _get_ai_tab_content_edit(self, session_id: str | None = None) -> QtWidgets.QTextEdit | None:
        """获取指定会话的内容编辑器，如果不指定则获取当前标签页的"""
        if session_id is None:
            widget = self._get_current_ai_tab_widget()
            if widget:
                return widget.property("content_edit")
            return None
        # 查找指定 session_id 的标签页
        for i in range(self.ai_tabs.count()):
            widget = self.ai_tabs.widget(i)
            if widget and widget.property("session_id") == session_id:
                return widget.property("content_edit")
        return None

    def _get_ai_tab_answer_edit(self, session_id: str | None = None) -> QtWidgets.QTextEdit | None:
        """获取指定会话的回答编辑器，如果不指定则获取当前标签页的"""
        if session_id is None:
            widget = self._get_current_ai_tab_widget()
            if widget:
                return widget.property("ai_answer_edit")
            return None
        # 查找指定 session_id 的标签页
        for i in range(self.ai_tabs.count()):
            widget = self.ai_tabs.widget(i)
            if widget and widget.property("session_id") == session_id:
                return widget.property("ai_answer_edit")
        return None

    def _sorted_ai_sessions(self) -> list[AiSession]:
        return sorted(
            self._ai_sessions.values(),
            key=lambda s: int(s.session_id) if s.session_id.isdigit() else 0,
            reverse=True,
        )

    def _on_ai_tab_close_requested(self, index: int) -> None:
        """关闭 AI 标签页"""
        widget = self.ai_tabs.widget(index)
        if widget is None:
            return
        session_id = widget.property("session_id")
        if session_id and session_id in self._ai_sessions:
            del self._ai_sessions[session_id]
        self.ai_tabs.removeTab(index)
        widget.deleteLater()
        # 如果所有标签都关闭了，退出 AI 模式
        if self.ai_tabs.count() == 0:
            self._ask_ai_mode = False
            self._current_ai_session_id = None
            self.ai_tabs.hide()
            self.content_edit.show()
            self.ai_answer_edit.hide()
            self._set_editor(None)
        self._save_settings()

    def _on_ai_tabs_context_menu(self, pos: QtCore.QPoint) -> None:
        """AI标签栏右键菜单"""
        # 获取点击位置对应的标签索引
        tab_bar = self.ai_tabs.tabBar()
        tab_index = tab_bar.tabAt(pos)

        if tab_index < 0:
            return

        # 切换到点击的标签
        self.ai_tabs.setCurrentIndex(tab_index)

        menu = QtWidgets.QMenu(self)

        # 关闭其他标签
        action_close_others = menu.addAction("关闭其他标签")
        action_close_others.triggered.connect(lambda: self._close_other_ai_tabs_except(tab_index))

        # 关闭当前标签
        action_close_current = menu.addAction("关闭当前标签")
        action_close_current.triggered.connect(lambda: self._on_ai_tab_close_requested(tab_index))

        menu.addSeparator()

        # 重命名标签
        action_rename = menu.addAction("重命名标签")
        action_rename.triggered.connect(lambda: self._rename_ai_tab(tab_index))

        # 在鼠标位置显示菜单
        global_pos = self.ai_tabs.mapToGlobal(pos)
        menu.exec(global_pos)

    def _close_other_ai_tabs_except(self, keep_index: int) -> None:
        """关闭除指定标签外的所有AI标签"""
        # 从后往前删除，避免索引变化
        for i in range(self.ai_tabs.count() - 1, -1, -1):
            if i != keep_index:
                self._on_ai_tab_close_requested(i)

    def _rename_ai_tab(self, tab_index: int) -> None:
        """重命名指定AI标签"""
        if tab_index < 0:
            return

        widget = self.ai_tabs.widget(tab_index)
        if widget is None:
            return

        session_id = widget.property("session_id")
        if not session_id or session_id not in self._ai_sessions:
            return

        session = self._ai_sessions[session_id]
        new_title, ok = QtWidgets.QInputDialog.getText(
            self, "重命名标签", "请输入新标签名:",
            QtWidgets.QLineEdit.EchoMode.Normal,
            session.title
        )
        if ok and new_title.strip():
            session.title = new_title.strip()
            self.ai_tabs.setTabText(tab_index, session.title)
            # 如果重命名的是当前标签，更新标题编辑框
            if tab_index == self.ai_tabs.currentIndex():
                self.title_edit.setText(session.title)
            self._save_settings()

    def _setup_ai_tab_close_buttons(self) -> None:
        """为所有AI标签页设置关闭按钮图标（X符号）"""
        tab_bar = self.ai_tabs.tabBar()
        for i in range(tab_bar.count()):
            # 创建自定义关闭按钮（样式从 style.qss 加载）
            close_btn = QtWidgets.QPushButton("×", tab_bar)
            close_btn.setToolTip("关闭标签")
            # 连接到关闭槽（使用闭包捕获当前索引）
            def make_close_handler(index: int):
                def handler():
                    self._on_ai_tab_close_requested(index)
                return handler
            close_btn.clicked.connect(make_close_handler(i))
            # 设置为标签的右按钮
            tab_bar.setTabButton(i, QtWidgets.QTabBar.ButtonPosition.RightSide, close_btn)

    def _on_ai_tab_changed(self, index: int) -> None:
        """切换 AI 标签页"""
        if index < 0:
            return
        widget = self.ai_tabs.widget(index)
        if widget is None:
            return
        session_id = widget.property("session_id")
        if session_id and session_id in self._ai_sessions:
            self._current_ai_session_id = session_id
            session = self._ai_sessions[session_id]
            # 更新标题编辑框
            self.title_edit.blockSignals(True)
            self.title_edit.setText(session.title)
            self.title_edit.blockSignals(False)
            # 更新 token 统计
            self._update_realtime_token_stats()
        self._save_settings()

    def _render_ai_session_text(self, session: AiSession) -> str:
        chunks: list[str] = []
        if not session.messages and not session.streaming_text:
            return "AI 回答会显示在这里（支持多会话上下文）"
        for msg in session.messages:
            role = "你" if msg.get("role") == "user" else "AI"
            chunks.append(f"{role}:\n{msg.get('content', '')}\n")
        if session.streaming_text:
            chunks.append(f"AI(输出中):\n{session.streaming_text}")
        return "\n".join(chunks).strip()

    def _restore_window_state(self) -> None:
        geo = self._settings.value("window/geometry")
        if isinstance(geo, (bytes, bytearray)):
            self.restoreGeometry(geo)

