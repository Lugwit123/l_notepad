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
_SILICONFLOW_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass(frozen=True)
class ModelPrice:
    input_per_m: float
    output_per_m: float
    currency: str = "$"


APP_QSS = r"""
QWidget {
  font-family: "Segoe UI", "Microsoft YaHei UI", Arial;
  font-size: 12px;
  color: #E9EEF5;
}
QMainWindow {
  background: #090D14;
}
QWidget#centralwidget {
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0B1020, stop:1 #0D1117);
}
QWidget#left_panel, QWidget#right_panel, QWidget#tab_log {
  background: rgba(15, 21, 34, 0.82);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 16px;
}
QLineEdit, QTextEdit, QComboBox {
  background: rgba(18, 24, 38, 0.96);
  border: 1px solid rgba(137, 221, 255, 0.12);
  border-radius: 12px;
  padding: 9px 11px;
  selection-background-color: rgba(77, 163, 255, 0.35);
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
  border: 1px solid rgba(77, 163, 255, 0.78);
  background: rgba(21, 30, 48, 0.98);
}
QLineEdit::placeholder, QTextEdit::placeholder {
  color: rgba(233,238,245,0.42);
}
QTextEdit#CodeEditor, QTextEdit#LogViewer, QTextEdit#AiAnswerViewer {
  background: #080D14;
  color: #D6DEEB;
  border: 1px solid rgba(137, 221, 255, 0.13);
  font-family: "Cascadia Mono", Consolas, "Microsoft YaHei UI", monospace;
  line-height: 1.35;
}
QComboBox {
  min-height: 18px;
}
QComboBox::drop-down {
  width: 28px;
  border: none;
  border-left: 1px solid rgba(255,255,255,0.08);
}
QComboBox QAbstractItemView {
  background: #101827;
  border: 1px solid rgba(77, 163, 255, 0.35);
  border-radius: 10px;
  padding: 6px;
  selection-background-color: rgba(77, 163, 255, 0.25);
  outline: 0;
}
QPushButton {
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 10px;
  padding: 8px 14px;
  min-height: 18px;
}
QPushButton:hover {
  background: rgba(77, 163, 255, 0.16);
  border: 1px solid rgba(77, 163, 255, 0.42);
}
QPushButton:pressed {
  background: rgba(77, 163, 255, 0.24);
}
QPushButton:disabled {
  color: rgba(233,238,245,0.32);
  background: rgba(255,255,255,0.035);
  border: 1px solid rgba(255,255,255,0.055);
}
QPushButton#PrimaryButton {
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(77, 163, 255, 0.34), stop:1 rgba(125, 92, 255, 0.28));
  border: 1px solid rgba(77, 163, 255, 0.62);
}
QPushButton#DangerButton {
  background: rgba(255, 92, 92, 0.16);
  border: 1px solid rgba(255, 92, 92, 0.50);
}
QListWidget {
  background: rgba(9, 14, 24, 0.82);
  border: 1px solid rgba(137, 221, 255, 0.10);
  border-radius: 10px;
  padding: 2px;
  outline: 0;
}
QListWidget::item {
  background: transparent;
  border-radius: 6px;
  padding: 3px 6px;
  margin: 0;
}
QListWidget::item:hover {
  background: rgba(255,255,255,0.06);
}
QListWidget::item:selected {
  background: rgba(77, 163, 255, 0.24);
  border: 1px solid rgba(77, 163, 255, 0.38);
  color: #FFFFFF;
}
QSplitter::handle {
  background: rgba(137, 221, 255, 0.06);
  margin: 8px 6px;
  border-radius: 4px;
}
QStatusBar {
  background: rgba(255,255,255,0.035);
  color: rgba(233,238,245,0.80);
}
QTabWidget::pane {
  border: none;
  top: 0;
}
QTabBar::tab {
  background: rgba(255,255,255,0.055);
  border: 1px solid rgba(255,255,255,0.09);
  border-radius: 10px;
  padding: 8px 20px;
  margin-right: 6px;
  margin-bottom: 8px;
}
QTabBar::tab:selected {
  background: rgba(77, 163, 255, 0.26);
  border: 1px solid rgba(77, 163, 255, 0.55);
  color: #FFFFFF;
}
QScrollBar:vertical, QScrollBar:horizontal {
  background: rgba(255,255,255,0.035);
  border: none;
  border-radius: 6px;
  margin: 2px;
}
QScrollBar:vertical {
  width: 10px;
}
QScrollBar:horizontal {
  height: 10px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
  background: rgba(137, 221, 255, 0.24);
  border-radius: 5px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
  background: rgba(137, 221, 255, 0.38);
}
QScrollBar::add-line, QScrollBar::sub-line {
  width: 0;
  height: 0;
}
"""


@dataclass
class UiState:
    current_note_id: int | None = None
    dirty: bool = False


class AiBridge(QtCore.QObject):
    chunk = QtCore.Signal(str)
    finished = QtCore.Signal(bool, str)


class ModelBridge(QtCore.QObject):
    finished = QtCore.Signal(bool, object)


class PriceBridge(QtCore.QObject):
    finished = QtCore.Signal(bool, object)


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
    def __init__(self, api: NotepadApi, restart_callback=None) -> None:
        super().__init__()
        self.api = api
        self._restart_callback = restart_callback
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
        self._ai_bridge = AiBridge()
        self._ai_bridge.chunk.connect(self._on_ai_chunk)
        self._ai_bridge.finished.connect(self._on_ai_finished)
        self._model_bridge = ModelBridge()
        self._model_bridge.finished.connect(self._on_models_loaded)
        self._price_bridge = PriceBridge()
        self._price_bridge.finished.connect(self._on_prices_loaded)
        self.setStyleSheet(APP_QSS)
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
        self.btn_new = self.findChild(QtWidgets.QPushButton, "btn_new")
        self.btn_save = self.findChild(QtWidgets.QPushButton, "btn_save")
        self.btn_delete = self.findChild(QtWidgets.QPushButton, "btn_delete")
        self.btn_refresh = self.findChild(QtWidgets.QPushButton, "btn_refresh")
        self.btn_favorite = self.findChild(QtWidgets.QPushButton, "btn_favorite")
        self.btn_ai_ask = self.findChild(QtWidgets.QPushButton, "btn_ai_ask")
        self.btn_restart = self.findChild(QtWidgets.QPushButton, "btn_restart")
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
            self.log_view,
        ]
        if any(w is None for w in required_widgets):
            raise RuntimeError("main_window.ui missing required widget objectName(s)")

        self.search_edit.textChanged.connect(self._apply_filter)
        self.notes_list.setSpacing(1)
        self.notes_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.notes_list.itemDoubleClicked.connect(self._rename_note_from_item)
        self.notes_list.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.notes_list.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        self.notes_list.setDragEnabled(True)
        self.notes_list.setAcceptDrops(True)
        self.notes_list.model().rowsMoved.connect(self._on_notes_rows_moved)

        self.title_edit.textEdited.connect(self._mark_dirty)
        self.model_combo.setEditable(True)
        self.model_combo.clear()
        self.model_combo.addItems(DEFAULT_MODEL_PRESETS)
        self.model_combo.setCurrentText(self._selected_ai_model)
        self.model_combo.currentTextChanged.connect(self._on_ai_model_changed)
        self.btn_refresh_models.clicked.connect(self._refresh_ai_models)

        self.content_edit.setObjectName("CodeEditor")
        self.content_edit.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        self.content_edit.textChanged.connect(self._mark_dirty)
        self.content_edit.textChanged.connect(self._update_realtime_token_stats)
        self._content_highlighter = TerminalCodeHighlighter(self.content_edit.document())

        self.ai_answer_edit.setObjectName("AiAnswerViewer")
        self.ai_answer_edit.setReadOnly(True)
        self.ai_answer_edit.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)
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

        self.log_view.setObjectName("LogViewer")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        self._log_highlighter = TerminalCodeHighlighter(self.log_view.document())
        self._apply_text_font_size()
        for editor in (self.content_edit, self.ai_answer_edit, self.log_view):
            editor.installEventFilter(self)
            editor.viewport().installEventFilter(self)

        self.status = self.statusBar()
        self._refresh_official_prices()
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
        self.notes_list.clear()
        ask_item = QtWidgets.QListWidgetItem("问AI\n千问 instant / 硅基流动")
        ask_font = ask_item.font()
        ask_font.setBold(True)
        ask_item.setFont(ask_font)
        ask_item.setFlags(ask_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsDragEnabled)
        ask_item.setData(QtCore.Qt.ItemDataRole.UserRole, ASK_AI_ITEM_ID)
        ask_item.setToolTip("使用硅基流动的千问 instant 模型提问")
        ask_item.setSizeHint(QtCore.QSize(0, 36))
        self.notes_list.addItem(ask_item)
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

        if self._ask_ai_mode:
            self.notes_list.setCurrentRow(0)
        elif current_id is not None:
            self._select_note_id(current_id)
        elif self.notes_list.count() > 1:
            self.notes_list.setCurrentRow(1)
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
            if int(item_id) == int(note_id):
                self.notes_list.setCurrentRow(i)
                return

    def _on_selection_changed(self) -> None:
        items = self.notes_list.selectedItems()
        if items and items[0].data(QtCore.Qt.ItemDataRole.UserRole) == ASK_AI_ITEM_ID:
            self._auto_save_note("切换到问AI前")
            self._set_ai_editor()
            return

        if self.state.dirty and not self._confirm_discard():
            self._select_note_id(self.state.current_note_id) if self.state.current_note_id else None
            return

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
        self._ask_ai_mode = False
        if self.state.dirty and not self._confirm_discard():
            return
        self._set_editor(None)
        self.title_edit.setText("未命名")
        self.content_edit.setPlainText("")
        self.state.current_note_id = None
        self.state.dirty = True
        self._update_title()

    def _save_note(self) -> None:
        if self._ask_ai_mode:
            self._ask_ai()
            return
        title = self.title_edit.text().strip() or "未命名"
        content = self.content_edit.toPlainText()
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
        content = self.content_edit.toPlainText()
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

    def _set_ai_editor(self) -> None:
        self._ask_ai_mode = True
        self.state.current_note_id = None
        self.state.dirty = False
        self.title_edit.blockSignals(True)
        self.content_edit.blockSignals(True)
        self.title_edit.setText("问AI")
        self.content_edit.setPlainText(
            "在这里输入问题，然后点击“问AI”。\n\n"
            "模型：千问 instant\n"
            "服务：SiliconFlow 硅基流动\n"
        )
        self.ai_answer_edit.clear()
        self.ai_answer_edit.show()
        self.title_edit.blockSignals(False)
        self.content_edit.blockSignals(False)
        self.btn_ai_ask.setEnabled(True)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_favorite.setEnabled(False)
        self.btn_new.setEnabled(True)
        self._update_title()
        self._update_realtime_token_stats()

    def _ask_ai(self) -> None:
        if not self._ask_ai_mode:
            return
        prompt = self.content_edit.toPlainText().strip()
        model = self.model_combo.currentText().strip() or DEFAULT_SILICONFLOW_MODEL
        if not prompt:
            self.status.showMessage("请输入问题", 2500)
            return
        if not SILICONFLOW_API_KEY:
            self.status.showMessage("未配置 SiliconFlow API Key", 5000)
            return

        self.btn_ai_ask.setEnabled(False)
        self.btn_ai_ask.setText("请求中...")
        self._ai_input_tokens = self._estimate_tokens(prompt)
        self._ai_output_tokens = 0
        self._ai_stream_text = ""
        self.ai_answer_edit.clear()
        self._update_token_labels()
        self.status.showMessage(f"正在请求模型：{model}", 2500)
        self.append_log(f"问AI请求已发送，模型：{model}")

        def _worker() -> None:
            payload = {
                "model": model,
                "stream": True,
                "messages": [
                    {"role": "system", "content": "你是一个简洁、可靠的中文助手。"},
                    {"role": "user", "content": prompt},
                ],
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
                            self._ai_bridge.chunk.emit(chunk)
                    content = "".join(chunks).strip()
                if not content:
                    content = "[接口返回为空]"
                self._ai_bridge.finished.emit(True, content)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
                self._ai_bridge.finished.emit(False, f"HTTPError {exc.code}: {body}")
            except Exception as exc:
                self._ai_bridge.finished.emit(False, repr(exc))

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
        self._ai_input_tokens = self._estimate_tokens(self.content_edit.toPlainText())
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

    def _on_ai_finished(self, ok: bool, message: str) -> None:
        self.btn_ai_ask.setEnabled(True)
        self.btn_ai_ask.setText("问AI")
        prefix = "AI回答" if ok else "问AI失败"
        if ok and self._ai_stream_text:
            self.status.showMessage(prefix, 3500)
            self.append_log(prefix)
            return
        self.ai_answer_edit.setPlainText(f"{prefix}:\n{message}")
        if ok:
            self._ai_output_tokens = self._estimate_tokens(message)
        self._update_token_labels()
        self.status.showMessage(prefix, 3500)
        self.append_log(prefix)

    def _on_ai_chunk(self, chunk: str) -> None:
        self._ai_stream_text += chunk
        self._ai_output_tokens = self._estimate_tokens(self._ai_stream_text)
        self.ai_answer_edit.setPlainText(f"AI回答:\n{self._ai_stream_text}")
        cursor = self.ai_answer_edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.ai_answer_edit.setTextCursor(cursor)
        self._update_token_labels()

    def _set_editor(self, note: NoteDto | None) -> None:
        self._ask_ai_mode = False
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
            self.content_edit.setPlainText("")
            self.state.current_note_id = None
        else:
            self.title_edit.setText(note.title)
            self.content_edit.setPlainText(note.content)
            self.state.current_note_id = note.id
        self.title_edit.blockSignals(False)
        self.content_edit.blockSignals(False)
        self.state.dirty = False
        self._update_title()
        self._update_token_labels()

    def _mark_dirty(self) -> None:
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
        return super().eventFilter(watched, event)

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
        try:
            self._text_font_size = max(8, min(28, int(str(font_size_raw))))
        except Exception:
            self._text_font_size = 10

    def _save_settings(self) -> None:
        try:
            self._settings.setValue("ui/favorites", json.dumps(self._favorite_order))
            self._settings.setValue(
                "ui/last_note_id",
                "" if self._last_open_note_id is None else str(self._last_open_note_id),
            )
            self._settings.setValue("ai/model", self.model_combo.currentText().strip() or self._selected_ai_model)
            self._settings.setValue("ui/text_font_size", str(self._text_font_size))
            self._settings.setValue("window/geometry", self.saveGeometry())
            self._settings.sync()
        except Exception:
            pass

    def _restore_window_state(self) -> None:
        geo = self._settings.value("window/geometry")
        if isinstance(geo, (bytes, bytearray)):
            self.restoreGeometry(geo)

