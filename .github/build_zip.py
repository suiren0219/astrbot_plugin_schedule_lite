"""构建 AstrBot 插件 ZIP 包（Release 与本地打包共用同一份文件清单）。

用法：python .github/build_zip.py 输出路径
包内顶层目录固定为 astrbot_plugin_schedule_lite，与安装目录名一致。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_schedule_lite"
ROOT_FILES = (
    ".gitattributes",
    ".gitignore",
    "_conf_schema.json",
    "CHANGELOG.md",
    "LICENSE",
    "main.py",
    "metadata.yaml",
    "README.md",
    "requirements.txt",
)
ROOT_DIRS = (
    ".astrbot-plugin",
    "docs",
    "pages/dashboard",
    "schedule_lite_core",
)
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in ROOT_FILES:
        path = root / name
        if path.is_file():
            files.append(path)
    for dirname in ROOT_DIRS:
        base = root / dirname
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            if path.suffix == ".pyc":
                continue
            files.append(path)
    return files


def main() -> int:
    out_arg = sys.argv[1] if len(sys.argv) > 1 else f"{PLUGIN_NAME}.zip"
    root = Path(__file__).resolve().parent.parent
    out = Path(out_arg)
    if not out.is_absolute():
        out = root / out
    files = collect_files(root)
    if not any(p.name == "main.py" for p in files):
        raise SystemExit("找不到 main.py，请在插件根目录运行")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, f"{PLUGIN_NAME}/{path.relative_to(root).as_posix()}")
    print(f"{out} files={len(files)} size={out.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
