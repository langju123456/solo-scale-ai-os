# ruff: noqa: F821
# This template packages only tracked application resources. It never discovers user data.
import os
import subprocess
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]


def tracked_files(*prefixes):
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", *prefixes],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    records = []
    for raw_relative in completed.stdout.split(b"\0"):
        if not raw_relative:
            continue
        relative = Path(raw_relative.decode("utf-8"))
        source = ROOT / relative
        if source.is_file():
            records.append((str(source), str(relative.parent)))
    return records


datas = tracked_files(".agents", "packages/buildlog/prompts")
datas.extend(collect_data_files("soloscale", includes=["content_data/*.json"]))
datas.append(
    (
        str(ROOT / "packages" / "buildlog" / "src" / "buildlog" / "web_static"),
        "buildlog/web_static",
    )
)
a = Analysis(
    [str(ROOT / "src" / "soloscale" / "local_ui.py")],
    pathex=[str(ROOT / "src"), str(ROOT / "packages" / "buildlog" / "src")],
    datas=datas,
    hiddenimports=collect_submodules("soloscale") + collect_submodules("buildlog"),
    excludes=["node", "playwright", "selenium"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SoloScaleBackend",
    console=False,
    codesign_identity=os.environ.get("SOLOSCALE_CODESIGN_IDENTITY") or None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="SoloScaleBackend")
