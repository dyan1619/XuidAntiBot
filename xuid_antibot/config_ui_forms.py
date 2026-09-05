# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""/abmenu: the whole in-game configuration form UI.

Shared conventions:
  - Vanilla icons (add_button(text, icon=...), ModalForm(icon=...)) —
    paths checked against Bedrock's original item_texture.json/
    terrain_texture.json; every client has them, no resource pack
    needed.
  - add_header()/add_divider()/add_label() (Endstone >= 0.8.0) ONLY
    on ActionForm — do NOT use Label inside ModalForm: a Label takes
    up a slot in the on_submit json_response array, SHIFTING THE
    INDEX of the TextInputs after it (that's why the text is merged
    into the TextInput's label instead).
  - Colors: §7 (gray) ONLY for explanatory text in content/labels,
    NEVER inside button text (it washes out on the bright background).
    Two-line buttons: line 1 is the bold name "§l§f...§r", line 2 is
    the description. §c delete/danger, §a add/enable, §e current
    value.
  - Every form calls back into the exact _cmd_* handler in
    commands.py (a single source of truth, no separate branches).

See xuid_antibot/__init__.py for where this module sits in the package."""
import json
import time

from endstone import ColorFormat
from endstone.form import ActionForm, ModalForm, TextInput


# ── Shared icon set (real vanilla paths, no resource pack needed) ──
_ICON_BASIC = "textures/items/book_written"        # Basic menu
_ICON_ADVANCED = "textures/items/redstone_dust"    # Advanced menu
_ICON_VERIFY = "textures/items/name_tag"           # verification mode
_ICON_TRANSFER = "textures/items/ender_eye"        # main server transfer
_ICON_SERVERS = "textures/items/map_empty"         # server management
_ICON_BLOCK = "textures/blocks/iron_bars"          # IP blocking / security
_ICON_LOCKDOWN = "textures/blocks/barrier"         # emergency lockdown
_ICON_MAINTENANCE = "textures/blocks/barrier"      # maintenance (hard lock, OP only)
_ICON_RELOAD = "textures/blocks/lever"             # reload config from files
_ICON_RATELIMIT = "textures/items/repeater"        # rate limit
_ICON_JOINLIMIT = "textures/items/hopper"         # join limit (whole-server join load)
_ICON_TIME = "textures/items/clock_item"           # time settings
_ICON_HONEYPOT = "textures/items/comparator"       # response honeypot
_ICON_PATTERN = "textures/items/paper"             # bot name patterns
_ICON_TIMING = "textures/items/experience_bottle"  # UI offset / buffer
_ICON_ISOLATION = "textures/items/nether_star"     # isolation Y coordinate
_ICON_GEO = "textures/items/compass_item"          # country filtering
_ICON_BACK = "textures/items/arrow"                # back button
_ICON_ADD = "textures/items/emerald"               # add new
_ICON_DELETE = "textures/blocks/barrier"           # delete / danger
# The only entry outside the item/block atlas: the red X icon lives in
# the vanilla UI texture set (textures/ui); every client has it.
_ICON_X = "textures/ui/cancel"                     # red X — blocked IP list


class ConfigUiFormsMixin:
    # ── ModalForm result helper ─────────────────────────────────
    # ModalForm on_submit returns a JSON STRING containing an ARRAY
    # ordered by control (not a dict keyed by name) — every TextInput
    # form in this file is read through exactly one helper, so
    # adding/removing controls later doesn't mean editing 15
    # identical try/except blocks scattered around.

    def _read_text_inputs(self, p, json_response: str, count: int, log_label: str):
        """Read `count` TextInput values (by index) from
        json_response, stripped. Returns None on a read error (already
        logged + reported to the player) — the caller must return
        immediately and not process garbage values."""
        try:
            values = json.loads(json_response)
            return [str(values[i]).strip() for i in range(count)]
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Error reading {log_label}: {e}")
            p.send_message(f"{ColorFormat.RED}Something went wrong, please try again.")
            return None

    # ── /abmenu root menu: Basic / Advanced navigation + quick toggles ──
    # Split into 2 submenus so no single screen gets too many buttons
    # (hard to use on mobile).

    def _send_config_form(self, player) -> None:
        _maintenance_line = (
            f"§c⚠ MAINTENANCE MODE ON — only OPs can join, even verified players§r\n\n"
            if self.maintenance_enabled else ""
        )
        _lockdown_line = (
            f"§c⚠ LOCKDOWN ON — blocking new unverified players§r\n\n"
            if self.lockdown_enabled else ""
        )
        form = ActionForm(
            title="§l⚙ Antibot Config",
            content=(
                f"{_maintenance_line}"
                f"{_lockdown_line}"
                f"§7Anti-join-bot plugin — verification form for new players + IP/name/country filtering.\n"
                f"§7Select §fBasic§7 or §fAdvanced§7 below to view/change details.\n\n"
                f"Verification: §f{'Easy (1 button)' if self.easy_verify else ('Strict (typed code)' if self.strict_verify else 'Default')}§r\n"
                f"Server transfer: {'§aON' if self.transfer_enabled else '§coff'}§r\n"
                f"Rate limit: §f{self.rate_limit_count} logins/{self._format_minutes(self.rate_limit_window)}§r\n"
                f"Verification timeout: §f{self.verify_timeout}s§r\n"
                f"IP block duration: §f{self._format_ttl(self.block_ttl)}§r"
            ),
        )

        maintenance_label = "Disable Maintenance" if self.maintenance_enabled else "Enable Maintenance"
        maintenance_desc = (
            "§cHARD LOCK ACTIVE — only OPs can join§r, even verified players. Click to disable"
            if self.maintenance_enabled
            else "Hard-locks the server — ONLY OPs can join, blocking EVERYONE else, even verified players"
        )
        form.add_button(
            f"§l§f{maintenance_label}§r\n{maintenance_desc}",
            icon=_ICON_MAINTENANCE,
            on_click=lambda p: self._config_toggle_maintenance(p),
        )

        lockdown_label = "Disable Lockdown" if self.lockdown_enabled else "Enable Lockdown"
        lockdown_desc = (
            "§cBLOCKING NEW players (including real ones)§r — previously verified players still join normally. Click to disable"
            if self.lockdown_enabled
            else "Temporarily blocks NEW players (including real ones) during an attack — returning players still join normally"
        )
        form.add_button(
            f"§l§f{lockdown_label}§r\n{lockdown_desc}",
            icon=_ICON_LOCKDOWN,
            on_click=lambda p: self._config_toggle_lockdown(p),
        )

        form.add_divider()

        form.add_button(
            "§l§fBasic§r\nVerification mode, server transfer, IP block duration, verification timeout",
            icon=_ICON_BASIC,
            on_click=lambda p: self._send_config_basic_form(p),
        )
        form.add_button(
            "§l§fAdvanced§r\nRate limit, join limit, honeypot, wrong attempts, patterns, timing, isolation Y, times",
            icon=_ICON_ADVANCED,
            on_click=lambda p: self._send_config_advanced_form(p),
        )
        form.add_button(
            f"§l§f! Blocked IPs§r\n{len(self.blocked_ips)} IPs currently auto-blocked by XuidAntiBot",
            icon=_ICON_X,
            on_click=lambda p: self._send_blocked_ips_form(p),
        )
        _geo_status = (
            f"§a{self.geo_filter_mode}§8: {', '.join(self.geo_filter_countries) or '(empty)'}"
            if self.geo_filter_enabled else "§coff"
        )
        _vpn_status = "§aON" if self.geo_filter_block_vpn else "§coff"
        form.add_button(
            f"§l§fCountry Filtering§r\nNow: {_geo_status} | AntiVPN: {_vpn_status}",
            icon=_ICON_GEO,
            on_click=lambda p: self._send_geofilter_form(p),
        )
        form.add_divider()
        form.add_button(
            "§l§fReload config from files§r\nUse after manually editing antibot_config.json — safe, does not disturb online players",
            icon=_ICON_RELOAD,
            on_click=lambda p: self._config_reload(p),
        )
        player.send_form(form)

    def _config_reload(self, player) -> None:
        self._cmd_reload(player)
        self._send_config_form(player)

    # ── "Basic" menu: what admins tweak most ──

    def _send_config_basic_form(self, player) -> None:
        if self.easy_verify:
            mode_label = "Easy (1 button, only stops bots that can't click)"
        elif self.strict_verify:
            mode_label = "Strict (typed code)"
        else:
            mode_label = "Default (3 buttons to pick)"

        _maintenance_warning = (
            f"\n\n§c⚠ MAINTENANCE MODE ON§7 — only OPs can join, even verified players."
            if self.maintenance_enabled else ""
        )
        _lockdown_warning = (
            f"\n\n§c⚠ LOCKDOWN ON§7 — blocking all UNVERIFIED players."
            if self.lockdown_enabled else ""
        )
        form = ActionForm(
            title="§lBasic Config",
            content=(
                f"§fCurrent verification mode: §e{mode_label}\n"
                f"§fMain server transfer: {'§aON' if self.transfer_enabled else '§coff'} "
                f"§7({len(self.main_servers)} servers configured)"
                f"{_maintenance_warning}"
                f"{_lockdown_warning}"
            ),
        )

        form.add_button(
            f"§l§fVerification mode§r\nNow: {mode_label} — new players must pass it before playing",
            icon=_ICON_VERIFY,
            on_click=lambda p: self._send_verify_mode_form(p),
        )

        transfer_label = "Disable main server transfer" if self.transfer_enabled else "Enable main server transfer"
        form.add_button(
            f"§l§f{transfer_label}§r\nAutomatically transfers players to the real game server after verifying"
            f" (server list is under 'Main servers' right below; currently {len(self.main_servers)} servers)",
            icon=_ICON_TRANSFER,
            on_click=lambda p: self._config_toggle_transfer(p),
        )
        form.add_button(
            "§l§fMain servers§r\nDestination game servers after verification — add/edit/remove",
            icon=_ICON_SERVERS,
            on_click=lambda p: self._send_servers_manage_form(p),
        )

        form.add_divider()

        form.add_button(
            f"§l§fDefault IP block duration§r\nNow: {self._format_ttl(self.block_ttl)}"
            f" — TTL for NEWLY auto-blocked IPs (not retroactive)",
            icon=_ICON_BLOCK,
            on_click=lambda p: self._send_expiry_form(p),
        )
        _transfer_wait_note = (
            f", includes {self.transfer_choose_timeout}s server-selection wait"
            if self.transfer_enabled and len(self.main_servers) >= 2
            else ""
        )
        form.add_button(
            f"§l§fVerification timeout§r\nNow: {self.verify_timeout}s"
            f"\n(UI countdown {self.UI_VERIFY_TIMEOUT}s — less than the real kick time; "
            f"effects {self.PENDING_EFFECT_DURATION}s — longer than real{_transfer_wait_note})",
            icon=_ICON_TIME,
            on_click=lambda p: self._send_verifytime_form(p),
        )

        form.add_divider()
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_form(p))

        player.send_form(form)

    # ── Pick one of the 3 verification modes ──
    # easy/strict are 2 independent flags in the data, but only one
    # level is effective at a time (easy overrides strict, see
    # _send_verify_form) — merged into a single choice so admins can't
    # accidentally enable both.

    def _send_verify_mode_form(self, player) -> None:
        form = ActionForm(
            title="§lChoose verification mode",
            content=(
                "§eEasy§7: a single button, click to enter — §conly stops bots that cannot click buttons§7 (use only for fast entry).\n\n"
                "§eStrict§7: shows a code the player must TYPE BACK — bots can't blindly click; the strongest blocking.\n\n"
                "§eDefault§7: shows a code plus 3 buttons; the player must read the code and click the matching button — a blindly clicking bot only hits 1/3.\n\n"
                "§7Clicking a mode applies it immediately — the other two turn off automatically."
            ),
        )
        form.add_divider()

        def pick(p, target: str):
            # Drive to the exact target state regardless of the current
            # state — avoids clicking multiple toggles to line things up.
            if target == "easy":
                if not self.easy_verify:
                    self._cmd_toggle(p, ["easy"])
                if self.strict_verify:
                    self._cmd_toggle(p, ["strict"])
            elif target == "strict":
                if self.easy_verify:
                    self._cmd_toggle(p, ["easy"])
                if not self.strict_verify:
                    self._cmd_toggle(p, ["strict"])
            else:  # default
                if self.easy_verify:
                    self._cmd_toggle(p, ["easy"])
                if self.strict_verify:
                    self._cmd_toggle(p, ["strict"])
            self._send_config_basic_form(p)

        easy_mark = " §a✔" if self.easy_verify else ""
        strict_mark = " §a✔" if (self.strict_verify and not self.easy_verify) else ""
        default_mark = " §a✔" if (not self.easy_verify and not self.strict_verify) else ""

        form.add_button(f"Easy (1 button, only stops bots that can't click){easy_mark}", icon=_ICON_VERIFY, on_click=lambda p: pick(p, "easy"))
        form.add_button(f"Strict (typed code){strict_mark}", icon=_ICON_VERIFY, on_click=lambda p: pick(p, "strict"))
        form.add_button(f"Default (3 buttons to pick){default_mark}", icon=_ICON_VERIFY, on_click=lambda p: pick(p, "default"))
        form.add_divider()
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_basic_form(p))

        player.send_form(form)

    # ── "Advanced" menu: fine-tuning parameters, changed less often ──

    def _send_config_advanced_form(self, player) -> None:
        form = ActionForm(
            title="§lAdvanced Config",
            content=(
                f"§fRate limit: §e{self.rate_limit_count} logins / {self._format_minutes(self.rate_limit_window)}"
                f"   §7|§f  Join limit: {'§a' + str(self.join_limit_count) + '/' + self._format_minutes(self.join_limit_window) if self.join_limit_enabled else '§coff'}\n"
                f"§fBot name patterns: §e{len(self.name_patterns)}"
            ),
        )
        form.add_divider()

        form.add_button(
            f"§l§fRate limit threshold§r\nNow: {self.rate_limit_count} logins / {self._format_minutes(self.rate_limit_window)}"
            f" — 1 IP logging in above this threshold -> auto-block",
            icon=_ICON_RATELIMIT,
            on_click=lambda p: self._send_ratelimit_form(p),
        )
        _joinlimit_status = (
            f"§a{self.join_limit_count} joins/{self._format_minutes(self.join_limit_window)}"
            if self.join_limit_enabled else "§coff"
        )
        form.add_button(
            f"§l§fJoin limit (whole server)§r\nNow: {_joinlimit_status}"
            f" — join waves above the threshold -> kick UNVERIFIED players (protects server load)",
            icon=_ICON_JOINLIMIT,
            on_click=lambda p: self._send_joinlimit_form(p),
        )
        form.add_button(
            f"§l§fMax wrong code attempts§r\nNow: {self.max_wrong_attempts} — more wrong attempts -> kick + IP block",
            icon=_ICON_BLOCK,
            on_click=lambda p: self._send_wrongattempts_form(p),
        )
        form.add_button(
            f"§l§fResponse honeypot threshold§r\nNow: {self.reaction_time_min_ms}ms"
            f" — submitting the form faster than this -> treated as a bot (real players need time to read)",
            icon=_ICON_HONEYPOT,
            on_click=lambda p: self._send_reactiontime_form(p),
        )
        form.add_button(
            f"§l§fBot name patterns§r\n{len(self.name_patterns)} regex patterns currently applied",
            icon=_ICON_PATTERN,
            on_click=lambda p: self._send_namepattern_form(p),
        )

        form.add_divider()

        form.add_button(
            f"§l§fUI offset / effect buffer§r"
            f"\nNow: UI -{self.ui_timeout_margin}s / effects +{self.pending_effect_buffer}s"
            f" relative to the real kick time",
            icon=_ICON_TIMING,
            on_click=lambda p: self._send_uitiming_form(p),
        )
        form.add_button(
            f"§l§fMax form closes§r\nNow: {self.max_form_resends}"
            f" — closing the verification form (without clicking) more than this -> soft kick, NO IP block",
            icon=_ICON_TIME,
            on_click=lambda p: self._send_formresends_form(p),
        )
        form.add_button(
            f"§l§fIsolation Y coordinate§r\nNow: Y={self.pending_isolation_y}"
            f" — where players are held while awaiting verification (blind + movement/chat locked) until they verify",
            icon=_ICON_ISOLATION,
            on_click=lambda p: self._send_isolationy_form(p),
        )
        form.add_button(
            f"§l§fServer selection timeout§r\nNow: {self.transfer_choose_timeout}s"
            f"\n§7(only applies with transfer enabled + >=2 servers; "
            f"the player is STILL pending then — blind/isolated like unverified)§r",
            icon=_ICON_TIME,
            on_click=lambda p: self._send_transfertimeout_form(p),
        )
        form.add_button(
            f"§l§fDisplay timezone§r\nNow: "
            f"UTC{'+' if self.display_timezone_offset >= 0 else ''}{self.display_timezone_offset:g}"
            f" (affects only date/time display in /abstatus)",
            icon=_ICON_TIME,
            on_click=lambda p: self._send_timezone_form(p),
        )

        form.add_divider()
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_form(p))

        player.send_form(form)

    def _config_toggle_transfer(self, player) -> None:
        self._cmd_toggle(player, ["transfer"])
        self._send_config_basic_form(player)

    def _config_toggle_lockdown(self, player) -> None:
        self._cmd_toggle(player, ["lockdown"])
        self._send_config_form(player)

    def _config_toggle_maintenance(self, player) -> None:
        self._cmd_toggle(player, ["maintenance"])
        self._send_config_form(player)

    # ── Country filtering (geo_filter) ──────────────────────────
    # Toggle + mode switch + country list (click to remove) + add-new.

    def _send_geofilter_form(self, player) -> None:
        form = ActionForm(
            title="⚑ Country Filtering",
            content=(
                f"§7Status: {'§aON' if self.geo_filter_enabled else '§coff'}\n"
                f"§7Mode: §f{self.geo_filter_mode}"
                f" §7({'ONLY allows the countries below' if self.geo_filter_mode == 'whitelist' else 'BLOCKS the countries below, allows the rest'})\n"
                f"§7AntiVPN (blocks proxy/VPN/hosting): {'§aON' if self.geo_filter_block_vpn else '§coff'}\n"
                f"§7Country lookup via ip-api.com (cached 24h per IP). If the API fails, nobody is blocked temporarily — real players are not disturbed."
            ),
        )
        form.add_divider()

        toggle_label = "Disable country filtering" if self.geo_filter_enabled else "Enable country filtering"
        form.add_button(
            f"§l§f{toggle_label}§r",
            icon=_ICON_GEO,
            on_click=lambda p: self._config_toggle_geofilter(p),
        )
        other_mode = "blacklist" if self.geo_filter_mode == "whitelist" else "whitelist"
        form.add_button(
            f"§l§fSwitch mode to {other_mode}§r\nNow: {self.geo_filter_mode}",
            icon=_ICON_GEO,
            on_click=lambda p: self._config_switch_geofilter_mode(p, other_mode),
        )
        vpn_toggle_label = "Disable AntiVPN" if self.geo_filter_block_vpn else "Enable AntiVPN"
        form.add_button(
            f"§l§f{vpn_toggle_label}§r\nBlocks proxy/VPN/hosting IPs (independent of country filtering)",
            icon=_ICON_GEO,
            on_click=lambda p: self._config_toggle_geofilter_vpn(p),
        )

        form.add_divider()

        if self.geo_filter_countries:
            for code in self.geo_filter_countries:
                form.add_button(
                    f"§c{code}§r\nClick to remove from the list",
                    icon=_ICON_DELETE,
                    on_click=lambda p, code=code: self._config_remove_geo_country(p, code),
                )
        else:
            form.add_label("§7No country codes in the list yet.")

        form.add_divider()
        form.add_button("+ Add country codes", icon=_ICON_ADD, on_click=lambda p: self._send_add_geo_country_form(p))
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_form(p))
        player.send_form(form)

    def _config_toggle_geofilter(self, player) -> None:
        self._cmd_geofilter(player, ["off" if self.geo_filter_enabled else "on"])
        self._send_geofilter_form(player)

    def _config_switch_geofilter_mode(self, player, mode: str) -> None:
        self._cmd_geofilter(player, ["mode", mode])
        self._send_geofilter_form(player)

    def _config_toggle_geofilter_vpn(self, player) -> None:
        self._cmd_geofilter(player, ["vpn", "off" if self.geo_filter_block_vpn else "on"])
        self._send_geofilter_form(player)

    def _config_remove_geo_country(self, player, code: str) -> None:
        self._cmd_geofilter(player, ["countries", "remove", code])
        self._send_geofilter_form(player)

    def _send_add_geo_country_form(self, player) -> None:
        form = ModalForm(
            title="+ Add country codes",
            icon=_ICON_ADD,
            controls=[
                TextInput(
                    label="ISO 3166-1 alpha-2 country codes (e.g. VN, FR, US) — multiple codes separated by spaces or commas.\n"
                    "Effect depends on the current mode: whitelist = ONLY these codes are allowed, blacklist = these codes are blocked",
                    placeholder="VN",
                ),
            ],
            submit_button="Add",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "add-country form")
            if vals is None:
                return
            if vals[0]:
                self._cmd_geofilter(p, ["countries", "add", vals[0]])
            self._send_geofilter_form(p)

        def on_close(p):
            self._send_geofilter_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_uitiming_form(self, player) -> None:
        form = ModalForm(
            title="⏱ UI offset / effect buffer",
            icon=_ICON_TIMING,
            controls=[
                TextInput(
                    label=(
                        "§7Adjust the 2 DELIBERATE offsets relative to the real kick time "
                        "(the REAL time is set in \"Verification timeout\").\n"
                        "§fUI offset (seconds) — the countdown shown to the player will be SHORTER "
                        "than the real kick time by exactly this much (feels more urgent; the real allowance is still more generous)"
                    ),
                    default_value=str(self.ui_timeout_margin),
                    placeholder=str(self.DEFAULT_UI_TIMEOUT_MARGIN),
                ),
                TextInput(
                    label=(
                        "§fEffect buffer (seconds) — the blindness/invisibility effects last LONGER "
                        "than the real kick time by exactly this much (a fallback safety net in case the effect-clear command fails)"
                    ),
                    default_value=str(self.pending_effect_buffer),
                    placeholder=str(self.DEFAULT_PENDING_EFFECT_BUFFER),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 2, "UI-offset/effect form")
            if vals is None:
                return
            self._cmd_setuimargin(p, [vals[0]])
            self._cmd_seteffectbuffer(p, [vals[1]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_formresends_form(self, player) -> None:
        form = ModalForm(
            title="♻ Max form closes",
            icon=_ICON_TIME,
            controls=[
                TextInput(
                    label="Max times a player may CLOSE the verification form (without clicking a button) before a kick — a soft kick, NO IP block",
                    default_value=str(self.max_form_resends),
                    placeholder=str(self.DEFAULT_MAX_FORM_RESENDS),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "max-form-closes form")
            if vals is None:
                return
            self._cmd_setformresends(p, [vals[0]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_isolationy_form(self, player) -> None:
        form = ModalForm(
            title="✦ Isolation Y coordinate",
            icon=_ICON_ISOLATION,
            controls=[
                TextInput(
                    label=(
                        "The Y coordinate where players are held while awaiting verification "
                        "(blind/invisible, chat/movement locked) until they verify.\n"
                        "Standard Overworld build limit: -64..319."
                    ),
                    default_value=str(self.pending_isolation_y),
                    placeholder=str(self.DEFAULT_PENDING_ISOLATION_Y),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "isolation-Y form")
            if vals is None:
                return
            # The form has no way to enter the "force" flag -> an
            # out-of-range Y is rejected by _cmd_setisolationy and not
            # saved; to force a value outside the standard range, use
            # /abset isolationy <y> force directly.
            self._cmd_setisolationy(p, [vals[0]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_wrongattempts_form(self, player) -> None:
        form = ModalForm(
            title="✖ Max wrong code attempts",
            icon=_ICON_BLOCK,
            controls=[
                TextInput(
                    label="Max wrong clicks/typed codes before a kick + IP auto-ban",
                    default_value=str(self.max_wrong_attempts),
                    placeholder=str(self.DEFAULT_MAX_WRONG_ATTEMPTS),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "max-wrong-attempts form")
            if vals is None:
                return
            self._cmd_setwrongattempts(p, [vals[0]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_reactiontime_form(self, player) -> None:
        form = ModalForm(
            title="⚠ Response honeypot threshold",
            icon=_ICON_HONEYPOT,
            controls=[
                TextInput(
                    label="Submitting the form faster than this (milliseconds) after it was sent -> treated as a bot",
                    default_value=str(self.reaction_time_min_ms),
                    placeholder=str(self.DEFAULT_REACTION_TIME_MIN_MS),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "honeypot-threshold form")
            if vals is None:
                return
            self._cmd_setreactiontime(p, [vals[0]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_transfertimeout_form(self, player) -> None:
        form = ModalForm(
            title="⏱ Server selection timeout",
            icon=_ICON_TIME,
            controls=[
                TextInput(
                    label="Seconds to wait for a main server pick after verifying before an automatic kick.\n"
                    "Only applies with transfer enabled and >=2 servers",
                    default_value=str(self.transfer_choose_timeout),
                    placeholder=str(self.DEFAULT_TRANSFER_CHOOSE_TIMEOUT),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "server-selection-timeout form")
            if vals is None:
                return
            self._cmd_settransfertimeout(p, [vals[0]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    # ── Bot name pattern management ─────────────────────────────
    # List patterns (click to remove) + add-new button.

    def _send_namepattern_form(self, player) -> None:
        form = ActionForm(
            title="✎ Bot name patterns",
            content=(
                "§7Each pattern is a regex — a joining name matching any pattern is blocked IMMEDIATELY on join.\n"
                "§7Click a pattern to remove it."
                if self.name_patterns else
                "§7No patterns yet — joining names are NOT filtered by pattern.\n"
                "§7Use \"Add new pattern\" below to add one."
            ),
        )
        form.add_divider()

        for i, pat in enumerate(self.name_patterns, start=1):
            form.add_button(
                f"§c{i}. {pat}§r\nClick to remove",
                icon=_ICON_DELETE,
                on_click=lambda p, pat=pat: self._config_remove_pattern(p, pat),
            )

        if self.name_patterns:
            form.add_divider()
        form.add_button("+ Add new pattern", icon=_ICON_ADD, on_click=lambda p: self._send_add_pattern_form(p))
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_advanced_form(p))
        player.send_form(form)

    def _config_remove_pattern(self, player, pattern: str) -> None:
        self._cmd_namepattern(player, ["remove", pattern])
        self._send_namepattern_form(player)

    def _send_add_pattern_form(self, player) -> None:
        form = ModalForm(
            title="+ Add bot name pattern",
            icon=_ICON_ADD,
            controls=[
                TextInput(
                    label="Regex — a joining name matching this pattern is blocked immediately on join (e.g. ^Guest[0-9]+$)",
                    placeholder="^Bot[0-9]{3,5}$",
                ),
            ],
            submit_button="Add",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "add-pattern form")
            if vals is None:
                return
            if vals[0]:
                self._cmd_namepattern(p, ["add", vals[0]])
            self._send_namepattern_form(p)

        def on_close(p):
            self._send_namepattern_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_expiry_form(self, player) -> None:
        form = ModalForm(
            title="⛔ Default IP block duration",
            icon=_ICON_BLOCK,
            controls=[
                TextInput(
                    label="DAYS until auto-expiry for NEWLY auto-blocked IPs (0 = permanent, e.g. 0.5 = 12 hours).\n"
                    "Applies only to NEW blocks — not retroactive for already blocked IPs",
                    default_value=str(round(self.block_ttl / 86400, 4)) if self.block_ttl else "0",
                    placeholder="0",
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "block-expiry form")
            if vals is None:
                return
            self._cmd_setexpiry(p, [vals[0]])
            self._send_config_basic_form(p)

        def on_close(p):
            self._send_config_basic_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_timezone_form(self, player) -> None:
        form = ModalForm(
            title="⏰ Display timezone",
            icon=_ICON_TIME,
            controls=[
                TextInput(
                    label="Timezone offset from UTC (fractional allowed, e.g. 7, -5, 5.5). Affects only how "
                    "dates/times are displayed in /abstatus and the blocked IP list; does not change the system clock.",
                    default_value=f"{self.display_timezone_offset:g}",
                    placeholder="0",
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "display-timezone form")
            if vals is None:
                return
            self._cmd_settimezone(p, [vals[0]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_ratelimit_form(self, player) -> None:
        # The UI takes/shows the window in MINUTES — _cmd_ratelimit
        # converts minutes->seconds on submit (internally still
        # seconds).
        form = ModalForm(
            title="♻ Rate limit threshold",
            icon=_ICON_RATELIMIT,
            controls=[
                TextInput(
                    label="Max logins from the same IP within the window below",
                    default_value=str(self.rate_limit_count),
                    placeholder=str(self.DEFAULT_RATE_LIMIT_COUNT),
                ),
                TextInput(
                    label="Window length in minutes — exceeding the count above within it -> auto-block the IP",
                    default_value=self._format_minutes_value(self.rate_limit_window),
                    placeholder=self._format_minutes_value(self.DEFAULT_RATE_LIMIT_WINDOW),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 2, "rate-limit form")
            if vals is None:
                return
            self._cmd_ratelimit(p, [vals[0], vals[1]])
            self._send_config_advanced_form(p)

        def on_close(p):
            self._send_config_advanced_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    # ── Join limit: toggle + threshold edit ─────────────────────

    def _send_joinlimit_form(self, player) -> None:
        form = ActionForm(
            title="⛔ Join limit (whole server)",
            content=(
                f"§7Status: {'§aON' if self.join_limit_enabled else '§coff'}\n"
                f"§7Current threshold: §f{self.join_limit_count} joins / {self._format_minutes(self.join_limit_window)} "
                f"§7(all IPs combined, UNVERIFIED players only)\n"
                f"§7Above the threshold -> kick with an \"overloaded server\" reason. NO auto-block "
                f"(unlike the rate limit — this is not evidence of a bot)."
            ),
        )
        toggle_label = "Disable join limit" if self.join_limit_enabled else "Enable join limit"
        form.add_button(
            f"§l§f{toggle_label}§r\nProtects the server from join floods (no IP auto-block)",
            icon=_ICON_JOINLIMIT,
            on_click=lambda p: self._config_toggle_joinlimit(p),
        )
        form.add_button(
            f"§l§fChange threshold§r\nNow: {self.join_limit_count} joins / {self._format_minutes(self.join_limit_window)}",
            icon=_ICON_JOINLIMIT,
            on_click=lambda p: self._send_joinlimit_edit_form(p),
        )
        form.add_divider()
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_advanced_form(p))
        player.send_form(form)

    def _config_toggle_joinlimit(self, player) -> None:
        self._cmd_toggle(player, ["joinlimit"])
        self._send_joinlimit_form(player)

    def _send_joinlimit_edit_form(self, player) -> None:
        # The UI takes/shows the window in MINUTES — _cmd_joinlimit
        # converts minutes->seconds on submit (internally still
        # seconds).
        form = ModalForm(
            title="⛔ Join limit threshold",
            icon=_ICON_JOINLIMIT,
            controls=[
                TextInput(
                    label="Max joins (UNVERIFIED players, counted across the whole server) within the window below",
                    default_value=str(self.join_limit_count),
                    placeholder=str(self.DEFAULT_JOIN_LIMIT_COUNT),
                ),
                TextInput(
                    label="Window length in minutes — exceeding the count above within it -> kick as overloaded",
                    default_value=self._format_minutes_value(self.join_limit_window),
                    placeholder=self._format_minutes_value(self.DEFAULT_JOIN_LIMIT_WINDOW),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 2, "join-limit form")
            if vals is None:
                return
            self._cmd_joinlimit(p, [vals[0], vals[1]])
            self._send_joinlimit_form(p)

        def on_close(p):
            self._send_joinlimit_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    def _send_verifytime_form(self, player) -> None:
        extra_note = (
            f" + {self.transfer_choose_timeout}s server-selection wait"
            if self.transfer_enabled and len(self.main_servers) >= 2
            else ""
        )
        form = ModalForm(
            title="⏱ Verification timeout",
            icon=_ICON_TIME,
            controls=[
                TextInput(
                    label=(
                        "Seconds to answer the form before an automatic kick (the REAL kick time).\n"
                        f"The UI will automatically show {self.ui_timeout_margin}s less, and the effects will run "
                        f"{self.pending_effect_buffer}s longer{extra_note} than the number you enter."
                    ),
                    default_value=str(self.verify_timeout),
                    placeholder=str(self.DEFAULT_VERIFY_TIMEOUT),
                ),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 1, "verification-timeout form")
            if vals is None:
                return
            self._cmd_setverifytime(p, [vals[0]])
            self._send_config_basic_form(p)

        def on_close(p):
            self._send_config_basic_form(p)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    # ── Blocked IP list: list -> detail -> unblock confirmation ─

    def _send_blocked_ips_form(self, player) -> None:
        form = ActionForm(
            title="! Blocked IPs",
            content=(
                f"§7{len(self.blocked_ips)} IPs currently auto-blocked by XuidAntiBot.\n"
                "§7Click an IP to see the reason/duration details, or to unblock it."
                if self.blocked_ips else
                "§7No IPs are currently auto-blocked by XuidAntiBot."
            ),
        )
        form.add_divider()

        # Capped at 40 buttons per screen — ActionForm has no built-in
        # pagination and very long lists overflow the form on some
        # clients. To see everything, use /abstatus or open
        # antibot_blocked.json directly.
        _MAX_LISTED = 40
        now = time.time()
        for ip, entry in list(self.blocked_ips.items())[:_MAX_LISTED]:
            expires_at = entry.get("expires_at")
            if expires_at is None:
                when = "permanent"
            else:
                remaining = max(0, int(expires_at - now))
                when = f"{self._format_ttl(remaining)} left"
            banned_at = entry.get("banned_at")
            banned_str = self._format_display_time(banned_at, "%Y-%m-%d") if banned_at else "?"

            def make_on_click(target_ip=ip):
                def on_click(p):
                    self._send_blocked_ip_detail_form(p, target_ip)
                return on_click
            form.add_button(f"§l§f{ip}§r\n{when} | blocked {banned_str}", icon=_ICON_BLOCK, on_click=make_on_click())

        if len(self.blocked_ips) > _MAX_LISTED:
            form.add_divider()
            form.add_label(
                f"§7... and {len(self.blocked_ips) - _MAX_LISTED} more IPs not shown here "
                "§7(see /abstatus or antibot_blocked.json)."
            )

        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_form(p))
        player.send_form(form)

    def _send_blocked_ip_detail_form(self, player, ip: str) -> None:
        entry = self.blocked_ips.get(ip)
        if entry is None:
            # The IP auto-expired or another admin removed it between
            # opening the list and clicking — go back to the list
            # (already refreshed).
            player.send_message(f"{ColorFormat.GRAY}IP {ip} is no longer in the block list (it may have expired).")
            self._send_blocked_ips_form(player)
            return

        reason = entry.get("reason", "")
        expires_at = entry.get("expires_at")
        if expires_at is None:
            when = "§cPermanent (no auto-expiry)"
        else:
            remaining = max(0, int(expires_at - time.time()))
            expires_str = self._format_display_time(expires_at)
            when = f"§f{self._format_ttl(remaining)} left §7(expires at {expires_str})"

        banned_at = entry.get("banned_at")
        banned_when = (
            self._format_display_time(banned_at)
            if banned_at else
            "§7Unknown (blocked before the blocked-at timestamp feature existed)"
        )

        form = ActionForm(
            title=f"! IP: {ip}",
            content=(
                f"§fReason: §7{reason or '(none)'}\n\n"
                f"§fBlocked at: §7{banned_when}\n\n"
                f"§fDuration: {when}"
            ),
        )

        def on_unblock(p):
            self._send_unblock_confirm_form(p, ip)
        form.add_button("§cUnblock this IP", icon=_ICON_DELETE, on_click=on_unblock)
        form.add_button("« Back to the list", icon=_ICON_BACK, on_click=lambda p: self._send_blocked_ips_form(p))

        player.send_form(form)

    def _send_unblock_confirm_form(self, player, ip: str) -> None:
        # Confirm before the actual unblock — avoids misclicks.
        form = ActionForm(
            title="⚠ Confirm unblock",
            content=f"§7Unblock IP '§f{ip}§7'?\n§7The IP will be processed from scratch (rate limit, verification form...) like a new player.",
        )

        def on_confirm(p):
            if ip not in self.blocked_ips:
                p.send_message(f"{ColorFormat.GRAY}IP {ip} is no longer in the block list (it may have expired or was already unblocked).")
                return
            del self.blocked_ips[ip]
            self._save_blocked()
            p.send_message(f"{ColorFormat.GREEN}Unblocked IP {ip}.")

        def on_cancel(p):
            p.send_message(f"{ColorFormat.GRAY}Cancelled; IP {ip} remains blocked.")
            self._send_blocked_ip_detail_form(p, ip)

        form.add_button("§cUnblock", icon=_ICON_DELETE, on_click=on_confirm)
        form.add_button("Cancel", icon=_ICON_BACK, on_click=on_cancel)
        player.send_form(form)

    # ── Main server management: list -> edit/remove, add-new ────

    def _send_servers_manage_form(self, player) -> None:
        form = ActionForm(
            title="⚙ Main servers",
            content=(
                "§7Servers players are transferred to after verifying.\n"
                "§7Click a server to edit/remove it, or add a new one below."
                if self.main_servers else
                "§7No servers yet — players who verify will stay here.\n"
                "§7Use \"Add new server\" to configure one."
            ),
        )
        form.add_divider()

        for s in self.main_servers:
            def make_on_click(target=s):
                def on_click(p):
                    self._send_edit_server_form(p, target)
                return on_click
            form.add_button(f"§l§f{s['name']}§r\n{s['host']}:{s['port']}", icon=_ICON_SERVERS, on_click=make_on_click())

        if self.main_servers:
            form.add_divider()

        def on_add(p):
            self._send_add_server_form(p)
        form.add_button("§a+ Add new server", icon=_ICON_ADD, on_click=on_add)
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_config_basic_form(p))

        player.send_form(form)

    def _send_add_server_form(self, player) -> None:
        form = ModalForm(
            title="+ Add main server",
            icon=_ICON_ADD,
            controls=[
                TextInput(label="Display name (e.g. Main Server 1)", placeholder="Server name"),
                TextInput(label="Host/IP", placeholder="e.g. 127.0.0.1 or mc.example.com"),
                TextInput(label="Port", placeholder="19132", default_value="19132"),
            ],
            submit_button="Add",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 3, "add-server form")
            if vals is None:
                return
            name, host, port_str = vals

            if not host:
                p.send_message(f"{ColorFormat.RED}Host cannot be empty — no server was added.")
                return
            if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
                p.send_message(f"{ColorFormat.RED}Invalid port (must be 1-65535) — no server was added.")
                return

            self.main_servers.append({
                "name": name or host,
                "host": host,
                "port": int(port_str),
            })
            self._save_config()
            p.send_message(f"{ColorFormat.GREEN}Added server '{name or host}' ({host}:{port_str}).")

        form.on_submit = on_submit
        player.send_form(form)

    def _find_server_index(self, target: dict):
        # Match by host+port (not by index) to avoid editing/removing
        # the wrong entry if the list changed between opening the form
        # and clicking (e.g. 2 admins editing at once).
        return next(
            (i for i, s in enumerate(self.main_servers)
             if s["host"] == target["host"] and s["port"] == target["port"]),
            None,
        )

    def _send_edit_server_form(self, player, target: dict) -> None:
        # Two explicit buttons "Edit details" / "Delete server" —
        # mis-clicking the name/host/port fields can never delete
        # anything.
        form = ActionForm(
            title=f"⚙ Server: {target['name']}",
            content=f"§7{target['host']}:{target['port']}",
        )
        form.add_divider()

        def on_edit(p):
            self._send_edit_fields_form(p, target)
        form.add_button("Edit details", icon=_ICON_SERVERS, on_click=on_edit)

        def on_delete(p):
            self._send_delete_confirm_form(p, target)
        form.add_button("§cDelete server", icon=_ICON_DELETE, on_click=on_delete)
        form.add_button("« Back", icon=_ICON_BACK, on_click=lambda p: self._send_servers_manage_form(p))

        player.send_form(form)

    def _send_edit_fields_form(self, player, target: dict) -> None:
        form = ModalForm(
            title=f"✎ Edit server: {target['name']}",
            icon=_ICON_SERVERS,
            controls=[
                TextInput(label="Display name", default_value=target["name"]),
                TextInput(label="Host/IP", default_value=target["host"]),
                TextInput(label="Port", default_value=str(target["port"])),
            ],
            submit_button="Save",
        )

        def on_submit(p, json_response: str) -> None:
            vals = self._read_text_inputs(p, json_response, 3, "edit-server form")
            if vals is None:
                return
            name, host, port_str = vals

            idx = self._find_server_index(target)
            if idx is None:
                p.send_message(f"{ColorFormat.RED}This server was changed/removed by someone else; start over.")
                return

            if not host:
                p.send_message(f"{ColorFormat.RED}Host cannot be empty — nothing was saved.")
                return
            if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
                p.send_message(f"{ColorFormat.RED}Invalid port (must be 1-65535) — nothing was saved.")
                return

            self.main_servers[idx] = {"name": name or host, "host": host, "port": int(port_str)}
            self._save_config()
            p.send_message(f"{ColorFormat.GREEN}Saved changes for server '{name or host}'.")

        form.on_submit = on_submit
        player.send_form(form)

    def _send_delete_confirm_form(self, player, target: dict) -> None:
        # Separate confirmation before the real delete — cannot be
        # undone.
        form = ActionForm(
            title="⚠ Confirm delete",
            content=f"§7Delete server '§f{target['name']}§7' (§f{target['host']}:{target['port']}§7)?\n§cThis cannot be undone.",
        )

        def on_confirm(p):
            idx = self._find_server_index(target)
            if idx is None:
                p.send_message(f"{ColorFormat.RED}This server was changed/removed by someone else; start over.")
                return
            removed = self.main_servers.pop(idx)
            self._save_config()
            p.send_message(f"{ColorFormat.GREEN}Deleted server '{removed['name']}'.")

        def on_cancel(p):
            p.send_message(f"{ColorFormat.GRAY}Cancelled; the server was not deleted.")

        form.add_button("§cDelete", icon=_ICON_DELETE, on_click=on_confirm)
        form.add_button("Cancel", icon=_ICON_BACK, on_click=on_cancel)
        player.send_form(form)
