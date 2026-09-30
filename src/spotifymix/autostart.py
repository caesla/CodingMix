"""Start the service at login: Task Scheduler, launchd or systemd, per platform."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from xml.sax.saxutils import escape

from spotifymix import paths

TASK_NAME = "SpotifyMix"
LAUNCHD_LABEL = "io.github.caesla.spotifymix"
SYSTEMD_UNIT = "spotifymix.service"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class AutostartError(Exception):
    pass


def default_runner(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


def service_executable() -> Path:
    found = shutil.which("spotifymix-service")
    if not found:
        raise AutostartError(
            "spotifymix-service was not found on PATH; install SpotifyMix with `uv tool install`"
        )
    return Path(found)


def render_windows_task(exe: Path, user_id: str) -> str:
    user = escape(user_id)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>SpotifyMix background service</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(exe))}</Command></Exec>
  </Actions>
</Task>
"""


def render_launchd_plist(exe: Path, log_dir: Path) -> str:
    data = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [str(exe)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_dir / "launchd.out.log"),
        "StandardErrorPath": str(log_dir / "launchd.err.log"),
    }
    return plistlib.dumps(data).decode("utf-8")


def render_systemd_unit(exe: Path) -> str:
    return f"""[Unit]
Description=SpotifyMix background service
After=network-online.target

[Service]
ExecStart="{exe}"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def _windows_user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _check(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        raise AutostartError(f"{what} failed: {(result.stderr or result.stdout).strip()}")


def install(
    exe: Path,
    *,
    platform: str = sys.platform,
    runner: Runner = default_runner,
    home: Path | None = None,
    uid: int | None = None,
    user_id: str | None = None,
) -> str:
    home = home or Path.home()
    if platform.startswith("win"):
        xml_path = paths.data_dir() / "spotifymix-task.xml"
        xml_path.write_text(render_windows_task(exe, user_id or _windows_user()), encoding="utf-16")
        created = runner(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
        if created.returncode == 0:
            runner(["schtasks", "/Run", "/TN", TASK_NAME])
            return "Windows Task Scheduler task 'SpotifyMix' (starts at logon, restarts on failure)"
        added = runner(["reg", "add", RUN_KEY, "/v", TASK_NAME, "/t", "REG_SZ",
                        "/d", f'"{exe}"', "/f"])
        _check(added, "registering the Run key")
        runner(["cmd", "/c", "start", "", str(exe)])
        return "Windows Run key 'SpotifyMix' (starts at logon; no automatic restart)"
    if platform == "darwin":
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / f"{LAUNCHD_LABEL}.plist"
        plist.write_text(render_launchd_plist(exe, paths.log_dir()), encoding="utf-8")
        domain = f"gui/{uid if uid is not None else os.getuid()}"
        runner(["launchctl", "bootout", domain, str(plist)])  # fine if it was not loaded
        _check(runner(["launchctl", "bootstrap", domain, str(plist)]), "launchctl bootstrap")
        return f"launchd agent {LAUNCHD_LABEL}"
    if platform.startswith("linux"):
        unit_dir = home / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / SYSTEMD_UNIT).write_text(render_systemd_unit(exe), encoding="utf-8")
        _check(runner(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _check(runner(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT]),
               "systemctl enable")
        return f"systemd user service {SYSTEMD_UNIT}"
    raise AutostartError(f"unsupported platform: {platform}")


def uninstall(
    *,
    platform: str = sys.platform,
    runner: Runner = default_runner,
    home: Path | None = None,
    uid: int | None = None,
) -> str:
    home = home or Path.home()
    if platform.startswith("win"):
        runner(["schtasks", "/End", "/TN", TASK_NAME])
        runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        runner(["reg", "delete", RUN_KEY, "/v", TASK_NAME, "/f"])
        return "Removed the SpotifyMix scheduled task and Run key (if present)"
    if platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        domain = f"gui/{uid if uid is not None else os.getuid()}"
        runner(["launchctl", "bootout", domain, str(plist)])
        plist.unlink(missing_ok=True)
        return f"Removed launchd agent {LAUNCHD_LABEL}"
    if platform.startswith("linux"):
        runner(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT])
        (home / ".config" / "systemd" / "user" / SYSTEMD_UNIT).unlink(missing_ok=True)
        runner(["systemctl", "--user", "daemon-reload"])
        return f"Removed systemd user service {SYSTEMD_UNIT}"
    raise AutostartError(f"unsupported platform: {platform}")
