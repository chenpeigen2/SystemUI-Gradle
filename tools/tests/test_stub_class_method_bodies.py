#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for build_sysuisdk.stub_class_method_bodies (MockableJarTransform 兼容性).

背景：AGP 的 MockableJarGenerator 替换方法体时不清理 tryCatchBlock，COMPUTE_FRAMES
在悬空 handler label 上 NPE（Cannot read field "outgoingEdges" because
"handlerRangeBlock" is null）。合入 android.jar 的真实 AOSP 字节码触发该 bug。
本工具把方法体打成标准 SDK stub 形态（与 android-35 android.jar 一致）。

测试独立解析 class 文件（不复用被测实现的解析器），期望值来自标准 SDK stub 形态
（android-35 android.jar 的 <init>: super() + throw "Stub!"）。
"""
import sys
import struct
import unittest
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import build_sysuisdk as b  # noqa: E402

_FIXTURE = _TOOLS / "tests" / "resources" / "stub_fixture.class"


# --- 独立的 class 文件解析器（测试自用） -------------------------------------

def _parse_methods(class_bytes: bytes):
    """返回 (方法列表, 字段名列表)；方法项 = (name, desc, access, code, exc_len, subn)"""
    off = 8  # magic + minor + major
    cp_count = struct.unpack_from(">H", class_bytes, off)[0]
    off += 2
    cp = {}
    i = 1
    while i < cp_count:
        tag = class_bytes[off]
        start = off
        off += 1
        if tag == 1:  # Utf8
            ln = struct.unpack_from(">H", class_bytes, off)[0]
            off += 2 + ln
            cp[i] = class_bytes[start + 3:start + 3 + ln].decode("utf-8")
        elif tag in (7, 8, 16, 19, 20):
            off += 2
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            off += 4
        elif tag == 15:
            off += 4
        elif tag in (5, 6):
            off += 8
            i += 1
        else:
            raise AssertionError(f"unknown cp tag {tag}")
        i += 1
    off += 2 + 2 + 2  # access, this, super
    ifc = struct.unpack_from(">H", class_bytes, off)[0]
    off += 2 + 2 * ifc

    def skip_attrs(o):
        n = struct.unpack_from(">H", class_bytes, o)[0]
        o += 2
        for _ in range(n):
            o += 2
            ln = struct.unpack_from(">I", class_bytes, o)[0]
            o += 4 + ln
        return o

    nfields = struct.unpack_from(">H", class_bytes, off)[0]
    off += 2
    field_names = []
    for _ in range(nfields):
        _a, fni, _fdi = struct.unpack_from(">HHH", class_bytes, off)
        field_names.append(cp.get(fni))
        off += 6
        off = skip_attrs(off)

    nmeth = struct.unpack_from(">H", class_bytes, off)[0]
    off += 2
    out = []
    for _ in range(nmeth):
        access, ni, di = struct.unpack_from(">HHH", class_bytes, off)
        off += 6
        nattr = struct.unpack_from(">H", class_bytes, off)[0]
        off += 2
        code = None
        exc_len = None
        subn = 0
        for _ in range(nattr):
            ani = struct.unpack_from(">H", class_bytes, off)[0]
            off += 2
            aln = struct.unpack_from(">I", class_bytes, off)[0]
            off += 4
            if cp.get(ani) == "Code":
                # Code: max_stack u2, max_locals u2, code_len u4, code, exc_len u2 ...
                clen = struct.unpack_from(">I", class_bytes, off + 4)[0]
                code = class_bytes[off + 8:off + 8 + clen]
                exc_len = struct.unpack_from(">H", class_bytes, off + 8 + clen)[0]
                sub_off = off + 8 + clen + 2 + exc_len * 8
                subn = struct.unpack_from(">H", class_bytes, sub_off)[0]
            off += aln
        out.append((cp.get(ni), cp.get(di), access, code, exc_len, subn))
    return out, field_names


def _methods(class_bytes: bytes):
    return _parse_methods(class_bytes)[0]


def _decode_prefix_ops(code: bytes, count: int):
    """解出前 count 条指令的 (opcode, 参数字节) —— 仅测试断言用。"""
    ops = []
    i = 0
    fixed = {0x2a: 0, 0xb7: 2, 0xbb: 2, 0x59: 0, 0x12: 1, 0x13: 2, 0xb6: 2,
             0xb1: 0, 0xbf: 0, 0xb0: 0, 0xac: 0, 0xad: 0, 0xae: 0, 0xaf: 0}
    while i < len(code) and len(ops) < count:
        op = code[i]
        ln = fixed[op]
        ops.append((op, code[i + 1:i + 1 + ln]))
        i += 1 + ln
    return ops


class StubClassMethodBodiesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig = _FIXTURE.read_bytes()
        cls.stubbed = b.stub_class_method_bodies(cls.orig)

    def test_fixture_has_real_bodies_first(self):
        """夹具本身必须带 try-catch（红：未打桩前存在 exception table）。"""
        methods = {m[0]: m for m in _methods(self.orig)}
        self.assertGreater(methods["compute"][4], 0)

    def test_fields_section_preserved(self):
        """字段区不得被改写/丢失（tail 拼接回归防护）。"""
        _, of = _parse_methods(self.orig)
        _, sf = _parse_methods(self.stubbed)
        self.assertEqual(of, sf)
        self.assertIn("x", sf)

    def test_wide_signature_methods_stubbed(self):
        methods = {m[0]: m for m in _methods(self.stubbed)}
        self.assertEqual(methods["wide"][4], 0)

    def test_non_ctor_body_becomes_stub_throw(self):
        """普通方法体 = 标准 SDK stub：new RuntimeException("Stub!") + athrow。"""
        methods = {m[0]: m for m in _methods(self.stubbed)}
        name, desc, access, code, exc, _ = methods["compute"]
        self.assertEqual(exc, 0, "exception table must be empty")
        ops = _decode_prefix_ops(code, 5)
        self.assertEqual([o for o, _ in ops], [0xbb, 0x59, 0x12, 0xb7, 0xbf])
        self.assertEqual(len(ops[0][1]), 2, "NEW 后跟 u2 常量池索引")
        self.assertEqual(len(ops[2][1]), 1, "ldc 后跟 u1 常量池索引（标准 SDK 形态）")

    def test_ctor_keeps_super_call_then_stub(self):
        """<init> 保留 super() 前缀后接 throw stub（与 android-35 形态一致）。"""
        methods = {m[0]: m for m in _methods(self.stubbed)}
        name, desc, access, code, exc, _ = methods["<init>"]
        self.assertEqual(exc, 0)
        ops = _decode_prefix_ops(code, 7)
        # aload_0; invokespecial super.<init>; new; dup; ldc; invokespecial; athrow
        self.assertEqual(ops[0][0], 0x2a)
        self.assertEqual(ops[1][0], 0xb7)
        self.assertEqual([o for o, _ in ops[2:]], [0xbb, 0x59, 0x12, 0xb7, 0xbf])

    def test_wide_signature_methods_stubbed(self):
        methods = {m[0]: m for m in _methods(self.stubbed)}
        self.assertEqual(methods["wide"][4], 0)

    def test_idempotent(self):
        again = b.stub_class_method_bodies(self.stubbed)
        self.assertEqual(again, self.stubbed, "stub pass must be idempotent")

    def test_structure_preserved(self):
        """方法集/签名/访问标志不得变化。"""
        o = [(n, d, a) for n, d, a, _, _, _ in _methods(self.orig)]
        s = [(n, d, a) for n, d, a, _, _, _ in _methods(self.stubbed)]
        self.assertEqual(o, s)

    def test_no_stackmap_survives(self):
        """打桩后的 Code 属性不得携带任何子属性（旧帧/行号/变量表全部清空）。"""
        for name, desc, access, code, exc, subn in _methods(self.stubbed):
            if code is not None:
                self.assertEqual(subn, 0, f"{name} still carries Code sub-attributes")

    def test_non_class_magic_entries_pass_through(self):
        """组合层只对 CAFEBABE 魔数的真实 class 打桩；其它字节原样透传。"""
        import io as _io
        import zipfile as _zip
        import tempfile
        fake = b"not-a-real-class-file"
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)

            def make(path, entries):
                with _zip.ZipFile(path, "w") as zf:
                    for n, d in entries.items():
                        zf.writestr(n, d)
                return path

            base = make(td / "base.jar", {"stock/Fake.class": fake})
            fw = make(td / "fw.jar", {"android/Fake.class": fake})
            res = make(td / "res.apk", {"resources.arsc": b"arsc"})
            out = b.compose_android_jar(base, fw, res, {})
            with _zip.ZipFile(_io.BytesIO(out)) as zf:
                self.assertEqual(zf.read("stock/Fake.class"), fake)
                self.assertEqual(zf.read("android/Fake.class"), fake)

    def test_compose_android_jar_stubs_overlay_classes(self):
        """compose_android_jar 产物中类的方法体必须已 stub 化（公开接缝）。"""
        import tempfile, zipfile
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)

            def make(path, entries):
                with zipfile.ZipFile(path, "w") as zf:
                    for n, d in entries.items():
                        zf.writestr(n, d)
                return path

            base = make(td / "base.jar", {"android/Keep.class": self.orig})
            fw = make(td / "fw.jar", {"android/Overlay.class": self.orig})
            res = make(td / "res.apk",
                       {"resources.arsc": b"arsc", "res/x/y.xml": b"<y/>"})
            out = b.compose_android_jar(base, fw, res, {})
            with zipfile.ZipFile(td / "out.jar", "w") as zf:
                zf.writestr("out.jar", out)  # 仅占位，直接解 bytes
            import io
            with zipfile.ZipFile(io.BytesIO(out)) as zf:
                for n in ("android/Keep.class", "android/Overlay.class"):
                    cls = zf.read(n)
                    for _, _, _, code, exc, _ in _methods(cls):
                        if code is not None:
                            self.assertEqual(exc, 0, f"{n} has live exception table")


if __name__ == "__main__":
    unittest.main()
