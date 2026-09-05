# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""XuidAntiBot — anti-spam-bot plugin for Endstone (Bedrock).

How it works: connections are rejected as early as PlayerLoginEvent
(before the player entity is created in the world) through multiple
filter layers (maintenance, lockdown, geo + AntiVPN, blocked IP, bot
name patterns, empty xuid, per-IP rate limit); anyone who passes all
those layers and enters the world without a prior verification must
pass a verification form.

Package layout — every module is a pure mixin, combined into exactly
one AntiBotPlugin class at the bottom of this file (there is still a
single runtime instance, so sharing self.* across files is normal).
To change any behavior, find the right module by its description
below; no need to touch other modules:

    __init__.py        — main class: command, permission, and constant declarations
    config.py          — plugin lifecycle + reading/writing 3 persistent JSON files
    helpers.py         — IP lookup, bot-name matching, rate limit, IP blocking,
                         blindness/invisibility effects, coordinate isolation
    login_guard.py     — on_player_login: the filter layers applied at login
    verify_forms.py    — layer 4: verification form when entering the world
    transfer.py        — transfers players to the main server
    events.py          — blocks chat/movement/interaction while pending
    commands.py        — on_command + all /ab... commands
    config_ui_forms.py — /abmenu: in-game configuration form UI

If unsure how an Endstone API (event, form, effect...) behaves, check
the Endstone documentation before changing anything, to avoid breaking
behavior that currently works.
"""
from endstone.plugin import Plugin

from .config import ConfigPersistenceMixin
from .helpers import HelpersMixin
from .geo_filter import GeoFilterMixin
from .login_guard import LoginGuardMixin
from .verify_forms import VerifyFormsMixin
from .transfer import TransferMixin
from .events import PlayerEventsMixin
from .commands import CommandsMixin
from .config_ui_forms import ConfigUiFormsMixin


class AntiBotPlugin(
    ConfigPersistenceMixin,
    HelpersMixin,
    GeoFilterMixin,
    LoginGuardMixin,
    VerifyFormsMixin,
    TransferMixin,
    PlayerEventsMixin,
    CommandsMixin,
    ConfigUiFormsMixin,
    Plugin,
):
    api_version = "0.11"
    prefix = "XuidAntiBot"

    # ── Command declarations ────────────────────────────────────
    # Note on the "usages" syntax (tab-complete on Endstone): EACH key
    # is its own literal on its own line — do NOT merge "(k1|k2|...)<key>"
    # into one shared enum; the merged form does not produce key
    # suggestions on Endstone (verified in practice). "<strict>" etc.
    # are fixed literals; Endstone matches usages line by line, then
    # suggests the rest of the matching line.
    # 6 toggle keys strict/easy/transfer/lockdown/maintenance/joinlimit:
    # typing them WITHOUT a value TOGGLES the current state (see
    # _resolve_toggle_value in commands.py).

    commands = {
        "abstatus": {
            "description": "Show XuidAntiBot status (tracked / auto-blocked IPs)",
            "usages": ["/abstatus"],
            "permissions": ["antibot.admin"],
        },
        "abreload": {
            "description": "Reload config from the JSON files (use after manually editing antibot_config.json) — no plugin restart, does not disturb players awaiting verification",
            "usages": ["/abreload"],
            "permissions": ["antibot.admin"],
        },
        "abunblock": {
            "description": "Remove an IP from the XuidAntiBot auto-block list",
            "usages": ["/abunblock <ip: string>"],
            "permissions": ["antibot.admin"],
        },
        "abgeo": {
            "description": "Manage country filtering + AntiVPN (proxy/VPN/hosting) — type /abgeo with no arguments to see usage",
            "usages": [
                "/abgeo",
                "/abgeo <on>",
                "/abgeo <off>",
                "/abgeo <mode> [whitelist|blacklist]",
                "/abgeo <countries> [add|remove|list] [value: message]",
                "/abgeo <vpn> [on|off]",
            ],
            "permissions": ["antibot.admin"],
        },
        "abmenu": {
            "description": "Open the XuidAntiBot configuration UI (verification mode, server transfer, IP block expiry, server management)",
            "usages": ["/abmenu"],
            "permissions": ["antibot.admin"],
        },
        "abset": {
            "description": "All-in-one command: configure XuidAntiBot through a single command (type /abset with no arguments to see every key)",
            # Each key gets its own usages line (fixed literal); toggle
            # keys accept [on|off], the mainservers key always opens a
            # form, and numeric/duration keys accept [value: message]
            # (the whole remaining part of the command line,
            # e.g. "ratelimit 5 10").
            "usages": [
                "/abset",
                "/abset <strict> [on|off]",
                "/abset <easy> [on|off]",
                "/abset <transfer> [on|off]",
                "/abset <lockdown> [on|off]",
                "/abset <maintenance> [on|off]",
                "/abset <joinlimit> [on|off]",
                "/abset <blockexpiry> [value: message]",
                "/abset <verifytimeout> [value: message]",
                "/abset <timezone> [value: message]",
                "/abset <ratelimit> [value: message]",
                "/abset <joinlimitconfig> [value: message]",
                "/abset <namepattern> [add|remove|list] [value: message]",
                "/abset <maxwrongattempts> [value: message]",
                "/abset <minreactiontime> [value: message]",
                "/abset <transfertimeout> [value: message]",
                "/abset <uitimeoutmargin> [value: message]",
                "/abset <pendingeffectbuffer> [value: message]",
                "/abset <maxformresends> [value: message]",
                "/abset <isolationy> [value: message]",
                "/abset <mainservers>",
            ],
            "permissions": ["antibot.admin"],
        },
    }

    # ── Permissions ─────────────────────────────────────────────

    permissions = {
        "antibot.admin": {
            "description": "XuidAntiBot administration permission",
            "default": "op",
        },
    }

    # ── Default constants ───────────────────────────────────────
    # Every value below is only the FACTORY default (used when
    # antibot_config.json is missing or lacks the key); the real
    # runtime values are the matching self.* attributes, changed via
    # /abset or /abmenu and persisted.
    #
    # Unit convention: INTERNAL STORAGE is always in SECONDS (backward
    # compatible with old config.json files); commands/forms take
    # INPUT in minutes (ratelimit, joinlimitconfig) or days
    # (blockexpiry), converted to seconds at assignment time.
    # Changing a TTL/timeout is NOT retroactive for IPs/joins already
    # handled — it only applies to new attempts.

    DEFAULT_RATE_LIMIT_COUNT = 3       # more than N logins from the same IP...
    DEFAULT_RATE_LIMIT_WINDOW = 60     # ...within X seconds -> auto-block the IP

    # TTL (seconds) for NEWLY auto-blocked IPs, 0 = permanent. Not
    # retroactive for IPs blocked before the change; /abset
    # blockexpiry takes input in DAYS.
    DEFAULT_BLOCK_TTL = 0

    # Timezone offset (hours from UTC, fractional allowed, e.g.
    # 5.5 = UTC+5:30) — DISPLAY ONLY, for dates/times, independent of
    # the host machine's system clock.
    DEFAULT_DISPLAY_TZ_OFFSET = 0

    # Regexes used to detect bot names (add/remove via /abset
    # namepattern). These are only factory defaults — the real value
    # is self.name_patterns.
    DEFAULT_NAME_PATTERNS = []

    # Join limit: counts the TOTAL number of UNVERIFIED joins across
    # the WHOLE SERVER (regardless of IP) within join_limit_window
    # seconds — unlike the rate limit (counted per IP to catch bots).
    # Exceeding the threshold -> kick as "server overloaded", with NO
    # auto-block of the IP (it is not evidence of a bot).
    DEFAULT_JOIN_LIMIT_COUNT = 10
    DEFAULT_JOIN_LIMIT_WINDOW = 60
