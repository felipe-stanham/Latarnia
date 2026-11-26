#!/bin/bash
# Bridge for pre-1.0 dispatchers, which `source` every template hook in order.
# Sorting first (00-), it hands the whole run to the v1 runner before anything reads
# stdin. The v1 runner skips this file. Managed by template-sync — do not edit.
if [ -z "$TEMPLATE_DISPATCH_V1" ]; then
  exec bash "$HOOK_DIR/../pre-tool/template/lib/dispatch.sh" "$(basename "$HOOK_DIR")"
fi
exit 0
