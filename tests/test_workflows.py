"""GitHub Actions workflows (.github/): structure, gates and pinning, checked offline."""

from __future__ import annotations

import itertools
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
#: The official DESIGN.md linter at an exact version.
LINTER_PACKAGE_RE = re.compile(r"@google/design\.md@\d+\.\d+\.\d+")


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
    assert triggers["push"]["branches"] == ["main"]
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
    # Every stage is merged: CI never accepts a stage that is not built.
    assert not any("--allow-partial" in command for command in commands)
    lint = next(step for step in steps(job) if "designmd lint" in str(step.get("run", "")))
    assert '--package "$DESIGN_MD_LINTER"' in lint["run"]
    assert "steps.pipeline.outputs.design_md" in lint["if"]
    # The path comes from a step output: passed through env, never interpolated into the script.
    assert "${{" not in lint["run"]
    assert any(str(u).startswith("actions/setup-node@") for u in uses(job))
    assert (ROOT / "scripts" / "run_e2e.py").is_file()


def test_official_linter_is_pinned(ci: dict[str, Any]) -> None:
    assert LINTER_PACKAGE_RE.fullmatch(ci["env"]["DESIGN_MD_LINTER"]), ci["env"]
    for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        for job in load_workflow(path)["jobs"].values():
            for command in runs(job):
                for package in re.findall(r"--package\s+(\S+)", command):
                    assert package == '"$DESIGN_MD_LINTER"' or LINTER_PACKAGE_RE.fullmatch(
                        package
                    ), (path.name, command)


def test_build_job_checks_every_packaged_resource(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["build"]
    script = "\n".join(runs(job))
    assert "uv build" in script
    assert "uvx twine check" in script
    patterns = re.findall(r"'src/get_design_markdown/\*(\.[\w.]+)'", script)
    # Every non-Python file shipped in the package (e.g. the collector's in-page scripts) must
    # be covered by the wheel check, which lists the files from git instead of by hand.
    resources = [
        path
        for path in (ROOT / "src" / "get_design_markdown").rglob("*")
        if path.is_file() and path.suffix not in (".py", ".pyc", ".typed")
    ]
    assert {path.suffix for path in resources} >= {".js", ".yaml", ".j2"}
    for path in resources:
        assert path.suffix in patterns, path
    assert "resources.txt" in script and "Wheel thiếu" in script
    smoke = next(step for step in steps(job) if "wheel-env" in str(step.get("run", "")))
    assert "resource_text" in smoke["run"] and "resources.txt" in smoke["run"]


def test_sdist_is_built_from_an_explicit_file_list(ci: dict[str, Any]) -> None:
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = config["tool"]["hatch"]["build"]["targets"]["sdist"]
    # Hatchling honours .gitignore but not .git/info/exclude: without an explicit list a local
    # build ships whatever untracked file sits in the working tree.
    included = set(sdist["only-include"])
    assert {"src", "tests", "README.md", "LICENSE", "pyproject.toml"} <= included
    assert not included & {"output", "e2e-output", ".env", ".venv", ".github"}
    tracked_top = {path.name for path in ROOT.iterdir() if path.name in included}
    assert tracked_top == included
    check = next(step for step in steps(ci["jobs"]["build"]) if "tar -tzf" in step.get("run", ""))
    # CI compares the sdist with the files git tracks, so nothing local slips in.
    assert "git ls-files" in check["run"] and "comm -23" in check["run"]


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


# ─── Public repository hygiene ───────────────────────────────────────────────

#: `pytest -m <marker>` commands the README documents (a single marker, not an expression).
README_MARKER_COMMAND_RE = re.compile(r"pytest -m (\w+)(?=[`\s;)]|$)")
#: First cell of a README table row: | `browser` | ... |
README_ROW_KEY_RE = re.compile(r"^\| `(\w+)` \|")
README_MARKER_TABLE_INTRO = "Marker của pytest:"
PYTEST_MARK_RE = re.compile(r"pytest\.mark\.(\w+)")

#: Wording of the internal parallel build: stream branches, stream ownership notes and the
#: integrator role. The public repository describes the project, not how it was assembled.
INTERNAL_PROCESS_RE = re.compile(
    r"\bstream/[\w*]"
    r"|\(stream [a-z]"
    r"|\bstreams? (?:analyzer|collector|synthesizer|exporters|qa-devops)\b"
    r"|\b(?:collector|analyzer(?:-[\w-]+)?|synthesizer|exporters?|components|qa-devops|parallel"
    r"|this|their) streams?\b"
    r"|\bintegrator\b"
    r"|phạm vi của mình",
    re.IGNORECASE,
)
PUBLIC_TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".yml", ".yaml", ".toml", ".j2", ".js", ".html", ".css", ".txt"}
)
#: Leftovers in files outside .github/, scripts/ and README.md, whose rewording was sent as a
#: change request; drop an entry once its file is reworded.
PENDING_REWORDING = frozenset(
    {
        "src/get_design_markdown/analyzer/components.py",
        "src/get_design_markdown/analyzer/components_context.py",
        "src/get_design_markdown/analyzer/components_detect.py",
        "src/get_design_markdown/analyzer/components_props.py",
        "src/get_design_markdown/analyzer/components_surface.py",
        "src/get_design_markdown/collector/collect.py",
        "src/get_design_markdown/synthesizer/synthesize.py",
        "tests/conftest.py",
        "tests/fixtures/build_samples.py",
        "tests/test_analyzer_layout.py",
        "tests/test_fixtures.py",
        "tests/test_glue.py",
    }
)


def registered_markers() -> set[str]:
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    return {str(line).split(":", 1)[0].strip() for line in markers}


def used_markers() -> set[str]:
    this_file = Path(__file__).resolve()
    return {
        marker
        for path in (ROOT / "tests").rglob("*.py")
        if path.resolve() != this_file
        for marker in PYTEST_MARK_RE.findall(path.read_text(encoding="utf-8"))
    }


def readme_marker_rows(readme: str) -> set[str]:
    """Markers listed in the README table that follows "Marker của pytest:"."""
    assert README_MARKER_TABLE_INTRO in readme
    lines = readme.split(README_MARKER_TABLE_INTRO, 1)[1].strip().splitlines()
    rows = itertools.takewhile(lambda line: line.startswith("|"), lines)
    return {match.group(1) for row in rows if (match := README_ROW_KEY_RE.match(row))}


def test_readme_marker_commands_select_tests() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    documented = set(README_MARKER_COMMAND_RE.findall(readme))
    assert "e2e" in documented
    registered = registered_markers()
    assert documented <= registered
    # A documented `pytest -m X` must select something: exit code 5 is not a passing run.
    assert documented <= used_markers()
    assert readme_marker_rows(readme) == registered


def public_text_files() -> Iterator[Path]:
    yield ROOT / "README.md"
    yield ROOT / "pyproject.toml"
    this_file = Path(__file__).resolve()
    for folder in (GITHUB_DIR, ROOT / "scripts", ROOT / "src", ROOT / "tests"):
        for path in sorted(folder.rglob("*")):
            if (
                path.is_file()
                and path.suffix in PUBLIC_TEXT_SUFFIXES
                and "__pycache__" not in path.parts
                and path.resolve() != this_file
            ):
                yield path


def test_public_files_do_not_describe_the_parallel_build() -> None:
    found: dict[str, list[str]] = {}
    for path in public_text_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if INTERNAL_PROCESS_RE.search(line):
                found.setdefault(path.relative_to(ROOT).as_posix(), []).append(
                    f"{number}: {line.strip()}"
                )
    unexpected = {path: lines for path, lines in found.items() if path not in PENDING_REWORDING}
    assert unexpected == {}


def test_internal_process_pattern() -> None:
    for text in (
        '      - "stream/**"',
        "Private to analyzer/components*.py (stream analyzer-components); not public.",
        "internal to the collector stream.",
        "whatever the state of the parallel streams",
        "test fixtures (integrator-owned, frozen)",
        "- [ ] Chỉ sửa các file thuộc phạm vi của mình",
    ):
        assert INTERNAL_PROCESS_RE.search(text), text
    for text in (
        "if isinstance(stream, io.TextIOWrapper):",
        "for stream in (sys.stdout, sys.stderr):",
        "git push origin main upstream/main",
        "Private to analyzer/components*.py; not a public interface.",
    ):
        assert INTERNAL_PROCESS_RE.search(text) is None, text
