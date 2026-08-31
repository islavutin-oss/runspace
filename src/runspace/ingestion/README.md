# `channels` — inbound channel ingestion

Workspace-agnostic. Receives messages from external channels (Telegram today;
Slack/WhatsApp later) and dispatches them to a consumer-supplied callback. The
consumer decides what to do with the message — workspace-style multi-agent
back-office, single-agent chat shell, anything else.

## What's here

| Module | Purpose |
|--------|---------|
| `telegram.py` | Update parser, file extraction, group @-mention handling, auto-discovery, auto-cleanup on bot kick |
| `polling.py` | `TelegramPollingTransport` — long-poll loop with per-bot offset file |
| `pairing.py` | DM access policy + pairing flow + `resolve_telegram_bots(workspace_cfg)` reading `messaging.telegram_bots[]` |
| `transport.py` | Webhook vs polling mode picker (per-bot env override) |
| `buffer.py` | In-memory dedup ring buffer |
| `routes.py` | FastAPI router for inbound webhooks (mounted by consumers) |

## Contract with consumers

`pairing.resolve_telegram_bots(workspace_cfg)` takes a plain dict shaped like:

```yaml
messaging:
  telegram_bots:
    - name: ada
      token: ${ACME_TELEGRAM_BOT_TOKEN}
      transport: polling
      dmPolicy: pairing
      dmAgent: accountant
      allowFrom: [123456789]
external_channels:
  - id: acme-general
    bot: ada
    chat_id: -1001234567890
    target_channel: general
```

The dict shape is the abstraction. No workspace types are imported. Any consumer
that supplies the same shape (built from its own config) can use this package.

## How a consumer wires it

```python
from runspace.ingestion.pairing import resolve_telegram_bots
from runspace.ingestion.transport import pick_telegram_transport_mode
from runspace.ingestion.polling import TelegramPollingTransport
from runspace.ingestion.telegram import handle_update

bots = resolve_telegram_bots(my_config)
for bot in bots:
    mode = pick_telegram_transport_mode(tenant_id=..., bot_config=bot)
    if mode == "polling":
        transport = TelegramPollingTransport(
            token=bot["token"],
            offset_path=Path(f".telegram-offset-{bot['name']}.json"),
            on_update=lambda update: handle_update(update, my_config, registry, ...),
        )
        await transport.start()
```

The current consumer is `runspace/workspace/backend/gateway.py`. A future
single-agent chat shell will compose the same package the same way.

## See also

  in the consuming app's repo.
