package com.gigachad.pal.state;

import com.gigachad.pal.PlayerActionLogger;
import com.gigachad.pal.context.WorldContext;
import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.tracker.Tracker;
import com.gigachad.pal.util.Names;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.NonNullList;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.protocol.game.ServerboundClientCommandPacket;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.stats.Stats;
import net.minecraft.stats.StatsCounter;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.food.FoodData;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Block;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Publishes a live snapshot of the player to {@code logs/player_actions/player_state.json},
 * next to the event log.
 * <p>
 * The event log is a stream of things that <em>happened</em>; it is a poor way to answer "how
 * much health does he have right now". A commentator reading it would see "Took 6 damage,
 * health now 8/20" and still be saying "you are nearly dead" ten minutes and two golden apples
 * later. This file is the answer to "right now" instead: overwritten once a second, always
 * current, and cheap for the bot to read whenever it needs a fact rather than a reaction.
 * <p>
 * It also carries the parts of vanilla's own statistics worth commenting on — deaths, what
 * killed you, what you have killed — which the client normally only has after asking the server
 * for them, so this asks periodically.
 */
public class StateExporter implements Tracker {
    private static final Logger LOGGER = LoggerFactory.getLogger("playeractionlogger");
    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("HH:mm:ss");
    private static final String DIR = "logs/player_actions";
    private static final String FILE = "player_state.json";

    private static final int WRITE_EVERY_TICKS = 20;        // once a second
    private static final long STATS_REBUILD_MS = 5_000L;    // re-read the counters
    private static final long STATS_REQUEST_MS = 30_000L;   // ask the server to resend them
    private static final int TOP_ENTRIES = 12;

    /** Pretty-printed on purpose: this file gets opened by hand while debugging. */
    private final Gson gson = new GsonBuilder().setPrettyPrinting().create();
    private final DeathHistory deaths;

    private Path file;
    private String playerName = "";
    private String worldName = "";
    private JsonObject statsCache = new JsonObject();
    private long statsBuiltAt = 0L;
    private long statsRequestedAt = 0L;
    private boolean writeFailed = false;

    public StateExporter(DeathHistory deaths) {
        this.deaths = deaths;
    }

    @Override
    public void onSessionStart(LocalPlayer player, EventLog log) {
        playerName = player.getGameProfile().getName();
        worldName = PlayerActionLogger.describeWorld(Minecraft.getInstance());
        statsCache = new JsonObject();
        statsBuiltAt = 0L;
        statsRequestedAt = 0L;
        writeFailed = false;

        try {
            Path dir = FabricLoader.getInstance().getGameDir().resolve(DIR);
            Files.createDirectories(dir);
            file = dir.resolve(FILE);
        } catch (IOException e) {
            LOGGER.error("Could not create the state directory", e);
            file = null;
        }
    }

    @Override
    public void tick(LocalPlayer player, EventLog log, long tick) {
        if (tick % WRITE_EVERY_TICKS != 0) return;

        long now = System.currentTimeMillis();
        if (now - statsRequestedAt >= STATS_REQUEST_MS) {
            statsRequestedAt = now;
            requestStats();
        }
        if (now - statsBuiltAt >= STATS_REBUILD_MS) {
            statsBuiltAt = now;
            statsCache = buildStats(player);
        }
        write(snapshot(player, true));
    }

    @Override
    public void onSessionEnd(EventLog log) {
        // Leave the last snapshot behind but mark it stale, so the bot can tell "he is at 3
        // hearts" from "he stopped playing an hour ago at 3 hearts".
        LocalPlayer player = Minecraft.getInstance().player;
        if (player != null) {
            write(snapshot(player, false));
        }
    }

    /**
     * The client's statistics are a cache the server fills on request; without asking, they stay
     * at whatever they were when the world loaded. Vanilla only asks when the stats screen is
     * opened, so the death counter would never move during a session.
     */
    private void requestStats() {
        ClientPacketListener connection = Minecraft.getInstance().getConnection();
        if (connection == null) return;
        connection.send(new ServerboundClientCommandPacket(
                ServerboundClientCommandPacket.Action.REQUEST_STATS));
    }

    /** Forces the next tick to publish fresh statistics — used right after a death. */
    public void invalidateStats() {
        statsBuiltAt = 0L;
        statsRequestedAt = 0L;
    }

    // ---- the snapshot -------------------------------------------------------

    private JsonObject snapshot(LocalPlayer player, boolean active) {
        JsonObject root = new JsonObject();
        root.addProperty("updated", LocalTime.now().format(TIME));
        root.addProperty("updated_epoch_ms", System.currentTimeMillis());
        root.addProperty("active", active);
        root.addProperty("player", playerName);
        root.addProperty("world", worldName);
        root.add("vitals", vitals(player));
        root.add("effects", effects(player));
        root.add("location", location(player));
        root.add("equipment", equipment(player));
        root.add("inventory", inventory(player));
        root.add("stats", statsCache);
        return root;
    }

    private JsonObject vitals(LocalPlayer player) {
        JsonObject json = new JsonObject();
        float health = player.getHealth();
        float maxHealth = player.getMaxHealth();
        float absorption = player.getAbsorptionAmount();
        FoodData food = player.getFoodData();
        int air = player.getAirSupply();
        int maxAir = player.getMaxAirSupply();

        json.addProperty("health", round(health));
        json.addProperty("max_health", round(maxHealth));
        json.addProperty("health_state", healthState(health, maxHealth));
        if (absorption > 0f) json.addProperty("absorption", round(absorption));
        json.addProperty("hunger", food.getFoodLevel());
        json.addProperty("saturation", round(food.getSaturationLevel()));
        json.addProperty("armor", player.getArmorValue());
        json.addProperty("xp_level", player.experienceLevel);
        json.addProperty("gamemode", gamemode());
        json.addProperty("difficulty", player.level().getDifficulty().name().toLowerCase());
        if (air < maxAir) {
            json.addProperty("air", air);
            json.addProperty("max_air", maxAir);
        }
        json.addProperty("doing", activity(player));

        // One ready-made English sentence, so the bot does not have to phrase numbers itself.
        StringBuilder summary = new StringBuilder();
        summary.append(String.format("Health %.0f/%.0f (%s)",
                health, maxHealth, healthState(health, maxHealth)));
        if (absorption > 0f) summary.append(String.format(" plus %.0f absorption", absorption));
        summary.append(String.format(", hunger %d/20", food.getFoodLevel()));
        if (food.getFoodLevel() <= 6) summary.append(" (hungry)");
        summary.append(String.format(", armour %d, XP level %d",
                player.getArmorValue(), player.experienceLevel));
        summary.append('.');

        List<String> flags = new ArrayList<>();
        if (player.isOnFire()) flags.add("on fire");
        if (player.isInLava()) flags.add("in lava");
        if (air <= 0 && player.isUnderWater()) flags.add("out of air");
        if (player.isFreezing()) flags.add("freezing");
        if (!flags.isEmpty()) summary.append(' ').append(capitalise(String.join(", ", flags))).append('.');
        summary.append(' ').append(capitalise(activity(player))).append('.');

        json.addProperty("summary", summary.toString());
        return json;
    }

    /** What the player is busy doing — the half of "how are you doing" that is not a number. */
    private static String activity(LocalPlayer player) {
        if (player.isSleeping()) return "asleep in a bed";
        if (player.isFallFlying()) return "flying with an elytra";
        Entity vehicle = player.getVehicle();
        if (vehicle != null) return "riding a " + Names.readable(vehicle);
        if (player.isUsingItem()) {
            ItemStack using = player.getUseItem();
            if (!using.isEmpty()) return "using a " + Names.readable(using);
        }
        if (player.isSwimming()) return "swimming";
        if (player.isUnderWater()) return "underwater";
        if (player.isSprinting()) return "sprinting";
        if (player.isShiftKeyDown()) return "sneaking";
        return "on foot";
    }

    private static String healthState(float health, float maxHealth) {
        if (maxHealth <= 0f) return "unknown";
        float fraction = health / maxHealth;
        if (fraction >= 1f) return "full health";
        if (fraction >= 0.75f) return "lightly scratched";
        if (fraction >= 0.5f) return "hurt";
        if (fraction >= 0.25f) return "badly hurt";
        return "about to die";
    }

    private static String gamemode() {
        GameType mode = Minecraft.getInstance().gameMode == null
                ? null : Minecraft.getInstance().gameMode.getPlayerMode();
        return mode == null ? "unknown" : mode.getName();
    }

    private JsonArray effects(LocalPlayer player) {
        JsonArray array = new JsonArray();
        for (MobEffectInstance instance : player.getActiveEffects()) {
            ResourceLocation id = BuiltInRegistries.MOB_EFFECT.getKey(instance.getEffect().value());
            JsonObject effect = new JsonObject();
            effect.addProperty("name", id == null ? "unknown" : Names.readable(id));
            effect.addProperty("level", instance.getAmplifier() + 1);
            effect.addProperty("seconds_left",
                    instance.isInfiniteDuration() ? -1 : instance.getDuration() / 20);
            array.add(effect);
        }
        return array;
    }

    private JsonObject location(LocalPlayer player) {
        WorldContext context = WorldContext.of(player);
        BlockPos pos = player.blockPosition();

        JsonObject json = new JsonObject();
        json.addProperty("dimension", context.dimension());
        json.addProperty("biome", context.biome());
        json.addProperty("time_of_day", context.phase());
        json.addProperty("weather", context.weather());
        json.addProperty("x", pos.getX());
        json.addProperty("y", pos.getY());
        json.addProperty("z", pos.getZ());
        json.addProperty("summary", context.describe());
        return json;
    }

    private JsonObject equipment(LocalPlayer player) {
        JsonObject json = new JsonObject();
        json.addProperty("main_hand", describe(player.getItemBySlot(EquipmentSlot.MAINHAND)));
        json.addProperty("off_hand", describe(player.getItemBySlot(EquipmentSlot.OFFHAND)));
        json.addProperty("head", describe(player.getItemBySlot(EquipmentSlot.HEAD)));
        json.addProperty("chest", describe(player.getItemBySlot(EquipmentSlot.CHEST)));
        json.addProperty("legs", describe(player.getItemBySlot(EquipmentSlot.LEGS)));
        json.addProperty("feet", describe(player.getItemBySlot(EquipmentSlot.FEET)));
        return json;
    }

    /** "Diamond Pickaxe (312/1561 durability left, about to break)". */
    private static String describe(ItemStack stack) {
        if (stack.isEmpty()) return "nothing";
        String name = Names.readable(stack);
        if (!stack.isDamageableItem()) {
            return stack.getCount() > 1 ? stack.getCount() + "x " + name : name;
        }
        int left = stack.getMaxDamage() - stack.getDamageValue();
        String note = left <= stack.getMaxDamage() * 0.1 ? ", about to break" : "";
        return String.format("%s (%d/%d durability left%s)", name, left, stack.getMaxDamage(), note);
    }

    private JsonObject inventory(LocalPlayer player) {
        Inventory inventory = player.getInventory();
        // Before 1.21.5 the backpack and hotbar are this one public list, and armour and
        // offhand are separate lists rather than an EntityEquipment.
        NonNullList<ItemStack> slots = inventory.items;

        Map<String, Integer> counts = new LinkedHashMap<>();
        int used = 0;
        for (ItemStack stack : slots) {
            if (stack.isEmpty()) continue;
            used++;
            counts.merge(Names.readable(stack), stack.getCount(), Integer::sum);
        }

        List<Map.Entry<String, Integer>> sorted = new ArrayList<>(counts.entrySet());
        sorted.sort(Comparator.<Map.Entry<String, Integer>>comparingInt(Map.Entry::getValue).reversed());

        JsonArray items = new JsonArray();
        List<String> readable = new ArrayList<>();
        for (Map.Entry<String, Integer> entry : sorted) {
            JsonObject item = new JsonObject();
            item.addProperty("name", entry.getKey());
            item.addProperty("count", entry.getValue());
            items.add(item);
            readable.add(entry.getValue() + "x " + entry.getKey());
        }

        JsonObject json = new JsonObject();
        json.addProperty("slots_used", used);
        json.addProperty("slots_free", slots.size() - used);
        json.addProperty("holding", describe(inventory.getSelected()));
        json.add("items", items);
        json.addProperty("summary", readable.isEmpty()
                ? "Carrying nothing at all."
                : String.format("%d of %d slots used. Carrying: %s.",
                        used, slots.size(), String.join(", ", readable)));
        return json;
    }

    // ---- vanilla statistics -------------------------------------------------

    private JsonObject buildStats(LocalPlayer player) {
        StatsCounter counter = player.getStats();
        JsonObject json = new JsonObject();

        int deathCount = counter.getValue(Stats.CUSTOM, Stats.DEATHS);
        json.addProperty("deaths", deathCount);
        json.addProperty("mob_kills", counter.getValue(Stats.CUSTOM, Stats.MOB_KILLS));
        json.addProperty("player_kills", counter.getValue(Stats.CUSTOM, Stats.PLAYER_KILLS));
        // Damage statistics are stored in tenths of a health point.
        json.addProperty("damage_taken", round(counter.getValue(Stats.CUSTOM, Stats.DAMAGE_TAKEN) / 10f));
        json.addProperty("damage_dealt", round(counter.getValue(Stats.CUSTOM, Stats.DAMAGE_DEALT) / 10f));
        json.addProperty("hours_played", round(counter.getValue(Stats.CUSTOM, Stats.PLAY_TIME) / 72000f));
        json.addProperty("minutes_since_last_death",
                counter.getValue(Stats.CUSTOM, Stats.TIME_SINCE_DEATH) / 1200);
        json.addProperty("blocks_walked", counter.getValue(Stats.CUSTOM, Stats.WALK_ONE_CM) / 100);
        json.addProperty("jumps", counter.getValue(Stats.CUSTOM, Stats.JUMP));

        JsonArray killedBy = entityStats(counter, Stats.ENTITY_KILLED_BY);
        json.add("killed_by", killedBy);
        json.add("kills_by_creature", entityStats(counter, Stats.ENTITY_KILLED));
        json.add("blocks_mined", blockStats(counter));

        // Vanilla knows which creature killed you, but files every fall, lava bath and long drop
        // into the void under the same single number. That breakdown comes from our own history.
        json.add("deaths_by_cause", deaths.byCause(worldName));
        json.add("recent_deaths", deaths.recent(worldName, 8));
        json.addProperty("summary", statsSummary(deathCount, killedBy, counter));
        return json;
    }

    private String statsSummary(int deathCount, JsonArray killedBy, StatsCounter counter) {
        StringBuilder sb = new StringBuilder();
        if (deathCount == 0) {
            sb.append("Has never died in this world");
        } else {
            sb.append(String.format("Has died %d time%s in this world",
                    deathCount, deathCount == 1 ? "" : "s"));
            if (!killedBy.isEmpty()) {
                JsonObject worst = killedBy.get(0).getAsJsonObject();
                sb.append(String.format(", most often to a %s (%d)",
                        worst.get("name").getAsString(), worst.get("count").getAsInt()));
            }
            int since = counter.getValue(Stats.CUSTOM, Stats.TIME_SINCE_DEATH) / 1200;
            sb.append(String.format(", the last one %d minute%s ago",
                    since, since == 1 ? "" : "s"));
        }
        sb.append(String.format(". %d mob kills, %.1f hours played.",
                counter.getValue(Stats.CUSTOM, Stats.MOB_KILLS),
                counter.getValue(Stats.CUSTOM, Stats.PLAY_TIME) / 72000f));
        return sb.toString();
    }

    private static JsonArray entityStats(StatsCounter counter,
                                         net.minecraft.stats.StatType<EntityType<?>> type) {
        List<Map.Entry<String, Integer>> found = new ArrayList<>();
        for (EntityType<?> entityType : BuiltInRegistries.ENTITY_TYPE) {
            int value = counter.getValue(type, entityType);
            if (value > 0) {
                found.add(Map.entry(Names.readable(Names.id(entityType)), value));
            }
        }
        return topOf(found);
    }

    private static JsonArray blockStats(StatsCounter counter) {
        List<Map.Entry<String, Integer>> found = new ArrayList<>();
        for (Block block : BuiltInRegistries.BLOCK) {
            int value = counter.getValue(Stats.BLOCK_MINED, block);
            if (value > 0) {
                found.add(Map.entry(Names.readable(Names.id(block)), value));
            }
        }
        return topOf(found);
    }

    private static JsonArray topOf(List<Map.Entry<String, Integer>> found) {
        found.sort(Comparator.<Map.Entry<String, Integer>>comparingInt(Map.Entry::getValue).reversed());
        JsonArray array = new JsonArray();
        for (Map.Entry<String, Integer> entry : found.subList(0, Math.min(TOP_ENTRIES, found.size()))) {
            JsonObject item = new JsonObject();
            item.addProperty("name", entry.getKey());
            item.addProperty("count", entry.getValue());
            array.add(item);
        }
        return array;
    }

    // ---- output -------------------------------------------------------------

    /**
     * Written to a temporary file and moved into place, because the bot polls this file and
     * would otherwise read half of it while it was being rewritten.
     */
    private void write(JsonObject state) {
        if (file == null) return;
        Path temp = file.resolveSibling(FILE + ".tmp");
        try {
            Files.writeString(temp, gson.toJson(state), StandardCharsets.UTF_8);
            try {
                Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING,
                        StandardCopyOption.ATOMIC_MOVE);
            } catch (AtomicMoveNotSupportedException e) {
                Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING);
            }
            writeFailed = false;
        } catch (IOException e) {
            // Once a second: report the first failure and then stay quiet about it.
            if (!writeFailed) {
                writeFailed = true;
                LOGGER.error("Could not write the player state file", e);
            }
        }
    }

    private static double round(float value) {
        return Math.round(value * 10.0) / 10.0;
    }

    private static String capitalise(String text) {
        return text.isEmpty() ? text : Character.toUpperCase(text.charAt(0)) + text.substring(1);
    }
}
