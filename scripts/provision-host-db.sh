#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
require_root
exec python3 "$DOCKERWS_ROOT/scripts/provision_host_db.py" "$@"
