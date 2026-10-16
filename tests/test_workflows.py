"""GitHub Actions workflows (.github/): structure, gates and pinning, checked offline."""

from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from get_design_markdown.ui.settings import LOG_DIR_ENV

ROOT = Path(__file__).resolve().parents[1]
GITHUB_DIR = ROOT / ".github"
WORKFLOWS_DIR = GITHUB_DIR / "workflows"
CI_FILE = WORKFLOWS_DIR / "ci.yml"
RELEASE_FILE = WORKFLOWS_DIR / "release.yml"

#: owner/repo[/path]@vN (or a full vN.N.N tag for actions without a moving major tag), or a
#: local reusable workflow.
PINNED_ACTION_RE = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@v\d+(\.\d+\.\d+)?$")
LOCAL_WORKFLOW_RE = re.compile(r"^\./\.github/workflows/[\w.-]+\.ya?ml$")
#: The official DESIGN.md linter at an exact version.
LINTER_PACKAGE_RE = re.compile(r"@google/design\.md@\d+\.\d+\.\d+")

PACKAGE_DIR = ROOT / "src" / "get_design_markdown"
UI_STATIC_DIR = PACKAGE_DIR / "ui" / "static"
#: git pathspec of the wheel check that lists every file of the UI folder, whatever its suffix.
UI_STATIC_PATHSPEC = "'src/get_design_markdown/ui/static/'"
#: The Windows exe: dependencies, build command, built program and release asset.
EXE_SYNC = "uv sync --locked --extra gui --group build"
BUILD_EXE_SCRIPT = "scripts/build_exe.py"
EXE_PROGRAM = r"dist\Get-Design-Markdown\Get-Design-Markdown.exe"
EXE_ZIP_GLOB = "dist/Get-Design-Markdown-*-windows-x64.zip"
RELEASE_EXE_ARTIFACT = "release-windows-exe"
RELEASE_ASSET = "Get-Design-Markdown-${VERSION}-windows-x64.zip"
#: Workspace folder that receives the exe's ui.log during the self-test (GDM_UI_LOG_DIR).
EXE_LOG_DIR = "exe-logs"


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


def step_index(job: dict[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> int:
    """Index of the first step matching `predicate` (fails when there is none)."""
    for index, step in enumerate(steps(job)):
        if predicate(step):
            return index
    raise AssertionError("no matching step")


def run_contains(text: str) -> Callable[[dict[str, Any]], bool]:
    return lambda step: text in str(step.get("run", ""))


def uses_action(prefix: str) -> Callable[[dict[str, Any]], bool]:
    return lambda step: str(step.get("uses", "")).startswith(prefix)


def upload_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in steps(job) if uses_action("actions/upload-artifact@")(step)]


def self_test_step(job: dict[str, Any]) -> dict[str, Any]:
    return steps(job)[step_index(job, run_contains("Start-Process"))]


def exe_build_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Install, build and self-test steps of a job that builds the Windows exe."""
    return [
        steps(job)[step_index(job, run_contains(EXE_SYNC))],
        steps(job)[step_index(job, run_contains(BUILD_EXE_SCRIPT))],
        self_test_step(job),
    ]


def test_ci_triggers_and_permissions(ci: dict[str, Any]) -> None:
    triggers = ci["on"]
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch", "workflow_call"}
    assert triggers["push"]["branches"] == ["main"]
    assert ci["permissions"] == {"contents": "read"}
    assert "github.ref" in ci["concurrency"]["group"]
    assert ci["concurrency"]["cancel-in-progress"] is True


def test_ci_has_required_jobs(ci: dict[str, Any]) -> None:
    assert {"lint", "test", "e2e", "build", "exe"} <= set(ci["jobs"])
    assert set(ci["jobs"]["e2e"]["needs"]) == {"lint", "test"}
    assert ci["jobs"]["exe"]["needs"] == "lint"


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
    # be covered by the wheel check, which lists the files from git instead of by hand. The
    # UI folder is listed as a whole, so a new kind of file there is covered as well.
    assert UI_STATIC_PATHSPEC in script
    resources = [
        path
        for path in PACKAGE_DIR.rglob("*")
        if path.is_file()
        and path.suffix not in (".py", ".pyc", ".typed")
        and "__pycache__" not in path.parts
    ]
    assert {path.suffix for path in resources} >= {".js", ".yaml", ".j2", ".html"}
    for path in resources:
        if UI_STATIC_DIR not in path.parents:
            assert path.suffix in patterns, path
    assert "resources.txt" in script and "Wheel thiếu" in script
    # An empty or missing UI folder fails the check instead of passing silently.
    assert (UI_STATIC_DIR / "index.html").is_file()
    assert "'get_design_markdown/ui/static/index.html' resources.txt" in script


def test_wheel_smoke_test_reads_the_ui_from_the_installed_wheel(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["build"]
    smoke = steps(job)[step_index(job, run_contains("wheel-env"))]["run"]
    assert "resource_text" in smoke and "resources.txt" in smoke
    assert 'resource_path("ui", "static", "index.html")' in smoke
    # Images are read as bytes; text files must also decode as UTF-8.
    assert "read_bytes()" in smoke
    # The code under test is the installed wheel, never the checkout.
    assert '"wheel-env" in Path(get_design_markdown.__file__).parts' in smoke
    for command in (
        "wheel-env/bin/gdm --version",
        "wheel-env/bin/gdm ui --help",
        "wheel-env/bin/gdm-ui --help",
    ):
        assert command in smoke, command


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


def test_exe_job_builds_and_self_tests_on_windows(ci: dict[str, Any]) -> None:
    job = ci["jobs"]["exe"]
    assert job["runs-on"] == "windows-latest"
    assert 0 < job["timeout-minutes"] <= 60
    commands = runs(job)
    assert EXE_SYNC in commands
    build = next(command for command in commands if BUILD_EXE_SCRIPT in command)
    assert build.startswith("uv run ") and build.endswith(f"python {BUILD_EXE_SCRIPT} --zip")
    assert "--extra gui" in build and "--group build" in build
    # Like on a user's PC: no Playwright Chromium, the self-test must find Edge by itself.
    assert not any("playwright install" in command for command in commands)
    assert not any(uses_action("actions/cache@")(step) for step in steps(job))

    test = self_test_step(job)
    assert test["shell"] == "pwsh"
    assert 0 < test["timeout-minutes"] <= 20
    script = test["run"]
    # A windowed exe returns at once: only -Wait -PassThru gives its real exit code.
    start = next(line for line in script.splitlines() if "Start-Process" in line)
    assert f"-FilePath '{EXE_PROGRAM}'" in start
    for token in ("-Wait", "-PassThru", "'--self-test'", "'--self-test-report'"):
        assert token in start, token
    assert "if ($p.ExitCode -ne 0)" in script and "exit 1" in script
    assert "Get-Content" in script and "Test-Path $report" in script
    # ui.log of the windowed exe (its only output) lands in the workspace and is uploaded.
    assert f"$env:{LOG_DIR_ENV} = Join-Path (Get-Location) '{EXE_LOG_DIR}'" in script
    assert "Get-Content -Encoding utf8 -Tail 60 $log" in script

    order = [
        step_index(job, run_contains(EXE_SYNC)),
        step_index(job, run_contains(BUILD_EXE_SCRIPT)),
        step_index(job, run_contains("Start-Process")),
        step_index(job, lambda step: step.get("with", {}).get("path") == EXE_ZIP_GLOB),
    ]
    assert order == sorted(order), order
    zip_upload, report_upload = upload_steps(job)
    assert zip_upload["with"]["path"] == EXE_ZIP_GLOB
    assert zip_upload["with"]["if-no-files-found"] == "error"
    assert zip_upload["with"]["archive"] is False
    assert "if" not in zip_upload
    assert report_upload["with"]["path"].split() == ["self-test.json", f"{EXE_LOG_DIR}/"]
    assert report_upload["if"] == "always()"


def test_exe_is_built_the_same_way_in_ci_and_release(
    ci: dict[str, Any], release: dict[str, Any]
) -> None:
    assert exe_build_steps(ci["jobs"]["exe"]) == exe_build_steps(release["jobs"]["exe"])


def test_release_workflow(release: dict[str, Any]) -> None:
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert release["permissions"] == {"contents": "read"}
    jobs = release["jobs"]
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    job = jobs["release"]
    assert set(job["needs"]) == {"ci", "exe"}
    assert job["permissions"] == {"contents": "write"}
    script = "\n".join(runs(job))
    assert "tomllib" in script and "GITHUB_REF_NAME" in script
    assert 'echo "version=${version}" >> "$GITHUB_OUTPUT"' in script
    assert "uv build" in script
    assert 'gh release create "$GITHUB_REF_NAME" dist/* "$exe_zip"' in script
    assert f'exe_zip="exe/{RELEASE_ASSET}"' in script
    create = steps(job)[step_index(job, run_contains("gh release create"))]
    assert create["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert create["env"]["VERSION"] == "${{ steps.version.outputs.version }}"
    download_at = step_index(job, uses_action("actions/download-artifact@"))
    assert steps(job)[download_at]["with"] == {"name": RELEASE_EXE_ARTIFACT, "path": "exe"}
    assert download_at < step_index(job, run_contains("gh release create"))


def test_release_builds_the_exe_for_the_tag(release: dict[str, Any]) -> None:
    job = release["jobs"]["exe"]
    assert job["needs"] == "ci"
    assert job["runs-on"] == "windows-latest"
    assert job["env"]["PYTHONUTF8"] == "1"
    # The tag is checked before the (long) build, with bash on Windows.
    version_at = step_index(job, lambda step: step.get("id") == "version")
    version = steps(job)[version_at]
    assert version["shell"] == "bash"
    for needle in ("tomllib", "GITHUB_REF_NAME", 'echo "version=${version}" >> "$GITHUB_OUTPUT"'):
        assert needle in version["run"], needle
    assert version_at < step_index(job, run_contains(BUILD_EXE_SCRIPT))
    check = steps(job)[step_index(job, run_contains(f'zip="dist/{RELEASE_ASSET}"'))]
    assert check["shell"] == "bash"
    assert check["env"]["VERSION"] == "${{ steps.version.outputs.version }}"
    zip_upload, report_upload = upload_steps(job)
    assert zip_upload["with"]["name"] == RELEASE_EXE_ARTIFACT
    assert zip_upload["with"]["path"] == (
        "dist/Get-Design-Markdown-${{ steps.version.outputs.version }}-windows-x64.zip"
    )
    assert zip_upload["with"]["if-no-files-found"] == "error"
    assert report_upload["with"]["path"].split() == ["self-test.json", f"{EXE_LOG_DIR}/"]
    assert report_upload["if"] == "always()"


def _artifact_names(workflow: dict[str, Any]) -> list[str]:
    names = []
    for job in workflow["jobs"].values():
        for step in upload_steps(job):
            settings = step["with"]
            # archive: false uploads one file under its own name; `name` is ignored then.
            archived = settings.get("archive", True) is not False
            names.append(str(settings["name"]) if archived else Path(settings["path"]).name)
    return names


def test_artifact_names_are_unique_in_a_release_run(
    ci: dict[str, Any], release: dict[str, Any]
) -> None:
    # The release run includes the CI jobs (workflow_call): one artifact name used twice
    # would fail the second upload.
    names = _artifact_names(ci) + _artifact_names(release)
    assert len(names) == len(set(names)), names
    assert RELEASE_EXE_ARTIFACT in _artifact_names(release)


def test_windows_steps_declare_their_shell(ci: dict[str, Any], release: dict[str, Any]) -> None:
    # Windows runners default to PowerShell: bash syntax needs `shell: bash`.
    bash_syntax = re.compile(r"\bif \[|\bfi\b|\$GITHUB_OUTPUT|\$\{\w+\}")
    for workflow in (ci, release):
        for name, job in workflow["jobs"].items():
            if job.get("runs-on") != "windows-latest":
                continue
            for step in steps(job):
                command = str(step.get("run", ""))
                if "Start-Process" in command:
                    assert step["shell"] == "pwsh", name
                elif bash_syntax.search(command):
                    assert step.get("shell") == "bash", (name, command)


def test_build_script_produces_the_released_names() -> None:
    script = ROOT / BUILD_EXE_SCRIPT
    if not script.is_file():
        pytest.skip(f"{BUILD_EXE_SCRIPT} chưa có trong bản mã nguồn này.")
    text = script.read_text(encoding="utf-8")
    assert "--zip" in text
    assert "windows-x64.zip" in text and "Get-Design-Markdown" in text


#: Stand-in for the windowed exe: writes the report it is given after a short pause (so a
#: missing -Wait would be caught) and exits with $FAKE_EXIT.
FAKE_EXE = """\
import json, os, sys, time
args = sys.argv[1:]
with open("env.txt", "w", encoding="utf-8") as handle:
    handle.write(os.environ.get("GDM_UI_LOG_DIR", ""))
time.sleep(1.0)
if os.environ["FAKE_REPORT"] == "1":
    path = args[args.index("--self-test-report") + 1]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"ok": os.environ["FAKE_EXIT"] == "0", "args": args}, handle)
sys.exit(int(os.environ["FAKE_EXIT"]))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Bước tự kiểm tra bản exe chạy trên Windows.")
@pytest.mark.parametrize(
    ("exit_code", "report", "expected"),
    [(0, True, 0), (1, True, 1), (0, False, 1)],
    ids=["passed", "failed", "no-report"],
)
def test_exe_self_test_step_runs_in_powershell(
    ci: dict[str, Any], tmp_path: Path, exit_code: int, report: bool, expected: int
) -> None:
    """The CI step itself, run the way GitHub runs a pwsh step, with pythonw.exe (a windowed
    program, like the exe) running a fake self-test in place of the exe."""
    pwsh = shutil.which("pwsh")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if pwsh is None or not pythonw.is_file():
        pytest.skip("Cần PowerShell 7 (pwsh) và pythonw.exe.")
    fake = tmp_path / "fake_exe.py"
    fake.write_text(FAKE_EXE, encoding="utf-8")
    script = self_test_step(ci["jobs"]["exe"])["run"]
    program = f"-FilePath '{EXE_PROGRAM}'"
    arguments = "-ArgumentList '--self-test'"
    assert script.count(program) == 1 and script.count(arguments) == 1
    script = script.replace(program, f"-FilePath '{pythonw}'").replace(
        arguments, f"-ArgumentList '\"{fake}\"','--self-test'"
    )
    work = tmp_path / "work dir"  # the report path holds a space
    work.mkdir()
    wrapper = work / "step.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'stop'\n"
        + script
        + "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n",
        encoding="utf-8-sig",
    )
    env = dict(os.environ, FAKE_EXIT=str(exit_code), FAKE_REPORT="1" if report else "0")
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", f". '{wrapper}'"],
        cwd=work,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == expected, output
    if report:
        # The report is printed to the job log.
        assert '"ok": ' in completed.stdout and "self-test.json" in completed.stdout
    # The program was started with the log folder of the step.
    assert (work / EXE_LOG_DIR).resolve() == Path(
        (work / "env.txt").read_text(encoding="utf-8").strip()
    ).resolve()
    if expected:
        assert "::error::" in completed.stdout, output


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


#: A Markdown heading outside code blocks, and an in-page link "](#anchor)".
README_HEADING_RE = re.compile(r"^#{1,6} (.+?)\s*$")
README_ANCHOR_LINK_RE = re.compile(r"\]\(#([^)\s]+)\)")


def github_anchor(heading: str) -> str:
    """The id GitHub gives a heading: lower case, punctuation dropped, spaces as dashes."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def readme_anchors(readme: str) -> set[str]:
    anchors: set[str] = set()
    fenced = False
    for line in readme.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (match := README_HEADING_RE.match(line)):
            anchor = github_anchor(match.group(1))
            # A repeated heading gets "-1", "-2"... like on GitHub.
            suffix = 0
            unique = anchor
            while unique in anchors:
                suffix += 1
                unique = f"{anchor}-{suffix}"
            anchors.add(unique)
    return anchors


def test_github_anchor() -> None:
    assert github_anchor("Tải bản exe cho Windows") == "tải-bản-exe-cho-windows"
    assert github_anchor("Định dạng DESIGN.md") == "định-dạng-designmd"
    assert (
        github_anchor("Dùng với Claude Code / claude.ai: `--prompt-only`")
        == "dùng-với-claude-code--claudeai---prompt-only"
    )


def test_readme_links_point_to_existing_sections() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    anchors = readme_anchors(readme)
    links = set(README_ANCHOR_LINK_RE.findall(readme))
    assert links, "README has no table of contents"
    assert links <= anchors, sorted(links - anchors)


def test_readme_documents_the_ui_and_the_exe() -> None:
    import typer

    from get_design_markdown import cli
    from get_design_markdown.models.raw import BROWSER_CHOICES
    from get_design_markdown.ui import settings

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commands = typer.main.get_command(cli.app).commands  # type: ignore[attr-defined]
    # Every option of `gdm ui`, and --browser of collect/get, with its choices.
    for name in ("ui", "get", "collect"):
        for param in commands[name].params:
            for option in param.opts:
                if option.startswith("--"):
                    assert f"`{option}" in readme, (name, option)
    for choice in BROWSER_CHOICES:
        assert f"`{choice}`" in readme, choice
    assert "| `GDM_BROWSER` |" in readme
    assert f"| `{settings.CONFIG_DIR_ENV}` |" in readme
    assert f"| `{settings.LOG_DIR_ENV}` |" in readme
    # Where the UI and the exe keep their files (ui/settings.py).
    for needle in (
        rf"%APPDATA%\{settings.APP_DIR_NAME}\{settings.SETTINGS_FILE}",
        rf"%LOCALAPPDATA%\{settings.APP_DIR_NAME}\logs",
        settings.LOG_FILE,
        rf"Documents\{settings.EXE_OUTPUT_DIR_NAME}",
    ):
        assert needle in readme, needle
    # The exe: release asset, unsigned-binary warning, options of ui/app.py main(), build.
    asset = EXE_ZIP_GLOB.removeprefix("dist/").replace("*", "<phiên-bản>")
    assert asset in readme
    for needle in ("More info", "Run anyway", "Thông tin thêm", "Vẫn chạy", "Tải Chromium"):
        assert needle in readme, needle
    for option in ("--browser-tab", "--port", "--self-test", "--self-test-report"):
        assert f"Get-Design-Markdown.exe {option}" in readme or f"'{option}'" in readme, option
    assert f"python {BUILD_EXE_SCRIPT} --zip" in readme
    assert "-Wait -PassThru" in readme
    assert '[gui]"' in readme and "--extra gui" in readme


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
    assert found == {}


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
