# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transfers players to the main server after they verify.

1 server -> direct transfer; >=2 servers -> a selection form; closing
it forces it back open; no choice within transfer_choose_timeout
seconds -> kick. See xuid_antibot/__init__.py for where this module sits
in the package."""
import time

from endstone import ColorFormat
from endstone.form import ActionForm


class TransferMixin:
    # Time allowed to pick a main server (seconds), counted from the
    # first time the form is shown.
    DEFAULT_TRANSFER_CHOOSE_TIMEOUT = 120  # seconds

    # ── Routing / server-selection form ─────────────────────────

    def _route_to_main_server(self, p) -> None:
        if len(self.main_servers) == 1:
            target = self.main_servers[0]
            self._do_transfer(p, target)
            return

        key = self._player_key(p)
        if key not in self._pending_transfer_deadline:
            self._pending_transfer_deadline[key] = time.time() + self.transfer_choose_timeout
            self.server.scheduler.run_task(
                self, lambda: self._check_transfer_timeout(p, key),
                delay=self.transfer_choose_timeout * 20,  # ticks (20 ticks = 1 second)
            )

        self._send_transfer_form(p, key)

    def _send_transfer_form(self, p, key: str) -> None:
        form = ActionForm(
            title="Select Server",
            content="Verification successful! Choose the server you want to join:",
        )

        def make_on_click(target: dict):
            def on_click(pl):
                self._pending_transfer_deadline.pop(key, None)
                self._close_count.pop(key, None)
                self._do_transfer(pl, target)
            return on_click

        for target in self.main_servers:
            form.add_button(target["name"], on_click=make_on_click(target))

        def on_close(pl):
            # Closed without choosing -> force it back open, BUT:
            #  1) do NOT resend the form directly/synchronously —
            #     spamming the X button floods unbounded synchronous
            #     callbacks that once crashed/OOM'd the server (really
            #     happened with the verify form); must go through the
            #     scheduler with a delay.
            #  2) cap the close count via max_form_resends (sharing
            #     _close_count) — past the cap, a soft kick.
            try:
                if key not in self._pending_transfer_deadline:
                    return  # already chose earlier, or the player left the server
                if time.time() >= self._pending_transfer_deadline[key]:
                    return  # let _check_transfer_timeout handle the kick, avoid a race
                count = self._close_count.get(key, 0) + 1
                self._close_count[key] = count
                if count > self.max_form_resends:
                    self._pending_transfer_deadline.pop(key, None)
                    self._close_count.pop(key, None)
                    self._clear_pending(pl, key)
                    try:
                        pl.kick("You closed the server selection form too many times. Please rejoin.")
                    except Exception:
                        pass
                    return
                self.server.scheduler.run_task(
                    self, lambda: self._send_transfer_form(pl, key),
                    delay=2,  # a few ticks, never call directly/synchronously
                )
            except Exception as e:
                self.logger.error(f"[XuidAntiBot] Error handling server-selection form close: {e}")

        form.on_close = on_close
        p.send_form(form)

    # ── Timeout / the actual transfer ───────────────────────────

    def _check_transfer_timeout(self, p, key: str) -> None:
        if key not in self._pending_transfer_deadline:
            return  # already picked a server or already left
        try:
            p.kick("You did not select a server in time. Please rejoin.")
        except Exception as e:
            self.logger.error(f"[XuidAntiBot] Error kicking {p.name} for a server-selection timeout: {e}")
        finally:
            self._pending_transfer_deadline.pop(key, None)
            self._close_count.pop(key, None)

    def _do_transfer(self, p, target: dict) -> None:
        key = self._player_key(p)
        try:
            p.send_message(
                f"{ColorFormat.GREEN}Transferring you to {ColorFormat.WHITE}{target['name']}{ColorFormat.GREEN}..."
            )
            p.transfer(target["host"], target["port"])
            self.logger.info(
                f"{ColorFormat.GREEN}[XuidAntiBot] Transferred {p.name} to server "
                f"'{target['name']}' ({target['host']}:{target['port']})"
            )
            # transfer() succeeded -> the player left this server;
            # clear pending here. If they come back later they rejoin
            # normally through the join event with fresh pending
            # state.
            self._clear_pending(p, key)
        except Exception as e:
            self.logger.error(
                f"[XuidAntiBot] Error transferring {p.name} to {target.get('host')}:{target.get('port')}: {e}"
            )
            p.send_message(
                f"{ColorFormat.RED}Server transfer failed right now, please try again or contact an admin."
            )
            # transfer failed -> the player is STILL on this map, which
            # is then their real map -> clear pending on the spot.
            self._clear_pending(p, key)
