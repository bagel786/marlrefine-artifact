from __future__ import annotations

import subprocess
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


def test_python_distribution_is_apache_scoped(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["uv", "build", "--out-dir", str(tmp_path)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    sdist = tmp_path / "marlrefine-0.1.0.tar.gz"
    wheel = tmp_path / "marlrefine-0.1.0-py3-none-any.whl"
    assert sdist.is_file()
    assert wheel.is_file()

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_paths = {
            member.name.split("/", 1)[1]
            for member in archive.getmembers()
            if member.isfile()
        }
    permitted_sdist_roots = {
        ".gitignore",
        "LICENSE",
        "PKG-INFO",
        "pyproject.toml",
    }
    assert "src/marlrefine/__init__.py" in sdist_paths
    assert "LICENSE" in sdist_paths
    assert "LICENSE-docs-data" not in sdist_paths
    assert all(
        path in permitted_sdist_roots or path.startswith("src/marlrefine/")
        for path in sdist_paths
    )

    with zipfile.ZipFile(wheel) as archive:
        wheel_paths = set(archive.namelist())
        metadata_path = next(
            path for path in wheel_paths if path.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
    assert "marlrefine-0.1.0.dist-info/licenses/LICENSE" in wheel_paths
    assert not any(path.endswith("LICENSE-docs-data") for path in wheel_paths)
    assert metadata["License-Expression"] == "Apache-2.0"
    assert metadata.get_all("License-File") == ["LICENSE"]
