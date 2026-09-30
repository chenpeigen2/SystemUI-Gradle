#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SysUISdk single-entry AOSP composition (Task 045).

Composes an independent, generator-owned ``android-SysUISdk`` platform from a
read-only stock SDK platform (default ``android-37.0``) plus exact already-built
AOSP ``out/`` artifacts:

    python3 tools/build_sysuisdk.py --aosp-root /path/to/aosp

Design (docs/architecture/2026-08-21-sysuisdk-single-entry-composition.md):

* One command, one transaction: compose into a sibling temporary staging
  directory, validate, then publish by rename. Failure cleans staging only.
* No Soong invocation, no in-place patching of an installed platform, no
  S0–S5/``--apply``/restore interface, no permanent backups.
* The frozen artifact map (§2 of the architecture spec) is exact: seven
  AOSP-relative inputs, no globbing, no newest-file fallback.
* The aggregate framework turbine JAR is master over duplicate stock SDK class
  entries; framework resources come byte-exactly from ``framework-res.apk``.
* The bridge is exactly the unchanged Task 041 35-entry allowlist plus the
  four dalvik optimization annotations, injected into both target JARs;
  ``AssumeTrueForR8`` stays out. The two
  ``android/compat/annotation/UnsupportedAppUsage{,$Container}`` classes are
  NOT bridged: the 17 framework aggregate turbine JAR already embeds them
  (turbine bytes), and the framework aggregate is master (D12, decision audit
  2026-08-29: ``docs/architecture/2026-08-29-decision-audit/
  d12-sysuisdk-bridge-collision.md``, option 1).
* Deterministic output: stable entry ordering, fixed timestamps/attributes/
  compression, and a generator marker recording input/output provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

# --- Constants --------------------------------------------------------------

TOOL_VERSION = "045.2"
DEFAULT_BASE_PLATFORM_NAME = "android-37.0"
OUTPUT_PLATFORM_NAME = "android-SysUISdk"
OUTPUT_PKG_PATH = f"platforms;{OUTPUT_PLATFORM_NAME}"
OUTPUT_API_LEVEL = "37"
OUTPUT_CODENAME = "SysUISdk"
OUTPUT_DISPLAY_NAME = "Android SDK Platform SysUISdk 37"
MARKER_NAME = ".sysuisdk-generated.json"
MARKER_SCHEMA_VERSION = 1


class BuildError(Exception):
    """Fatal, user-facing composition error (reported without a traceback)."""


# --- Frozen AOSP artifact map (architecture spec §2) --------------------------
# Seven exact AOSP-relative inputs. Missing files are fatal; there is no
# glob-based or newest-file fallback. (D12 2026-08-29: the former
# ``unsupportedappusage_jar`` input was removed with its bridge slice; the two
# UnsupportedAppUsage classes now come from the framework aggregate JAR.)

AOSP_INPUT_RELPATHS: dict[str, str] = {
    "framework_jar":
        "out/soong/.intermediates/frameworks/base/framework/"
        "android_common/turbine-combined/framework.jar",
    "framework_res_apk":
        "out/soong/.intermediates/frameworks/base/core/res/"
        "framework-res/android_common/framework-res.apk",
    "core_libart_jar":
        "out/soong/.intermediates/libcore/core-libart/"
        "android_common_apex31/javac/core-libart.jar",
    "aconfig_annotations_jar":
        "out/soong/.intermediates/frameworks/libs/modules-utils/java/"
        "aconfig-annotations-lib/linux_glibc_common/javac/"
        "aconfig-annotations-lib.jar",
    "keepanno_jar":
        "out/soong/.intermediates/prebuilts/r8/keepanno-annotations/"
        "android_common/combined/keepanno-annotations.jar",
    "iremote_callback_aidl":
        "frameworks/base/core/java/android/os/IRemoteCallback.aidl",
    "screenshot_request_aidl":
        "frameworks/base/core/java/com/android/internal/util/"
        "ScreenshotRequest.aidl",
}


def resolve_inputs(aosp_root: Path) -> dict[str, Path]:
    """Resolve every frozen input under ``aosp_root`` or fail with its path."""
    aosp_root = Path(aosp_root)
    resolved: dict[str, Path] = {}
    missing: list[Path] = []
    for key, rel in AOSP_INPUT_RELPATHS.items():
        path = aosp_root / rel
        if not path.is_file():
            missing.append(path)
        resolved[key] = path
    if missing:
        raise BuildError(
            "missing frozen AOSP input(s): "
            + "; ".join(str(p) for p in missing))
    return resolved


# --- framework.aidl hidden declaration derivation -----------------------------
# The two hidden declarations are derived from their primary AOSP sources
# (package + top-level kind/name are parsed and checked, never hard-coded
# without checking the source).

HIDDEN_AIDL_SOURCES: tuple[tuple[str, str, str], ...] = (
    # (input key, expected FQN, expected declaration kind)
    ("iremote_callback_aidl", "android.os.IRemoteCallback", "interface"),
    ("screenshot_request_aidl",
     "com.android.internal.util.ScreenshotRequest", "parcelable"),
)


def derive_aidl_declaration(source_text: str, expected_fqn: str,
                            expected_kind: str) -> str:
    """Derive the fully-qualified declaration from a primary AIDL source.

    Parses the ``package`` statement and the top-level ``interface``/
    ``parcelable`` declaration, verifies the expected kind and FQN, and
    returns ``"<kind> <fqn>;"``. Any mismatch is fatal.
    """
    import re
    pkg_match = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", source_text)
    if not pkg_match:
        raise BuildError(
            f"AIDL source for {expected_fqn} has no package declaration")
    package = pkg_match.group(1)
    decl_match = re.search(
        r"(?m)^\s*(?:oneway\s+)?(interface|parcelable)\s+(\w+)", source_text)
    if not decl_match:
        raise BuildError(
            f"AIDL source for {expected_fqn} has no top-level "
            f"interface/parcelable declaration")
    kind, name = decl_match.group(1), decl_match.group(2)
    fqn = f"{package}.{name}"
    if fqn != expected_fqn:
        raise BuildError(
            f"AIDL source declares {fqn}, expected {expected_fqn}")
    if kind != expected_kind:
        raise BuildError(
            f"AIDL source for {fqn} declares kind {kind!r}, "
            f"expected {expected_kind!r}")
    return f"{kind} {fqn};"


def derive_hidden_aidl_declarations(aosp_root: Path) -> list[str]:
    """Derive both hidden declarations from the frozen primary sources."""
    inputs = resolve_inputs(aosp_root)
    decls: list[str] = []
    for key, fqn, kind in HIDDEN_AIDL_SOURCES:
        text = inputs[key].read_text(encoding="utf-8")
        decls.append(derive_aidl_declaration(text, fqn, kind))
    return decls


# --- CLI ---------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="build_sysuisdk.py",
        description="Compose android-SysUISdk from a stock SDK platform "
                    "plus exact already-built AOSP out/ artifacts.")
    ap.add_argument("--aosp-root", required=True,
                    help="path to the AOSP tree (read-only; consumes out/ "
                         "intermediates and two primary AIDL sources)")
    ap.add_argument("--sdk-root",
                    help="SDK root (default: --sdk-root > ANDROID_SDK_ROOT > "
                         "ANDROID_HOME > OS-specific default)")
    ap.add_argument("--base-platform",
                    default=DEFAULT_BASE_PLATFORM_NAME,
                    help=f"stock base platform name under <sdk-root>/platforms "
                         f"or an explicit platform directory path "
                         f"(default {DEFAULT_BASE_PLATFORM_NAME})")
    ap.add_argument("--output",
                    help=f"output platform directory (default "
                         f"<sdk-root>/platforms/{OUTPUT_PLATFORM_NAME})")
    ap.add_argument("--replace", action="store_true",
                    help="replace an existing generator-owned output "
                         "(refused for unmarked outputs and for the base "
                         "platform)")
    return ap


# --- SDK-root discovery -------------------------------------------------------

def default_sdk_root(platform_system: str, environ: dict, home: Path) -> Path:
    """OS-specific default SDK root (spec §2 discovery order, step 4–6)."""
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
    """Resolve the SDK root: CLI > ANDROID_SDK_ROOT > ANDROID_HOME > default."""
    if cli_value:
        return Path(cli_value).expanduser()
    if environ.get("ANDROID_SDK_ROOT"):
        return Path(environ["ANDROID_SDK_ROOT"]).expanduser()
    if environ.get("ANDROID_HOME"):
        return Path(environ["ANDROID_HOME"]).expanduser()
    return default_sdk_root(platform_system, environ,
                            Path(home).expanduser())


# --- Path resolution ----------------------------------------------------------

def resolve_base_platform(spec: str, sdk_root: Path) -> Path:
    """Resolve --base-platform: an existing directory path wins; otherwise a
    platform name under ``<sdk-root>/platforms``."""
    if Path(spec).exists():
        return Path(spec).resolve()
    if Path(spec).is_absolute() or (len(Path(spec).parts) > 1
                                    and Path(spec) != Path(".")):
        return Path(spec)
    return (Path(sdk_root) / "platforms" / spec).resolve()


def resolve_output(cli_value: str | None, sdk_root: Path) -> Path:
    if cli_value:
        return Path(cli_value)
    return Path(sdk_root) / "platforms" / OUTPUT_PLATFORM_NAME


# --- Frozen bridge allowlist (39 entries) ------------------------------------
# The bridge is exactly the unchanged Task 041 35-entry allowlist plus the
# four existing dalvik optimization annotations. ``AssumeTrueForR8`` stays
# out (release-only adapter in app/proguard_gradle.flags owns it).

_DALVIK_OPTIMIZATION_ENTRIES = (
    "dalvik/annotation/optimization/DeadReferenceSafe.class",
    "dalvik/annotation/optimization/NeverCompile.class",
    "dalvik/annotation/optimization/NeverInline.class",
    "dalvik/annotation/optimization/ReachabilitySensitive.class",
)
_IO_UTILS_ENTRIES = (
    "libcore/io/IoUtils.class",
    "libcore/io/IoUtils$FileReader.class",
)
_NATIVE_ALLOCATION_REGISTRY_ENTRIES = (
    "libcore/util/NativeAllocationRegistry.class",
    "libcore/util/NativeAllocationRegistry$CleanerRunner.class",
    "libcore/util/NativeAllocationRegistry$CleanerThunk.class",
    "libcore/util/NativeAllocationRegistry$Metrics.class",
)
_DDMC_ENTRIES = (
    "org/apache/harmony/dalvik/ddmc/Chunk.class",
    "org/apache/harmony/dalvik/ddmc/ChunkHandler.class",
    "org/apache/harmony/dalvik/ddmc/DdmServer.class",
    "org/apache/harmony/dalvik/ddmc/DdmVmInternal.class",
)
_ACONFIG_FLAG_ACCESSOR_ENTRIES = (
    "com/android/aconfig/annotations/AconfigFlagAccessor.class",
)
_KEEPANNO_ANNOTATION_ENTRIES = tuple(
    f"com/android/tools/r8/keepanno/annotations/{name}.class" for name in (
        "AnnotationPattern", "CheckOptimizedOut", "CheckRemoved",
        "ClassAccessFlags", "ClassNamePattern", "FieldAccessFlags",
        "InstanceOfPattern", "KeepBinding", "KeepCondition", "KeepConstraint",
        "KeepEdge", "KeepForApi", "KeepItemKind", "KeepOption", "KeepTarget",
        "MemberAccessFlags", "MethodAccessFlags", "StringPattern",
        "TypePattern", "UsedByNative", "UsedByReflection", "UsesReflection",
    ))

# (source input key, exact entry tuple) slices — declarative, never expanded
# at runtime by package prefix.
_BRIDGE_SLICES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("core_libart_jar", _DALVIK_OPTIMIZATION_ENTRIES),
    ("core_libart_jar", _IO_UTILS_ENTRIES),
    ("core_libart_jar", _NATIVE_ALLOCATION_REGISTRY_ENTRIES),
    ("core_libart_jar", _DDMC_ENTRIES),
    ("aconfig_annotations_jar", _ACONFIG_FLAG_ACCESSOR_ENTRIES),
    ("keepanno_jar", _KEEPANNO_ANNOTATION_ENTRIES),
)

BRIDGE_ENTRIES: tuple[str, ...] = tuple(sorted(
    entry for _, entries in _BRIDGE_SLICES for entry in entries))
assert len(BRIDGE_ENTRIES) == 37, len(BRIDGE_ENTRIES)
assert "com/android/aconfig/annotations/AssumeTrueForR8.class" \
    not in BRIDGE_ENTRIES
# D12 (2026-08-29): the two UnsupportedAppUsage classes must NOT be bridged;
# they arrive via the framework aggregate JAR (master over duplicates).
assert "android/compat/annotation/UnsupportedAppUsage.class" \
    not in BRIDGE_ENTRIES


def load_bridge(inputs: dict[str, Path]) -> dict[str, bytes]:
    """Load every allowlisted bridge entry, byte-exact from its source jar.

    A declared entry missing from its assigned source jar, or declared by
    more than one slice, is fatal before any composition happens.
    """
    bridge: dict[str, bytes] = {}
    owners: dict[str, str] = {}
    for source_key, entries in _BRIDGE_SLICES:
        source = inputs[source_key]
        with zipfile.ZipFile(source, "r") as zf:
            names = set(zf.namelist())
            for entry in entries:
                if entry in owners:
                    raise BuildError(
                        f"bridge entry declared by two slices: {entry}")
                if entry not in names:
                    raise BuildError(
                        f"declared bridge entry missing from {source}: "
                        f"{entry}")
                owners[entry] = source_key
                bridge[entry] = zf.read(entry)
    assert len(bridge) == 37
    return bridge


# --- Deterministic ZIP composition -------------------------------------------

FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)   # ZIP epoch — constant for determinism
FIXED_FILE_ATTR = 0o644 << 16            # regular file, rw-r--r--
FIXED_CREATE_SYSTEM = 3                  # Unix (constant, not platform-derived)


def _read_unique_entries(path: Path) -> dict[str, bytes]:
    """Read every non-directory entry of a ZIP; duplicate names are fatal."""
    with zipfile.ZipFile(path, "r") as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise BuildError(
                f"duplicate entry names in {path}: {dupes[:3]}...")
        return {n: zf.read(n) for n in names}


def _is_resource_entry(name: str) -> bool:
    """An entry owned by the framework resource set."""
    return name == "resources.arsc" or name.startswith("res/")


def _apply_bridge(entries: dict[str, bytes], bridge: dict[str, bytes]) -> None:
    """Inject bridge entries: equal bytes are idempotent, unequal are fatal."""
    for name, data in sorted(bridge.items()):
        existing = entries.get(name)
        if existing is None:
            entries[name] = data
        elif existing != data:
            raise BuildError(
                f"bridge collision: target entry {name} differs from the "
                f"approved source bytes")


def _write_deterministic_zip(entries: dict[str, bytes]) -> bytes:
    """Serialize entries into a deterministic ZIP: sorted names, fixed
    timestamps/attributes/compression."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FIXED_FILE_ATTR
            info.create_system = FIXED_CREATE_SYSTEM
            zf.writestr(info, entries[name])
    return buf.getvalue()


# --- Method body stubbing (MockableJarTransform 兼容性) ----------------------
#
# AGP 的 MockableJarGenerator（builder）把方法体替换成 stub 时不清 tryCatchBlocks，
# ClassWriter COMPUTE_FRAMES 在悬空 handler label 上 NPE（Cannot read field
# "outgoingEdges" because "handlerRangeBlock" is null）→ androidApis 解析失败、
# Android Studio 同步中断。Google 标准 android.jar 不含真实方法体故从不触发；
# SysUISdk 合并的 AOSP 真实字节码（带 try-catch）触发。本节把方法体打成标准 SDK
# stub 形态（android-35 android.jar 形态：<init> 保留 super() 前缀后 throw "Stub!"，
# 其余方法 throw "Stub!"，exception table 与 Code 子属性清空）。幂等。

_STUB_MARKER = "Stub!"


def _init_opcode_len() -> list[int]:
    t = [1] * 256
    for op in (0x10, 0x12, 0x15, 0x16, 0x17, 0x18, 0x19,
               0x36, 0x37, 0x38, 0x39, 0x3a, 0xa9, 0xbc):
        t[op] = 2
    for op in (0x11, 0x13, 0x14, 0x84, *range(0x99, 0xa9),
               0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8,
               0xbb, 0xbd, 0xc0, 0xc1, 0xc6, 0xc7):
        t[op] = 3
    t[0xc5] = 4
    t[0xb9] = 5
    t[0xba] = 5
    t[0xc8] = 5
    t[0xc9] = 5
    t[0xaa] = 0   # tableswitch（变长）
    t[0xab] = 0   # lookupswitch（变长）
    t[0xc4] = 0   # wide（变长）
    return t


_OPCODE_LEN = _init_opcode_len()


def _insn_length(code: bytes, i: int) -> int:
    op = code[i]
    if op == 0xc4:  # wide：0xc4 + u1 op + u2 index [+ s2]（iinc）
        return 6 if code[i + 1] == 0x84 else 4
    if op == 0xaa:  # tableswitch：pad 到 4 字节对齐 + default/low/high + offsets
        base = i + 1 + (-(i + 1)) % 4
        low, high = struct.unpack_from(">ii", code, base + 4)
        return base - i + 12 + (high - low + 1) * 4
    if op == 0xab:  # lookupswitch：pad + default/npairs + pairs
        base = i + 1 + (-(i + 1)) % 4
        (npairs,) = struct.unpack_from(">i", code, base + 4)
        return base - i + 8 + npairs * 8
    return _OPCODE_LEN[op]


def _first_invokespecial_end(code: bytes) -> int | None:
    """返回首条 INVOKESPECIAL 的结束位置（<init> 的 super() 调用），无则 None。"""
    i = 0
    while i < len(code):
        if code[i] == 0xb7:
            return i + _insn_length(code, i)
        i += _insn_length(code, i)
    return None


class _ClassCp:
    """class 常量池：保留原始条目 + find-or-add 追加（索引稳定→幂等）。"""

    def __init__(self, raw: bytes, count: int, utf8s: dict[int, str]):
        self.raw_entries: list[bytes] = []   # 下标 = 索引-1（长型占位 None）
        self.utf8s = utf8s
        self._by_utf8: dict[str, int] = {v: k for k, v in utf8s.items()}
        self._class: dict[int, int] = {}
        self._string: dict[int, int] = {}
        self._nat: dict[tuple[int, int], int] = {}
        self._methodref: dict[tuple[int, int], int] = {}
        self._next = count

    def seed(self, raw_entries: list[bytes]) -> None:
        """用已有条目播种 find-or-add 映射（追加前先复用，保证幂等）。"""
        self.raw_entries = raw_entries
        for idx0, raw in enumerate(raw_entries):
            if raw is None:
                continue
            idx = idx0 + 1
            tag = raw[0]
            if tag == 7:
                (u,) = struct.unpack_from(">H", raw, 1)
                self._class.setdefault(u, idx)
            elif tag == 8:
                (u,) = struct.unpack_from(">H", raw, 1)
                self._string.setdefault(u, idx)
            elif tag == 12:
                n, d = struct.unpack_from(">HH", raw, 1)
                self._nat.setdefault((n, d), idx)
            elif tag == 10:
                c, t = struct.unpack_from(">HH", raw, 1)
                self._methodref.setdefault((c, t), idx)

    def _append(self, raw: bytes, wide: bool = False) -> int:
        idx = self._next
        self.raw_entries.append(raw)
        if wide:
            self.raw_entries.append(None)
        self._next += 2 if wide else 1
        return idx

    def utf8(self, s: str) -> int:
        if s in self._by_utf8:
            return self._by_utf8[s]
        data = s.encode("utf-8")
        idx = self._append(bytes([1]) + struct.pack(">H", len(data)) + data)
        self._by_utf8[s] = idx
        self.utf8s[idx] = s
        return idx

    def class_ref(self, internal: str) -> int:
        u = self.utf8(internal)
        if u in self._class:
            return self._class[u]
        idx = self._append(bytes([7]) + struct.pack(">H", u))
        self._class[u] = idx
        return idx

    def string(self, s: str) -> int:
        u = self.utf8(s)
        if u in self._string:
            return self._string[u]
        idx = self._append(bytes([8]) + struct.pack(">H", u))
        self._string[u] = idx
        return idx

    def nat(self, name: str, desc: str) -> int:
        n, d = self.utf8(name), self.utf8(desc)
        key = (n, d)
        if key in self._nat:
            return self._nat[key]
        idx = self._append(bytes([12]) + struct.pack(">HH", n, d))
        self._nat[key] = idx
        return idx

    def methodref(self, internal: str, name: str, desc: str) -> int:
        c = self.class_ref(internal)
        t = self.nat(name, desc)
        key = (c, t)
        if key in self._methodref:
            return self._methodref[key]
        idx = self._append(bytes([10]) + struct.pack(">HH", c, t))
        self._methodref[key] = idx
        return idx

    def emit(self) -> bytes:
        out = [b"", b""]
        for e in self.raw_entries:
            if e is not None:
                out.append(e)
        return b"".join(out), self._next


def stub_class_method_bodies(class_bytes: bytes) -> bytes:
    """把单个 .class 的方法体打成标准 SDK stub 形态（幂等）。

    <init>：保留到首条 INVOKESPECIAL（super()）为止的前缀后接 throw stub；
    其余方法（含 <clinit>）：整体替换为 throw new RuntimeException("Stub!")。
    exception table 与 Code 子属性（StackMapTable/LineNumberTable 等）全部清空。
    """
    if class_bytes[:4] != b"\xca\xfe\xba\xbe":
        raise BuildError("stub_class_method_bodies: not a class file")
    off = 8
    cp_count = struct.unpack_from(">H", class_bytes, off)[0]
    off += 2
    utf8s: dict[int, str] = {}
    raw_entries: list[bytes] = []
    i = 1
    while i < cp_count:
        start = off
        tag = class_bytes[off]
        off += 1
        wide = False
        if tag == 1:
            ln = struct.unpack_from(">H", class_bytes, off)[0]
            off += 2 + ln
            utf8s[i] = class_bytes[start + 3:start + 3 + ln].decode("utf-8", errors="replace")
        elif tag in (7, 8, 16, 19, 20):
            off += 2
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            off += 4
        elif tag == 15:
            off += 4   # MethodHandle: u1 kind + u2 index（额外 3 字节 + tag）
        elif tag in (5, 6):
            off += 8
            wide = True
        else:
            raise BuildError(f"stub_class_method_bodies: unknown cp tag {tag}")
        raw_entries.append(class_bytes[start:off])
        if wide:
            raw_entries.append(None)
            i += 2
        else:
            i += 1
    tail_start = off  # access/this/super/interfaces/fields/methods/attrs
    cp = _ClassCp(b"", cp_count, utf8s)
    cp.seed(raw_entries)

    # 头部（access/this/super + interfaces）原样保留
    off += 6
    ifc_n = struct.unpack_from(">H", class_bytes, off)[0]
    head_end = off + 2 + ifc_n * 2

    def skip_attrs(o: int) -> int:
        n = struct.unpack_from(">H", class_bytes, o)[0]
        o += 2
        for _ in range(n):
            o += 2
            ln = struct.unpack_from(">I", class_bytes, o)[0]
            o += 4 + ln
        return o

    off = head_end
    nfields = struct.unpack_from(">H", class_bytes, off)[0]
    o = off + 2
    for _ in range(nfields):
        o += 6
        o = skip_attrs(o)
    fields_end = o

    nmeth = struct.unpack_from(">H", class_bytes, fields_end)[0]
    o = fields_end + 2
    method_blocks: list[bytes] = []
    changed = False

    def stub_code(method_name: str, code: bytes, max_stack: int, max_locals: int) -> bytes:
        str_idx = cp.string(_STUB_MARKER)
        ldc = (b"\x12" + struct.pack(">B", str_idx)) if str_idx <= 0xff \
            else (b"\x13" + struct.pack(">H", str_idx))
        thr = (b"\xbb" + struct.pack(">H", cp.class_ref("java/lang/RuntimeException"))
               + b"\x59"
               + ldc
               + b"\xb7" + struct.pack(">H", cp.methodref(
                   "java/lang/RuntimeException", "<init>", "(Ljava/lang/String;)V"))
               + b"\xbf")
        if method_name == "<init>":
            end = _first_invokespecial_end(code)
            new_code = (code[:end] + thr) if end is not None else thr
        else:
            new_code = thr
        body = struct.pack(">HHI", max(max_stack, 3), max_locals, len(new_code)) \
            + new_code + struct.pack(">H", 0) + struct.pack(">H", 0)
        return struct.pack(">HI", cp.utf8("Code"), len(body)) + body

    for _ in range(nmeth):
        start = o
        _acc, name_idx, _desc = struct.unpack_from(">HHH", class_bytes, o)
        o += 6
        nattr = struct.unpack_from(">H", class_bytes, o)[0]
        o += 2
        method_name = utf8s.get(name_idx, "")
        new_attrs: list[bytes] = []
        method_changed = False
        for _ in range(nattr):
            astart = o
            ani = struct.unpack_from(">H", class_bytes, o)[0]
            aln = struct.unpack_from(">I", class_bytes, o + 2)[0]
            aend = o + 6 + aln
            if utf8s.get(ani) == "Code":
                max_stack, max_locals = struct.unpack_from(">HH", class_bytes, o + 6)
                clen = struct.unpack_from(">I", class_bytes, o + 10)[0]
                code = class_bytes[o + 14:o + 14 + clen]
                exc_len = struct.unpack_from(">H", class_bytes, o + 14 + clen)[0]
                sub_off = o + 14 + clen + 2 + exc_len * 8
                sub_n = struct.unpack_from(">H", class_bytes, sub_off)[0]
                rebuilt = stub_code(method_name, code, max_stack, max_locals)
                if (exc_len == 0 and sub_n == 0
                        and rebuilt == class_bytes[astart:aend]):
                    new_attrs.append(class_bytes[astart:aend])
                else:
                    new_attrs.append(rebuilt)
                    method_changed = True
            else:
                new_attrs.append(class_bytes[astart:aend])
            o = aend
        changed = changed or method_changed
        block = class_bytes[start:start + 6] + struct.pack(">H", len(new_attrs)) \
            + b"".join(new_attrs)
        method_blocks.append(block)

    attrs_start = o
    tail = class_bytes[tail_start:head_end] + class_bytes[head_end:fields_end] \
        + struct.pack(">H", nmeth) + b"".join(method_blocks) \
        + class_bytes[attrs_start:]
    if not changed:
        return class_bytes
    cp_bytes, new_count = cp.emit()
    header = struct.pack(">IHH", 0xCAFEBABE,
                         *struct.unpack_from(">HH", class_bytes, 4)) \
        + struct.pack(">H", new_count) + cp_bytes
    return header + tail


def stub_jar_method_bodies(jar_bytes: bytes) -> bytes:
    """对 jar 内全部真实 .class（CAFEBABE 魔数）执行 stub_class_method_bodies
    （幂等、确定性输出）；非 class 字节的 .class 条目原样透传。"""
    entries = {n: d for n, d in _read_unique_entries_bytes(jar_bytes).items()}
    for name in list(entries):
        if name.endswith(".class") and entries[name][:4] == b"\xca\xfe\xba\xbe":
            entries[name] = stub_class_method_bodies(entries[name])
    return _write_deterministic_zip(entries)


def _read_unique_entries_bytes(jar_bytes: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(jar_bytes), "r") as zf:
        for name in zf.namelist():
            if name in out:
                raise BuildError(f"duplicate zip entry: {name}")
            out[name] = zf.read(name)
    return out


def compose_android_jar(base_jar: Path, framework_jar: Path,
                        framework_res_apk: Path,
                        bridge: dict[str, bytes]) -> bytes:
    """Compose android.jar (architecture spec §3.1).

    1. Start from the stock base jar (non-resource entries).
    2. Overlay every non-resource entry from the framework aggregate — a
       duplicate framework entry intentionally wins.
    3. Take the complete resource set (``resources.arsc`` + ``res/**``)
       byte-exactly from the framework-res APK; all other APK entries
       (manifest, META-INF signing, assets) are excluded.
    4. Inject the bridge under the idempotent/fatal collision rule.
    """
    base = _read_unique_entries(base_jar)
    framework = _read_unique_entries(framework_jar)
    apk = _read_unique_entries(framework_res_apk)
    entries: dict[str, bytes] = {}
    for name, data in base.items():
        if not _is_resource_entry(name):
            entries[name] = data
    for name, data in framework.items():
        if not _is_resource_entry(name):
            entries[name] = data
    for name, data in apk.items():
        if _is_resource_entry(name):
            entries[name] = data
    _apply_bridge(entries, bridge)
    for name in list(entries):
        if name.endswith(".class") and entries[name][:4] == b"\xca\xfe\xba\xbe":
            entries[name] = stub_class_method_bodies(entries[name])
    return _write_deterministic_zip(entries)


def compose_core_modules_jar(base_jar: Path,
                             bridge: dict[str, bytes]) -> bytes:
    """Compose core-for-system-modules.jar (spec §3.2): stock base + bridge."""
    entries = _read_unique_entries(base_jar)
    _apply_bridge(entries, bridge)
    return _write_deterministic_zip(entries)


# --- Platform composition (transaction) ---------------------------------------

# Base-platform files replaced by composed bytes (never copied verbatim).
_COMPOSED_BASE_NAMES = ("android.jar", "core-for-system-modules.jar",
                        "framework.aidl")


def _is_backup_name(name: str) -> bool:
    return name.endswith(".orig") or ".bak-" in name


def _copy_base_platform(base: Path, staging: Path) -> None:
    """Copy the stock base platform into staging, skipping backup artifacts
    and the files this generator composes."""
    for entry in sorted(os.listdir(base)):
        if entry in _COMPOSED_BASE_NAMES or entry == MARKER_NAME \
                or _is_backup_name(entry):
            continue
        src = base / entry
        dst = staging / entry
        if src.is_dir():
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("*.orig", "*.bak-*"))
        else:
            shutil.copy2(src, dst)


def compose_framework_aidl(base_text: str, decls: list[str]) -> str:
    """Append each absent declaration exactly once to the stock framework.aidl."""
    text = base_text
    if text and not text.endswith("\n"):
        text += "\n"
    for decl in decls:
        if decl not in text:
            text += decl + "\n"
    return text


def rewrite_package_xml(text: str) -> str:
    """Rewrite the base package.xml for the generated platform identity."""
    def sub(pattern: str, replacement: str) -> None:
        nonlocal text
        text = re.sub(pattern, lambda m: m.group(1) + replacement + m.group(2),
                      text, count=1)
    sub(r'(localPackage path=")[^"]*(")', OUTPUT_PKG_PATH)
    sub(r"(<api-level>)[^<]*(</api-level>)", OUTPUT_API_LEVEL)
    sub(r"(<codename>)[^<]*(</codename>)", OUTPUT_CODENAME)
    sub(r"(<display-name>)[^<]*(</display-name>)", OUTPUT_DISPLAY_NAME)
    return text


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_inventory(root: Path) -> dict[str, str]:
    """{relative posix path: sha256} for every file under root."""
    return {p.relative_to(root).as_posix(): _sha256_file(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def _validate_platform(staging: Path, inputs: dict[str, Path],
                       bridge: dict[str, bytes], decls: list[str],
                       base_platform: Path) -> None:
    """Full pre-publication validation (architecture spec §5)."""
    staging = Path(staging)
    # 1. input ZIPs contain unique names.
    for key in ("framework_jar", "framework_res_apk", "core_libart_jar",
                "aconfig_annotations_jar", "keepanno_jar"):
        with zipfile.ZipFile(inputs[key], "r") as zf:
            names = [i.filename for i in zf.infolist() if not i.is_dir()]
        if len(names) != len(set(names)):
            raise BuildError(f"duplicate entry names in input {key}: "
                             f"{inputs[key]}")
    # 2. generated jars are readable and carry the complete bridge.
    android_jar = staging / "android.jar"
    core_jar = staging / "core-for-system-modules.jar"
    for path in (android_jar, core_jar):
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise BuildError(f"generated jar failed CRC check at {bad}: "
                                 f"{path}")
            names = set(zf.namelist())
            for entry, data in sorted(bridge.items()):
                if entry not in names:
                    raise BuildError(f"bridge entry missing from {path.name}: "
                                     f"{entry}")
                if zf.read(entry) != data:
                    raise BuildError(f"bridge entry bytes differ from source "
                                     f"in {path.name}: {entry}")
    # 3. android.jar resource set is byte-exact vs framework-res.apk.
    with zipfile.ZipFile(android_jar, "r") as az, \
            zipfile.ZipFile(inputs["framework_res_apk"], "r") as pz:
        a_res = {i.filename: az.read(i.filename)
                 for i in az.infolist()
                 if not i.is_dir() and _is_resource_entry(i.filename)}
        p_res = {i.filename: pz.read(i.filename)
                 for i in pz.infolist()
                 if not i.is_dir() and _is_resource_entry(i.filename)}
    if a_res != p_res:
        raise BuildError("android.jar resource set differs from "
                         "framework-res.apk")
    # 4. the two hidden AIDL declarations are present and source-derived.
    aidl_text = (staging / "framework.aidl").read_text(encoding="utf-8")
    derived = []
    for key, fqn, kind in HIDDEN_AIDL_SOURCES:
        decl = derive_aidl_declaration(
            inputs[key].read_text(encoding="utf-8"), fqn, kind)
        derived.append(decl)
        if decl not in aidl_text:
            raise BuildError(f"framework.aidl is missing derived hidden "
                             f"declaration: {decl}")
    if derived != decls:
        raise BuildError("framework.aidl declarations do not match the "
                         "derived set")
    # 5. package.xml metadata.
    pkg_text = (staging / "package.xml").read_text(encoding="utf-8")
    for needle in (f'path="{OUTPUT_PKG_PATH}"',
                   f"<api-level>{OUTPUT_API_LEVEL}</api-level>",
                   f"<codename>{OUTPUT_CODENAME}</codename>"):
        if needle not in pkg_text:
            raise BuildError(f"package.xml metadata check failed: "
                             f"{needle!r} not found")
    # 6. no backup artifacts in the generated platform.
    for p in staging.rglob("*"):
        if _is_backup_name(p.name):
            raise BuildError(f"backup artifact in generated platform: {p}")
    # 7. composition is deterministic (re-compose and compare).
    android_again = compose_android_jar(
        base_platform / "android.jar", inputs["framework_jar"],
        inputs["framework_res_apk"], bridge)
    if _sha256_bytes(android_again) != _sha256_file(android_jar):
        raise BuildError("android.jar composition is not deterministic")
    core_again = compose_core_modules_jar(
        base_platform / "core-for-system-modules.jar", bridge)
    if _sha256_bytes(core_again) != _sha256_file(core_jar):
        raise BuildError("core-for-system-modules.jar composition is not "
                         "deterministic")


def _build_marker(base_platform: Path, inputs: dict[str, Path],
                  staging: Path) -> dict:
    """Ownership + provenance marker (deterministic, no absolute paths)."""
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "base_platform": {
            "name": base_platform.name,
            "inventory": _dir_inventory(base_platform),
        },
        "inputs": {key: {"path": rel, "sha256": _sha256_file(inputs[key])}
                   for key, rel in AOSP_INPUT_RELPATHS.items()},
        "generated": {"inventory": _dir_inventory(staging)},
    }


def _is_generator_owned(path: Path) -> bool:
    marker = path / MARKER_NAME
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict)
            and data.get("schema_version") == MARKER_SCHEMA_VERSION
            and "tool_version" in data
            and "generated" in data)


def _refuse_alias(output: Path, base: Path) -> None:
    if output == base or base in output.parents or output in base.parents:
        raise BuildError(
            f"refusing: output {output} aliases or overlaps the stock base "
            f"platform {base}")


def _publish(staging: Path, output: Path) -> None:
    """Publish staging as output by rename; replace a marked output
    transactionally (the old owned output is removed within the transaction)."""
    if not output.exists():
        os.rename(staging, output)
        return
    old = Path(tempfile.mkdtemp(prefix=f".{output.name}.old-",
                                dir=output.parent))
    old.rmdir()  # reserve a unique sibling name, then use it for the rename
    os.rename(output, old)
    try:
        os.rename(staging, output)
    except BaseException:
        os.rename(old, output)  # roll back to the previous owned output
        raise
    shutil.rmtree(old)


def build_platform(aosp_root: Path, base_platform: Path, output: Path,
                   replace: bool = False) -> dict:
    """Compose, validate, and atomically publish android-SysUISdk."""
    aosp_root = Path(aosp_root).resolve()
    base_platform = Path(base_platform).resolve()
    output = Path(output).resolve()
    if not base_platform.is_dir():
        raise BuildError(f"base platform not found: {base_platform}")
    for name in _COMPOSED_BASE_NAMES:
        if not (base_platform / name).is_file():
            raise BuildError(f"base platform missing {name}: {base_platform}")
    _refuse_alias(output, base_platform)
    if output.exists():
        if not replace:
            raise BuildError(
                f"output already exists: {output} (pass --replace to replace "
                f"a generator-owned output)")
        if not _is_generator_owned(output):
            raise BuildError(
                f"refusing --replace: {output} is not generator-owned "
                f"(no valid {MARKER_NAME} marker)")
    inputs = resolve_inputs(aosp_root)
    bridge = load_bridge(inputs)
    decls = derive_hidden_aidl_declarations(aosp_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-",
                                    dir=output.parent))
    try:
        _copy_base_platform(base_platform, staging)
        android_bytes = compose_android_jar(
            base_platform / "android.jar", inputs["framework_jar"],
            inputs["framework_res_apk"], bridge)
        core_bytes = compose_core_modules_jar(
            base_platform / "core-for-system-modules.jar", bridge)
        aidl_text = compose_framework_aidl(
            (base_platform / "framework.aidl").read_text(encoding="utf-8"),
            decls)
        (staging / "android.jar").write_bytes(android_bytes)
        (staging / "core-for-system-modules.jar").write_bytes(core_bytes)
        (staging / "framework.aidl").write_text(aidl_text, encoding="utf-8",
                                                 newline="\n")
        pkg = staging / "package.xml"
        if not pkg.is_file():
            raise BuildError(f"base platform missing package.xml: "
                             f"{base_platform}")
        pkg.write_text(rewrite_package_xml(
            pkg.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
        _validate_platform(staging, inputs, bridge, decls, base_platform)
        marker = _build_marker(base_platform, inputs, staging)
        (staging / MARKER_NAME).write_text(
            json.dumps(marker, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        _publish(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"output": str(output), "marker": marker}


# --- main ---------------------------------------------------------------------

def run(argv: list[str] | None = None) -> int:
    """CLI entry: parse, resolve, compose, publish. Returns an exit code."""
    args = build_arg_parser().parse_args(argv)
    try:
        sdk_root = resolve_sdk_root(args.sdk_root, dict(os.environ),
                                    platform.system(), Path.home())
        base = resolve_base_platform(args.base_platform, sdk_root)
        output = resolve_output(args.output, sdk_root)
        report = build_platform(aosp_root=args.aosp_root,
                                base_platform=base, output=output,
                                replace=args.replace)
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    marker = report["marker"]
    print(f"SysUISdk composed: {report['output']}")
    print(f"  base platform : {marker['base_platform']['name']} "
          f"({len(marker['base_platform']['inventory'])} files)")
    print(f"  AOSP inputs   : {len(marker['inputs'])} (exact frozen map)")
    print(f"  bridge entries: {len(BRIDGE_ENTRIES)} in both target jars")
    print(f"  generated     : {len(marker['generated']['inventory'])} files")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
