package com.gigachad.pal.tracker;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.block.BlockState;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.util.math.BlockPos;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Same idea as {@link MiningTracker}, for placing blocks: one summary per building session,
 * with the footprint of what was built.
 */
public class BuildTracker implements Tracker {
    private static final long IDLE_MS = 4_000L;
    private static final long MAX_SESSION_MS = 90_000L;
    private static final int MIN_BLOCKS_TO_REPORT = 8;

    private final Map<String, Integer> session = new LinkedHashMap<>();

    private long startTime;
    private long lastPlace;
    private boolean active;
    private int minX, maxX, minY, maxY, minZ, maxZ;

    @Override
    public void onSessionStart(ClientPlayerEntity player, EventLog log) {
        session.clear();
        active = false;
    }

    /** Called from {@code ClientPlayerInteractionManagerMixin}. */
    public void onBlockPlaced(BlockState state, BlockPos pos) {
        long now = System.currentTimeMillis();

        if (!active) {
            active = true;
            startTime = now;
            session.clear();
            minX = maxX = pos.getX();
            minY = maxY = pos.getY();
            minZ = maxZ = pos.getZ();
        }

        lastPlace = now;
        session.merge(Names.readable(state), 1, Integer::sum);
        minX = Math.min(minX, pos.getX());
        maxX = Math.max(maxX, pos.getX());
        minY = Math.min(minY, pos.getY());
        maxY = Math.max(maxY, pos.getY());
        minZ = Math.min(minZ, pos.getZ());
        maxZ = Math.max(maxZ, pos.getZ());
    }

    @Override
    public void tick(ClientPlayerEntity player, EventLog log, long tick) {
        if (!active || tick % 10 != 0) return;

        long now = System.currentTimeMillis();
        boolean stopped = now - lastPlace > IDLE_MS;
        if (!stopped && now - startTime <= MAX_SESSION_MS) return;

        flush(log, stopped);
    }

    @Override
    public void onSessionEnd(EventLog log) {
        if (active) flush(log, true);
    }

    /**
     * Names the shape from its bounding box and how densely it is filled.
     * <p>
     * Dimensions alone are useless to a commentator — "13 blocks across a 17x5x34 area" says
     * nothing, while "placing blocks while moving around" says exactly what happened.
     */
    private static String describeShape(int w, int h, int d, int count) {
        long volume = (long) w * h * d;
        double density = volume <= 0 ? 1.0 : (double) count / volume;
        int longest = Math.max(w, d);
        int shortest = Math.min(w, d);

        // Sparse blocks over a huge box: walking along placing the odd block, not building.
        if (volume > 150 && density < 0.05) {
            return "scattered blocks while moving around";
        }
        if (w == 1 && d == 1 && h > 2) {
            return h >= 8 ? "a tall pillar (" + h + " blocks up)" : "a pillar " + h + " blocks up";
        }
        if (h == 1 && w > 2 && d > 2) {
            return "a floor";
        }
        if (h == 1 && longest > 3 && shortest <= 2) {
            return "a path";
        }
        // Rising roughly as fast as it extends sideways.
        if (h > 2 && shortest <= 2 && Math.abs(h - longest) <= 2) {
            return "a staircase";
        }
        if (h > 2 && shortest == 1) {
            return "a wall";
        }
        if (h <= 2 && longest > 5 && shortest <= 2) {
            return "a bridge";
        }
        if (w > 2 && d > 2 && h > 2) {
            return density > 0.5 ? "a solid block of blocks" : "a room or shelter";
        }
        return "something";
    }

    private void flush(EventLog log, boolean finished) {
        int total = session.values().stream().mapToInt(Integer::intValue).sum();
        long seconds = Math.max(1L, (System.currentTimeMillis() - startTime) / 1000L);

        if (total >= MIN_BLOCKS_TO_REPORT) {
            int w = maxX - minX + 1;
            int h = maxY - minY + 1;
            int d = maxZ - minZ + 1;

            StringBuilder sb = new StringBuilder();
            sb.append(finished ? "Finished building " : "Still building ");
            sb.append(describeShape(w, h, d, total));
            sb.append(" — ").append(seconds).append("s, ").append(total);
            sb.append(total == 1 ? " block: " : " blocks: ");
            session.forEach((name, count) -> sb.append(count).append("x ").append(name).append(", "));
            sb.setLength(sb.length() - 2);
            sb.append('.');
            log.log(Level.INFO, "building", sb.toString());
        }

        if (finished) {
            active = false;
        } else {
            startTime = System.currentTimeMillis();
        }
        session.clear();
    }
}
