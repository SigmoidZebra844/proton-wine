#!/usr/bin/env python3
"""Post-install binary verification for a bionic Proton/GE layer tree.

Runs in CI right after `build-step-*.sh --install`, BEFORE packaging. It reads the
actual compiled binaries in OUTPUT_DIR and refuses the layer if any shipped feature
is missing. A green compile is not proof: a patch that stops applying, a configure
flag that flips, or an `#ifdef` that compiles a feature out all produce a complete,
bootable, silently-degraded layer. Every check below is a feature that once shipped
broken or nearly did (see the git log of build-scripts/).

usage: verify-layer.py OUTPUT_DIR --arch aarch64|x86_64 --api 28|35 --pages 4k|16k [--ge] [--gdk]

Exit 0 = every check passed. Exit 1 = at least one FATAL. stdlib only.
"""
import argparse
import os
import struct
import sys

fails = 0


def report(ok, name, detail=""):
    global fails
    print("  %-5s %s%s" % ("ok" if ok else "FATAL", name, ("  [%s]" % detail) if detail else ""))
    if not ok:
        fails += 1
    return ok


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def u16(s):
    return s.encode("utf-16-le")


# ---------------------------------------------------------------- ELF helpers
def elf_load_aligns(data):
    """Set of p_align values of PT_LOAD segments (ELF64 little-endian)."""
    if data[:4] != b"\x7fELF" or data[4] != 2:
        return None
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x36)
    aligns = set()
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type = struct.unpack_from("<I", data, off)[0]
        if p_type == 1:  # PT_LOAD
            aligns.add(struct.unpack_from("<Q", data, off + 0x30)[0])
    return aligns


def elf_android_api(data):
    """API level from the .note.android.ident note (name 'Android', type 1), or None."""
    if data[:4] != b"\x7fELF" or data[4] != 2:
        return None
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    if not e_shoff or e_shstrndx >= e_shnum:
        return None
    def sh(i):
        off = e_shoff + i * e_shentsize
        name, typ = struct.unpack_from("<II", data, off)
        offset, size = struct.unpack_from("<QQ", data, off + 0x18)
        return name, typ, offset, size
    _, _, stroff, strsize = sh(e_shstrndx)
    strtab = data[stroff:stroff + strsize]
    for i in range(e_shnum):
        name, typ, offset, size = sh(i)
        if typ != 7:  # SHT_NOTE
            continue
        end = strtab.find(b"\0", name)
        if strtab[name:end] != b".note.android.ident":
            continue
        namesz, descsz, ntype = struct.unpack_from("<III", data, offset)
        nm = data[offset + 12:offset + 12 + namesz]
        if nm.rstrip(b"\0") == b"Android" and ntype == 1:
            desc_off = offset + 12 + ((namesz + 3) & ~3)
            return struct.unpack_from("<I", data, desc_off)[0]
    return None


def elf_section_size(data, wanted):
    """sh_size of a named ELF64 section, or None."""
    if data[:4] != b"\x7fELF" or data[4] != 2:
        return None
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    if not e_shoff or e_shstrndx >= e_shnum:
        return None
    stroff, strsize = struct.unpack_from("<QQ", data, e_shoff + e_shstrndx * e_shentsize + 0x18)
    strtab = data[stroff:stroff + strsize]
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name = struct.unpack_from("<I", data, off)[0]
        if strtab[name:strtab.find(b"\0", name)] == wanted:
            return struct.unpack_from("<Q", data, off + 0x20)[0]
    return None


# --------------------------------------------------------------------- checks
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--arch", choices=["aarch64", "x86_64"], required=True)
    ap.add_argument("--api", type=int, required=True, help="expected .note.android.ident API level")
    ap.add_argument("--pages", choices=["4k", "16k"], required=True)
    ap.add_argument("--ge", action="store_true", help="expect the GE-Proton game-fixes tier")
    ap.add_argument("--gdk", action="store_true", help="expect the GDK-Proton WineGDK modules")
    a = ap.parse_args()

    root = a.output_dir
    arm64ec = a.arch == "aarch64"
    unix = os.path.join(root, "lib/wine", a.arch + "-unix")
    pe = os.path.join(root, "lib/wine", ("aarch64" if arm64ec else "x86_64") + "-windows")
    pe32 = os.path.join(root, "lib/wine/i386-windows")

    print("== verify-layer: %s arch=%s api=%d pages=%s ge=%s gdk=%s" % (root, a.arch, a.api, a.pages, a.ge, a.gdk))

    # 1. Tree shape. A skeleton tree (failed make) has bin/ + share/ but few or no DLLs.
    print("-- tree shape")
    n_pe = len([f for f in os.listdir(pe)]) if os.path.isdir(pe) else 0
    n_pe32 = len([f for f in os.listdir(pe32)]) if os.path.isdir(pe32) else 0
    n_unix = len([f for f in os.listdir(unix)]) if os.path.isdir(unix) else 0
    report(n_pe >= 800, "%s has a full DLL set" % os.path.relpath(pe, root), "%d entries" % n_pe)
    report(n_pe32 >= 800, "%s has a full DLL set" % os.path.relpath(pe32, root), "%d entries" % n_pe32)
    report(n_unix >= 25, "%s has the unix libs" % os.path.relpath(unix, root), "%d entries" % n_unix)
    report(os.path.isfile(os.path.join(root, "share/wine/wine.inf")), "share/wine/wine.inf present")
    # Wine-11 scripts symlink bin/wine to the unix loader; Wine-10's install ships the
    # loader binary itself in bin/. Either is a working layer; absent is not.
    wine = os.path.join(root, "bin/wine")
    if os.path.islink(wine):
        report(os.readlink(wine) == "../lib/wine/%s-unix/wine" % a.arch, "bin/wine loader", "symlink -> " + os.readlink(wine))
    else:
        report(read(wine)[:4] == b"\x7fELF", "bin/wine loader", "regular ELF" if os.path.isfile(wine) else "missing")
    # 2. ELF properties of the unix side: page alignment + Android API level.
    print("-- ELF (unix side)")
    elfs = [os.path.join(unix, f) for f in sorted(os.listdir(unix))] if os.path.isdir(unix) else []
    elfs = [p for p in elfs if os.path.isfile(p) and not os.path.islink(p)]
    bad_align, bad_api, seen = [], [], 0
    for p in elfs:
        d = read(p)
        al = elf_load_aligns(d)
        if al is None:
            continue
        seen += 1
        # wine-preloader is a static, custom-linked binary (fixed -Ttext, no LDFLAGS) and has
        # shipped 4 KB-aligned in every 16 KB build to date (v1..v7, sdk35 included); the
        # dynamic objects are what the 16 KB linker flag governs. Follow-up: align it too.
        # (x86_64 names it wine64-preloader.)
        if a.pages == "16k" and al != {0x4000} and os.path.basename(p) not in ("wine-preloader", "wine64-preloader"):
            bad_align.append("%s=%s" % (os.path.basename(p), ",".join(hex(x) for x in sorted(al))))
        api = elf_android_api(d)
        if api is not None and api != a.api:
            bad_api.append("%s=%d" % (os.path.basename(p), api))
    report(seen >= 25, "parsed unix ELF objects", str(seen))
    if a.pages == "16k":
        report(not bad_align, "every unix ELF except wine-preloader is 16 KB page aligned (p_align 0x4000)", " ".join(bad_align[:6]))
    for f in ("ntdll.so", "win32u.so"):
        api = elf_android_api(read(os.path.join(unix, f)))
        report(api == a.api, "%s .note.android.ident API == %d" % (f, a.api), str(api))
    report(not bad_api, "no unix .so built against a different API level", " ".join(bad_api[:6]))

    # 3. Android/bionic patches that must be IN the binaries.
    print("-- Android patches (compiled in)")
    ntdll_so = read(os.path.join(unix, "ntdll.so"))
    report(b"WINEESYNC" in ntdll_so, "ntdll.so: esync (WINEESYNC)")
    report(b"esync: up and running" in read(os.path.join(root, "bin/wineserver")),
           "wineserver: esync server side")
    report(b"WINE_FAST_YIELD" in ntdll_so, "ntdll.so: fast-yield gate")
    report(b"WINEVMEMMAXSIZE" in ntdll_so, "ntdll.so: WINEVMEMMAXSIZE address-space cap")
    report(b"C.UTF-8" in ntdll_so, "ntdll.so: C.UTF-8 bionic locale bring-up")
    if arm64ec:
        report(b"load_unixlib_by_name" in ntdll_so, "ntdll.so: FEX unixlib load-by-name loader")
    report(b"WINE_ANDROID_GATEWAY" in read(os.path.join(unix, "nsiproxy.so")),
           "nsiproxy.so: EA default-route fix (WINE_ANDROID_GATEWAY)")
    xin = b"transient wait failure in the update thread"
    report(xin in read(os.path.join(pe, "xinput1_3.dll")), "xinput1_3.dll: WAIT_FAILED retry (controller-dies fix)")
    report(xin in read(os.path.join(pe32, "xinput1_3.dll")), "i386 xinput1_3.dll: WAIT_FAILED retry")
    report(b"WINE_FROM_ANDROID_CLIPBOARD" in read(os.path.join(unix, "win32u.so")),
           "win32u.so: Android clipboard bridge")

    # 4. In-tree features (committed source, not patches) that a bad merge can lose.
    print("-- in-tree features")
    if arm64ec:
        # RtlIsEcCode bounds guard `if (ptr >> 47) return FALSE;` compiles to lsr x8,x0,#47
        # (0xd36ffc08). The -4 layer (no guard) had no such instruction in ntdll.dll.
        report(b"\x08\xfc\x6f\xd3" in read(os.path.join(pe, "ntdll.dll")),
               "ntdll.dll: RtlIsEcCode bounds guard (Denuvo / NFS Heat)")
    # win32u's implementation is the unix .so; font_handles[32768] (16 B each) lives in its .bss.
    w32so = read(os.path.join(unix, "win32u.so"))
    bss = elf_section_size(w32so, b".bss")
    report(bss is not None and bss >= 0x80000, "win32u.so: font-handle cap 32768 (.bss >= 512 KiB)",
           hex(bss) if bss is not None else "no .bss")
    report(b"WINE_XP_FRAMES" in w32so, "win32u.so: XP window frames")
    for d, label in ((pe, "explorer.exe"), (pe32, "i386 explorer.exe")):
        ex = read(os.path.join(d, "explorer.exe"))
        report(u16("WINE_TASKBAR_STYLE") in ex, "%s: XP taskbar" % label)
        report(u16("--end-session --force --kill --shutdown") in ex, "%s: Turn Off ends the desktop" % label)
    for d, label in ((pe, "winexp.msstyles"), (pe32, "i386 winexp.msstyles")):
        ms = read(os.path.join(d, "winexp.msstyles"))
        report(len(ms) > 1000000 and u16("SILVERDARK_INI") in ms, "%s: XP visual style" % label, str(len(ms)))
    report(b"theme_reload_cs" in read(os.path.join(pe, "uxtheme.dll")), "uxtheme.dll: live theme reload")

    # 5. DirectAudio v1.3.2 (opt-in driver): complete 3-file set + the 1.3.2 mic marker.
    print("-- DirectAudio")
    da_so = read(os.path.join(unix, "winedirectaudio.so"))
    report(b"BANNER_AUDIO_DIRECT_MIC" in da_so, "winedirectaudio.so: v1.3.2 (mic capture marker)")
    report(os.path.isfile(os.path.join(pe, "winedirectaudio.drv")), "winedirectaudio.drv (64-bit PE) present")
    report(os.path.isfile(os.path.join(pe32, "winedirectaudio.drv")), "winedirectaudio.drv (i386 PE) present")

    # 6. GE-Proton game-fixes tier (only on GE layers).
    if a.ge:
        print("-- GE game-fixes tier")
        report(b"WINE_NO_OPEN_FILE_SEARCH" in ntdll_so, "ntdll.so: pso2 hack")
        report(b"Star Citizen" in read(os.path.join(pe, "user32.dll")), "user32.dll: Star Citizen msgbox silence")
        report(b"EAC_LAUNCHERDIR" in ntdll_so, "ntdll.so: EAC 60101 timeout")
        report(b"WINE_BLACK_DESERT_KEEP_FULLSCREEN" in w32so, "win32u.so: Black Desert keep-fullscreen on focus loss")
        # max-payne-cpu-detection is #ifdef __i386__ in the PE loader: only the wow64 ntdll carries it.
        report(u16("rlmfc.dll") in read(os.path.join(pe32, "ntdll.dll")), "i386 ntdll.dll: Max Payne rlmfc.dll CPU detection fix")
        if not arm64ec:
            # Both are x86_64-only: ai-limit is `#if __x86_64__ && !__arm64ec__` in the PE loader,
            # nascar25 lives in the seccomp SIGSYS path of unix/signal_x86_64.c (its SteamGameId
            # check sits in install_bpf, so the string is only there if HAVE_SECCOMP compiled it).
            report(u16("AI-LIMIT.exe") in read(os.path.join(pe, "ntdll.dll")), "ntdll.dll: AI LIMIT DX12 compute-shader fallback")
            report(b"3873970" in ntdll_so, "ntdll.so: NASCAR 25 protector repair (seccomp SIGSYS)")

    # 7. GDK-Proton: the WineGDK modules GDK-Proton-Custom release-11-7 ships (only on GDK layers).
    if a.gdk:
        print("-- GDK-Proton (WineGDK modules)")
        machine = 0xAA64 if arm64ec else 0x8664  # ARM64X images carry ARM64 in the file header
        for m in ("xgameruntime.dll", "Microsoft.WindowsAppRuntime.Bootstrap.dll", "windows.ui.core.textinput.dll",
                  "windows.devices.enumeration.dll", "twinapi.appcore.dll", "wintypes.dll"):
            for d, label, want in ((pe, "", machine), (pe32, "i386 ", 0x14C)):
                data = read(os.path.join(d, m))
                got = None
                if data[:2] == b"MZ":
                    got = struct.unpack_from("<H", data, struct.unpack_from("<I", data, 0x3C)[0] + 4)[0]
                report(got == want, "%s%s built" % (label, m), "machine %s" % (hex(got) if got is not None else "missing"))
        report(b"InitializeApiImplEx2" in read(os.path.join(pe, "xgameruntime.dll")),
               "xgameruntime.dll: exports InitializeApiImplEx2")
        report(b"MddBootstrapInitialize2" in read(os.path.join(pe, "Microsoft.WindowsAppRuntime.Bootstrap.dll")),
               "Microsoft.WindowsAppRuntime.Bootstrap.dll: exports MddBootstrapInitialize2")
        report(u16("Windows.UI.Text.Core.CoreTextServicesManager") in read(os.path.join(pe, "windows.ui.core.textinput.dll")),
               "windows.ui.core.textinput.dll: WineGDK CoreTextServicesManager")
        report(u16("Windows.ApplicationModel.DataTransfer.DataTransferManager") in read(os.path.join(pe, "twinapi.appcore.dll")),
               "twinapi.appcore.dll: WineGDK DataTransferManager")
        report(b"WINHTTP_OPTION_IPV6_FAST_FALLBACK %lu ignored" in read(os.path.join(pe, "winhttp.dll")),
               "winhttp.dll: IPV6_FAST_FALLBACK accepted (GDK XCurl)")

    print("== verify-layer: %s" % ("PASS" if fails == 0 else "%d FATAL check(s)" % fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
