#!/bin/sh
# Usage: scripts/bump-scrcpy.sh 5.0.2
# Points the Dockerfile at a new scrcpy release. The build verifies the
# release's server checksum from upstream's install_release.sh, so nothing
# else needs changing.
set -eu
version="${1#v}"
case "$version" in
  ''|*[!0-9.]*) echo "usage: $0 <version, e.g. 5.0.2>" >&2; exit 1 ;;
esac
cd "$(dirname "$0")/.."
sed -i "s/^ARG SCRCPY_VERSION=.*/ARG SCRCPY_VERSION=${version}/" Dockerfile
grep -n "^ARG SCRCPY_VERSION=" Dockerfile
