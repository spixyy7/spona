import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE.parent / "spona" / "clip_helper.dex"
MIN_API = "26"


def _version_key(path: Path):
    parts = []
    for piece in path.name.replace("android-", "").replace("-", ".").split("."):
        parts.append(int(piece) if piece.isdigit() else 0)
    return parts


def _sdk_root() -> Path:
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(variable)
        if value and Path(value).is_dir():
            return Path(value)
    sys.exit("Set ANDROID_HOME to your Android SDK folder")


def _java_home() -> Path:
    value = os.environ.get("JAVA_HOME")
    if value and (Path(value) / "bin").is_dir():
        return Path(value)
    javac = shutil.which("javac")
    if javac:
        return Path(javac).resolve().parent.parent
    sys.exit("Set JAVA_HOME to a JDK 11 or newer")


def _newest(folder: Path, needed: str) -> Path:
    candidates = [path for path in folder.iterdir() if (path / needed).exists()] if folder.is_dir() else []
    if not candidates:
        sys.exit(f"Nothing with {needed} in {folder}")
    return max(candidates, key=_version_key)


def main() -> None:
    sdk = _sdk_root()
    java_home = _java_home()
    android_jar = _newest(sdk / "platforms", "android.jar") / "android.jar"
    tools = _newest(sdk / "build-tools", "d8.bat" if os.name == "nt" else "d8")
    d8 = tools / ("d8.bat" if os.name == "nt" else "d8")
    javac = java_home / "bin" / ("javac.exe" if os.name == "nt" else "javac")
    environment = dict(os.environ, JAVA_HOME=str(java_home))
    environment["PATH"] = str(java_home / "bin") + os.pathsep + environment.get("PATH", "")
    with tempfile.TemporaryDirectory() as work:
        classes = Path(work) / "classes"
        output = Path(work) / "dex"
        classes.mkdir()
        output.mkdir()
        subprocess.run(
            [str(javac), "--release", "8", "-nowarn", "-classpath", str(android_jar), "-d", str(classes),
             str(HERE / "ClipHelper.java")],
            check=True,
            env=environment,
        )
        class_files = [str(path) for path in classes.rglob("*.class")]
        subprocess.run(
            [str(d8), "--release", "--min-api", MIN_API, "--lib", str(android_jar), "--output", str(output),
             *class_files],
            check=True,
            env=environment,
        )
        shutil.copyfile(output / "classes.dex", TARGET)
    print(f"Built {TARGET.relative_to(HERE.parent)} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
