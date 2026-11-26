#!/bin/bash
# TEMPLATE-DISPATCH-STUB — managed by template-sync, do not edit.
# The runner (and the hook contract) lives in ../pre-tool/template/lib/dispatch.sh.
# To add project-specific hooks, place .sh files in ./project/
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HOOK_DIR/../pre-tool/template/lib/dispatch.sh" "$(basename "$HOOK_DIR")"
