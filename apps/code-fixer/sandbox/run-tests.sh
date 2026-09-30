#!/bin/sh
# The workspace arrives read-only at /src. Tests run on a scratch copy in /work (a tmpfs),
# so nothing they write survives the container.
set -e
cp -R /src/. /work/
cd /work
exec "$@"
