# The handwriting service

`glassine-handwriting-service` (spec 004 FR-005, FR-012 to FR-015) holds the recognizer for
every app in the person's session: one recognizer process, one model in memory (SC-002). It
owns the person's store, so apps never touch its files. It has two sides:

- the **service interface** for apps, JSON lines over a unix socket (below; its schema is
  `docs/service.schema.json`, an example client `service/examples/hw-service-example.py`);
- the **compositor's channel** behind the pen interface (docs/pen-interface.md). The
  compositor starts the service and is its only peer on that side.

The service exits when the compositor's channel closes, and the compositor starts it again.
The recognizer is started on the first request and leaves after a while idle. Sessions
begun before such a restart are begun again on the next request.

## Who is calling (FR-001)

**Service socket.** The service asks the operating system who is calling (`SO_PEERCRED`).
Only the person's own user is answered. A process whose root holds `/.flatpak-info` is its
sandbox's application id. Any other process is `host.` followed by the program it runs (for
an interpreter, the script). A sandboxed app without a name is `unknown` and is never
allowed to learn or to see samples.

**Pen interface.** The compositor names the app: the security-context application id, or the
process id, which the service resolves as above.

**Recognizer.** The service runs one recognizer in relay mode (docs/recognizer.md, Relay).
Before each request for an app it sends `client NAME`, so every session and every sample
belongs to the app it was made for, as if that app had spoken to the recognizer itself. A
pick through the pen interface therefore stores the same sample as a direct client confirming
the same pen input.

## Permissions (FR-014)

Per app, the person decides two things:

- `learn`: its reports keep samples;
- `samples`: it may list and delete the samples kept through it.

Each is `allowed`, `refused` or unasked. Reading and neatening need neither. The choices are
kept in `$XDG_CONFIG_HOME/glassine/handwriting-apps.json`. Only an unsandboxed caller, such
as Settings or the samples app, can change them (`permission.set`). The samples app itself
(`host.glassine-handwriting-samples`) is allowed both. A request that needs a permission the
app lacks gets the error `permission-needed` (never asked) or `not-allowed` (refused), never
an empty result.

## The service interface

Socket: `$XDG_RUNTIME_DIR/glassine-handwriting/service`. A Flatpak app reaches it with
`--filesystem=xdg-run/glassine-handwriting` (FR-002), the one documented permission.
Each line is a request `{"id": …, "method": "…", "params": {…}}`. It is answered by a line
with the same `id` and either `"result": {…}` or `"error": {"code", "message"}`.

| Method | Params | Result |
|---|---|---|
| `hello` | | `version` (1), `app` (who the service takes the caller for), `sandboxed`, `neaten`, `text`, `letters`, `languages` |
| `status` | | `engine` (`running`, `pid`, `starts`), `surfaces` (pen interface surfaces held) |
| `permission.get` | | the caller's `learn`, `samples` |
| `permission.set` | `app`, `learn`, `samples` | that app's states; unsandboxed callers only |
| `read` | `strokes`, `count` (1–8), `units`, `context` | `candidates`: `text`, `score`, `from`, `fit`, `chars`; or `private`, or `gesture` |
| `neaten` | `strokes`, `strength` (0–1), `text`, `units`, `context` | `text`, `strokes`: `how`, `points` |
| `samples.list` | | `samples` kept through the caller: `id`, `time`, `source`, `language`, `text` (needs `samples`) |
| `samples.delete` | `id` | `{}`, or `not-yours` (needs `samples`) |
| `letters.status` | | `state`, `covered`, `font` (needs `samples`) |

Strokes are lists of points `[x, y, t]`, `[x, y, t, pressure]` or
`[x, y, t, pressure, tilt_x, tilt_y]`, with t in ms, pressure 0 to 1 and tilt in degrees.
`context`: `purpose` and `hints` as in `zwp_text_input_v3`, plus `before` and `languages`.
`units`: as the recognizer's (`device`, `device:PX_PER_MM`, `em`).

Error codes: `bad-request`, `unknown-method`, `permission-needed`, `not-allowed`,
`not-yours`, `unavailable`, `failed`.

Not in version 1, still owed for spec 004 FR-013 and FR-015: sample export and show,
learning control, letter model and font builds with progress notifications, and donation
(through the samples app or Settings only). "Donation" is the internal name of what the
person sees as a contribution.

## The compositor's channel

The compositor starts the service with `--pen-fd N`: its end of a socket pair. Both sides
send JSON objects, one per line, and neither waits for an answer. `s` names a surface, as
the compositor numbers them. `k` names a stroke, by the surface's stroke numbers.

From the compositor:

| `op` | Fields | Meaning |
|---|---|---|
| `surface` | `s`, `sandbox` or `pid` | a surface that wants the service, and whose it is |
| `set` | `s`, `neaten`, `strength`, `text`, `purpose`, `hints`, `before`, `after`, `languages`, `guides` ([spacing, first]), `box` ([x0, y0, x1, y1], when the area is set), `units` | its choices; a private field (`password`, `pin`, the hints `hidden_text` or `sensitive_data`) is followed by `destroy` instead |
| `stroke` | `s`, `k`, `points` ([x, y, t, pressure, tilt_x, tilt_y]) | a raw stroke |
| `clear` | `s` | the app is done with the writing held |
| `report` | `s`, `serial`, `action` (`picked`, `accepted`, `corrected`, `discarded`), `rank`, `text` | what the person did with the candidates `serial` |
| `destroy` | `s` | forget the surface and all it held |

From the service:

| `op` | Fields | Meaning |
|---|---|---|
| `hello` | `version`, `neaten`, `text`, `letters`, `languages` | sent once at start: the capabilities |
| `neat` | `s`, `k`, `how`, `points` ([x, y] per raw point) | the neatened stroke `k` |
| `reading` | `s`, `text`, `strokes` | a partial reading |
| `candidates` | `s`, `serial`, `items` (`text`, `from`, `fit`, `chars`) | the candidates at a pause |
| `report` | `s`, `serial`, `result` | the answer to a report |

The compositor turns these into the pen interface's events. It sends a stroke's points in
parts small enough for the Wayland wire, and it keeps the time, pressure and tilt of each
raw point for the neat stroke.

**Timing.** The writing is read 250 ms after the last stroke. Neatening goes line by line
(the guides, else the lines found from the writing) and, within a line, word by word along
the best reading. A word is neatened again only when its strokes, its text or the strength
change. The candidates follow a pause of 900 ms.
