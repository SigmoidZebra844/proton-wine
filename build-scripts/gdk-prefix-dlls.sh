#!/bin/bash
# GDK-Proton: put the extra native DLLs into the prefix's system32.
#
# This is the bionic-layer equivalent of GDK-Proton-Custom (LukasPAH, tag release-11-7):
#   * build.sh downloads a set of msys2 mingw64 DLLs (the libcurl stack, with
#     libcurl-4.dll renamed to XCurl.dll, which GDK titles link against), and
#   * patch_proton.sh makes the proton script copy them into
#     drive_c/windows/system32 of every prefix it creates.
# A Winlator container gets its prefix from the layer's prefixPack.txz instead of a
# proton script, so the same files are appended to that archive under
# .wine/drive_c/windows/system32. The existing entries are left byte-for-byte as-is.
#
# The GDK builtins that patch_proton.sh also copies (xgameruntime,
# windows.ui.core.textinput, windows.devices.enumeration,
# Microsoft.WindowsAppRuntime.Bootstrap) are Wine modules of this layer; wineboot
# puts them in system32 itself on the container's first boot (wine.inf: 11,,*).
#
# The msys2 package versions are the ones release-11-7's build.sh pins, and each
# download is sha256-checked so a repo change can never alter what ships.
#
# usage: gdk-prefix-dlls.sh <prefixPack.txz>   (the archive is rewritten in place)
set -euo pipefail

PACK="$(realpath "$1")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# package | sha256 of mingw-w64-x86_64-<package>-any.pkg.tar.zst | DLLs taken from it
PACKAGES=(
  "brotli-1.2.0-1|f5f2f7e723a08378241d15f0537386950c1a48e2d82bc47bedd632bd61852aba|libbrotlicommon libbrotlidec"
  "c-ares-1.34.8-1|34c1b4c196f52bd3f0c0b4ac652fabd2325086183c000baa10f721c815b29542|libcares-2"
  "curl-8.20.0-1|7a5fc57f4275c5824dfe9704b64032fbd05c09722a003c7be507a56238c01f6f|libcurl-4"
  "gettext-runtime-1.0-1|be68d7f260633284b910c588c6d82ee304a81c8817a686d2cd9df83f872c27af|libintl-8 libasprintf-0"
  "libiconv-1.19-1|21e334d0911f25de75d3e18e0697648bcecfa9658256d600cad0827d719c2f35|libiconv-2 libcharset-1"
  "libidn2-2.3.8-4|f98a157446f9ac35465022e329cdaf6169b0c9fa19837e9d80aff9d4e5109013|libidn2-0"
  "libpsl-0.21.5-3|2a86f7ce3cd3fabff7eb8e5c0d3eb1134808881a44c3838e1b518b75cb402e35|libpsl-5"
  "libssh2-1.11.1-2|83d0b99f79e930b30eb642382662686b696d64ceb927c2606debd79cb6ff670a|libssh2-1"
  "libunistring-1.4.2-1|c940cf52fb2b579baa596d3c1d911f7544cbf90f8bd19c260377aa115c65b059|libunistring-5"
  "nghttp2-1.69.0-1|2dbd0ed693918d1c14f97b33ab738fc9385995ddb82bc47ae3e535ce992829df|libnghttp2-14"
  "nghttp3-1.9.0-1|5472f5ae75ae28d9d80ba12645036721b7c7b135cfb45251bf55b22f5baa0304|libnghttp3-9"
  "ngtcp2-1.22.1-1|34a1ba4d116b3ec6017de9333a0244bb810a4b8b7ef52595dbeae687657b4c7a|libngtcp2-16 libngtcp2_crypto_ossl-0 libngtcp2_crypto_gnutls-8"
  "openssl-3.6.2-2|79c32d7f2dd8aacaaf2a787ca19938f2a16a96340afacd8af86bd976cb66a540|libcrypto-3-x64 libssl-3-x64"
  "zlib-1.3.2-2|9e75842a070ba648e986e12424e1c92c9d7d77200e85f6a34eeb600819f2e694|zlib1"
  "zstd-1.5.7-2|1add6705b344664f6aca108c85f79ab5bdd9e1162662bb06a4cf40a34f6e0907|libzstd"
)

SYS32=".wine/drive_c/windows/system32"
DLLDIR="$WORK/root/$SYS32"
mkdir -p "$DLLDIR"

for row in "${PACKAGES[@]}"; do
  IFS='|' read -r pkg sha dlls <<< "$row"
  file="mingw-w64-x86_64-$pkg-any.pkg.tar.zst"
  echo "GDK: fetching $file"
  curl -sSfL --retry 5 --retry-delay 10 -o "$WORK/$file" "https://repo.msys2.org/mingw/mingw64/$file"
  echo "$sha  $WORK/$file" | sha256sum -c - >/dev/null || { echo "FATAL: sha256 mismatch for $file"; exit 1; }
  members=()
  for d in $dlls; do members+=("mingw64/bin/$d.dll"); done
  tar --zstd -xf "$WORK/$file" -C "$DLLDIR" --transform 's/mingw64\/bin\///' "${members[@]}"
done

# Same two fix-ups as build.sh.
cp "$DLLDIR/libngtcp2_crypto_ossl-0.dll" "$DLLDIR/libngtcp2_crypto_ossl.dll"
mv "$DLLDIR/libcurl-4.dll" "$DLLDIR/XCurl.dll"

# The file list patch_proton.sh copies (its xgameruntime.dll.threading entry names a
# file that build.sh never produces, and the four GDK builtins come from wineboot).
EXPECTED="libasprintf-0 libbrotlicommon libbrotlidec libcares-2 libcharset-1 libcrypto-3-x64
          libiconv-2 libidn2-0 libintl-8 libnghttp2-14 libnghttp3-9 libngtcp2_crypto_gnutls-8
          libngtcp2_crypto_ossl libngtcp2_crypto_ossl-0 libngtcp2-16 libpsl-5 libssh2-1
          libssl-3-x64 libunistring-5 libzstd zlib1 XCurl"
for d in $EXPECTED; do
  [ -s "$DLLDIR/$d.dll" ] || { echo "FATAL: $d.dll missing after extraction"; exit 1; }
done

# Refuse to shadow anything the prefixPack already carries.
if xz -dc "$PACK" | tar -t | grep -qiE "^$SYS32/($(echo $EXPECTED | sed 's/ /|/g'))\.dll$"; then
  echo "FATAL: prefixPack already contains one of the GDK DLLs"; exit 1
fi

# Append to the existing archive (same mode as its other entries) and recompress.
xz -dc "$PACK" > "$WORK/prefixPack.tar"
(cd "$WORK/root" && find "$SYS32" -name '*.dll' | LC_ALL=C sort > "$WORK/list")
tar -rf "$WORK/prefixPack.tar" -C "$WORK/root" --owner=0 --group=0 --mode=0700 -T "$WORK/list"
xz -T0 -9 -c "$WORK/prefixPack.tar" > "$PACK"

echo "GDK: added $(wc -l < "$WORK/list") DLLs to $SYS32 in $(basename "$PACK"):"
sed 's/^/  /' "$WORK/list"
