# Telegram Attention Alerts for Herdr

Receive a Telegram message only when a Herdr agent needs your attention or
becomes available for more work. The plugin uses Herdr's native
`pane.agent_status_changed` event hook: no terminal scraping, agent prompts,
or session restart is involved.

Messages are available in Spanish (`es`), English (`en`), and Portuguese
(`pt`). Spanish is the default. Blocked alerts are grouped into a prioritized
decision queue. A `done` event creates an availability queue so you can assign
the next task to the listed pane. Neither path reads terminal output nor the
agent conversation.

## Quick start

1. Install the public plugin:

   ```bash
   herdr plugin install blockshiftnetwork/herdr-telegram-attention
   ```

2. Follow [Configure securely](#configure-securely) below to add your bot token
   and chat ID outside the checkout.
3. Start the dispatcher once to enable the Telegram buttons:

   ```bash
   herdr plugin pane open --plugin blockshiftnetwork.telegram-attention \
     --entrypoint dispatcher --placement tab --no-focus
   ```

4. Verify configuration without sending a message:

   ```bash
   herdr plugin action invoke blockshiftnetwork.telegram-attention.status
   ```

Use the `test` action only when you intentionally want Telegram to send a test
message. The dispatcher runs in its own unfocused Herdr tab; it does not stop
or restart agent panes.

## Requirements

- Herdr 0.8.0 or newer
- `bash` and `python3`
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
# Optional but recommended, especially for group chats: only this Telegram user
# may use the inline controls.
TELEGRAM_ALLOWED_USER_ID=replace-with-your-telegram-user-id
# Notify when Herdr detects that background work finished and the agent is idle.
# Defaults to true. Set false if you want only blockers.
TELEGRAM_NOTIFY_AVAILABLE=true
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

The event hook sends a blocker incident for `blocked`. When Herdr emits `done`
(unseen background work settled to idle), the plugin sends or updates an
availability queue when `TELEGRAM_NOTIFY_AVAILABLE=true`. It removes an agent
from that queue when the agent becomes `working` or `blocked`, and deduplicates
repeated `done` events by pane and Herdr sequence.

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

## Availability queue

`done` is an operational signal, not a claim that an objective was completed.
The plugin therefore never injects a prompt asking the agent to justify,
evaluate, or report its response. It sends a neutral “agents available” queue
containing agent, project, workspace, tab, pane, and task title. Use that pane
identifier in Herdr to give the agent its next task.

The queue is grouped by workspace and project, so several finished agents do
not interrupt you separately. Its Telegram controls are:

- **Mark reviewed**: archives that availability batch after you have assigned
  or considered the next work.
- **View agents**: shows the agent and pane identifiers in a short callback
  response.

An agent that resumes work or needs an approval is removed automatically. The
dispatcher processes the Telegram controls, so keep it running:

```bash
herdr plugin pane open --plugin blockshiftnetwork/herdr-telegram-attention \
  --entrypoint dispatcher --placement tab --no-focus
```

If an agent received an old managed-goal command before upgrading, that command
now exits safely without sending a report, Telegram message, or new prompt.

## Privacy and security

- The repository contains no token or chat ID.
- Credentials live only in the private plugin config directory with mode `600`.
- Telegram requests enforce the official HTTPS Telegram endpoint, use a timeout,
  URL-encoded fields, and do
  not write the token to plugin logs.
- Inline controls are restricted to the configured chat; set
  `TELEGRAM_ALLOWED_USER_ID` to restrict them to one Telegram user as well.
- Incident and availability fields are size-limited; reviewed records expire
  after seven days and the availability queue is capped at 200 agents.
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
