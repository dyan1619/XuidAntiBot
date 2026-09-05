# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer 4: the verification form shown when entering the world (asked
only once per xuid/uuid).

After passing the login layers, a player who has NEVER verified enters
a "guest" (pending) state: blinded + invisible + isolated + chat/
movement/interaction blocked, until they click correctly on the form.
Bot scripts usually cannot click ActionForms themselves -> timeout ->
kick + auto-ban of the IP. 3 form modes (easy_verify / default
3 buttons / strict_verify typed input); easy takes priority if both
are enabled. See xuid_antibot/__init__.py for where this module sits in
the package."""
import json
import time
import random

from endstone import ColorFormat
from endstone.event import event_handler, PlayerJoinEvent
from endstone.form import ActionForm, ModalForm, TextInput

from .helpers import BOT_SUSPECT_KICK_MESSAGE


class VerifyFormsMixin:
    # ── Constants / properties derived from verify_timeout ──────

    DEFAULT_VERIFY_TIMEOUT = 120  # seconds allowed to answer the form before the kick (the REAL time)

    # DELIBERATE offset between the UI and the real kick time: the UI
    # always shows exactly this many seconds LESS (feels more urgent,
    # while the real allowance is more generous). Recomputed from
    # self.verify_timeout, so changing verify_timeout needs no manual
    # UI update. This is intended behavior, not a bug.
    DEFAULT_UI_TIMEOUT_MARGIN = 60  # seconds — UI shows = verify_timeout - 60s

    @property
    def UI_VERIFY_TIMEOUT(self) -> int:  # seconds shown on the UI, always less than the real verify_timeout
        return max(self.verify_timeout - self.ui_timeout_margin, 0)

    # DELIBERATE buffer added on top of the total wait time to get the
    # /effect duration (blindness/invisibility) — the effect always
    # lasts LONGER than the real kick time, acting as a safety net in
    # case the explicit effect-removal command fails to run (client
    # disconnects, exception midway...).
    DEFAULT_PENDING_EFFECT_BUFFER = 15  # seconds — effect = verify_timeout + 15s

    @property
    def PENDING_EFFECT_DURATION(self) -> int:  # seconds = real total wait + buffer
        # With >=2 servers and transfer enabled there is still the
        # server-selection wait (transfer_choose_timeout) after
        # verifying — the effect must cover BOTH phases, otherwise it
        # expires mid-selection and lifts the blindness/invisibility
        # too early, before the player is in the real world.
        total_wait = self.verify_timeout
        if self.transfer_enabled and len(self.main_servers) >= 2:
            total_wait += self.transfer_choose_timeout
        return total_wait + self.pending_effect_buffer

    # ── Pending state management ────────────────────────────────

    def _enter_pending(self, player, key: str) -> None:
        """The SINGLE point that starts the pending state
        (blindness/invisibility/isolation + pending_ids). Shared by
        both unverified players and verified players who still have to
        pick a server — neither is in the real world yet, so both get
        locked down identically."""
        self.pending_ids.add(key)

        # Record the IP as pending (see login_guard.py step 0d — blocks
        # a second connection from the same IP while one player on that
        # IP has not finished verifying). Do not overwrite if the IP
        # already has a DIFFERENT key pending (a rare case, self-heals
        # once _clear_pending removes the rightful owner).
        ip = self._get_ip(player)
        if ip and ip not in self._pending_ip_key:
            self._pending_ip_key[ip] = key

        # Isolate BEFORE pinning the coordinates — if the position were
        # pinned first, on_player_move would cancel the very teleport
        # we are about to make.
        self._apply_pending_isolation(player)
        try:
            loc = player.location  # position AFTER the teleport (Y = pending_isolation_y)
            self._pending_spawn_loc[key] = (loc.x, loc.y, loc.z)
        except Exception:
            pass

        # Blindness so they can't see the real world while waiting
        # (limits abusing the pending window to scout other people's
        # bases); invisibility so mobs/other players hardly notice
        # them.
        self._apply_pending_blindness(player)
        self._apply_pending_invisibility(player)

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        player = event.player
        key = self._player_key(player)

        if key in self.verified_ids:
            # Already verified -> do NOT ask the form again, but if a
            # server still has to be picked they are NOT in the real
            # world yet -> still fully pending, just skipping the form
            # step. Without this it would be a bug: a pre-verified
            # player rejoining would stand at the server picker with
            # NO lockdown at all.
            if self.transfer_enabled and self.main_servers:
                self._enter_pending(player, key)
                self._route_to_main_server(player)
            return

        self._enter_pending(player, key)

        player.send_message(
            f"{ColorFormat.YELLOW}Please verify to enter the server "
            f"(you cannot move or chat until you verify)."
        )
        self._send_verify_form(player, key)

        # Timeout: verify_timeout seconds without verifying -> kick +
        # auto-ban of the IP (no form response within that time is
        # almost certainly not a real human at the controls).
        self.server.scheduler.run_task(
            self, lambda: self._check_verify_timeout(player, key),
            delay=self.verify_timeout * 20,  # ticks (20 ticks = 1 second)
        )

    # ── Honeypot / code constants ───────────────────────────────

    # Drop characters that are easy to misread on screen: 0/O, 1/I/L,
    # 5/S, 8/B, 2/Z. (An old revision accidentally listed '6' twice,
    # doubling its probability — one copy was removed; generated codes
    # look the same, only the distribution is now even.)
    SAFE_CODE_CHARS = "ACDEFGHJKMNPQRTUVWXY34679"

    DEFAULT_MAX_WRONG_ATTEMPTS = 3  # 3 wrong attempts max; the 4th kicks + auto-bans the IP

    # Closing the form without clicking more than n times -> soft kick
    # (no ban — just a bug/lag).
    DEFAULT_MAX_FORM_RESENDS = 10

    # Response-time honeypot: submitting BEFORE this mark (counted from
    # the form send) is treated as a bot, blocked regardless of right
    # or wrong. 200ms is below the fastest human reflex (~120-150ms
    # simple reflex; "choice reaction" is slower still) -> almost never
    # blocks a real player, while scripted bots (tens of ms) are always
    # caught. Applies to all 3 form modes.
    DEFAULT_REACTION_TIME_MIN_MS = 200

    # ── Sending the form (mode selection) ───────────────────────

    def _send_verify_form(self, player, key: str) -> None:
        # Record the send time EVERY time (including resends after a
        # wrong click) — used by the response-time honeypot.
        self._form_sent_at[key] = time.time()
        if self.easy_verify:
            self._send_verify_form_easy(player, key)
        elif self.strict_verify:
            self._send_verify_form_strict(player, key)
        else:
            self._send_verify_form_buttons(player, key)

    # Easy mode: only stops bots that CANNOT CLICK BUTTONS — a bot
    # that can click passes with the single button 100% of the time;
    # those are handled by the layers BEFORE the form. Use when a fast
    # experience matters most.

    def _send_verify_form_easy(self, player, key: str) -> None:
        form = ActionForm(
            title="Anti-Bot Verification",
            content=(
                "This server has anti-bot verification enabled.\n"
                "Click the button below to confirm you are not an automated bot "
                f"and enter the server, within {self._verify_timeout_label()}."
            ),
        )

        def on_click(p):
            self._on_verify_success(p, key)
        form.add_button("Confirm & enter server", on_click=on_click)

        def on_close(p):
            self._on_verify_form_closed(p, key)
        form.on_close = on_close

        player.send_form(form)

    # Default mode: 3 buttons (1 right, 2 wrong), shuffled on every
    # send — a "click a fixed button" bot is useless; it must read the
    # code in the content and find the matching button. There is still
    # a 1/3 chance of a lucky guess; use strict mode (/abset strict)
    # to eliminate it.

    def _send_verify_form_buttons(self, player, key: str) -> None:
        correct_code = self._gen_code()
        wrong_codes = set()
        while len(wrong_codes) < 2:
            c = self._gen_code()
            if c != correct_code:
                wrong_codes.add(c)

        buttons = [correct_code, *wrong_codes]
        random.shuffle(buttons)

        form = ActionForm(
            title="Anti-Bot Verification",
            content=(
                f"This server has anti-bot verification enabled.\n"
                f"To confirm you are not an automated bot, click the button with the code: "
                f"§l{correct_code}§r within {self._verify_timeout_label()}"
            ),
        )

        def make_on_click(code: str, is_correct: bool):
            def on_click(p):
                if is_correct:
                    self._on_verify_success(p, key)
                    return
                self._on_verify_wrong(p, key)
            return on_click

        for code in buttons:
            form.add_button(code, on_click=make_on_click(code, code == correct_code))

        def on_close(p):
            self._on_verify_form_closed(p, key)

        form.on_close = on_close
        player.send_form(form)

    # Strict mode: NO button to blindly click — bot scripts can only
    # send pre-canned selection packets (button id/option), they cannot
    # read the form content and type a string back into a TextInput,
    # so they almost certainly cannot pass. A typo still counts as a
    # normal wrong attempt (shares MAX_WRONG_ATTEMPTS with button
    # mode).
    #
    # API NOTE: ModalForm on_submit returns a JSON STRING (an array
    # ordered by the controls added via add_control), not a dict keyed
    # by name — json.loads() it and read by the TextInput's INDEX; do
    # not use str.find()/in on the raw JSON string (error-prone
    # matches).
    TEXT_INPUT_INDEX = 0  # the TextInput is the ONLY control in the form -> always position 0

    def _send_verify_form_strict(self, player, key: str) -> None:
        correct_code = self._gen_code()
        # A new code is generated on every form send (wrong/close), so
        # the stored code for this key must be updated on every call.
        self._strict_codes[key] = correct_code

        form = ModalForm(
            title="Anti-Bot Verification (strict mode)",
            controls=[
                TextInput(
                    label=(
                        f"This server has anti-bot verification enabled (strict mode).\n"
                        f"Type the following code EXACTLY into the box below: §l{correct_code}§r\n"
                        f"(you have {self._verify_timeout_label()} to complete it; the code is case-sensitive)"
                    ),
                    placeholder="Type the code here...",
                ),
            ],
            submit_button="Confirm",
        )

        def on_submit(p, json_response: str) -> None:
            try:
                values = json.loads(json_response)
                typed = str(values[self.TEXT_INPUT_INDEX]).strip()
            except Exception as e:
                self.logger.error(f"[XuidAntiBot] Error reading strict-mode form result for {p.name}: {e}")
                typed = ""

            expected = self._strict_codes.get(key)
            if expected is not None and typed == expected:
                self._strict_codes.pop(key, None)
                self._form_sent_at.pop(key, None)
                self._on_verify_success(p, key)
                return
            self._on_verify_wrong(p, key)

        def on_close(p):
            self._on_verify_form_closed(p, key)

        form.on_submit = on_submit
        form.on_close = on_close
        player.send_form(form)

    @staticmethod
    def _gen_code() -> str:
        return "".join(random.choices(VerifyFormsMixin.SAFE_CODE_CHARS, k=4))

    def _verify_timeout_label(self) -> str:
        # Deliberately uses UI_VERIFY_TIMEOUT (exactly
        # ui_timeout_margin seconds less than the real kick) —
        # intended behavior, not a bug. "X minutes", "X minutes Y
        # seconds", or "X seconds" when under a minute.
        total = self.UI_VERIFY_TIMEOUT
        minutes, seconds = divmod(total, 60)
        minute_word = "minute" if minutes == 1 else "minutes"
        if minutes and seconds:
            sec_word = "second" if seconds == 1 else "seconds"
            return f"{minutes} {minute_word} {seconds} {sec_word}"
        if minutes:
            return f"{minutes} {minute_word}"
        return f"{seconds} {'second' if seconds == 1 else 'seconds'}"

    # ── Result handling (shared by all 3 modes) ──────────────────

    def _discard_verify_state(self, key: str) -> None:
        """Cleans the 4 verification-form state dicts by player_key
        (wrong attempts, close count, strict code, form send time).
        Shared by every point that ends the verification wait +
        on_player_quit — when adding new form state later, add one
        line here instead of editing 6 places like before."""
        self._wrong_attempts.pop(key, None)
        self._close_count.pop(key, None)
        self._strict_codes.pop(key, None)
        self._form_sent_at.pop(key, None)

    def _reacted_too_fast(self, key: str) -> bool:
        """Response-time honeypot — True if the submission arrived
        faster than reaction_time_min_ms after the form was sent.
        Right or wrong doesn't matter: an instant reply is by itself
        an anomaly."""
        sent_at = self._form_sent_at.get(key)
        if sent_at is None:
            return False  # no timestamp to compare against -> don't block blindly
        elapsed_ms = (time.time() - sent_at) * 1000
        return elapsed_ms < self.reaction_time_min_ms

    def _block_honeypot_too_fast(self, p, key: str) -> None:
        ip = self._get_ip(p)
        reason = f"Form response under {self.reaction_time_min_ms}ms (name: {p.name})"
        if ip:
            self._block_ip(ip, reason)
        self._discard_verify_state(key)
        self._clear_pending(p, key)
        self.logger.info(
            f"{ColorFormat.YELLOW}[XuidAntiBot] Blocked by response-time honeypot: {p.name} (ip={ip})"
        )
        try:
            # Generic message — does not reveal the detection mechanism
            # (see the note for step 1 in login_guard.py).
            p.kick(BOT_SUSPECT_KICK_MESSAGE)
        except Exception:
            pass

    def _on_verify_success(self, p, key: str) -> None:
        if self._reacted_too_fast(key):
            self._block_honeypot_too_fast(p, key)
            return

        # Do NOT remove pending_ids here — pending may only be cleared
        # when the player actually enters the real world (no transfer
        # -> below; with transfer -> inside _do_transfer, on both
        # success and failure).
        self._discard_verify_state(key)
        self.verified_ids.add(key)
        self._save_verified()
        ip = self._get_ip(p)
        self.logger.info(
            f"{ColorFormat.GREEN}[XuidAntiBot] Verification successful: {p.name} (ip={ip}, {key})"
        )

        if self.transfer_enabled and self.main_servers:
            self._route_to_main_server(p)
        else:
            self._clear_pending(p, key)
            p.send_message(f"{ColorFormat.GREEN}Verification successful! Enjoy your stay.")

    def _on_verify_wrong(self, p, key: str) -> None:
        if self._reacted_too_fast(key):
            self._block_honeypot_too_fast(p, key)
            return

        # Allow up to max_wrong_attempts mistakes (real players
        # misclick/misread/mistype), ban beyond that.
        self._wrong_attempts[key] += 1
        attempts = self._wrong_attempts[key]

        if attempts <= self.max_wrong_attempts:
            remaining = self.max_wrong_attempts - attempts
            if remaining > 0:
                p.send_message(
                    f"{ColorFormat.RED}Wrong code ({attempts}/{self.max_wrong_attempts} attempts) "
                    f"— {ColorFormat.YELLOW}{remaining} wrong attempt(s) left before you are kicked"
                    f"{ColorFormat.RED}. Try again with the new code."
                )
            else:
                # FINAL warning — one more wrong attempt means a kick.
                p.send_message(
                    f"{ColorFormat.RED}Wrong code ({attempts}/{self.max_wrong_attempts} attempts) "
                    f"— {ColorFormat.GOLD}§lWARNING: one more wrong attempt and you will be KICKED!"
                )
            if key in self.pending_ids:
                self._send_verify_form(p, key)
            return

        ip = self._get_ip(p)
        reason = f"Wrong verification code more than {self.max_wrong_attempts} times (name: {p.name})"
        if ip:
            self._block_ip(ip, reason)
        self._discard_verify_state(key)
        self._clear_pending(p, key)
        try:
            p.kick(BOT_SUSPECT_KICK_MESSAGE)
        except Exception:
            pass

    def _on_verify_form_closed(self, p, key: str) -> None:
        # Form closed without clicking -> reopen it automatically (new
        # code). The count is capped to avoid an infinite loop (this
        # once caused an OOM); it MUST be delayed a few ticks through
        # the scheduler (never call directly/synchronously — spamming
        # the X button floods synchronous callbacks that once crashed
        # the server); wrapped in try/except. Closing the UI is NOT
        # bot behavior -> only a soft kick past the cap, no IP ban.
        try:
            if key not in self.pending_ids:
                return
            count = self._close_count.get(key, 0) + 1
            self._close_count[key] = count
            if count > self.max_form_resends:
                self._discard_verify_state(key)
                self._clear_pending(p, key)
                try:
                    p.kick("You closed the verification form too many times. Please rejoin.")
                except Exception:
                    pass
                return
            self.server.scheduler.run_task(
                self,
                lambda: self._resend_form_safe(p, key),
                delay=5,
            )
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Error in on_close: {e}")

    def _resend_form_safe(self, player, key: str) -> None:
        try:
            if key in self.pending_ids:
                self._send_verify_form(player, key)
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Error resending the form: {e}")

    def _check_verify_timeout(self, player, key: str) -> None:
        if key not in self.pending_ids:
            return  # already verified or already left the server
        ip = self._get_ip(player)
        reason = f"No form verification within {self.verify_timeout}s (name: {player.name})"
        if ip:
            self._block_ip(ip, reason)
        self._discard_verify_state(key)
        self._clear_pending(player, key)
        try:
            player.kick("You did not verify in time. Please rejoin.")
        except Exception:
            pass
