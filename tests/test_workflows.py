"""GitHub Actions workflows (.github/): structure, gates and pinning, checked offline."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GITHUB_DIR = ROOT / ".github"
WORKFLOWS_DIR = GITHUB_DIR / "workflows"
CI_FILE = WORKFLOWS_DIR / "ci.yml"
RELEASE_FILE = WORKFLOWS_DIR / "release.yml"

#: owner/repo[/path]@vN, or a local reusable workflow.
PINNED_ACTION_RE = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@v\d+$")
LOCAL_WORKFLOW_RE = re.compile(r"^\./\.github/workflows/[\w.-]+\.ya?ml$")


def load_workflow(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    # YAML 1.1 reads the bare key `on` as the boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return load_workflow(CI_FILE)


@pytest.fixture(scope="module")
def release() -> dict[str, Any]:
    return load_workflow(RELEASE_FILE)


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    result = job.get("steps", [])
    assert isinstance(result, list)
    return result


def runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in steps(job) if "run" in step]


def uses(job: dict[str, Any]) -> list[str]:
    return [str(step["uses"]) for step in steps(job) if "uses" in step]


def all_uses(workflow: dict[str, Any]) -> Iterator[str]:
    for job in workflow["jobs"].values():
        if "uses" in job:
            yield str(job["uses"])
        yield from uses(job)


def test_ci_triggers_and_permissions(ci: dict[str, Any]) -> None:
    triggers = ci["on"]
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch", "workflow_call"}
    assert triggers["push"]["branches"] == ["main", "stream/**"]
    assert ci["permissions"] == {"contents": "read"}
    assert "github.ref" in ci["concurrency"]["group"]
    assert ci["concurrency"]["cancel-in-progress"] is True


def test_ci_has_required_jobs(ci: dict[str, Any]) -> None:
    assert {"lint", "test", "e2e", "build"} <= set(ci["jobs"])
    assert set(ci["jobs"]["e2e"]["needs"]) == {"lint", "test"}


def test_lint_job_runs_the_gate(ci: dict[str, Any]) -> None:
    commands = runs(ci["jobs"]["lint"])
    for command in (
        "uv sync --locked",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy src tests",
        "uv run mypy scripts",
    ):
        assert command in commands, command


def test_test_job_matrix_and_browser(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["test"]
    strategy = job["strategy"]
    assert strategy["fail-fast"] is False
    assert set(strategy["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    assert strategy["matrix"]["python"] == ["3.11", "3.12", "3.13"]
    assert job["env"]["GDM_REQUIRE_BROWSER"] == "1"
    assert job["env"]["PYTHONUTF8"] == "1"
    setup = next(step for step in steps(job) if str(step.get("uses", "")).startswith("astral-sh"))
    assert setup["with"]["python-version"] == "${{ matrix.python }}"
    assert setup["with"]["enable-cache"] is True
    commands = runs(job)
    assert "uv run playwright install --with-deps chromium" in commands
    assert "uv run playwright install chromium" in commands
    assert any(c.startswith("uv run pytest -ra --junitxml=") for c in commands)
    cache = next(
        step for step in steps(job) if str(step.get("uses", "")).startswith("actions/cache")
    )
    assert "ms-playwright" in cache["with"]["path"]
    assert "hashFiles('uv.lock')" in cache["with"]["key"]
    upload = next(s for s in steps(job) if str(s.get("uses", "")).startswith("actions/upload"))
    assert upload["if"] == "always()"


def test_e2e_job(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["e2e"]
    commands = runs(job)
    assert "uv run pytest -m e2e -ra" in commands
    assert "uv run python scripts/run_e2e.py --out e2e-output" in commands
    lint = next(step for step in steps(job) if "@google/design.md" in str(step.get("run", "")))
    assert "designmd lint" in lint["run"]
    assert "steps.pipeline.outputs.design_md" in lint["if"]
    # The path comes from a step output: passed through env, never interpolated into the script.
    assert "${{" not in lint["run"]
    assert any(str(u).startswith("actions/setup-node@") for u in uses(job))
    assert (ROOT / "scripts" / "run_e2e.py").is_file()


def test_build_job_checks_packaged_resources(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["build"]
    script = "\n".join(runs(job))
    assert "uv build" in script
    assert "uvx twine check" in script
    members = re.findall(r"get_design_markdown/[\w./-]+", script)
    required = {
        "get_design_markdown/collector/js/extract.js",
        "get_design_markdown/synthesizer/phrases/en.yaml",
        "get_design_markdown/synthesizer/phrases/vi.yaml",
        "get_design_markdown/templates/design.md.j2",
        "get_design_markdown/templates/prompt.md.j2",
        "get_design_markdown/templates/preview.html.j2",
    }
    assert required <= set(members)
    for member in required:
        assert (ROOT / "src" / member).is_file(), member


def test_release_workflow(release: dict[str, Any]) -> None:
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert release["permissions"] == {"contents": "read"}
    jobs = release["jobs"]
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    job = jobs["release"]
    assert job["needs"] == "ci"
    assert job["permissions"] == {"contents": "write"}
    script = "\n".join(runs(job))
    assert "tomllib" in script and "GITHUB_REF_NAME" in script
    assert "uv build" in script
    assert 'gh release create "$GITHUB_REF_NAME" dist/*' in script
    create = next(step for step in steps(job) if "gh release create" in str(step.get("run", "")))
    assert create["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_release_uses_no_secrets_or_pypi() -> None:
    text = RELEASE_FILE.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "pypi" not in text.lower()


@pytest.mark.parametrize("path", [CI_FILE, RELEASE_FILE], ids=["ci", "release"])
def test_every_action_is_pinned_to_a_major_version(path: Path) -> None:
    for reference in all_uses(load_workflow(path)):
        assert PINNED_ACTION_RE.match(reference) or LOCAL_WORKFLOW_RE.match(reference), reference


@pytest.mark.parametrize("path", [CI_FILE, RELEASE_FILE], ids=["ci", "release"])
def test_uv_is_set_up_before_use(path: Path) -> None:
    for name, job in load_workflow(path)["jobs"].items():
        seen_setup = False
        for step in steps(job):
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                seen_setup = True
            command = str(step.get("run", ""))
            if re.search(r"(^|\s)uvx?\s", command):
                assert seen_setup, f"{name}: '{command}' chạy trước setup-uv"


def test_project_version_matches_release_rule() -> None:
    import tomllib

    from get_design_markdown import __version__

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc|dev)\d+)?", version)
    assert __version__ == version
    script = "\n".join(runs(load_workflow(RELEASE_FILE)["jobs"]["release"]))
    assert "src/get_design_markdown/__init__.py" in script


def test_dependabot_updates_actions_weekly() -> None:
    config = yaml.safe_load((GITHUB_DIR / "dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    update = config["updates"][0]
    assert update["package-ecosystem"] == "github-actions"
    assert update["schedule"]["interval"] == "weekly"


def test_pull_request_template_checklist() -> None:
    text = (GITHUB_DIR / "pull_request_template.md").read_text(encoding="utf-8")
    for needle in (
        "uv run ruff check .",
        "uv run mypy scripts",
        "uv run pytest",
        "change request",
        "CLAUDE.md",
        "output/",
        "scripts/verify_output.py",
    ):
        assert needle in text, needle
