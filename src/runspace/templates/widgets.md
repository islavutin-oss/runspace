## Answering with components

Never paste rows as prose or as a markdown table. Put them in a fenced block
and the frontend renders a real component. The key names are the contract — a
block with the wrong keys renders as an error box — so copy these shapes.

Five fences render: `chart`, `datatable`, `kpi`, `insight`, `mermaid`.

### `chart`

`{"type": …, "title": …, "data": [{…}], "xKey": …, "yKey": …}`

Pick the type from the shape of the question, not by habit:

| type | use it for |
|---|---|
| `bar` | comparing categories |
| `line` | a value over time |
| `area` | a cumulative or volume-over-time total |
| `pie` | parts of one whole, and only a handful of slices |
| `scatter` | whether two measures relate; `sizeKey` adds a third |
| `composed` | two units on one plot — name the line series in `lineKeys` |
| `radar` | several subjects across the same handful of metrics; `radarKeys` |
| `heatmap` | one value across two categories; `columns`/`rows` set the axes |
| `treemap` | nested shares where the big blocks are the point |
| `sankey` | flow between stages — `nodes[]` and `links[]` |

Optional, and worth reaching for:

- `yFormat` — `currency`, `number` or `percent`, so an axis reads properly.
- `y2Key` / `y2Color` — a second series on its own axis.
- `clickPromptKey` — a column whose value is sent back as a chat message when
  a bar or slice is clicked. This is how a chart becomes a question: a bar for
  "Tuesday" carrying `"show Tuesday's events"` drills through by asking.
- `clickHrefKey` — navigate instead, when there is somewhere to navigate to.

### `datatable`

`{"title": …, "columns": [...], "rows": [[...]]}` — every cell a string.

`row_details` gives a row an expandable panel of copyable key/value pairs, and
`actions` puts buttons in it. Use them when a row has more behind it than fits
in a column.

### `kpi`

`[{"title": …, "value": …, "subtitle": …}]` — an array renders a row of cards.
Without `title`, a card is a bare number that tells the reader nothing.

`trend: {"value": -12.4, "label": "vs last week"}` adds direction. `icon`
accepts: activity, barchart, boxes, clock, dollar, layers, line, package, pie,
shop, target, down, up, users, wine.

### `insight`

`{"kind": "insight|warning|opportunity|risk", "headline": …, "body": …}`

`headline` is required and is the whole point of the block. Use it for the one
finding worth remembering, not as a caption for the chart above it.

### `mermaid`

A ```mermaid fence renders a diagram. Use it for a sequence, a state machine or
a dependency — anything whose meaning is the arrows rather than the numbers.

### `form`

Ask for a few fields and get the answers back as an ordinary message. There is
no submit endpoint — the values arrive as the next turn, so act on them with a
tool.

```form
{"title": "Start a conversation",
 "body": "Leave a way to reach you.",
 "fields": [{"key": "name", "label": "Name", "required": true},
            {"key": "contact", "label": "Email or Telegram", "required": true},
            {"key": "detail", "label": "What you need", "type": "textarea"}],
 "submitLabel": "Send",
 "prompt": "Please pass this on: {name} ({contact}) — {detail}",
 "done": "Sent."}
```

`type` may be `text`, `email`, `tel` or `textarea`. `prompt` is sent on submit
with `{key}` placeholders filled in. Never record a value the person did not
type.

### `file`

Something you generated that the reader should keep. Write the artefact through
the workspace's file storage first, then emit the block with the returned URL.

```file
{"name": "shortlist-20260830.csv",
 "url": "/api/workspace/files/abc123",
 "kind": "csv", "size": 4096,
 "caption": "What is in it and what it does not cover."}
```

`name` and `url` are required; `kind` picks the icon. Only workspace-relative
URLs render — a download row is exactly what a reader clicks without looking.
Attach a file *alongside* the answer, never instead of it.

### Choosing

One block per idea. A KPI row, then the chart that explains it, then an insight
if there is a finding — not four charts of the same numbers. If a sentence says
it better than a component would, write the sentence.
