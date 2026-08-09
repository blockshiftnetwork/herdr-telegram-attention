# Telegram Attention Alerts for Herdr

Receive a Telegram message when a Herdr agent enters the semantic `blocked`
state and needs your attention. The plugin uses Herdr's native
`pane.agent_status_changed` event hook: no polling, terminal scraping, or
session restart is involved.

Messages are available in Spanish (`es`), English (`en`), and Portuguese
(`pt`). Spanish is the default. Finished-agent messages are disabled by
default to avoid notification noise. Blocked alerts include the agent, project
label, Git branch when available, terminal task title when available, workspace,
tab, pane, and Herdr-provided reason. They never include terminal output or the
agent conversation.

## Requirements

- Herdr 0.8.0 or newer
- `bash`, `python3`, and `curl`
- A Telegram bot token and a chat ID. Start a conversation with the bot before
  testing it, otherwise Telegram rejects direct messages.

## Install

```bash
herdr plugin install blockshiftnetwork/herdr-telegram-attention
```

For local development, use the checkout instead:

```bash
herdr plugin link /absolute/path/to/herdr-telegram-attention
```

Plugin registration is global for your Herdr user account and does not stop or
restart active sessions or panes.

## Configure securely

Ask Herdr for the stable, user-private configuration directory. It is separate
from the installed plugin checkout, so upgrades do not overwrite credentials.

```bash
config_dir="$(herdr plugin config-dir blockshiftnetwork.telegram-attention)"
mkdir -p "$config_dir"
chmod 700 "$config_dir"
cat > "$config_dir/.env" <<'EOF'
TELEGRAM_BOT_TOKEN=replace-with-your-bot-token
TELEGRAM_CHAT_ID=replace-with-your-chat-id
TELEGRAM_LANGUAGE=es
# Set true only if you also want a message when an agent is done.
TELEGRAM_NOTIFY_DONE=false
# Both are enabled by default. Set false to reduce alert context.
TELEGRAM_INCLUDE_GIT=true
TELEGRAM_INCLUDE_TITLE=true
EOF
chmod 600 "$config_dir/.env"
```

The parser accepts only `KEY=value` entries; it does not execute the
configuration file as shell code. Do not set `TELEGRAM_API_BASE` in production:
the plugin only accepts `https://api.telegram.org`, so the bot token cannot be
sent to a custom or cleartext endpoint.

## Test and operate

```bash
herdr plugin action invoke blockshiftnetwork.telegram-attention.test
herdr plugin action invoke blockshiftnetwork.telegram-attention.status
herdr plugin log list --plugin blockshiftnetwork.telegram-attention --limit 20
```

The event hook sends alerts only for `blocked`; it ignores `working`, `idle`,
and `unknown`. A `done` alert is sent only when `TELEGRAM_NOTIFY_DONE=true`.
The plugin deduplicates repeated status notifications with the same Herdr pane,
state, and sequence number.

## Attention control plane

Version 0.3 groups agents blocked by the same project and reason into one
priority-ranked incident. Its inline buttons acknowledge an incident, snooze it
for 30 minutes, or show the affected agents. Callback processing is restricted
to the configured chat ID.

Start the local callback dispatcher once; it runs in a separate Herdr tab and
does not affect existing agent panes:

```bash
herdr plugin pane open --plugin blockshiftnetwork.telegram-attention \
  --entrypoint dispatcher --placement tab --no-focus
```

## Privacy and security

- The repository contains no token or chat ID.
- Credentials live only in the private plugin config directory with mode `600`.
- Telegram requests enforce the official HTTPS Telegram endpoint, use a timeout,
  URL-encoded fields, and do
  not write the token to plugin logs.
- Incident fields are size-limited; resolved incidents expire after seven days
  and the local state holds at most 200 incidents.
- Alert content is limited to Herdr-provided agent, workspace, tab, pane, state,
  and optional status message.

## Development checks

```bash
bash -n bin/herdr-telegram-attention tests/test.sh
tests/test.sh
```

## Publishing

This repository is intended to be public and tagged with the `herdr-plugin`
topic, which is how Herdr's marketplace discovers community plugins.
