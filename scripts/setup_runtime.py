#!/usr/bin/env python3
"""Download/check fixed binaries and configure this project's local IoTDB only.

Does not start services, replace UDFs, delete data, or modify shell profiles.
"""
import hashlib
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "apache-maven-3.9.11-bin.tar.gz": (
        "https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.11/",
        "tools/apache-maven-3.9.11", "bin/mvn"),
    "apache-iotdb-2.0.11-all-bin.zip": (
        "https://downloads.apache.org/iotdb/2.0.11/",
        "runtime/apache-iotdb-2.0.11-all-bin", "sbin/start-standalone.sh"),
}


def main():
    local = ROOT / ".local"
    downloads = local / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    (local / "logs").mkdir(exist_ok=True)
    verified = {}
    for name, (base, destination, marker) in PACKAGES.items():
        for suffix in ("", ".sha512"):
            path = downloads / (name + suffix)
            if not path.exists():
                temporary = path.with_name(path.name + ".part")
                subprocess.run(["curl", "-fL", "--retry", "2", "--connect-timeout", "15",
                                "--max-time", "300", base + name + suffix,
                                "-o", str(temporary)], check=True)
                temporary.replace(path)
        archive = downloads / name
        match = re.search(r"\b[0-9a-fA-F]{128}\b", (downloads / (name + ".sha512")).read_text())
        if not match:
            raise ValueError(f"Invalid checksum file: {name}")
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha512").hexdigest()
        if actual != match.group().lower():
            raise ValueError(f"SHA512 mismatch: {name}; nothing extracted")
        verified[name] = {"url": base + name, "sha512": actual,
                          "verified": True, "bytes": archive.stat().st_size}
        target = local / destination
        if not (target / marker).exists():
            # Refuse to overlay an incomplete directory that may contain user data.
            if target.exists():
                raise ValueError(f"Incomplete destination exists: {target}; inspect it manually")
            target.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".zip"):
                with zipfile.ZipFile(archive) as package:
                    if any(not entry.startswith(target.name + "/")
                           or ".." in Path(entry).parts for entry in package.namelist()):
                        raise ValueError("Unexpected archive member")
                    package.extractall(target.parent)
            else:
                with tarfile.open(archive) as package:
                    package.extractall(target.parent, filter="data")
        print(f"SHA512 OK: {name}")

    properties = local / "runtime/apache-iotdb-2.0.11-all-bin/conf/iotdb-system.properties"
    overlay = {}
    for line in (ROOT / "configs/iotdb-local.properties").read_text().splitlines():
        if line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            overlay[key] = value
    lines = properties.read_text().splitlines()
    configured = []
    for line in lines:
        key = line.split("=", 1)[0]
        configured.append(f"{key}={overlay.pop(key)}" if key in overlay else line)
    configured.extend(f"{key}={value}" for key, value in overlay.items())
    text = "\n".join(configured) + "\n"
    # Do not rewrite an unchanged live configuration.
    if properties.read_text() != text:
        properties.write_text(text)
    (local / "logs/download-verification.json").write_text(json.dumps(verified, indent=2) + "\n")
    print("Prepared local Maven and IoTDB; services not started.")


if __name__ == "__main__":
    main()
