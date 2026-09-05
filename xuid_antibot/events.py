# -*- coding: utf-8 -*-
# Copyright 2026 dyan1619
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blocks every action of players who are pending (awaiting
verification).

Pending players are not in the real world yet: no chat, no movement,
no damage, no placing/breaking blocks, no interaction/item use/
teleport. See xuid_antibot/__init__.py for where this module sits in the
package."""
from endstone import ColorFormat, Player
from endstone.event import (
    event_handler,
    PlayerQuitEvent,
    PlayerChatEvent,
    PlayerMoveEvent,
    PlayerTeleportEvent,
    ActorDamageEvent,
    BlockBreakEvent,
    BlockPlaceEvent,
    PlayerInteractEvent,
    PlayerInteractActorEvent,
    PlayerDropItemEvent,
    PlayerPickupItemEvent,
    PlayerItemConsumeEvent,
    PlayerCommandEvent,
    PlayerEmoteEvent,
    PlayerBedEnterEvent,
    PlayerItemHeldEvent,
    PlayerGameModeChangeEvent,
    PlayerPortalEvent,
    PlayerJumpEvent,
    ActorKnockbackEvent,
    PlayerSkinChangeEvent,
)


class PlayerEventsMixin:
    # ── Leaving the server: clean up runtime state by player_key ─

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        key = self._player_key(event.player)
        # Clean the verification-form state + server-selection
        # deadline, then go through _clear_pending (the single
        # pending-clearing point) so internal dicts never leak and
        # stay consistent with every other clearing point.
        self._discard_verify_state(key)
        self._pending_transfer_deadline.pop(key, None)
        self._clear_pending(event.player, key)

    # ── Chat ────────────────────────────────────────────────────

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()
            event.player.send_message(
                f"{ColorFormat.RED}You need to verify (click the button on the form) before you can chat."
            )

    # ── Damage / knockback: the actor is the RECEIVING side ─────
    # A pending player is both frozen and blind -> cancel damage
    # aimed at them so they can't be killed at spawn for nothing;
    # cancel knockback so they can't be launched off the pinned
    # position (explosions, pistons...).
    #
    # Husks are a special case: on a successful melee hit they also
    # apply the Hunger status effect to the victim, independently of
    # the damage itself. Hunger then drains food/saturation over the
    # following seconds through normal exhaustion — cancelling the
    # ActorDamageEvent does NOT stop this, since the effect isn't
    # part of the damage event. If left unhandled, a griefer luring a
    # desert husk next to a pending (frozen, blind, defenseless)
    # player can still drain their food bar even though no damage
    # gets through. There is no attribute API to read or reset
    # food/saturation directly (see README), so instead of trying to
    # restore a value, we remove the cause: clear the Hunger effect
    # right after it would have been applied. `/effect ... clear
    # hunger` only strips that one effect and leaves everything else
    # (food, saturation, other effects) untouched.

    @event_handler
    def on_actor_damage(self, event: ActorDamageEvent) -> None:
        if isinstance(event.actor, Player) and self._is_pending(event.actor):
            event.cancel()
            attacker = event.damage_source.actor
            if attacker is not None and attacker.type == "minecraft:husk":
                event.actor.perform_command("effect @s clear hunger")

    @event_handler
    def on_actor_knockback(self, event: ActorKnockbackEvent) -> None:
        if isinstance(event.actor, Player) and self._is_pending(event.actor):
            event.cancel()

    # ── Movement / teleport: pin the coordinates at spawn ────────
    # Compare coordinates only (x, y, z), ignore yaw/pitch (looking
    # around doesn't count as moving).
    #
    # PlayerTeleportEvent is a SEPARATE event from PlayerMoveEvent
    # (ender pearl, chorus fruit, /tp...). NO temporary flag is used
    # to tell the plugin's own teleports apart from unauthorized ones
    # — both internal teleports naturally fall into an early-return
    # branch:
    #   - _apply_pending_isolation: the teleport runs BEFORE
    #     _pending_spawn_loc[key] is set -> spawn is None.
    #   - _clear_pending_isolation: the teleport runs AFTER
    #     pending_ids.discard(key) -> key not in pending_ids.

    @event_handler
    def on_player_move(self, event: PlayerMoveEvent) -> None:
        if not self.pending_ids:
            return
        key = self._player_key(event.player)
        if key not in self.pending_ids:
            return
        spawn = self._pending_spawn_loc.get(key)
        if spawn is None:
            return
        to = event.to_location
        if (to.x, to.y, to.z) != spawn:
            event.cancel()

    @event_handler
    def on_player_teleport(self, event: PlayerTeleportEvent) -> None:
        if not self.pending_ids:
            return
        key = self._player_key(event.player)
        if key not in self.pending_ids:
            return
        spawn = self._pending_spawn_loc.get(key)
        if spawn is None:
            return
        to = event.to_location
        if (to.x, to.y, to.z) != spawn:
            event.cancel()

    # ── Block world interaction while pending ────────────────────
    # Lock down "everything": no placing/breaking blocks, no item
    # use/opening chests/levers, no riding/trading, no dropping/
    # picking up items, no eating or drinking — even standing still,
    # blocks near the isolation zone are within reach.

    @event_handler
    def on_block_break(self, event: BlockBreakEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_block_place(self, event: BlockPlaceEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent) -> None:
        # Covers both item use (right-click air) AND block interaction
        # (chests, doors, buttons...) — no distinction, block it all.
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_interact_actor(self, event: PlayerInteractActorEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_drop_item(self, event: PlayerDropItemEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_pickup_item(self, event: PlayerPickupItemEvent) -> None:
        # Dropped items drifting close are still auto-picked-up ->
        # block it so they can't "vacuum up" other people's loot.
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_item_consume(self, event: PlayerItemConsumeEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_command(self, event: PlayerCommandEvent) -> None:
        # All of this plugin's commands are admin commands, and both
        # verification/transfer go through forms — blocking commands
        # absolutely breaks no flow; it just stops pending players
        # from running other commands if the server has loose
        # permission setup (e.g. /tp, /gamemode for a default rank).
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_emote(self, event: PlayerEmoteEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_bed_enter(self, event: PlayerBedEnterEvent) -> None:
        # Sleeping in a bed sets the spawn point — a real-world action.
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_item_held(self, event: PlayerItemHeldEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_game_mode_change(self, event: PlayerGameModeChangeEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_portal(self, event: PlayerPortalEvent) -> None:
        # Changing dimension through a portal — a real-world action
        # (like the bed).
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_jump(self, event: PlayerJumpEvent) -> None:
        if self._is_pending(event.player):
            event.cancel()

    @event_handler
    def on_player_skin_change(self, event: PlayerSkinChangeEvent) -> None:
        # Skin-change spam causes lag (the server broadcasts skins to
        # nearby players) -> block it while pending.
        if self._is_pending(event.player):
            event.cancel()
