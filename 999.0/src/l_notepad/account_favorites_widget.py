# -*- coding: utf-8 -*-
"""
账号收藏标签页 - 收藏常用账号信息
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from PySide6 import QtCore, QtGui, QtWidgets

from .folder_favorites_widget import (
    _favorites_copy_to_clipboard,
    _favorites_read_from_clipboard,
)


class AccountItem(TypedDict, total=False):
    """账号收藏项目类型定义"""
    name: str  # 显示名称
    username: str  # 用户名
    password: str  # 密码
    server: str  # 服务器地址
    notes: str  # 备注


class AddAccountDialog(QtWidgets.QDialog):
    """添加/编辑账号的对话框"""
    
    def __init__(
        self,
        parent: QtWidgets.QWidget,
        name: str = "",
        username: str = "",
        password: str = "",
        server: str = "",
        notes: str = "",
    ) -> None:
        super().__init__(parent)
        self._setup_ui(name, username, password, server, notes)
    
    def _setup_ui(
        self,
        name: str,
        username: str,
        password: str,
        server: str,
        notes: str,
    ) -> None:
        """初始化 UI"""
        self.setWindowTitle("添加账号" if not name else "编辑账号")
        self.setMinimumWidth(450)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # 名称输入
        name_layout = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel("显示名称:")
        name_label.setFixedWidth(80)
        self.name_input = QtWidgets.QLineEdit(name)
        self.name_input.setPlaceholderText("例如：公司服务器账号")
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)
        
        # 用户名输入
        username_layout = QtWidgets.QHBoxLayout()
        username_label = QtWidgets.QLabel("用户名:")
        username_label.setFixedWidth(80)
        self.username_input = QtWidgets.QLineEdit(username)
        self.username_input.setPlaceholderText("输入用户名")
        username_layout.addWidget(username_label)
        username_layout.addWidget(self.username_input)
        layout.addLayout(username_layout)
        
        # 密码输入
        password_layout = QtWidgets.QHBoxLayout()
        password_label = QtWidgets.QLabel("密码:")
        password_label.setFixedWidth(80)
        self.password_input = QtWidgets.QLineEdit(password)
        self.password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password_input.setPlaceholderText("输入密码")
        # 添加显示/隐藏密码按钮
        show_password_btn = QtWidgets.QPushButton("显示")
        show_password_btn.setFixedWidth(50)
        show_password_btn.setCheckable(True)
        show_password_btn.toggled.connect(
            lambda checked: self.password_input.setEchoMode(
                QtWidgets.QLineEdit.Normal if checked else QtWidgets.QLineEdit.Password
            )
        )
        password_layout.addWidget(password_label)
        password_layout.addWidget(self.password_input)
        password_layout.addWidget(show_password_btn)
        layout.addLayout(password_layout)
        
        # 服务器地址输入
        server_layout = QtWidgets.QHBoxLayout()
        server_label = QtWidgets.QLabel("服务器地址:")
        server_label.setFixedWidth(80)
        self.server_input = QtWidgets.QLineEdit(server)
        self.server_input.setPlaceholderText("例如：192.168.1.100 或 server.example.com")
        server_layout.addWidget(server_label)
        server_layout.addWidget(self.server_input)
        layout.addLayout(server_layout)
        
        # 备注输入
        notes_layout = QtWidgets.QHBoxLayout()
        notes_label = QtWidgets.QLabel("备注:")
        notes_label.setFixedWidth(80)
        self.notes_input = QtWidgets.QLineEdit(notes)
        self.notes_input.setPlaceholderText("可选的备注信息")
        notes_layout.addWidget(notes_label)
        notes_layout.addWidget(self.notes_input)
        layout.addLayout(notes_layout)
        
        layout.addStretch()
        
        # 按钮
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def get_account_data(self) -> AccountItem:
        """获取账号数据"""
        return AccountItem(
            name=self.name_input.text().strip(),
            username=self.username_input.text().strip(),
            password=self.password_input.text(),
            server=self.server_input.text().strip(),
            notes=self.notes_input.text().strip(),
        )


class AccountFavoritesWidget(QtWidgets.QWidget):
    """账号收藏桌面组件（嵌入到 l_notepad）"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tab_account_favorites")
        self._accounts: list[AccountItem] = []
        self._setup_data()
        self._setup_ui()
        self._refresh_list()
    
    def _setup_data(self) -> None:
        """初始化数据（从文件加载）"""
        self._favorites_file = Path.home() / ".lugwit" / "l_notepad" / "account_favorites.json"
        self._favorites_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_favorites()
    
    def _setup_ui(self) -> None:
        """初始化 UI"""
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)
        
        # 标题
        title_label = QtWidgets.QLabel("👤 账号收藏")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2c3e50;")
        main_layout.addWidget(title_label)
        
        # 操作按钮栏
        btn_layout = QtWidgets.QHBoxLayout()
        
        add_btn = QtWidgets.QPushButton(" 添加账号")
        add_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogNewFolder))
        add_btn.clicked.connect(self._add_account)
        btn_layout.addWidget(add_btn)
        
        copy_btn = QtWidgets.QPushButton(" 复制信息")
        copy_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogSaveButton))
        copy_btn.clicked.connect(self._copy_account_info)
        btn_layout.addWidget(copy_btn)
        
        edit_btn = QtWidgets.QPushButton(" 编辑")
        edit_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        edit_btn.clicked.connect(self._edit_account)
        btn_layout.addWidget(edit_btn)
        
        delete_btn = QtWidgets.QPushButton(" 删除")
        delete_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogCancelButton))
        delete_btn.clicked.connect(self._remove_account)
        btn_layout.addWidget(delete_btn)
        
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)
        
        # 账号列表
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setObjectName("account_favorites_list")
        self.list_widget.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemDoubleClicked.connect(self._copy_account_info)
        self._apply_list_style()
        main_layout.addWidget(self.list_widget)
        
        # 提示标签
        self.hint_label = QtWidgets.QLabel(
            "💡 提示：点击「添加账号」保存常用账号信息，双击或点击「复制信息」可快速复制到剪贴板"
        )
        self.hint_label.setStyleSheet("color: #7f8c8d; font-size: 12px; padding: 4px;")
        self.hint_label.setWordWrap(True)
        main_layout.addWidget(self.hint_label)
    
    def _apply_list_style(self) -> None:
        """应用列表样式"""
        self.list_widget.setSpacing(2)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setIconSize(QtCore.QSize(16, 16))
        self.list_widget.setStyleSheet(
            """
            QListWidget#account_favorites_list {
                padding: 2px;
                outline: none;
                border: 1px solid #404040;
                border-radius: 4px;
                
            }
            QListWidget#account_favorites_list::item {
                min-height: 18px;
                padding: 1px 6px;
                margin: 1px 0;
                border-radius: 4px;
                color: #e0e0e0;
            }
            QListWidget#account_favorites_list::item:selected {
                border: 1px solid #44a8eb;
                border-radius: 4px;
                background-color: transparent;
            }
            QListWidget#account_favorites_list::item:hover {
                background-color: #2d2d2d;
            }
            """
        )
    
    def _load_favorites(self) -> None:
        """从文件加载收藏数据"""
        try:
            if self._favorites_file.exists():
                with open(self._favorites_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._accounts = [AccountItem(**item) for item in data]
            else:
                self._accounts = []
        except Exception as e:
            print(f"加载账号收藏失败: {e}")
            self._accounts = []
    
    def _save_favorites(self) -> None:
        """保存收藏数据到文件"""
        try:
            with open(self._favorites_file, "w", encoding="utf-8") as f:
                json.dump(self._accounts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存账号收藏失败: {e}")
    
    def _refresh_list(self) -> None:
        """刷新列表显示"""
        self.list_widget.clear()
        
        if not self._accounts:
            hint_item = QtWidgets.QListWidgetItem("暂无收藏的账号，点击「添加账号」开始使用")
            hint_item.setForeground(QtGui.QColor("#95a5a6"))
            hint_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.list_widget.addItem(hint_item)
            return
        
        for account in self._accounts:
            name = account.get("name", "未命名")
            username = account.get("username", "")
            server = account.get("server", "")
            
            # 创建列表项
            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(QtCore.QSize(0, 20))  # 高度对齐文件夹收藏
            
            # 设置文本
            display_text = name
            if username:
                display_text += f" ({username})"
            if server:
                display_text += f" - {server}"
            item.setText(display_text)
            
            # 设置图标
            item.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton))
            
            # 存储完整数据
            item.setData(QtCore.Qt.ItemDataRole.UserRole, account)
            
            self.list_widget.addItem(item)
    
    def _add_account(self) -> None:
        """添加新账号"""
        dialog = AddAccountDialog(self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            account_data = dialog.get_account_data()
            
            if not account_data.get("name"):
                QtWidgets.QMessageBox.warning(self, "提示", "请输入显示名称")
                return
            
            self._accounts.append(account_data)
            self._save_favorites()
            self._refresh_list()
    
    def _edit_account(self) -> None:
        """编辑选中的账号"""
        current_item = self.list_widget.currentItem()
        if not current_item:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择要编辑的账号")
            return
        
        account = current_item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not account:
            return
        
        dialog = AddAccountDialog(
            self,
            name=account.get("name", ""),
            username=account.get("username", ""),
            password=account.get("password", ""),
            server=account.get("server", ""),
            notes=account.get("notes", ""),
        )
        
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            new_data = dialog.get_account_data()
            
            if not new_data.get("name"):
                QtWidgets.QMessageBox.warning(self, "提示", "请输入显示名称")
                return
            
            # 更新数据
            idx = self.list_widget.row(current_item)
            if 0 <= idx < len(self._accounts):
                self._accounts[idx] = new_data
                self._save_favorites()
                self._refresh_list()
    
    def _remove_account(self) -> None:
        """删除选中的账号"""
        current_item = self.list_widget.currentItem()
        if not current_item:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择要删除的账号")
            return
        
        account = current_item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not account:
            return
        
        name = account.get("name", "未命名")
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除账号「{name}」吗？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            idx = self.list_widget.row(current_item)
            if 0 <= idx < len(self._accounts):
                self._accounts.pop(idx)
                self._save_favorites()
                self._refresh_list()
    
    def _copy_account_info(self) -> None:
        """复制账号信息到剪贴板"""
        current_item = self.list_widget.currentItem()
        if not current_item:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择要复制的账号")
            return
        
        account = current_item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not account:
            return
        
        # 格式化账号信息
        lines = []
        if account.get("name"):
            lines.append(f"名称: {account['name']}")
        if account.get("username"):
            lines.append(f"用户名: {account['username']}")
        if account.get("password"):
            lines.append(f"密码: {account['password']}")
        if account.get("server"):
            lines.append(f"服务器: {account['server']}")
        if account.get("notes"):
            lines.append(f"备注: {account['notes']}")
        
        if not lines:
            QtWidgets.QMessageBox.information(self, "提示", "该账号没有可复制的信息")
            return
        
        text = "\n".join(lines)
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(text)
        
        QtWidgets.QMessageBox.information(
            self, "已复制", f"账号信息已复制到剪贴板：\n\n{text}"
        )
    
    def _copy_value(self, value: str, label: str) -> None:
        """复制单个字段值到剪贴板"""
        if not value:
            return
        QtWidgets.QApplication.clipboard().setText(value)

    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        """显示右键菜单"""
        current_item = self.list_widget.itemAt(pos)
        account = current_item.data(QtCore.Qt.ItemDataRole.UserRole) if current_item else None

        menu = QtWidgets.QMenu(self)

        # 为每个有值的信息生成独立的复制菜单项
        if account:
            field_labels = [
                ("name", "名称"),
                ("username", "用户名"),
                ("password", "密码"),
                ("server", "服务器"),
                ("notes", "备注"),
            ]
            has_copy_field = False
            for key, label in field_labels:
                value = account.get(key, "")
                if not value:
                    continue
                has_copy_field = True
                action = menu.addAction(f" 复制{label}")
                action.triggered.connect(
                    lambda checked=False, v=value, lb=label: self._copy_value(v, lb)
                )
            if has_copy_field:
                menu.addSeparator()

            menu.addAction(" 复制全部信息").triggered.connect(self._copy_account_info)
            menu.addAction(" 复制条目").triggered.connect(
                lambda checked=False, a=account: self._copy_item(a)
            )
            menu.addAction(" 编辑").triggered.connect(self._edit_account)
            menu.addAction(" 删除").triggered.connect(self._remove_account)
            menu.addSeparator()

        paste_action = menu.addAction(" 粘贴条目")
        paste_action.setEnabled(_favorites_read_from_clipboard() is not None)
        paste_action.triggered.connect(self._paste_item)
        menu.exec(self.list_widget.mapToGlobal(pos))

    # ── 跨标签复制/粘贴（保留来源类型）────────────────────────────
    def _copy_item(self, account: dict) -> None:
        if account:
            _favorites_copy_to_clipboard(account)

    def _paste_item(self) -> None:
        data = _favorites_read_from_clipboard()
        if data is None:
            return
        self._accounts.append(data)
        self._save_favorites()
        self._refresh_list()

