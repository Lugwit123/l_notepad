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
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets


class FolderFavoritesWidget(QtWidgets.QWidget):
    """文件夹收藏桌面组件（嵌入到 l_notepad）"""

    def __init__(self, parent: QtWidgets.QWidget | None = None, restart_callback=None) -> None:
        super().__init__(parent)
        self._restart_callback = restart_callback  # 保存重启回调（兼容现有调用）
        self._last_clipboard_text = ""  # 上一次剪贴板内容
        self._explorer_hwnd = None  # 收藏夹导航的资源管理器窗口句柄
        self._setup_data()
        self._setup_ui()
        self._refresh_list()
        self._refresh_clipboard_display()
        # 使用 Qt 剪贴板信号替代定时器，事件驱动更高效
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.dataChanged.connect(self._on_clipboard_changed)

    def set_caller_hwnd(self, hwnd: int) -> None:
        """设置快捷键触发时的前台窗口句柄（由 ui.py 调用）"""
        self._explorer_hwnd = hwnd
        print(f"收藏夹已记录调用者窗口句柄: {hwnd}")

    def _setup_data(self) -> None:
        """初始化数据路径"""
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        self.favorites_dir = Path(app_data) / "l_folder_favorites"
        self.favorites_dir.mkdir(parents=True, exist_ok=True)
        self.favorites_file = self.favorites_dir / "favorites.json"
        self.favorites = self._load_favorites()
        # 剪贴板历史
        self._clipboard_file = self.favorites_dir / "clipboard_history.json"
        self.clipboard_history: list[dict] = self._load_clipboard_history()

    def _load_favorites(self) -> list[dict]:
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
                        return data
            except Exception as e:
                print(f"加载剪贴板历史失败: {e}")
        return []

    def _save_clipboard_history(self) -> None:
        """保存剪贴板历史记录"""
        try:
            with open(self._clipboard_file, "w", encoding="utf-8") as f:
                json.dump(self.clipboard_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存剪贴板历史失败: {e}")

    def _setup_ui(self) -> None:
        """初始化UI"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 标题
        title_label = QtWidgets.QLabel("📁 文件夹收藏与命令")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 5px;")
        layout.addWidget(title_label)

        # 收藏夹列表
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemDoubleClicked.connect(self._execute_item)
        layout.addWidget(self.list_widget)

        # 按钮布局
        button_layout = QtWidgets.QHBoxLayout()

        self.add_button = QtWidgets.QPushButton("添加当前文件夹")
        self.add_button.clicked.connect(self._add_current_folder)
        button_layout.addWidget(self.add_button)

        self.browse_button = QtWidgets.QPushButton("浏览添加")
        self.browse_button.clicked.connect(self._browse_add_folder)
        button_layout.addWidget(self.browse_button)

        self.remove_button = QtWidgets.QPushButton("删除")
        self.remove_button.clicked.connect(self._remove_item)
        button_layout.addWidget(self.remove_button)

        self.execute_button = QtWidgets.QPushButton("执行")
        self.execute_button.clicked.connect(self._execute_item)
        button_layout.addWidget(self.execute_button)

        layout.addLayout(button_layout)

        # 类型筛选
        filter_layout = QtWidgets.QHBoxLayout()
        filter_label = QtWidgets.QLabel("显示:")
        filter_layout.addWidget(filter_label)

        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["全部", " 文件夹", "⚡ 命令"])
        self.filter_combo.currentIndexChanged.connect(self._refresh_list)
        filter_layout.addWidget(self.filter_combo)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # 说明标签
        hint_label = QtWidgets.QLabel("提示: 双击执行命令或打开文件夹，右键可删除或复制路径")
        hint_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint_label)

        # ===== 剪贴板历史区域 =====
        clipboard_group = QtWidgets.QWidget()
        clipboard_group.setStyleSheet(
            "background-color: #2b2b2b; border-radius: 5px; padding: 5px;"
        )
        clipboard_main_layout = QtWidgets.QVBoxLayout(clipboard_group)

        # 标题行（标题 + 清除按钮）
        clipboard_title_row = QtWidgets.QHBoxLayout()
        clipboard_title = QtWidgets.QLabel("📋 剪贴板历史")
        clipboard_title.setStyleSheet(
            "color: #cccccc; font-size: 12px; font-weight: bold; padding: 3px;"
        )
        clipboard_title_row.addWidget(clipboard_title)
        clipboard_title_row.addStretch()

        self.btn_refresh_clipboard = QtWidgets.QPushButton("🔄 刷新")
        self.btn_refresh_clipboard.setObjectName("ClipboardRefreshButton")
        self.btn_refresh_clipboard.setStyleSheet(
            """QPushButton#ClipboardRefreshButton {
                padding: 3px 12px;
                font-size: 12px;
                border: 1px solid rgba(137, 221, 255, 0.25);
                border-radius: 8px;
                background: rgba(255,255,255,0.04);
                color: #89DDFF;
                margin: 0 4px;
            }
            QPushButton#ClipboardRefreshButton:hover {
                background: rgba(137, 221, 255, 0.15);
            }"""
        )
        self.btn_refresh_clipboard.clicked.connect(self._refresh_clipboard_display)
        clipboard_title_row.addWidget(self.btn_refresh_clipboard)

        self.btn_clear_clipboard = QtWidgets.QPushButton("🗑️ 清除全部")
        self.btn_clear_clipboard.setObjectName("ClipboardClearButton")
        self.btn_clear_clipboard.setStyleSheet(
            """QPushButton#ClipboardClearButton {
                padding: 3px 12px;
                font-size: 12px;
                border: 1px solid rgba(255, 85, 85, 0.25);
                border-radius: 8px;
                background: rgba(255,255,255,0.04);
                color: #f07178;
                margin: 0 4px;
            }
            QPushButton#ClipboardClearButton:hover {
                background: rgba(255, 85, 85, 0.15);
            }"""
        )
        self.btn_clear_clipboard.clicked.connect(self._clear_clipboard_history)
        clipboard_title_row.addWidget(self.btn_clear_clipboard)
        clipboard_main_layout.addLayout(clipboard_title_row)

        # 剪贴板列表
        self.clipboard_list = QtWidgets.QListWidget()
        self.clipboard_list.setStyleSheet(
            "background-color: #1e1e1e; border: 1px solid #3c3c3c; color: #d4d4d4;"
        )
        self.clipboard_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.clipboard_list.customContextMenuRequested.connect(
            self._show_clipboard_context_menu
        )
        self.clipboard_list.itemDoubleClicked.connect(self._use_clipboard_item)
        clipboard_main_layout.addWidget(self.clipboard_list)

        layout.addWidget(clipboard_group)

    def _refresh_list(self) -> None:
        """刷新列表"""
        self.list_widget.clear()
        filter_type = self.filter_combo.currentIndex()

        for fav in self.favorites:
            item_type = fav.get("type", "folder")

            # 筛选
            if filter_type == 1 and item_type != "folder":
                continue
            if filter_type == 2 and item_type != "command":
                continue

            # 创建列表项
            if item_type == "folder":
                display_text = f"📁 {fav['path']}"
            else:
                display_text = f"⚡ {fav['name']}"

            item = QtWidgets.QListWidgetItem(display_text)
            item.setData(QtCore.Qt.UserRole, fav)
            self.list_widget.addItem(item)

    def _add_current_folder(self) -> None:
        """添加当前文件夹（从剪贴板或手动输入）"""
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
            if fav["path"] == path:
                QtWidgets.QMessageBox.information(self, "提示", "该文件夹已在收藏中")
                return

        self.favorites.append({"type": "folder", "name": folder_name, "path": path})
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
            fav = self.favorites[current_row]
            item_type = fav.get("type", "folder")

            if item_type == "folder":
                path = fav["path"]
                if os.path.exists(path):
                    self._navigate_to_folder(path)
                else:
                    QtWidgets.QMessageBox.warning(self, "警告", f"路径不存在: {path}")
            else:
                command = fav.get("command", "")
                if command:
                    try:
                        subprocess.Popen(command, shell=True)
                        print(f"执行命令: {command}")
                    except Exception as e:
                        QtWidgets.QMessageBox.warning(self, "错误", f"执行命令失败: {e}")

    def _navigate_to_folder(self, folder_path: str) -> None:
        """导航到指定文件夹（纯 Win32 API，不依赖 COM）"""
        user32 = ctypes.windll.user32

        # 优先使用快捷键触发时记录的前台窗口句柄
        if self._explorer_hwnd and user32.IsWindow(self._explorer_hwnd):
            hwnd = self._explorer_hwnd
            if self._navigate_explorer_hwnd(hwnd, folder_path):
                print(f"✓ 已导航到缓存窗口（句柄 {hwnd}）: {folder_path}")
                return
            print(f"✗ 缓存窗口导航失败，尝试其他 Explorer")

        # 尝试查找任意一个 Explorer 窗口
        found_hwnd = self._find_any_explorer_hwnd()
        if found_hwnd:
            if self._navigate_explorer_hwnd(found_hwnd, folder_path):
                self._explorer_hwnd = found_hwnd
                print(f"✓ 已导航到 Explorer 窗口（句柄 {found_hwnd}）: {folder_path}")
                return

        # 没有 Explorer 窗口，打开新窗口
        print(f"✓ 没有 Explorer 窗口，打开新窗口: {folder_path}")
        os.startfile(folder_path)

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
            print(f"✗ 导航失败: {e}")
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
                    navigate_action = QtGui.QAction("🚀 跳转到此文件夹", self)
                    navigate_action.triggered.connect(
                        lambda: self._navigate_to_folder(fav["path"])
                    )
                    copy_path_action = QtGui.QAction("📋 复制路径", self)
                    copy_path_action.triggered.connect(
                        lambda: self._copy_path_to_clipboard(fav["path"])
                    )
                else:
                    execute_action = QtGui.QAction("⚡ 执行命令", self)
                    navigate_action = None
                    copy_path_action = None

                execute_action.triggered.connect(self._execute_item)
                menu.addAction(execute_action)
                if navigate_action:
                    menu.addAction(navigate_action)
                if copy_path_action:
                    menu.addAction(copy_path_action)

            remove_action = QtGui.QAction("🗑️ 删除", self)
            remove_action.triggered.connect(self._remove_item)
            menu.addAction(remove_action)

            menu.exec(self.list_widget.mapToGlobal(position))

    def _copy_path_to_clipboard(self, path: str) -> None:
        """将路径复制到剪贴板"""
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(path)
        print(f"已复制路径到剪贴板: {path}")

    # ===== 剪贴板历史相关方法 =====

    def _refresh_clipboard_display(self) -> None:
        """初始化时刷新剪贴板显示列表（直接显示已加载的历史记录）"""
        self._update_clipboard_list()
        # 初始化当前剪贴板内容
        clipboard = QtWidgets.QApplication.clipboard()
        self._last_clipboard_text = clipboard.text().strip()

    def _on_clipboard_changed(self) -> None:
        """剪贴板内容变化时的回调（Qt 信号驱动）"""
        if not hasattr(self, "clipboard_list"):
            return

        # 获取当前剪贴板内容
        clipboard = QtWidgets.QApplication.clipboard()
        current_text = clipboard.text().strip()

        # 如果有新内容且与上次不同，添加到历史
        if (
            current_text
            and current_text != self._last_clipboard_text
            and current_text not in [item["text"] for item in self.clipboard_history]
        ):
            self.clipboard_history.insert(
                0,
                {
                    "text": current_text,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            self._save_clipboard_history()
            # 只在内容变化时更新UI
            self._update_clipboard_list()

        self._last_clipboard_text = current_text

    def _update_clipboard_list(self) -> None:
        """更新剪贴板列表显示（仅在内容变化时调用）"""
        if not hasattr(self, "clipboard_list"):
            return
        self.clipboard_list.clear()
        for item in self.clipboard_history:
            display_text = f"[{item['time']}] {item['text']}"
            list_item = QtWidgets.QListWidgetItem(display_text)
            list_item.setData(QtCore.Qt.UserRole, item["text"])
            list_item.setToolTip(item["text"])
            self.clipboard_list.addItem(list_item)
        # 更新记录数量显示
        if hasattr(self, "clipboard_count_label"):
            self.clipboard_count_label.setText(f"{len(self.clipboard_history)} 条")

    def _show_clipboard_context_menu(self, position: QtCore.QPoint) -> None:
        """显示剪贴板历史右键菜单"""
        item = self.clipboard_list.itemAt(position)
        if item:
            menu = QtWidgets.QMenu()

            copy_action = QtGui.QAction("📋 复制文本", self)
            text = item.data(QtCore.Qt.UserRole)
            copy_action.triggered.connect(
                lambda: QtWidgets.QApplication.clipboard().setText(text)
            )
            menu.addAction(copy_action)

            delete_action = QtGui.QAction("🗑️ 删除此条", self)
            delete_action.triggered.connect(
                lambda: self._delete_clipboard_item(item)
            )
            menu.addAction(delete_action)

            menu.addSeparator()

            clear_action = QtGui.QAction("🗑️ 清除全部历史", self)
            clear_action.triggered.connect(self._clear_clipboard_history)
            menu.addAction(clear_action)

            menu.exec(self.clipboard_list.mapToGlobal(position))

    def _use_clipboard_item(self, item: QtWidgets.QListWidgetItem) -> None:
        """双击使用剪贴板项"""
        text = item.data(QtCore.Qt.UserRole)
        if text:
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setText(text)
            display = text[:50] + "..." if len(text) > 50 else text
            print(f"已使用剪贴板项: {display}")

    def _delete_clipboard_item(self, item: QtWidgets.QListWidgetItem) -> None:
        """删除单条剪贴板记录"""
        text = item.data(QtCore.Qt.UserRole)
        self.clipboard_history = [
            h for h in self.clipboard_history if h["text"] != text
        ]
        self._save_clipboard_history()
        self._update_clipboard_list()

    def _clear_clipboard_history(self) -> None:
        """清除剪贴板历史"""
        if not self.clipboard_history:
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认清除",
            f"确定要清除全部 {len(self.clipboard_history)} 条剪贴板历史记录吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self.clipboard_history = []
            self._save_clipboard_history()
            self._update_clipboard_list()
            print("✓ 剪贴板历史已清除")
