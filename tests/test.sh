#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PLUGIN="$ROOT/bin/herdr-telegram-attention"

assert_contains() {
  local haystack="$1" needle="$2"
  [[ "$haystack" == *"$needle"* ]] || {
    printf 'Expected output to contain: %s\nActual output:\n%s\n' "$needle" "$haystack" >&2
    exit 1
  }
}

test_spanish_blocked_event() {
  local output
  output="$(HERDR_PLUGIN_EVENT_JSON='{"type":"pane.agent_status_changed","pane_id":"w1:p2","workspace_id":"w1","tab_id":"w1:t1","agent":"codex","agent_status":"blocked","state_change_seq":42}' \
    HERDR_PLUGIN_CONTEXT_JSON='{"workspace_label":"api","workspace_cwd":"/tmp/not-a-repository","terminal_title_stripped":"Approve deployment"}' \
    TELEGRAM_LANGUAGE=es "$PLUGIN" --event --dry-run)"
  assert_contains "$output" 'Herdr: requiere tu atención'
  assert_contains "$output" 'Contexto: codex'
  assert_contains "$output" 'Proyecto: api'
  assert_contains "$output" 'Tarea: Approve deployment'
}

test_non_blocked_event_is_silent() {
  local output
  output="$(HERDR_PLUGIN_EVENT_JSON='{"pane_id":"w1:p2","agent":"claude","agent_status":"done","state_change_seq":43}' \
    TELEGRAM_LANGUAGE=en TELEGRAM_NOTIFY_DONE=true "$PLUGIN" --event --dry-run)"
  [[ -z "$output" ]] || { printf 'Done event must not create an attention incident\n' >&2; exit 1; }
}

test_unhandled_state_is_silent() {
  local output
  output="$(HERDR_PLUGIN_EVENT_JSON='{"pane_id":"w1:p2","agent_status":"working"}' "$PLUGIN" --event --dry-run)"
  [[ -z "$output" ]] || { printf 'Working event must be silent\n' >&2; exit 1; }
}

test_spanish_blocked_event
test_non_blocked_event_is_silent
test_unhandled_state_is_silent
printf 'All tests passed.\n'
