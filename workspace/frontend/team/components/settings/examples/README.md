# Example settings widgets

These are **not** the built-ins. They are app-specific widgets, kept here as
worked examples of overriding a section type, and they share filenames with
the built-ins on purpose — the point is that an application can replace one.

`SettingsPage` resolves a section in this order:

1. `customWidgets[section.component]` when `type: custom`
2. `customWidgets[section.type]` — an app-provided override for a built-in type
3. the built-in for that type
4. "Unknown widget type"

So passing `{ gateway_status: MyWidget }` replaces the built-in everywhere
that type appears, with no change to the workspace config.

| file | what it shows | how it differs from the built-in |
|---|---|---|
| `GatewayStatusWidget.tsx` | A WhatsApp bridge with QR scan, pairing codes and a reconnect action | The built-in lists any set of gateways and their state; this one drives a single provider's pairing flow |
| `ScheduleWidget.tsx` | Opening hours — a row per weekday with open/close/closed | The built-in reads a cron expression; this one edits a business's trading hours |

Both are deliberately concrete. Copy one, change what it renders, pass it in:

```tsx
import GatewayStatus from './examples/GatewayStatusWidget'

<SettingsPage sections={sections} customWidgets={{ gateway_status: GatewayStatus }} />
```
