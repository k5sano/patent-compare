"""pytest 共通設定: プロジェクトルートを sys.path に追加する。"""

import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _patch_project_root(monkeypatch, project_root):
    """Patch services that resolve case data via PROJECT_ROOT."""
    from services import case_service

    monkeypatch.setattr(case_service, "PROJECT_ROOT", Path(project_root))
    return Path(project_root)


def pytest_configure(config):
    config.addinivalue_line("markers", "no_network: test must not use external network")


import pytest


@pytest.fixture
def isolated_project_root(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "cases").mkdir(parents=True)
    return _patch_project_root(monkeypatch, root)


@pytest.fixture
def copy_case_fixture(isolated_project_root):
    def _copy(name):
        src = ROOT / "tests" / "fixtures" / "cases" / name
        dst = isolated_project_root / "cases" / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        for sub in ("input", "citations", "prompts", "responses", "output", "analysis"):
            (dst / sub).mkdir(exist_ok=True)
        return dst
    return _copy
