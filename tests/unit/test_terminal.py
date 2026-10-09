import os
import sys

import pytest
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.defaults import create_output

import pcd_cli.terminals.unix as unix_terminal
from pcd_cli.terminal import terminal_output


def test_unix_output_uses_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = DummyOutput()
    seen_streams: list[object] = []
    monkeypatch.setattr("pcd_cli.environment.sys.platform", "linux")

    def fake_output(*, stdout: object) -> DummyOutput:
        seen_streams.append(stdout)
        return expected

    monkeypatch.setattr(unix_terminal, "create_output", fake_output)

    with terminal_output() as output:
        assert output is expected

    assert seen_streams == [sys.stderr]


def test_unsupported_platform_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pcd_cli.environment.sys.platform", "freebsd")

    with (
        pytest.raises(RuntimeError, match="Unsupported platform: freebsd"),
        terminal_output(),
    ):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console behavior test")
def test_windows_output_uses_console_and_restores_redirected_handle() -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError

    stdout_handle = wintypes.DWORD(-11)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL
    read_fd, write_fd = os.pipe()
    previous = kernel32.GetStdHandle(stdout_handle)
    redirected = wintypes.HANDLE(msvcrt.get_osfhandle(write_fd))
    try:
        assert kernel32.SetStdHandle(stdout_handle, redirected)

        with pytest.raises(NoConsoleScreenBufferError):
            create_output(stdout=sys.stderr)

        with terminal_output() as output:
            assert output.get_size().rows > 0
            assert kernel32.GetStdHandle(stdout_handle) == redirected.value

        assert kernel32.GetStdHandle(stdout_handle) == redirected.value
    finally:
        kernel32.SetStdHandle(stdout_handle, previous)
        os.close(read_fd)
        os.close(write_fd)
