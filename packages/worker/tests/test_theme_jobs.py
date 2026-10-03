"""Скрипт картинки слайда через PowerPoint. Самого PowerPoint на CI нет, поэтому на Windows
проверяются синтаксис скрипта (парсером PowerShell) и повтор вызовов, отклонённых занятым
PowerPoint."""

import os
import shutil
import subprocess
import sys

import pytest

from autogenerator.worker.theme_jobs import _POWERSHELL, _RETRY

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None, reason="PowerShell есть только на Windows"
)


def powershell(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_image_script_parses():
    check = (
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseInput("
        "$env:AGEN_SCRIPT, [ref]$null, [ref]$e); if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }"
    )
    res = powershell(check, AGEN_SCRIPT=_POWERSHELL)
    assert res.returncode == 0, res.stdout + res.stderr


def test_busy_calls_are_retried_others_are_not():
    script = (
        _RETRY
        + r"""
$ErrorActionPreference = 'Stop'
function Busy { [System.Runtime.InteropServices.COMException]::new('занят', -2147418111) }
$script:n = 0
Invoke-Retry { $script:n++; if ($n -lt 3) { throw (Busy) } }
if ($n -ne 3) { exit 2 }
# Отказ, завёрнутый в исключение вызова метода, как его выдаёт PowerShell для объектов COM.
$script:k = 0
Invoke-Retry {
    $script:k++
    if ($k -lt 2) { throw [System.Management.Automation.MethodInvocationException]::new('Export', (Busy)) }
}
if ($k -ne 2) { exit 3 }
$script:m = 0
try { Invoke-Retry { $script:m++; throw [System.Runtime.InteropServices.COMException]::new('другое', -2147467259) } }
catch { }
if ($m -ne 1) { exit 4 }
exit 0
"""
    )
    res = powershell(script)
    assert res.returncode == 0, res.stdout + res.stderr
