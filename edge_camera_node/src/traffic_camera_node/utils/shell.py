from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def run_command(command: list[str], timeout: int = 10) -> CommandResult:
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def request_safe_shutdown() -> CommandResult:
    candidates = [
        ["/sbin/shutdown", "-h", "now"],
        ["shutdown", "-h", "now"],
    ]
    last: CommandResult | None = None
    for cmd in candidates:
        try:
            result = run_command(cmd, timeout=5)
        except (FileNotFoundError, PermissionError):
            continue
        last = result
        if result.ok:
            return result
    return last or CommandResult(
        returncode=1,
        stdout="",
        stderr="Unable to run shutdown command.",
    )
