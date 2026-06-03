# -*- coding: utf-8 -*-
"""
设置页面组件 - l_notepad 和 l_folder_favorites 的设置
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6 import QtCore, QtWidgets


class SettingsWidget(QtWidgets.QWidget):
    """设置页面组件"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = QtCore.QSettings("Lugwit", "l_notepad_pc")
        self._setup_data()
        self._setup_ui()
        self._load_settings()

    def _setup_data(self) -> None:
        """初始化数据路径"""
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        self.folder_fav_dir = Path(app_data) / "l_folder_favorites"
        self.folder_fav_dir.mkdir(parents=True, exist_ok=True)
        self.favorites_file = self.folder_fav_dir / "favorites.json"
        self.clipboard_file = self.folder_fav_dir / "clipboard_history.json"
        self.config_file = self.folder_fav_dir / "config.json"
        self._load_folder_fav_settings()
        self._load_folder_fav_hotkey()

    def _load_folder_fav_hotkey(self) -> None:
        """加载文件夹收藏的快捷键设置"""
        self.hotkey_button = "middle"  # 默认 Ctrl + 中键
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.hotkey_button = data.get("hotkey_button", "middle")
            except Exception:
                pass

    def _load_folder_fav_settings(self) -> None:
        """加载文件夹收藏的设置"""
        self.clipboard_max_items = 3  # 默认值
        if self.clipboard_file.exists():
            try:
                with open(self.clipboard_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.clipboard_max_items = data.get("max_items", 3)
            except Exception:
                pass

    def _setup_ui(self) -> None:
        """初始化UI"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 内容容器（在 ScrollArea 中）
        content_widget = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_widget)
        content_layout.setSpacing(20)

        # ===== 笔记本设置区域 =====
        notepad_group = self._create_group("📓 笔记本设置")
        notepad_layout = QtWidgets.QVBoxLayout()

        # 快捷键设置
        hotkey_layout = QtWidgets.QFormLayout()
        hotkey_layout.setSpacing(10)

        # 双击热键选择
        self.hotkey_combo = QtWidgets.QComboBox()
        self.hotkey_combo.addItems(["Ctrl", "Alt", "Shift"])
        self.hotkey_combo.currentTextChanged.connect(self._on_hotkey_changed)
        hotkey_layout.addRow("双击热键:", self.hotkey_combo)

        # 双击间隔
        self.hotkey_interval_spin = QtWidgets.QDoubleSpinBox()
        self.hotkey_interval_spin.setRange(0.05, 1.0)
        self.hotkey_interval_spin.setSingleStep(0.05)
        self.hotkey_interval_spin.setSuffix(" 秒")
        self.hotkey_interval_spin.valueChanged.connect(self._on_interval_changed)
        hotkey_layout.addRow("双击间隔:", self.hotkey_interval_spin)

        notepad_layout.addLayout(hotkey_layout)
        notepad_group.setLayout(notepad_layout)
        content_layout.addWidget(notepad_group)

        # ===== 文件夹收藏设置区域 =====
        folder_fav_group = self._create_group("📁 文件夹收藏设置")
        folder_fav_layout = QtWidgets.QVBoxLayout()

        # 快捷键设置
        hotkey_layout = QtWidgets.QHBoxLayout()
        hotkey_label = QtWidgets.QLabel("全局显示快捷键:")
        hotkey_layout.addWidget(hotkey_label)

        self.folder_hotkey_combo = QtWidgets.QComboBox()
        self.folder_hotkey_combo.addItem("Ctrl + 中键", "middle")
        self.folder_hotkey_combo.addItem("Ctrl + 左键", "left")
        # 加载已保存的设置
        idx = self.folder_hotkey_combo.findData(self.hotkey_button)
        if idx >= 0:
            self.folder_hotkey_combo.setCurrentIndex(idx)
        self.folder_hotkey_combo.currentIndexChanged.connect(self._on_folder_hotkey_changed)
        hotkey_layout.addWidget(self.folder_hotkey_combo)
        hotkey_layout.addStretch()
        folder_fav_layout.addLayout(hotkey_layout)

        # 快捷键提示
        hotkey_hint = QtWidgets.QLabel("提示: 切换后立即生效，无需重启")
        hotkey_hint.setStyleSheet("color: gray; font-size: 11px;")
        folder_fav_layout.addWidget(hotkey_hint)

        # 剪贴板历史条数
        clipboard_layout = QtWidgets.QHBoxLayout()
        clipboard_label = QtWidgets.QLabel("剪贴板历史显示条数:")
        clipboard_layout.addWidget(clipboard_label)

        self.clipboard_count_combo = QtWidgets.QComboBox()
        for i in range(1, 11):
            self.clipboard_count_combo.addItem(str(i), i)
        self.clipboard_count_combo.setCurrentText(str(self.clipboard_max_items))
        self.clipboard_count_combo.currentIndexChanged.connect(self._on_clipboard_count_changed)
        clipboard_layout.addWidget(self.clipboard_count_combo)
        clipboard_layout.addStretch()

        folder_fav_layout.addLayout(clipboard_layout)

        # 收藏夹数据管理
        data_mgmt_layout = QtWidgets.QHBoxLayout()
        data_mgmt_label = QtWidgets.QLabel("收藏夹数据:")
        data_mgmt_layout.addWidget(data_mgmt_label)

        self.open_data_btn = QtWidgets.QPushButton("打开数据文件夹")
        self.open_data_btn.clicked.connect(self._open_data_folder)
        data_mgmt_layout.addWidget(self.open_data_btn)

        self.clear_data_btn = QtWidgets.QPushButton("清空收藏夹")
        self.clear_data_btn.clicked.connect(self._clear_favorites)
        data_mgmt_layout.addWidget(self.clear_data_btn)

        data_mgmt_layout.addStretch()
        folder_fav_layout.addLayout(data_mgmt_layout)

        folder_fav_group.setLayout(folder_fav_layout)
        content_layout.addWidget(folder_fav_group)

        # ===== 通用设置区域 =====
        general_group = self._create_group("⚙️ 通用设置")
        general_layout = QtWidgets.QVBoxLayout()

        # 主题设置
        theme_layout = QtWidgets.QHBoxLayout()
        theme_label = QtWidgets.QLabel("主题:")
        theme_layout.addWidget(theme_label)

        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems(["深色", "浅色", "跟随系统"])
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch()
        general_layout.addLayout(theme_layout)

        # 字体大小
        font_layout = QtWidgets.QHBoxLayout()
        font_label = QtWidgets.QLabel("字体大小:")
        font_layout.addWidget(font_label)

        self.font_size_spin = QtWidgets.QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(10)
        font_layout.addWidget(self.font_size_spin)
        font_layout.addStretch()
        general_layout.addLayout(font_layout)

        general_group.setLayout(general_layout)
        content_layout.addWidget(general_group)

        # 添加弹性空间
        content_layout.addStretch()

        # 添加到滚动区域
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content_widget)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        layout.addWidget(scroll_area)

    def _create_group(self, title: str) -> QtWidgets.QGroupBox:
        """创建分组框"""
        group = QtWidgets.QGroupBox(title)
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        return group

    def _load_settings(self) -> None:
        """加载设置"""
        # 笔记本快捷键设置
        hotkey_key = self._settings.value("hotkey/key", "Ctrl", type=str)
        index = self.hotkey_combo.findText(hotkey_key)
        if index >= 0:
            self.hotkey_combo.setCurrentIndex(index)

        hotkey_interval = self._settings.value("hotkey/interval", 0.15, type=float)
        self.hotkey_interval_spin.setValue(hotkey_interval)

        # 文件夹收藏设置
        self.clipboard_count_combo.setCurrentText(str(self.clipboard_max_items))

        # 通用设置
        theme = self._settings.value("general/theme", "深色", type=str)
        index = self.theme_combo.findText(theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)

        font_size = self._settings.value("general/font_size", 10, type=int)
        self.font_size_spin.setValue(font_size)

    def _on_hotkey_changed(self, value: str) -> None:
        """热键改变"""
        self._settings.setValue("hotkey/key", value)
        # 通知主窗口更新热键
        if hasattr(self.parent(), "_hotkey_key_callback") and self.parent()._hotkey_key_callback:
            self.parent()._hotkey_key_callback(value)

    def _on_interval_changed(self, value: float) -> None:
        """间隔改变"""
        self._settings.setValue("hotkey/interval", value)
        # 通知主窗口更新间隔
        if hasattr(self.parent(), "_hotkey_interval_callback") and self.parent()._hotkey_interval_callback:
            self.parent()._hotkey_interval_callback(value)

    def _on_clipboard_count_changed(self, index: int) -> None:
        """剪贴板条数改变"""
        count = self.clipboard_count_combo.itemData(index)
        if count:
            self.clipboard_max_items = count
            self._save_clipboard_settings()
            # 通知文件夹收藏组件更新
            self._notify_clipboard_update()

    def _save_clipboard_settings(self) -> None:
        """保存剪贴板设置"""
        try:
            data = {"max_items": self.clipboard_max_items}
            with open(self.clipboard_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存剪贴板设置失败: {e}")

    def _notify_clipboard_update(self) -> None:
        """通知文件夹收藏组件更新"""
        # 可以通过信号机制通知，这里暂时打印日志
        print(f"剪贴板显示条数已更新为: {self.clipboard_max_items}")

    def _open_data_folder(self) -> None:
        """打开数据文件夹"""
        try:
            os.startfile(str(self.folder_fav_dir))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"打开文件夹失败: {e}")

    def _clear_favorites(self) -> None:
        """清空收藏夹"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认清空",
            "确定要清空所有收藏的文件夹和命令吗？此操作不可撤销。",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                # 清空收藏夹
                if self.favorites_file.exists():
                    self.favorites_file.write_text("[]", encoding="utf-8")

                # 清空剪贴板历史
                if self.clipboard_file.exists():
                    self.clipboard_file.write_text("[]", encoding="utf-8")

                QtWidgets.QMessageBox.information(self, "成功", "已清空所有收藏夹数据")

                # 通知主窗口刷新
                if hasattr(self.parent(), "refresh_notes"):
                    self.parent().refresh_notes()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "错误", f"清空失败: {e}")

    def _on_folder_hotkey_changed(self, index: int) -> None:
        """文件夹收藏快捷键改变"""
        self.hotkey_button = self.folder_hotkey_combo.itemData(index)
        self._save_folder_fav_hotkey()

    def _save_folder_fav_hotkey(self) -> None:
        """保存文件夹收藏快捷键设置"""
        try:
            data = {"hotkey_button": self.hotkey_button}
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存文件夹收藏快捷键设置失败: {e}")
