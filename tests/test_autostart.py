import plistlib
import subprocess
from pathlib import Path

import pytest

from codingmix import autostart, paths

EXE = Path("/opt/tools/codingmix-service")


class FakeRunner:
    def __init__(self, fail_prefixes=()):
        self.calls = []
        self.fail_prefixes = [tuple(p) for p in fail_prefixes]

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        failed = any(tuple(args[:len(p)]) == p for p in self.fail_prefixes)
        return subprocess.CompletedProcess(args, 1 if failed else 0, "", "denied" if failed else "")


def test_windows_install_registers_task():
    runner = FakeRunner()
    text = autostart.install(EXE, platform="win32", runner=runner, user_id="PC\\me")
    create = runner.calls[0]
    assert create[:4] == ["schtasks", "/Create", "/TN", "CodingMix"]
    xml_path = Path(create[create.index("/XML") + 1])
    xml = xml_path.read_text(encoding="utf-16")
    assert str(EXE) in xml and "LogonTrigger" in xml and "RestartOnFailure" in xml
    assert "PC\\me" in xml
    assert xml_path.parent == paths.data_dir()
    assert runner.calls[1] == ["schtasks", "/Run", "/TN", "CodingMix"]
    assert "Task Scheduler" in text


def test_windows_falls_back_to_run_key():
    runner = FakeRunner(fail_prefixes=[["schtasks", "/Create"]])
    text = autostart.install(EXE, platform="win32", runner=runner, user_id="PC\\me")
    reg = next(c for c in runner.calls if c[0] == "reg")
    assert reg[:2] == ["reg", "add"] and f'"{EXE}"' in reg
    assert "Run key" in text


def test_windows_xml_escapes_special_characters():
    xml = autostart.render_windows_task(Path("C:/A&B/codingmix-service.exe"), "PC\\me")
    assert "A&amp;B" in xml


def test_macos_install_writes_plist_and_bootstraps(tmp_path):
    runner = FakeRunner()
    autostart.install(EXE, platform="darwin", runner=runner, home=tmp_path, uid=501)
    plist_path = tmp_path / "Library" / "LaunchAgents" / "io.github.caesla.codingmix.plist"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["ProgramArguments"] == [str(EXE)]
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert runner.calls[-1] == ["launchctl", "bootstrap", "gui/501", str(plist_path)]


def test_linux_install_writes_unit_and_enables(tmp_path):
    runner = FakeRunner()
    autostart.install(EXE, platform="linux", runner=runner, home=tmp_path)
    unit = (tmp_path / ".config" / "systemd" / "user" / "codingmix.service").read_text()
    assert f'ExecStart="{EXE}"' in unit and "Restart=on-failure" in unit
    assert ["systemctl", "--user", "enable", "--now", "codingmix.service"] in runner.calls


def test_linux_failure_raises(tmp_path):
    runner = FakeRunner(fail_prefixes=[["systemctl", "--user", "enable"]])
    with pytest.raises(autostart.AutostartError):
        autostart.install(EXE, platform="linux", runner=runner, home=tmp_path)


def test_uninstall_linux_removes_unit(tmp_path):
    autostart.install(EXE, platform="linux", runner=FakeRunner(), home=tmp_path)
    runner = FakeRunner()
    autostart.uninstall(platform="linux", runner=runner, home=tmp_path)
    assert not (tmp_path / ".config" / "systemd" / "user" / "codingmix.service").exists()
    assert ["systemctl", "--user", "disable", "--now", "codingmix.service"] in runner.calls


def test_uninstall_windows_removes_task_and_run_key():
    runner = FakeRunner(fail_prefixes=[["reg"]])
    autostart.uninstall(platform="win32", runner=runner)
    assert ["schtasks", "/Delete", "/TN", "CodingMix", "/F"] in runner.calls
    assert any(c[:2] == ["reg", "delete"] for c in runner.calls)


def test_unsupported_platform_raises():
    with pytest.raises(autostart.AutostartError, match="unsupported"):
        autostart.install(EXE, platform="plan9", runner=FakeRunner())
