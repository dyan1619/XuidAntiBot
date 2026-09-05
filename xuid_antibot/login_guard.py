# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""on_player_login — rejects connections AT login, BEFORE the player
entity is created in the world (no wasted resources, nothing left for
edban to clean up afterwards).

Layer order (by step number; when an earlier layer returns early the
later ones don't run): maintenance -> geo/AntiVPN -> verified bypass
-> lockdown -> join limit -> pending IP conflict -> blocked IP ->
bot-name pattern + "_" -> empty xuid -> per-IP rate limit. See
xuid_antibot/__init__.py for where this module sits in the package."""
from endstone import ColorFormat
from endstone.event import event_handler, PlayerLoginEvent

from .helpers import BOT_SUSPECT_KICK_MESSAGE


class LoginGuardMixin:
    # ── Main event: the login filter layers ─────────────────────

    @event_handler
    def on_player_login(self, event: PlayerLoginEvent) -> None:
        player = event.player
        name = player.name
        ip = self._get_ip(player)

        # -2) MAINTENANCE — hard lock: only OPs may join, blocks
        #     EVERYONE including verified players. Runs BEFORE geo
        #     (step -1) so admins can always get in regardless of how
        #     geo/AntiVPN is configured. Does NOT auto-block the IP
        #     (an admin's deliberate lock, not evidence of a bot).
        #     player.is_op is a property on Player.
        if self.maintenance_enabled and not player.is_op:
            event.cancel()
            self._deny(
                event,
                "The server is under maintenance.\nPlease try again later.",
            )
            self.logger.info(
                f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by maintenance (not an OP): {name} (ip={ip})"
            )
            return

        # -1) GEO + ANTIVPN — deliberately placed BEFORE the verified
        #     bypass (step 0): if the admin changes the country
        #     whitelist / enables AntiVPN, then verified players who
        #     are outside the allowed region / using a VPN are blocked
        #     too, exactly as intended, no exceptions. The VPN kick
        #     message is deliberately SEPARATE from the geo one
        #     (admin asked for it to be explicit). See geo_filter.py:
        #     24h cache, fail-open on API errors.
        if ip and not self._geo_is_allowed(ip):
            event.cancel()
            info = self.geo_cache.get(ip)
            info = info[0] if info else None
            is_vpn = bool(info and self.geo_filter_block_vpn and (info.get("proxy") or info.get("hosting")))
            if is_vpn:
                self._deny(
                    event,
                    "Connection refused: VPN/Proxy/Hosting detected.",
                )
                self.logger.info(
                    f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by AntiVPN (proxy/hosting detected via ip-api.com): "
                    f"{name} (ip={ip})"
                )
            else:
                self._deny(
                    event,
                    "Connection refused due to regional restrictions.",
                )
                self.logger.info(
                    f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by geo_filter ({self.geo_filter_mode}="
                    f"{self.geo_filter_countries}): {name} (ip={ip})"
                )
            return

        # 0) ALREADY VERIFIED (by xuid/uuid) -> bypass the whole IP
        #    blacklist and the rate limit below. Home ISPs often use
        #    DYNAMIC IPs that rotate on modem reconnects — without the
        #    bypass, a previously auto-blocked IP can easily end up
        #    assigned to a verified player, locking them out through
        #    no fault of their own.
        key = self._player_key(player)
        if key in self.verified_ids:
            return  # bypass the whole IP blacklist/rate limit, let them straight in

        # 0b) LOCKDOWN — reaching this point means definitely NOT
        #     verified (the verified branch already returned above).
        #     All new connections are blocked until the admin disables
        #     it; no auto-expiry, no auto-block of the IP (a proactive
        #     temporary lock, not suspicious behavior).
        if self.lockdown_enabled:
            event.cancel()
            self._deny(
                event,
                "The server is currently in lockdown.\n"
                "Please try again later or contact an admin.",
            )
            self.logger.info(
                f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by lockdown (not verified): {name} (ip={ip})"
            )
            return

        # 0c) JOIN LIMIT — applies only to UNVERIFIED players. Limits
        #     total SERVER LOAD (all IPs combined within the window in
        #     seconds), unlike the rate limit at step 3 (per-IP
        #     counting). Above the threshold -> kick as overloaded, NO
        #     auto-block of the IP. Placed before the bot-detection
        #     layers to stop floods early without wasting further
        #     work.
        if self.join_limit_enabled:
            if self._check_join_limit():
                event.cancel()
                self._deny(
                    event,
                    "The server is currently overloaded, please try again in a few minutes.",
                )
                self.logger.info(
                    f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by join_limit "
                    f"({self.join_limit_count} joins/{self.join_limit_window}s): {name} (ip={ip})"
                )
                return

        # 0d) THIS IP ALREADY HAS ONE PENDING PLAYER (not yet finished
        #     verifying) -> reject a SECOND connection from the same IP,
        #     don't let them into the world. Deliberately only BLOCKS,
        #     does NOT auto-block the IP right away (unlike steps
        #     1/2/2b/2c) — a shared-NAT ISP/net cafe can have 2 real
        #     people joining close together on the same IP; a hard block
        #     would be too easy to get wrong. Instead this still counts
        #     as one login attempt for the rate limit at step 3: if it's
        #     a bot opening many connections back-to-back on the same
        #     IP, the rate limit will catch and auto-block it as usual;
        #     1-2 real people colliding by chance are just rejected this
        #     one time, not remembered as an offense.
        if ip and ip in self._pending_ip_key and self._pending_ip_key[ip] != key:
            event.cancel()
            exceeded = self._check_rate_limit(ip)
            if exceeded:
                reason = (
                    f"Login spam ({self.rate_limit_count} logins/{self.rate_limit_window}s) "
                    f"— same IP already has a pending session — latest name: {name}"
                )
                self._block_ip(ip, reason)
            self._deny(
                event,
                "This IP already has an unfinished verification session.\n"
                "Please wait for it to complete (or expire) and try again.",
            )
            self.logger.info(
                f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by pending-IP conflict: {name} (ip={ip})"
            )
            return

        # 1) PREVIOUSLY BLOCKED IP -> reject immediately. _is_blocked
        #    lazily removes expired entries (TTL set via /abset
        #    blockexpiry) — the connection then falls through to steps
        #    2/2b/2c/3 like a new IP. An IP that keeps trying to log
        #    in while blocked (almost certainly a repeat attacker) ->
        #    _block_ip automatically EXTENDS a timed record.
        if ip and self._is_blocked(ip):
            event.cancel()
            # Do NOT show the specific reason/IP/detection mechanism on
            # the kick screen — even "rate limit" is enough for an
            # attacker to figure out how to evade. Keep it as generic
            # as possible.
            self._block_ip(ip, self.blocked_ips[ip]["reason"])
            self._deny(event, BOT_SUSPECT_KICK_MESSAGE)
            return

        # 2/2b) NAME MATCHES A BOT PATTERN or contains "_" -> reject
        #       immediately AND auto-block the IP (no waiting for the
        #       rate-limit threshold) — the name alone is suspicious
        #       enough, no further evidence needed.
        name_is_bot = self._matches_bot_name(name)

        # 2b) "_" is always a bot (Xbox Live Gamertags don't allow "_"
        #     in valid names) — hard block, no opt-out. Uses the `in`
        #     operator instead of a regex: same result, cheaper, runs
        #     on every login.
        name_is_underscore_bot = "_" in name

        if name_is_bot or name_is_underscore_bot:
            reason_label = "bot name pattern match" if name_is_bot else "name contains '_' (not allowed by Xbox Live)"
            if ip:
                self._block_ip(ip, f"{reason_label} ({name})")
            event.cancel()
            self._deny(event, BOT_SUSPECT_KICK_MESSAGE)
            self.logger.info(f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by {reason_label}: {name} (ip={ip})")
            return

        # 2c) EMPTY XUID — a separate check from the name check: a
        #     player not authenticated through a real Xbox Live account
        #     is almost always a bot/fake connection, catching names
        #     that the two layers above miss.
        xuid = getattr(player, "xuid", None)
        if not xuid:
            reason = f"xuid=None (not signed in to Xbox Live) ({name})"
            if ip:
                self._block_ip(ip, reason)
            event.cancel()
            self._deny(event, "You are not signed in to Xbox Live. Please sign in and try again.")
            self.logger.info(f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by xuid=None: {name} (ip={ip})")
            return

        # 3) PER-IP RATE LIMIT — the last safety net for names that
        #    match no pattern (in case the attacker switches name
        #    styles); still catches a single IP hammering logins in a
        #    short time.
        if ip:
            exceeded = self._check_rate_limit(ip)
            if exceeded:
                reason = f"Login spam ({self.rate_limit_count} logins/{self.rate_limit_window}s) — latest name: {name}"
                self._block_ip(ip, reason)
                event.cancel()
                # Uses the one shared generic message like every other
                # auto-block — does not reveal this was rate limiting
                # (see the note at step 1).
                self._deny(event, BOT_SUSPECT_KICK_MESSAGE)
                return

    # ── Helper ──────────────────────────────────────────────────

    @staticmethod
    def _deny(event: PlayerLoginEvent, message: str) -> None:
        try:
            event.kick_message = message
        except Exception:
            pass
