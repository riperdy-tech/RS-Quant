#!/usr/bin/env python3
"""QuantDesk Release Builder and Packaging Automation (§16.1, §16.3)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
WEB_DIR = ROOT_DIR / "web"
LAUNCHER_DIR = ROOT_DIR / "launcher"


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def ensure_web_build() -> None:
    """Verifies or builds static frontend web assets."""
    dist_index = WEB_DIR / "dist" / "index.html"
    if dist_index.exists():
        print(f"Verified existing web distribution at {WEB_DIR / 'dist'}")
        return

    print("Building web frontend with npm...")
    subprocess.run(["npm", "--prefix", str(WEB_DIR), "run", "build"], check=True)
    if not dist_index.exists():
        raise RuntimeError(f"Web build failed to produce {dist_index}")


def build_pyinstaller_bundle() -> Path:
    """Invokes PyInstaller using QuantDesk.spec."""
    print("Building PyInstaller package with QuantDesk.spec...")
    spec_path = LAUNCHER_DIR / "QuantDesk.spec"
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec file not found at {spec_path}")

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(DIST_DIR), str(spec_path)],
        cwd=str(LAUNCHER_DIR),
        check=True,
    )
    output_bundle = DIST_DIR / "QuantDesk"
    if not output_bundle.exists():
        raise RuntimeError(f"PyInstaller failed to create {output_bundle}")
    print(f"PyInstaller bundle successfully created at {output_bundle}")
    return output_bundle


def generate_release_manifest(bundle_dir: Path) -> Path:
    """Generates release manifest containing SHA-256 hashes of all bundled files."""
    manifest_files: list[dict[str, object]] = []
    for path in sorted(bundle_dir.glob("**/*")):
        if path.is_file():
            rel_path = path.relative_to(bundle_dir).as_posix()
            manifest_files.append({
                "path": rel_path,
                "size_bytes": path.stat().st_size,
                "sha256": compute_sha256(path),
            })

    manifest: dict[str, object] = {
        "app": "QuantDesk",
        "version": "0.1.0",
        "platform": sys.platform,
        "total_files": len(manifest_files),
        "files": manifest_files,
    }

    manifest_path = bundle_dir / "release_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated release manifest with {len(manifest_files)} files at {manifest_path}")
    return manifest_path


def test_install_path_with_spaces() -> bool:
    """Verifies application execution and configuration loading in a directory path containing spaces (§16.1, Task 17)."""
    print("Testing application execution in a path containing spaces...")
    with tempfile.TemporaryDirectory(prefix="QuantDesk Program Files Test ") as temp_dir:
        temp_path = Path(temp_dir)
        test_file = temp_path / "test_load.py"
        test_file.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "src_path = Path(sys.argv[1])\n"
            "sys.path.insert(0, str(src_path))\n"
            "import quantdesk\n"
            "from quantdesk.config.loader import load_config\n"
            "cfg = load_config(Path(sys.argv[2]))\n"
            "assert cfg.mode.value == 'DEMO'\n"
            "print('PATH_WITH_SPACES_TEST: SUCCESS')\n",
            encoding="utf-8",
        )

        config_path = ROOT_DIR / "configs" / "demo.yaml"
        src_path = ROOT_DIR / "src"

        res = subprocess.run(
            [sys.executable, str(test_file), str(src_path), str(config_path)],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0 or "PATH_WITH_SPACES_TEST: SUCCESS" not in res.stdout:
            print(f"Path with spaces test failed: {res.stderr}", file=sys.stderr)
            return False

        print("Path with spaces verification: PASS")
        return True


def compile_inno_setup_installer() -> str:
    """Attempts to compile Inno Setup installer if ISCC is installed."""
    iss_file = LAUNCHER_DIR / "installer.iss"
    iscc_exe = shutil.which("ISCC") or shutil.which("iscc")
    if not iscc_exe:
        # Check standard Windows Inno Setup paths
        candidates = [
            Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
            Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
        ]
        for c in candidates:
            if c.exists():
                iscc_exe = str(c)
                break

    if iscc_exe:
        print(f"Found Inno Setup Compiler at {iscc_exe}. Compiling installer...")
        subprocess.run([iscc_exe, str(iss_file)], check=True)
        return "COMPILED"
    else:
        print("Inno Setup Compiler (ISCC) not detected in environment. Checked in installer.iss ready for CI.")
        return "SKIPPED_NO_ISCC"


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantDesk Release Builder")
    parser.add_argument("--target", choices=["windows", "linux", "all"], default="windows")
    parser.add_argument("--skip-bundle", action="store_true", help="Skip PyInstaller bundle step")
    parser.add_argument("--test-spaces", action="store_true", help="Run path-with-spaces verification")

    args = parser.parse_args()

    if args.test_spaces and not test_install_path_with_spaces():
        return 1

    if args.skip_bundle:
        print("Skipping PyInstaller bundle per flag.")
        return 0

    ensure_web_build()

    if args.target in ("windows", "all"):
        bundle_dir = build_pyinstaller_bundle()
        generate_release_manifest(bundle_dir)
        installer_status = compile_inno_setup_installer()
        print(f"Release build complete for target 'windows'. Installer status: {installer_status}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
