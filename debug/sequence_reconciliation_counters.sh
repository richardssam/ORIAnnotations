#!/usr/bin/env bash
# Sequence-reconciliation convergence counters for an ori_sync plugin log
# (the file pointed to by ORI_SYNC_LOG_FILE).
#
# Usage: debug/sequence_reconciliation_counters.sh <plugin.log>
#
# Baseline captured 2026-08-03 20:05-20:08 on a two-xStudio session with
# three clips in one sequence, before fix-sequence-track-reconciliation:
#
#   INSERT_CHILD sent                   153
#   REPLACE_TIMELINE sent                57
#   sequence track new media (drag path) 152
#   source_ranges-changed rebuilds        58
#   log size                            2.6 MB
#
# manager_clips oscillated 0 -> 2 -> 5 -> 8 -> 5 -> 3 and never settled at
# the real clip count. A converged session broadcasts a bounded number of
# each per edit -- not one per poll -- and manager_clips settles and stays.
# See openspec/changes/fix-sequence-track-reconciliation.

set -euo pipefail
log="${1:?usage: $0 <plugin.log>}"

# rabbitmq_network.py logs both outbound (=== MQ SEND ===) and inbound
# (=== MQ RECV ===) payloads in the same pretty-printed JSON shape, so a
# plain grep for "event": "X" double-counts a peer's own broadcasts together
# with whatever it received from someone else. Track SEND/RECV mode as we
# scan so "sent" only counts this peer's own outbound messages.
count_sent_event() {
    awk -v pat="\"event\": \"$1\"" '
        /=== MQ SEND/ { mode = "send" }
        /=== MQ RECV/ { mode = "recv" }
        mode == "send" && index($0, pat) { count++ }
        END { print count + 0 }
    ' "$log"
}

echo "INSERT_CHILD sent:                    $(count_sent_event 'INSERT_CHILD')"
echo "REPLACE_TIMELINE sent:                $(count_sent_event 'REPLACE_TIMELINE')"
echo "sequence track new media (drag path): $(grep -c 'sequence track new media' "$log" || true)"
echo "sequence new media (bin path):        $(grep -c 'sequence new media:' "$log" || true)"
echo "source_ranges-changed rebuilds:        $(grep -c 'source_ranges changed' "$log" || true)"
echo "log size:                              $(du -h "$log" | cut -f1)"
echo
echo "manager_clips/bin_media progression (last 20 passes):"
grep '\[2F\] sync authority:.*manager_clips=' "$log" | tail -20 || true
echo
echo "no-op ('is it converging?') passes:    $(grep -c 'incremental pass: no track changes' "$log" || true)"
