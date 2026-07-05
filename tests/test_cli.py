"""CLI surface: help lists every command and bare invocation doesn't error."""

from goldilocks.cli import main


def test_help_lists_all_commands(capsys):
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    for command in ("strategies", "backtest", "run", "stop", "status", "help"):
        assert command in out
    assert "KILL_SWITCH" in out  # the emergency halt must be discoverable


def test_bare_invocation_prints_help(capsys):
    assert main([]) == 0
    assert "commands:" in capsys.readouterr().out
