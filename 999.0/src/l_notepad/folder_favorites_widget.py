# -*- coding: utf-8 -*-
"""
文件夹收藏标签页 - 嵌入到 l_notepad 的桌面组件
"""

from __future__ import annotations

import json
import os
import subprocess
import ctypes
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from PySide6 import QtCore, QtGui, QtWidgets


class FavoriteItem(TypedDict, total=False):
    """收藏夹项目类型定义"""
    type: str  # "folder"、"command" 或 "url"
    name: str
    path: str  # 仅 folder 类型
    command: str  # 仅 command 类型
    url: str  # 仅 url 类型


class RenameItemDialog(QtWidgets.QDialog):
    """重命名收藏夹项目的对话框（支持名称和值联动）"""
    
    def __init__(
        self,
        parent: QtWidgets.QWidget,
        item_type: str,
        name: str,
        value: str,
    ) -> None:
        super().__init__(parent)
        self._item_type = item_type
        self._setup_ui(item_type, name, value)
    
    def _setup_ui(self, item_type: str, name: str, value: str) -> None:
        """初始化 UI"""
        title_map = {
            "folder": "修改文件夹",
            "command": "修改命令",
            "url": "修改网址"
        }
        label_map = {
            "folder": "文件夹路径:",
            "command": "执行命令:",
            "url": "网址链接:"
        }
        
        self.setWindowTitle(title_map.get(item_type, "修改项目"))
        self.setMinimumWidth(500)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # 显示名称输入框
        name_layout = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel("显示名称:")
        name_label.setFixedWidth(80)
        self.name_input = QtWidgets.QLineEdit(name)
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)
        
        # 真实值输入框（路径、命令或网址）
        value_layout = QtWidgets.QHBoxLayout()
        value_label = QtWidgets.QLabel(label_map.get(item_type, "值:"))
        value_label.setFixedWidth(80)
        self.value_input = QtWidgets.QLineEdit(value)
        value_layout.addWidget(value_label)
        value_layout.addWidget(self.value_input)
        layout.addLayout(value_layout)
        
        # 锁复选框（默认勾选，表示名称和值联动）
        self.lock_checkbox = QtWidgets.QCheckBox(" 名称和值保持一致")
        self.lock_checkbox.setChecked(True)
        layout.addWidget(self.lock_checkbox)
        
        # 设置联动逻辑
        self._setup_lock_logic()
        
        # 按钮
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def _setup_lock_logic(self) -> None:
        """设置锁定/解锁时的联动逻辑"""
        # 保存信号处理函数，以便后续断开
        self._sync_name_to_value = None
        self._sync_value_to_name = None
        
        def on_lock_changed(checked: bool):
            if checked:
                # 锁定时：双向同步
                def sync_name_to_value(text):
                    self.value_input.blockSignals(True)
                    self.value_input.setText(text)
                    self.value_input.blockSignals(False)
                
                def sync_value_to_name(text):
                    self.name_input.blockSignals(True)
                    self.name_input.setText(text)
                    self.name_input.blockSignals(False)
                
                # 保存引用以便后续使用
                self._sync_name_to_value = sync_name_to_value
                self._sync_value_to_name = sync_value_to_name
                
                self.name_input.textChanged.connect(self._sync_name_to_value)
                self.value_input.textChanged.connect(self._sync_value_to_name)
            else:
                # 解锁时：断开联动
                try:
                    if self._sync_name_to_value:
                        self.name_input.textChanged.disconnect(self._sync_name_to_value)
                    if self._sync_value_to_name:
                        self.value_input.textChanged.disconnect(self._sync_value_to_name)
                except Exception:
                    pass
        
        self.lock_checkbox.stateChanged.connect(
            lambda state: on_lock_changed(state == 2)
        )
        
        # 初始化时如果是勾选状态，立即连接信号
        if self.lock_checkbox.isChecked():
            on_lock_changed(True)
    
    def get_result(self) -> tuple[str, str]:
        """获取修改后的名称和值"""
        return self.name_input.text().strip(), self.value_input.text().strip()


# 剪贴板历史最多保存条数（超出丢弃最旧的）
CLIPBOARD_MAX_STORED = 2000


class ClipboardHistoryModel(QtCore.QAbstractListModel):
    """剪贴板历史数据模型（配合 QListView 虚拟化渲染）。

    QListView 只渲染可见行，因此无论历史多大都不会卡顿，
    去掉了原先的「分批加载更多」逻辑。去重用 set 维护，查找 O(1)。
    """

    TextRole = QtCore.Qt.UserRole

    def __init__(self, items: list[dict] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._items: list[dict] = list(items) if items else []
        self._text_set: set[str] = {it["text"] for it in self._items}

    # ---- Qt model 接口 ----
    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QtCore.QModelIndex, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        if role == QtCore.Qt.DisplayRole:
            return f"[{item['time']}] {item['text']}"
        if role == QtCore.Qt.ToolTipRole:
            return item["text"]
        if role == self.TextRole:
            return item["text"]
        return None

    # ---- 业务接口 ----
    def items(self) -> list[dict]:
        return self._items

    def contains(self, text: str) -> bool:
        return text in self._text_set

    def prepend(self, item: dict, max_stored: int = CLIPBOARD_MAX_STORED) -> None:
        """插入到最前；超出上限时裁剪最旧的若干条。"""
        self.beginInsertRows(QtCore.QModelIndex(), 0, 0)
        self._items.insert(0, item)
        self._text_set.add(item["text"])
        self.endInsertRows()
        if len(self._items) > max_stored:
            start, end = max_stored, len(self._items) - 1
            self.beginRemoveRows(QtCore.QModelIndex(), start, end)
            for dropped in self._items[max_stored:]:
                self._text_set.discard(dropped["text"])
            del self._items[max_stored:]
            self.endRemoveRows()

    def remove_text(self, text: str) -> bool:
        for i, it in enumerate(self._items):
            if it["text"] == text:
                self.beginRemoveRows(QtCore.QModelIndex(), i, i)
                del self._items[i]
                self._text_set.discard(text)
                self.endRemoveRows()
                return True
        return False

    def reset_items(self, items: list[dict]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._text_set = {it["text"] for it in self._items}
        self.endResetModel()

    def clear(self) -> None:
        self.reset_items([])

    def dedupe(self) -> int:
        """相同文本只保留最新一条（保持时间倒序）。返回移除条数。"""
        seen: set[str] = set()
        deduped: list[dict] = []
        for it in self._items:
            t = it.get("text", "")
            if t in seen:
                continue
            seen.add(t)
            deduped.append(it)
        removed = len(self._items) - len(deduped)
        if removed > 0:
            self.reset_items(deduped)
        return removed


class FolderFavoritesWidget(QtWidgets.QWidget):
    """文件夹收藏桌面组件（嵌入到 l_notepad）"""

    # Ctrl+中键识别到调用程序/地址栏路径后发出，用于更新自定义标题栏文本
    caller_info_changed = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None, restart_callback=None) -> None:
        super().__init__(parent)
        self._restart_callback = restart_callback  # 保存重启回调（兼容现有调用）
        self._last_clipboard_text = ""  # 上一次剪贴板内容
        self._explorer_hwnd = None  # 收藏夹导航的资源管理器窗口句柄
        self._caller_program = ""    # Ctrl+中键唤起时的调用程序名（如 explorer.exe）
        self._caller_path = ""       # 调用者地址栏当前文件夹路径（仅 Explorer 可读）
        self._filter_index = 0       # 显示筛选：0=全部 1=文件夹 2=网址 3=命令
        self._favorites_kind = "folder"  # 收藏种类：folder=文件夹收藏(全部) / url=网址收藏(独立文件)
        self._ui_initialized = False
        # 写盘防抖：剪贴板变化频繁时合并多次写入，避免阻塞 UI
        self._clipboard_save_timer = QtCore.QTimer(self)
        self._clipboard_save_timer.setSingleShot(True)
        self._clipboard_save_timer.setInterval(800)
        self._clipboard_save_timer.timeout.connect(self._save_clipboard_history)
        self._setup_data()
        QtCore.QTimer.singleShot(0, self.finalize_ui)

    def _apply_favorites_list_compact_style(self) -> None:
        """压缩收藏夹列表项间距，让路径列表更密集。"""
        self.list_widget.setSpacing(0)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setIconSize(QtCore.QSize(14, 14))
        self.list_widget.setStyleSheet(
            """
            QListWidget#folder_favorites_list {
                padding: 1px 2px;
                outline: none;
            }
            QListWidget#folder_favorites_list::item {
                min-height: 18px;
                padding: 1px 6px;
                margin: 0;
            }
            QListWidget#folder_favorites_list::item:selected {
                border-radius: 4px;
            }
            """
        )

    def finalize_ui(self) -> None:
        if self._ui_initialized:
            return
        self._ui_initialized = True
        self._setup_ui()
        self._refresh_list()
        self._refresh_clipboard_display()
        clipboard = QtWidgets.QApplication.clipboard()
        try:
            clipboard.dataChanged.disconnect(self._on_clipboard_changed)
        except Exception:
            pass
        clipboard.dataChanged.connect(self._on_clipboard_changed)

    def set_caller_hwnd(self, hwnd: int) -> None:
        """设置快捷键触发时的前台窗口句柄（由 ui.py 调用）。

        同时识别调用程序名，并在调用者是资源管理器时读取其地址栏当前路径。
        """
        self._explorer_hwnd = hwnd
        self._caller_program = self._get_process_name(hwnd)
        self._caller_path = self._get_explorer_path_for_hwnd(hwnd)
        print(
            f"收藏夹已记录调用者: hwnd={hwnd}, program={self._caller_program!r}, "
            f"path={self._caller_path!r}"
        )
        self._update_caller_info_label()

    def _get_process_name(self, hwnd: int) -> str:
        """根据窗口句柄取所属进程的可执行文件名（如 explorer.exe）。"""
        try:
            import ctypes
            from ctypes import wintypes

            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return ""
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
            )
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                ):
                    return os.path.basename(buf.value)
            finally:
                kernel32.CloseHandle(handle)
        except Exception as e:
            print(f"识别调用程序失败: {e}")
        return ""

    def _get_explorer_path_for_hwnd(self, hwnd: int) -> str:
        """若调用者是资源管理器窗口，读取其地址栏当前文件夹路径（Shell COM）。"""
        try:
            import pythoncom
            import win32com.client
        except Exception as e:
            print(f"读取地址栏路径所需组件不可用: {e}")
            return ""

        path = ""
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            for window in shell.Windows():
                try:
                    if int(window.HWND) != int(hwnd):
                        continue
                    folder = window.Document.Folder
                    path = str(folder.Self.Path)
                    break
                except Exception:
                    continue
        except Exception as e:
            print(f"读取 Explorer 地址栏路径失败: {e}")
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

        if path and os.path.isdir(path):
            return os.path.normpath(path)
        return ""

    def _update_caller_info_label(self) -> None:
        """发出识别到的调用者信息，由外层更新到自定义标题栏。"""
        program = self._caller_program or "未知程序"
        if self._caller_path:
            text = f" {program} — {self._caller_path}"
        else:
            text = f" {program}（未识别到地址栏路径）"
        self.caller_info_changed.emit(text)

    def show_actions_menu(self, global_pos) -> None:
        """在「文件夹收藏」标签右键时弹出操作菜单（原按钮/筛选已合并到此）。"""
        menu = QtWidgets.QMenu(self)
        menu.addAction(" 添加当前文件夹").triggered.connect(self._add_current_folder)
        menu.addAction(" 浏览添加文件夹").triggered.connect(self._browse_add_folder)
        menu.addAction(" 添加网址").triggered.connect(self._add_url)
        menu.addAction(" 添加命令").triggered.connect(self._add_command)
        menu.addSeparator()
        menu.addAction(" 执行").triggered.connect(self._execute_item)
        menu.addAction(" 删除").triggered.connect(self._remove_item)
        menu.addSeparator()

        # 显示筛选（radio 单选）
        filter_menu = menu.addMenu(" 显示筛选")
        group = QtGui.QActionGroup(filter_menu)
        group.setExclusive(True)
        current = getattr(self, "_filter_index", 0)
        for idx, label in enumerate(["全部", " 文件夹", " 网址", " 命令"]):
            act = filter_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(idx == current)
            group.addAction(act)
            act.triggered.connect(lambda _checked=False, i=idx: self._set_filter_index(i))

        menu.exec(global_pos)

    def _set_filter_index(self, index: int) -> None:
        self._filter_index = int(index)
        self._refresh_list()

    def _setup_data(self) -> None:
        """初始化数据路径"""
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        self.favorites_dir = Path(app_data) / "l_folder_favorites"
        self.favorites_dir.mkdir(parents=True, exist_ok=True)
        self.favorites_file = self.favorites_dir / self._favorites_filename()
        self.favorites: list[FavoriteItem] = self._load_favorites()
        # 剪贴板历史
        self._clipboard_file = self.favorites_dir / "clipboard_history.json"
        self.clipboard_model = ClipboardHistoryModel(self._load_clipboard_history())

    def _favorites_filename(self) -> str:
        """按收藏种类返回数据文件名（网址收藏独立存储）。"""
        return "url_favorites.json" if self._favorites_kind == "url" else "favorites.json"

    def set_favorites_kind(self, kind: str) -> None:
        """设置收藏种类（folder/url）。网址收藏使用独立数据文件并只显示网址。

        需在面板创建后、由外部（ui.py）调用；会重新指向数据文件并刷新。
        """
        if kind == self._favorites_kind:
            return
        self._favorites_kind = kind
        self.favorites_file = self.favorites_dir / self._favorites_filename()
        self.favorites = self._load_favorites()
        if kind == "url":
            self._filter_index = 2  # 仅显示网址
        if getattr(self, "_ui_initialized", False) and getattr(self, "list_widget", None) is not None:
            self._refresh_list()

    def _load_favorites(self) -> list[FavoriteItem]:
        """加载收藏夹数据"""
        if self.favorites_file.exists():
            try:
                with open(self.favorites_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载收藏夹失败: {e}")
        return []

    def _save_favorites(self) -> None:
        """保存收藏夹数据"""
        try:
            with open(self.favorites_file, "w", encoding="utf-8") as f:
                json.dump(self.favorites, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"保存收藏夹失败: {e}")

    def _load_clipboard_history(self) -> list[dict]:
        """加载剪贴板历史记录"""
        if self._clipboard_file.exists():
            try:
                with open(self._clipboard_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data[:CLIPBOARD_MAX_STORED]
            except Exception as e:
                print(f"加载剪贴板历史失败: {e}")
        return []

    def _save_clipboard_history(self) -> None:
        """保存剪贴板历史记录（由防抖 timer 触发，避免频繁同步写盘）"""
        try:
            with open(self._clipboard_file, "w", encoding="utf-8") as f:
                json.dump(
                    self.clipboard_model.items(), f, ensure_ascii=False, indent=2
                )
        except Exception as e:
            print(f"保存剪贴板历史失败: {e}")

    def _schedule_save_clipboard(self) -> None:
        """请求一次写盘（防抖：800ms 内的多次请求合并为一次）。"""
        self._clipboard_save_timer.start()

    def _setup_ui(self) -> None:
        """初始化UI。优先复用 main_window.ui 中定义的控件。"""
        if self.layout() is not None:
            # 兼容两种 .ui 变体：文件夹收藏(folder_favorites_list) 与 网址收藏(url_favorites_list)
            fav_list = (
                self.findChild(QtWidgets.QListWidget, "folder_favorites_list")
                or self.findChild(QtWidgets.QListWidget, "url_favorites_list")
            )
            cb_count = self.findChild(QtWidgets.QLabel, "clipboard_count_label")
            cb_btn = self.findChild(QtWidgets.QToolButton, "ClipboardActionsButton")
            cb_search = self.findChild(QtWidgets.QLineEdit, "clipboard_search")
            cb_list = self.findChild(QtWidgets.QListView, "clipboard_list")
            # 完整变体：收藏列表 + 剪贴板区域齐全
            if fav_list is not None and all([cb_count, cb_btn, cb_search, cb_list]):
                self.list_widget = fav_list
                self.clipboard_count_label = cb_count
                self.clipboard_actions_btn = cb_btn
                self._clipboard_search = cb_search
                self.clipboard_list = cb_list
                self._setup_existing_ui_widgets()
                return
            # 精简变体（网址收藏页）：只有收藏列表，无剪贴板控件
            if fav_list is not None:
                self.list_widget = fav_list
                self._setup_existing_favorites_list_only()
                return

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 标题
        title_label = QtWidgets.QLabel(" 文件夹收藏与命令")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 5px;")
        layout.addWidget(title_label)

        # 收藏夹列表
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setObjectName("folder_favorites_list")
        self._apply_favorites_list_compact_style()
        self.list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemDoubleClicked.connect(self._execute_item)
        # 启用拖拽排序
        self.list_widget.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.list_widget.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.list_widget.setDragEnabled(True)
        self.list_widget.setAcceptDrops(True)
        self.list_widget.viewport().setAcceptDrops(True)
        # 监听拖拽完成事件，保存新顺序
        self.list_widget.model().rowsMoved.connect(self._on_items_reordered)
        layout.addWidget(self.list_widget)

        # 操作按钮与显示筛选已合并到「文件夹收藏」标签的右键菜单，见 show_actions_menu()

        # 说明标签
        hint_label = QtWidgets.QLabel("提示: 右键「文件夹收藏」标签可添加/执行/删除并切换显示类型")
        hint_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint_label)

        # ===== 剪贴板历史区域 =====
        clipboard_group = QtWidgets.QWidget()
        clipboard_group.setObjectName("ClipboardGroup")
        clipboard_group.setStyleSheet(
            "#ClipboardGroup { background-color: #2b2b2b; border-radius: 5px; padding: 5px; }"
        )
        clipboard_main_layout = QtWidgets.QVBoxLayout(clipboard_group)

        # 标题行（标题 + 清除按钮）
        clipboard_title_row = QtWidgets.QHBoxLayout()
        clipboard_title = QtWidgets.QLabel(" 剪贴板历史")
        clipboard_title.setStyleSheet(
            "color: #cccccc; font-size: 12px; font-weight: bold; padding: 3px;"
        )
        clipboard_title_row.addWidget(clipboard_title)

        self.clipboard_count_label = QtWidgets.QLabel(
            f"{self.clipboard_model.rowCount()} 条"
        )
        self.clipboard_count_label.setObjectName("clipboard_count_label")
        self.clipboard_count_label.setStyleSheet(
            "color: #89DDFF; font-size: 12px; padding: 3px 6px;"
        )
        clipboard_title_row.addWidget(self.clipboard_count_label)
        clipboard_title_row.addStretch()

        # 三个操作（刷新/清理重复/清除全部）收进一个「箭头」下拉按钮。
        self.clipboard_actions_btn = QtWidgets.QToolButton()
        self.clipboard_actions_btn.setObjectName("ClipboardActionsButton")
        self.clipboard_actions_btn.setText("")
        self.clipboard_actions_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.clipboard_actions_btn.setFixedHeight(10)
        self.clipboard_actions_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.clipboard_actions_btn.setStyleSheet(
            """
            QToolButton#ClipboardActionsButton {
                padding: 0 6px;
                font-size: 10px;
                line-height: 10px;
                border: 1px solid rgba(137, 221, 255, 0.25);
                border-radius: 5px;
                background: rgba(255,255,255,0.04);
                color: #89DDFF;
                margin: 0 4px;
            }
            QToolButton#ClipboardActionsButton:hover {
                background: rgba(137, 221, 255, 0.15);
            }
            QToolButton#ClipboardActionsButton::menu-indicator {
                image: none;
                width: 0;
            }
            QMenu#ClipboardActionsMenu::item {
                height: 20px;
                padding: 0 16px;
                font-size: 12px;
            }
            """
        )

        clipboard_menu = QtWidgets.QMenu(self.clipboard_actions_btn)
        clipboard_menu.setObjectName("ClipboardActionsMenu")
        act_refresh = clipboard_menu.addAction(" 刷新")
        act_refresh.triggered.connect(self._refresh_clipboard_display)
        act_dedupe = clipboard_menu.addAction(" 清理重复")
        act_dedupe.triggered.connect(self._dedupe_clipboard_history)
        act_clear = clipboard_menu.addAction(" 清除全部")
        act_clear.triggered.connect(self._clear_clipboard_history)
        self.clipboard_actions_btn.setMenu(clipboard_menu)
        clipboard_title_row.addWidget(self.clipboard_actions_btn)
        clipboard_main_layout.addLayout(clipboard_title_row)

        # 搜索过滤
        self._clipboard_search = QtWidgets.QLineEdit()
        self._clipboard_search.setObjectName("clipboard_search")
        self._clipboard_search.setPlaceholderText(" 搜索剪贴板历史...")
        self._clipboard_search.setClearButtonEnabled(True)
        self._clipboard_search.setStyleSheet(
            "background-color: #1e1e1e; border: 1px solid #3c3c3c; color: #d4d4d4;"
            " border-radius: 4px; padding: 4px 6px;"
        )
        self._clipboard_search.textChanged.connect(self._on_clipboard_search_changed)
        clipboard_main_layout.addWidget(self._clipboard_search)

        # 剪贴板列表（QListView + Model 虚拟化：只渲染可见行，海量历史也不卡）
        self._clipboard_proxy = QtCore.QSortFilterProxyModel(self)
        self._clipboard_proxy.setSourceModel(self.clipboard_model)
        self._clipboard_proxy.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self._clipboard_proxy.setFilterRole(ClipboardHistoryModel.TextRole)

        self.clipboard_list = QtWidgets.QListView()
        self.clipboard_list.setObjectName("clipboard_list")
        self.clipboard_list.setModel(self._clipboard_proxy)
        self.clipboard_list.setUniformItemSizes(True)
        self.clipboard_list.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.clipboard_list.setSpacing(2)  # 降低 item 间隔
        self.clipboard_list.setStyleSheet(
            """
            QListView#clipboard_list {
                background-color: #1e1e1e;
                border: 1px solid #3c3c3c;
                color: #d4d4d4;
                padding: 2px;
            }
            QListView#clipboard_list::item {
                padding: 1px 1px;
                margin: 0;
                min-height: 16px;
            }
            QListView#clipboard_list::item:selected {
                background-color: #3a3a3a;
                border-radius: 3px;
            }
            QScrollBar:vertical {
                background: #1e1e1e;
                width: 12px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #5a5a5a;
                min-height: 24px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #6e6e6e;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            """
        )
        self.clipboard_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.clipboard_list.customContextMenuRequested.connect(
            self._show_clipboard_context_menu
        )
        self.clipboard_list.doubleClicked.connect(self._use_clipboard_item)
        
        # 强制设置紧凑的 item 高度
        class CompactItemDelegate(QtWidgets.QStyledItemDelegate):
            def sizeHint(self, option, index):
                size = super().sizeHint(option, index)
                size.setHeight(16)  # 固定高度为 16px
                return size
        
        self.clipboard_list.setItemDelegate(CompactItemDelegate())
        clipboard_main_layout.addWidget(self.clipboard_list)

        layout.addWidget(clipboard_group)

    def _wire_favorites_list(self) -> None:
        """收藏列表的通用初始化（样式、右键菜单、双击、拖拽排序）。"""
        self._apply_favorites_list_compact_style()
        self.list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemDoubleClicked.connect(self._execute_item)
        self.list_widget.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.list_widget.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.list_widget.setDragEnabled(True)
        self.list_widget.setAcceptDrops(True)
        self.list_widget.viewport().setAcceptDrops(True)
        self.list_widget.model().rowsMoved.connect(self._on_items_reordered)

    def _setup_existing_favorites_list_only(self) -> None:
        """精简变体（网址收藏页）：仅初始化收藏列表，无剪贴板区域。"""
        self._wire_favorites_list()

    def _setup_existing_ui_widgets(self) -> None:
        self._wire_favorites_list()

        self.clipboard_count_label.setText(f"{self.clipboard_model.rowCount()} 条")
        self.clipboard_actions_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.clipboard_actions_btn.setCursor(QtCore.Qt.PointingHandCursor)

        clipboard_menu = QtWidgets.QMenu(self.clipboard_actions_btn)
        clipboard_menu.setObjectName("ClipboardActionsMenu")
        act_refresh = clipboard_menu.addAction(" 刷新")
        act_refresh.triggered.connect(self._refresh_clipboard_display)
        act_dedupe = clipboard_menu.addAction(" 清理重复")
        act_dedupe.triggered.connect(self._dedupe_clipboard_history)
        act_clear = clipboard_menu.addAction(" 清除全部")
        act_clear.triggered.connect(self._clear_clipboard_history)
        self.clipboard_actions_btn.setMenu(clipboard_menu)

        self._clipboard_search.textChanged.connect(self._on_clipboard_search_changed)
        self._clipboard_proxy = QtCore.QSortFilterProxyModel(self)
        self._clipboard_proxy.setSourceModel(self.clipboard_model)
        self._clipboard_proxy.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self._clipboard_proxy.setFilterRole(ClipboardHistoryModel.TextRole)
        self.clipboard_list.setModel(self._clipboard_proxy)
        self.clipboard_list.setUniformItemSizes(True)
        self.clipboard_list.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.clipboard_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.clipboard_list.customContextMenuRequested.connect(
            self._show_clipboard_context_menu
        )
        self.clipboard_list.doubleClicked.connect(self._use_clipboard_item)
        
        # 强制设置紧凑的 item 高度
        class CompactItemDelegate(QtWidgets.QStyledItemDelegate):
            def sizeHint(self, option, index):
                size = super().sizeHint(option, index)
                size.setHeight(16)  # 固定高度为 16px
                return size
        
        self.clipboard_list.setItemDelegate(CompactItemDelegate())

    def _refresh_list(self) -> None:
        """刷新列表"""
        self.list_widget.clear()
        filter_type = getattr(self, "_filter_index", 0)
    
        # 创建图标缓存
        folder_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon)
        command_icon = self.style().standardIcon(QtWidgets.QStyle.SP_CommandLink)
    
        for fav in self.favorites:
            item_type = fav.get("type", "folder")
    
            # 筛选
            if filter_type == 1 and item_type != "folder":
                continue
            if filter_type == 2 and item_type != "url":
                continue
            if filter_type == 3 and item_type != "command":
                continue
    
            # 创建列表项
            if item_type == "folder":
                display_text = f" {fav.get('path', '')}"
                icon = folder_icon
            elif item_type == "url":
                url = fav.get("url", "")
                name = fav.get("name", "")
                display_text = f" {name}  —  {url}" if url else f" {name}"
                icon = QtGui.QIcon()  # 网址暂无图标
            else:
                display_text = f" {fav.get('name', '')}"
                icon = command_icon
    
            item = QtWidgets.QListWidgetItem(icon, display_text)
            item.setSizeHint(QtCore.QSize(0, 20))
            item.setData(QtCore.Qt.UserRole, fav)
            self.list_widget.addItem(item)
    
    def _on_items_reordered(self) -> None:
        """拖拽排序完成后，更新 self.favorites 的顺序并保存"""
        # 从 UI 列表重建 favorites 顺序
        new_favorites = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            fav = item.data(QtCore.Qt.UserRole)
            if fav:
                new_favorites.append(fav)
        
        # 更新数据并保存
        self.favorites = new_favorites
        self._save_favorites()
        print(f" 已保存收藏顺序（{len(new_favorites)} 项）")

    def _add_current_folder(self) -> None:
        """添加当前文件夹：优先使用 Ctrl+中键唤起时识别到的调用者地址栏路径，
        识别不到再回退到手动选择对话框。"""
        if self._caller_path and os.path.isdir(self._caller_path):
            self._add_folder_by_path(self._caller_path)
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self._add_folder_by_path(folder)

    def _browse_add_folder(self) -> None:
        """浏览并添加文件夹"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self._add_folder_by_path(folder)

    def _add_folder_by_path(self, path: str) -> None:
        """通过路径添加文件夹"""
        if not path:
            QtWidgets.QMessageBox.warning(self, "警告", "未能识别有效的文件夹路径")
            return
        if not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, "警告", f"路径不存在: {path}")
            return

        path = path.strip()
        if len(path) >= 2 and path[1] == ":":
            drive = path[0].upper()
            suffix = path[2:].replace("/", "\\").strip("\\")
            if not suffix:
                path = f"{drive}:\\"
                folder_name = f"{drive}:"
            else:
                path = os.path.normpath(path)
                folder_name = os.path.basename(path)
        else:
            path = os.path.abspath(path)
            folder_name = os.path.basename(path)

        # 检查是否已存在
        for fav in self.favorites:
            if fav.get("path") == path:
                QtWidgets.QMessageBox.information(self, "提示", "该文件夹已在收藏中")
                return

        self.favorites.append({"type": "folder", "name": folder_name, "path": path})
        self._save_favorites()
        self._refresh_list()

    def _add_url(self) -> None:
        """添加网址"""
        # 创建自定义对话框，包含名称和网址两个输入框
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("添加网址")
        dialog.setModal(True)
        dialog.setMinimumWidth(450)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # 名称输入
        name_label = QtWidgets.QLabel("网址名称:")
        name_input = QtWidgets.QLineEdit()
        name_input.setPlaceholderText("例如：GitHub、百度、公司内部系统")
        layout.addWidget(name_label)
        layout.addWidget(name_input)
        
        # 网址链接输入
        url_label = QtWidgets.QLabel("网址链接:")
        url_input = QtWidgets.QLineEdit()
        url_input.setPlaceholderText("例如：https://github.com")
        layout.addWidget(url_label)
        layout.addWidget(url_input)
        
        # 按钮
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        # 显示对话框
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            name = name_input.text().strip()
            url = url_input.text().strip()
            
            if not name or not url:
                return
            
            # 添加到收藏夹
            self.favorites.append({
                "type": "url",
                "name": name,
                "url": url
            })
            self._save_favorites()
            self._refresh_list()

    def _remove_item(self) -> None:
        """删除选中的项目"""
        current_row = self.list_widget.currentRow()
        if current_row >= 0 and current_row < len(self.favorites):
            del self.favorites[current_row]
            self._save_favorites()
            self._refresh_list()

    def _execute_item(self) -> None:
        """执行选中的项目"""
        current_row = self.list_widget.currentRow()
        if current_row >= 0 and current_row < len(self.favorites):
            fav: FavoriteItem = self.favorites[current_row]
            item_type = fav.get("type", "folder")

            if item_type == "folder":
                path = fav.get("path", "")
                if os.path.exists(path):
                    self._navigate_to_folder(path)
                else:
                    QtWidgets.QMessageBox.warning(self, "警告", f"路径不存在: {path}")
            elif item_type == "url":
                url = fav.get("url", "")
                if url:
                    try:
                        webbrowser.open(url)
                        print(f"打开网址: {url}")
                    except Exception as e:
                        QtWidgets.QMessageBox.warning(self, "错误", f"打开网址失败: {e}")
            else:
                command = fav.get("command", "")
                if command:
                    try:
                        subprocess.Popen(command, shell=True)
                        print(f"执行命令: {command}")
                    except Exception as e:
                        QtWidgets.QMessageBox.warning(self, "错误", f"执行命令失败: {e}")

    def _navigate_to_folder(self, folder_path: str) -> None:
        """导航到指定文件夹：优先 COM 直接驱动 Explorer，失败再回退按键模拟。"""
        # 1. 优先用 Shell COM 直接导航（不需抢占前台，最稳定）
        if self._navigate_explorer_com(folder_path):
            print(f" COM 已导航到 Explorer: {folder_path}")
            return

        user32 = ctypes.windll.user32

        # 优先使用快捷键触发时记录的前台窗口句柄
        if self._explorer_hwnd and user32.IsWindow(self._explorer_hwnd):
            hwnd = self._explorer_hwnd
            if self._navigate_explorer_hwnd(hwnd, folder_path):
                print(f" 已导航到缓存窗口（句柄 {hwnd}）: {folder_path}")
                return
            print(f" 缓存窗口导航失败，尝试其他 Explorer")

        # 尝试查找任意一个 Explorer 窗口
        found_hwnd = self._find_any_explorer_hwnd()
        if found_hwnd:
            if self._navigate_explorer_hwnd(found_hwnd, folder_path):
                self._explorer_hwnd = found_hwnd
                print(f" 已导航到 Explorer 窗口（句柄 {found_hwnd}）: {folder_path}")
                return

        # 没有 Explorer 窗口，打开新窗口
        print(f" 没有 Explorer 窗口，打开新窗口: {folder_path}")
        os.startfile(folder_path)

    def _navigate_explorer_com(self, folder_path: str) -> bool:
        """通过 Shell.Application COM 让已存在的 Explorer 窗口导航到目标路径。
        优先复用缓存的调用者窗口句柄，否则使用第一个文件资源管理器窗口。"""
        try:
            import pythoncom
            import win32com.client
        except Exception as e:
            print(f"COM 导航组件不可用: {e}")
            return False

        target_path = os.path.normpath(folder_path)
        if not os.path.isdir(target_path):
            return False

        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            cached = int(self._explorer_hwnd) if self._explorer_hwnd else 0
            target = None
            fallback = None
            for window in shell.Windows():
                try:
                    whwnd = int(window.HWND)
                    _ = window.Document.Folder  # 仅文件资源管理器窗口可访问
                except Exception:
                    continue
                if cached and whwnd == cached:
                    target = window
                    break
                if fallback is None:
                    fallback = window
            target = target or fallback
            if target is None:
                return False
            try:
                target.Navigate2(target_path)
            except Exception:
                target.Navigate(target_path)
            try:
                ctypes.windll.user32.SetForegroundWindow(int(target.HWND))
            except Exception:
                pass
            self._explorer_hwnd = int(target.HWND)
            return True
        except Exception as e:
            print(f"COM 导航失败: {e}")
            return False
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    @classmethod
    def _find_any_explorer_hwnd(cls) -> int | None:
        """通过 FindWindowW 查找 CabinetWClass 窗口"""
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW("CabinetWClass", None)
        return hwnd if hwnd else None

    def _navigate_explorer_hwnd(self, hwnd: int, folder_path: str) -> bool:
        """通过模拟按键在 Explorer 窗口地址栏中导航"""
        user32 = ctypes.windll.user32
        VK_F4 = 0x73
        VK_RETURN = 0x0D
        VK_ESCAPE = 0x1B

        try:
            # 1. 让 Explorer 窗口置前
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.1)

            # 2. 发送 F4 激活地址栏（Explorer 标准快捷键）
            user32.keybd_event(VK_F4, 0, 0, 0)
            user32.keybd_event(VK_F4, 0, 2, 0)  # KEYEVENTF_KEYUP = 2
            time.sleep(0.15)

            # 3. 找到地址栏的 Edit 控件（F4 激活后地址栏变为可编辑的 Edit）
            edit_hwnd = self._find_address_bar_edit(hwnd)
            if edit_hwnd:
                # 直接设置文本 + 回车
                user32.SetWindowTextW(edit_hwnd, folder_path)
                user32.keybd_event(VK_RETURN, 0, 0, 0)
                user32.keybd_event(VK_RETURN, 0, 2, 0)
                print(f"  通过 Edit 控件导航: edit_hwnd={edit_hwnd}")
                return True

            # 4. 找不到 Edit 控件（Windows 11 XAML 地址栏），回退到剪贴板 + 粘贴方式
            print(f"  未找到 Edit 控件，回退到剪贴板粘贴方式")
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setText(folder_path)
            time.sleep(0.05)
            # Ctrl+A 全选地址栏现有内容（防止路径被追加到末尾）
            user32.keybd_event(0x11, 0, 0, 0)  # VK_CONTROL down
            user32.keybd_event(0x41, 0, 0, 0)  # VK_A down
            user32.keybd_event(0x41, 0, 2, 0)  # VK_A up
            user32.keybd_event(0x11, 0, 2, 0)  # VK_CONTROL up
            time.sleep(0.05)
            # Ctrl+V 粘贴（替换已全选的内容）
            user32.keybd_event(0x11, 0, 0, 0)  # VK_CONTROL down
            user32.keybd_event(0x56, 0, 0, 0)  # VK_V down
            user32.keybd_event(0x56, 0, 2, 0)  # VK_V up
            user32.keybd_event(0x11, 0, 2, 0)  # VK_CONTROL up
            time.sleep(0.05)
            # 回车确认导航
            user32.keybd_event(VK_RETURN, 0, 0, 0)
            user32.keybd_event(VK_RETURN, 0, 2, 0)
            return True

        except Exception as e:
            print(f" 导航失败: {e}")
            return False

    # ===== Explorer 窗口控件分析 =====

    @staticmethod
    def dump_explorer_tree() -> str:
        """遍历所有 Explorer 窗口的完整控件树，返回可读的层级文本"""
        user32 = ctypes.windll.user32
        find_ex = user32.FindWindowExW
        find_ex.restype = ctypes.c_void_p

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        results: list[str] = []

        def _enum_cb(hwnd, _lparam):
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            if cls_buf.value == "CabinetWClass":
                title_buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title_buf, 512)
                results.append(f"\n=== Explorer  hwnd={hwnd}  title={title_buf.value!r} ===")
                FolderFavoritesWidget._dump_children(hwnd, find_ex, user32, results, depth=1)
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        return "\n".join(results) if results else "未找到任何 Explorer 窗口"

    @staticmethod
    def _dump_children(parent_hwnd: int, find_ex, user32, results: list[str], depth: int, max_depth: int = 15) -> None:
        """递归遍历子窗口并 append 到 results"""
        if depth > max_depth:
            return
        indent = "  " * depth
        child = find_ex(parent_hwnd, 0, None, None)
        while child:
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls_buf, 256)
            cls_name = cls_buf.value

            title_buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(child, title_buf, 256)
            title = title_buf.value

            # 获取窗口尺寸
            r = (ctypes.c_long * 4)()
            user32.GetWindowRect(child, ctypes.byref(r))
            w = r[2] - r[0]
            h = r[3] - r[1]

            info = f"{indent}[{cls_name}]  hwnd={child}  size={w}x{h}"
            if title:
                info += f"  text={title!r}"
            results.append(info)

            FolderFavoritesWidget._dump_children(child, find_ex, user32, results, depth + 1, max_depth)
            child = find_ex(parent_hwnd, child, None, None)

    @staticmethod
    def _find_address_bar_edit(explorer_hwnd: int) -> int | None:
        """查找 Explorer 地址栏的 Edit 控件（通过 FindWindowExW 逐层遍历）"""
        user32 = ctypes.windll.user32
        find = user32.FindWindowExW
        find.restype = ctypes.c_void_p

        # Explorer 控件层级:
        # CabinetWClass
        #   └─ WorkerW
        #       └─ ReBarWindow32
        #           └─ Address Band Root
        #               └─ ComboBoxEx32
        #                   └─ ComboBox
        #                       └─ Edit
        worker = find(explorer_hwnd, 0, "WorkerW", None)
        if not worker:
            return None
        rebar = find(worker, 0, "ReBarWindow32", None)
        if not rebar:
            return None
        # 遍历 ReBarWindow32 的子窗口查找 "Address Band Root"
        child = find(rebar, 0, None, None)
        addr_band = 0
        while child:
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls_buf, 256)
            if cls_buf.value == "Address Band Root":
                addr_band = child
                break
            child = find(rebar, child, None, None)
        if not addr_band:
            return None
        combo_ex = find(addr_band, 0, "ComboBoxEx32", None)
        if not combo_ex:
            return None
        combo = find(combo_ex, 0, "ComboBox", None)
        if not combo:
            return None
        edit = find(combo, 0, "Edit", None)
        return edit if edit else None

    def _show_context_menu(self, position: QtCore.QPoint) -> None:
        """显示右键菜单"""
        item = self.list_widget.itemAt(position)
        if item:
            menu = QtWidgets.QMenu()

            current_row = self.list_widget.currentRow()
            if current_row >= 0 and current_row < len(self.favorites):
                fav = self.favorites[current_row]
                item_type = fav.get("type", "folder")

                if item_type == "folder":
                    execute_action = QtGui.QAction(" 打开文件夹", self)
                    navigate_action = QtGui.QAction(" 跳转到此文件夹", self)
                    navigate_action.triggered.connect(
                        lambda: self._navigate_to_folder(fav["path"])
                    )
                    copy_path_action = QtGui.QAction(" 复制路径", self)
                    copy_path_action.triggered.connect(
                        lambda: self._copy_path_to_clipboard(fav["path"])
                    )
                    rename_action = QtGui.QAction(" 修改文件夹", self)
                    rename_action.triggered.connect(
                        lambda: self._rename_item(current_row)
                    )
                    copy_name_action = QtGui.QAction(" 复制文件夹名称", self)
                    copy_name_action.triggered.connect(
                        lambda: self._copy_name_to_clipboard(fav["name"])
                    )
                elif item_type == "url":
                    execute_action = QtGui.QAction(" 打开网址", self)
                    navigate_action = None
                    copy_path_action = QtGui.QAction(" 复制网址", self)
                    copy_path_action.triggered.connect(
                        lambda: self._copy_path_to_clipboard(fav["url"])
                    )
                    rename_action = QtGui.QAction(" 修改网址", self)
                    rename_action.triggered.connect(
                        lambda: self._rename_item(current_row)
                    )
                    copy_name_action = QtGui.QAction(" 复制网址名称", self)
                    copy_name_action.triggered.connect(
                        lambda: self._copy_name_to_clipboard(fav["name"])
                    )
                else:
                    execute_action = QtGui.QAction(" 执行命令", self)
                    navigate_action = None
                    copy_path_action = None
                    rename_action = QtGui.QAction(" 修改命令", self)
                    rename_action.triggered.connect(
                        lambda: self._rename_item(current_row)
                    )
                    copy_name_action = QtGui.QAction(" 复制命令名称", self)
                    copy_name_action.triggered.connect(
                        lambda: self._copy_name_to_clipboard(fav["name"])
                    )

                execute_action.triggered.connect(self._execute_item)
                menu.addAction(execute_action)
                if navigate_action:
                    menu.addAction(navigate_action)
                if copy_path_action:
                    menu.addAction(copy_path_action)
                menu.addAction(rename_action)
                menu.addAction(copy_name_action)

            remove_action = QtGui.QAction(" 删除", self)
            remove_action.triggered.connect(self._remove_item)
            menu.addAction(remove_action)

            menu.exec(self.list_widget.mapToGlobal(position))
        # 空白处右键不再显示菜单，所有添加操作都在标签页右键菜单中

    def _copy_path_to_clipboard(self, path: str) -> None:
        """将路径复制到剪贴板"""
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(path)
        print(f"已复制路径到剪贴板: {path}")

    def _copy_name_to_clipboard(self, name: str) -> None:
        """将名称复制到剪贴板"""
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(name)
        print(f"已复制名称到剪贴板: {name}")

    def _rename_item(self, row: int) -> None:
        """重命名选中的项目"""
        if row < 0 or row >= len(self.favorites):
            return
        fav: FavoriteItem = self.favorites[row]
        item_type = fav.get("type", "folder")
        
        # 准备对话框参数
        name = fav.get("name", "")
        value = fav.get("path" if item_type == "folder" else ("url" if item_type == "url" else "command"), "")
        
        # 创建并显示对话框
        dialog = RenameItemDialog(self, item_type, name, value)
        
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            new_name, new_value = dialog.get_result()
            
            if new_name:
                fav["name"] = new_name
            if new_value:
                if item_type == "folder":
                    fav["path"] = new_value
                elif item_type == "url":
                    fav["url"] = new_value
                else:
                    fav["command"] = new_value
        
        self._save_favorites()
        self._refresh_list()

    def _add_command(self) -> None:
        """添加新命令"""
        # 创建自定义对话框，包含名称和命令两个输入框
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("添加命令")
        dialog.setModal(True)
        dialog.setMinimumWidth(400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # 名称输入
        name_label = QtWidgets.QLabel("命令名称:")
        name_input = QtWidgets.QLineEdit()
        name_input.setPlaceholderText("请输入命令名称")
        layout.addWidget(name_label)
        layout.addWidget(name_input)
        
        # 命令内容输入
        command_label = QtWidgets.QLabel("命令内容:")
        command_input = QtWidgets.QLineEdit()
        command_input.setPlaceholderText("请输入要执行的命令")
        layout.addWidget(command_label)
        layout.addWidget(command_input)
        
        # 按钮
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        # 显示对话框
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            name = name_input.text().strip()
            command = command_input.text().strip()
            
            if not name or not command:
                return
            
            # 添加到收藏夹
            self.favorites.append({
                "type": "command",
                "name": name,
                "command": command
            })
            self._save_favorites()
            self._refresh_list()

    # ===== 剪贴板历史相关方法 =====

    def _refresh_clipboard_display(self) -> None:
        """初始化时刷新剪贴板显示（QListView 由 model 驱动，无需手动建项）"""
        self._update_clipboard_count_label()
        # 初始化当前剪贴板内容
        clipboard = QtWidgets.QApplication.clipboard()
        self._last_clipboard_text = clipboard.text().strip()

    def _on_clipboard_changed(self) -> None:
        """剪贴板内容变化时的回调（Qt 信号驱动）"""
        if not hasattr(self, "clipboard_model"):
            return

        clipboard = QtWidgets.QApplication.clipboard()
        current_text = clipboard.text().strip()

        # 新内容且与上次不同、且不在历史中 → 插入到最前
        if (
            current_text
            and current_text != self._last_clipboard_text
            and not self.clipboard_model.contains(current_text)
        ):
            self.clipboard_model.prepend(
                {
                    "text": current_text,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
                CLIPBOARD_MAX_STORED,
            )
            self._schedule_save_clipboard()
            self._update_clipboard_count_label()

        self._last_clipboard_text = current_text

    def _on_clipboard_search_changed(self, text: str) -> None:
        """搜索框内容变化：交给代理模型过滤（虚拟化渲染，无需手动建项）。"""
        self._clipboard_proxy.setFilterFixedString(text.strip())
        self._update_clipboard_count_label()

    def _update_clipboard_count_label(self) -> None:
        """更新记录数量显示。"""
        if not hasattr(self, "clipboard_count_label"):
            return
        total = self.clipboard_model.rowCount()
        matched = self._clipboard_proxy.rowCount()
        if matched != total:
            self.clipboard_count_label.setText(f"匹配 {matched}/{total} 条")
        else:
            self.clipboard_count_label.setText(f"{total} 条")

    def _dedupe_clipboard_history(self) -> None:
        """清理重复：相同文本只保留最新一条（保持时间倒序）。"""
        removed = self.clipboard_model.dedupe()
        if removed <= 0:
            QtWidgets.QMessageBox.information(self, "清理重复", "没有发现重复记录。")
            return
        self._schedule_save_clipboard()
        self._update_clipboard_count_label()
        print(f" 已清理 {removed} 条重复剪贴板记录")

    def _clipboard_text_at(self, point: QtCore.QPoint) -> str:
        """根据视图坐标取对应行的原始文本。"""
        index = self.clipboard_list.indexAt(point)
        if not index.isValid():
            return ""
        return index.data(ClipboardHistoryModel.TextRole) or ""

    def _show_clipboard_context_menu(self, position: QtCore.QPoint) -> None:
        """显示剪贴板历史右键菜单"""
        text = self._clipboard_text_at(position)
        if not text:
            return
        menu = QtWidgets.QMenu()

        copy_action = QtGui.QAction(" 复制文本", self)
        copy_action.triggered.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(text)
        )
        menu.addAction(copy_action)

        delete_action = QtGui.QAction(" 删除此条", self)
        delete_action.triggered.connect(lambda: self._delete_clipboard_item(text))
        menu.addAction(delete_action)

        menu.addSeparator()

        clear_action = QtGui.QAction(" 清除全部历史", self)
        clear_action.triggered.connect(self._clear_clipboard_history)
        menu.addAction(clear_action)

        menu.exec(self.clipboard_list.viewport().mapToGlobal(position))

    def _use_clipboard_item(self, index: QtCore.QModelIndex) -> None:
        """双击使用剪贴板项（写回剪贴板）"""
        text = index.data(ClipboardHistoryModel.TextRole) if index.isValid() else ""
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            display = text[:50] + "..." if len(text) > 50 else text
            print(f"已使用剪贴板项: {display}")

    def _delete_clipboard_item(self, text: str) -> None:
        """删除单条剪贴板记录"""
        if self.clipboard_model.remove_text(text):
            self._schedule_save_clipboard()
            self._update_clipboard_count_label()

    def _clear_clipboard_history(self) -> None:
        """清除剪贴板历史"""
        if self.clipboard_model.rowCount() == 0:
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认清除",
            f"确定要清除全部 {self.clipboard_model.rowCount()} 条剪贴板历史记录吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self.clipboard_model.clear()
            self._schedule_save_clipboard()
            self._update_clipboard_count_label()
            print(" 剪贴板历史已清除")

    def _flush_clipboard_save(self) -> None:
        """若有挂起的防抖写盘请求，立即落盘（用于隐藏/关闭前）。"""
        if self._clipboard_save_timer.isActive():
            self._clipboard_save_timer.stop()
            self._save_clipboard_history()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._flush_clipboard_save()
        super().hideEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._flush_clipboard_save()
        super().closeEvent(event)
