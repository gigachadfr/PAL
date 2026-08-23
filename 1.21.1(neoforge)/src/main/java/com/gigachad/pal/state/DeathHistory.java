package com.gigachad.pal.state;

import com.gigachad.pal.util.Causes;
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonSyntaxException;
import com.gigachad.pal.util.GameDir;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Every death, kept across sessions.
 * <p>
 * Vanilla already counts deaths, and {@code ENTITY_KILLED_BY} breaks down exactly which creature
 * killed you and how often — but nothing in vanilla records that four of your deaths were falls
 * and two were lava. Those are all just {@code +1} on the deaths counter. This fills that gap,
 * and adds the where and when, so the commentator can say "third time you have died in the
 * Nether" instead of reacting to each death as if it were the first.
 * <p>
 * Written to its own file, never truncated with the session log.
 */
public class DeathHistory {
    private static final Logger LOGGER = LoggerFactory.getLogger("playeractionlogger");
    private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");
    private static final String DIR = "logs/player_actions";
    private static final String FILE = "deaths.json";
    /** Old enough deaths stop being interesting long before the file becomes large. */
    private static final int MAX_ENTRIES = 400;

    private final Gson gson = new Gson();
    private final List<JsonObject> entries = new ArrayList<>();
    private Path file;
    private boolean loaded = false;

    /** Reads the file once per game launch. Safe to call on every session start. */
    public synchronized void load() {
        if (loaded) return;
        loaded = true;
        try {
            Path dir = GameDir.get().resolve(DIR);
            Files.createDirectories(dir);
            file = dir.resolve(FILE);
            if (!Files.exists(file)) return;

            JsonArray array = gson.fromJson(
                    Files.readString(file, StandardCharsets.UTF_8), JsonArray.class);
            if (array == null) return;
            for (JsonElement element : array) {
                if (element.isJsonObject()) entries.add(element.getAsJsonObject());
            }
            LOGGER.info("Loaded {} past deaths", entries.size());
        } catch (IOException | JsonSyntaxException e) {
            // A corrupt history is not worth failing a session over — start a fresh one.
            LOGGER.warn("Could not read the death history, starting a new one", e);
            entries.clear();
        }
    }

    /**
     * @param translationKey the death message's translation key, or null on a server that sent
     *                       a plain-text message. The key is what makes the cause language-proof.
     */
    public synchronized void record(String message, String translationKey, String world,
                                    String dimension, int y) {
        JsonObject entry = new JsonObject();
        entry.addProperty("when", LocalDateTime.now().format(STAMP));
        entry.addProperty("cause", Causes.fromDeathKey(translationKey));
        entry.addProperty("message", message);
        entry.addProperty("world", world);
        entry.addProperty("dimension", dimension);
        entry.addProperty("y", y);
        entries.add(entry);

        while (entries.size() > MAX_ENTRIES) {
            entries.removeFirst();
        }
        save();
    }

    private void save() {
        if (file == null) return;
        JsonArray array = new JsonArray();
        entries.forEach(array::add);
        try {
            Files.writeString(file, gson.toJson(array), StandardCharsets.UTF_8);
        } catch (IOException e) {
            LOGGER.error("Could not write the death history", e);
        }
    }

    /**
     * Deaths grouped by cause, most frequent first, for one world only — vanilla's own death
     * counter is per-world too, so mixing worlds here would make the two disagree.
     */
    public synchronized JsonArray byCause(String world) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (JsonObject entry : entries) {
            if (!matchesWorld(entry, world)) continue;
            counts.merge(entry.get("cause").getAsString(), 1, Integer::sum);
        }

        List<Map.Entry<String, Integer>> sorted = new ArrayList<>(counts.entrySet());
        sorted.sort(Comparator.<Map.Entry<String, Integer>>comparingInt(Map.Entry::getValue).reversed());

        JsonArray array = new JsonArray();
        for (Map.Entry<String, Integer> count : sorted) {
            JsonObject item = new JsonObject();
            item.addProperty("cause", count.getKey());
            item.addProperty("count", count.getValue());
            array.add(item);
        }
        return array;
    }

    /** The last {@code limit} deaths in this world, newest last so it reads as a story. */
    public synchronized JsonArray recent(String world, int limit) {
        List<JsonObject> matching = new ArrayList<>();
        for (JsonObject entry : entries) {
            if (matchesWorld(entry, world)) matching.add(entry);
        }
        JsonArray array = new JsonArray();
        for (JsonObject entry : matching.subList(Math.max(0, matching.size() - limit), matching.size())) {
            array.add(entry);
        }
        return array;
    }

    public synchronized int countIn(String world) {
        return (int) entries.stream().filter(e -> matchesWorld(e, world)).count();
    }

    private static boolean matchesWorld(JsonObject entry, String world) {
        JsonElement value = entry.get("world");
        return value != null && value.getAsString().equals(world);
    }
}
