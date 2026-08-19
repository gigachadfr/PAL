package com.gigachad.pal.tracker;

import com.gigachad.pal.log.EventLog;
import net.minecraft.client.player.LocalPlayer;

/**
 * A source of log events. Everything runs on the client thread, so implementations need no
 * synchronisation of their own.
 */
public interface Tracker {

    /** Called once when the player joins a world. Reset any per-session state here. */
    default void onSessionStart(LocalPlayer player, EventLog log) {}

    /**
     * Called every client tick (20/s). Implementations are expected to throttle themselves —
     * use {@code tick % n == 0} rather than doing real work 20 times a second.
     */
    default void tick(LocalPlayer player, EventLog log, long tick) {}

    /** Called when the player leaves the world. Flush anything still pending. */
    default void onSessionEnd(EventLog log) {}
}
