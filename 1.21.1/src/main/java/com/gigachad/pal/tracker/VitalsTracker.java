package com.gigachad.pal.tracker;

import com.gigachad.pal.PlayerActionLogger;
import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.phys.AABB;

import java.util.List;

/**
 * Health, hunger, air and XP, tracked by watching values change on the client rather than by
 * hooking server-side damage events — which is what makes this work on remote servers.
 * <p>
 * The exact damage source is not available client-side, so it is inferred from the player's
 * state (in lava, drowning, a hostile within reach…). Deaths are logged by
 * {@code DeathScreenMixin} instead, which gets the game's own exact death message.
 */
public class VitalsTracker implements Tracker {
    private static final float LOW_HEALTH_FRACTION = 0.3f;
    private static final float RECOVERED_FRACTION = 0.5f;
    private static final float MIN_DAMAGE_TO_LOG = 0.5f;
    private static final long DAMAGE_THROTTLE_MS = 4_000L;

    private float lastHealth = -1f;
    private int lastFood = -1;
    private int lastXpLevel = -1;
    private double lastFallDistance = 0.0;  // fallDistance is a double as of 26.1
    private float pendingDamage = 0f;
    private static final long LEVEL_SETTLE_MS = 3_000L;
    private int pendingLevel = 0;
    private int reportedLevel = 0;
    private long levelSettleAt = 0L;
    private boolean lowHealthFlagged = false;
    private boolean starvingFlagged = false;
    private boolean dippedBelowHalf = false;

    @Override
    public void onSessionStart(LocalPlayer player, EventLog log) {
        lastHealth = -1f;
        lastFood = -1;
        lastXpLevel = -1;
        pendingLevel = 0;
        reportedLevel = 0;
        lastFallDistance = 0.0;
        lowHealthFlagged = false;
        starvingFlagged = false;
        dippedBelowHalf = false;
    }

    @Override
    public void tick(LocalPlayer player, EventLog log, long tick) {
        if (player.isSpectator() || player.isCreative()) {
            lastHealth = player.getHealth();
            return;
        }

        float health = player.getHealth();
        float maxHealth = player.getMaxHealth();
        int food = player.getFoodData().getFoodLevel();
        int xp = player.experienceLevel;

        // ---- damage taken -------------------------------------------------
        // Skipped while hosting: ServerDamageMixin reports the real source instead of guessing.
        if (!PlayerActionLogger.hostMode()
                && lastHealth >= 0f && health < lastHealth - MIN_DAMAGE_TO_LOG && health > 0f) {
            // Accumulate, so a burst that the throttle swallows is still reported in full.
            // Reporting only the last tick's loss produced lines like "Took 1 damage. Health
            // now 11/20" right after 19/20.
            pendingDamage += lastHealth - health;

            String cause = guessDamageSource(player);
            String msg = String.format("Took %.0f damage from %s. Health now %.0f/%.0f.",
                    pendingDamage, cause, health, maxHealth);

            boolean severe = pendingDamage >= maxHealth * 0.25f;
            if (severe) {
                log.log(Level.NOTABLE, "damage", msg);
                pendingDamage = 0f;
            } else if (log.logThrottled(Level.INFO, "damage", msg, DAMAGE_THROTTLE_MS)) {
                pendingDamage = 0f;
            }
        } else if (health > lastHealth) {
            pendingDamage = 0f;  // regenerating: whatever went unreported is stale now
        }

        // ---- low health ---------------------------------------------------
        if (health > 0f && health <= maxHealth * LOW_HEALTH_FRACTION) {
            if (!lowHealthFlagged) {
                lowHealthFlagged = true;
                log.log(Level.CRITICAL, "low_health",
                        String.format("Health is critical: %.0f/%.0f left.", health, maxHealth));
            }
        } else if (health >= maxHealth * RECOVERED_FRACTION) {
            lowHealthFlagged = false;
        }

        // ---- recovery -----------------------------------------------------
        // Damage is logged, healing never was, so the log's last word on the player's health
        // stayed "took 6 damage, 8 left" for the rest of the session. Announced once per dip,
        // which costs one line per fight at most.
        if (health >= maxHealth && dippedBelowHalf) {
            dippedBelowHalf = false;
            log.log(Level.INFO, "recovered", "Back to full health.");
        } else if (health < maxHealth * 0.5f) {
            dippedBelowHalf = true;
        }

        // ---- hunger -------------------------------------------------------
        if (food <= 3 && !starvingFlagged) {
            starvingFlagged = true;
            log.log(Level.NOTABLE, "hunger",
                    String.format("Starving — hunger down to %d/20.", food));
        } else if (food >= 10) {
            starvingFlagged = false;
        }

        // ---- drowning -----------------------------------------------------
        int air = player.getAirSupply();
        if (air <= 0 && player.isUnderWater()) {
            log.logThrottled(Level.CRITICAL, "drowning", "Out of air and drowning.", 5_000L);
        }

        // ---- level up -----------------------------------------------------
        // A dragon kill dumps thousands of XP at once and the level counter sweeps upward,
        // which used to emit a line for every multiple of 5 it passed through. Wait for the
        // climb to settle, then report where it landed.
        if (lastXpLevel >= 0 && xp > lastXpLevel) {
            pendingLevel = xp;
            levelSettleAt = System.currentTimeMillis() + LEVEL_SETTLE_MS;
        } else if (pendingLevel > 0 && System.currentTimeMillis() >= levelSettleAt) {
            if (pendingLevel % 5 == 0 || pendingLevel - reportedLevel >= 5) {
                log.log(Level.INFO, "level_up",
                        String.format("Reached experience level %d.", pendingLevel));
                reportedLevel = pendingLevel;
            }
            pendingLevel = 0;
        }

        lastHealth = health;
        lastFood = food;
        lastXpLevel = xp;
        lastFallDistance = player.fallDistance;
    }

    /**
     * Best-effort attribution of damage using only client-visible state. Order matters: the
     * environmental causes are unambiguous, so they win over the "a mob is nearby" guess.
     */
    private String guessDamageSource(LocalPlayer player) {
        if (player.isInLava()) return "lava";
        if (player.isOnFire()) return "fire";
        if (player.getAirSupply() <= 0 && player.isUnderWater()) return "drowning";
        if (player.isInWall()) return "suffocation";
        if (lastFallDistance > 3.0f) return String.format("a %.0f block fall", lastFallDistance);

        Entity closest = closestHostile(player);
        return closest == null ? "an unknown source" : "a " + Names.readable(closest);
    }

    private Entity closestHostile(LocalPlayer player) {
        // Wide enough to catch a skeleton shooting from across the room — the old 5 block box
        // left most ranged hits attributed to "an unknown source".
        AABB box = player.getBoundingBox().inflate(16.0);
        List<Monster> mobs =
                player.level().getEntitiesOfClass(Monster.class, box, e -> e.isAlive());

        Entity closest = null;
        double best = Double.MAX_VALUE;
        for (Monster mob : mobs) {
            double d = mob.distanceToSqr(player);
            if (d < best) {
                best = d;
                closest = mob;
            }
        }
        return closest;
    }
}
