# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""on_command + every /ab... command.

6 top-level commands: /abstatus (status), /abreload (reload the 3 JSON
files), /abunblock (unblock an IP), /abgeo (geo filter + AntiVPN),
/abmenu (form UI), /abset (the all-in-one config command, 20 keys).
The _cmd_* handlers are the shared BACKEND for both typed commands and
the /abmenu forms (config_ui_forms.py calls back into exactly these
functions — a single source of truth).

See xuid_antibot/__init__.py for where this module sits in the package."""
import re
import time

from endstone import ColorFormat, Player
from endstone.command import Command, CommandSender


class CommandsMixin:
    # ── Router: dispatch the 6 top-level commands ───────────────

    def on_command(self, sender: CommandSender, command: Command, args: list) -> bool:
        if command.name == "abstatus":
            return self._cmd_status_and_servers(sender)
        if command.name == "abreload":
            return self._cmd_reload(sender)
        if command.name == "abunblock":
            return self._cmd_unblock(sender, args)
        if command.name == "abgeo":
            return self._cmd_geofilter(sender, args)
        if command.name == "abmenu":
            return self._cmd_config(sender)
        if command.name == "abset":
            return self._cmd_abset(sender, args)
        return False

    # ── /abstatus: status + main server list ────────────────────

    def _cmd_status_and_servers(self, sender: CommandSender) -> bool:
        # Combines XuidAntiBot status + the main server list into one command.
        self._cmd_status(sender)
        sender.send_message("")
        self._cmd_servers(sender)
        return True

    # ── /abreload: reload the 3 JSON files ──────────────────

    def _cmd_reload(self, sender: CommandSender) -> bool:
        # Re-read config/blocked/verified from the files — like the
        # load step of on_enable but WITHOUT restarting the plugin:
        # nobody is kicked, pending players are untouched, and no
        # other runtime state is reset (_login_times, _join_times,
        # pending_ids...). The _load_* functions validate on their
        # own, so the plugin cannot crash from this.
        try:
            self._load_config()
            self._load_blocked()
            self._load_verified()
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Error during /abreload: {e}")
            sender.send_error_message(
                f"Failed to reload the config — see the server log for details. "
                f"The running values may not have changed."
            )
            return True
        sender.send_message(
            f"{ColorFormat.GREEN}Config reloaded from files "
            f"{ColorFormat.GRAY}(antibot_config.json, antibot_blocked.json, antibot_verified.json). "
            f"{ColorFormat.WHITE}{len(self.blocked_ips)} blocked IPs, "
            f"{ColorFormat.WHITE}{len(self.verified_ids)} verified players."
        )
        return True

    def _cmd_status(self, sender: CommandSender) -> bool:
        sender.send_message(f"{ColorFormat.GOLD}=== XuidAntiBot — status ===")
        if self.maintenance_enabled:
            sender.send_message(
                f"{ColorFormat.RED}⚠ MAINTENANCE MODE IS ON {ColorFormat.GRAY}"
                f"— blocks EVERYONE except OPs, including verified players. "
                f"Disable with /abset maintenance off or /abmenu."
            )
        if self.lockdown_enabled:
            sender.send_message(
                f"{ColorFormat.RED}⚠ LOCKDOWN IS ON {ColorFormat.GRAY}"
                f"— blocks all UNVERIFIED players. "
                f"Disable with /abset lockdown off or /abmenu."
            )
        sender.send_message(
            f"{ColorFormat.YELLOW}Verification timeout (real kick): {ColorFormat.WHITE}"
            f"{self.verify_timeout}s "
            f"{ColorFormat.GRAY}(UI shows {self.UI_VERIFY_TIMEOUT}s, effects last {self.PENDING_EFFECT_DURATION}s)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Rate limit: {ColorFormat.WHITE}"
            f"{self.rate_limit_count} logins / {self._format_minutes(self.rate_limit_window)} "
            f"{ColorFormat.GRAY}(more than N logins in this window from the same IP -> auto-block)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Join limit (whole server): {ColorFormat.WHITE}"
            f"{'ON' if self.join_limit_enabled else 'off'} "
            f"{ColorFormat.GRAY}({self.join_limit_count} joins/{self._format_minutes(self.join_limit_window)}, "
            f"all IPs combined, UNVERIFIED players only -> kick as overloaded, no auto-block)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Easy verification (1 button, only stops bots that can't click): {ColorFormat.WHITE}"
            f"{'ON' if self.easy_verify else 'off'}"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Strict verification (typed code): {ColorFormat.WHITE}"
            f"{'ON' if self.strict_verify else 'off'}"
            f"{ColorFormat.GRAY}{' (overridden by easy verification)' if self.easy_verify and self.strict_verify else ''}"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Display timezone: {ColorFormat.WHITE}"
            f"UTC{'+' if self.display_timezone_offset >= 0 else ''}{self.display_timezone_offset:g} "
            f"{ColorFormat.GRAY}(affects only how dates/times are displayed, does not change the system clock)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Default IP block duration (new blocks): {ColorFormat.WHITE}"
            f"{self._format_ttl(self.block_ttl)}"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Transfer to main server after verification: {ColorFormat.WHITE}"
            f"{'ON' if self.transfer_enabled else 'off'} "
            f"{ColorFormat.GRAY}({len(self.main_servers)} servers configured, details below)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Main server selection timeout: {ColorFormat.WHITE}"
            f"{self.transfer_choose_timeout}s"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Max wrong code attempts: {ColorFormat.WHITE}"
            f"{self.max_wrong_attempts} "
            f"{ColorFormat.GRAY}(more wrong attempts than this -> kick + auto-block)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Form response honeypot threshold: {ColorFormat.WHITE}"
            f"{self.reaction_time_min_ms}ms "
            f"{ColorFormat.GRAY}(submitting faster than this -> treated as a bot)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Bot name patterns: {ColorFormat.WHITE}"
            f"{len(self.name_patterns)} patterns "
            f"{ColorFormat.GRAY}(see details with /abset namepattern list)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}UI offset / effect buffer: {ColorFormat.WHITE}"
            f"-{self.ui_timeout_margin}s / +{self.pending_effect_buffer}s "
            f"{ColorFormat.GRAY}(offset between the UI timer and the blindness/invisibility effects during verification)"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Max form closes: {ColorFormat.WHITE}"
            f"{self.max_form_resends}"
        )
        sender.send_message(
            f"{ColorFormat.YELLOW}Isolation Y coordinate: {ColorFormat.WHITE}"
            f"{self.pending_isolation_y} "
            f"{ColorFormat.GRAY}(the Y level used to hold players while they await verification)"
        )
        sender.send_message(f"{ColorFormat.YELLOW}Auto-blocked IPs: {ColorFormat.WHITE}{len(self.blocked_ips)}")
        now = time.time()
        for ip, entry in list(self.blocked_ips.items())[:20]:
            reason = entry.get("reason", "")
            expires_at = entry.get("expires_at")
            if expires_at is None:
                when = "permanent"
            else:
                remaining = max(0, int(expires_at - now))
                when = f"{self._format_ttl(remaining)} left"
            banned_at = entry.get("banned_at")
            banned_str = self._format_display_time(banned_at, "%Y-%m-%d") if banned_at else "?"
            sender.send_message(f"{ColorFormat.GRAY}  - {ip}: {reason} [{when}] {ColorFormat.DARK_GRAY}(blocked: {banned_str})")
        if len(self.blocked_ips) > 20:
            sender.send_message(f"{ColorFormat.GRAY}  ... and {len(self.blocked_ips) - 20} more IPs")
        return True

    # ── Time formatting helpers ─────────────────────────────────

    @staticmethod
    def _format_ttl(seconds: int) -> str:
        if seconds <= 0:
            return "permanent"
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if not parts and secs:
            parts.append(f"{secs}s")
        return " ".join(parts) if parts else "0s"

    @staticmethod
    def _format_minutes(seconds: int) -> str:
        # For SHORT windows (rate limit/join limit) — no days/hours
        # because real windows never get that long.
        if seconds < 60:
            return f"{seconds} {'second' if seconds == 1 else 'seconds'}"
        minutes, secs = divmod(seconds, 60)
        minute_word = "minute" if minutes == 1 else "minutes"
        if secs:
            sec_word = "second" if secs == 1 else "seconds"
            return f"{minutes} {minute_word} {secs} {sec_word}"
        return f"{minutes} {minute_word}"

    @staticmethod
    def _format_minutes_value(seconds: int) -> str:
        # Like _format_minutes but returns a PURE NUMBER (for
        # default_value/placeholder of a TextInput), fractional
        # allowed (e.g. 90s -> "1.5").
        minutes = seconds / 60
        if minutes == int(minutes):
            return str(int(minutes))
        return f"{minutes:g}"

    # ── /abunblock: remove an IP from the blacklist ─────────────

    def _cmd_unblock(self, sender: CommandSender, args: list) -> bool:
        if not args:
            sender.send_error_message("Usage: /abunblock <ip>")
            return True
        ip = args[0]
        if ip not in self.blocked_ips:
            sender.send_error_message(f"IP {ip} is not in the XuidAntiBot auto-block list.")
            return True
        del self.blocked_ips[ip]
        self._save_blocked()
        sender.send_message(f"{ColorFormat.GREEN}Unblocked IP {ip}.")
        return True

    # ── Toggles on|off: argument resolution + 6 thin wrappers ───

    def _resolve_toggle_value(self, sender: CommandSender, args: list, current: bool, key_name: str):
        """Returns (new_value, ok). new_value=None + ok=False means
        bad syntax (an error message was already sent); the caller
        must return True immediately without further processing."""
        if not args:
            return (not current, True)
        arg = args[0].lower()
        if arg == "on":
            return (True, True)
        if arg == "off":
            return (False, True)
        sender.send_error_message(
            f"Usage: /abset {key_name} [on|off]  (no argument = toggle the current state)"
        )
        return (None, False)

    def _cmd_toggle_strict(self, sender: CommandSender, args: list) -> bool:
        new_value, ok = self._resolve_toggle_value(sender, args, self.strict_verify, "strict")
        if not ok:
            return True
        return self._cmd_toggle(sender, ["strict"], new_value)

    def _cmd_toggle_easy(self, sender: CommandSender, args: list) -> bool:
        new_value, ok = self._resolve_toggle_value(sender, args, self.easy_verify, "easy")
        if not ok:
            return True
        return self._cmd_toggle(sender, ["easy"], new_value)

    def _cmd_toggle_transfer(self, sender: CommandSender, args: list) -> bool:
        new_value, ok = self._resolve_toggle_value(sender, args, self.transfer_enabled, "transfer")
        if not ok:
            return True
        return self._cmd_toggle(sender, ["transfer"], new_value)

    def _cmd_toggle_lockdown(self, sender: CommandSender, args: list) -> bool:
        new_value, ok = self._resolve_toggle_value(sender, args, self.lockdown_enabled, "lockdown")
        if not ok:
            return True
        return self._cmd_toggle(sender, ["lockdown"], new_value)

    def _cmd_toggle_maintenance(self, sender: CommandSender, args: list) -> bool:
        new_value, ok = self._resolve_toggle_value(sender, args, self.maintenance_enabled, "maintenance")
        if not ok:
            return True
        return self._cmd_toggle(sender, ["maintenance"], new_value)

    def _cmd_toggle_joinlimit(self, sender: CommandSender, args: list) -> bool:
        new_value, ok = self._resolve_toggle_value(sender, args, self.join_limit_enabled, "joinlimit")
        if not ok:
            return True
        return self._cmd_toggle(sender, ["joinlimit"], new_value)

    def _cmd_toggle(self, sender: CommandSender, args: list, new_value: bool = None) -> bool:
        # new_value=None (internal call, e.g. from config_ui_forms.py)
        # -> TOGGLE like the original behavior; the _cmd_toggle_*
        # wrappers resolve on/off/toggle from real args and pass
        # new_value explicitly.
        sub = args[0].lower() if args else ""
        if sub == "strict":
            self.strict_verify = (not self.strict_verify) if new_value is None else new_value
            self._save_config()
            state = "ON" if self.strict_verify else "OFF"
            sender.send_message(
                f"{ColorFormat.GREEN}Strict verification mode turned {state}. "
                f"{ColorFormat.GRAY}"
                f"({'New players must type the code back; there is no button to click.' if self.strict_verify else 'Back to the 3-button verification.'})"
            )
            return True

        if sub == "easy":
            self.easy_verify = (not self.easy_verify) if new_value is None else new_value
            self._save_config()
            state = "ON" if self.easy_verify else "OFF"
            sender.send_message(
                f"{ColorFormat.GREEN}Easy verification mode turned {state}. "
                f"{ColorFormat.GRAY}"
                f"({'The form has a single button, click to pass — only stops bots that cannot click buttons; the rest relies on the layers before the form.' if self.easy_verify else 'Back to normal verification (per strict_verify).'})"
            )
            return True

        if sub == "transfer":
            self.transfer_enabled = (not self.transfer_enabled) if new_value is None else new_value
            self._save_config()
            state = "ON" if self.transfer_enabled else "OFF"
            if self.transfer_enabled and not self.main_servers:
                sender.send_message(
                    f"{ColorFormat.YELLOW}Main server transfer is now ON, but NO servers "
                    f"are configured yet — players will stay here until you add "
                    f"servers with /abset mainservers."
                )
            else:
                sender.send_message(
                    f"{ColorFormat.GREEN}Main server transfer after verification turned {state}. "
                    f"{ColorFormat.GRAY}({len(self.main_servers)} servers configured.)"
                )
            return True

        if sub == "lockdown":
            self.lockdown_enabled = (not self.lockdown_enabled) if new_value is None else new_value
            self._save_config()
            if self.lockdown_enabled:
                sender.send_message(
                    f"{ColorFormat.RED}Lockdown is now ON. "
                    f"{ColorFormat.GRAY}All players who have NEVER verified will be blocked from joining. "
                    f"Previously verified players still join normally. "
                    f"Use /abset lockdown off (or /abmenu) to disable."
                )
            else:
                sender.send_message(
                    f"{ColorFormat.GREEN}Lockdown is now OFF. "
                    f"{ColorFormat.GRAY}New players can join again (still passing the other XuidAntiBot filters)."
                )
            return True

        if sub == "maintenance":
            self.maintenance_enabled = (not self.maintenance_enabled) if new_value is None else new_value
            self._save_config()
            if self.maintenance_enabled:
                sender.send_message(
                    f"{ColorFormat.RED}Maintenance mode is now ON. "
                    f"{ColorFormat.GRAY}Blocking EVERYONE except OPs — including verified players. "
                    f"Use /abset maintenance off (or /abmenu) to disable."
                )
            else:
                sender.send_message(
                    f"{ColorFormat.GREEN}Maintenance mode is now OFF. "
                    f"{ColorFormat.GRAY}Everyone can join again (still passing the other XuidAntiBot filters)."
                )
            return True

        if sub == "joinlimit":
            self.join_limit_enabled = (not self.join_limit_enabled) if new_value is None else new_value
            self._save_config()
            state = "ON" if self.join_limit_enabled else "OFF"
            sender.send_message(
                f"{ColorFormat.GREEN}Join limit turned {state}. "
                f"{ColorFormat.GRAY}"
                f"(Current threshold: {self.join_limit_count} joins/{self._format_minutes(self.join_limit_window)}, "
                f"counted across the WHOLE server for UNVERIFIED players — change with /abset joinlimitconfig <count> <window_minutes>.)"
            )
            return True

        # Safe fallback if called directly with an unknown sub.
        sender.send_error_message("Invalid sub-command (internal) — use /abset <strict|easy|transfer|lockdown|maintenance|joinlimit>.")
        return True

    # ── /abset blockexpiry: TTL for new IP blocks ───────────────

    def _cmd_setexpiry(self, sender: CommandSender, args: list) -> bool:
        # Input is in DAYS (float, 0.5 = 12 hours); internally
        # block_ttl stays in SECONDS — converted as soon as input
        # arrives. Applies only to NEW blocks, not retroactive for
        # already-blocked IPs (to change an old IP: unblock with
        # /abunblock and let it re-block under the new TTL).
        if not args:
            sender.send_error_message("Usage: /abset blockexpiry <days|0>  (0 = permanent, e.g. 0.5 = 12 hours)")
            return True
        try:
            days = float(args[0])
        except ValueError:
            sender.send_error_message("Usage: /abset blockexpiry <days|0>  (0 = permanent, e.g. 0.5 = 12 hours)")
            return True
        if days < 0:
            sender.send_error_message("Days cannot be negative.")
            return True
        seconds = round(days * 86400)
        self.block_ttl = seconds
        self._save_config()
        if seconds == 0:
            sender.send_message(
                f"{ColorFormat.GREEN}Set: IPs auto-blocked from now on are blocked PERMANENTLY "
                f"(no auto-expiry)."
            )
        else:
            sender.send_message(
                f"{ColorFormat.GREEN}Set: IPs auto-blocked from now on expire after "
                f"{ColorFormat.WHITE}{self._format_ttl(seconds)}{ColorFormat.GREEN}. "
                f"{ColorFormat.GRAY}(If the attacker keeps using that IP while it is still blocked, "
                f"the expiry is automatically renewed/extended from scratch.)"
            )
        return True

    # ── /abset timezone: display timezone ───────────────────────

    def _cmd_settimezone(self, sender: CommandSender, args: list) -> bool:
        # Affects ONLY how dates/times are displayed, not the
        # TTL/rate-limit logic (still computed in UTC epoch). Limited
        # to -12..+14 like real UTC offsets.
        if not args:
            sender.send_error_message("Usage: /abset timezone <offset>  (e.g. 7, -5, 5.5; UTC -12..+14)")
            return True
        try:
            offset = float(args[0])
        except ValueError:
            sender.send_error_message("Usage: /abset timezone <offset>  (e.g. 7, -5, 5.5; UTC -12..+14)")
            return True
        if offset < -12 or offset > 14:
            sender.send_error_message("Invalid offset. Must be between -12 and +14 (real UTC timezones).")
            return True
        self.display_timezone_offset = offset
        self._save_config()
        sign = "+" if offset >= 0 else ""
        sender.send_message(
            f"{ColorFormat.GREEN}Display timezone set to: {ColorFormat.WHITE}UTC{sign}{offset:g}"
            f"{ColorFormat.GREEN}. {ColorFormat.GRAY}(Affects only how dates/times are displayed; does not change the TTL/rate-limit logic.)"
        )
        return True

    # ── /abset verifytimeout ────────────────────────────────────

    def _cmd_setverifytime(self, sender: CommandSender, args: list) -> bool:
        # Not retroactive for players CURRENTLY pending (their
        # timeout was already scheduled with the OLD value at join
        # time) — only applies to the next joins.
        # UI_VERIFY_TIMEOUT/PENDING_EFFECT_DURATION are properties, so
        # they recompute from the new value immediately.
        if not args or not args[0].isdigit():
            sender.send_error_message("Usage: /abset verifytimeout <seconds>  (e.g. /abset verifytimeout 120)")
            return True
        seconds = int(args[0])
        if seconds < 1:
            sender.send_error_message("Seconds must be >= 1.")
            return True
        self.verify_timeout = seconds
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Verification timeout set to: "
            f"{ColorFormat.WHITE}{seconds} seconds{ColorFormat.GREEN} "
            f"(the real kick time). The UI will show {ColorFormat.WHITE}{self.UI_VERIFY_TIMEOUT} seconds"
            f"{ColorFormat.GREEN}, and the blindness/invisibility effects will last "
            f"{ColorFormat.WHITE}{self.PENDING_EFFECT_DURATION} seconds{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(Applies only to players joining AFTER this command.)"
        )
        return True

    # ── /abset ratelimit: per-IP rate limit threshold ───────────

    def _cmd_ratelimit(self, sender: CommandSender, args: list) -> bool:
        # The window is entered in MINUTES (float); internally it
        # stays in SECONDS — converted as soon as input arrives. No
        # need to reset the recorded _login_times (the sliding window
        # computes correctly under the new window size).
        default_window_min = self.DEFAULT_RATE_LIMIT_WINDOW / 60
        if len(args) < 2 or not args[0].isdigit():
            sender.send_error_message(
                "Usage: /abset ratelimit <count> <window_minutes>  "
                f"(e.g. /abset ratelimit {self.DEFAULT_RATE_LIMIT_COUNT} {default_window_min:g})"
            )
            return True
        count = int(args[0])
        try:
            window_minutes = float(args[1])
        except ValueError:
            sender.send_error_message(
                "Usage: /abset ratelimit <count> <window_minutes>  "
                f"(e.g. /abset ratelimit {self.DEFAULT_RATE_LIMIT_COUNT} {default_window_min:g})"
            )
            return True
        if count < 1:
            sender.send_error_message("count must be >= 1 (at least 1 login for it to mean anything).")
            return True
        window = round(window_minutes * 60)
        if window < 1:
            sender.send_error_message("window_minutes must correspond to >= 1 second.")
            return True
        self.rate_limit_count = count
        self.rate_limit_window = window
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Rate limit set: more than "
            f"{ColorFormat.WHITE}{count} logins{ColorFormat.GREEN} within "
            f"{ColorFormat.WHITE}{self._format_minutes(window)}{ColorFormat.GREEN} from the same IP -> auto-block."
        )
        return True

    # ── /abset joinlimitconfig: whole-server join limit ─────────

    def _cmd_joinlimit(self, sender: CommandSender, args: list) -> bool:
        # Counted across ALL IPs (protects server load), unlike
        # _cmd_ratelimit (per-IP counting to catch bots). The window
        # is entered in MINUTES; internally SECONDS. The feature
        # toggle is the separate "joinlimit" key.
        default_window_min = self.DEFAULT_JOIN_LIMIT_WINDOW / 60
        if len(args) < 2 or not args[0].isdigit():
            sender.send_error_message(
                "Usage: /abset joinlimitconfig <count> <window_minutes>  "
                f"(e.g. /abset joinlimitconfig {self.DEFAULT_JOIN_LIMIT_COUNT} {default_window_min:g})"
            )
            return True
        count = int(args[0])
        try:
            window_minutes = float(args[1])
        except ValueError:
            sender.send_error_message(
                "Usage: /abset joinlimitconfig <count> <window_minutes>  "
                f"(e.g. /abset joinlimitconfig {self.DEFAULT_JOIN_LIMIT_COUNT} {default_window_min:g})"
            )
            return True
        if count < 1:
            sender.send_error_message("count must be >= 1 (at least 1 join for it to mean anything).")
            return True
        window = round(window_minutes * 60)
        if window < 1:
            sender.send_error_message("window_minutes must correspond to >= 1 second.")
            return True
        self.join_limit_count = count
        self.join_limit_window = window
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Join limit set: more than "
            f"{ColorFormat.WHITE}{count} joins{ColorFormat.GREEN} (UNVERIFIED players) within "
            f"{ColorFormat.WHITE}{self._format_minutes(window)}{ColorFormat.GREEN} counted across the WHOLE server -> kick as overloaded. "
            f"{ColorFormat.GRAY}"
            f"({'The feature is currently ON.' if self.join_limit_enabled else 'Note: the feature is currently OFF; enable it with /abset joinlimit.'})"
        )
        return True

    # ── /abset namepattern: manage bot-name regexes ─────────────

    def _cmd_namepattern(self, sender: CommandSender, args: list) -> bool:
        # add: validate the regex before saving (avoid a crash on the
        # next load). remove: by the pattern string OR its list index.
        # list: show entries with their indexes.
        sub = args[0].lower() if args else ""

        if sub == "list" or not sub:
            if not self.name_patterns:
                sender.send_message(f"{ColorFormat.YELLOW}No patterns yet (empty list).")
            else:
                sender.send_message(f"{ColorFormat.GOLD}=== XuidAntiBot — name_patterns ===")
                for i, pat in enumerate(self.name_patterns, start=1):
                    sender.send_message(f"{ColorFormat.GRAY}  {i}. {ColorFormat.WHITE}{pat}")
            return True

        if sub == "add":
            if len(args) < 2:
                sender.send_error_message("Usage: /abset namepattern add <regex>  (e.g. /abset namepattern add ^Guest[0-9]+$)")
                return True
            pattern = " ".join(args[1:])
            try:
                re.compile(pattern)
            except re.error as e:
                sender.send_error_message(f"Invalid regex: {e}")
                return True
            if pattern in self.name_patterns:
                sender.send_error_message("This pattern is already in the list.")
                return True
            self.name_patterns.append(pattern)
            self._name_re = [re.compile(p) for p in self.name_patterns]
            self._save_config()
            sender.send_message(
                f"{ColorFormat.GREEN}Pattern added: {ColorFormat.WHITE}{pattern}{ColorFormat.GREEN}. "
                f"{ColorFormat.GRAY}({len(self.name_patterns)} patterns now.)"
            )
            return True

        if sub == "remove":
            if len(args) < 2:
                sender.send_error_message("Usage: /abset namepattern remove <regex|index>")
                return True
            target = " ".join(args[1:])
            # Delete by index (handy for long patterns) or by the exact
            # regex string.
            removed = None
            if target.isdigit():
                idx = int(target) - 1
                if 0 <= idx < len(self.name_patterns):
                    removed = self.name_patterns.pop(idx)
            elif target in self.name_patterns:
                self.name_patterns.remove(target)
                removed = target
            if removed is None:
                sender.send_error_message("Pattern not found (check again with /abset namepattern list).")
                return True
            self._name_re = [re.compile(p) for p in self.name_patterns]
            self._save_config()
            sender.send_message(
                f"{ColorFormat.GREEN}Pattern removed: {ColorFormat.WHITE}{removed}{ColorFormat.GREEN}. "
                f"{ColorFormat.GRAY}({len(self.name_patterns)} patterns left"
                f"{', NO patterns left — only the _/xuid/rate-limit layers remain' if not self.name_patterns else ''}.)"
            )
            return True

        sender.send_error_message("Usage: /abset namepattern <add|remove|list> [pattern]")
        return True

    # ── /abgeo: manage the geo filter + AntiVPN ─────────────────

    def _cmd_geofilter(self, sender: CommandSender, args: list) -> bool:
        #   /abgeo on|off            — toggle country filtering
        #   /abgeo mode whitelist|blacklist
        #   /abgeo countries add|remove|list <codes...>
        #   /abgeo vpn on|off        — toggle AntiVPN
        # Filtering details: see geo_filter.py.
        sub = args[0].lower() if args else ""

        if sub == "vpn":
            action = args[1].lower() if len(args) > 1 else ""
            if action not in ("on", "off"):
                sender.send_error_message("Usage: /abgeo vpn <on|off>")
                return True
            self.geo_filter_block_vpn = (action == "on")
            self._save_config()
            state = "ON" if self.geo_filter_block_vpn else "OFF"
            sender.send_message(
                f"{ColorFormat.GREEN}Proxy/VPN/hosting IP blocking (AntiVPN) turned {state}. "
                f"{ColorFormat.GRAY}(independent of country filtering; shares the IP lookup data)"
            )
            return True

        if sub in ("on", "off"):
            self.geo_filter_enabled = (sub == "on")
            self._save_config()
            state = "ON" if self.geo_filter_enabled else "OFF"
            if self.geo_filter_enabled and not self.geo_filter_countries:
                sender.send_message(
                    f"{ColorFormat.YELLOW}Country filtering is ON, but there are NO country codes "
                    f"in the list yet — this filter layer will NOT block anyone until you add "
                    f"codes with /abgeo countries add <code>."
                )
            else:
                sender.send_message(
                    f"{ColorFormat.GREEN}Country-based connection filtering turned {state}. "
                    f"{ColorFormat.GRAY}(mode={self.geo_filter_mode}, countries={self.geo_filter_countries})"
                )
            return True

        if sub == "mode":
            if len(args) < 2 or args[1].lower() not in ("whitelist", "blacklist"):
                sender.send_error_message("Usage: /abgeo mode <whitelist|blacklist>")
                return True
            self.geo_filter_mode = args[1].lower()
            self._save_config()
            desc = (
                f"ONLY allows these countries: {self.geo_filter_countries}"
                if self.geo_filter_mode == "whitelist"
                else f"BLOCKS these countries: {self.geo_filter_countries}, allows the rest"
            )
            sender.send_message(f"{ColorFormat.GREEN}geofilter mode set to {self.geo_filter_mode}. {ColorFormat.GRAY}({desc})")
            return True

        if sub == "countries":
            action = args[1].lower() if len(args) > 1 else ""
            if action == "list" or not action:
                if not self.geo_filter_countries:
                    sender.send_message(f"{ColorFormat.YELLOW}The country list is empty.")
                else:
                    sender.send_message(
                        f"{ColorFormat.GOLD}=== XuidAntiBot — geofilter countries "
                        f"({self.geo_filter_mode}) ==={ColorFormat.WHITE} {', '.join(self.geo_filter_countries)}"
                    )
                return True

            if action in ("add", "remove"):
                if len(args) < 3:
                    sender.send_error_message(f"Usage: /abgeo countries {action} <country_codes...>  (e.g. VN, FR)")
                    return True
                # ISO 3166-1 alpha-2 — normalized to uppercase; enter
                # multiple codes separated by spaces and/or commas.
                raw_codes = " ".join(args[2:]).replace(",", " ").split()
                codes = sorted({c.strip().upper() for c in raw_codes if c.strip()})
                invalid = [c for c in codes if not (len(c) == 2 and c.isalpha())]
                if invalid:
                    sender.send_error_message(
                        f"Invalid country codes (must be 2 letters, e.g. VN, FR, US): {', '.join(invalid)}"
                    )
                    return True
                current = set(self.geo_filter_countries)
                if action == "add":
                    current.update(codes)
                else:
                    current.difference_update(codes)
                self.geo_filter_countries = sorted(current)
                self._save_config()
                sender.send_message(
                    f"{ColorFormat.GREEN}geofilter countries list updated: "
                    f"{ColorFormat.WHITE}{', '.join(self.geo_filter_countries) or '(empty)'}"
                )
                return True

            sender.send_error_message("Usage: /abgeo countries <add|remove|list> [country_codes...]")
            return True

        sender.send_error_message(
            "Usage: /abgeo <on|off|mode|countries|vpn> ...\n"
            f"{ColorFormat.GRAY}e.g. /abgeo on | "
            "/abgeo mode whitelist | "
            "/abgeo countries add VN | "
            "/abgeo vpn on"
        )
        return True

    # ── /abset: numeric/duration keys ───────────────────────────

    def _cmd_setwrongattempts(self, sender: CommandSender, args: list) -> bool:
        if not args or not args[0].isdigit():
            sender.send_error_message("Usage: /abset maxwrongattempts <count>  (e.g. /abset maxwrongattempts 3)")
            return True
        count = int(args[0])
        if count < 1:
            sender.send_error_message("Count must be >= 1.")
            return True
        self.max_wrong_attempts = count
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Max wrong attempts set to: {ColorFormat.WHITE}{count}{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(The {count + 1}. wrong attempt triggers a kick + IP auto-ban. "
            f"Applies only to players joining AFTER this command.)"
        )
        return True

    def _cmd_setreactiontime(self, sender: CommandSender, args: list) -> bool:
        if not args or not args[0].isdigit():
            sender.send_error_message("Usage: /abset minreactiontime <ms>  (e.g. /abset minreactiontime 200)")
            return True
        ms = int(args[0])
        if ms < 0:
            sender.send_error_message("Milliseconds cannot be negative.")
            return True
        self.reaction_time_min_ms = ms
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Form response honeypot threshold set to: {ColorFormat.WHITE}{ms}ms{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(Submitting the form before this mark after it was sent is treated as a bot. "
            f"0 = disables this honeypot entirely.)"
        )
        return True

    def _cmd_settransfertimeout(self, sender: CommandSender, args: list) -> bool:
        if not args or not args[0].isdigit():
            sender.send_error_message("Usage: /abset transfertimeout <seconds>  (e.g. /abset transfertimeout 120)")
            return True
        seconds = int(args[0])
        if seconds < 1:
            sender.send_error_message("Seconds must be >= 1.")
            return True
        self.transfer_choose_timeout = seconds
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Main server selection timeout set to: "
            f"{ColorFormat.WHITE}{seconds} seconds{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(Applies only to server selections made AFTER this command.)"
        )
        return True

    def _cmd_setuimargin(self, sender: CommandSender, args: list) -> bool:
        if not args or not args[0].lstrip("-").isdigit():
            sender.send_error_message("Usage: /abset uitimeoutmargin <seconds>  (e.g. /abset uitimeoutmargin 60)")
            return True
        seconds = int(args[0])
        if seconds < 0:
            sender.send_error_message("Seconds cannot be negative.")
            return True
        if seconds >= self.verify_timeout:
            sender.send_error_message(
                f"The margin ({seconds}s) must be smaller than the current verification timeout "
                f"({self.verify_timeout}s), otherwise the UI would show 0 or a negative time."
            )
            return True
        self.ui_timeout_margin = seconds
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}UI offset set to: {ColorFormat.WHITE}{seconds} seconds{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(The UI will show {self.UI_VERIFY_TIMEOUT}s instead of the real {self.verify_timeout}s. "
            f"Takes effect immediately, even for players currently pending, since it is a dynamically computed display value.)"
        )
        return True

    def _cmd_seteffectbuffer(self, sender: CommandSender, args: list) -> bool:
        if not args or not args[0].lstrip("-").isdigit():
            sender.send_error_message("Usage: /abset pendingeffectbuffer <seconds>  (e.g. /abset pendingeffectbuffer 15)")
            return True
        seconds = int(args[0])
        if seconds < 0:
            sender.send_error_message(
                "Seconds cannot be negative — the effect must last AS LONG AS OR LONGER THAN the real kick "
                "time to act as a fallback safety net."
            )
            return True
        self.pending_effect_buffer = seconds
        self._save_config()
        if self.transfer_enabled and len(self.main_servers) >= 2:
            formula = (
                f"{self.verify_timeout}s (verify_timeout) + "
                f"{self.transfer_choose_timeout}s (transfer_choose_timeout) + {seconds}s"
            )
        else:
            formula = f"{self.verify_timeout}s (verify_timeout) + {seconds}s"
        sender.send_message(
            f"{ColorFormat.GREEN}Effect buffer set to: {ColorFormat.WHITE}{seconds} seconds{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(Blindness/invisibility will last {self.PENDING_EFFECT_DURATION}s "
            f"= {formula}. "
            f"Applies only to players joining AFTER this command — effects for players already pending were "
            f"scheduled with the old buffer.)"
        )
        return True

    def _cmd_setformresends(self, sender: CommandSender, args: list) -> bool:
        if not args or not args[0].isdigit():
            sender.send_error_message("Usage: /abset maxformresends <count>  (e.g. /abset maxformresends 10)")
            return True
        count = int(args[0])
        if count < 1:
            sender.send_error_message("Count must be >= 1.")
            return True
        self.max_form_resends = count
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Max form closes set to: {ColorFormat.WHITE}{count}{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(Closing the form without clicking more than {count} times -> soft kick, NO IP ban. "
            f"Applies only to players joining AFTER this command.)"
        )
        return True

    def _cmd_setisolationy(self, sender: CommandSender, args: list) -> bool:
        if not args or not args[0].lstrip("-").isdigit():
            sender.send_error_message("Usage: /abset isolationy <y>  (e.g. /abset isolationy 2000 force)")
            return True
        y = int(args[0])
        forced = len(args) >= 2 and args[1].lower() == "force"
        # Standard Overworld build limit -64..319 — WARNING only, not a
        # hard refusal (custom worlds/dimensions have different
        # limits); force it with the "force" flag at the admin's own
        # risk.
        if not (-64 <= y <= 319) and not forced:
            sender.send_error_message(
                f"Y={y} is outside the STANDARD build limit of the Overworld (-64..319). "
                f"If your server uses a world/dimension with different limits, run "
                f"/abset isolationy {y} force to set it anyway (overflow/teleport errors at your own risk)."
            )
            return True
        self.pending_isolation_y = y
        self._save_config()
        sender.send_message(
            f"{ColorFormat.GREEN}Isolation Y coordinate set to: {ColorFormat.WHITE}{y}{ColorFormat.GREEN}. "
            f"{ColorFormat.GRAY}(Applies only to players entering the pending state AFTER this command.)"
        )
        return True

    # ── Main server list + management form ──────────────────────

    def _cmd_servers(self, sender: CommandSender) -> bool:
        enabled_label = f"{ColorFormat.GREEN}ON" if self.transfer_enabled else f"{ColorFormat.RED}OFF"
        sender.send_message(
            f"{ColorFormat.YELLOW}Main server transfer: {enabled_label}"
            f"{ColorFormat.YELLOW} (change with /abset transfer)"
        )
        if not self.main_servers:
            sender.send_message(
                f"{ColorFormat.YELLOW}No main servers configured — on or off, players "
                f"who verify will keep playing on this server. "
                f"Use /abset mainservers (or /abmenu) to add some."
            )
            return True
        sender.send_message(f"{ColorFormat.GOLD}=== Main servers ===")
        for i, s in enumerate(self.main_servers, 1):
            sender.send_message(
                f"{ColorFormat.GRAY}  {i}. {ColorFormat.WHITE}{s['name']} "
                f"{ColorFormat.GRAY}({s['host']}:{s['port']})"
            )
        mode = "direct automatic transfer (only 1 server)" if len(self.main_servers) == 1 else "players pick via form"
        sender.send_message(f"{ColorFormat.YELLOW}Selection mode: {ColorFormat.WHITE}{mode}")
        if not self.transfer_enabled:
            sender.send_message(
                f"{ColorFormat.GRAY}(Note: servers are configured but the feature is OFF — "
                f"nobody is being transferred yet.)"
            )
        return True

    def _cmd_setup_servers(self, sender: CommandSender, args: list) -> bool:
        # Forms can only be sent to a real Player (CommandSender may be
        # the console when run from a file/RCON) — isinstance tells
        # them apart because Player inherits from CommandSender.
        # args ignored (the /abset router always passes the remaining
        # arguments; this key doesn't use them).
        if not isinstance(sender, Player):
            sender.send_error_message(
                "/abset mainservers can only be used in game (needs to send a form), not from console."
            )
            return True
        self._send_servers_manage_form(sender)
        return True

    # ── /abmenu: open the combined UI form ──────────────────────

    def _cmd_config(self, sender: CommandSender) -> bool:
        if not isinstance(sender, Player):
            sender.send_error_message(
                "/abmenu can only be used in game (needs to send a form), not from console. "
                "Use /abset <key> [value...] from console instead (e.g. /abset blockexpiry 3)."
            )
            return True
        self._send_config_form(sender)
        return True

    # ── /abset: key table router -> handler ─────────────────────
    # Pure router: each key calls back into the EXISTING _cmd_*
    # handler (no new logic) — /abmenu calls the same handlers, so
    # there is always a single source of truth.

    _ABSET_KEYS = {
        "strict": "_cmd_toggle_strict",
        "easy": "_cmd_toggle_easy",
        "transfer": "_cmd_toggle_transfer",
        "lockdown": "_cmd_toggle_lockdown",
        "maintenance": "_cmd_toggle_maintenance",
        "joinlimit": "_cmd_toggle_joinlimit",
        "blockexpiry": "_cmd_setexpiry",
        "verifytimeout": "_cmd_setverifytime",
        "ratelimit": "_cmd_ratelimit",
        "joinlimitconfig": "_cmd_joinlimit",
        "namepattern": "_cmd_namepattern",
        "maxwrongattempts": "_cmd_setwrongattempts",
        "minreactiontime": "_cmd_setreactiontime",
        "transfertimeout": "_cmd_settransfertimeout",
        "uitimeoutmargin": "_cmd_setuimargin",
        "pendingeffectbuffer": "_cmd_seteffectbuffer",
        "maxformresends": "_cmd_setformresends",
        "isolationy": "_cmd_setisolationy",
        "timezone": "_cmd_settimezone",
        "mainservers": "_cmd_setup_servers",
    }

    def _cmd_abset(self, sender: CommandSender, args: list) -> bool:
        if not args:
            keys = ", ".join(self._ABSET_KEYS.keys())
            sender.send_error_message(f"Usage: /abset <key> [value...]. Valid keys: {keys}")
            return True

        key = args[0].lower()
        method_name = self._ABSET_KEYS.get(key)
        if method_name is None:
            keys = ", ".join(self._ABSET_KEYS.keys())
            sender.send_error_message(f"Invalid key: '{key}'. Valid keys: {keys}")
            return True

        method = getattr(self, method_name)
        return method(sender, args[1:])
