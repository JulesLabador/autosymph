from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install-skills.sh"
CHECK = ROOT / "scripts" / "check-skills.sh"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_install_skills_dry_run_allows_existing_conflicts(tmp_path: Path) -> None:
    target = tmp_path / "skills"
    target.mkdir()
    (target / "autoplan").mkdir()

    result = run_script(str(INSTALL), "--dry-run", "--target", str(target))

    assert result.returncode == 0, result.stderr
    assert "WOULD-CONFLICT autoplan" in result.stdout


def test_install_skills_symlink_target_checks_clean(tmp_path: Path) -> None:
    target = tmp_path / "skills"

    install = run_script(str(INSTALL), "--target", str(target))
    check = run_script(str(CHECK), "--strict", "--target", str(target))

    assert install.returncode == 0, install.stderr
    assert check.returncode == 0, check.stdout + check.stderr
    assert (target / "autosymph-monitor").is_symlink()


def test_install_skills_copy_target_checks_clean(tmp_path: Path) -> None:
    target = tmp_path / "skills"

    install = run_script(str(INSTALL), "--copy", "--target", str(target))
    check = run_script(str(CHECK), "--strict", "--target", str(target))

    assert install.returncode == 0, install.stderr
    assert check.returncode == 0, check.stdout + check.stderr
    assert (target / "autosymph-monitor" / "SKILL.md").exists()
    assert not (target / "autosymph-monitor").is_symlink()


def test_check_skills_strict_detects_drift(tmp_path: Path) -> None:
    target = tmp_path / "skills"
    install = run_script(str(INSTALL), "--copy", "--target", str(target))
    assert install.returncode == 0, install.stderr

    (target / "autosymph-monitor" / "SKILL.md").write_text("drift\n")
    check = run_script(str(CHECK), "--strict", "--target", str(target))

    assert check.returncode == 1
    assert "DRIFT   autosymph-monitor" in check.stdout

