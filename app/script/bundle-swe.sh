#!/usr/bin/env bash
# Put swe inside Quiver.app, with a Python of its own.
#
#   app/script/bundle-swe.sh <path/to/Quiver.app>
#
# A new Mac has no swe and no Python 3.11, so the app carries both:
#
#   Contents/Resources/swe/bin/swe       a shell shim, what the app runs
#   Contents/Resources/swe/python/       python-build-standalone CPython
#     lib/python3.13/site-packages/quiver   this checkout's src/quiver
#
# swe needs nothing outside the standard library, so the package is copied
# in as source rather than pip-installed: no build backend, no network
# beyond the one Python download, and no console script with an absolute
# shebang that breaks when the app moves. Bytecode is compiled here and the
# shim runs with -B, so Python never writes __pycache__ into the signed
# bundle.
#
# The download is pinned by version and checksum and kept in
# QUIVER_PYTHON_CACHE (default ~/.quiver/cache/build/python), so rebuilds
# don't fetch it again. It matches the host architecture, as the swift build
# does. build-app.sh signs the binaries afterwards.
set -euo pipefail

bundle="${1:?usage: bundle-swe.sh <path/to/Quiver.app>}"
here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "$here/.." && pwd)"

release="20261003"
python_version="3.13.16"
case "$(uname -m)" in
  arm64)
    triple="aarch64-apple-darwin"
    sha256="9e01f63bbb08576cd9c8bc2d0564d098cb30c8453a0cd4bcf6aef458f6d2a147"
    ;;
  x86_64)
    triple="x86_64-apple-darwin"
    sha256="b4dad38ba6a344555ccb71a1b08caad0a6c0dda88c5803658bc95bd7f04e9f5c"
    ;;
  *) echo "bundle-swe: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
archive="cpython-${python_version}+${release}-${triple}-install_only_stripped.tar.gz"
url="https://github.com/astral-sh/python-build-standalone/releases/download/${release}/${archive//+/%2B}"

cache="${QUIVER_PYTHON_CACHE:-$HOME/.quiver/cache/build/python}"
mkdir -p "$cache"
tarball="$cache/$archive"
verify() { [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$sha256" ]; }
if [ ! -f "$tarball" ] || ! verify "$tarball"; then
  echo "bundle-swe: downloading Python $python_version ($triple)" >&2
  # A download of its own, renamed into place only once verified: several
  # sessions build at once, and a shared temporary name let one build
  # overwrite or move another's half-written file.
  partial="$(mktemp "$cache/$archive.XXXXXX")"
  if ! curl --fail --location --silent --show-error --retry 3 -o "$partial" "$url"; then
    rm -f "$partial"
    echo "bundle-swe: couldn't download $url" >&2
    exit 1
  fi
  if ! verify "$partial"; then
    rm -f "$partial"
    echo "bundle-swe: checksum mismatch for $archive" >&2
    exit 1
  fi
  mv -f "$partial" "$tarball"
fi

dest="$bundle/Contents/Resources/swe"
rm -rf "$dest"
mkdir -p "$dest/bin"
tar -xzf "$tarball" -C "$dest"   # unpacks to $dest/python

# Trim what swe never uses: Tk, the test suite, pip, headers, the static
# library, the scripts that would carry absolute shebangs, and the
# libpython dylib, which is only for embedding (the interpreter binary has
# Python linked in statically; check with otool -L).
py="$dest/python"
stdlib="$py/lib/python3.13"
rm -rf "$py/include" "$py/share" "$py/lib/pkgconfig" "$py"/lib/libpython* \
       "$py"/lib/itcl* "$py"/lib/tcl* "$py"/lib/tk* "$py"/lib/thread* \
       "$py"/lib/libtcl* "$py"/lib/libtk* \
       "$stdlib"/config-3.13-* "$stdlib/test" "$stdlib/idlelib" \
       "$stdlib/tkinter" "$stdlib/turtledemo" "$stdlib/ensurepip" \
       "$stdlib"/lib-dynload/_tkinter* "$stdlib"/site-packages/pip*
find "$py/bin" -mindepth 1 ! -name 'python3' ! -name 'python3.13' -exec rm -rf {} +
find "$py" -name '__pycache__' -type d -prune -exec rm -rf {} +

# The package, without anything a previous run left behind.
site="$stdlib/site-packages"
mkdir -p "$site"
cp -R "$repo/src/quiver" "$site/quiver"
find "$site/quiver" -name '__pycache__' -type d -prune -exec rm -rf {} +

# unchecked-hash: the .pyc files are trusted as they are, so nothing
# compares source timestamps or rewrites them at run time.
"$py/bin/python3" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$stdlib" >/dev/null

cat > "$dest/bin/swe" <<'SHIM'
#!/bin/sh
# swe as bundled with Quiver.app. -I keeps the user's PYTHONPATH and site
# packages out; -B keeps Python from writing into the signed bundle.
root="$(cd "$(dirname "$0")/.." && pwd -P)"
exec "$root/python/bin/python3" -I -B -c \
  'import sys; sys.argv[0] = "swe"; from quiver.cli import main; sys.exit(main())' "$@"
SHIM
chmod 755 "$dest/bin/swe"

# Fails the build if the bundled swe can't start, in a home of its own so
# nothing touches the builder's ~/.quiver.
probe_home="$(mktemp -d)"
trap 'rm -rf "$probe_home"' EXIT
if ! out="$(HOME="$probe_home" "$dest/bin/swe" version --json 2>&1)" \
   || ! printf '%s' "$out" | grep -q '"name": *"swe"'; then
  echo "bundle-swe: the bundled swe didn't start: $out" >&2
  exit 1
fi
