#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package the generated ``android-SysUISdk`` platform as a deterministic
release ZIP for GitHub Releases (2026-09-03, plan A: publish the existing
officially-based platform with a LICENSE/NOTICE provenance stack).

    uv run python tools/package_sysuisdk_release.py

* Source platform must be generator-owned (valid ``.sysuisdk-generated.json``
  marker); refusing to package anything else keeps provenance auditable.
* Output is deterministic: sorted entry names, fixed timestamps/attributes,
  fixed compression — two runs over the same platform produce identical bytes.
* The ZIP layout is ``android-SysUISdk/...`` plus top-level ``LICENSE``,
  ``NOTICE`` and ``README.txt`` taken from ``release/sysuisdk/`` in this repo,
  so users unzip straight into ``<sdk>/platforms/``.
* A ``<output>.sha256`` sidecar is always written next to the ZIP.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform as plat
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

MARKER_NAME = ".sysuisdk-generated.json"
MARKER_SCHEMA_VERSION = 1
PLATFORM_DIR_NAME = "android-SysUISdk"

DEFAULT_RELEASE_NAME = "SysUISdk-android-17.0.0_r1-r1"
TOP_LEVEL_DOCS = ("LICENSE", "NOTICE", "README.txt")

FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_FILE_ATTR = 0o644 << 16
FIXED_CREATE_SYSTEM = 3


class PackageError(Exception):
    """Fatal, user-facing packaging error (reported without a traceback)."""


def default_sdk_root(platform_system: str, environ: dict, home: Path) -> Path:
    if platform_system == "Windows":
        local = environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Android" / "Sdk"
        return home / "AppData" / "Local" / "Android" / "Sdk"
    if platform_system == "Darwin":
        return home / "Library" / "Android" / "sdk"
    return home / "Android" / "Sdk"


def resolve_sdk_root(cli_value: str | None, environ: dict,
                     platform_system: str, home: Path) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    if environ.get("ANDROID_SDK_ROOT"):
        return Path(environ["ANDROID_SDK_ROOT"]).expanduser()
    if environ.get("ANDROID_HOME"):
        return Path(environ["ANDROID_HOME"]).expanduser()
    return default_sdk_root(platform_system, environ,
                            Path(home).expanduser())


def check_generator_owned(platform_dir: Path) -> dict:
    """Require a valid generator marker; return it. Anything else is fatal:
    only generator-composed platforms carry auditable provenance."""
    marker = Path(platform_dir) / MARKER_NAME
    if not marker.is_file():
        raise PackageError(
            f"refusing to package {platform_dir}: no {MARKER_NAME} marker — "
            f"only generator-owned platforms are publishable")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageError(f"invalid marker {marker}: {exc}")
    if not (isinstance(data, dict)
            and data.get("schema_version") == MARKER_SCHEMA_VERSION
            and "tool_version" in data
            and "generated" in data):
        raise PackageError(f"marker {marker} does not match schema "
                           f"{MARKER_SCHEMA_VERSION}")
    return data


def collect_platform_entries(platform_dir: Path) -> dict[str, bytes]:
    """Read every regular file under the platform dir, keyed by the archive
    path ``android-SysUISdk/<relative posix path>``. Symlinks are fatal."""
    platform_dir = Path(platform_dir)
    entries: dict[str, bytes] = {}
    for path in sorted(platform_dir.rglob("*")):
        rel = path.relative_to(platform_dir).as_posix()
        if path.is_symlink():
            raise PackageError(f"symlink in platform dir is not supported: "
                               f"{path}")
        if path.is_file():
            entries[f"{PLATFORM_DIR_NAME}/{rel}"] = path.read_bytes()
    if not entries:
        raise PackageError(f"platform dir is empty: {platform_dir}")
    return entries


def collect_doc_entries(release_dir: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    for name in TOP_LEVEL_DOCS:
        path = Path(release_dir) / name
        if not path.is_file():
            raise PackageError(f"missing release doc: {path}")
        entries[name] = path.read_bytes()
    return entries


def write_deterministic_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FIXED_FILE_ATTR
            info.create_system = FIXED_CREATE_SYSTEM
            zf.writestr(info, entries[name])
    return buf.getvalue()


def write_deterministic_targz(entries: dict[str, bytes]) -> bytes:
    """Deterministic tar.gz: sorted entries, mtime 0, uid/gid 0, mode 0644,
    gzip header without filename/timestamp — byte-identical across runs."""
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
        for name in sorted(entries):
            data = entries[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            info.type = tarfile.REGTYPE
            tf.addfile(info, io.BytesIO(data))
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(tar_buf.getvalue())
    return gz_buf.getvalue()


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="package_sysuisdk_release.py",
        description="Package android-SysUISdk as deterministic release "
                    "artifacts (zip and/or tar.gz).")
    ap.add_argument("--platform",
                    help="platform directory (default "
                         "<sdk-root>/platforms/android-SysUISdk)")
    ap.add_argument("--sdk-root",
                    help="SDK root (default: --sdk-root > ANDROID_SDK_ROOT > "
                         "ANDROID_HOME > OS-specific default)")
    ap.add_argument("--release-dir",
                    help="directory holding LICENSE/NOTICE/README.txt "
                         "(default <repo>/release/sysuisdk)")
    ap.add_argument("--name", default=DEFAULT_RELEASE_NAME,
                    help=f"release base name (default {DEFAULT_RELEASE_NAME})")
    ap.add_argument("--output",
                    help="output path. Endswith .zip/.tar.gz: single artifact "
                         "of that format; extension-less: base path for both "
                         "formats (default <repo>/dist/<name>)")
    return ap


def _resolve_outputs(output_arg: str | None, name: str,
                     repo_root: Path) -> list[tuple[str, Path]]:
    """Return [(format, path)]: extension decides single format; extension-less
    base produces both (release policy: ship zip AND tar.gz)."""
    if output_arg:
        p = Path(output_arg)
        if p.name.endswith(".tar.gz"):
            return [("tar.gz", p)]
        if p.name.endswith(".zip"):
            return [("zip", p)]
        base = p
    else:
        base = repo_root / "dist" / name
    return [("zip", Path(str(base) + ".zip")),
            ("tar.gz", Path(str(base) + ".tar.gz"))]


def run(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    try:
        sdk_root = resolve_sdk_root(args.sdk_root, dict(os.environ),
                                    plat.system(), Path.home())
        platform_dir = (Path(args.platform).expanduser() if args.platform
                        else sdk_root / "platforms" / PLATFORM_DIR_NAME)
        release_dir = (Path(args.release_dir) if args.release_dir
                       else repo_root / "release" / "sysuisdk")
        outputs = _resolve_outputs(args.output, args.name, repo_root)

        check_generator_owned(platform_dir)
        entries = collect_doc_entries(release_dir)
        entries.update(collect_platform_entries(platform_dir))

        results = []
        for fmt, output in outputs:
            payload = (write_deterministic_zip(entries) if fmt == "zip"
                       else write_deterministic_targz(entries))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            output.with_suffix(output.suffix + ".sha256").write_text(
                f"{digest}  {output.name}\n", encoding="utf-8")
            results.append((fmt, output, len(payload), digest))
    except PackageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for fmt, output, size, digest in results:
        print(f"packaged: {output}")
        print(f"  format  : {fmt}")
        print(f"  entries : {len(entries)}")
        print(f"  size    : {size} bytes")
        print(f"  sha256  : {digest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
