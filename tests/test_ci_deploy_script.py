"""Test deploy/ci-deploy.sh: deploy commit, health check, dan rollback otomatis."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "ci-deploy.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="butuh bash + git",
)


def _git(cwd, *args):
    result = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def vm(tmp_path):
    """Palsukan VM: repo git + stub systemctl/sudo + interpreter palsu."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True
    )

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "master")
    shas = []
    for name in ("v1.txt", "v2.txt"):
        (work / name).write_text(name)
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", name)
        shas.append(_git(work, "rev-parse", "HEAD"))
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "master")

    bot = tmp_path / "bot"
    subprocess.run(["git", "clone", "-q", str(origin), str(bot)], check=True)
    _git(bot, "reset", "--hard", shas[0])  # bot jalan di commit v1
    (bot / "requirements.txt").write_text("")
    (bot / ".env").write_text("TELEGRAM_BOT_TOKEN=test\n")
    (bot / "data").mkdir()
    (bot / "scripts").mkdir()
    stub = bot / "stub"
    stub.mkdir()

    (stub / "unit").write_text("[Service]\nExecStart=stub/fakepy main.py\n")
    (stub / "fakepy").write_text(
        '#!/bin/sh\ncase "$1" in\n  -m) exit 0 ;;\n  *) exit "${HEALTH_RC:-0}" ;;\nesac\n'
    )
    (stub / "fakepy").chmod(0o755)
    (stub / "wrapper.sh").write_text(
        "#!/bin/sh\n"
        "systemctl() {\n"
        "  case \"$1\" in\n"
        "    cat) cat stub/unit ;;\n"
        "    is-active) return \"${ACTIVE_RC:-0}\" ;;\n"
        "    restart) echo restart >> stub/restart.log ;;\n"
        "    *) return 0 ;;\n"
        "  esac\n"
        "}\n"
        "sudo() { if [ \"$1\" = \"-n\" ]; then shift; fi; \"$@\"; }\n"
        '. "' + DEPLOY_SCRIPT.as_posix() + '" "$@"\n'
    )

    return {"bot": bot, "sha_v1": shas[0], "sha_v2": shas[1]}


def _run(vm, *args, health_rc=0, active_rc=0):
    env = os.environ.copy()
    env.update({
        "BOT_DIR": ".",
        "HEALTH_TIMEOUT": "1",
        "HEALTH_INTERVAL": "1",
        "HEALTH_RC": str(health_rc),
        "ACTIVE_RC": str(active_rc),
    })
    return subprocess.run(
        ["bash", "stub/wrapper.sh", *args],
        cwd=vm["bot"], env=env, capture_output=True, text=True,
    )


def _head(vm):
    return _git(vm["bot"], "rev-parse", "HEAD")


def _restarts(vm):
    log = vm["bot"] / "stub" / "restart.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_deploy_installs_target_commit_and_restarts(vm):
    result = _run(vm, "deploy", vm["sha_v2"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(vm) == vm["sha_v2"]
    assert _restarts(vm) == 1


def test_health_ok_keeps_new_commit(vm):
    _run(vm, "deploy", vm["sha_v2"])
    result = _run(vm, "health", health_rc=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(vm) == vm["sha_v2"]
    assert _restarts(vm) == 1  # tidak ada restart tambahan


def test_health_fail_rolls_back_to_previous_commit(vm):
    _run(vm, "deploy", vm["sha_v2"])
    result = _run(vm, "health", health_rc=1)
    assert result.returncode == 1, result.stdout + result.stderr
    assert _head(vm) == vm["sha_v1"]
    assert _restarts(vm) == 2  # restart deploy + restart rollback


def test_service_not_active_triggers_rollback(vm):
    _run(vm, "deploy", vm["sha_v2"])
    result = _run(vm, "health", health_rc=0, active_rc=3)
    assert result.returncode == 1, result.stdout + result.stderr
    assert _head(vm) == vm["sha_v1"]
