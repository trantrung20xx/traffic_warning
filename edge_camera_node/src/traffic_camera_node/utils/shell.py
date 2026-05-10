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
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timed_out = f"Command timed out after {timeout}s: {' '.join(command)}"
        merged_stderr = f"{stderr.strip()} {timed_out}".strip()
        return CommandResult(returncode=124, stdout=stdout.strip(), stderr=merged_stderr)


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
