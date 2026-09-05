# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin lifecycle + reading/writing the 3 persistent JSON files
(antibot_config.json, antibot_blocked.json, antibot_verified.json).

The JSON files live in the plugin's data folder and can be edited by
hand, then reloaded with /abreload without a restart. See
xuid_antibot/__init__.py for where this module sits in the package."""
import os
import re
import json
from typing import Dict
from collections import defaultdict, deque

from endstone import ColorFormat


class ConfigPersistenceMixin:
    # ── on_load: initialize default state ───────────────────────
    # Every field below is the real RUNTIME value (instance
    # attribute); _load_config will overwrite it with the value from
    # antibot_config.json if present. Change at runtime via
    # /abset <key> or the /abmenu form; both persist to file.

    def on_load(self) -> None:
        self.config_file = os.path.join(self.data_folder, "antibot_config.json")
        self.blocked_file = os.path.join(self.data_folder, "antibot_blocked.json")
        self.verified_file = os.path.join(self.data_folder, "antibot_verified.json")

        # Verification form mode (layer 4, see verify_forms.py). 3 levels:
        #   easy (one button to pass) > strict (type the code back) >
        #   default (3 buttons, 1 right + 2 wrong — a blindly guessing
        #   bot still has a 1/3 chance to pass).
        # If both are enabled, easy takes priority (see _send_verify_form).
        self.strict_verify: bool = False
        self.easy_verify: bool = False

        # Time allowed to answer the form before an automatic kick
        # (seconds). UI_VERIFY_TIMEOUT and PENDING_EFFECT_DURATION are
        # properties recomputed from this value.
        self.verify_timeout: int = self.DEFAULT_VERIFY_TIMEOUT

        # Per-IP login rate limit: more than rate_limit_count logins
        # within rate_limit_window seconds -> auto-block the IP.
        self.rate_limit_count: int = self.DEFAULT_RATE_LIMIT_COUNT
        self.rate_limit_window: int = self.DEFAULT_RATE_LIMIT_WINDOW

        # Current TTL (seconds) for NEWLY auto-blocked IPs, 0 = permanent.
        self.block_ttl: int = self.DEFAULT_BLOCK_TTL

        # Display timezone offset (hours from UTC, fractional allowed)
        # — affects only display, not the TTL/rate-limit logic.
        self.display_timezone_offset: float = self.DEFAULT_DISPLAY_TZ_OFFSET

        # Transfer to the main server after verification. OFF by
        # DEFAULT even when main_servers is configured — the admin must
        # enable it explicitly, to avoid accidentally enabling real
        # transfers while only preparing the server list.
        self.transfer_enabled: bool = False

        # Main server list: {"name": str, "host": str, "port": int}.
        # Empty OR transfer_enabled=False -> no transfer at all.
        self.main_servers: list = []
        # Round-robin position — only used with exactly 1 server
        # (direct transfer); with >=2 servers the player picks via form.
        self._server_rr_index: int = 0

        self.blocked_ips: Dict[str, dict] = {}  # ip -> {"reason": str, "expires_at": float|None, "banned_at": float|None}; expires_at=None -> permanent, banned_at=None -> unknown (blocked before this field existed)
        self._login_times: Dict[str, deque] = defaultdict(deque)  # ip -> recent timestamps

        # Country filter + AntiVPN (see geo_filter.py). AntiVPN shares
        # the cache/API and is independent of geo_filter_enabled.
        self.geo_filter_enabled: bool = self.DEFAULT_GEO_FILTER_ENABLED
        self.geo_filter_mode: str = self.DEFAULT_GEO_FILTER_MODE  # "whitelist" | "blacklist"
        self.geo_filter_countries: list = list(self.DEFAULT_GEO_FILTER_COUNTRIES)  # e.g. ["VN"], ["FR"]
        self.geo_filter_block_vpn: bool = self.DEFAULT_GEO_FILTER_BLOCK_VPN
        self.geo_cache: Dict[str, tuple] = {}  # ip -> (dict{country,proxy,hosting}, expires_at) — 24h cache, not saved to file

        # Form verification: ask only once per xuid (or uuid if there
        # is no xuid), persisted so it is never asked again.
        self.verified_ids: set = set()
        # pending_ids: players CURRENTLY waiting on the form — during
        # that time chat/movement/interaction are blocked (see
        # events.py).
        self.pending_ids: set = set()
        # _pending_ip_key: ip -> the player_key currently pending on that
        # IP. Used to block a SECOND connection from the same IP while
        # one player on that IP has not finished verifying yet (see
        # login_guard.py step 0d and _enter_pending/_clear_pending). Only
        # ONE key is kept per IP — no need to count, just to know
        # "is someone pending on this IP right now". Not saved to file
        # (runtime-only, like pending_ids).
        self._pending_ip_key: Dict[str, str] = {}
        self._pending_spawn_loc: Dict[str, tuple] = {}  # player_key -> (x,y,z) at join, to pin the position
        self._pending_original_loc: Dict[str, "Location"] = {}  # player_key -> original position before isolation up to a high Y
        self._wrong_attempts: Dict[str, int] = defaultdict(int)  # player_key -> wrong code attempts
        self._close_count: Dict[str, int] = {}  # player_key -> number of form closes without clicking
        self._strict_codes: Dict[str, str] = {}  # player_key -> current correct code (strict mode only)
        self._form_sent_at: Dict[str, float] = {}  # player_key -> time.time() of the last form send (response-time honeypot)

        # Main server selection: closing the form without choosing ->
        # force it to reappear; past the deadline (time.time()) without
        # a choice -> kick.
        self._pending_transfer_deadline: Dict[str, float] = {}

        # Regexes for bot name detection (see helpers._matches_bot_name).
        self.name_patterns: list = list(self.DEFAULT_NAME_PATTERNS)
        self._name_re = [re.compile(p) for p in self.name_patterns]

        # Max wrong attempts before kick + auto-ban of the IP.
        self.max_wrong_attempts: int = self.DEFAULT_MAX_WRONG_ATTEMPTS

        # Response-time honeypot threshold (ms) — 0 = disabled.
        self.reaction_time_min_ms: int = self.DEFAULT_REACTION_TIME_MIN_MS

        # Time allowed to pick a main server after verifying (seconds).
        self.transfer_choose_timeout: int = self.DEFAULT_TRANSFER_CHOOSE_TIMEOUT

        # UI offset vs the real verify_timeout (seconds) — the UI shows
        # LESS than the real value.
        self.ui_timeout_margin: int = self.DEFAULT_UI_TIMEOUT_MARGIN

        # Buffer added ON TOP (seconds) to the blindness/invisibility
        # effect duration — see PENDING_EFFECT_DURATION
        # (verify_forms.py).
        self.pending_effect_buffer: int = self.DEFAULT_PENDING_EFFECT_BUFFER

        # Max form closes without clicking before a soft kick (no IP
        # ban).
        self.max_form_resends: int = self.DEFAULT_MAX_FORM_RESENDS

        # Y coordinate used to isolate players awaiting verification.
        self.pending_isolation_y: int = self.DEFAULT_PENDING_ISOLATION_Y

        # Lockdown: blocks UNVERIFIED players (step 0b in
        # login_guard); previously verified players still join
        # normally. Disabled manually, never expires.
        self.lockdown_enabled: bool = False

        # Maintenance: hard lock, blocks EVERYONE except OPs (even
        # verified players), runs BEFORE geo_filter (step -2). Disabled
        # manually, never expires.
        self.maintenance_enabled: bool = False

        # Join limit: counts total UNVERIFIED joins across the whole
        # server within join_limit_window seconds; above the threshold
        # -> kick as overloaded, NO auto-block of the IP. _join_times
        # is not saved to file (a short sliding window; losing it on
        # restart is acceptable).
        self.join_limit_enabled: bool = False
        self.join_limit_count: int = self.DEFAULT_JOIN_LIMIT_COUNT
        self.join_limit_window: int = self.DEFAULT_JOIN_LIMIT_WINDOW
        self._join_times: deque = deque()  # timestamps of COUNTED joins (kicked attempts don't count)

    # ── on_enable / on_disable ──────────────────────────────────

    def on_enable(self) -> None:
        self.register_events(self)
        os.makedirs(self.data_folder, exist_ok=True)
        self._load_config()
        self._load_blocked()
        self._load_verified()
        self.logger.info(
            f"{ColorFormat.GREEN}[XuidAntiBot] Enabled — "
            f"rate limit {self.rate_limit_count} logins/{self.rate_limit_window}s, "
            f"strict verification (typed code): {'ON' if self.strict_verify else 'off'}, "
            f"{len(self.blocked_ips)} auto-blocked IPs, "
            f"{len(self.verified_ids)} players pre-verified."
        )

    def on_disable(self) -> None:
        self._save_blocked()

    # ── Config / persistence ────────────────────────────────────
    # File reading convention: any data error (bad regex, entry
    # missing a field, wrong type...) is SKIPPED + logged as a warning
    # instead of crashing the plugin at startup; a missing key -> fall
    # back to the default. The lockdown/maintenance/transfer/AntiVPN
    # flags intentionally default to False when the key is missing
    # (old config files) — never turn anything ON behind the admin's
    # back.

    def _load_config(self) -> None:
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.strict_verify = bool(data.get("strict_verify", False))
                self.easy_verify = bool(data.get("easy_verify", False))
                self.verify_timeout = int(data.get("verify_timeout", self.DEFAULT_VERIFY_TIMEOUT))
                self.rate_limit_count = int(data.get("rate_limit_count", self.DEFAULT_RATE_LIMIT_COUNT))
                self.rate_limit_window = int(data.get("rate_limit_window", self.DEFAULT_RATE_LIMIT_WINDOW))
                self.block_ttl = int(data.get("block_ttl", self.DEFAULT_BLOCK_TTL))
                self.display_timezone_offset = float(
                    data.get("display_timezone_offset", self.DEFAULT_DISPLAY_TZ_OFFSET)
                )
                self.max_wrong_attempts = int(data.get("max_wrong_attempts", self.DEFAULT_MAX_WRONG_ATTEMPTS))
                self.reaction_time_min_ms = int(data.get("reaction_time_min_ms", self.DEFAULT_REACTION_TIME_MIN_MS))
                self.transfer_choose_timeout = int(
                    data.get("transfer_choose_timeout", self.DEFAULT_TRANSFER_CHOOSE_TIMEOUT)
                )
                self.ui_timeout_margin = int(data.get("ui_timeout_margin", self.DEFAULT_UI_TIMEOUT_MARGIN))
                self.pending_effect_buffer = int(
                    data.get("pending_effect_buffer", self.DEFAULT_PENDING_EFFECT_BUFFER)
                )
                self.max_form_resends = int(data.get("max_form_resends", self.DEFAULT_MAX_FORM_RESENDS))
                self.pending_isolation_y = int(data.get("pending_isolation_y", self.DEFAULT_PENDING_ISOLATION_Y))
                self.lockdown_enabled = bool(data.get("lockdown_enabled", False))
                self.maintenance_enabled = bool(data.get("maintenance_enabled", False))
                self.join_limit_enabled = bool(data.get("join_limit_enabled", False))
                self.join_limit_count = int(data.get("join_limit_count", self.DEFAULT_JOIN_LIMIT_COUNT))
                self.join_limit_window = int(data.get("join_limit_window", self.DEFAULT_JOIN_LIMIT_WINDOW))
                self.geo_filter_enabled = bool(data.get("geo_filter_enabled", self.DEFAULT_GEO_FILTER_ENABLED))
                raw_geo_mode = str(data.get("geo_filter_mode", self.DEFAULT_GEO_FILTER_MODE)).lower()
                self.geo_filter_mode = raw_geo_mode if raw_geo_mode in ("whitelist", "blacklist") else self.DEFAULT_GEO_FILTER_MODE
                raw_geo_countries = data.get("geo_filter_countries", self.DEFAULT_GEO_FILTER_COUNTRIES)
                # Normalize to uppercase, drop empty/non-string values.
                self.geo_filter_countries = sorted({
                    str(c).strip().upper() for c in raw_geo_countries if str(c).strip()
                }) if isinstance(raw_geo_countries, list) else list(self.DEFAULT_GEO_FILTER_COUNTRIES)
                self.geo_filter_block_vpn = bool(data.get("geo_filter_block_vpn", self.DEFAULT_GEO_FILTER_BLOCK_VPN))
                # Validate each regex pattern individually — skip the
                # broken ones, keep the valid ones; if nothing survives
                # the filter, fall back to the default so the name
                # filter layer isn't silently turned off.
                raw_patterns = data.get("name_patterns", self.DEFAULT_NAME_PATTERNS)
                valid_patterns = []
                for pat in raw_patterns:
                    try:
                        re.compile(pat)
                        valid_patterns.append(str(pat))
                    except re.error as e:
                        self.logger.error(f"[XuidAntiBot] Skipping invalid name_patterns regex '{pat}': {e}")
                self.name_patterns = valid_patterns or list(self.DEFAULT_NAME_PATTERNS)
                self._name_re = [re.compile(p) for p in self.name_patterns]
                self.transfer_enabled = bool(data.get("transfer_enabled", False))
                raw_servers = data.get("main_servers", [])
                # Minimal validation — skip entries missing host/port.
                servers = []
                for s in raw_servers:
                    if not isinstance(s, dict) or "host" not in s or "port" not in s:
                        self.logger.error(f"[XuidAntiBot] Skipping invalid main_servers entry: {s}")
                        continue
                    servers.append({
                        "name": str(s.get("name") or s["host"]),
                        "host": str(s["host"]),
                        "port": int(s["port"]),
                    })
                self.main_servers = servers
            else:
                self._save_config()
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Failed to load config: {e}")

    def _save_config(self) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "strict_verify": self.strict_verify,
                        "easy_verify": self.easy_verify,
                        "verify_timeout": self.verify_timeout,
                        "rate_limit_count": self.rate_limit_count,
                        "rate_limit_window": self.rate_limit_window,
                        "block_ttl": self.block_ttl,
                        "display_timezone_offset": self.display_timezone_offset,
                        "transfer_enabled": self.transfer_enabled,
                        "main_servers": self.main_servers,
                        "name_patterns": self.name_patterns,
                        "max_wrong_attempts": self.max_wrong_attempts,
                        "reaction_time_min_ms": self.reaction_time_min_ms,
                        "transfer_choose_timeout": self.transfer_choose_timeout,
                        "ui_timeout_margin": self.ui_timeout_margin,
                        "pending_effect_buffer": self.pending_effect_buffer,
                        "max_form_resends": self.max_form_resends,
                        "pending_isolation_y": self.pending_isolation_y,
                        "lockdown_enabled": self.lockdown_enabled,
                        "maintenance_enabled": self.maintenance_enabled,
                        "join_limit_enabled": self.join_limit_enabled,
                        "join_limit_count": self.join_limit_count,
                        "join_limit_window": self.join_limit_window,
                        "geo_filter_enabled": self.geo_filter_enabled,
                        "geo_filter_mode": self.geo_filter_mode,
                        "geo_filter_countries": self.geo_filter_countries,
                        "geo_filter_block_vpn": self.geo_filter_block_vpn,
                    },
                    f, indent=2, ensure_ascii=False,
                )
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Failed to save config: {e}")

    def _load_blocked(self) -> None:
        # Backward compatibility with 3 generations of the blocked-file
        # format:
        #   {ip: "reason"} (oldest, always permanent) ->
        #   {ip: {"reason", "expires_at"}} ->
        #   {ip: {"reason", "expires_at", "banned_at"}} (current).
        # Old files lack banned_at -> set None (don't invent a
        # timestamp); the UI shows "unknown" for those IPs.
        try:
            if os.path.exists(self.blocked_file):
                with open(self.blocked_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                migrated: Dict[str, dict] = {}
                for ip, val in raw.items():
                    if isinstance(val, str):
                        migrated[ip] = {"reason": val, "expires_at": None, "banned_at": None}
                    elif isinstance(val, dict):
                        migrated[ip] = {
                            "reason": val.get("reason", ""),
                            "expires_at": val.get("expires_at"),
                            "banned_at": val.get("banned_at"),
                        }
                self.blocked_ips = migrated
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Failed to load antibot_blocked.json: {e}")
            self.blocked_ips = {}

    def _save_blocked(self) -> None:
        try:
            with open(self.blocked_file, "w", encoding="utf-8") as f:
                json.dump(self.blocked_ips, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Failed to save antibot_blocked.json: {e}")

    def _load_verified(self) -> None:
        try:
            if os.path.exists(self.verified_file):
                with open(self.verified_file, "r", encoding="utf-8") as f:
                    self.verified_ids = set(json.load(f))
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Failed to load antibot_verified.json: {e}")
            self.verified_ids = set()

    def _save_verified(self) -> None:
        try:
            with open(self.verified_file, "w", encoding="utf-8") as f:
                json.dump(sorted(self.verified_ids), f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Failed to save antibot_verified.json: {e}")
