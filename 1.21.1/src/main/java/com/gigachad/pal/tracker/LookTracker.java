package com.gigachad.pal.tracker;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.entity.AgeableMob;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.projectile.ProjectileUtil;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.HashMap;
import java.util.Map;

/**
 * What the player is deliberately looking at.
 * <p>
 * The first version of this mod logged a line every time the crosshair crossed anything, which
 * produced dozens of lines a second while panning across a herd. Here a look only counts once
 * it has been held on the same entity for {@link #DWELL_MS}, which is the difference between
 * glancing past a cow and actually staring at one.
 */
public class LookTracker implements Tracker {
    private static final int CHECK_EVERY_TICKS = 5;
    private static final double RANGE = 24.0;
    /** How long the crosshair must stay on one entity before it counts as looking at it. */
    private static final long DWELL_MS = 1_500L;
    /** Don't re-report the same kind of creature more often than this. */
    private static final long REPEAT_MS = 45_000L;

    private final Map<String, Long> lastReported = new HashMap<>();
    private int currentId = -1;
    private long lookingSince = 0L;
    private boolean reported = false;

    @Override
    public void onSessionStart(LocalPlayer player, EventLog log) {
        lastReported.clear();
        currentId = -1;
        reported = false;
    }

    @Override
    public void tick(LocalPlayer player, EventLog log, long tick) {
        if (tick % CHECK_EVERY_TICKS != 0) return;

        Entity target = raycastEntity(player);
        long now = System.currentTimeMillis();

        if (target == null) {
            currentId = -1;
            reported = false;
            return;
        }

        // Switched target: restart the dwell timer.
        if (target.getId() != currentId) {
            currentId = target.getId();
            lookingSince = now;
            reported = false;
            return;
        }

        if (reported || now - lookingSince < DWELL_MS) return;
        reported = true;

        String name = Names.readable(target);
        Long previous = lastReported.get(name);
        if (previous != null && now - previous < REPEAT_MS) return;
        lastReported.put(name, now);

        double distance = Math.sqrt(target.distanceToSqr(player));
        log.log(Level.INFO, "looking_at", describe(target, name, distance));
    }

    private static String describe(Entity target, String name, double distance) {
        String how;
        if (target instanceof Monster) {
            how = "Eyeing";
        } else if (target instanceof Player) {
            how = "Staring at";
        } else if (target instanceof AgeableMob) {
            how = "Staring at";
        } else {
            how = "Looking at";
        }

        if (target instanceof LivingEntity living && living.isBaby()) {
            name = "baby " + name;
        }
        return String.format("%s a %s, %.0f blocks away.", how, name, distance);
    }

    /**
     * Single raycast down the crosshair. Cheaper than the old approach of scanning every entity
     * in a 128-block box and raycasting to each one.
     */
    private static Entity raycastEntity(LocalPlayer player) {
        Vec3 eye = player.getEyePosition();
        Vec3 direction = player.getViewVector(1.0f);
        Vec3 end = eye.add(direction.scale(RANGE));
        AABB box = player.getBoundingBox().expandTowards(direction.scale(RANGE)).inflate(1.0);

        EntityHitResult hit = ProjectileUtil.getEntityHitResult(
                player, eye, end, box,
                entity -> entity instanceof LivingEntity && entity.isAlive() && !entity.isSpectator(),
                RANGE * RANGE);

        return hit == null ? null : hit.getEntity();
    }
}
