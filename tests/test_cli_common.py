"""cli_common holds the options, defaults and error paths every command shares."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from vibeview import cli_common
from vibeview.cli import main


class TestSizeType:
    @pytest.mark.parametrize("text", ["900x600", "900X600", " 900x600 "])
    def test_parses_width_by_height(self, text: str) -> None:
        assert cli_common.SIZE.convert(text, None, None) == (900, 600)

    @pytest.mark.parametrize("text", ["nope", "900", "900x", "0x600", "-1x5"])
    def test_rejects_malformed_sizes(self, text: str) -> None:
        with pytest.raises(click.BadParameter, match="invalid --size"):
            cli_common.SIZE.convert(text, None, None)

    def test_option_factory_reports_through_click(self) -> None:
        @click.command()
        @cli_common.size_option()
        def cmd(size: cli_common.Size) -> None:
            click.echo(f"{size[0]}:{size[1]}")

        runner = CliRunner()
        assert runner.invoke(cmd, []).output.strip() == "900:600"
        assert runner.invoke(cmd, ["--size", "12x34"]).output.strip() == "12:34"
        bad = runner.invoke(cmd, ["--size", "wide"])
        assert bad.exit_code != 0 and "invalid --size" in bad.output


class TestResolveServerOptions:
    def _command(self):
        @click.command()
        @cli_common.port_option()
        @cli_common.host_option()
        @cli_common.no_browser_option()
        @click.pass_context
        def cmd(ctx: click.Context, port: int, host: str, no_browser: bool) -> None:
            port, host, no_browser = cli_common.resolve_server_options(ctx, port, host, no_browser)
            click.echo(f"{port} {host} {no_browser}")

        return cmd

    def test_config_supplies_defaults_only(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        (tmp_path / "vibe-view").mkdir()
        (tmp_path / "vibe-view" / "config.toml").write_text(
            '[server]\nport = 9001\nhost = "0.0.0.0"\nopen_browser = false\n'
        )
        runner = CliRunner()
        assert runner.invoke(self._command(), []).output.strip() == "9001 0.0.0.0 True"
        assert (
            runner.invoke(self._command(), ["--port", "7000", "--host", "::1"]).output.strip()
            == "7000 ::1 True"
        )

    def test_programmatic_values_are_kept(self, monkeypatch, tmp_path: Path) -> None:
        """A caller that already resolved its options is not second-guessed."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        (tmp_path / "vibe-view").mkdir()
        (tmp_path / "vibe-view" / "config.toml").write_text("[server]\nport = 9001\n")
        inner = self._command()

        @click.command()
        @click.pass_context
        def outer(ctx: click.Context) -> None:
            ctx.invoke(inner, port=8080, host="127.0.0.1", no_browser=False)

        assert CliRunner().invoke(outer, []).output.strip() == "8080 127.0.0.1 False"


class TestOpenReaderOrExit:
    def test_returns_a_reader(self, water_qvf: Path) -> None:
        reader = cli_common.open_reader_or_exit(water_qvf)
        try:
            assert reader.sections
        finally:
            reader.close()

    def test_exits_with_the_reason(self, tmp_path: Path, capsys) -> None:
        broken = tmp_path / "broken.qvf"
        broken.write_bytes(b"not a zip")
        with pytest.raises(SystemExit) as raised:
            cli_common.open_reader_or_exit(broken, prefix="table: ")
        assert raised.value.code == 1
        assert capsys.readouterr().err.startswith("table: Error: ")

    def test_json_errors_go_to_stdout(self, tmp_path: Path, capsys) -> None:
        broken = tmp_path / "broken.qvf"
        broken.write_bytes(b"not a zip")
        with pytest.raises(SystemExit):
            cli_common.open_reader_or_exit(broken, json_errors=True)
        out = capsys.readouterr().out
        assert out.startswith('{"error": ')


class TestParseCsvList:
    def test_casts_and_strips(self) -> None:
        assert cli_common.parse_csv_list("1, 2,3", cast=int, option="--replicate") == [1, 2, 3]
        assert cli_common.parse_csv_list("a, b", cast=str, option="--keep") == ["a", "b"]

    def test_usage_errors_name_the_option(self) -> None:
        with pytest.raises(click.UsageError, match="--replicate expects 3"):
            cli_common.parse_csv_list("1,2", cast=int, option="--replicate", expected=3)
        with pytest.raises(click.UsageError, match="--replicate expects comma-separated"):
            cli_common.parse_csv_list("1,x", cast=int, option="--replicate")


class TestDefaultOutput:
    def test_beside_the_input_by_default(self) -> None:
        src = Path("/data/runs/water.qvf")
        assert cli_common.default_output(src, ".png", tag="structure") == Path(
            "/data/runs/water_structure.png"
        )
        assert cli_common.default_output(src, ".xyz") == Path("/data/runs/water.xyz")
        assert cli_common.default_output(src, "", tag="frames") == Path("/data/runs/water_frames")

    def test_working_directory_for_aggregates(self) -> None:
        src = Path("/data/runs/water.qvf")
        assert cli_common.default_output(src, ".qvf", tag="merged", beside_input=False) == Path(
            "water_merged.qvf"
        )

    def test_help_strings_describe_the_convention(self) -> None:
        assert cli_common.describe_default(".png", tag="<section>") == (
            "<stem>_<section>.png beside the input"
        )


def test_commands_share_the_declarations() -> None:
    """Every server command takes the same --port/--host, every image command the same --size."""
    ports = {}
    sizes = {}
    for name, command in main.commands.items():
        for param in command.params:
            if param.name == "port":
                ports[name] = param.help
            if param.name == "size":
                sizes[name] = type(param.type).__name__
    assert set(ports) >= {"open", "compare", "serve", "from-vq", "demo", "quickstart", "desktop"}
    assert set(sizes) >= {"batch", "capture", "capture-selftest", "animate"}
    assert all(kind == "SizeType" for name, kind in sizes.items() if name != "show")


def test_capture_and_export_default_beside_the_input(water_qvf: Path, tmp_path: Path) -> None:
    import shutil

    source = tmp_path / "nested" / "water.qvf"
    source.parent.mkdir()
    shutil.copy(water_qvf, source)
    result = CliRunner().invoke(main, ["export", str(source), "-f", "xyz"])
    assert result.exit_code == 0, result.output
    assert (source.parent / "water.xyz").exists()
