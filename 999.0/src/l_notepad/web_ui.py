# -*- coding: utf-8 -*-

from __future__ import annotations

import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets


class WebNotepadWindow(QtWidgets.QMainWindow):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.setWindowTitle("L Notepad - Web")
        self._settings = QtCore.QSettings("Lugwit", "l_notepad")
        self.resize(1100, 720)
        self._restore_window_state()

        # Try embedded web view; fall back to system browser if QtWebEngine is missing.
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore
        except Exception:
            self._setup_fallback()
            webbrowser.open(self.url)
            return

        view = QWebEngineView()
        view.setUrl(QtCore.QUrl(self.url))
        self.setCentralWidget(view)

        tb = QtWidgets.QToolBar("导航")
        tb.setMovable(False)
        self.addToolBar(tb)

        act_back = QtGui.QAction("返回", self)
        act_forward = QtGui.QAction("前进", self)
        act_reload = QtGui.QAction("刷新", self)
        act_home = QtGui.QAction("主页", self)
        act_open = QtGui.QAction("外部打开当前页", self)
        act_copy = QtGui.QAction("复制当前链接", self)

        act_back.triggered.connect(view.back)
        act_forward.triggered.connect(view.forward)
        act_reload.triggered.connect(view.reload)
        act_home.triggered.connect(lambda: view.setUrl(QtCore.QUrl(self.url)))
        act_open.triggered.connect(lambda: webbrowser.open(view.url().toString() or self.url))
        act_copy.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(view.url().toString() or self.url))

        tb.addAction(act_back)
        tb.addAction(act_forward)
        tb.addAction(act_reload)
        tb.addSeparator()
        tb.addAction(act_home)
        tb.addSeparator()
        tb.addAction(act_open)
        tb.addAction(act_copy)

        self.statusBar().showMessage(self.url)
        view.urlChanged.connect(lambda u: self.statusBar().showMessage(u.toString()))
        view.loadFinished.connect(self._on_load_finished)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self._save_window_state()
        super().closeEvent(event)

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            return
        QtWidgets.QMessageBox.warning(
            self,
            "加载失败",
            "网页加载失败。\n"
            "可能原因：后端未启动 / 端口被占用 / 网络策略阻拦。\n"
            "你可以尝试「刷新」，或使用工具栏的「外部打开当前页」。",
        )

    def _restore_window_state(self) -> None:
        geo = self._settings.value("window/geometry")
        state = self._settings.value("window/state")
        if isinstance(geo, (bytes, bytearray)):
            self.restoreGeometry(geo)
        if isinstance(state, (bytes, bytearray)):
            self.restoreState(state)

    def _save_window_state(self) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/state", self.saveState())

    def _setup_fallback(self) -> None:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        label = QtWidgets.QLabel(
            "当前环境缺少 QtWebEngine，无法内嵌网页。\n"
            "已尝试用系统默认浏览器打开网页端。"
        )
        label.setWordWrap(True)

        url_edit = QtWidgets.QLineEdit(self.url)
        url_edit.setReadOnly(True)

        btn_row = QtWidgets.QHBoxLayout()
        btn_open = QtWidgets.QPushButton("用浏览器打开")
        btn_copy = QtWidgets.QPushButton("复制链接")
        btn_open.clicked.connect(lambda: webbrowser.open(self.url))
        btn_copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.url))
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_copy)
        btn_row.addStretch(1)

        layout.addWidget(label)
        layout.addWidget(url_edit)
        layout.addLayout(btn_row)
        layout.addStretch(1)
        self.setCentralWidget(w)

