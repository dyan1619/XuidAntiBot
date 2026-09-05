# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the whole plugin: IP lookup, bot-name matching,
rate limit, join limit, IP blocking, blindness/invisibility effects,
coordinate isolation, player_key.

See xuid_antibot/__init__.py for where this module sits in the package."""
import time

from typing import Optional

from endstone import ColorFormat
from endstone.level import Location

# Kick message shared by every auto-block point (rate limit, bot name,
# empty xuid, wrong code, honeypot...). Deliberately GENERIC — never
# reveals the detection mechanism on the kick screen (even the word
# "rate limit" is enough for an attacker to figure out how to evade;
# see the note for step 1 in login_guard.py).
BOT_SUSPECT_KICK_MESSAGE = (
    "You have been blocked due to suspected bot activity.\n"
    "If you believe this is a mistake, please contact the server admin to be unblocked."
)


class HelpersMixin:
    # ── Identification ──────────────────────────────────────────

    @staticmethod
    def _get_ip(player) -> Optional[str]:
        # Same way edban gets the IP, so both plugins see consistent data.
        try:
            addr = player.address
            if hasattr(addr, "hostname"):
                return addr.hostname
            s = str(addr)
            return s.split(":")[0] if ":" in s else s
        except Exception:
            return None

    def _format_display_time(self, ts: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
        # Format epoch ts according to self.display_timezone_offset
        # (hours from UTC, fractional allowed) instead of
        # time.localtime() — independent of the host system clock.
        # Shared by everything that shows dates/times.
        offset = getattr(self, "display_timezone_offset", 0) or 0
        shifted = time.gmtime(ts + offset * 3600)
        return time.strftime(fmt, shifted)

    def _matches_bot_name(self, name: str) -> bool:
        # Fast path: with no patterns at all (factory default) skip the
        # loop entirely — this runs on EVERY login attempt.
        if not self._name_re:
            return False
        return any(p.match(name) for p in self._name_re)

    # ── Rate limit (per IP) / join limit (whole server) ──────────

    def _check_rate_limit(self, ip: str) -> bool:
        """True if this IP JUST crossed the threshold on this call (so
        auto-block it right away). The deque only keeps timestamps
        within the most recent rate_limit_window."""
        now = time.time()
        dq = self._login_times[ip]
        dq.append(now)
        while dq and now - dq[0] > self.rate_limit_window:
            dq.popleft()
        return len(dq) >= self.rate_limit_count

    def _check_join_limit(self) -> bool:
        """True if the total number of UNVERIFIED joins across the whole
        server within the most recent join_limit_window (all IPs
        combined) exceeds join_limit_count (so kick as overloaded).

        IMPORTANT: only call this for joins that actually COUNT (after
        deciding NOT to kick) — kicked attempts must not be appended to
        _join_times, otherwise the window never drains during a join
        flood and real players get stuck indefinitely instead of for
        exactly join_limit_window seconds."""
        now = time.time()
        dq = self._join_times
        while dq and now - dq[0] > self.join_limit_window:
            dq.popleft()
        if len(dq) >= self.join_limit_count:
            return True
        dq.append(now)
        return False

    # ── IP blocking ─────────────────────────────────────────────

    def _is_blocked(self, ip: str) -> bool:
        """Look up blocked_ips, lazily removing expired entries (returns
        False after cleanup — that IP is then processed from scratch
        through the filter layers like a new IP)."""
        entry = self.blocked_ips.get(ip)
        if entry is None:
            return False
        expires_at = entry.get("expires_at")
        if expires_at is not None and time.time() >= expires_at:
            del self.blocked_ips[ip]
            self._save_blocked()
            self.logger.info(
                f"{ColorFormat.GRAY}[XuidAntiBot] Block for IP {ip} expired, removed from the blacklist."
            )
            return False
        return True

    def _block_ip(self, ip: str, reason: str) -> None:
        """Add/extend an IP block; shared by every auto-block point.

        - Not blocked yet (or expired): create a new record with TTL =
          the current self.block_ttl (0 = permanent), banned_at = the
          FIRST time it was blocked (unchanged on extension).
        - Currently blocked WITH an expiry, and offending again:
          extend expires_at = now + current TTL ("expiry gets renewed
          if the attack continues").
        - Currently blocked PERMANENTLY: keep it permanent (never
          downgraded to a timed block when the TTL changes), only
          update the reason.
        """
        now = time.time()
        expires_at = (now + self.block_ttl) if self.block_ttl > 0 else None
        existing = self.blocked_ips.get(ip)

        if existing is not None and existing.get("expires_at") is None:
            existing["reason"] = reason
            self._save_blocked()
            return

        if existing is not None and existing.get("expires_at") is not None:
            existing["expires_at"] = expires_at
            existing["reason"] = reason
            self._save_blocked()
            self.logger.info(
                f"{ColorFormat.RED}[XuidAntiBot] EXTENDED block for IP {ip} — {reason}"
            )
            return

        self.blocked_ips[ip] = {"reason": reason, "expires_at": expires_at, "banned_at": now}
        self._save_blocked()
        self.logger.info(f"{ColorFormat.RED}[XuidAntiBot] AUTO-BLOCK IP {ip} — {reason}")

    # ── Effects for pending players (blindness + invisibility) ──
    #
    # ENDSTONE 0.11.x NOTE: there is NO Mob.add_effect / remove_effect
    # / Effect class — vanilla /effect must be dispatched via console
    # (server.dispatch_command with server.command_sender, independent
    # of the player's OP permissions). Duration is in SECONDS per the
    # /effect command syntax; PENDING_EFFECT_DURATION acts as a safety
    # net so the effect expires on time even if the removal command
    # fails to run.

    def _run_effect_command(self, player, effect_name: str, seconds: int, amplifier: int = 0) -> None:
        try:
            self.server.dispatch_command(
                self.server.command_sender,
                f'effect "{player.name}" {effect_name} {seconds} {amplifier} true',
            )
        except Exception as e:
            self.logger.error(
                f"[XuidAntiBot] Error running effect command ({effect_name}) for {player.name}: {e}"
            )

    def _clear_effect_command(self, player) -> None:
        # "clear" removes ALL effects instead of just the plugin's two —
        # also cleans up splash/lingering potions others may have hit
        # the player with while pending. Trade-off: rare legitimate
        # buffs acquired before joining are also removed — accepted to
        # prioritize safety while blind + locked in place.
        try:
            self.server.dispatch_command(
                self.server.command_sender,
                f'effect "{player.name}" clear',
            )
        except Exception as e:
            self.logger.error(
                f"[XuidAntiBot] Error clearing all effects for {player.name}: {e}"
            )

    def _clear_pending_effects(self, player) -> None:
        self._clear_effect_command(player)

    def _apply_pending_blindness(self, player) -> None:
        self._run_effect_command(player, "blindness", self.PENDING_EFFECT_DURATION)

    def _apply_pending_invisibility(self, player) -> None:
        # The trailing "true" = hideParticles, so particles don't leak
        # the player's position.
        self._run_effect_command(player, "invisibility", self.PENDING_EFFECT_DURATION)

    # ── Y-coordinate isolation ──────────────────────────────────

    # Y=2000 is deliberately OUTSIDE the standard Overworld build limit
    # (-64..319): nobody can place/break blocks there, so nobody can
    # interfere with the isolation zone, while still staying within the
    # properly-lit region (block Y is stored in 12 bits = ±2048, minus
    # padding leaves ±2032). Entities are NOT constrained by the build
    # limit and exist normally at Y=2000. Admins set a different value
    # at their own risk (custom worlds/dimensions may have different
    # limits; the /abset isolationy command has a "force" flag).
    DEFAULT_PENDING_ISOLATION_Y = 2000

    def _apply_pending_isolation(self, player) -> None:
        key = self._player_key(player)
        try:
            loc = player.location
            # Save the ORIGINAL position BEFORE teleporting so the
            # player can be returned to the exact spot after verifying.
            self._pending_original_loc[key] = Location(
                loc.dimension, loc.x, loc.y, loc.z, loc.pitch, loc.yaw
            )
            isolation_loc = Location(
                loc.dimension, loc.x, self.pending_isolation_y, loc.z,
                loc.pitch, loc.yaw,
            )
            player.teleport(isolation_loc)
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Isolation teleport error for {player.name}: {e}")

    def _clear_pending_isolation(self, player) -> None:
        key = self._player_key(player)
        original = self._pending_original_loc.pop(key, None)
        if original is None:
            return
        try:
            player.teleport(original)
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Error teleporting {player.name} back to the original position: {e}")

    def _clear_pending(self, player, key: str) -> None:
        """The SINGLE point that clears the pending state (whether the
        player made it into the real world or was kicked/blocked).
        pending_ids is the ONLY flag for "not in the real world yet" —
        every pending cleanup MUST go through here, never discard/pop
        on its own, so effects + isolation + pending_ids always stay
        in sync."""
        self.pending_ids.discard(key)
        self._pending_spawn_loc.pop(key, None)
        self._clear_pending_effects(player)
        self._clear_pending_isolation(player)

        # Remove the IP from _pending_ip_key ONLY when the current key is
        # actually the one holding that IP — avoids clearing key B's
        # pending state if the IP was already reclaimed by key B (a
        # different connection, same IP) after key A got cleared.
        ip = self._get_ip(player)
        if ip and self._pending_ip_key.get(ip) == key:
            del self._pending_ip_key[ip]

    @staticmethod
    def _player_key(player) -> str:
        """Identity for remembering verified-or-not: prefer xuid
        (stable, tied to a real Xbox Live account); uuid is only a
        fallback.

        NOTE: the "uuid:..." branch is almost never used to bypass —
        players with xuid=None are already rejected at login_guard
        step 2c BEFORE reaching the verification form, so a uuid key
        can never end up in verified_ids. That is intentional
        (blocking empty-xuid bots first); if step 2c is ever relaxed,
        the uuid branch will actually take effect."""
        xuid = getattr(player, "xuid", None)
        if xuid:
            return f"xuid:{xuid}"
        return f"uuid:{player.unique_id}"

    def _is_pending(self, player) -> bool:
        """True if the player is pending (awaiting verification /
        awaiting server selection).

        Fast path for hot events (move/jump/item, fired every tick for
        ALL players): when pending_ids is empty — the normal state when
        nobody is waiting — return False immediately, skipping the
        getattr + player_key string build for every single event."""
        if not self.pending_ids:
            return False
        return self._player_key(player) in self.pending_ids
