package com.gigachad.pal.tracker;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.mob.CreeperEntity;
import net.minecraft.entity.mob.HostileEntity;
import net.minecraft.util.math.Box;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Turns "what is about to go wrong" into log lines. This is the tracker that makes the
 * commentary feel alive — reacting to a creeper at your back is far better material than
 * reacting to the 200th stone block you mined.
 * <p>
 * Note: {@code MobEntity.getTarget()} is server-only, so "is it actually after me" cannot be
 * known on the client. Proximity plus line of sight is used as the stand-in.
 */
public class DangerTracker implements Tracker {
    private static final int CHECK_EVERY_TICKS = 10;
    private static final double CLOSE_RANGE = 6.0;
    private static final double AWARE_RANGE = 14.0;
    private static final long MOB_THROTTLE_MS = 20_000L;

    @Override
    public void tick(ClientPlayerEntity player, EventLog log, long tick) {
        if (tick % CHECK_EVERY_TICKS != 0) return;
        if (player.isSpectator() || player.isCreative()) return;

        // ---- a creeper about to blow -------------------------------------
        List<CreeperEntity> creepers = player.clientWorld.getEntitiesByClass(
                CreeperEntity.class, player.getBoundingBox().expand(CLOSE_RANGE), e -> e.isAlive());
        for (CreeperEntity creeper : creepers) {
            if (creeper.getFuseSpeed() > 0) {
                log.logThrottled(Level.CRITICAL, "creeper_fuse",
                        String.format("A Creeper is hissing %.0f blocks away, about to explode.",
                                Math.sqrt(creeper.squaredDistanceTo(player))),
                        3_000L);
                break;
            }
        }

        // ---- standing in lava --------------------------------------------
        if (player.isInLava()) {
            log.logThrottled(Level.CRITICAL, "in_lava", "In the lava and burning.", 5_000L);
        }

        // ---- a long fall in progress -------------------------------------
        if (player.fallDistance > 10.0f && !player.isFallFlying()) {
            log.logThrottled(Level.CRITICAL, "falling",
                    String.format("Falling — already %.0f blocks down.", player.fallDistance),
                    4_000L);
        }

        // ---- hostile mobs closing in -------------------------------------
        List<HostileEntity> mobs = player.clientWorld.getEntitiesByClass(
                HostileEntity.class, player.getBoundingBox().expand(AWARE_RANGE), e -> e.isAlive());
        if (mobs.isEmpty()) {
            log.resetThrottle("mobs_nearby");
            return;
        }

        long close = mobs.stream()
                .filter(m -> m.squaredDistanceTo(player) < CLOSE_RANGE * CLOSE_RANGE)
                .count();
        if (close == 0) return;

        Map<String, Integer> counts = new HashMap<>();
        for (HostileEntity mob : mobs) {
            counts.merge(Names.readable(mob), 1, Integer::sum);
        }

        StringBuilder sb = new StringBuilder("Surrounded by hostile mobs: ");
        counts.forEach((name, count) -> sb.append(count).append("x ").append(name).append(", "));
        sb.setLength(sb.length() - 2);
        sb.append('.');

        Level level = close >= 3 ? Level.CRITICAL : Level.NOTABLE;
        log.logThrottled(level, "mobs_nearby", sb.toString(), MOB_THROTTLE_MS);
    }
}
