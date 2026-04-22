# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore, QtWidgets

from .api_client import ApiError, NotepadApi, NoteDto


APP_QSS = r"""
QWidget {
  font-family: "Segoe UI", "Microsoft YaHei UI", Arial;
  font-size: 12px;
  color: #E9EEF5;
}
QMainWindow {
  background: #0E1116;
}
QLineEdit, QTextEdit {
  background: #121826;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 10px;
  padding: 10px;
  selection-background-color: rgba(77, 163, 255, 0.35);
}
QLineEdit:focus, QTextEdit:focus {
  border: 1px solid rgba(77, 163, 255, 0.75);
}
QPushButton {
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 10px;
  padding: 8px 12px;
}
QPushButton:hover {
  background: rgba(255,255,255,0.10);
}
QPushButton:pressed {
  background: rgba(255,255,255,0.14);
}
QPushButton#PrimaryButton {
  background: rgba(77, 163, 255, 0.22);
  border: 1px solid rgba(77, 163, 255, 0.55);
}
QPushButton#DangerButton {
  background: rgba(255, 92, 92, 0.16);
  border: 1px solid rgba(255, 92, 92, 0.50);
}
QListWidget {
  background: #0F1522;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px;
  padding: 6px;
}
QListWidget::item {
  background: transparent;
  border-radius: 10px;
  padding: 10px 10px;
}
QListWidget::item:selected {
  background: rgba(77, 163, 255, 0.18);
  border: 1px solid rgba(77, 163, 255, 0.22);
}
QSplitter::handle {
  background: rgba(255,255,255,0.05);
}
QStatusBar {
  background: rgba(255,255,255,0.04);
  color: rgba(233,238,245,0.80);
}
"""


@dataclass
class UiState:
    current_note_id: int | None = None
    dirty: bool = False


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, api: NotepadApi) -> None:
        super().__init__()
        self.api = api
        self.state = UiState()
        self.setStyleSheet(APP_QSS)
        self.setWindowTitle("L Notepad")
        self.resize(980, 640)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题…")
        self.search_edit.textChanged.connect(self._apply_filter)

        self.notes_list = QtWidgets.QListWidget()
        self.notes_list.setMinimumWidth(260)
        self.notes_list.setSpacing(4)
        self.notes_list.itemSelectionChanged.connect(self._on_selection_changed)

        self.title_edit = QtWidgets.QLineEdit()
        self.title_edit.setPlaceholderText("标题")
        self.title_edit.textEdited.connect(self._mark_dirty)

        self.content_edit = QtWidgets.QTextEdit()
        self.content_edit.textChanged.connect(self._mark_dirty)

        self.btn_new = QtWidgets.QPushButton("新建")
        self.btn_save = QtWidgets.QPushButton("保存")
        self.btn_delete = QtWidgets.QPushButton("删除")
        self.btn_refresh = QtWidgets.QPushButton("刷新")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_delete.setObjectName("DangerButton")

        self.btn_new.clicked.connect(self._new_note)
        self.btn_save.clicked.connect(self._save_note)
        self.btn_delete.clicked.connect(self._delete_note)
        self.btn_refresh.clicked.connect(self.refresh_notes)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_layout.addWidget(self.search_edit)
        left_layout.addWidget(self.notes_list)

        left_btn_row = QtWidgets.QHBoxLayout()
        left_btn_row.addWidget(self.btn_refresh)
        left_layout.addLayout(left_btn_row)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setSpacing(10)
        right_layout.addWidget(self.title_edit)
        right_layout.addWidget(self.content_edit, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.btn_new)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch(1)
        right_layout.addLayout(btn_row)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self.status = self.statusBar()
        self.refresh_notes()

    def refresh_notes(self) -> None:
        try:
            notes = self.api.list_notes()
        except ApiError as e:
            self._show_error(str(e))
            return

        current_id = self.state.current_note_id
        query = self.search_edit.text().strip()
        self.notes_list.blockSignals(True)
        self.notes_list.clear()
        for n in notes:
            if query and query.lower() not in n.title.lower():
                continue
            item = QtWidgets.QListWidgetItem(f"{n.title}\n{n.updated_at}")
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, n.id)
            item.setToolTip(f"#{n.id}  {n.updated_at}")
            self.notes_list.addItem(item)
        self.notes_list.blockSignals(False)

        if current_id is not None:
            self._select_note_id(current_id)
        elif self.notes_list.count() > 0:
            self.notes_list.setCurrentRow(0)
        else:
            self._set_editor(None)

    def _apply_filter(self) -> None:
        # lightweight local filter; refresh keeps the list consistent
        self.refresh_notes()

    def _select_note_id(self, note_id: int) -> None:
        for i in range(self.notes_list.count()):
            item = self.notes_list.item(i)
            if int(item.data(QtCore.Qt.ItemDataRole.UserRole)) == int(note_id):
                self.notes_list.setCurrentRow(i)
                return

    def _on_selection_changed(self) -> None:
        if self.state.dirty and not self._confirm_discard():
            self._select_note_id(self.state.current_note_id) if self.state.current_note_id else None
            return

        items = self.notes_list.selectedItems()
        if not items:
            self._set_editor(None)
            return

        note_id = int(items[0].data(QtCore.Qt.ItemDataRole.UserRole))
        try:
            note = self.api.get_note(note_id)
        except ApiError as e:
            self._show_error(str(e))
            return
        self._set_editor(note)

    def _new_note(self) -> None:
        if self.state.dirty and not self._confirm_discard():
            return
        self._set_editor(None)
        self.title_edit.setText("未命名")
        self.content_edit.setPlainText("")
        self.state.current_note_id = None
        self.state.dirty = True
        self._update_title()

    def _save_note(self) -> None:
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

    def _delete_note(self) -> None:
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

    def _set_editor(self, note: NoteDto | None) -> None:
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

    def _mark_dirty(self) -> None:
        if not self.state.dirty:
            self.state.dirty = True
            self._update_title()

    def _confirm_discard(self) -> bool:
        ret = QtWidgets.QMessageBox.question(self, "未保存更改", "当前笔记有未保存更改，是否丢弃？")
        return ret == QtWidgets.QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.state.dirty and not self._confirm_discard():
            event.ignore()
            return
        event.accept()

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "错误", message)

    def _update_title(self) -> None:
        suffix = " *" if self.state.dirty else ""
        cur = f"#{self.state.current_note_id}" if self.state.current_note_id else "新建"
        self.setWindowTitle(f"L Notepad - {cur}{suffix}")

