# -*- coding: utf-8 -*-
"""
Muse CLI 标签页 Mixin

将 j_disc_backup_ui.py 中与 Muse CLI 标签页相关的所有方法单独拆出，
通过多重继承混入 JDiscBackupUI，避免主文件过大。

依赖约定（由宿主类提供）：
  - self.ui          : 通过 QUiLoader 加载的 UI 对象
  - self._MUSECLI_BAT / _MUSECLI_YAML 类属性路径
  - subprocess / threading / yaml / QApplication / QTimer 等已在宿主模块导入
"""

import json
import os
import subprocess
import threading
import traceback
from typing import Any, Dict, List, Optional

import yaml
from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QBoxLayout, QComboBox, QLayout, QSizePolicy, QTextEdit, QWidget

from l_qt_wgt_lib.smart_widget.code_editor import CodeEditorWidget # noqa: E402

_HAS_CODE_EDITOR = True


class HistoryComboBox(QComboBox):
    """可编辑的下拉框，自动维护输入历史（最多 _MAX_HISTORY 条）。

    用法：
        combo = HistoryComboBox(placeholder="请输入...")
        combo.commit()          # 手动把当前文本存入历史
        text = combo.current()  # 获取当前文本（等价于 currentText()）
    """

    _MAX_HISTORY: int = 20

    def __init__(self, placeholder: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        line_edit = self.lineEdit()
        assert line_edit is not None
        if placeholder:
            line_edit.setPlaceholderText(placeholder)
        line_edit.returnPressed.connect(self.commit)

    def current(self) -> str:
        """返回当前编辑框文本（去空白）。"""
        return self.currentText().strip()

    def commit(self) -> None:
        """将当前文本存入历史列表（去重 + 最多保留 _MAX_HISTORY 条）。"""
        text = self.current()
        if not text:
            return
        # 收集现有历史（排除空串）
        existing = [self.itemText(i) for i in range(self.count()) if self.itemText(i)]
        if text in existing:
            existing.remove(text)
        existing.insert(0, text)
        existing = existing[: self._MAX_HISTORY]
        self.blockSignals(True)
        self.clear()
        self.addItems(existing)
        self.setCurrentText(text)
        self.blockSignals(False)


class MuseCliTabMixin:
    """Muse CLI 标签页所有方法的 Mixin。"""

    _MUSECLI_BAT: str = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "bat", "muse_cli.bat"
    )
    _MUSECLI_YAML: str = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "cli", "muse_cli.yaml"
    )
    _MUSECLI_HISTORY_MAX: int = 20

    def _init_musecli_tab(self) -> None:
        self._musecli_process: Optional[subprocess.Popen] = None
        self._musecli_thread: Optional[threading.Thread] = None
        self._musecli_extra_history: Dict[str, List[str]] = {}
        self._musecli_yaml_data: Optional[List[Dict]] = None

        self._musecli_load_yaml()

        cmd_combo = getattr(self.ui, "musecliCommandComboBox", None)  # type: ignore[attr-defined]
        extra_combo = getattr(self.ui, "musecliExtraArgsComboBox", None)  # type: ignore[attr-defined]
        run_btn = getattr(self.ui, "musecliRunButton", None)  # type: ignore[attr-defined]
        stop_btn = getattr(self.ui, "musecliStopButton", None)  # type: ignore[attr-defined]
        clear_btn = getattr(self.ui, "musecliClearOutputButton", None)  # type: ignore[attr-defined]
        copy_btn = getattr(self.ui, "musecliCopyCommandButton", None)  # type: ignore[attr-defined]
        fill_btn = getattr(self.ui, "museCliFillParamsButton", None)  # type: ignore[attr-defined]
        recursive_cb = getattr(self.ui, "musecliRecursiveCheckBox", None)  # type: ignore[attr-defined]
        limit_spin = getattr(self.ui, "musecliLimitSpinBox", None)  # type: ignore[attr-defined]

        # 把帮助文本框替换为 CodeEditorWidget（日志模式，只读）
        self._musecli_upgrade_help_to_code_editor()

        # 把 lib / type / category 字段提升为 HistoryComboBox
        lib_edit = self._musecli_upgrade_to_history_combo(
            "musecliLibEdit", placeholder="如 h72、h55、rsvs、h42"
        )
        type_edit = self._musecli_upgrade_to_history_combo(
            "musecliTypeEdit", placeholder="如 simpleaction、engineering"
        )
        cat_edit = self._musecli_upgrade_to_history_combo(
            "musecliCategoryEdit", placeholder="如 动作资源/03角色动画"
        )

        if cmd_combo is not None:
            cmd_combo.currentTextChanged.connect(self._on_musecli_command_changed)
        if extra_combo is not None:
            extra_combo.lineEdit().returnPressed.connect(self._on_musecli_extra_args_committed)
        if run_btn is not None:
            run_btn.clicked.connect(self._on_musecli_run)
        if stop_btn is not None:
            stop_btn.clicked.connect(self._on_musecli_stop)
        if clear_btn is not None:
            clear_btn.clicked.connect(lambda: self._musecli_output_append("", clear=True))
        if copy_btn is not None:
            copy_btn.clicked.connect(self._on_musecli_copy_command)
        if fill_btn is not None:
            fill_btn.clicked.connect(self._on_musecli_fill_params)
        for w in [lib_edit, type_edit, cat_edit]:
            if w is not None:
                w.currentTextChanged.connect(self._musecli_update_preview)
        if recursive_cb is not None:
            recursive_cb.stateChanged.connect(self._musecli_update_preview)
        if limit_spin is not None:
            limit_spin.valueChanged.connect(self._musecli_update_preview)
        if extra_combo is not None:
            extra_combo.currentTextChanged.connect(self._musecli_update_preview)
        email_edit = getattr(self.ui, "musecliEmailEdit", None)  # type: ignore[attr-defined]
        if email_edit is not None:
            email_edit.textChanged.connect(self._musecli_update_preview)
            _dc = getattr(self, "data_center", None)  # type: ignore[attr-defined]
            _cfg = getattr(_dc, "config", None) if _dc is not None else None
            default_email = str(getattr(_cfg, "user_email", "") or "").strip()
            if default_email:
                email_edit.setText(default_email)

        self._on_musecli_command_changed(cmd_combo.currentText() if cmd_combo else "query")
        self._musecli_fill_presets(cmd_combo.currentText() if cmd_combo else "query")
        self._musecli_load_state()

    def _musecli_upgrade_help_to_code_editor(self) -> None:
        """把 musecliHelpTextEdit 替换为 CodeEditorWidget（log 模式只读）。"""
        if not _HAS_CODE_EDITOR:
            return
        old = getattr(self.ui, "musecliHelpTextEdit", None)  # type: ignore[attr-defined]
        if old is None or isinstance(old, CodeEditorWidget):
            return
        layout = self._find_direct_layout(old)
        parent_widget = old.parentWidget()
        new_editor = CodeEditorWidget(parent=parent_widget)
        new_editor.setObjectName("musecliHelpTextEdit")
        new_editor.setSizePolicy(old.sizePolicy())
        new_editor.setMinimumSize(old.minimumSize())
        new_editor.setMaximumSize(old.maximumSize())
        new_editor.editor()._switch_mode("log")  # type: ignore[attr-defined]
        new_editor.editor().setReadOnly(True)
        if layout is not None:
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is not None and item.widget() is old:
                    layout.removeWidget(old)
                    old.hide()
                    old.setParent(None)  # type: ignore[arg-type]
                    layout.insertWidget(i, new_editor)
                    break
        setattr(self.ui, "musecliHelpTextEdit", new_editor)

    def _musecli_save_state(self) -> None:
        """把 musecli 标签页的历史记录写入 ui_state_cache_file 的 'musecli' key。"""
        cache_file = getattr(self, "_ui_state_cache_file", "")  # type: ignore[attr-defined]
        if not cache_file:
            return
        existing: Dict[str, Any] = {}
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except Exception:
                traceback.print_exc()
        musecli_state: Dict[str, Any] = {
            "extra_history": self._musecli_extra_history,
        }
        for field in ("musecliLibEdit", "musecliTypeEdit", "musecliCategoryEdit"):
            combo = getattr(self.ui, field, None)  # type: ignore[attr-defined]
            if combo is not None:
                musecli_state[field] = [combo.itemText(i) for i in range(combo.count())]
        existing["musecli"] = musecli_state
        try:
            parent_dir = os.path.dirname(os.path.normpath(cache_file))
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()

    def _musecli_load_state(self) -> None:
        """从 ui_state_cache_file 的 'musecli' key 恢复历史记录到各 combo。"""
        cache_file = getattr(self, "_ui_state_cache_file", "")  # type: ignore[attr-defined]
        if not cache_file or not os.path.exists(cache_file):
            return
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                state = json.load(f) or {}
        except Exception:
            traceback.print_exc()
            return
        musecli_state = state.get("musecli")
        if not isinstance(musecli_state, dict):
            return
        extra_history = musecli_state.get("extra_history")
        if isinstance(extra_history, dict):
            for k, v in extra_history.items():
                if isinstance(v, list):
                    self._musecli_extra_history[str(k)] = [str(i) for i in v]
        for field in ("musecliLibEdit", "musecliTypeEdit", "musecliCategoryEdit"):
            items = musecli_state.get(field)
            if not isinstance(items, list):
                continue
            combo = getattr(self.ui, field, None)  # type: ignore[attr-defined]
            if combo is None:
                continue
            existing = [combo.itemText(i) for i in range(combo.count())]
            to_add = [str(v) for v in items if str(v) not in existing]
            if to_add:
                combo.addItems(to_add)

    def _musecli_fill_presets(self, cmd_name: str) -> None:
        """把当前命令中各参数的 presets 填入对应的 HistoryComboBox。

        flag -> combo 的映射关系：
          --lib      -> musecliLibEdit
          --type     -> musecliTypeEdit
          --category -> musecliCategoryEdit
        """
        flag_to_widget = {
            "--lib": "musecliLibEdit",
            "--type": "musecliTypeEdit",
            "--category": "musecliCategoryEdit",
        }
        cmd_info = self._musecli_get_cmd_info(cmd_name)
        if cmd_info is None:
            return
        for group in cmd_info.get("groups", []):
            if not isinstance(group, dict):
                continue
            for arg in group.get("args", []):
                if not isinstance(arg, dict):
                    continue
                presets = arg.get("presets")
                if not presets or not isinstance(presets, list):
                    continue
                flags = arg.get("flags", [])
                widget_name = next(
                    (flag_to_widget[f] for f in flags if f in flag_to_widget), None
                )
                if widget_name is None:
                    continue
                combo = getattr(self.ui, widget_name, None)  # type: ignore[attr-defined]
                if combo is None:
                    continue
                preset_strs = [str(p) for p in presets if p is not None]
                current = combo.currentText()
                existing = [combo.itemText(i) for i in range(combo.count())]
                to_add = [p for p in preset_strs if p not in existing]
                if to_add:
                    combo.addItems(to_add)
                if not current:
                    combo.setCurrentIndex(0)

    @staticmethod
    def _find_direct_layout(widget: QWidget) -> Optional[QBoxLayout]:
        """递归查找直接包含 widget 的 QBoxLayout（而非 parentWidget 的顶层布局）。"""
        parent = widget.parentWidget()
        if parent is None:
            return None

        def _search(layout: Optional[QLayout]) -> Optional[QBoxLayout]:
            if layout is None:
                return None
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is None:
                    continue
                if item.widget() is widget and isinstance(layout, QBoxLayout):
                    return layout
                sub = item.layout()
                if sub is not None:
                    found = _search(sub)
                    if found is not None:
                        return found
            return None

        return _search(parent.layout())

    def _musecli_upgrade_to_history_combo(
        self, widget_name: str, placeholder: str = ""
    ) -> Optional["HistoryComboBox"]:
        """将 UI 中名为 widget_name 的 QComboBox 原地替换为 HistoryComboBox。

        替换后同名属性写回 self.ui，布局中的位置不变。
        返回新实例，如果控件不存在则返回 None。
        """
        old = getattr(self.ui, widget_name, None)  # type: ignore[attr-defined]
        if old is None:
            return None
        parent_widget = old.parentWidget()
        layout = self._find_direct_layout(old)
        preset_items = [old.itemText(i) for i in range(old.count())]
        new_combo = HistoryComboBox(placeholder=placeholder, parent=parent_widget)
        new_combo.setObjectName(widget_name)
        new_combo.setToolTip(old.toolTip())
        new_combo.setSizePolicy(old.sizePolicy())
        new_combo.setMinimumSize(old.minimumSize())
        new_combo.setMaximumSize(old.maximumSize())
        if preset_items:
            new_combo.addItems(preset_items)
            new_combo.setCurrentIndex(0)
        if layout is not None:
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is not None and item.widget() is old:
                    layout.removeWidget(old)
                    old.hide()
                    old.setParent(None)  # type: ignore[arg-type]
                    layout.insertWidget(i, new_combo)
                    break
        setattr(self.ui, widget_name, new_combo)
        return new_combo

    def _musecli_load_yaml(self) -> None:
        try:
            with open(self._MUSECLI_YAML, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self._musecli_yaml_data = data.get("commands", []) if isinstance(data, dict) else []
        except Exception:
            self._musecli_yaml_data = []

    def _musecli_get_cmd_info(self, cmd_name: str) -> Optional[Dict]:
        if not self._musecli_yaml_data:
            return None
        for cmd in self._musecli_yaml_data:
            if isinstance(cmd, dict) and cmd.get("name") == cmd_name:
                return cmd
        return None

    def _musecli_cmd_has_flag(self, cmd_name: str, flag: str) -> bool:
        """判断指定命令是否包含某个参数 flag（如 '--recursive'）。"""
        cmd_info = self._musecli_get_cmd_info(cmd_name)
        if cmd_info is None:
            return False
        for group in cmd_info.get("groups", []):
            if not isinstance(group, dict):
                continue
            for arg in group.get("args", []):
                if not isinstance(arg, dict):
                    continue
                if flag in arg.get("flags", []):
                    return True
        return False

    def _musecli_render_help(self, cmd_name: str) -> str:
        cmd_info = self._musecli_get_cmd_info(cmd_name)
        if cmd_info is None:
            return f"（未找到命令 {cmd_name!r} 的帮助信息）"
        lines = [f"命令: {cmd_name}", f"说明: {cmd_info.get('help', '')}", ""]
        groups = cmd_info.get("groups", [])
        for group in groups:
            if not isinstance(group, dict):
                continue
            title = group.get("title", "")
            desc = group.get("description", "")
            if title:
                lines.append(f"[{title}]")
            if desc:
                lines.append(f"  {desc}")
            args = group.get("args", [])
            for arg in args:
                if not isinstance(arg, dict):
                    continue
                flags = arg.get("flags", [])
                flag_str = ", ".join(flags)
                help_text = arg.get("help", "")
                required = " *必填*" if arg.get("required") else ""
                default = (
                    f"  默认: {arg['default']}"
                    if "default" in arg and arg["default"] is not None
                    else ""
                )
                choices = f"  可选值: {arg['choices']}" if "choices" in arg else ""
                action = "  (开关)" if arg.get("action") == "store_true" else ""
                lines.append(f"  {flag_str}{required}{action}")
                if help_text:
                    lines.append(f"    {help_text}{default}{choices}")
            if title or args:
                lines.append("")
        return "\n".join(lines)

    def _musecli_fill_params_template(self, cmd_name: str) -> str:
        cmd_info = self._musecli_get_cmd_info(cmd_name)
        if cmd_info is None:
            return ""
        parts = []
        skip_flags = {"--lib", "--type", "--email", "--category", "--recursive", "--limit"}
        groups = cmd_info.get("groups", [])
        for group in groups:
            if not isinstance(group, dict):
                continue
            args = group.get("args", [])
            for arg in args:
                if not isinstance(arg, dict):
                    continue
                if arg.get("required"):
                    continue
                flags = arg.get("flags", [])
                if not flags:
                    continue
                flag = flags[0]
                if flag in skip_flags:
                    continue
                action = arg.get("action", "")
                default = arg.get("default")
                choices = arg.get("choices", [])
                if action == "store_true":
                    parts.append(flag)
                elif choices:
                    parts.append(f"{flag} {choices[0]}")
                elif default is not None and default != "null":
                    parts.append(f"{flag} {default}")
                else:
                    dest = arg.get("dest") or flag.lstrip("-").replace("-", "_")
                    parts.append(f"{flag} <{dest}>")
        return " ".join(parts)

    def _musecli_build_command(self) -> str:
        cmd = getattr(self.ui, "musecliCommandComboBox", None)  # type: ignore[attr-defined]
        lib = getattr(self.ui, "musecliLibEdit", None)  # type: ignore[attr-defined]
        typ = getattr(self.ui, "musecliTypeEdit", None)  # type: ignore[attr-defined]
        cat = getattr(self.ui, "musecliCategoryEdit", None)  # type: ignore[attr-defined]
        recursive = getattr(self.ui, "musecliRecursiveCheckBox", None)  # type: ignore[attr-defined]
        limit = getattr(self.ui, "musecliLimitSpinBox", None)  # type: ignore[attr-defined]
        extra = getattr(self.ui, "musecliExtraArgsComboBox", None)  # type: ignore[attr-defined]

        cmd_name = cmd.currentText().strip() if cmd else "query"
        lib_val = lib.currentText().strip() if lib else ""
        type_val = typ.currentText().strip() if typ else ""
        cat_val = cat.currentText().strip() if cat else ""
        recursive_val = recursive.isChecked() if recursive else False
        limit_val = limit.value() if limit else 1000
        extra_val = extra.currentText().strip() if extra else ""
        email_edit = getattr(self.ui, "musecliEmailEdit", None)  # type: ignore[attr-defined]
        email_val = email_edit.text().strip() if email_edit else ""

        bat = self._MUSECLI_BAT
        parts = [f'"{bat}"', cmd_name]
        if lib_val:
            parts += ["--lib", lib_val]
        if type_val:
            parts += ["--type", type_val]
        if cat_val:
            parts += ["--category", f'"{cat_val}"']
        if recursive_val:
            parts.append("--recursive")
        if limit_val != 1000:
            parts += ["--limit", str(limit_val)]
        if email_val:
            parts += ["--email", email_val]
        if extra_val:
            parts.append(extra_val)
        return " ".join(parts)

    def _musecli_update_preview(self, *_: Any) -> None:
        label = getattr(self.ui, "musecliCommandPreviewLabel", None)  # type: ignore[attr-defined]
        if label is None:
            return
        label.setText(f"命令预览: {self._musecli_build_command()}")

    def _on_musecli_command_changed(self, cmd_name: str) -> None:
        help_edit = getattr(self.ui, "musecliHelpTextEdit", None)  # type: ignore[attr-defined]
        if help_edit is not None:
            help_text = self._musecli_render_help(cmd_name)
            if isinstance(help_edit, CodeEditorWidget):
                help_edit.set_code(help_text)
            elif isinstance(help_edit, QTextEdit):
                help_edit.setPlainText(help_text)

        extra_combo = getattr(self.ui, "musecliExtraArgsComboBox", None)  # type: ignore[attr-defined]
        if extra_combo is not None:
            extra_combo.blockSignals(True)
            extra_combo.clear()
            history = self._musecli_extra_history.get(cmd_name, [])
            extra_combo.addItems(history)
            extra_combo.setCurrentText(history[0] if history else "")
            extra_combo.blockSignals(False)

        recursive_cb = getattr(self.ui, "musecliRecursiveCheckBox", None)  # type: ignore[attr-defined]
        if recursive_cb is not None:
            has_recursive = self._musecli_cmd_has_flag(cmd_name, "--recursive")
            recursive_cb.setEnabled(has_recursive)
            if not has_recursive:
                recursive_cb.setChecked(False)

        self._musecli_fill_presets(cmd_name)
        self._musecli_update_preview()

    def _on_musecli_extra_args_committed(self) -> None:
        extra_combo = getattr(self.ui, "musecliExtraArgsComboBox", None)  # type: ignore[attr-defined]
        cmd_combo = getattr(self.ui, "musecliCommandComboBox", None)  # type: ignore[attr-defined]
        if extra_combo is None or cmd_combo is None:
            return
        text = extra_combo.currentText().strip()
        if not text:
            return
        cmd_name = cmd_combo.currentText()
        history = self._musecli_extra_history.setdefault(cmd_name, [])
        if text in history:
            history.remove(text)
        history.insert(0, text)
        if len(history) > self._MUSECLI_HISTORY_MAX:
            history[:] = history[: self._MUSECLI_HISTORY_MAX]
        extra_combo.blockSignals(True)
        extra_combo.clear()
        extra_combo.addItems(history)
        extra_combo.setCurrentText(text)
        extra_combo.blockSignals(False)

    def _on_musecli_fill_params(self) -> None:
        cmd_combo = getattr(self.ui, "musecliCommandComboBox", None)  # type: ignore[attr-defined]
        extra_combo = getattr(self.ui, "musecliExtraArgsComboBox", None)  # type: ignore[attr-defined]
        if cmd_combo is None or extra_combo is None:
            return
        template = self._musecli_fill_params_template(cmd_combo.currentText())
        extra_combo.setCurrentText(template)
        self._musecli_update_preview()

    def _on_musecli_copy_command(self) -> None:
        cmd = self._musecli_build_command()
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(cmd)

    def _musecli_output_append(self, text: str, clear: bool = False) -> None:
        out = getattr(self.ui, "musecliOutputTextEdit", None)  # type: ignore[attr-defined]
        if out is None:
            return
        if clear:
            out.clear()
            return
        out.moveCursor(QTextCursor.MoveOperation.End)
        out.insertPlainText(text)
        out.moveCursor(QTextCursor.MoveOperation.End)

    def _on_musecli_run(self) -> None:
        if self._musecli_process is not None:
            return
        # 运行前先把所有历史字段的当前值存入历史
        self._on_musecli_extra_args_committed()
        for field in ("musecliLibEdit", "musecliTypeEdit", "musecliCategoryEdit"):
            w = getattr(self.ui, field, None)  # type: ignore[attr-defined]
            if isinstance(w, HistoryComboBox):
                w.commit()
        cmd = self._musecli_build_command()
        self._musecli_output_append(f"$ {cmd}\n", clear=False)

        run_btn = getattr(self.ui, "musecliRunButton", None)  # type: ignore[attr-defined]
        stop_btn = getattr(self.ui, "musecliStopButton", None)  # type: ignore[attr-defined]
        if run_btn:
            run_btn.setEnabled(False)
        if stop_btn:
            stop_btn.setEnabled(True)

        env = os.environ.copy()
        env["MUSE_CLI_NO_PAUSE"] = "1"
        email_edit = getattr(self.ui, "musecliEmailEdit", None)  # type: ignore[attr-defined]
        email_val = email_edit.text().strip() if email_edit else ""
        if email_val:
            env["MUSE_USER_EMAIL"] = email_val
        try:
            self._musecli_process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except Exception as e:
            self._musecli_output_append(f"[启动失败] {e}\n")
            self._musecli_process = None
            if run_btn:
                run_btn.setEnabled(True)
            if stop_btn:
                stop_btn.setEnabled(False)
            return

        def _read_output() -> None:
            proc = self._musecli_process
            if proc is None or proc.stdout is None:
                return
            for line in proc.stdout:
                QTimer.singleShot(0, lambda ln=line: self._musecli_output_append(ln))
            proc.wait()
            QTimer.singleShot(0, self._musecli_on_process_done)

        self._musecli_thread = threading.Thread(target=_read_output, daemon=True)
        self._musecli_thread.start()

    def _on_musecli_stop(self) -> None:
        if self._musecli_process is not None:
            try:
                self._musecli_process.terminate()
            except Exception:
                pass
        self._musecli_on_process_done()

    def _musecli_on_process_done(self) -> None:
        self._musecli_process = None
        self._musecli_thread = None
        run_btn = getattr(self.ui, "musecliRunButton", None)  # type: ignore[attr-defined]
        stop_btn = getattr(self.ui, "musecliStopButton", None)  # type: ignore[attr-defined]
        if run_btn:
            run_btn.setEnabled(True)
        if stop_btn:
            stop_btn.setEnabled(False)
        self._musecli_output_append("\n[完成]\n")
