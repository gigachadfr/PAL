package com.gigachad.pal.tracker;

import com.gigachad.pal.PlayerActionLogger;
import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * Kills, inferred client-side. There is no "you killed X" packet, so a death is attributed to
 * the player when they hit that entity shortly before it died.
 * <p>
 * Routine kills are batched ("Killed 5x Zombie, 2x Skeleton") while bosses and rare mobs get
 * their own line immediately.
 */
public class CombatTracker implements Tracker {
    private static final long ATTRIBUTION_WINDOW_MS = 5_000L;
    private static final long BATCH_IDLE_MS = 8_000L;

    private static final Set<String> BIG_KILLS = Set.of(
            "ender_dragon", "wither", "warden", "elder_guardian",
            "ravager", "evoker", "piglin_brute", "hoglin");

    /** Entity network id -> time we last hit it. */
    private final Map<Integer, Long> recentlyHit = new HashMap<>();
    private final Map<String, Integer> pendingKills = new LinkedHashMap<>();
    private long lastKillTime;

    @Override
    public void onSessionStart(LocalPlayer player, EventLog log) {
        recentlyHit.clear();
        pendingKills.clear();
    }

    /** Called from {@code MultiPlayerGameModeMixin} when the player swings at something. */
    public void onAttack(Entity target) {
        long now = System.currentTimeMillis();
        recentlyHit.put(target.getId(), now);
        recentlyHit.entrySet().removeIf(e -> now - e.getValue() > ATTRIBUTION_WINDOW_MS);
    }

    /**
     * Called from {@code LivingEntityMixin} — runs client-side when the death status arrives.
     * Skipped while hosting, where {@code ServerKillMixin} knows for certain who killed what.
     */
    public void onEntityDied(Entity entity, EventLog log) {
        if (PlayerActionLogger.hostMode()) return;

        Long hitAt = recentlyHit.remove(entity.getId());
        if (hitAt == null || System.currentTimeMillis() - hitAt > ATTRIBUTION_WINDOW_MS) {
            return; // not our kill
        }
        record(entity, log);
    }

    /** Called from {@code ServerKillMixin}: the game confirmed we landed the killing blow. */
    public void onConfirmedKill(Entity entity, EventLog log) {
        recentlyHit.remove(entity.getId());
        record(entity, log);
    }

    private void record(Entity entity, EventLog log) {
        String path = Names.entityPath(entity);
        String name = Names.readable(entity);

        if (entity instanceof Player) {
            log.log(Level.NOTABLE, "player_kill",
                    String.format("Killed player %s.", entity.getName().getString()));
            return;
        }

        if (BIG_KILLS.contains(path)) {
            log.log(Level.NOTABLE, "big_kill", String.format("Killed a %s!", name));
            return;
        }

        pendingKills.merge(name, 1, Integer::sum);
        lastKillTime = System.currentTimeMillis();
    }

    @Override
    public void tick(LocalPlayer player, EventLog log, long tick) {
        if (pendingKills.isEmpty() || tick % 20 != 0) return;
        if (System.currentTimeMillis() - lastKillTime < BATCH_IDLE_MS) return;
        flush(log);
    }

    @Override
    public void onSessionEnd(EventLog log) {
        if (!pendingKills.isEmpty()) flush(log);
    }

    private void flush(EventLog log) {
        StringBuilder sb = new StringBuilder("Killed ");
        pendingKills.forEach((name, count) -> sb.append(count).append("x ").append(name).append(", "));
        sb.setLength(sb.length() - 2);
        sb.append('.');
        log.log(Level.INFO, "kills", sb.toString());
        pendingKills.clear();
    }
}
