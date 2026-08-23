package com.gigachad.pal.log;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.gigachad.pal.util.GameDir;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.Map;

/**
 * Writes one JSON object per line (JSONL) to {@code <gamedir>/logs/player_actions/session.log}.
 * <p>
 * Kept deliberately small and synchronous: after aggregation the mod emits at most a handful of
 * events per second, so flushing on every write costs nothing and lets the Python bot react in
 * real time.
 */
public class EventLog {
    private static final Logger LOGGER = LoggerFactory.getLogger("playeractionlogger");
    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("HH:mm:ss");
    private static final String LOG_DIR = "logs/player_actions";
    private static final String LOG_FILE = "session.log";

    private final Gson gson = new Gson();
    private final Object lock = new Object();
    /** Key -> last emission time, backing {@link #logThrottled}. */
    private final Map<String, Long> lastSeen = new HashMap<>();

    private BufferedWriter writer;
    private long lastThrottleSweep = 0L;

    /** Opens (and truncates) the log file for a new session. Safe to call repeatedly. */
    public void start(String playerName, String worldName) {
        synchronized (lock) {
            closeWriter();
            lastSeen.clear();
            try {
                Path dir = GameDir.get().resolve(LOG_DIR);
                Files.createDirectories(dir);
                writer = Files.newBufferedWriter(
                        dir.resolve(LOG_FILE),
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.WRITE,
                        StandardOpenOption.TRUNCATE_EXISTING);
            } catch (IOException e) {
                LOGGER.error("Failed to open the action log", e);
                writer = null;
                return;
            }
        }

        JsonObject data = new JsonObject();
        data.addProperty("player", playerName);
        data.addProperty("world", worldName);
        log(Level.INFO, "session_start",
                String.format("New session started as %s in %s.", playerName, worldName), data);
    }

    public void log(Level level, String type, String message) {
        log(level, type, message, null);
    }

    public void log(Level level, String type, String message, JsonObject data) {
        JsonObject event = new JsonObject();
        event.addProperty("t", LocalTime.now().format(TIME));
        event.addProperty("lvl", level.name());
        event.addProperty("type", type);
        event.addProperty("msg", message);
        if (data != null && !data.isEmpty()) {
            event.add("data", data);
        }
        write(gson.toJson(event));
    }

    /**
     * Emits the event only if the same {@code type} has not been logged within
     * {@code minIntervalMs}. Used by the ambient trackers (danger, scene) so a persistent
     * condition does not flood the log every tick.
     *
     * @return whether the event was actually written
     */
    public boolean logThrottled(Level level, String type, String message, long minIntervalMs) {
        long now = System.currentTimeMillis();
        synchronized (lock) {
            Long previous = lastSeen.get(type);
            if (previous != null && now - previous < minIntervalMs) {
                return false;
            }
            lastSeen.put(type, now);

            // Keep the throttle map from growing without bound over a long session.
            if (now - lastThrottleSweep > 60_000L) {
                lastThrottleSweep = now;
                lastSeen.entrySet().removeIf(e -> now - e.getValue() > 300_000L);
            }
        }
        log(level, type, message);
        return true;
    }

    /** Forgets the throttle state for a type, so the next call to {@link #logThrottled} passes. */
    public void resetThrottle(String type) {
        synchronized (lock) {
            lastSeen.remove(type);
        }
    }

    private void write(String line) {
        synchronized (lock) {
            if (writer == null) return;
            try {
                writer.write(line);
                writer.newLine();
                writer.flush();
            } catch (IOException e) {
                LOGGER.error("Failed to write to the action log", e);
            }
        }
    }

    /** Closes the file. The old version never did this, so logs leaked across worlds. */
    public void close() {
        synchronized (lock) {
            closeWriter();
        }
    }

    private void closeWriter() {
        if (writer == null) return;
        try {
            writer.close();
        } catch (IOException e) {
            LOGGER.error("Failed to close the action log", e);
        }
        writer = null;
    }
}
