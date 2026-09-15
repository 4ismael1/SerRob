"""Build a Pterodactyl upload package using an explicit allowlist (no secrets)."""
import hashlib
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / "dist" / "roblox-discord-pterodactyl.zip"
    destination.parent.mkdir(exist_ok=True)
    files = [root / name for name in (
        "main.py", "requirements.txt", "requirements-dev.txt", "README.md", ".env.example",
        "pytest.ini", "Dockerfile", "compose.yaml", ".dockerignore", ".gitignore",
    )]
    for folder, pattern in (("serverbot", "*.py"), ("tests", "*.py"), ("tools", "*.py"),
                            ("docs", "*.md"), (".github/workflows", "*.yml")):
        files.extend((root / folder).rglob(pattern))
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, path.relative_to(root).as_posix())
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert not any(n == ".env" or n.startswith(("data/", ".validation-runtime/", ".venv/")) for n in archive.namelist())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    print(f"PACKAGE_OK | {len(files)} files | {destination.stat().st_size} bytes")
    print(destination)


if __name__ == "__main__":
    main()
