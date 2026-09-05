# XuidAntiBot

[![Version](https://img.shields.io/badge/version-1.0.1-blue)]()
[![Endstone API](https://img.shields.io/badge/Endstone%20API-0.11-blueviolet)](https://endstone.dev)
[![Minecraft](https://img.shields.io/badge/Minecraft-Bedrock-green)](https://endstone.dev)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

> Advanced bot and spam protection for Minecraft Bedrock servers powered by
> [Endstone](https://endstone.dev) — verification forms, per-IP rate limiting,
> geo filtering, AntiVPN, automatic IP blocking, lockdown and maintenance
> modes, join limits, and hub-style server transfers.

XuidAntiBot rejects bot connections **at login time** — before the
player entity is even created in the world — and challenges every first-time
player with an in-game verification form. Each player only ever verifies
once (remembered by Xbox Live xuid), so regular players enjoy an
uninterrupted experience while scripted bots never make it past the door.

The plugin is a single Python wheel: drop it into your server's `plugins/`
folder, restart, and it works out of the box with sensible defaults. Every
setting can be changed live — either through the in-game form UI
(`/abmenu`) or one all-in-one command (`/abset`) — with no restart required.

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Permissions](#permissions)
- [Configuration](#configuration)
  - [Config menu UI (`/abmenu`)](#config-menu-ui-abmenu)
  - [All-in-one command (`/abset`)](#all-in-one-command-abset)
  - [Geo filter and AntiVPN (`/abgeo`)](#geo-filter-and-antivpn-abgeo)
- [Verification modes](#verification-modes)
- [What unverified players experience](#what-unverified-players-experience)
- [Hub mode: server transfer after verification](#hub-mode-server-transfer-after-verification)
- [Data files](#data-files)
- [Design and performance notes](#design-and-performance-notes)
- [Project structure](#project-structure)
- [Development](#development)
- [FAQ](#faq)
- [License](#license)
- [Credits](#credits)

## Features

**Login-time filtering (the cheapest place to stop a bot)**

- Maintenance mode — hard lock, only operators can join
- Country filter (whitelist or blacklist, ISO 3166-1 alpha-2 codes)
- AntiVPN — rejects proxy, VPN and hosting/datacenter IPs
- Automatic IP blocking with configurable expiry, auto-renewed if the
  attacker keeps hammering while blocked
- Bot name detection via custom regex patterns
- Hard block of names containing `_` (impossible in valid Xbox Live
  gamertags)
- Rejection of connections with an empty xuid (not signed in to Xbox Live)
- Per-IP login rate limit (e.g. more than 3 logins in 60 s = auto-block)
- Join limit — caps total unverified joins across the whole server to
  survive join floods
- Lockdown mode — blocks all unverified players with one command

**In-game verification (layer 4)**

- Three form modes: single button, 3-button code matching, or strict
  typed code (see [verification modes](#verification-modes))
- Asked **once per xuid**, persisted across restarts — regular players
  never see it again
- Response-time honeypot: answering faster than 200 ms is treated as a
  bot, no matter if the answer is right
- Wrong-attempt limit and form-close limit before a kick
- Verification timeout with automatic kick + IP ban

**Full lock-down while a player awaits verification**

- Blindness + invisibility effects, isolated at a configurable Y level
- Position pinned: no movement, no teleports
- 21 event types cancelled: chat, damage, knockback, block break/place,
  interaction, item drop/pickup/consume, commands, emotes, beds, portals,
  gamemode and skin changes, and more

**Hub / proxy-server workflow**

- After verifying, players are transferred to your main server
- One server = automatic direct transfer; multiple servers = a selection
  form with a timeout

**Operations**

- Complete in-game configuration UI (`/abmenu`) and all-in-one command
  (`/abset`, 20 keys with tab-completion)
- Everything persisted in three plain JSON files — hand-editable, hot
  reloadable with `/abreload`, no plugin restart needed
- Robust config loading: invalid entries are skipped with a warning
  instead of crashing the server at startup

## How it works

Every connection passes through the following layers at
`PlayerLoginEvent` (the first event a connection triggers — cancelling it
here means the player entity is never created):

| # | Layer | Action |
|---|-------|--------|
| 1 | Maintenance mode | Only operators may join |
| 2 | Geo filter + AntiVPN | Reject disallowed countries and proxy/VPN/hosting IPs |
| 3 | Verified bypass | Players verified before skip the IP blocklist and rate limit |
| 4 | Lockdown | Block everyone not yet verified |
| 5 | Join limit | Cap total unverified joins server-wide (kick as "overloaded") |
| 6 | Blocked IP list | Reject known-bad IPs, auto-extend the ban on retry |
| 7 | Bot name pattern + `_` | Auto-block the IP |
| 8 | Empty xuid | Auto-block the IP |
| 9 | Per-IP rate limit | Too many logins in the window = auto-block the IP |

The order is deliberate: geo/AntiVPN runs **before** the verified bypass
(when you change the country rules, they apply to everyone, including
already-verified players), the join limit runs before the expensive
detection layers to stop floods early, and every auto-block uses the same
generic kick message so attackers cannot learn which layer caught them.

### Maintenance vs. lockdown

These are the two "hard lock" toggles and it's easy to mix them up — they
exist for two different situations:

- **Maintenance mode** — use this when **you're taking the server down
  for upkeep** (updates, migrations, backups, planned downtime). It shuts
  the door to everyone except operators, including players who are
  already verified regulars, since the point is that *nobody* should be
  playing right now.
- **Lockdown mode** — use this when **the server is under an active bot
  or raid attack** and you need to stop the bleeding immediately. It
  blocks every player who has never verified before, while still letting
  your existing verified community play normally — so you're not forced
  to shut the whole server down just to stop an attack in progress.
  Lockdown only **rejects the connection** (a kick with a message) — it
  does not add the IP to the auto-block list the way the bot-detection
  layers do. It's a manual, reversible gate, not a punishment: turn it
  off and blocked players can simply reconnect.

| | Maintenance | Lockdown |
|---|---|---|
| Command | `/abset maintenance on` | `/abset lockdown on` |
| Use case | Planned server maintenance / downtime | Under active bot attack or raid, right now |
| Blocks | **Everyone** except operators — including already-verified players | Only players who have **never verified** |
| Verified players | Blocked too | Let through |
| Operators | Always bypass, by design | Blocked too (unless also verified) |
| IP auto-blocked? | No | No — kick only, no auto-block |
| Runs at | Layer 1 (first check, before anything else) | Layer 4 (after the verified bypass) |

In short: **lockdown** stops an ongoing attack while keeping the server
open for your real players; **maintenance** closes the server to
everyone but staff. Both can be toggled from `/abmenu` as well as
`/abset`.

Players who pass all nine layers and have never verified before enter a
**pending** state and receive the verification form (see
[verification modes](#verification-modes)). Until they answer correctly
they are blind, invisible, isolated, and frozen. Passing the form marks
their xuid as verified — permanently, until you clear the file.

## Requirements

- A Minecraft **Bedrock Dedicated Server** running
  [Endstone](https://endstone.dev) **0.11 or newer**
- Python **3.11 or newer** (Endstone ships with an embedded runtime)
- Internet access for the geo filter / AntiVPN lookups (only if enabled —
  the plugin works fully offline with those features off)

## Installation

1. Download `endstone_xuidantibot-<version>-py3-none-any.whl` from the
   [releases](../../releases) page.
2. Copy the wheel into your server's `plugins/` folder:
   ```text
   bedrock-server/
   ├── endstone.exe            # or your endstone launcher
   ├── worlds/...
   └── plugins/
       └── endstone_xuidantibot-1.0.1-py3-none-any.whl
   ```
3. Restart the server. Endstone installs the plugin automatically and
   prints:
   ```text
   [XuidAntiBot] Enabled — rate limit 3 logins/60s, ...
   ```
4. Done. The plugin folder `plugins/XuidAntiBot/` with the default config
   is created on first start.

## Quick start

Run these as an operator, in game:

```text
/abmenu                          open the config UI — easiest way to
                                  configure everything, no commands needed
/abset strict on                 (recommended) strongest verification
/abset blockexpiry 7             auto-blocks expire after 7 days
/abgeo countries add VN          (optional) allow your country only:
/abgeo mode whitelist
/abgeo on
/abstatus                        review the current configuration
```

Everything above the `/abstatus` line can also be done through `/abmenu`
instead — it's the same settings, just presented as an in-game form.

The plugin is fully functional with zero configuration — the defaults
already block name bots, empty-xuid bots, login spam, and form-less bots,
and verify every new player once.

## Commands

All commands require the `antibot.admin` permission (granted to operators
by default) and can be run from console or in game, except where noted.

| Command | Description |
|---------|-------------|
| `/abstatus` | Show the full XuidAntiBot status: verification mode, rate limit, blocked IP list with remaining expiry, main server list |
| `/abreload` | Reload the three JSON files (after editing them by hand) — no restart, does not disturb players currently verifying |
| `/abunblock <ip>` | Remove one IP from the auto-block list |
| `/abgeo ...` | Manage the country filter and AntiVPN (see [below](#geo-filter-and-antivpn-abgeo)) |
| `/abmenu` | Open the in-game configuration UI (**in game only** — forms cannot be sent from console) |
| `/abset <key> [value...]` | All-in-one configuration command, 20 keys with tab-completion (see [below](#all-in-one-command-abset)) |

## Permissions

| Permission | Default | Grants |
|------------|---------|--------|
| `antibot.admin` | `op` | Access to all six commands and the config UI |

Maintenance mode additionally lets **operators** bypass the lock by design,
regardless of this permission.

## Configuration

There are three equivalent ways to change any setting — they share the
same backend, so the result is always the same:

1. **`/abmenu`** — the in-game form UI (recommended — no need to learn
   any command syntax or key names, everything is point-and-click)
2. **`/abset <key> [value...]`** — the all-in-one command (console-friendly)
3. **Edit `plugins/XuidAntiBot/antibot_config.json`** by hand, then `/abreload`

All changes persist immediately to `antibot_config.json`.

### Config menu UI (`/abmenu`)

The easiest way to configure the plugin. Run `/abmenu` in game and every
setting is presented as buttons, toggles, sliders and text fields inside
Minecraft forms — no need to memorize `/abset` keys or edit JSON by hand.
Changes made in the UI apply and save immediately, the same as `/abset`.

The UI is organized into sections:

- **Basic config** — verification mode (easy / 3-button / strict),
  verification timeout, IP block expiry
- **Advanced config** — rate limit, wrong attempts, reaction time,
  UI margin, effect buffer, max form closes, isolation Y, timezone
- **Country filtering** — geo filter mode, country codes, AntiVPN
- **Bot name patterns** — add/remove regex patterns
- **Join limit** — whole-server flood protection
- **Blocked IPs** — inspect and unblock individual IPs
- **Main servers** — manage the transfer target list

Every section opens as its own form, so you only ever see the settings
relevant to what you're changing.

### All-in-one command (`/abset`)

Toggle keys accept `[on|off]`; typing the key alone **toggles** the
current state. Run `/abset` with no arguments to list every key.

| Key | Usage | Meaning | Default |
|-----|-------|---------|---------|
| `strict` | `/abset strict [on\|off]` | Strict verification: the player must type the code back | off |
| `easy` | `/abset easy [on\|off]` | Easy verification: single button | off |
| `transfer` | `/abset transfer [on\|off]` | Transfer players to a main server after verifying | off |
| `lockdown` | `/abset lockdown [on\|off]` | Block unverified players from joining | off |
| `maintenance` | `/abset maintenance [on\|off]` | Hard lock: everyone except operators | off |
| `joinlimit` | `/abset joinlimit [on\|off]` | Enable the whole-server join limit | off |
| `blockexpiry` | `/abset blockexpiry <days\|0>` | Expiry of **new** auto-blocks (`0.5` = 12 h, `0` = permanent). Not retroactive | 0 (permanent) |
| `verifytimeout` | `/abset verifytimeout <seconds>` | Real time allowed to verify before kick + IP ban | 120 |
| `timezone` | `/abset timezone <offset>` | Display-only UTC offset (`7`, `-5`, `5.5`...; −12..+14) | 0 (UTC) |
| `ratelimit` | `/abset ratelimit <count> <window_minutes>` | Per-IP login limit — above it, auto-block | 3 / 60 s |
| `joinlimitconfig` | `/abset joinlimitconfig <count> <window_minutes>` | Join limit threshold (enable with `joinlimit`) | 10 / 60 s |
| `namepattern` | `/abset namepattern <add\|remove\|list> [regex]` | Bot name regexes, validated before saving | (empty) |
| `maxwrongattempts` | `/abset maxwrongattempts <count>` | Wrong code attempts allowed; the next one kicks + IP-bans | 3 |
| `minreactiontime` | `/abset minreactiontime <ms>` | Response-time honeypot threshold, `0` = disabled | 200 |
| `transfertimeout` | `/abset transfertimeout <seconds>` | Time to pick a main server (2+ servers configured) | 120 |
| `uitimeoutmargin` | `/abset uitimeoutmargin <seconds>` | How much less the form **shows** vs the real kick time | 60 |
| `pendingeffectbuffer` | `/abset pendingeffectbuffer <seconds>` | Extra duration for the blindness/invisibility safety net | 15 |
| `maxformresends` | `/abset maxformresends <count>` | Form closes (without clicking) before a soft kick | 10 |
| `isolationy` | `/abset isolationy <y> [force]` | Y level used to isolate pending players (warns outside -64..319) | 2000 |
| `mainservers` | `/abset mainservers` | Open the main-server management form (**in game only**) | (none) |

Examples:

```text
/abset ratelimit 5 2            5 logins per 2 minutes per IP
/abset namepattern add ^Guest[0-9]+$
/abset verifytimeout 90
```

### Geo filter and AntiVPN (`/abgeo`)

| Usage | Meaning |
|-------|---------|
| `/abgeo` | Show the current geo configuration |
| `/abgeo <on\|off>` | Enable / disable the country filter |
| `/abgeo mode <whitelist\|blacklist>` | `whitelist`: only listed countries may join; `blacklist`: listed countries are blocked |
| `/abgeo countries <add\|remove\|list> [codes...]` | Manage ISO 3166-1 alpha-2 codes (`VN`, `FR`, `US`, ...) |
| `/abgeo vpn <on\|off>` | AntiVPN: reject proxy / VPN / hosting IPs |

Details:

- Lookups use the free tier of [ip-api.com](https://ip-api.com) (HTTP,
  45 requests/minute) and are cached **per IP for 24 hours**, so the rate
  limit is practically never hit on a normal server.
- AntiVPN is checked **before** the country filter — a VPN IP from an
  allowed country is still blocked.
- **Fail-open**: if the lookup fails (network error, quota, private IP),
  the connection is not blocked — the later layers still apply. Real
  players are never locked out by an API outage.
- An empty country list with the filter on blocks **nobody** (avoids
  locking out the whole server by accident).

## Verification modes

| Mode | What the player sees | What it stops |
|------|----------------------|---------------|
| Default (both toggles off) | A 4-character code and **3 shuffled buttons** — click the one matching the code | Bots that cannot click forms; bots that blindly click a fixed button (still a 1/3 lucky-guess chance) |
| Easy (`/abset easy on`) | One button: "Confirm & enter server" | Only bots that cannot click forms — the friendliest mode |
| Strict (`/abset strict on`) | The code must be **typed** into a text input, case-sensitive | Everything above, plus button-clicking bots — scripts cannot read the form and type the code back |

If both `easy` and `strict` are on, **easy wins**. The code uses
unambiguous characters only (no `0/O`, `1/I/L`, `5/S`, `8/B`, `2/Z`),
and a new code is generated for every resend.

Guards shared by all modes:

- **Response-time honeypot** — submitting within 200 ms of the form being
  sent is treated as a bot (below human reaction time) and blocks the IP
  regardless of a correct answer
- **Wrong attempts** — 3 strikes, then kick + IP ban
- **Form closes** — closing without clicking reopens the form; past 10
  closes, a soft kick (no ban — closing is lag/accident behavior, not
  bot behavior)
- **Timeout** — no answer within `verify_timeout` (120 s default) = kick
  + IP ban

## What unverified players experience

While pending, a player is completely separated from the real world:

- Teleported to the **isolation Y level** (default Y = 2000) and pinned
  there — every movement and teleport is cancelled
- **Blindness** so they cannot scout other players' bases, and
  **invisibility** so mobs and players barely notice them
- Chat, damage, knockback, block break/place, every kind of interaction,
  item drop / pickup / consumption, commands, emotes, bed entry, portals,
  held-item, gamemode and skin changes — all cancelled

The effects are scheduled to last the full worst-case wait (verification
+ server selection + a safety buffer), so they never expire early, and
they are explicitly cleared the moment verification succeeds. The timer
shown on the form is intentionally shorter than the real one
(`uitimeoutmargin`, default 60 s) — the real allowance is more generous
than what the player is told.

## Hub mode: server transfer after verification

Typical setup: a lightweight "gate" server that only runs XuidAntiBot
and forwards verified players to your real world(s):

1. `/abset mainservers` (in game) — add servers with a name, host, port
2. `/abset transfer on`

Behavior:

- **One** main server: players are transferred directly after verifying
- **Two or more**: a selection form appears; closing it reopens it, and
  no choice within `transfertimeout` (120 s) is a kick
- Already-verified players rejoining later skip the form and go straight
  to the server selection
- Transfer is **disabled by default** even with servers configured — it
  only starts once you explicitly enable it

## Data files

All state lives in `plugins/XuidAntiBot/` as plain JSON:

| File | Contents |
|------|----------|
| `antibot_config.json` | Every setting (26 keys) — mirrors the `/abset` keys |
| `antibot_blocked.json` | Auto-blocked IPs: `{"<ip>": {"reason": str, "expires_at": float\|null, "banned_at": float\|null}}` (`expires_at: null` = permanent) |
| `antibot_verified.json` | Sorted list of verified player keys (xuid) |

- Hand-editing is supported: fix what you need, then run `/abreload`
- Older file generations are migrated transparently on load (the blocked
  file has three historical formats, all still readable)
- Invalid entries (bad regex, missing fields, wrong types) are skipped
  with a log warning — the plugin never crashes at startup over a
  hand-edited file
- `/abunblock <ip>` or the Blocked IPs section of `/abmenu` removes a
  wrong block

## Design and performance notes

- **Fail-closed for bots, fail-open for infrastructure**: if the geo API
  is unreachable, players are not blocked; the name/xuid/rate-limit
  layers still run
- **Nothing wasted**: rejected connections are cancelled at
  `PlayerLoginEvent` — no entity is spawned, nothing to clean up
- **Cheap hot paths**: all high-frequency event handlers (movement,
  chat, interaction...) exit immediately when nobody is pending
- **Auto-blocked kick messages are deliberately generic** — an attacker
  must not learn which layer detected them
- **Dynamic-IP friendly**: verification is keyed to the xuid, and
  verified players bypass the IP blocklist, so a rotating home IP can
  never lock a legitimate verified player out
- **Bans renew themselves**: an IP that keeps trying to log in while
  blocked gets its expiry extended from scratch

## Project structure

The plugin is built from pure mixins combined into the single
`AntiBotPlugin` class — one runtime instance, ten focused modules:

```text
xuid_antibot/
├── __init__.py         Plugin class, commands, permissions, defaults
├── config.py           Lifecycle, 3 JSON files, config validation,
│                       legacy data-folder migration
├── helpers.py          IP lookup, bot-name matching, rate limit,
│                       IP blocking, effects, isolation
├── geo_filter.py       Country filter + AntiVPN (ip-api.com, 24 h cache)
├── login_guard.py      The 9 login filter layers
├── verify_forms.py     Verification forms (3 modes), honeypot, timeouts
├── transfer.py         Post-verification server transfer
├── events.py           The 21 event cancellations while pending
├── commands.py         All /ab... commands (shared backend)
└── config_ui_forms.py  The /abmenu in-game UI
```

## Development

```bash
# Build the wheel from source
pip install build
python -m build          # -> dist/endstone_xuidantibot-1.0.1-py3-none-any.whl

# Live development on a running server
pip install -e .         # in the server's Python environment
# then use /reload in game to pick up code changes
```

Note on naming: the distribution name must start with `endstone-` and
match the entry-point name (`XuidAntiBot`) — that pair is what the
Endstone plugin loader requires, and the entry-point name is what shows
up in-game as the plugin name and data folder. Keep both in sync if you
fork this project.

The test suite and CI build the wheel on every push
(`.github/workflows/build.yml`).

## FAQ

**A real player got auto-blocked. Now what?**
`/abunblock <ip>`, or unblock them from the Blocked IPs section of
`/abmenu`. Verified players bypass the blocklist anyway.

**My players have dynamic IPs — won't the rate limit ban them?**
Verification is remembered **by xuid**, not IP. Once a player has
verified, they bypass the IP blocklist and rate limit entirely.

**I changed the country list and a verified player got blocked.**
That is intentional — the geo/AntiVPN layer runs before the verified
bypass, so country rules apply to everyone. Re-check the list with
`/abgeo countries list`.

**Does it work without internet?**
Yes, with the geo filter and AntiVPN off (the defaults). Those two
features need ip-api.com; on API failure the layer fails open.

**Can bots just guess the 3-button code?**
Yes, with a 1/3 chance per try — but the 3-wrong-attempts limit means a
guesser is kicked and IP-banned quickly. Use `/abset strict on` to
eliminate guessing entirely.

**Console says a form cannot be sent?**
`/abmenu` and `/abset mainservers` need a real player (forms are
client-side UIs). Every other command works from console.

**What is the `_` name rule?**
Valid Xbox Live gamertags never contain underscores, so a name with `_`
is a forged connection. This check cannot be disabled.

## License

Copyright 2026 dyan1619 —
[GPL-3.0-or-later](LICENSE). In short: you may use, study, modify and
redistribute this plugin, including commercially, as long as derivative
works stay under the same license and keep the source available.

## Credits

- [Endstone](https://endstone.dev) — the plugin framework this runs on
- [ip-api.com](https://ip-api.com) — free IP geolocation/proxy data
- Bot protection concepts inspired by the wider Minecraft anti-bot
  community (captcha forms, honeypots, per-IP rate limiting)
