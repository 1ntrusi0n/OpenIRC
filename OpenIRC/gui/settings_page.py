"""Validated settings and first-run setup using native property pages."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget, QWizard, QWizardPage,
)

from OpenIRC.config.settings import RESTART_REQUIRED, ServerSettings
from .widgets import Page, value


SECTIONS = {
    "Identity": ["server_name", "network_name", "description", "admin_name", "admin_email"],
    "Listening": ["bind_address", "irc_enabled", "irc_port", "ipv6_enabled", "tls_enabled", "tls_port", "tls_cert", "tls_key"],
    "Users": ["allow_anonymous", "max_users", "max_connections_per_ip", "nick_length", "max_channels_per_user", "reservation_policy"],
    "Channels": ["max_channels", "default_channel_modes", "auto_register_channels", "destroy_empty_channels"],
    "Security": ["host_privacy", "require_tls_auth", "commands_per_second", "messages_per_second", "connections_per_minute", "nick_changes_per_minute", "auth_failures", "lockout_seconds", "flood_penalty_seconds"],
    "IRCX": ["ircx_enabled", "unicode_mode", "listx_enabled", "access_enabled", "prop_enabled", "create_enabled", "auditorium_enabled", "listx_limit"],
    "Logging": ["log_level", "log_directory", "log_max_bytes", "log_retention"],
    "Startup": ["auto_start", "minimize_to_tray", "confirm_shutdown"],
    "Advanced": ["registration_timeout", "ping_interval", "ping_timeout", "send_queue_limit"],
}
LABELS = {
    "tls_cert": "TLS certificate (PEM)", "tls_key": "TLS private key (PEM)",
    "irc_enabled": "Enable plaintext IRC", "irc_port": "IRC port", "ipv6_enabled": "Enable IPv6", "tls_enabled": "Enable TLS listener", "tls_port": "TLS port",
    "auto_start": "Start server when OpenIRC launches", "require_tls_auth": "Require TLS for network account authentication", "host_privacy": "Public host visibility", "unicode_mode": "Unicode compatibility",
    "auth_failures": "Failed login threshold", "lockout_seconds": "Login lockout (seconds)", "log_max_bytes": "Maximum log file size (bytes)", "log_retention": "Rotated log files to keep", "nick_length": "Maximum nickname length",
}
CHOICES = {
    "host_privacy": [("Cloaked", "cloak"), ("Hidden", "hidden"), ("Full host / IP", "full")],
    "unicode_mode": [("Modern UTF-8", "modern"), ("Strict historical IRCX compatibility", "historical")],
    "reservation_policy": [("Off", "off"), ("Warn", "warn"), ("Require matching account", "require")],
    "log_level": [(level, level) for level in ["DEBUG", "INFO", "WARNING", "ERROR"]],
}


class SettingsForm(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.fields: dict[str, QWidget] = {}
        self.defaults = ServerSettings().to_dict(include_secrets=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        for section, names in SECTIONS.items():
            content = QWidget()
            form = QFormLayout(content)
            form.setContentsMargins(18, 18, 18, 18)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            if section == "Listening":
                note = QLabel("Listener changes apply after the next server start. Port 0 selects an available port.")
                note.setWordWrap(True)
                form.addRow(note)
            for name in names:
                default = self.defaults[name]
                if name in CHOICES:
                    field = QComboBox()
                    for label, data in CHOICES[name]:
                        field.addItem(label, data)
                    field.currentIndexChanged.connect(self.changed)
                elif isinstance(default, bool):
                    field = QCheckBox()
                    field.toggled.connect(self.changed)
                elif isinstance(default, int):
                    field = QSpinBox()
                    field.setRange(0, 1_000_000_000)
                    if name.endswith("_port"):
                        field.setMaximum(65535)
                    field.valueChanged.connect(self.changed)
                elif isinstance(default, float):
                    field = QDoubleSpinBox()
                    field.setRange(0, 60)
                    field.valueChanged.connect(self.changed)
                else:
                    field = QLineEdit()
                    field.textEdited.connect(self.changed)
                self.fields[name] = field
                label = LABELS.get(name, name.replace("_", " ").capitalize())
                if name in RESTART_REQUIRED:
                    label += " *"
                if name in {"tls_cert", "tls_key", "log_directory"}:
                    bar = QHBoxLayout()
                    bar.addWidget(field)
                    browse = QPushButton("Browse…")
                    browse.clicked.connect(lambda checked=False, key=name: self.browse(key))
                    bar.addWidget(browse)
                    form.addRow(label, bar)
                else:
                    form.addRow(label, field)
            if section == "IRCX":
                clone = QCheckBox("Automatic channel cloning — planned")
                clone.setEnabled(False)
                form.addRow(clone)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.tabs.addTab(scroll, section)
        motd_page = QWidget()
        motd_layout = QVBoxLayout(motd_page)
        motd_layout.addWidget(QLabel("Message of the day shown after registration and with MOTD."))
        self.motd = QPlainTextEdit()
        self.motd.textChanged.connect(self.changed)
        motd_layout.addWidget(self.motd)
        self.tabs.addTab(motd_page, "MOTD")
        self.set_values(self.defaults)

    def browse(self, name: str) -> None:
        if name == "log_directory":
            path = QFileDialog.getExistingDirectory(self, "Select log directory")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select PEM file", "", "PEM files (*.pem *.crt *.key);;All files (*)")
        if path:
            self.fields[name].setText(path)
            self.changed.emit()

    def set_values(self, settings: Any) -> None:
        for name, field in self.fields.items():
            current = value(settings, name, self.defaults[name])
            field.blockSignals(True)
            if isinstance(field, QComboBox):
                field.setCurrentIndex(max(0, field.findData(current)))
            elif isinstance(field, QCheckBox):
                field.setChecked(bool(current))
            elif isinstance(field, (QSpinBox, QDoubleSpinBox)):
                field.setValue(current)
            else:
                field.setText(str(current or ""))
            field.blockSignals(False)
        self.motd.blockSignals(True)
        self.motd.setPlainText(value(settings, "motd", self.defaults["motd"]))
        self.motd.blockSignals(False)

    def values(self) -> dict:
        result = {}
        for name, field in self.fields.items():
            if isinstance(field, QComboBox):
                result[name] = field.currentData()
            elif isinstance(field, QCheckBox):
                result[name] = field.isChecked()
            elif isinstance(field, (QSpinBox, QDoubleSpinBox)):
                result[name] = field.value()
            else:
                result[name] = field.text().strip()
        result["motd"] = self.motd.toPlainText()
        ServerSettings.from_dict(result).validate()
        return result


class SettingsPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Server settings", "Changes are saved explicitly. Fields marked * require the server to restart.")
        self.controller = controller
        self.form = SettingsForm()
        self.layout.addWidget(self.form)
        self.pending = QLabel("")
        self.pending.setWordWrap(True)
        self.layout.addWidget(self.pending)
        self.buttons([("Save settings", self.save), ("Revert changes", self.revert)])
        self.dirty = False
        self.loaded = False
        self.form.changed.connect(self.mark_dirty)

    def mark_dirty(self) -> None:
        self.dirty = True

    def refresh(self, snapshot: Any) -> None:
        pending = value(value(snapshot, "statistics", {}), "pending_restart", ())
        self.pending.setText("Pending restart: " + ", ".join(name.replace("_", " ") for name in pending) if pending else "All saved settings are active.")
        if not self.loaded or not self.dirty:
            self.form.set_values(value(snapshot, "settings", {}))
            self.loaded = True

    def revert(self) -> None:
        self.form.set_values(value(self.controller.snapshot, "settings", {}))
        self.dirty = False

    def save(self) -> None:
        try:
            values = self.form.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Settings need attention", str(exc))
            return
        self.controller.command("settings.save", {"values": values}, callback=self.saved)

    def saved(self, result: Any = None) -> None:
        self.dirty = False
        restart = value(result, "restart_required", ())
        self.controller.statusBar().showMessage("Settings saved." + (" Restart required: " + ", ".join(restart) if restart else " Changes are active."), 8000)


class SetupWizard(QWizard):
    def __init__(self, parent: QWidget, *, start_server: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Welcome to OpenIRC")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(600, 440)
        first = QWizardPage()
        first.setTitle("Set up your OpenIRC server")
        first.setSubTitle("Create a local server configuration. You can change these settings later.")
        form = QFormLayout(first)
        self.name = QLineEdit("irc.openirc.local")
        self.network = QLineEdit("OpenIRC")
        self.bind = QComboBox()
        self.bind.setEditable(True)
        self.bind.addItems(["127.0.0.1", "0.0.0.0", "::1", "::"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(6667)
        for label, widget in [("Server hostname", self.name), ("Network name", self.network), ("Bind address", self.bind), ("IRC port", self.port)]:
            form.addRow(label, widget)
        self.addPage(first)
        tls_page = QWizardPage()
        tls_page.setTitle("Encrypted connections")
        tls_page.setSubTitle("Enable TLS with an existing PEM certificate and private key, or configure TLS later in Settings.")
        tls_form = QFormLayout(tls_page)
        self.tls = QCheckBox("Enable TLS listener on port 6697")
        tls_form.addRow(self.tls)
        self.cert = QLineEdit()
        self.key = QLineEdit()
        for label, field in [("Certificate", self.cert), ("Private key", self.key)]:
            row = QHBoxLayout()
            row.addWidget(field)
            button = QPushButton("Browse…")
            button.clicked.connect(lambda checked=False, edit=field: self.select_pem(edit))
            row.addWidget(button)
            tls_form.addRow(label, row)
        self.addPage(tls_page)
        admin = QWizardPage()
        admin.setTitle("Create the first administrator")
        admin.setSubTitle("The local console opens without a login. This account authenticates operator clients and the built-in chat panel.")
        form = QFormLayout(admin)
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password = QLineEdit()
        self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Account username", self.username)
        form.addRow("Password", self.password)
        form.addRow("Confirm password", self.confirm_password)
        note = QLabel("The server will start listening when setup completes." if start_server else "The server will remain stopped until you select Start Server.")
        note.setWordWrap(True)
        form.addRow(note)
        self.addPage(admin)

    def select_pem(self, field: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select PEM file", "", "PEM files (*.pem *.crt *.key);;All files (*)")
        if path:
            field.setText(path)

    def values(self) -> dict:
        bind = self.bind.currentText().strip()
        return {"server_name": self.name.text().strip(), "network_name": self.network.text().strip(), "bind_address": bind, "ipv6_enabled": ":" in bind, "irc_port": self.port.value(), "tls_enabled": self.tls.isChecked(), "tls_cert": self.cert.text().strip(), "tls_key": self.key.text().strip()}

    def validateCurrentPage(self) -> bool:
        if self.currentId() == 2:
            if not self.username.text().strip() or not self.password.text():
                QMessageBox.warning(self, "Account incomplete", "Enter an administrator username and password.")
                return False
            if self.password.text() != self.confirm_password.text():
                QMessageBox.warning(self, "Passwords differ", "The two passwords must match.")
                return False
            try:
                ServerSettings.from_dict(self.values()).validate()
            except ValueError as exc:
                QMessageBox.warning(self, "Configuration incomplete", str(exc))
                return False
        return super().validateCurrentPage()

    def payload(self) -> dict:
        result = {"values": self.values(), "username": self.username.text().strip(), "password": self.password.text()}
        self.password.clear()
        self.confirm_password.clear()
        return result
