package com.gigachad.pal.tracker;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.block.BlockState;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.util.math.BlockPos;

import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * Aggregates block breaking into one readable summary per mining session, instead of the old
 * behaviour of writing a line per block ("Broke Stone at 123,45,-678") — which drowned every
 * interesting event in noise and told the model nothing useful.
 * <p>
 * Valuable ores still get their own immediate line, because that is the moment worth reacting to.
 */
public class MiningTracker implements Tracker {
    private static final long IDLE_MS = 2_500L;
    private static final long MAX_SESSION_MS = 60_000L;
    /** Below this, a "session" is just incidental block breaking and is not worth a line. */
    private static final int MIN_BLOCKS_TO_REPORT = 5;

    private static final Set<String> VALUABLE = Set.of(
            "diamond_ore", "deepslate_diamond_ore",
            "emerald_ore", "deepslate_emerald_ore",
            "ancient_debris");

    private final Map<String, Integer> session = new LinkedHashMap<>();
    /** Ores already seen this session — reset per world, unlike the old permanent JSON file. */
    private final Set<String> seenOres = new HashSet<>();

    private long startTime;
    private long lastBreak;
    private boolean active;
    private int oresThisSession;

    @Override
    public void onSessionStart(ClientPlayerEntity player, EventLog log) {
        session.clear();
        seenOres.clear();
        active = false;
        oresThisSession = 0;
    }

    /** Called from {@code ClientPlayerInteractionManagerMixin}. */
    public void onBlockBroken(BlockState state, BlockPos pos, EventLog log) {
        String path = Names.blockPath(state);
        long now = System.currentTimeMillis();

        if (!active) {
            active = true;
            startTime = now;
            session.clear();
            oresThisSession = 0;
        }
        lastBreak = now;
        session.merge(Names.readable(state), 1, Integer::sum);

        if (!isOre(path)) return;
        oresThisSession++;

        boolean first = seenOres.add(path);
        String name = Names.readable(state);

        if (VALUABLE.contains(path)) {
            log.log(Level.NOTABLE, "valuable_ore", first
                    ? String.format("Found %s for the first time this session, at Y=%d!",
                            name.toUpperCase(), pos.getY())
                    : String.format("Mined another %s at Y=%d.", name, pos.getY()));
        } else if (first) {
            log.log(Level.NOTABLE, "first_ore",
                    String.format("First %s of the session, at Y=%d.", name, pos.getY()));
        }
    }

    @Override
    public void tick(ClientPlayerEntity player, EventLog log, long tick) {
        if (!active || tick % 10 != 0) return;

        long now = System.currentTimeMillis();
        boolean stopped = now - lastBreak > IDLE_MS;
        boolean tooLong = now - startTime > MAX_SESSION_MS;
        if (!stopped && !tooLong) return;

        flush(log, stopped);
    }

    @Override
    public void onSessionEnd(EventLog log) {
        if (active) flush(log, true);
    }

    private void flush(EventLog log, boolean finished) {
        int total = session.values().stream().mapToInt(Integer::intValue).sum();
        long seconds = Math.max(1L, (System.currentTimeMillis() - startTime) / 1000L);

        if (total >= MIN_BLOCKS_TO_REPORT || oresThisSession > 0) {
            StringBuilder sb = new StringBuilder();
            sb.append(finished ? "Finished mining" : "Still mining");
            sb.append(" — ").append(seconds).append("s, ").append(total);
            sb.append(total == 1 ? " block: " : " blocks: ");
            session.forEach((name, count) -> sb.append(count).append("x ").append(name).append(", "));
            sb.setLength(sb.length() - 2);
            sb.append('.');
            log.log(Level.INFO, "mining", sb.toString());
        }

        if (finished) {
            active = false;
            session.clear();
        } else {
            // Rolling window: keep the session going but start counting again.
            session.clear();
            startTime = System.currentTimeMillis();
            oresThisSession = 0;
        }
    }

    private static boolean isOre(String path) {
        return path.endsWith("_ore") || path.equals("ancient_debris");
    }
}
