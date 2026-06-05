# -*- coding: utf-8 -*-

from __future__ import annotations

import subprocess
import sys
import json
import os
import re
import ctypes
import logging
import threading
import traceback
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime

from pathlib import Path

from PySide6 import QtCore, QtGui, QtUiTools, QtWidgets
try:
    import shiboken6
except Exception:  # pragma: no cover - shiboken6 随 PySide6 提供，兜底避免导入异常
    shiboken6 = None

from .api_client import ApiError, NotepadApi, NoteDto
from .folder_favorites_widget import FolderFavoritesWidget as FolderFavoritesPanel
from .settings_widget import SettingsWidget

# 从 l_qt_wgt_lib 导入代码编辑器组件
from l_qt_wgt_lib.smart_widget import (
    CodeEditorWidget,
    apply_indent_display_options,
    load_indent_display_options_from_settings,
)
from l_qt_wgt_lib.tray_window import TrayAwareMixin


SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_MODELS_URL = "https://api.siliconflow.cn/v1/models"
SILICONFLOW_PRICING_URL = "https://www.siliconflow.com/pricing"
DEFAULT_SILICONFLOW_MODEL = "Qwen/Qwen2.5-7B-Instruct"
SILICONFLOW_MODELS = [
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",
    "deepseek-ai/DeepSeek-V4-Flash",
    "Pro/zai-org/GLM-4.7",
]
SILICONFLOW_API_KEY = os.environ.get(
    "SILICONFLOW_API_KEY",
    "sk-gzwtmzfhglvibdbvrttmsuuqsyyjxghxlxzdhubdefmshqoi",
)

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_MODELS_URL = "https://open.bigmodel.cn/api/paas/v4/models"
DEFAULT_ZHIPU_MODEL = "glm-4-flash"
ZHIPU_MODELS = [
    "glm-4-flash",
    "glm-4-air",
    "glm-4-plus",
    "glm-4-long",
]
DEFAULT_ZHIPU_KEY = "263c58d09135c4f088b0d436e3b89bfb.hXFGig2ucu4xe5PT"
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", DEFAULT_ZHIPU_KEY)

DEFAULT_MODEL_PRESETS = SILICONFLOW_MODELS

# AI 提供商配置：{provider_name: (url, api_key, models, default_model)}
AI_PROVIDERS: dict[str, tuple[str, str, list[str], str]] = {
    "SiliconFlow": (SILICONFLOW_URL, SILICONFLOW_API_KEY, SILICONFLOW_MODELS, DEFAULT_SILICONFLOW_MODEL),
    "智谱 Zhipu": (ZHIPU_URL, ZHIPU_API_KEY, ZHIPU_MODELS, DEFAULT_ZHIPU_MODEL),
}
DEFAULT_PROVIDER = "SiliconFlow"
ASK_AI_ITEM_ID = "__ask_ai__"
ASK_AI_SESSION_PREFIX = "__ask_ai_session__:"
EXTERNAL_FILE_PREFIX = "__external_file__:"
EXTERNAL_FILES_STATE_NAME = "external_files.json"
LOG_VIEW_CONTENT_CACHE_KEY = "__l_notepad_log_view__"
LOG_LEVEL = logging.INFO
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger().setLevel(LOG_LEVEL)
logger = logging.getLogger(__name__)
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


_AI_PROMPT_PLACEHOLDER = (
    '在这里输入问题，然后点击"问AI"。\n\n'
    "支持多会话与上下文记忆。\n"
    "支持模型：硅基流动 SiliconFlow / 智谱 Zhipu。"
)
_AI_ANSWER_PLACEHOLDER = "AI 回答会显示在这里（支持多会话上下文）"


def _is_ai_prompt_placeholder_body(text: str) -> bool:
    """历史版本曾把提示文案写入文档，与 placeholder 等价时视为空。"""
    return text.strip() == _AI_PROMPT_PLACEHOLDER.strip()


def _set_code_editor_document(editor: CodeEditorWidget, text: str) -> None:
    """写入正文；空字符串时清空文档以显示 placeholder（勿把提示文案 setPlainText）。"""
    if text:
        editor.setPlainText(text)
    else:
        editor.clear()


def _plain_text_line_wrap_mode(mode) -> QtWidgets.QPlainTextEdit.LineWrapMode:
    """QTextEdit.LineWrapMode 与 QPlainTextEdit.LineWrapMode 在 PySide6 中类型不兼容。"""
    if isinstance(mode, QtWidgets.QPlainTextEdit.LineWrapMode):
        return mode
    plain = QtWidgets.QPlainTextEdit.LineWrapMode
    text = QtWidgets.QTextEdit.LineWrapMode
    if mode == text.NoWrap:
        return plain.NoWrap
    if mode in (text.WidgetWidth, text.FixedPixelWidth, text.FixedColumnWidth):
        return plain.WidgetWidth
    return plain(int(mode))


def _apply_text_edit_appearance(
    editor: CodeEditorWidget,
    source: QtWidgets.QTextEdit | CodeEditorWidget,
) -> None:
    if isinstance(source, CodeEditorWidget):
        placeholder = source.placeholderText()
        wrap_mode = source.lineWrapMode()
        read_only = source.isReadOnly()
        accept_drops = source.editor().acceptDrops()
        size_policy = source.sizePolicy()
    else:
        placeholder = source.placeholderText()
        wrap_mode = source.lineWrapMode()
        read_only = source.isReadOnly()
        accept_drops = source.acceptDrops()
        size_policy = source.sizePolicy()
    editor.clear()
    editor.setLineWrapMode(_plain_text_line_wrap_mode(wrap_mode))
    editor.setReadOnly(read_only)
    if accept_drops:
        editor.setAcceptDrops(True)
    # 正文为空；placeholder 仅作灰色提示，不写入文档
    editor.setPlaceholderText(placeholder)
    # 继承尺寸策略，确保撑满父布局
    editor.setSizePolicy(size_policy)


def replace_text_edit_with_code_editor(
    old_widget: QtWidgets.QTextEdit,
    obj_name: str,
) -> CodeEditorWidget:
    """将 .ui 中的 QTextEdit 替换为 CodeEditorWidget（保留布局与外观属性）。"""
    parent = old_widget.parentWidget()
    layout = parent.layout() if parent is not None else None
    new_editor = CodeEditorWidget(parent)
    new_editor.setObjectName(obj_name)
    _apply_text_edit_appearance(new_editor, old_widget)
    
    if layout is not None:
        # 查找旧 widget 在布局中的索引和属性
        index = -1
        stretch = 0
        alignment = 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.widget() is old_widget:
                index = i
                stretch = layout.stretch(i)
                alignment = layout.alignment() if hasattr(layout, 'alignment') else 0
                break
        
        # 移除旧 widget 并插入新 widget
        if index >= 0:
            layout.removeWidget(old_widget)
            # 根据布局类型插入到正确位置
            if isinstance(layout, QtWidgets.QVBoxLayout) or isinstance(layout, QtWidgets.QHBoxLayout):
                layout.insertWidget(index, new_editor, stretch)
            else:
                layout.addWidget(new_editor)
        else:
            layout.replaceWidget(old_widget, new_editor)
    
    old_widget.deleteLater()
    return new_editor


def create_code_editor_widget(
    parent: QtWidgets.QWidget,
    obj_name: str,
    template: QtWidgets.QTextEdit | CodeEditorWidget | None = None,
) -> CodeEditorWidget:
    """新建 CodeEditorWidget，可选从模板（QTextEdit 或已替换的 CodeEditorWidget）复制外观。"""
    editor = CodeEditorWidget(parent)
    editor.setObjectName(obj_name)
    editor.clear()
    if template is not None:
        _apply_text_edit_appearance(editor, template)
    return editor


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


# ---- 左侧文件树 delegate：将 "文件名\n日期" 分别绘制成两行不同颜色 ----

class _NoteTreeItemDelegate(QtWidgets.QStyledItemDelegate):
    """将 item 文本按换行符拆分成两行：第一行文件名粗体，第二行日期橙色。"""

    _COLOR_MAIN = QtGui.QColor("#E9EEF5")
    _COLOR_DATE = QtGui.QColor("#FFA657")
    _COLOR_FOLDER = QtGui.QColor("#89DDFF")
    _COLOR_ASK_AI = QtGui.QColor("#B794F4")
    # 父级目录高亮叠加色（alpha 0.8）
    _COLOR_PARENT_HIGHLIGHT = QtGui.QColor(77, 130, 220, 24)
    # 标记 item 为“父级高亮”的自定义 role
    _PARENT_HIGHLIGHT_ROLE = QtCore.Qt.ItemDataRole.UserRole + 99

    def paint(self, painter, option, index):
        # 先绘制背景/选中等默认样式
        opt_copy = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt_copy, index)
        opt_copy.text = ""
        style = option.widget.style() if option.widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem, opt_copy, painter, option.widget)

        # 若 item 被标记为“父级高亮”，在默认背景之上叠加高亮色块
        is_highlighted = bool(index.data(self._PARENT_HIGHLIGHT_ROLE))
        if is_highlighted:
            painter.save()
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.fillRect(option.rect, self._COLOR_PARENT_HIGHLIGHT)
            painter.restore()

        text = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        parts = text.split("\n") if isinstance(text, str) else [str(text)]
        rect = option.rect
        padding_left = 2
        total_lines = len(parts)
        # 紧凑行高：根据行数自适应
        line_height = max(14, rect.height() // max(total_lines, 1))
        x = rect.x() + padding_left
        y = rect.y() + max(2, (rect.height() - line_height * total_lines) // 2)
        for i, part in enumerate(parts):
            font = QtGui.QFont(option.font)
            if i == 0:
                # 第一行：文件名
                role = index.data(QtCore.Qt.ItemDataRole.UserRole) or ""
                if isinstance(role, str) and role.startswith("__folder__:"):
                    color = self._COLOR_FOLDER
                elif role == ASK_AI_ITEM_ID:
                    color = self._COLOR_ASK_AI
                else:
                    color = self._COLOR_MAIN
                font.setBold(True)
            else:
                color = self._COLOR_DATE
                font.setBold(False)
                if font.pointSize() and font.pointSize() > 8:
                    font.setPointSize(font.pointSize() - 2)
            painter.save()
            painter.setFont(font)
            painter.setPen(QtGui.QPen(color))
            line_rect = QtCore.QRect(x, y + i * line_height, rect.width() - padding_left - 4, line_height)
            painter.drawText(
                line_rect,
                QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft,
                part,
            )
            painter.restore()

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        text = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        lines = text.split("\n") if isinstance(text, str) else [str(text)]
        return QtCore.QSize(base.width(), max(32, len(lines) * 14 + 4))


class _NoteTreeProxyStyle(QtWidgets.QProxyStyle):
    """在深色主题下绘制树形层级指示线（虚线）。

    重写 PE_IndicatorBranch：
    - 先调用基类绘制折叠/展开箭头三角；
    - 再叠加绘制层级指示线（天蓝淡色虚线）。
    """

    _LINE_COLOR = QtGui.QColor(137, 221, 255, 70)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QtWidgets.QStyle.PrimitiveElement.PE_IndicatorBranch:
            super().drawPrimitive(element, option, painter, widget)
            return

        flags = option.state
        rect = option.rect
        SF = QtWidgets.QStyle.StateFlag

        # State_Children: 有子节点（需要画箭头）
        # State_Open    : 展开
        # State_Sibling : 当前节点下方还有兄弟节点（垂直线需要继续向下）
        has_children = bool(flags & SF.State_Children)
        has_sibling_below = bool(flags & SF.State_Sibling)

        # 基类绘制箭头三角（仅对有子节点的项）
        super().drawPrimitive(element, option, painter, widget)

        if rect.width() <= 2 or rect.height() <= 2 or widget is None:
            return

        # 通过 indexAt 判断是否为根节点：根节点不画层级指示线
        pos = QtCore.QPoint(rect.center().x(), rect.center().y())
        index = widget.indexAt(pos)
        if not index.isValid():
            return
        is_root = not index.parent().isValid()
        if is_root:
            return

        painter.save()
        pen = QtGui.QPen(self._LINE_COLOR)
        pen.setStyle(QtCore.Qt.PenStyle.DotLine)
        pen.setWidth(1)
        painter.setPen(pen)

        mid_x = rect.x() + rect.width() // 2
        mid_y = rect.y() + rect.height() // 2

        # 1) 垂直连接线
        if has_sibling_below:
            # 有兄弟在下方：竖线贯穿整个 rect
            painter.drawLine(mid_x, rect.top(), mid_x, rect.bottom())
        else:
            # 无兄弟在下方（最后一项）：竖线仅上半部（L 形）
            painter.drawLine(mid_x, rect.top(), mid_x, mid_y)

        # 2) 水平连接线：子节点画从中部向右的横线
        painter.drawLine(mid_x, mid_y, rect.right(), mid_y)

        painter.restore()


class RightPanel(QtWidgets.QWidget):
    """右侧面板（代码构建），每次重建都会创建全新实例，规避 PySide6 中
    QUiLoader 加载的 QMainWindow 被 C++ 层销毁导致的控件失效问题。

    对应 main_window.ui 中 splitter_main 右侧的 right_panel 部分。
    所有子控件均以属性暴露（title_edit、provider_combo、model_combo 等）。
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("right_panel")

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 10, 12, 10)

        # ---- 标题行：title_edit + provider_combo + label_model + model_combo + btn_refresh_models
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(8)

        self.title_edit = QtWidgets.QLineEdit(self)
        self.title_edit.setObjectName("title_edit")
        self.title_edit.setPlaceholderText("标题")
        title_row.addWidget(self.title_edit)

        self.provider_combo = QtWidgets.QComboBox(self)
        self.provider_combo.setObjectName("provider_combo")
        self.provider_combo.setMinimumWidth(120)
        title_row.addWidget(self.provider_combo)

        self.label_model = QtWidgets.QLabel("模型:", self)
        self.label_model.setObjectName("label_model")
        title_row.addWidget(self.label_model)

        self.model_combo = QtWidgets.QComboBox(self)
        self.model_combo.setObjectName("model_combo")
        self.model_combo.setMinimumWidth(260)
        title_row.addWidget(self.model_combo)

        self.btn_refresh_models = QtWidgets.QPushButton("刷新模型", self)
        self.btn_refresh_models.setObjectName("btn_refresh_models")
        title_row.addWidget(self.btn_refresh_models)
        root.addLayout(title_row)

        # ---- token 统计行
        token_row = QtWidgets.QHBoxLayout()
        token_row.setSpacing(8)

        self.label_input_tokens = QtWidgets.QLabel("输入: 0 tokens", self)
        self.label_input_tokens.setObjectName("label_input_tokens")
        token_row.addWidget(self.label_input_tokens)

        self.label_output_tokens = QtWidgets.QLabel("输出: 0 tokens", self)
        self.label_output_tokens.setObjectName("label_output_tokens")
        token_row.addWidget(self.label_output_tokens)

        self.label_cost = QtWidgets.QLabel("费用: 价格未知", self)
        self.label_cost.setObjectName("label_cost")
        token_row.addWidget(self.label_cost)

        self.label_price_source = QtWidgets.QLabel("价格: 未加载", self)
        self.label_price_source.setObjectName("label_price_source")
        token_row.addWidget(self.label_price_source)

        token_row.addStretch()

        self.tab_count = QtWidgets.QLabel("Tab: 0", self)
        self.tab_count.setObjectName("tab_count")
        token_row.addWidget(self.tab_count)
        root.addLayout(token_row)

        # ---- AI 标签页（含 Template 标签）
        self.ai_tabs = QtWidgets.QTabWidget(self)
        self.ai_tabs.setObjectName("ai_tabs")
        self.ai_tabs.setDocumentMode(True)
        self.ai_tabs.setTabsClosable(True)

        self._ai_tab_template_widget = QtWidgets.QWidget()
        self._ai_tab_template_widget.setObjectName("ai_tab_template")
        tpl = QtWidgets.QVBoxLayout(self._ai_tab_template_widget)
        tpl.setSpacing(10)
        tpl.setContentsMargins(10, 10, 10, 10)

        self._ai_template_content_edit = CodeEditorWidget(self._ai_tab_template_widget)
        self._ai_template_content_edit.setObjectName("CodeEditor")
        self._ai_template_content_edit.setPlaceholderText(
            '在这里输入问题，然后点击"问AI"。\n\n'
            "支持多会话与上下文记忆。\n支持模型：硅基流动 / 智谱 Zhipu。"
        )
        tpl.addWidget(self._ai_template_content_edit)

        self._ai_template_answer_edit = CodeEditorWidget(self._ai_tab_template_widget)
        self._ai_template_answer_edit.setObjectName("AiAnswerViewer")
        self._ai_template_answer_edit.setReadOnly(True)
        self._ai_template_answer_edit.setPlaceholderText("AI 回答会显示在这里（支持多会话上下文）")
        tpl.addWidget(self._ai_template_answer_edit)

        self.ai_tabs.addTab(self._ai_tab_template_widget, "Template")
        self.ai_tabs.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        root.addWidget(self.ai_tabs, 1)

        # ---- content_edit（普通笔记 / 外部文件 / 空态）
        self.content_edit = CodeEditorWidget(self)
        self.content_edit.setObjectName("content_edit")
        self.content_edit.setAcceptDrops(True)
        self.content_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        root.addWidget(self.content_edit, 1)

        # ---- ai_answer_edit（旧版 AI 回答区，默认隐藏）
        self.ai_answer_edit = CodeEditorWidget(self)
        self.ai_answer_edit.setObjectName("ai_answer_edit")
        self.ai_answer_edit.setVisible(False)
        self.ai_answer_edit.setReadOnly(True)
        self.ai_answer_edit.setPlaceholderText("AI 回答会显示在这里")
        self.ai_answer_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        root.addWidget(self.ai_answer_edit, 1)

        # ---- 操作按钮行
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_new = QtWidgets.QPushButton("新建", self)
        self.btn_new.setObjectName("btn_new")
        btn_row.addWidget(self.btn_new)

        self.btn_save = QtWidgets.QPushButton("保存", self)
        self.btn_save.setObjectName("btn_save")
        btn_row.addWidget(self.btn_save)

        self.btn_delete = QtWidgets.QPushButton("删除", self)
        self.btn_delete.setObjectName("btn_delete")
        btn_row.addWidget(self.btn_delete)

        self.btn_restart = QtWidgets.QPushButton("重启", self)
        self.btn_restart.setObjectName("btn_restart")
        btn_row.addWidget(self.btn_restart)

        btn_row.addStretch()

        self.btn_ai_ask = QtWidgets.QPushButton("问AI", self)
        self.btn_ai_ask.setObjectName("btn_ai_ask")
        btn_row.addWidget(self.btn_ai_ask)
        root.addLayout(btn_row)

    # ---- 公共方法 -------------------------------------------------------

    def attach_to_splitter(self, splitter: QtWidgets.QSplitter) -> None:
        """把 right_panel 作为第二个子控件加入 splitter，并设置拉伸策略。"""
        self.setParent(splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def bind_to(self, mw: "MainWindow") -> None:
        """将本实例的控件绑定到 MainWindow 属性，并记录到 _right_widget_refs。"""
        mapping = {
            "title_edit": self.title_edit,
            "provider_combo": self.provider_combo,
            "label_model": self.label_model,
            "model_combo": self.model_combo,
            "btn_refresh_models": self.btn_refresh_models,
            "label_input_tokens": self.label_input_tokens,
            "label_output_tokens": self.label_output_tokens,
            "label_cost": self.label_cost,
            "label_price_source": self.label_price_source,
            "tab_count": self.tab_count,
            "content_edit": self.content_edit,
            "ai_answer_edit": self.ai_answer_edit,
            "ai_tabs": self.ai_tabs,
            "btn_new": self.btn_new,
            "btn_save": self.btn_save,
            "btn_delete": self.btn_delete,
            "btn_ai_ask": self.btn_ai_ask,
            "btn_restart": self.btn_restart,
        }
        for key, widget in mapping.items():
            setattr(mw, key, widget)
            mw._right_widget_refs[key] = widget

        # MainWindow 还需引用 ai_tab_template 相关的隐藏控件（供 ai 标签页新建复用）
        mw._ai_tab_template_widget = self._ai_tab_template_widget
        mw._ai_template_content_edit = self._ai_template_content_edit
        mw._ai_template_answer_edit = self._ai_template_answer_edit

        # 隐藏 Template 标签页并移除它（保留模板控件引用供后续使用）
        tpl_index = mw.ai_tabs.indexOf(self._ai_tab_template_widget)
        if tpl_index >= 0:
            mw.ai_tabs.removeTab(tpl_index)
        self._ai_tab_template_widget.hide()


class MainWindow(TrayAwareMixin, QtWidgets.QMainWindow):
    def __init__(self, api: NotepadApi, restart_callback=None, hotkey_interval_callback=None, hotkey_key_callback=None) -> None:
        super().__init__()
        self.api = api
        self._restart_callback = restart_callback
        self._hotkey_interval_callback = hotkey_interval_callback
        self._hotkey_key_callback = hotkey_key_callback
        self.state = UiState()
        self._allow_close = False
        self._settings = QtCore.QSettings("Lugwit", "l_notepad_pc")
        self._favorite_order: list[int] = []
        self._last_open_note_id: int | None = None
        # 从设置中加载 AI 模型，如果保存过则使用保存的值
        saved_model = self._settings.value("ai/model", "")
        self._selected_ai_model = saved_model if saved_model else DEFAULT_SILICONFLOW_MODEL
        self._model_prices: dict[str, ModelPrice] = {}
        self._ai_input_tokens = 0
        self._ai_output_tokens = 0
        self._ai_stream_text = ""
        self._text_font_size = 10
        self._ask_ai_mode = False
        self._external_files: list[str] = []
        self._current_external_file: str | None = None
        self._ai_sessions: dict[str, AiSession] = {}
        self._current_ai_session_id: str | None = None
        self._ai_request_seq = 0
        self._active_ai_request_id: int | None = None
        self._in_selection_changed = False
        self._initializing = True
        # 右侧区域控件引用统一收口，避免只依赖局部属性导致引用丢失
        self._right_widget_refs: dict[str, QtWidgets.QWidget] = {}
        self._tray_icon: QtWidgets.QSystemTrayIcon | None = None
        self._log_level = "INFO"
        self._settings.setValue("log/level", self._log_level)
        self._previous_excepthook = sys.excepthook
        sys.excepthook = self._log_unhandled_exception
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
        self._load_external_files_state()
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

        # 右侧面板由代码构建（RightPanel 类），每次切换文件时重建实例，
        # 彻底规避旧版本中 QWidget 失效的问题。
        _cw = loaded.findChild(QtWidgets.QWidget, "centralwidget")
        self.setCentralWidget(_cw)
        # loaded 的 C++ QMainWindow 在 setCentralWidget 后已不再需要，
        # 显式删除避免 PySide6 的 Python 包装器干扰后续 findChild。
        del loaded
        import gc as _gc
        _gc.collect()

        # 通过 self.findChild 查找全部控件（centralwidget 已 reparent 到 self）
        self.tabs = self.findChild(QtWidgets.QTabWidget, "tabs")
        self.search_edit = self.findChild(QtWidgets.QLineEdit, "search_edit")
        self.notes_list = self.findChild(QtWidgets.QListWidget, "notes_list")
        self.notes_tree = self.findChild(QtWidgets.QTreeWidget, "notes_list")
        self.btn_refresh = self.findChild(QtWidgets.QPushButton, "btn_refresh")
        self.btn_favorite = self.findChild(QtWidgets.QPushButton, "btn_favorite")
        self.log_view = self.findChild(QtWidgets.QTextEdit, "log_view")
        self.help_view = self.findChild(QtWidgets.QTextEdit, "help_view")

        splitter = self.findChild(QtWidgets.QSplitter, "splitter_main")
        if splitter is None:
            raise RuntimeError("main_window.ui missing splitter_main")

        # 移除 .ui 中原有的 right_panel（将由代码构建的 RightPanel 替代）
        for _i in range(splitter.count()):
            _w = splitter.widget(_i)
            if _w is not None and _w.objectName() == "right_panel":
                _w.setParent(None)
                _w.deleteLater()
                break

        # 代码构建右侧面板
        self._right_panel = RightPanel(self)
        splitter.addWidget(self._right_panel)
        splitter.setStretchFactor(0, 0)  # 左侧不拉伸（保持紧凑）
        splitter.setStretchFactor(1, 1)  # 右侧占满剩余空间
        # 初始宽度分配：左侧 ~180px，右侧按窗口宽度补
        try:
            total_w = splitter.width() or 900
            left_w = min(200, max(170, int(total_w * 0.18)))
            splitter.setSizes([left_w, max(100, total_w - left_w)])
        except Exception:
            splitter.setSizes([180, 720])
        self._right_panel.bind_to(self)

        # 用 CodeEditorWidget 替换 .ui 中的 QTextEdit（日志 / 帮助）
        self.log_view = replace_text_edit_with_code_editor(self.log_view, "LogViewer")
        if self.help_view is not None:
            self.help_view = replace_text_edit_with_code_editor(self.help_view, "HelpViewer")
            self.help_view.setReadOnly(True)
            self.help_view.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            self._load_help_page()

        required_widgets = [
            ("tabs", self.tabs),
            ("search_edit", self.search_edit),
            ("notes_list_or_tree", self.notes_list or self.notes_tree),
            ("title_edit", self.title_edit),
            ("provider_combo", self.provider_combo),
            ("label_model", self.label_model),
            ("model_combo", self.model_combo),
            ("btn_refresh_models", self.btn_refresh_models),
            ("label_input_tokens", self.label_input_tokens),
            ("label_output_tokens", self.label_output_tokens),
            ("label_cost", self.label_cost),
            ("label_price_source", self.label_price_source),
            ("tab_count", self.tab_count),
            ("content_edit", self.content_edit),
            ("ai_answer_edit", self.ai_answer_edit),
            ("btn_new", self.btn_new),
            ("btn_save", self.btn_save),
            ("btn_delete", self.btn_delete),
            ("btn_refresh", self.btn_refresh),
            ("btn_favorite", self.btn_favorite),
            ("btn_ai_ask", self.btn_ai_ask),
            ("btn_restart", self.btn_restart),
            ("log_view", self.log_view),
            ("ai_tabs", self.ai_tabs),
        ]
        missing = [name for name, w in required_widgets if w is None]
        if missing:
            raise RuntimeError(
                f"main_window.ui missing required widget objectName(s): {missing}\n"
                f"  ui_path: {ui_path} (exists={ui_path.exists()})"
            )
        assert self.tab_count is not None
        self.tab_count.setText("Tab: 0")

        # 初始化 .ui 文件中的 "问AI" item data
        self._notes_tree_mode = self._qt_is_valid(getattr(self, "notes_tree", None))
        if self._notes_tree_mode:
            self._setup_notes_tree()
        else:
            if self.notes_list and self.notes_list.count() > 0:
                first_item = self.notes_list.item(0)
                if first_item.text().startswith("问AI"):
                    first_item.setData(QtCore.Qt.ItemDataRole.UserRole, ASK_AI_ITEM_ID)
                    font = first_item.font()
                    font.setBold(True)
                    first_item.setFont(font)
                    first_item.setFlags(first_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsDragEnabled)
                    first_item.setSizeHint(QtCore.QSize(0, 36))

            if self.notes_list:
                self.notes_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
            self.notes_list.itemSelectionChanged.connect(self._on_selection_changed)
            self.notes_list.itemDoubleClicked.connect(self._rename_note_from_item)
            self.notes_list.model().rowsMoved.connect(self._on_notes_rows_moved)

        self.search_edit.textChanged.connect(self._apply_filter)

        # 右侧面板信号连接（初始 + 切换文件时重建都会调用 _connect_right_panel_signals）
        self._connect_right_panel_signals()

        self._tab_main_widget = self.findChild(QtWidgets.QWidget, "tab_main")
        self._tab_log_widget = self.findChild(QtWidgets.QWidget, "tab_log")
        self._folder_favorites_panel = FolderFavoritesPanel(self, restart_callback=self._restart_app)
        self._folder_favorites_tab_index = -1
        self._settings_widget = SettingsWidget(self)
        if hasattr(self._settings_widget, "font_size_spin"):
            self._settings_widget.font_size_spin.blockSignals(True)
            self._settings_widget.font_size_spin.setValue(self._text_font_size)
            self._settings_widget.font_size_spin.blockSignals(False)
        if self.tabs is not None:
            # 插入文件夹收藏标签页（索引 1）
            self.tabs.insertTab(1, self._folder_favorites_panel, " 文件夹收藏")
            self._folder_favorites_tab_index = self.tabs.indexOf(self._folder_favorites_panel)
            # 插入设置标签页（在日志之前）
            log_index = self.tabs.indexOf(self._tab_log_widget)
            if log_index >= 0:
                self.tabs.insertTab(log_index, self._settings_widget, "设置")
            else:
                # 如果找不到日志标签页，插入到最后
                self.tabs.addTab(self._settings_widget, "设置")
            self._ensure_open_file_tab()
            self._settings_widget.indent_display_changed.connect(
                self._apply_indent_display_settings
            )
            self._settings_widget.folder_hotkey_changed.connect(
                self._on_folder_hotkey_button_changed
            )
            self._settings_widget.font_size_changed.connect(self._set_text_font_size)
            self.tabs.currentChanged.connect(self._on_main_tab_changed)

        self.log_view.setObjectName("LogViewer")
        self._apply_text_font_size()
        # log_view / help_view 的事件过滤（右侧面板的 content_edit / ai_answer_edit
        # 已在 _connect_right_panel_signals 中处理）
        for editor_widget in (self.log_view, self.help_view):
            if editor_widget is None:
                continue
            if hasattr(editor_widget, "editor"):
                editor = editor_widget.editor()
                editor.installEventFilter(self)
                editor.viewport().installEventFilter(self)

        self.status = self.statusBar()
        
        # 状态栏添加当前高亮模式显示
        self._highlight_mode_label = QtWidgets.QLabel("高亮: 日志文件")
        self.status.addPermanentWidget(self._highlight_mode_label)
        
        # 应用默认高亮模式（注意：需要在 _highlight_mode_label 创建之后调用）
        self._on_highlight_mode_changed("日志文件")
        
        self._refresh_official_prices()
        # 恢复 AI 标签页
        self._restore_ai_tabs()
        # 初始状态下隐藏 AI 相关控件（因为 _ask_ai_mode = False）
        if self.provider_combo:
            self.provider_combo.hide()
        if self.label_model:
            self.label_model.hide()
        if self.model_combo:
            self.model_combo.hide()
        if self.btn_refresh_models:
            self.btn_refresh_models.hide()
        if self.label_input_tokens:
            self.label_input_tokens.hide()
        if self.label_output_tokens:
            self.label_output_tokens.hide()
        if self.label_cost:
            self.label_cost.hide()
        if self.label_price_source:
            self.label_price_source.hide()
        self.refresh_notes()
        self._initializing = False
        self.append_log("日志窗口已初始化")
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._save_settings)

    def _mode_from_filename(self, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        ext_mode_map = {
            ".py": "python",
            ".md": "markdown",
            ".mdc": "markdown",
            ".markdown": "markdown",
            ".log": "log",
            ".txt": "text",
        }
        return ext_mode_map.get(ext, "text")

    def _note_file_path(self, title: str) -> Path:
        return Path(__file__).resolve().parent / "notepad_list" / title

    def _is_open_file_tab(self, index: int) -> bool:
        """判断顶层标签索引是否为「+ 打开文件」占位页。"""
        if self.tabs is None or index < 0 or index >= self.tabs.count():
            return False
        widget = self.tabs.widget(index)
        return widget is not None and widget.objectName() == "_open_file_holder"

    def _on_main_tab_changed(self, index: int) -> None:
        # 拦截顶层「+ 打开文件」标签页：触发打开文件对话框后返回上一个标签
        if self._is_open_file_tab(index):
            prev_idx = getattr(self, "_prev_main_tab_index", 0)
            try:
                self.tabs.blockSignals(True)
                if 0 <= prev_idx < self.tabs.count() and not self._is_open_file_tab(prev_idx):
                    self.tabs.setCurrentIndex(prev_idx)
                elif self.tabs.count() > 1:
                    self.tabs.setCurrentIndex(0)
                self.tabs.blockSignals(False)
            except Exception:
                try:
                    self.tabs.blockSignals(False)
                except Exception:
                    pass
            try:
                self.btn_open_external_file.click()
            except Exception:
                self._open_external_file()
            return

        # 记录上一次有效标签索引（排除「+ 打开文件」）
        if not self._is_open_file_tab(index):
            self._prev_main_tab_index = index

        if index < 0 or self.log_view is None or self._tab_log_widget is None:
            return
        if self.tabs.widget(index) is not self._tab_log_widget:
            return
        self.log_view.restore_text_from_cache(LOG_VIEW_CONTENT_CACHE_KEY, mode="log")

    def _on_highlight_mode_changed(self, mode_text: str) -> None:
        """切换内容编辑器的高亮模式。
        
        Args:
            mode_text: 模式名称
        """
        mode_map = {
            "普通文本": "text",
            "Python 代码": "python",
            "Markdown 源码": "markdown",
            "日志文件": "log"
        }
        mode = mode_map.get(mode_text, "log")
        
        # 使用 CodeEditorWidget 的 set_mode 方法切换模式
        self.content_edit.set_mode(mode)
        
        # 同步设置日志视图为 log 模式
        self.log_view.set_mode("log")
        
        # 更新状态栏
        self._current_highlight_mode = mode
        self._highlight_mode_label.setText(f"高亮: {mode_text}")
        self.status.showMessage(f"已切换到 {mode_text} 模式", 2000)

    def _auto_set_highlight_mode(self, filename: str) -> None:
        """根据文件扩展名自动设置高亮模式。
        
        Args:
            filename: 文件名
        """
        ext = Path(filename).suffix.lower()
        
        # 扩展名到模式的映射
        ext_mode_map = {
            '.py': ('python', 'Python 代码'),
            '.md': ('markdown', 'Markdown 源码'),
            '.mdc': ('markdown', 'Markdown 源码'),
            '.markdown': ('markdown', 'Markdown 源码'),
            '.log': ('log', '日志文件'),
            '.txt': ('text', '普通文本'),
        }
        
        mode, mode_text = ext_mode_map.get(ext, ('text', '普通文本'))
        
        # 设置编辑器模式
        self.content_edit.set_mode(mode)
        
        # 更新状态栏显示
        self._current_highlight_mode = mode
        self._highlight_mode_label.setText(f"高亮: {mode_text}")
        
        # 同步更新下拉框显示
        if hasattr(self, '_highlight_mode_combo'):
            index = self._highlight_mode_combo.findText(mode_text)
            if index >= 0:
                self._highlight_mode_combo.setCurrentIndex(index)

    def refresh_notes(self) -> None:
        try:
            notes = self.api.list_notes()
        except ApiError as e:
            self._show_error(str(e))
            return

        current_id = None if self._current_external_file else self.state.current_note_id
        if current_id is None and not self._current_external_file:
            current_id = self._last_open_note_id
        query = self.search_edit.text().strip()
        notes_sorted = self._sort_notes(notes)
        grouped_notes = self._group_notes_by_folder(notes_sorted, query=query)
        if self._notes_tree_mode:
            self._refresh_notes_tree(grouped_notes, query=query)
        else:
            self._refresh_notes_list(grouped_notes, query=query)

        if self._ask_ai_mode:
            self._set_ai_editor(self._current_ai_session_id)
        elif current_id is not None:
            selected = self._select_note_id(current_id)
            if not selected:
                self.append_log(f"刷新列表后未找到当前笔记: #{current_id}")
        elif self._current_external_file:
            selected = self._select_external_file(self._current_external_file)
            if not selected:
                self.append_log(f"刷新列表后未找到外部文件: {self._current_external_file}")
        elif self._notes_count() > 1:
            self._select_first_note_item()
        else:
            self._set_editor(None)
        self._update_favorite_button_label()

    def _apply_filter(self) -> None:
        # lightweight local filter; refresh keeps the list consistent
        self.refresh_notes()

    def _setup_notes_tree(self) -> None:
        tree = self.notes_tree
        if tree is None:
            return
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.setAnimated(True)
        tree.setExpandsOnDoubleClick(True)
        tree.setUniformRowHeights(False)
        tree.setIndentation(5)
        # 只保留第 0 列（名称 + 日期），隐藏其它列
        for col in range(1, tree.columnCount()):
            tree.setColumnHidden(col, True)
        # 自定义 delegate 实现两行不同颜色
        if getattr(self, "_note_tree_delegate", None) is None:
            self._note_tree_delegate = _NoteTreeItemDelegate(tree)
        tree.setItemDelegate(self._note_tree_delegate)
        # 自定义 ProxyStyle 绘制层级指示线
        if getattr(self, "_note_tree_style", None) is None:
            self._note_tree_style = _NoteTreeProxyStyle(tree.style())
        tree.setStyle(self._note_tree_style)
        tree.itemSelectionChanged.connect(self._on_selection_changed)
        tree.itemDoubleClicked.connect(self._rename_note_from_item)
        tree.itemExpanded.connect(self._on_folder_expanded_or_collapsed)
        tree.itemCollapsed.connect(self._on_folder_expanded_or_collapsed)

    def _notes_count(self) -> int:
        if self._notes_tree_mode and self.notes_tree is not None:
            count = 0
            root = self.notes_tree.invisibleRootItem()
            for i in range(root.childCount()):
                child = root.child(i)
                count += self._count_note_items_recursive(child)
            return count
        return self.notes_list.count() if self.notes_list is not None else 0

    def _count_note_items_recursive(self, item: QtWidgets.QTreeWidgetItem) -> int:
        item_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        count = 1 if isinstance(item_id, int) else 0
        for i in range(item.childCount()):
            count += self._count_note_items_recursive(item.child(i))
        return count

    def _refresh_notes_list(self, grouped_notes: list[tuple[str, list[NoteDto]]], *, query: str = "") -> None:
        self.notes_list.blockSignals(True)
        for i in range(self.notes_list.count() - 1, -1, -1):
            item = self.notes_list.item(i)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) != ASK_AI_ITEM_ID:
                self.notes_list.takeItem(i)
        if grouped_notes:
            for folder_name, folder_items in grouped_notes:
                folder_label = folder_name or "未归档"
                folder_header = QtWidgets.QListWidgetItem(f"📁 {folder_label}")
                folder_header.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                folder_header.setData(QtCore.Qt.ItemDataRole.UserRole, f"__folder__:{folder_label}")
                folder_font = folder_header.font()
                folder_font.setBold(True)
                folder_header.setFont(folder_font)
                folder_header.setBackground(QtGui.QBrush(QtGui.QColor(245, 245, 245)))
                folder_header.setForeground(QtGui.QBrush(QtGui.QColor(90, 90, 90)))
                folder_header.setSizeHint(QtCore.QSize(0, 28))
                self.notes_list.addItem(folder_header)
                for n in folder_items:
                    self.notes_list.addItem(self._create_note_list_item(n))
        elif query:
            empty_item = QtWidgets.QListWidgetItem(f"未找到匹配笔记：{query}")
            empty_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            empty_item.setData(QtCore.Qt.ItemDataRole.UserRole, "__empty__")
            empty_item.setForeground(QtGui.QBrush(QtGui.QColor(120, 120, 120)))
            empty_item.setSizeHint(QtCore.QSize(0, 28))
            self.notes_list.addItem(empty_item)
        for file_path in self._external_files:
            path = Path(file_path)
            if query and query.lower() not in path.name.lower():
                continue
            item = QtWidgets.QListWidgetItem(f"↗ {path.name}\n{file_path}")
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, f"{EXTERNAL_FILE_PREFIX}{file_path}")
            item.setToolTip(file_path)
            item.setSizeHint(QtCore.QSize(0, 44))
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
            self.notes_list.addItem(item)
        self.notes_list.blockSignals(False)

    def _refresh_notes_tree(self, grouped_notes: list[tuple[str, list[NoteDto]]], *, query: str = "") -> None:
        tree = self.notes_tree
        if tree is None:
            return

        # ① 清空前先收集所有文件夹的展开状态，合并到持久化字典中
        if not hasattr(self, "_tree_folder_expanded_state"):
            self._tree_folder_expanded_state = {}

        def _collect_expand_walk(node: QtWidgets.QTreeWidgetItem) -> None:
            role = node.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
            if isinstance(role, str) and (
                role.startswith("__folder__:") or role == ASK_AI_ITEM_ID
            ):
                self._tree_folder_expanded_state[role] = node.isExpanded()
            for i in range(node.childCount()):
                _collect_expand_walk(node.child(i))

        try:
            root_collect = tree.invisibleRootItem()
            for i in range(root_collect.childCount()):
                _collect_expand_walk(root_collect.child(i))
        except Exception:
            pass

        tree.blockSignals(True)
        tree.clear()
        ask_ai_item = QtWidgets.QTreeWidgetItem(["▾ 🤖 问AI"])
        ask_ai_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, ASK_AI_ITEM_ID)
        ask_ai_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "问AI")
        font = ask_ai_item.font(0)
        font.setBold(True)
        ask_ai_item.setFont(0, font)
        ask_ai_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#B794F4")))
        ask_ai_item.setBackground(0, QtGui.QBrush(QtGui.QColor("rgba(183, 148, 244, 0.06)")))
        tree.addTopLevelItem(ask_ai_item)
        if grouped_notes:
            for folder_name, folder_items in grouped_notes:
                if folder_name == "":
                    # 根目录文件（folder_name 为空）作为顶层独立项，
                    # 不再错误塞入「问AI」分组
                    for n in folder_items:
                        tree.addTopLevelItem(self._create_note_tree_item(n))
                else:
                    parent = self._ensure_tree_folder_item(tree, folder_name)
                    if parent is None:
                        parent = tree.invisibleRootItem()
                    for n in folder_items:
                        parent.addChild(self._create_note_tree_item(n))
        elif query:
            empty_item = QtWidgets.QTreeWidgetItem([f"未找到匹配笔记：{query}"])
            empty_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "__empty__")
            tree.addTopLevelItem(empty_item)
        for file_path in self._external_files:
            path = Path(file_path)
            if query and query.lower() not in path.name.lower():
                continue
            item = QtWidgets.QTreeWidgetItem([f"↗ {path.name}", file_path])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, f"{EXTERNAL_FILE_PREFIX}{file_path}")
            tree.addTopLevelItem(item)
        # ② 按持久化的展开状态恢复（未记录的文件夹默认展开；ASK_AI 默认折叠由外部控制）
        def _restore_expand_walk(node: QtWidgets.QTreeWidgetItem) -> None:
            role = node.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
            if isinstance(role, str) and (
                role.startswith("__folder__:") or role == ASK_AI_ITEM_ID
            ):
                expanded = self._tree_folder_expanded_state.get(role, True)
                node.setExpanded(bool(expanded))
                # 同步图标（▾/▸）和子项计数
                self._update_folder_icon(node)
            for i in range(node.childCount()):
                _restore_expand_walk(node.child(i))

        try:
            root_restore = tree.invisibleRootItem()
            for i in range(root_restore.childCount()):
                _restore_expand_walk(root_restore.child(i))
        except Exception:
            tree.expandAll()

        # 刷新所有文件夹项的图标和子项计数
        self._refresh_all_folder_icons()
        tree.blockSignals(False)

    def _refresh_all_folder_icons(self) -> None:
        """遍历树中所有文件夹/ASK_AI 项，更新展开图标和子项计数。"""
        tree = self.notes_tree
        if tree is None:
            return

        def walk(item: QtWidgets.QTreeWidgetItem) -> None:
            role = item.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
            if isinstance(role, str) and (
                role.startswith("__folder__:") or role == ASK_AI_ITEM_ID
            ):
                self._update_folder_icon(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        root = tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i))

    def _on_folder_expanded_or_collapsed(self, item: QtWidgets.QTreeWidgetItem) -> None:
        """itemExpanded/itemCollapsed 信号的处理：实时切换 ▾/▸，并持久化展开状态。"""
        self._update_folder_icon(item)
        # 持久化当前文件夹的展开状态，供刷新/重建后恢复
        role = item.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
        if isinstance(role, str) and (
            role.startswith("__folder__:") or role == ASK_AI_ITEM_ID
        ):
            if not hasattr(self, "_tree_folder_expanded_state"):
                self._tree_folder_expanded_state = {}
            self._tree_folder_expanded_state[role] = item.isExpanded()

    def _ensure_tree_folder_item(self, tree: QtWidgets.QTreeWidget, folder_path: str) -> QtWidgets.QTreeWidgetItem:
        parts = [p for p in folder_path.replace("\\", "/").split("/") if p]
        parent = tree.invisibleRootItem()
        current_path = []
        for part in parts:
            current_path.append(part)
            current_path_str = "/".join(current_path)
            found = None
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, QtCore.Qt.ItemDataRole.UserRole) == f"__folder__:{current_path_str}":
                    found = child
                    break
            if found is None:
                # 初始显示展开状态（▾），折叠后通过 _update_folder_icon 切换为 ▸
                found = QtWidgets.QTreeWidgetItem([f"▾ 📁 {part}"])
                found.setData(0, QtCore.Qt.ItemDataRole.UserRole, f"__folder__:{current_path_str}")
                found.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, part)  # 保留纯名称用于更新计数/图标
                folder_font = found.font(0)
                folder_font.setBold(True)
                found.setFont(0, folder_font)
                found.setForeground(0, QtGui.QBrush(QtGui.QColor("#89DDFF")))
                # 文件夹项的背景微亮，区别于子项
                found.setBackground(0, QtGui.QBrush(QtGui.QColor("rgba(137, 221, 255, 0.06)")))
                parent.addChild(found)
                # 默认展开
                found.setExpanded(True)
            parent = found
        return parent

    def _update_folder_icon(self, item: QtWidgets.QTreeWidgetItem) -> None:
        """根据文件夹展开状态更新图标（▾/▸）并刷新子项计数。"""
        if item is None:
            return
        role = item.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
        if not (isinstance(role, str) and role.startswith("__folder__:")):
            return
        base_name = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1) or ""
        if not base_name:
            # 回退：从当前文本中提取名称
            cur = (item.text(0) or "").strip()
            # 去除 ▾/▸ 与 📁 前缀
            for prefix in ("▾", "▸"):
                if cur.startswith(prefix):
                    cur = cur[len(prefix):].strip()
            if cur.startswith("📁"):
                cur = cur[2:].strip()
            # 去掉尾部的 " (N)" 计数
            if " (" in cur and cur.endswith(")"):
                cur = cur.rsplit(" (", 1)[0]
            base_name = cur
        # 统计直接子项（仅笔记项，排除子文件夹）
        note_count = 0
        for i in range(item.childCount()):
            child = item.child(i)
            cid = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(cid, int):
                note_count += 1
        icon = "▾" if item.isExpanded() else "▸"
        count_text = f" ({note_count})" if note_count > 0 else ""
        item.setText(0, f"{icon} 📁 {base_name}{count_text}")

    _MAX_FILENAME_CHARS = 30

    @staticmethod
    def _truncate_filename(name: str, limit: int = 30) -> str:
        """文件名超过 limit 个字符时，截断并加 '…'。"""
        if not name or len(name) <= limit:
            return name
        return name[:limit] + "…"

    def _create_note_list_item(self, note: NoteDto) -> QtWidgets.QListWidgetItem:
        display_title = self._truncate_filename(Path(note.title).name)
        title = f"※ {display_title}" if self._is_favorite(note.id) else display_title
        item = QtWidgets.QListWidgetItem(f"  {title}\n  {note.updated_at}")
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, note.id)
        item.setToolTip(f"{Path(note.title).name}  #{note.id}  {note.updated_at}")
        item.setSizeHint(QtCore.QSize(0, 40))
        item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        return item

    def _create_note_tree_item(self, note: NoteDto) -> QtWidgets.QTreeWidgetItem:
        display_title = self._truncate_filename(Path(note.title).name)
        title = f"※ {display_title}" if self._is_favorite(note.id) else display_title
        # 日期简短格式：将 ISO 8601 中的 'T' 替换为空格，并只保留分钟
        updated_at = (note.updated_at or "").replace("T", " ").split(":", 2)
        short_date = ":".join(updated_at[:2]) if len(updated_at) >= 2 else (note.updated_at or "")
        # 文件名 + 日期换行显示
        item = QtWidgets.QTreeWidgetItem([f"{title}\n{short_date}"])
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        # 文件名字体色，日期用浅灰（通过 foreground 统一色，让换行后的第二行更淡）
        item.setForeground(0, QtGui.QBrush(QtGui.QColor("#E9EEF5")))
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, note.id)
        item.setToolTip(0, f"{Path(note.title).name}  #{note.id}  {note.updated_at}")
        return item

    def _group_notes_by_folder(
        self,
        notes: list[NoteDto],
        *,
        query: str = "",
    ) -> list[tuple[str, list[NoteDto]]]:
        grouped: dict[str, list[NoteDto]] = {}
        for note in notes:
            if note.title.startswith("问AI"):
                continue
            if query and query.lower() not in note.title.lower():
                continue
            folder_name = self._note_folder_name(note.title)
            grouped.setdefault(folder_name, []).append(note)
        return sorted(
            grouped.items(),
            key=lambda kv: (kv[0] == "", kv[0].lower()),
        )

    @staticmethod
    def _note_folder_name(title: str) -> str:
        path = Path(title)
        parent = path.parent
        if str(parent) in {".", ""}:
            return ""
        return str(parent).replace("\\", "/")

    def _select_note_id(self, note_id: int) -> bool:
        if self._notes_tree_mode and self.notes_tree is not None:
            item = self._find_tree_item_by_note_id(note_id)
            if item is not None:
                self.notes_tree.setCurrentItem(item)
                return True
            return False
        for i in range(self.notes_list.count()):
            item = self.notes_list.item(i)
            item_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if item_id == ASK_AI_ITEM_ID:
                continue
            if isinstance(item_id, str) and item_id.startswith(ASK_AI_SESSION_PREFIX):
                continue
            if isinstance(item_id, str) and item_id.startswith(EXTERNAL_FILE_PREFIX):
                continue
            if isinstance(item_id, str) and (item_id.startswith("__folder__:") or item_id.startswith("__empty__")):
                continue
            if int(item_id) == int(note_id):
                self.notes_list.setCurrentRow(i)
                return True
        return False

    def _select_external_file(self, file_path: str) -> bool:
        target = f"{EXTERNAL_FILE_PREFIX}{file_path}"
        if self._notes_tree_mode and self.notes_tree is not None:
            item = self._find_tree_item_by_user_role(target)
            if item is not None:
                self.notes_tree.setCurrentItem(item)
                return True
            return False
        for i in range(self.notes_list.count()):
            item = self.notes_list.item(i)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == target:
                self.notes_list.setCurrentRow(i)
                return True
        return False

    def _select_ai_session_id(self, session_id: str) -> None:
        target = f"{ASK_AI_SESSION_PREFIX}{session_id}"
        for i in range(self.notes_list.count()):
            item = self.notes_list.item(i)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == target:
                self.notes_list.setCurrentRow(i)
                return

    def _find_tree_item_by_user_role(self, target) -> QtWidgets.QTreeWidgetItem | None:
        if self.notes_tree is None:
            return None
        root = self.notes_tree.invisibleRootItem()
        stack = [root.child(i) for i in range(root.childCount())]
        while stack:
            item = stack.pop(0)
            if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == target:
                return item
            for i in range(item.childCount()):
                stack.append(item.child(i))
        return None

    def _find_tree_item_by_note_id(self, note_id: int) -> QtWidgets.QTreeWidgetItem | None:
        return self._find_tree_item_by_user_role(note_id)

    def _on_selection_changed(self) -> None:
        if self._in_selection_changed:
            return
        self._in_selection_changed = True
        try:
            self._on_selection_changed_inner()
        except Exception:
            self._append_exception_log("选择切换异常")
            raise
        finally:
            self._in_selection_changed = False
            # 选中子文件时，高亮其父级目录链
            self._highlight_parent_folders()

    # 文件夹默认底色缓存（role -> QColor）
    _FOLDER_DEFAULT_BG: dict = {
        "__ask_ai__": QtGui.QColor(183, 148, 244, 15),
    }
    _FOLDER_DEFAULT_BG_DEFAULT = QtGui.QColor(137, 221, 255, 15)
    # 父级高亮色（透明度 0.8）
    _FOLDER_HIGHLIGHT_BG = QtGui.QColor(77, 130, 220, 50)

    def _highlight_parent_folders(self) -> None:
        """清除所有文件夹的高亮，并为选中项的所有祖先文件夹加高亮底色。

        通过 UserRole+99 标记 item，由 _NoteTreeItemDelegate 在 paint 中叠加绘制，
        避免 QSS 中 ::item{background:transparent} 覆盖 setBackground。
        """
        tree = self.notes_tree
        if tree is None or not self._notes_tree_mode:
            return

        highlight_role = _NoteTreeItemDelegate._PARENT_HIGHLIGHT_ROLE

        # 1. 重置所有文件夹的高亮标记和底色
        root = tree.invisibleRootItem()

        def reset_walk(node: QtWidgets.QTreeWidgetItem) -> None:
            role = node.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
            if isinstance(role, str) and (
                role.startswith("__folder__:") or role == ASK_AI_ITEM_ID
            ):
                node.setData(0, highlight_role, False)
                default = (
                    self._FOLDER_DEFAULT_BG.get(role)
                    or self._FOLDER_DEFAULT_BG_DEFAULT
                )
                node.setBackground(0, QtGui.QBrush(default))
            for i in range(node.childCount()):
                reset_walk(node.child(i))

        for i in range(root.childCount()):
            reset_walk(root.child(i))

        # 2. 找出所有选中项的祖先文件夹，并标记高亮
        selected = tree.selectedItems()
        if not selected:
            tree.viewport().update()
            return

        highlight_brush = QtGui.QBrush(self._FOLDER_HIGHLIGHT_BG)
        highlighted: set[int] = set()

        for item in selected:
            # 若当前项本身就是文件夹，也需高亮
            role = item.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
            if isinstance(role, str) and (
                role.startswith("__folder__:") or role == ASK_AI_ITEM_ID
            ):
                if id(item) not in highlighted:
                    item.setData(0, highlight_role, True)
                    item.setBackground(0, highlight_brush)
                    highlighted.add(id(item))
            # 向上遍历父链
            cur = item.parent()
            while cur is not None:
                role = cur.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
                if isinstance(role, str) and (
                    role.startswith("__folder__:") or role == ASK_AI_ITEM_ID
                ):
                    if id(cur) not in highlighted:
                        cur.setData(0, highlight_role, True)
                        cur.setBackground(0, highlight_brush)
                        highlighted.add(id(cur))
                cur = cur.parent()

        tree.viewport().update()

    def _selected_note_items(self) -> list[object]:
        if self._notes_tree_mode and self.notes_tree is not None:
            return self.notes_tree.selectedItems()
        return self.notes_list.selectedItems()

    def _selected_item_id(self):
        items = self._selected_note_items()
        if not items:
            return None
        item = items[0]
        if self._notes_tree_mode and isinstance(item, QtWidgets.QTreeWidgetItem):
            return item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        return item.data(QtCore.Qt.ItemDataRole.UserRole)

    def _on_selection_changed_inner(self) -> None:
        items = self._selected_note_items()
        item_id = self._selected_item_id()
        self.append_debug_log(f"选择切换: item_id={item_id!r}, selected={len(items)}")
        if item_id == ASK_AI_ITEM_ID:
            if not self._initializing and self.state.current_note_id is not None:
                self._auto_save_note("切换到问AI前", detail_ui=True)
            self.append_debug_log("切换到 AI 模式")
            self._set_ai_editor(self._current_ai_session_id)
            # AI 会话不再在列表中显示，不需要选择
            return
        # AI 会话不再在列表中，不再处理 ASK_AI_SESSION_PREFIX

        if self.state.dirty:
            self.append_debug_log("切换到普通笔记/外部文件前触发自动保存")
            self._auto_save_note("切换日志前", detail_ui=True)

        if not items:
            self.append_log("未选中任何条目，切换到空编辑器")
            self._set_editor(None)
            return

        self._ask_ai_mode = False
        item_id = self._selected_item_id()
        if isinstance(item_id, str) and item_id.startswith(EXTERNAL_FILE_PREFIX):
            self.append_log(f"切换到外部文件: {item_id[len(EXTERNAL_FILE_PREFIX):]}")
            self._set_external_file_editor(item_id[len(EXTERNAL_FILE_PREFIX):])
            return
        note_id = int(item_id)
        try:
            note = self.api.get_note(note_id)
        except ApiError as e:
            self._show_error(str(e))
            return
        self.append_log(f"切换到普通笔记: #{note.id} {note.title}")
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
        if self._current_external_file:
            self._save_external_file()
            return
        title = self.title_edit.text().strip() or "未命名"
        content = self._get_content_text()
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

    @staticmethod
    def _format_file_size(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        if num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        return f"{num_bytes / (1024 * 1024):.1f} MB"

    def _notepad_list_dir(self) -> Path:
        return Path(__file__).resolve().parent / "notepad_list"

    def _resolve_saved_path(self, title: str, external_path: str | None = None) -> Path | None:
        if external_path:
            path = Path(external_path)
            return path if path.is_file() else None
        matches = list(self._notepad_list_dir().rglob(title))
        if len(matches) == 1:
            return matches[0]
        return None

    def _stat_from_path_or_fallback(
        self,
        path: Path | None,
        *,
        content: str = "",
        updated_at: str | None = None,
    ) -> tuple[int | None, str | None]:
        if path is not None and path.is_file():
            try:
                st = path.stat()
                saved_at = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                return int(st.st_size), saved_at
            except OSError:
                pass
        size_bytes = len(content.encode("utf-8")) if content else None
        saved_at = None
        if updated_at:
            saved_at = str(updated_at)[:19].replace("T", " ")
        return size_bytes, saved_at

    def set_tray_icon(self, tray: QtWidgets.QSystemTrayIcon | None) -> None:
        self._tray_icon = tray

    def _notify_tray(
        self,
        title: str,
        message: str,
        *,
        icon: QtWidgets.QSystemTrayIcon.MessageIcon = QtWidgets.QSystemTrayIcon.MessageIcon.Information,
        timeout_ms: int = 2500,
    ) -> None:
        if self._tray_icon is None:
            return
        self._tray_icon.showMessage(title, message, icon, timeout_ms)

    def _report_autosave(
        self,
        reason: str,
        *,
        ok: bool,
        filename: str,
        size_bytes: int | None = None,
        saved_at: str | None = None,
        error: str | None = None,
        notify_tray: bool = False,
        detail_ui: bool = False,
    ) -> None:
        size_text = self._format_file_size(size_bytes) if size_bytes is not None else "-"
        date_text = saved_at or "-"

        if detail_ui:
            if ok:
                msg = f"{reason} 自动保存：{filename} | 大小 {size_text} | {date_text}"
                timeout = 3500
            else:
                err = error or "未知错误"
                msg = f"{reason} 自动保存失败：{filename} | 大小 {size_text} | {date_text} | {err}"
                timeout = 5000
            self.status.showMessage(msg, timeout)
            self.append_log(msg)

        if notify_tray:
            if ok:
                self._notify_tray("L Notepad", f"已自动保存：{filename}（{size_text}）")
            else:
                err = error or "未知错误"
                self._notify_tray(
                    "L Notepad",
                    f"自动保存失败：{err}",
                    icon=QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    timeout_ms=5000,
                )

    def _auto_save_note(
        self,
        reason: str,
        *,
        notify_tray: bool = False,
        detail_ui: bool = False,
    ) -> None:
        if self._ask_ai_mode or not self.state.dirty:
            return
        if self._current_external_file:
            self._save_external_file(reason, notify_tray=notify_tray, detail_ui=detail_ui)
            return
        if self.state.current_note_id is None:
            return
        if not self._qt_is_valid(getattr(self, "title_edit", None)):
            self.append_log(f"{reason} 自动保存跳过：title_edit 已失效")
            return
        if not self._qt_is_valid(getattr(self, "content_edit", None)):
            self.append_log(f"{reason} 自动保存跳过：content_edit 已失效")
            return
        title = self.title_edit.text().strip() or "未命名"
        content = self._get_content_text()
        try:
            note = self.api.update_note(self.state.current_note_id, title=title, content=content)
        except ApiError as e:
            if detail_ui or notify_tray:
                self._report_autosave(
                    reason,
                    ok=False,
                    filename=title,
                    error=str(e),
                    notify_tray=notify_tray,
                    detail_ui=detail_ui,
                )
            else:
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

        saved_path = self._resolve_saved_path(note.title)
        size_bytes, saved_at = self._stat_from_path_or_fallback(
            saved_path,
            content=content,
            updated_at=note.updated_at,
        )
        if detail_ui or notify_tray:
            self._report_autosave(
                reason,
                ok=True,
                filename=note.title,
                size_bytes=size_bytes,
                saved_at=saved_at,
                notify_tray=notify_tray,
                detail_ui=detail_ui,
            )
        else:
            self.status.showMessage(f"{reason} 已自动保存：#{note.id}", 2500)
            self.append_log(f"{reason} 自动保存日志文件：#{note.id}")

    def _delete_note(self) -> None:
        if self._ask_ai_mode:
            return
        if self._current_external_file:
            file_path = self._current_external_file
            ret = QtWidgets.QMessageBox.question(self, "移除外部文件", f"从列表移除外部文件？\n{file_path}")
            if ret != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self._external_files = [x for x in self._external_files if x != file_path]
            self._current_external_file = None
            self.state.current_note_id = None
            self.state.dirty = False
            self._save_external_files_state()
            self.refresh_notes()
            self.status.showMessage("已从列表移除，硬盘文件未删除", 2500)
            self._update_title()
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

    def _external_files_state_path(self) -> Path:
        return Path(__file__).resolve().parent / EXTERNAL_FILES_STATE_NAME

    def _load_external_files_state(self) -> None:
        state_path = self._external_files_state_path()
        self._external_files = []
        self._current_external_file = None
        if not state_path.exists():
            return
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        files = data.get("files", []) if isinstance(data, dict) else []
        current = data.get("current", "") if isinstance(data, dict) else ""
        if isinstance(files, list):
            seen: set[str] = set()
            for item in files:
                file_path = str(item).strip()
                if file_path and file_path not in seen:
                    seen.add(file_path)
                    self._external_files.append(file_path)
        current_path = str(current).strip()
        if current_path in self._external_files:
            self._current_external_file = current_path

    def _save_external_files_state(self) -> None:
        data = {
            "files": self._external_files,
            "current": self._current_external_file or "",
        }
        self._external_files_state_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_open_file_tab(self) -> None:
        """在顶层 tabs 末尾添加「+ 打开文件」占位标签（需在其它动态标签插入之后调用）。"""
        if self.tabs is None:
            return
        for i in range(self.tabs.count() - 1, -1, -1):
            widget = self.tabs.widget(i)
            if widget is not None and widget.objectName() == "_open_file_holder":
                return
        holder = QtWidgets.QWidget()
        holder.setObjectName("_open_file_holder")
        self.tabs.addTab(holder, "+ 打开文件")

    def _open_external_file(self) -> None:
        if self.state.dirty and not self._confirm_discard():
            return
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "打开硬盘文件", "")
        if not file_path:
            return
        file_path = str(Path(file_path))
        if file_path not in self._external_files:
            self._external_files.insert(0, file_path)
        self._current_external_file = file_path
        self.state.current_note_id = None
        self.state.dirty = False
        self._save_external_files_state()
        self.refresh_notes()
        self._select_external_file(file_path)

    def _qt_is_valid(self, widget) -> bool:
        """检查 PySide 包装对象背后的 C++ 对象是否仍然有效。"""
        if widget is None:
            return False
        if shiboken6 is None:
            return True
        try:
            return bool(shiboken6.isValid(widget))
        except Exception:
            return False

    def _remember_right_widget(self, key: str, widget: QtWidgets.QWidget | None) -> None:
        if widget is None:
            return
        self._right_widget_refs[key] = widget

    def _get_right_widget(self, key: str, fallback_attr: str | None = None, expected_type=None):
        widget = self._right_widget_refs.get(key)
        if widget is None and fallback_attr is not None:
            widget = getattr(self, fallback_attr, None)
        if expected_type is not None and not isinstance(widget, expected_type):
            widget = getattr(self, key, None)
        if self._qt_is_valid(widget):
            return widget
        if fallback_attr:
            widget = getattr(self, fallback_attr, None)
            if self._qt_is_valid(widget):
                self._right_widget_refs[key] = widget
                return widget
        return None

    def _refresh_core_widget_refs(self) -> None:
        """右侧面板失效时，通过重建 RightPanel 实例刷新全部右侧控件引用。

        只有当关键右侧控件失效时才重建，避免每次调用都丢失编辑状态。
        """
        critical = ("title_edit", "content_edit", "ai_tabs", "btn_save", "ai_answer_edit")
        need_rebuild = False
        for key in critical:
            widget = self._right_widget_refs.get(key) or getattr(self, key, None)
            if not self._qt_is_valid(widget):
                need_rebuild = True
                break
        if not need_rebuild:
            return
        self._rebuild_right_panel()

    def _rebuild_right_panel(self) -> None:
        """销毁旧的右侧面板并新建 RightPanel 实例。"""
        splitter = self.findChild(QtWidgets.QSplitter, "splitter_main")
        if splitter is None:
            self.append_log("警告: 重建右侧面板时找不到 splitter_main，跳过")
            return

        # 移除当前右侧面板（含旧 RightPanel 及其全部子控件）
        for _i in range(splitter.count()):
            _w = splitter.widget(_i)
            if _w is not None and _w.objectName() == "right_panel":
                _w.setParent(None)
                _w.deleteLater()
                break

        self._right_panel = RightPanel(self)
        splitter.addWidget(self._right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self._right_panel.bind_to(self)
        self._connect_right_panel_signals()

    def _connect_right_panel_signals(self) -> None:
        """把重建后的右侧面板控件与 MainWindow 信号连接起来。"""
        # AI 标签页
        self.ai_tabs.tabCloseRequested.connect(self._on_ai_tab_close_requested)
        self.ai_tabs.currentChanged.connect(self._on_ai_tab_changed)
        self.ai_tabs.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.ai_tabs.customContextMenuRequested.connect(self._on_ai_tabs_context_menu)
        self._setup_ai_tab_close_buttons()

        # 标题编辑
        self.title_edit.textEdited.connect(self._mark_dirty)

        # 提供商选择
        self.provider_combo.clear()
        for provider_name in AI_PROVIDERS:
            self.provider_combo.addItem(provider_name)
        saved_provider = self._settings.value("ai/provider", DEFAULT_PROVIDER)
        if saved_provider in AI_PROVIDERS:
            self.provider_combo.setCurrentText(saved_provider)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)

        # 模型列表
        self._update_model_list_for_provider(self.provider_combo.currentText())
        self.model_combo.setCurrentText(self._selected_ai_model)
        self.model_combo.currentTextChanged.connect(self._on_ai_model_changed)
        self.btn_refresh_models.clicked.connect(self._refresh_ai_models)

        # 笔记区编辑器
        self.content_edit.editor().textChanged.connect(self._mark_dirty)
        self.content_edit.editor().textChanged.connect(self._update_realtime_token_stats)

        # 「打开文件」作为顶层标签栏（主界面/帮助/日志）末尾的按钮式标签
        if not self._qt_is_valid(getattr(self, "btn_open_external_file", None)):
            self.btn_open_external_file = QtWidgets.QPushButton("+ 打开文件")
            self.btn_open_external_file.setObjectName("OpenFileTabButton")
            self.btn_open_external_file.setStyleSheet(
                """QPushButton#OpenFileTabButton {
                    padding: 3px 12px;
                    font-size: 12px;
                    border: 1px solid rgba(137, 221, 255, 0.25);
                    border-radius: 8px;
                    background: rgba(255,255,255,0.04);
                    color: #89DDFF;
                    margin: 0 4px;
                }
                QPushButton#OpenFileTabButton:hover {
                    background: rgba(77, 163, 255, 0.18);
                    border-color: rgba(77, 163, 255, 0.55);
                }
                QPushButton#OpenFileTabButton:pressed {
                    background: rgba(77, 163, 255, 0.28);
                }"""
            )
            self.btn_open_external_file.clicked.connect(self._open_external_file)

        self.btn_save.setObjectName("PrimaryButton")
        self.btn_ai_ask.setObjectName("PrimaryButton")
        self.btn_delete.setObjectName("DangerButton")

        # 按钮
        self.btn_new.clicked.connect(self._new_note)
        self.btn_save.clicked.connect(self._save_note)
        self.btn_delete.clicked.connect(self._delete_note)
        self.btn_refresh.clicked.connect(self.refresh_notes)
        self.btn_favorite.clicked.connect(self._toggle_favorite_current)
        self.btn_ai_ask.clicked.connect(self._ask_ai)
        self.btn_restart.clicked.connect(self._restart_app)

        # 编辑器事件过滤
        for editor_widget in (self.content_edit, self.ai_answer_edit):
            if hasattr(editor_widget, "editor"):
                editor = editor_widget.editor()
                editor.installEventFilter(self)
                editor.viewport().installEventFilter(self)

        # 初始隐藏 AI 相关控件
        for w in (self.provider_combo, self.label_model, self.model_combo,
                  self.btn_refresh_models, self.label_input_tokens,
                  self.label_output_tokens, self.label_cost, self.label_price_source):
            if w is not None:
                w.hide()


    def _ensure_main_tab_active(self, reason: str) -> None:
        """切换左侧笔记/AI 时确保主界面页是当前页，否则右侧父级不可见。"""
        self._refresh_core_widget_refs()
        if not self._qt_is_valid(getattr(self, "tabs", None)) or not self._qt_is_valid(getattr(self, "_tab_main_widget", None)):
            return
        main_index = self.tabs.indexOf(self._tab_main_widget)
        if main_index < 0:
            return
        if self.tabs.currentIndex() != main_index:
            self.tabs.setCurrentIndex(main_index)
            self.append_log(f"已切回主界面页: reason={reason}, index={main_index}")

    def _set_right_panel_mode(self, mode: str) -> None:
        """统一切换右侧主编辑区域，避免 AI/普通笔记/外部文件状态残留。"""
        self._refresh_core_widget_refs()
        self._ensure_main_tab_active(f"right_panel:{mode}")
        is_ai = mode == "ai"
        is_editor = mode in {"note", "external", "empty"}
        ai_tabs_valid = self._qt_is_valid(getattr(self, "ai_tabs", None))
        content_valid = self._qt_is_valid(getattr(self, "content_edit", None))
        answer_valid = self._qt_is_valid(getattr(self, "ai_answer_edit", None))
        if ai_tabs_valid:
            self.ai_tabs.setVisible(is_ai)
        elif is_ai:
            self.append_log("警告: ai_tabs 已失效，无法显示 AI 面板")
        if content_valid:
            self.content_edit.setVisible(is_editor)
        else:
            self.append_log("警告: content_edit 已失效，无法显示普通笔记面板")
        if answer_valid:
            self.ai_answer_edit.hide()
        if is_ai and ai_tabs_valid:
            self.ai_tabs.raise_()
        elif is_editor and content_valid:
            self._raise_content_editor_surface()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        parent = self.content_edit.parentWidget() if content_valid else None
        self.append_log(
            "右侧面板切换: "
            f"mode={mode}, current_tab={self.tabs.currentIndex() if self._qt_is_valid(getattr(self, 'tabs', None)) else -1}, "
            f"ai_tabs_valid={ai_tabs_valid}, "
            f"ai_tabs={self.ai_tabs.isVisible() if ai_tabs_valid else None}, "
            f"content_valid={content_valid}, "
            f"content_edit={self.content_edit.isVisible() if content_valid else None}, "
            f"content_hidden={self.content_edit.isHidden() if content_valid else None}, "
            f"content_parent_visible={parent.isVisible() if parent else None}, "
            f"content_size={self.content_edit.size().width()}x{self.content_edit.size().height() if content_valid else -1}, "
            f"ai_answer_edit={self.ai_answer_edit.isVisible() if answer_valid else None}, "
            f"ai_tabs_count={self.ai_tabs.count() if ai_tabs_valid else None}"
        )

    def _raise_content_editor_surface(self) -> None:
        """抬起右侧内容编辑区域，同时保留 Markdown 预览层在最上方。"""
        if not self._qt_is_valid(getattr(self, "content_edit", None)):
            return
        self.content_edit.raise_()
        try:
            if self.content_edit.is_markdown_preview_mode():
                editor = self.content_edit.editor()
                preview_view = getattr(editor, "_preview_view", None)
                if preview_view is not None and self._qt_is_valid(preview_view):
                    if hasattr(editor, "_sync_markdown_preview_geometry"):
                        editor._sync_markdown_preview_geometry()
                    preview_view.show()
                    preview_view.raise_()
        except Exception as exc:
            self.append_debug_log(f"Markdown 预览层置顶跳过: {exc!r}")

    def _set_ai_controls_visible(self, visible: bool) -> None:
        """统一切换 AI 顶部/状态栏相关控件可见性。"""
        widgets = (
            self.provider_combo,
            self.label_model,
            self.model_combo,
            self.btn_refresh_models,
            self.label_input_tokens,
            self.label_output_tokens,
            self.label_cost,
            self.label_price_source,
        )
        changed = 0
        skipped: list[str] = []
        for widget in widgets:
            if not self._qt_is_valid(widget):
                if widget is not None:
                    try:
                        skipped.append(widget.objectName() or widget.__class__.__name__)
                    except Exception:
                        skipped.append(type(widget).__name__)
                continue
            widget.setVisible(visible)
            changed += 1
        if skipped:
            self.append_log(f"AI 控件可见性跳过已失效控件: {', '.join(skipped)}")
        self.append_log(f"AI 控件可见性: {visible}, changed={changed}")

    def _set_external_file_editor(self, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            self._show_error(f"外部文件不存在：{file_path}")
            return
        mode = self._mode_from_filename(path.name)
        self._ask_ai_mode = False
        self._current_ai_session_id = None
        self._current_external_file = str(path)
        self.state.current_note_id = None
        if not self.content_edit.load_text_file_cached(path, mode=mode):
            self._show_error(f"打开外部文件失败：{file_path}")
            self._current_external_file = None
            return
        self.title_edit.blockSignals(True)
        self.content_edit.blockSignals(True)
        self.title_edit.setText(path.name)
        self.title_edit.blockSignals(False)
        self.content_edit.blockSignals(False)
        self._set_right_panel_mode("external")
        self._set_ai_controls_visible(False)
        self.btn_ai_ask.setEnabled(False)
        self.btn_save.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_favorite.setEnabled(False)
        self._auto_set_highlight_mode(path.name)
        self.state.dirty = False
        self._save_external_files_state()
        self._update_title()

    def _save_external_file(
        self,
        reason: str | None = None,
        *,
        notify_tray: bool = False,
        detail_ui: bool = False,
    ) -> None:
        if not self._current_external_file:
            return
        path = Path(self._current_external_file)
        filename = path.name
        content = self._get_content_text()
        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            if reason and (detail_ui or notify_tray):
                self._report_autosave(
                    reason,
                    ok=False,
                    filename=filename,
                    error=str(exc),
                    notify_tray=notify_tray,
                    detail_ui=detail_ui,
                )
            else:
                self.status.showMessage(f"保存外部文件失败：{exc}", 5000)
                self.append_log(f"保存外部文件失败：{exc}")
            return
        self.state.dirty = False
        self._save_external_files_state()
        self._update_title()
        size_bytes, saved_at = self._stat_from_path_or_fallback(path)
        if reason and (detail_ui or notify_tray):
            self._report_autosave(
                reason,
                ok=True,
                filename=filename,
                size_bytes=size_bytes,
                saved_at=saved_at,
                notify_tray=notify_tray,
                detail_ui=detail_ui,
            )
        elif reason:
            self.status.showMessage(f"{reason} 已保存外部文件", 2500)
        else:
            self.status.showMessage("已保存外部文件", 2500)

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
        self.append_log(
            f"点击置顶/收藏: ask_ai_mode={self._ask_ai_mode}, current_note_id={self.state.current_note_id}, "
            f"current_external={self._current_external_file!r}"
        )
        if self._ask_ai_mode:
            self.append_log("当前处于 AI 模式，忽略置顶/收藏切换")
            return
        if self.state.current_note_id is None:
            self.append_log("当前没有可置顶的笔记，忽略")
            return
        note_id = int(self.state.current_note_id)
        before = list(self._favorite_order)
        if note_id in self._favorite_order:
            self._favorite_order = [x for x in self._favorite_order if x != note_id]
            self.status.showMessage("已取消置顶/收藏", 2000)
            action = "取消"
        else:
            self._favorite_order.insert(0, note_id)
            self.status.showMessage("已置顶/收藏", 2000)
            action = "置顶"
        self.append_log(f"置顶/收藏操作: note_id={note_id}, action={action}, before={before}, after={self._favorite_order}")
        self._save_settings()
        self.refresh_notes()
        self._select_note_id(note_id)
        self._update_favorite_button_label()
        self.append_log(f"置顶/收藏完成: button_text={self.btn_favorite.text()!r}")

    def _on_notes_rows_moved(self, *_args) -> None:
        if self._notes_tree_mode:
            return
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

    def _rename_note_from_item(self, item) -> None:
        item_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if isinstance(item, QtWidgets.QTreeWidgetItem) else item.data(QtCore.Qt.ItemDataRole.UserRole)
        if item_id == ASK_AI_ITEM_ID or (isinstance(item_id, str) and (item_id.startswith("__folder__:") or item_id.startswith("__empty__"))):
            return
        note_id = int(item_id)
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
        assert self.title_edit is not None
        assert self.btn_ai_ask is not None
        assert self.btn_save is not None
        assert self.btn_delete is not None
        assert self.btn_favorite is not None
        assert self.btn_new is not None
        assert self.ai_tabs is not None
        self._ask_ai_mode = True
        self.state.current_note_id = None
        self.state.dirty = False

        # 切换到 AI 模式：显示 ai_tabs，隐藏普通编辑器
        self.append_log(f"进入 AI 模式，session_id={session_id!r}")
        self._set_right_panel_mode("ai")
        # 先确保至少有一个可见 AI 标签页，避免右侧布局没有任何内容变化
        if self.ai_tabs.count() == 0:
            self.append_log("AI 标签页当前为空，立即创建默认会话页")
            session = self._new_ai_session(select=True)
            self._current_ai_session_id = session.session_id
            self.append_log(f"默认会话页已创建: {session.session_id} / {session.title}")

        session = None
        if session_id and session_id in self._ai_sessions:
            session = self._ai_sessions[session_id]
            self.append_log(f"使用传入的 AI 会话: {session.session_id}")
        elif self._current_ai_session_id and self._current_ai_session_id in self._ai_sessions:
            session = self._ai_sessions[self._current_ai_session_id]
            self.append_log(f"使用当前 AI 会话: {session.session_id}")
        elif self.ai_tabs.count() > 0:
            tab_index = self.ai_tabs.currentIndex()
            if tab_index < 0:
                tab_index = 0
            widget = self.ai_tabs.widget(tab_index)
            tab_session_id = widget.property("session_id") if widget else None
            self.append_log(f"尝试从当前 AI 标签页获取会话: index={tab_index}, session_id={tab_session_id!r}")
            if tab_session_id and tab_session_id in self._ai_sessions:
                session = self._ai_sessions[tab_session_id]
        elif self._ai_sessions:
            session = self._sorted_ai_sessions()[0]
            self.append_log(f"从已有 AI 会话中选取: {session.session_id}")
        if session is None:
            self.append_log(f"未找到可复用 AI 会话，准备创建新会话；当前 sessions={len(self._ai_sessions)}, tabs={self.ai_tabs.count()}")
            session = self._new_ai_session(select=True)
            self.append_log(f"新建 AI 会话完成: {session.session_id} / {session.title}, tabs={self.ai_tabs.count()}")
        self._current_ai_session_id = session.session_id
        self.append_log(f"AI 当前会话: {session.session_id} / {session.title}")
        self.append_log(f"AI 标签页数量: {self.ai_tabs.count()}")
        self.append_log(f"AI tabs visible after select: {self.ai_tabs.isVisible()}")
        self.append_log(f"当前 AI 标签页索引: {self.ai_tabs.currentIndex()}")
        if self.ai_tabs.count() == 0:
            self.append_log("警告: AI 标签页数量为 0，尝试强制创建一个标签页")
            self._create_ai_tab(session)

        # 检查是否已有对应 session 的标签页
        tab_index = -1
        for i in range(self.ai_tabs.count()):
            widget = self.ai_tabs.widget(i)
            if widget and widget.property("session_id") == session.session_id:
                tab_index = i
                break

        if tab_index >= 0:
            # 切换到已有标签页
            self.append_log(f"定位到 AI 标签页索引: {tab_index}")
            self.ai_tabs.setCurrentIndex(tab_index)
            # 更新标签页内容
            self._update_ai_tab_content(session)
        else:
            self.append_log(f"未找到AI标签页，不自动创建: {session.title}")
            self._update_ai_tab_count()

        self.title_edit.blockSignals(True)
        self.title_edit.setText(session.title)
        self.title_edit.blockSignals(False)

        self._update_ai_ask_button_state()
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_favorite.setEnabled(False)
        self.btn_new.setEnabled(True)
        self._set_ai_controls_visible(True)
        self._update_title()
        self._update_realtime_token_stats()
        self.append_log("AI 模式切换完成")

    def _update_ai_tab_content(self, session: AiSession) -> None:
        """更新指定会话的标签页内容"""
        content_edit = self._get_ai_tab_content_edit(session.session_id)
        answer_edit = self._get_ai_tab_answer_edit(session.session_id)

        if content_edit:
            content_edit.blockSignals(True)
            draft = session.draft_prompt
            if _is_ai_prompt_placeholder_body(draft):
                draft = ""
                session.draft_prompt = ""
            _set_code_editor_document(content_edit, draft)
            content_edit.blockSignals(False)

        if answer_edit:
            answer_edit.blockSignals(True)
            _set_code_editor_document(answer_edit, self._render_ai_session_text(session))
            answer_edit.blockSignals(False)

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
        provider_name = self.provider_combo.currentText()
        if provider_name not in AI_PROVIDERS:
            provider_name = DEFAULT_PROVIDER
        api_url, api_key, _, default_model = AI_PROVIDERS[provider_name]
        model = self.model_combo.currentText().strip() or default_model
        if not prompt:
            self.status.showMessage("请输入问题", 2500)
            return
        if not api_key:
            self.status.showMessage(f"未配置 {provider_name} API Key", 5000)
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
            _set_code_editor_document(answer_edit, self._render_ai_session_text(session))
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
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
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
        self._update_ai_ask_button_state()

    def _update_ai_ask_button_state(self) -> None:
        if self.btn_ai_ask is None:
            return
        self.btn_ai_ask.setEnabled(True)
        self.btn_ai_ask.setText("问AI")

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
        label_input = self._get_right_widget("label_input_tokens", expected_type=QtWidgets.QLabel)
        label_output = self._get_right_widget("label_output_tokens", expected_type=QtWidgets.QLabel)
        label_cost = self._get_right_widget("label_cost", expected_type=QtWidgets.QLabel)
        if not (self._qt_is_valid(label_input) and self._qt_is_valid(label_output) and self._qt_is_valid(label_cost)):
            self.append_log("token label 已失效，跳过更新")
            return
        label_input.setText(f"输入: {self._ai_input_tokens} tokens")
        label_output.setText(f"输出: {self._ai_output_tokens} tokens")
        price = self._current_model_price()
        if price is None:
            label_cost.setText("费用: 价格未知")
            return
        input_cost = self._ai_input_tokens / 1_000_000 * price.input_per_m
        output_cost = self._ai_output_tokens / 1_000_000 * price.output_per_m
        total = input_cost + output_cost
        label_cost.setText(
            f"费用: {price.currency}{total:.6f} "
            f"(入 {price.currency}{input_cost:.6f} / 出 {price.currency}{output_cost:.6f})"
        )

    def _update_model_list_for_provider(self, provider_name: str) -> None:
        """根据提供商更新模型列表。"""
        if provider_name not in AI_PROVIDERS:
            provider_name = DEFAULT_PROVIDER
        _, _, models, default_model = AI_PROVIDERS[provider_name]
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(default_model)

    def _on_provider_changed(self, provider_name: str) -> None:
        """提供商切换时更新模型列表并保存设置。"""
        if provider_name not in AI_PROVIDERS:
            return
        self._settings.setValue("ai/provider", provider_name)
        self._settings.sync()
        self._update_model_list_for_provider(provider_name)
        self.append_log(f"AI提供商已切换：{provider_name}")

    def _on_ai_model_changed(self, model: str) -> None:
        model = model.strip()
        if not model:
            return
        self._selected_ai_model = model
        self._settings.setValue("ai/model", model)
        self._settings.sync()
        self.append_log(f"AI模型已选择：{model}")

    def _refresh_ai_models(self) -> None:
        provider_name = self.provider_combo.currentText()
        if provider_name != "SiliconFlow":
            self.status.showMessage(f"刷新模型列表仅支持 SiliconFlow，{provider_name} 请使用预设列表", 4000)
            return
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
        if not self._qt_is_valid(getattr(self, "label_price_source", None)):
            self.append_log(f"价格控件已失效，跳过更新：ok={ok}, payload={payload!r}")
            return
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
                    _set_code_editor_document(
                        answer_edit, self._render_ai_session_text(session)
                    )
        else:
            session.streaming_text = f"{prefix}:\n{message}"
            if self._current_ai_session_id == session_id:
                answer_edit = self._get_ai_tab_answer_edit(session_id)
                if answer_edit:
                    _set_code_editor_document(
                        answer_edit, self._render_ai_session_text(session)
                    )
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
                _set_code_editor_document(
                    answer_edit, self._render_ai_session_text(session)
                )
                cursor = answer_edit.textCursor()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                answer_edit.setTextCursor(cursor)
            self._update_token_labels()

    def _set_editor(self, note: NoteDto | None) -> None:
        self._ask_ai_mode = False
        self._current_ai_session_id = None
        self._current_external_file = None
        self._set_right_panel_mode("empty" if note is None else "note")
        if self._qt_is_valid(getattr(self, "ai_answer_edit", None)):
            self.ai_answer_edit.clear()
        if self._qt_is_valid(getattr(self, "btn_ai_ask", None)):
            self.btn_ai_ask.setEnabled(False)
        if self._qt_is_valid(getattr(self, "btn_save", None)):
            self.btn_save.setEnabled(True)
        if self._qt_is_valid(getattr(self, "btn_delete", None)):
            self.btn_delete.setEnabled(True)
        if self._qt_is_valid(getattr(self, "btn_favorite", None)):
            self.btn_favorite.setEnabled(True)
        self._refresh_core_widget_refs()
        self._set_ai_controls_visible(False)
        title_edit_widget = self._get_right_widget("title_edit", "title_edit")
        content_edit_widget = self._get_right_widget("content_edit", "content_edit")
        title_edit_valid = self._qt_is_valid(title_edit_widget)
        content_edit_valid = self._qt_is_valid(content_edit_widget)
        if title_edit_valid:
            title_edit_widget.blockSignals(True)
        if content_edit_valid:
            content_edit_widget.blockSignals(True)
        expected_content = ""
        try:
            if note is None:
                self.append_log("准备刷新右侧内容: 空编辑器")
                if title_edit_valid:
                    title_edit_widget.setText("")
                if content_edit_valid:
                    content_edit_widget.clear()
                self.state.current_note_id = None
            else:
                self.append_log(
                    "准备刷新右侧内容: "
                    f"note_id={note.id}, title={note.title!r}, api_chars={len(note.content or '')}"
                )
                if title_edit_valid:
                    title_edit_widget.setText(note.title)
                note_path = self._note_file_path(note.title)
                if content_edit_valid and note_path.is_file() and note_path.suffix.lower() == ".log":
                    mode = self._mode_from_filename(note.title)
                    loaded = content_edit_widget.load_text_file_cached(note_path, mode=mode)
                    expected_content = content_edit_widget.toPlainText()
                    self.append_log(
                        "日志文件缓存加载: "
                        f"path={str(note_path)!r}, loaded={loaded}, chars={len(expected_content)}"
                    )
                else:
                    expected_content = note.content or ""
                    if content_edit_valid:
                        content_edit_widget.setPlainText(expected_content)
                self.state.current_note_id = note.id
                # 根据文件扩展名自动切换高亮模式
                self._auto_set_highlight_mode(note.title)
        finally:
            if title_edit_valid:
                title_edit_widget.blockSignals(False)
            if content_edit_valid:
                content_edit_widget.blockSignals(False)
        self._set_right_panel_mode("empty" if note is None else "note")
        self._remember_right_widget("title_edit", title_edit_widget)
        self._remember_right_widget("content_edit", content_edit_widget)
        self._verify_right_note_content(note, expected_content)
        self.state.dirty = False
        self._update_title()
        self._update_token_labels()

    def _verify_right_note_content(self, note: NoteDto | None, expected_content: str) -> None:
        """验证右侧编辑器是否已经显示目标笔记内容，并输出诊断日志。"""
        if not self._qt_is_valid(getattr(self, "content_edit", None)):
            self.append_log("右侧内容验证跳过：content_edit 已失效")
            return
        actual_content = self.content_edit.toPlainText()
        previous_sig = getattr(self, "_last_right_content_signature", None)
        title_text = self.title_edit.text() if self._qt_is_valid(getattr(self, "title_edit", None)) else ""
        current_sig = (self.state.current_note_id, title_text, len(actual_content), actual_content[:80])
        changed = previous_sig != current_sig
        matches_expected = actual_content == (expected_content or "")
        note_id = None if note is None else note.id
        note_title = "" if note is None else note.title
        self._last_right_content_signature = current_sig
        parent_chain: list[str] = []
        parent = self.content_edit.parentWidget()
        while parent is not None and len(parent_chain) < 6:
            name = parent.objectName() or parent.__class__.__name__
            parent_chain.append(f"{name}:visible={parent.isVisible()},hidden={parent.isHidden()}")
            parent = parent.parentWidget()
        self.append_log(
            "右侧内容验证: "
            f"note_id={note_id}, title={note_title!r}, "
            f"expected_chars={len(expected_content or '')}, actual_chars={len(actual_content)}, "
            f"matches_expected={matches_expected}, changed={changed}, "
            f"visible={self.content_edit.isVisible()}, hidden={self.content_edit.isHidden()}, "
            f"parents={' > '.join(parent_chain)}, "
            f"preview={actual_content[:60]!r}"
        )

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
        self._save_settings()
        self.save_window_position()
        self.hide()
        event.ignore()

    def on_tray_message_log(self, message: str) -> None:
        self.append_log(message)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            if isinstance(event, QtGui.QWindowStateChangeEvent):
                old = event.oldState()
                if not (old & QtCore.Qt.WindowState.WindowMinimized) and (
                    self.windowState() & QtCore.Qt.WindowState.WindowMinimized
                ):
                    self._auto_save_note("窗口最小化", notify_tray=True)
        elif event.type() == QtCore.QEvent.Type.WindowDeactivate:
            QtCore.QTimer.singleShot(0, self._auto_save_on_deactivate)
        super().changeEvent(event)

    def _auto_save_on_deactivate(self) -> None:
        if self.windowState() & QtCore.Qt.WindowState.WindowMinimized:
            return
        self._auto_save_note("窗口失去焦点", detail_ui=True)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        # CodeEditorWidget 已代理所有常用方法，直接使用
        text_targets = {
            self.content_edit,
            self.ai_answer_edit,
            self.log_view,
            self.content_edit.viewport(),
            self.ai_answer_edit.viewport(),
            self.log_view.viewport(),
        }
        if self.help_view is not None:
            text_targets.update(
                {
                    self.help_view,
                    self.help_view.viewport(),
                }
            )
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
        # CodeEditorWidget 已代理 toHtml 方法
        return self.content_edit.toHtml()

    def _get_content_text(self) -> str:
        return self.content_edit.toPlainText()

    def _set_content_html(self, content: str) -> None:
        # CodeEditorWidget 已代理 setHtml 和 setPlainText 方法
        if self._looks_like_html(content):
            self.content_edit.setHtml(content)
            self._reload_local_images(self.content_edit)
        else:
            self.content_edit.setPlainText(content)

    def _set_content_text(self, content: str) -> None:
        self.content_edit.setPlainText(content or "")

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
        # CodeEditorWidget 已代理 setFont 和 document 方法
        editors: list[CodeEditorWidget] = [
            self.content_edit,
            self.ai_answer_edit,
            self.log_view,
        ]
        if self.help_view is not None:
            editors.append(self.help_view)
        if self.ai_tabs is not None:
            for i in range(self.ai_tabs.count()):
                tab = self.ai_tabs.widget(i)
                if tab is None:
                    continue
                for key in ("content_edit", "ai_answer_edit"):
                    w = tab.property(key)
                    if isinstance(w, CodeEditorWidget):
                        editors.append(w)
        for editor_widget in editors:
            editor_widget.setFont(font)
            editor_widget.document().setDefaultFont(font)

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "错误", message)

    def _update_title(self) -> None:
        suffix = " *" if self.state.dirty else ""
        if self._current_external_file:
            # 外部文件：显示完整路径层级
            p = Path(self._current_external_file)
            try:
                # 尽量显示相对 notepad_list 的相对路径
                rel = p.relative_to(self._notepad_list_dir()).as_posix()
            except Exception:
                rel = p.as_posix()
            cur = rel.replace("/", " › ")
        elif self.state.current_note_id:
            try:
                note = self.api.get_note(self.state.current_note_id)
                # note.title 为相对 notepad_list 的路径，例如 "日志A/maya_xxx.py"
                raw_title = note.title or f"#{note.id}"
                cur = raw_title.replace("/", " › ").replace("\\", " › ")
            except Exception:
                cur = f"#{self.state.current_note_id}"
        else:
            cur = "新建"
        self.setWindowTitle(f"L Notepad - {cur}{suffix}")

    @QtCore.Slot()
    def show_from_hotkey(self) -> None:
        self.append_log("收到快捷键触发，尝试显示到前台")
        self._bring_to_front()

    @QtCore.Slot()
    def show_folder_favorites_from_hotkey(self) -> None:
        """Ctrl+鼠标 全局快捷键：置前窗口并切换到「文件夹收藏」标签。"""
        self.append_log("收到文件夹收藏快捷键 (Ctrl+鼠标)，切换到收藏标签")
        self._bring_to_front()
        if self.tabs is None or self._folder_favorites_tab_index < 0:
            self.append_log("警告: 未找到文件夹收藏标签页")
            return
        self.tabs.setCurrentIndex(self._folder_favorites_tab_index)
        if self._folder_favorites_panel is not None:
            self._folder_favorites_panel.setFocus()

    def _on_folder_hotkey_button_changed(self, button: str) -> None:
        callback = getattr(self, "_folder_favorites_hotkey_callback", None)
        if callable(callback):
            callback(button)

    def _bring_to_front(self) -> None:
        is_hidden = self.isHidden()
        is_minimized = bool(self.windowState() & QtCore.Qt.WindowState.WindowMinimized)
        self.append_log(f"_bring_to_front: hidden={is_hidden}, minimized={is_minimized}, pos={self.pos().x()},{self.pos().y()}")
        if hasattr(self, "_saved_pos"):
            self.move(self._saved_pos)
            self.append_log(f"恢复到保存位置: {self._saved_pos.x()},{self._saved_pos.y()}")
        self.show()
        self.showNormal()
        self.setWindowState(
            (self.windowState() & ~QtCore.Qt.WindowState.WindowMinimized)
            | QtCore.Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()
        self.append_log(f"show 后: hidden={self.isHidden()}, visible={self.isVisible()}, pos={self.pos().x()},{self.pos().y()}")
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
        except Exception as e:
            self.append_log(f"Windows 前台显示逻辑失败: {e}")

    def _help_markdown_path(self) -> Path:
        return Path(__file__).resolve().parent / "help.md"

    def _load_help_page(self) -> None:
        """从 help.md 加载帮助页（Markdown 预览 + HTML 缓存）。"""
        if self.help_view is None:
            self.append_log("help_view 为 None，跳过加载")
            return
        path = self._help_markdown_path()
        fallback = "# L Notepad\n\n帮助文件未找到，请检查同目录下的 `help.md`。"
        if not path.exists():
            self.append_log(f"帮助文件不存在: {path}")
        else:
            self.append_log(f"加载帮助文件: {path}")
            # 读取文件内容用于诊断
            try:
                content = path.read_text(encoding="utf-8")
                self.append_log(f"帮助文件内容长度: {len(content)} 字符, {len(content.splitlines())} 行")
            except Exception as e:
                self.append_log(f"读取帮助文件失败: {e}")
        try:
            self.help_view.load_markdown_preview_file(path, fallback=fallback)
            self.append_log("帮助页面加载完成")
            # 检查 help_view 是否有内容
            if hasattr(self.help_view, 'toPlainText'):
                text = self.help_view.toPlainText()
                self.append_log(f"help_view 文本长度: {len(text)} 字符")
            if hasattr(self.help_view, 'toHtml'):
                html = self.help_view.toHtml()
                self.append_log(f"help_view HTML 长度: {len(html)} 字符")
        except Exception as e:
            self.append_log(f"帮助页面加载失败: {e}")
            import traceback
            self.append_log(traceback.format_exc())

    @QtCore.Slot(str, str)
    @QtCore.Slot(str)
    def append_log(self, message: str, level: str = "INFO") -> None:
        level = (level or "INFO").upper()
        if level == "DEBUG" and self._log_level != "DEBUG":
            return
        if level == "DEBUG":
            logger.debug(message)
        elif level in {"WARNING", "WARN"}:
            logger.warning(message)
        elif level == "ERROR":
            logger.error(message)
        else:
            logger.info(message)
        ts = datetime.now().strftime("%H:%M:%S")
        if self._qt_is_valid(getattr(self, "log_view", None)):
            self.log_view.append_text_update_cache(
                f"[{ts}] [{level}] {message}\n",
                LOG_VIEW_CONTENT_CACHE_KEY,
            )
            # 滚动到底部
            scrollbar = self.log_view.verticalScrollBar()
            if scrollbar:
                scrollbar.setValue(scrollbar.maximum())

    def append_debug_log(self, message: str) -> None:
        self.append_log(message, level="DEBUG")

    def _append_exception_log(self, title: str) -> None:
        detail = traceback.format_exc().rstrip()
        self.append_log(f"{title}:\n{detail}")

    def _log_unhandled_exception(self, exc_type, exc_value, exc_traceback) -> None:
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).rstrip()
        self.append_log(f"未捕获异常:\n{detail}")
        if getattr(self, "_previous_excepthook", None) is not None:
            self._previous_excepthook(exc_type, exc_value, exc_traceback)

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
                    if _is_ai_prompt_placeholder_body(draft_prompt):
                        draft_prompt = ""
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
        # 读取目录展开状态持久化字典
        expand_raw = self._settings.value("ui/folder_expanded", "{}")
        try:
            parsed = json.loads(str(expand_raw)) if expand_raw is not None else {}
            if isinstance(parsed, dict):
                # 确保值均为 bool
                self._tree_folder_expanded_state = {str(k): bool(v) for k, v in parsed.items()}
            else:
                self._tree_folder_expanded_state = {}
        except Exception:
            self._tree_folder_expanded_state = {}
        load_indent_display_options_from_settings(self._settings)

    @QtCore.Slot()
    def _apply_indent_display_settings(self) -> None:
        """从 QSettings 重新加载 Python 缩进显示选项（设置页修改后触发）。"""
        load_indent_display_options_from_settings(self._settings)

    def _restore_ai_tabs(self) -> None:
        """从 notepad_list 目录中的问AI文件恢复 AI 标签页"""
        notepad_list_dir = Path(__file__).resolve().parent / "notepad_list"
        ask_ai_files = sorted(notepad_list_dir.glob("问AI*.md")) if notepad_list_dir.exists() else []
        ask_ai_titles = {ask_ai_file.stem for ask_ai_file in ask_ai_files}

        self._ai_sessions = {
            sid: sess for sid, sess in self._ai_sessions.items()
            if sess.title in ask_ai_titles
        }
        if self._current_ai_session_id not in self._ai_sessions:
            self._current_ai_session_id = None

        for ask_ai_file in ask_ai_files:
            title = ask_ai_file.stem
            existing_session = next(
                (sess for sess in self._ai_sessions.values() if sess.title == title),
                None,
            )
            if existing_session is None:
                sid = str(QtCore.QDateTime.currentMSecsSinceEpoch()) + f"_{title}"
                existing_session = AiSession(session_id=sid, title=title, messages=[])
                self._ai_sessions[sid] = existing_session
            if self._current_ai_session_id is None:
                self._current_ai_session_id = existing_session.session_id

            self._create_ai_tab(existing_session)
            self._load_ai_session_content(existing_session)
        
        # 如果有当前会话，切换到对应标签页
        if self._current_ai_session_id:
            for i in range(self.ai_tabs.count()):
                widget = self.ai_tabs.widget(i)
                if widget and widget.property("session_id") == self._current_ai_session_id:
                    self.ai_tabs.setCurrentIndex(i)
                    break
        self._update_ai_tab_count()
    
    def _load_ai_session_content(self, session: AiSession) -> None:
        """从 notepad_list 目录加载问AI文件内容到会话"""
        if not self.ai_tabs:
            return
        notepad_list_dir = Path(__file__).resolve().parent / "notepad_list"
        file_path = notepad_list_dir / f"{session.title}.md"
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
                # 找到对应的标签页并设置内容
                for i in range(self.ai_tabs.count()):
                    widget = self.ai_tabs.widget(i)
                    if widget and widget.property("session_id") == session.session_id:
                        content_edit = widget.property("content_edit")
                        if content_edit:
                            content_edit.setPlainText(content)
                        break
            except Exception as exc:
                self.append_log(f"加载问AI文件失败 {session.title}: {exc}")

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
            # 保存目录展开状态
            try:
                self._settings.setValue(
                    "ui/folder_expanded",
                    json.dumps(getattr(self, "_tree_folder_expanded_state", {}) or {}),
                )
            except Exception:
                pass
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
        # 如果会话数量超过10个，删除最旧的会话
        if len(self._ai_sessions) >= 10:
            self.append_log("AI 会话超过上限，准备删除最旧会话")
            self._remove_oldest_ai_session()

        sid = str(QtCore.QDateTime.currentMSecsSinceEpoch())
        title = f"问AI {len(self._ai_sessions) + 1}"
        sess = AiSession(session_id=sid, title=title, messages=[])
        self._ai_sessions[sid] = sess
        if select:
            self._current_ai_session_id = sid
        self.append_log(f"创建 AI 会话: {sid} / {title} / select={select}")
        # 创建标签页
        self._create_ai_tab(sess)
        self._save_settings()
        return sess
    
    def _remove_oldest_ai_session(self) -> None:
        """删除最旧的AI会话"""
        if not self._ai_sessions:
            return
        # 获取第一个（最旧的）会话ID
        oldest_sid = next(iter(self._ai_sessions))
        # 删除对应的标签页
        for i in range(self.ai_tabs.count()):
            widget = self.ai_tabs.widget(i)
            if widget and widget.property("session_id") == oldest_sid:
                self.ai_tabs.removeTab(i)
                self._right_widget_refs.pop("ai_answer_edit", None)
                break
        # 删除会话
        del self._ai_sessions[oldest_sid]

    def _create_ai_tab(self, session: AiSession) -> None:
        """为 AI 会话创建一个标签页（从 .ui 模板克隆为 CodeEditorWidget）"""
        tab_widget = QtWidgets.QWidget()
        tab_layout = QtWidgets.QVBoxLayout(tab_widget)
        tab_layout.setSpacing(10)
        tab_layout.setContentsMargins(10, 10, 10, 10)

        template_content = getattr(self, "_ai_template_content_edit", None)
        template_answer = getattr(self, "_ai_template_answer_edit", None)

        content_edit = create_code_editor_widget(tab_widget, "CodeEditor", template_content)
        if template_content is None:
            content_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
            content_edit.setPlaceholderText(_AI_PROMPT_PLACEHOLDER)

        ai_answer_edit = create_code_editor_widget(tab_widget, "AiAnswerViewer", template_answer)
        if template_answer is None:
            ai_answer_edit.setReadOnly(True)
            ai_answer_edit.setPlaceholderText(_AI_ANSWER_PLACEHOLDER)

        tab_layout.addWidget(content_edit, 1)
        tab_layout.addWidget(ai_answer_edit, 2)

        font = QtGui.QFont("Cascadia Mono", self._text_font_size)
        for editor_widget in (content_edit, ai_answer_edit):
            editor_widget.setFont(font)
            editor_widget.document().setDefaultFont(font)

        tab_widget.setProperty("session_id", session.session_id)
        tab_widget.setProperty("content_edit", content_edit)
        tab_widget.setProperty("ai_answer_edit", ai_answer_edit)

        content_edit.editor().textChanged.connect(
            lambda: self._on_ai_tab_text_changed(session.session_id)
        )

        # 添加到标签页
        index = self.ai_tabs.addTab(tab_widget, session.title)
        self.ai_tabs.setCurrentIndex(index)

        # 为新标签设置关闭按钮
        self._setup_ai_tab_close_button(index)
        self._update_ai_tab_count()
        self._update_ai_tab_content(session)

    def _update_ai_tab_count(self) -> None:
        count = self.ai_tabs.count() if self._qt_is_valid(getattr(self, "ai_tabs", None)) else 0
        if self._qt_is_valid(getattr(self, "tab_count", None)):
            self.tab_count.setText(f"Tab: {count}")
        else:
            self.append_log(f"tab_count 已失效，跳过显示更新: {count}")
        self.append_log(f"AI标签页数量: {count}")

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

    def _get_ai_tab_content_edit(self, session_id: str | None = None) -> CodeEditorWidget | None:
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

    def _get_ai_tab_answer_edit(self, session_id: str | None = None) -> CodeEditorWidget | None:
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
        self._update_ai_tab_count()
        # 如果所有标签都关闭了，退出 AI 模式
        if self.ai_tabs.count() == 0:
            self._ask_ai_mode = False
            self._current_ai_session_id = None
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
        if index < 0 or not self._qt_is_valid(getattr(self, "ai_tabs", None)):
            return

        widget = self.ai_tabs.widget(index)
        if widget is None:
            return
        session_id = widget.property("session_id")
        if session_id and session_id in self._ai_sessions:
            self._current_ai_session_id = session_id
            session = self._ai_sessions[session_id]
            # 切换标签页时同步刷新当前页内容，避免右侧显示旧会话内容
            self._update_ai_tab_content(session)
            # 更新标题编辑框
            if self._qt_is_valid(getattr(self, "title_edit", None)):
                self.title_edit.blockSignals(True)
                self.title_edit.setText(session.title)
                self.title_edit.blockSignals(False)
            # 更新 token 统计
            self._update_realtime_token_stats()
            self._update_ai_ask_button_state()
        self._save_settings()

    def _render_ai_session_text(self, session: AiSession) -> str:
        chunks: list[str] = []
        if not session.messages and not session.streaming_text:
            return ""
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

