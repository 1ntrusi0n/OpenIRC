"""UI acceptance tests, including privacy and server-thread lifecycle."""
from __future__ import annotations

import os
from pathlib import Path
import socket

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox

from OpenIRC.config.settings import ServerSettings
from OpenIRC.gui.dialogs import AccountEditor, ChannelEditor, OperatorEditor
from OpenIRC.gui.main_window import MainWindow
from OpenIRC.gui.settings_page import SetupWizard
from OpenIRC.gui.widgets import DataTable
from OpenIRC.models.domain import ROLE_PERMISSIONS, ServerSnapshot


class FakeBridge(QObject):
    ready = pyqtSignal()
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    closed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.requests = []
        self.sequence = 0

    def request(self, action, payload=None):
        self.sequence += 1
        self.requests.append((self.sequence, action, payload))
        return self.sequence

    def start(self):
        self.ready.emit()

    def close(self):
        self.closed.emit()


def snapshot(**changes):
    base = {"status": "stopped", "settings": ServerSettings(setup_complete=True).to_dict(include_secrets=True), "statistics": {"uptime": 0}}
    base.update(changes)
    return ServerSnapshot(**base)


@pytest.fixture
def console(qtbot, tmp_path):
    bridge = FakeBridge()
    window = MainWindow(tmp_path, bridge=bridge, start_bridge=False)
    window._initialized = True
    window._setup_shown = True
    window.receive_snapshot(snapshot())
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "_shutdown_complete", True))
    yield window, bridge
    window.timer.stop()
    window.pages["Chat"].timer.stop()
    window._shutdown_complete = True
    window.close()


def test_console_stopped_management_and_live_actions(console):
    window, bridge = console
    assert len(window.pages) == 10
    assert window.actions["start"].isEnabled()
    assert not window.actions["stop"].isEnabled()
    assert window.pages["Settings"].isEnabled()
    window.actions["start"].trigger()
    assert bridge.requests[-1][1] == "start"
    window.receive_snapshot(snapshot(status="running"))
    assert window.actions["stop"].isEnabled()
    assert not window.actions["start"].isEnabled()


def test_historical_chat_uses_decoded_identity_and_room(console):
    window, bridge = console
    settings = ServerSettings(setup_complete=True, unicode_mode="historical").to_dict()
    window.receive_snapshot(snapshot(status="running", settings=settings))
    chat = window.pages["Chat"]
    chat.session_id = "test-session"
    chat.nickname = "Éric"
    chat.handle_line(":%Éric!user@local JOIN %#café")
    assert chat.tabs.tabText(chat.tabs.currentIndex()) == "#café"
    chat.handle_line(":server 353 %Éric = %#café :.%Éric @Helper")
    chat.handle_line(":server 366 %Éric %#café :End")
    room = chat.room("#café")
    assert room.member_names["éric"] == ".Éric"
    chat.handle_line(":%Éric!user@local NICK :%René")
    assert chat.nickname == "René"


def test_gui_debug_flag_reaches_server_bridge(qtbot, tmp_path):
    window = MainWindow(tmp_path, start_bridge=False, debug=True)
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "_shutdown_complete", True))
    assert window.bridge.debug is True
    window.timer.stop()
    window.pages["Chat"].timer.stop()
    window._shutdown_complete = True


@pytest.mark.parametrize("requested,saved,expected", [(False, False, 0), (True, False, 1), (False, True, 1), (True, True, 1)])
def test_start_on_launch_runs_once_without_changing_settings(console, requested, saved, expected):
    window, bridge = console
    window._setup_shown = False
    window._start_server_on_launch = requested
    settings = ServerSettings(setup_complete=True, auto_start=saved).to_dict()
    window.receive_snapshot(snapshot(settings=settings))
    window.receive_snapshot(snapshot(settings=settings))
    assert [request[1] for request in bridge.requests] == ["start"] * expected
    assert window.snapshot.settings["auto_start"] is saved


@pytest.mark.parametrize("requested,outcome", [(True, "success"), (True, "failure"), (True, "cancel"), (False, "success")])
def test_first_run_starts_only_after_successful_setup(console, monkeypatch, requested, outcome):
    window, bridge = console
    window._start_server_on_launch = requested
    monkeypatch.setattr(SetupWizard, "exec", lambda self: outcome != "cancel")
    monkeypatch.setattr(SetupWizard, "payload", lambda self: {"username": "admin", "password": "test-password"})
    failures = []
    monkeypatch.setattr(window, "setup_failed", failures.append)
    window.first_run()
    assert not any(request[1] == "start" for request in bridge.requests)
    if outcome == "cancel":
        assert not bridge.requests
        return
    token, action, _ = bridge.requests[-1]
    assert action == "setup"
    if outcome == "failure":
        bridge.failed.emit(token, "Setup could not be saved")
        assert failures == ["Setup could not be saved"]
    else:
        bridge.completed.emit(token, {})
    assert sum(request[1] == "start" for request in bridge.requests) == int(requested and outcome == "success")


@pytest.mark.parametrize("requested", [False, True])
def test_cli_passes_start_server_to_gui(monkeypatch, tmp_path, requested):
    from OpenIRC.app import main
    launches = []
    monkeypatch.setattr("OpenIRC.gui.main_window.run_gui", lambda path, debug, **options: launches.append((path, debug, options)) or 0)
    args = ["--data-dir", str(tmp_path), "--debug"] + (["--start-server"] if requested else [])
    assert main(args) == 0
    assert launches == [(tmp_path, True, {"start_server": requested})]


def test_sortable_table_keeps_identity_and_clears_stale_selection(qtbot):
    table = DataTable([("Nickname", "nick"), ("Bytes", "bytes")])
    qtbot.addWidget(table)
    table.set_records([{"id": "a", "nick": "A", "bytes": 200}, {"id": "b", "nick": "B", "bytes": 30}])
    table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert table.item(0, 0).text() == "B"
    table.selectRow(0)
    table.set_records([{"id": "b", "nick": "Renamed", "bytes": 500}, {"id": "a", "nick": "A", "bytes": 200}])
    assert table.selected()["id"] == "b"
    table.set_records([{"id": "a", "nick": "A", "bytes": 200}])
    assert table.selected() is None


def test_secret_editors_never_echo_existing_keys(console):
    window, _ = console
    channel = {"id": "c1", "name": "#room", "modes": "ntk", "registered": True, "secrets_set": ("MEMBERKEY",), "properties": {"ONJOIN": "Welcome"}}
    editor = ChannelEditor(window, channel)
    assert all(not field.text() for field in editor.keys.values())
    assert editor.payload()["secrets"] == {}
    assert "k" in editor.payload()["modes"]
    assert "ACCOUNT" not in editor.payload()["properties"]
    editor.keys["OWNERKEY"].setText("new key")
    editor.reset_keys["MEMBERKEY"].setChecked(True)
    assert editor.payload()["secrets"] == {"MEMBERKEY": "", "OWNERKEY": "new key"}
    assert not editor.keys["OWNERKEY"].text()
    clone = next(check for check in editor.findChildren(QCheckBox) if "cloning" in check.text())
    assert not clone.isEnabled()


def test_account_password_replacement_and_operator_templates(console):
    window, _ = console
    editor = AccountEditor(window, {"id": "a", "username": "alice", "display_name": "Alice"})
    assert "password" not in editor.payload()
    editor.password.setText("replacement-secret")
    assert editor.payload()["password"] == "replacement-secret"
    assert editor.password.text() == ""
    operator = OperatorEditor(window, [{"id": "a", "username": "alice"}])
    operator.role.setCurrentIndex(operator.role.findData("moderator"))
    assert set(operator.payload()["permissions"]) == set(ROLE_PERMISSIONS["moderator"])
    assert "manage_accounts" not in operator.payload()["permissions"]


def test_chat_requires_explicit_credentials_and_does_not_read_admin_members(console):
    window, bridge = console
    chat = window.pages["Chat"]
    window.receive_snapshot(snapshot(status="running", channels=({"id": "secret", "name": "#secret", "members": ({"nick": "HiddenPeer"},)},)))
    assert set(chat.rooms) == {"Server"}
    chat.username.setText("operator")
    chat.password.setText("never-save")
    chat.nick.setText("IndependentNickname")
    chat.connect_chat()
    request = bridge.requests[-1]
    assert request[1] == "chat.connect"
    assert request[2]["nickname"] == "IndependentNickname"
    assert chat.password.text() == ""
    bridge.completed.emit(request[0], {"id": "local-session"})
    assert chat.session_id == "local-session"
    chat.handle_line(":IndependentNickname!user@local JOIN :#auditorium")
    chat.handle_line(":server 353 IndependentNickname = #auditorium :@Host IndependentNickname")
    chat.handle_line(":server 366 IndependentNickname #auditorium :End")
    room = chat.rooms["#auditorium"]
    assert set(room.member_names) == {"host", "independentnickname"}
    assert "HiddenPeer" not in room.transcript.toPlainText()
    chat.handle_line(":Host!u@h PRIVMSG #auditorium :Welcome")
    assert "<Host> Welcome" in room.transcript.toPlainText()
    window.receive_snapshot(snapshot())
    assert chat.session_id is None
    assert not chat.send_button.isEnabled()


def test_chat_nickname_role_and_kill_updates(console):
    window, _ = console
    chat = window.pages["Chat"]
    chat.nickname = "Me"
    chat.session_id = "session"
    chat.handle_line(":server 353 Me = #room :@Owner +Me")
    chat.handle_line(":server 366 Me #room :End")
    chat.handle_line(":Me!u@h NICK :Renamed")
    assert chat.nickname == "Renamed"
    assert chat.rooms["#room"].member_names["renamed"] == "+Renamed"
    chat.handle_line("ERROR :Killed by administrator")
    assert chat.session_id is None
    assert not chat.rooms["#room"].member_names


def test_settings_edits_survive_polling_and_show_restart(console):
    window, _ = console
    page = window.pages["Settings"]
    page.form.fields["network_name"].setText("EditedNetwork")
    page.mark_dirty()
    window.receive_snapshot(snapshot(statistics={"pending_restart": ("irc_port",)}))
    assert page.form.fields["network_name"].text() == "EditedNetwork"
    assert "irc port" in page.pending.text()
    page.revert()
    assert page.form.fields["network_name"].text() == "OpenIRC"


def test_first_run_payload_and_password_cleanup(console):
    window, _ = console
    wizard = SetupWizard(window)
    wizard.username.setText("admin")
    wizard.password.setText("secret")
    wizard.confirm_password.setText("secret")
    wizard.bind.setCurrentText("::1")
    payload = wizard.payload()
    assert payload["values"]["ipv6_enabled"] is True
    assert payload["username"] == "admin"
    assert not wizard.password.text()
    assert not wizard.confirm_password.text()


def test_logs_filter_and_clear_do_not_mutate_snapshot(console):
    window, _ = console
    import time
    entries = ({"time": time.time() - 1, "level": "INFO", "category": "server", "message": "Started"}, {"time": time.time() - 1, "level": "ERROR", "category": "tls", "message": "Bad certificate"})
    window.receive_snapshot(snapshot(logs=entries))
    logs = window.pages["Logs / Audit"]
    logs.severity.setCurrentText("ERROR")
    assert logs.table.rowCount() == 1
    assert "Bad certificate" in logs.table.visible_text()
    logs.clear()
    assert logs.table.rowCount() == 0
    assert len(window.snapshot.logs) == 2


def test_real_bridge_stopped_crud_chat_and_restart(qtbot, tmp_path, monkeypatch):
    """Real Qt events, SQLite worker, asyncio listeners and local chat cooperate."""
    from PyQt6.QtWidgets import QMessageBox
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, message: errors.append(message))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window = MainWindow(tmp_path)
    window._setup_shown = True
    sockets = []

    def call(action, payload=None):
        results = []
        failures = []
        window.command(action, payload, callback=lambda result: results.append(result), failure=failures.append)
        qtbot.waitUntil(lambda: bool(results or failures), timeout=15000)
        assert not failures, failures
        return results[0]

    try:
        qtbot.waitUntil(lambda: window._initialized and window.snapshot is not None, timeout=15000)
        call("setup", {"username": "admin", "password": "integration-secret", "values": {"irc_port": 0, "confirm_shutdown": False}})
        editor = ChannelEditor(window)
        editor.name.setText("#gui")
        editor.topic.setText("Managed from the GUI")
        editor.properties["ONJOIN"].setPlainText("Welcome\nStay a while")
        channel_id = call("channel.save", editor.payload())["id"]
        call("start")
        qtbot.waitUntil(lambda: window.snapshot.status == "Running", timeout=10000)
        # Only the test inspects the bound ephemeral port. Widgets use snapshots.
        port = window.bridge.server.bound_addresses[0][1]
        for nick in ["First", "Second"]:
            client = socket.create_connection(("127.0.0.1", port), timeout=3)
            client.settimeout(3)
            client.sendall(f"NICK {nick}\r\nUSER user 0 * :GUI test\r\nJOIN #gui\r\n".encode())
            sockets.append(client)
        qtbot.waitUntil(lambda: len(window.snapshot.connections) == 2, timeout=10000)
        assert window.pages["Connections"].table.rowCount() == 2
        chat = window.pages["Chat"]
        chat.username.setText("admin")
        chat.password.setText("integration-secret")
        chat.nick.setText("ConsoleChat")
        chat.connect_chat()
        qtbot.waitUntil(lambda: chat.session_id is not None, timeout=10000)
        chat.join_channel("#gui")
        qtbot.waitUntil(lambda: "#gui" in chat.rooms and "first" in chat.rooms["#gui"].member_names, timeout=10000)
        sockets[0].sendall(b"PRIVMSG #gui :A live network message\r\n")
        qtbot.waitUntil(lambda: "A live network message" in chat.rooms["#gui"].transcript.toPlainText(), timeout=10000)
        channel = next(record for record in window.snapshot.channels if record["id"] == channel_id)
        members = ChannelEditor(window, channel, window.snapshot.accounts, window)
        for row in range(members.members.rowCount()):
            if members.members.item(row, 0).text() == "Second":
                members.members.selectRow(row)
                break
        members.role.setCurrentText("Host")
        members.set_role()
        qtbot.waitUntil(lambda: any(member["nick"] == "Second" and member["role"] == "Host" for record in window.snapshot.channels for member in record["members"]), timeout=10000)
        members.kick()
        qtbot.waitUntil(lambda: all(member["nick"] != "Second" for record in window.snapshot.channels for member in record["members"]), timeout=10000)
        connections = window.pages["Connections"]
        for row in range(connections.table.rowCount()):
            if connections.table.item(row, 0).text() == "Second":
                connections.table.selectRow(row)
                break
        connections.kill()
        qtbot.waitUntil(lambda: all(record["nick"] != "Second" for record in window.snapshot.connections), timeout=10000)
        call("stop")
        qtbot.waitUntil(lambda: window.snapshot.status == "Stopped" and chat.session_id is None, timeout=10000)
        assert window.bridge._thread.is_alive()
        call("channel.topic", {"id": channel_id, "topic": "Edited while stopped"})
        call("start")
        qtbot.waitUntil(lambda: window.snapshot.status == "Running", timeout=10000)
        call("stop")
        assert not errors
    finally:
        for client in sockets:
            client.close()
        window.timer.stop()
        window.pages["Chat"].timer.stop()
        window._closing = True
        window.bridge.close()
        qtbot.waitUntil(lambda: not window.bridge._thread.is_alive(), timeout=15000)
        window._shutdown_complete = True
        window.close()
