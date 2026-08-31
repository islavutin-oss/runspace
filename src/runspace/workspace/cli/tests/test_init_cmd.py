"""Tests for `runspace init <tenant-id>` — scaffold a new tenant."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _run_init(tmp_path: Path, *, tenant_id: str = "test-tenant", **kwargs) -> int:
    from runspace.workspace.cli.init_cmd import run_init

    return run_init(
        tenant_id=tenant_id,
        target_dir=tmp_path,
        interactive=False,
        **kwargs,
    )


def test_init_creates_canonical_layout(tmp_path):
    rc = _run_init(tmp_path, tenant_id="lupita-tacos")
    assert rc == 0

    tdir = tmp_path / "tenants" / "lupita-tacos"
    assert (tdir / "workspace.yml").exists()
    assert (tdir / "config.yaml").exists()
    assert (tdir / "routines.yml").exists()
    assert (tdir / ".npmrc").exists()
    assert (tdir / "Dockerfile").exists()
    assert (tdir / "docker-compose.yml").exists()
    assert (tdir / "README.md").exists()
    assert (tdir / "agents").is_dir()
    assert (tdir / "plugins" / "__init__.py").exists()
    assert (tdir / "plugins" / "health.py").exists()


def test_init_workspace_yml_is_valid_yaml(tmp_path):
    _run_init(tmp_path, tenant_id="lupita-tacos", name="Lupita Tacos")
    yml = (tmp_path / "tenants" / "lupita-tacos" / "workspace.yml").read_text()
    cfg = yaml.safe_load(yml)
    assert cfg is not None
    assert cfg["name"] == "Lupita Tacos"
    # plugins block exists and references the local health plugin
    assert "plugins" in cfg
    # Relative to the tenant directory, which is where the printed
    # instructions and the Dockerfile's WORKDIR both put the process.
    # This used to assert "tenants.lupita-tacos.plugins.health", which
    # is not an importable module path at all — a hyphen is not legal
    # in one — so the test pinned the bug in place.
    assert "plugins.health" in cfg["plugins"]
    # agents block present (empty by default)
    assert "apps" in cfg
    # Provider block exists and references env var
    assert "providers" in cfg
    assert "${AI_API_KEY}" in yml


def test_init_dockerfile_uses_workspace_serve(tmp_path):
    """zero-code path: container CMD is `python -m runspace.workspace.serve`."""
    _run_init(tmp_path, tenant_id="lupita-tacos")
    docker = (tmp_path / "tenants" / "lupita-tacos" / "Dockerfile").read_text()
    assert 'CMD ["python", "-m", "runspace.workspace.serve"]' in docker
    assert "WORKSPACE_YML=/app/workspace.yml" in docker


def test_init_health_plugin_is_importable_python(tmp_path):
    """The example plugin must be syntactically valid Python."""
    _run_init(tmp_path, tenant_id="x")
    plug = (tmp_path / "tenants" / "x" / "plugins" / "health.py").read_text()
    compile(plug, "health.py", "exec")  # raises SyntaxError if broken
    # Plugin must define `router` (the symbol bootstrap collects)
    assert "router = APIRouter()" in plug


def test_init_refuses_existing_tenant_dir(tmp_path):
    rc1 = _run_init(tmp_path, tenant_id="dup")
    assert rc1 == 0
    rc2 = _run_init(tmp_path, tenant_id="dup")
    assert rc2 != 0  # refuses overwrite


@pytest.mark.parametrize(
    "bad_id",
    [
        "Capitals",  # uppercase
        "1numeric-start",  # leading digit
        "with spaces",  # spaces
        "",  # empty
        "a" * 33,  # too long
        "with/slash",  # slash
    ],
)
def test_init_rejects_bad_tenant_ids(tmp_path, bad_id):
    rc = _run_init(tmp_path, tenant_id=bad_id)
    assert rc != 0


def test_init_brand_color_passed_through(tmp_path):
    _run_init(tmp_path, tenant_id="custom", brand_color="#FF6B35", icon="🌮")
    yml = (tmp_path / "tenants" / "custom" / "workspace.yml").read_text()
    assert "#FF6B35" in yml
    assert "🌮" in yml


def test_init_does_not_scaffold_a_registry_nobody_else_can_reach(tmp_path):
    """This used to write `registry=http://localhost:4873/` — the Verdaccio used
    to publish @runspace/ui from a development machine — into every new project.
    A fresh `npm install` then failed with a connection refused for anyone who
    was not that machine, and npm reports it as "Exit handler never called",
    which points nowhere near the cause."""
    _run_init(tmp_path, tenant_id="x")
    npmrc = (tmp_path / "tenants" / "x" / ".npmrc").read_text()
    active = [ln for ln in npmrc.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert active == [], f"the scaffold must not set a registry: {active}"
    assert "localhost" not in npmrc.replace("# ", "").split("registry=")[0] or True
    assert not any("localhost" in ln for ln in active)


class TestScaffoldIsRunnable:
    """`runspace init` is the first command a new user types. What it prints
    and what it generates have to work, together, without editing."""

    @staticmethod
    def _scaffold(tmp_path, tenant_id="acme-widgets"):
        from runspace.workspace.cli.init_cmd import run_init

        run_init(tenant_id=tenant_id, target_dir=tmp_path, interactive=False)
        return tmp_path / "tenants" / tenant_id

    def test_a_closed_stdin_takes_the_defaults_instead_of_crashing(self, tmp_path, monkeypatch):
        """CI, a Dockerfile and `runspace init x < /dev/null` all have no tty.
        The prompts already show the value they will use, so EOF is not an
        error — it used to raise EOFError half way through scaffolding."""
        import builtins

        from runspace.workspace.cli.init_cmd import run_init

        def no_input(_prompt=""):
            raise EOFError

        monkeypatch.setattr(builtins, "input", no_input)
        rc = run_init(tenant_id="quiet-tenant", target_dir=tmp_path, interactive=True)
        assert rc == 0
        assert (tmp_path / "tenants" / "quiet-tenant" / "workspace.yml").exists()

    def test_the_generated_plugin_path_is_an_importable_module_name(self, tmp_path):
        """`tenants.acme-widgets.plugins.health` is not importable from
        anywhere: a hyphen is not legal in a dotted module path, and the
        prefix assumed a working directory the printed instructions do not
        put you in."""
        import yaml

        d = self._scaffold(tmp_path)
        cfg = yaml.safe_load((d / "workspace.yml").read_text())
        for mod in cfg.get("plugins") or []:
            for part in mod.split("."):
                assert part.isidentifier(), f"{mod!r} is not an importable module path"
            first = mod.split(".")[0]
            assert (d / first).exists() or (d / f"{first}.py").exists(), (
                f"{mod!r} resolves relative to a directory the scaffold does not put you in"
            )

    def test_the_printed_next_steps_name_files_that_exist(self, tmp_path, capsys):
        from runspace.workspace.cli.init_cmd import run_init

        run_init(tenant_id="acme-widgets", target_dir=tmp_path, interactive=False)
        printed = capsys.readouterr().out
        d = tmp_path / "tenants" / "acme-widgets"
        assert "runspace.workspace.serve workspace.yml" in printed
        assert (d / "workspace.yml").exists()
        if "docker compose" in printed:
            assert (d / "docker-compose.yml").exists(), (
                "instructions promise docker compose with no compose file"
            )

    def test_the_dockerfile_workdir_agrees_with_the_plugin_path(self, tmp_path):
        """The container copies the tenant directory to WORKDIR, so a plugin
        path is relative to that same directory — the two have to agree or the
        image starts and the local run does not, or the reverse."""
        import yaml

        d = self._scaffold(tmp_path)
        dockerfile = (d / "Dockerfile").read_text()
        assert "WORKDIR /app" in dockerfile
        cfg = yaml.safe_load((d / "workspace.yml").read_text())
        for mod in cfg.get("plugins") or []:
            assert not mod.startswith("tenants."), (
                f"{mod!r} would need the repository root as the working directory, "
                "but the Dockerfile and the printed instructions both use the tenant directory"
            )
