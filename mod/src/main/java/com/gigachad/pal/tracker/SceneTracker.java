package com.gigachad.pal.tracker;

import com.gigachad.pal.context.WorldContext;
import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.google.gson.JsonObject;
import net.minecraft.client.network.ClientPlayerEntity;

/**
 * Keeps the AI aware of where the player is. Emits a periodic scene line, and calls out the
 * transitions worth reacting to: nightfall, a storm rolling in, entering the Nether, surfacing
 * from a long cave trip.
 */
public class SceneTracker implements Tracker {
    /** Re-state the scene at most this often, even when nothing changed. */
    private static final long HEARTBEAT_MS = 180_000L;
    /**
     * Floor between two scene lines. Without it, jumping or walking under a tree flips
     * isSkyVisible and emits "On the surface" / "Under cover" every other second — that alone
     * was 46% of a real session log. A dimension change bypasses this.
     */
    private static final long MIN_INTERVAL_MS = 25_000L;
    private static final int CHECK_EVERY_TICKS = 20;

    /** Last state published as a scene line. */
    private WorldContext last;
    /** Last state observed, used for transition detection only. */
    private WorldContext lastSeen;
    private long lastEmit;

    @Override
    public void onSessionStart(ClientPlayerEntity player, EventLog log) {
        last = null;
        lastSeen = null;
        lastEmit = 0L;
    }

    @Override
    public void tick(ClientPlayerEntity player, EventLog log, long tick) {
        if (tick % CHECK_EVERY_TICKS != 0) return;

        WorldContext now = WorldContext.of(player);
        long elapsed = System.currentTimeMillis() - lastEmit;

        // Transitions are compared against the previous *observation*, so none is ever missed,
        // and they bypass the cooldown entirely — they are the reactable moments.
        boolean dimensionChanged = false;
        if (lastSeen != null) {
            if (!now.dimension().equals(lastSeen.dimension())) {
                dimensionChanged = true;
                log.log(Level.NOTABLE, "dimension",
                        String.format("Entered %s.", now.dimension()));
            } else if (!now.phase().equals(lastSeen.phase())) {
                if ("night".equals(now.phase())) {
                    log.log(Level.NOTABLE, "nightfall",
                            "Night has fallen — hostile mobs are spawning now.");
                } else if ("day".equals(now.phase())) {
                    log.log(Level.NOTABLE, "daybreak", "The sun is up, it is daytime again.");
                }
            } else if (!now.weather().equals(lastSeen.weather()) && !"clear".equals(now.weather())) {
                log.log(Level.NOTABLE, "weather",
                        "thunderstorm".equals(now.weather())
                                ? "A thunderstorm has started."
                                : "It has started raining.");
            }
        }
        lastSeen = now;

        // The scene line itself is compared against what was last *published*, so a change held
        // back by the cooldown is still emitted once the cooldown expires rather than lost.
        boolean changed = now.differsMeaningfullyFrom(last);
        if (!dimensionChanged) {
            if (!changed && elapsed < HEARTBEAT_MS) return;
            if (elapsed < MIN_INTERVAL_MS) return;
        }

        last = now;
        lastEmit = System.currentTimeMillis();
        log.log(Level.INFO, "scene", now.describe(), toData(now));
    }

    private static JsonObject toData(WorldContext ctx) {
        JsonObject data = new JsonObject();
        data.addProperty("dimension", ctx.dimension());
        data.addProperty("biome", ctx.biome());
        data.addProperty("phase", ctx.phase());
        data.addProperty("weather", ctx.weather());
        data.addProperty("y", ctx.y());
        return data;
    }

    /** Exposed so other trackers can enrich their own messages with the current scene. */
    public WorldContext current() {
        return last;
    }
}
