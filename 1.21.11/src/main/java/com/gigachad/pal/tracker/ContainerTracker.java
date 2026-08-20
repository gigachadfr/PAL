package com.gigachad.pal.tracker;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.InventoryMenu;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.MenuType;
import net.minecraft.world.inventory.Slot;
import net.minecraft.resources.Identifier;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * What the player took out of and put into containers.
 * <p>
 * Rather than diffing every slot on every click (the old approach: up to 90 {@code ItemStack}
 * copies per click, and it still missed shift-click edge cases), this snapshots the player's own
 * inventory when a container opens and compares it when it closes. Simpler, cheaper, and it
 * catches every path in and out.
 */
public class ContainerTracker implements Tracker {
    private static final Map<String, String> FRIENDLY_NAMES = Map.ofEntries(
            Map.entry("generic_9x1", "chest"),
            Map.entry("generic_9x2", "chest"),
            Map.entry("generic_9x3", "chest"),
            Map.entry("generic_9x4", "large chest"),
            Map.entry("generic_9x5", "large chest"),
            Map.entry("generic_9x6", "large chest"),
            Map.entry("generic_3x3", "dispenser"),
            Map.entry("crafter_3x3", "crafter"),
            Map.entry("blast_furnace", "blast furnace"),
            Map.entry("brewing_stand", "brewing stand"),
            Map.entry("cartography_table", "cartography table"),
            Map.entry("enchantment", "enchanting table"),
            Map.entry("smithing", "smithing table"),
            Map.entry("merchant", "villager trade"),
            Map.entry("shulker_box", "shulker box"),
            Map.entry("crafting", "crafting table"));

    /**
     * Blocks where items are consumed rather than deposited. Saying the player "stored" the
     * ingredients of a pickaxe is actively misleading to the model — they were used up.
     */
    private static final Set<String> WORKSTATIONS = Set.of(
            "crafting table", "crafter", "anvil", "smithing table", "grindstone",
            "stonecutter", "loom", "cartography table", "enchanting table");

    private String containerName;
    private Map<String, Integer> playerBefore;
    private Map<String, Integer> containerBefore;
    private AbstractContainerMenu handler;
    private long openedAt;

    @Override
    public void onSessionStart(LocalPlayer player, EventLog log) {
        reset();
    }

    private void reset() {
        containerName = null;
        playerBefore = null;
        containerBefore = null;
        handler = null;  // never hold a closed handler
    }

    /** Called from {@code MinecraftClientMixin} when a container screen opens. */
    public void onOpen(AbstractContainerMenu screenHandler, LocalPlayer player) {
        // The player's own inventory is not a container worth narrating.
        if (screenHandler instanceof InventoryMenu) {
            reset();
            return;
        }

        handler = screenHandler;
        containerName = describe(screenHandler);
        playerBefore = snapshotInventory(player.getInventory());
        containerBefore = snapshotContainer(screenHandler, player);
        openedAt = System.currentTimeMillis();
    }

    /** Called from {@code MinecraftClientMixin} when the container screen closes. */
    public void onClose(LocalPlayer player, EventLog log) {
        if (containerName == null || playerBefore == null) return;

        Map<String, Integer> playerAfter = snapshotInventory(player.getInventory());
        Map<String, Integer> containerAfter = snapshotContainer(handler, player);

        Map<String, Integer> gained = increases(playerBefore, playerAfter);
        Map<String, Integer> lost = increases(playerAfter, playerBefore);

        String name = containerName;
        long seconds = Math.max(1L, (System.currentTimeMillis() - openedAt) / 1000L);
        Map<String, Integer> putIn = increases(containerBefore, containerAfter);
        reset();

        if (gained.isEmpty() && lost.isEmpty()) return;

        StringBuilder sb = new StringBuilder();
        sb.append("Used a ").append(name).append(" for ").append(seconds).append("s");

        if (WORKSTATIONS.contains(name)) {
            // No persistent storage: what left the player's inventory really was consumed.
            if (!gained.isEmpty()) append(sb, " — made ", gained);
            if (!lost.isEmpty()) {
                append(sb, gained.isEmpty() ? " — used up " : ", using ", lost);
            }
        } else {
            // Storage container (chest, furnace…). Reporting it as "made X using Y" produced
            // nonsense like "made 4x Iron Ingot, using 6x Raw Gold" when the player dropped ore
            // in and picked up unrelated ingots that were already smelted. Describe the two
            // movements separately instead, using what actually entered the container.
            if (!lost.isEmpty()) {
                append(sb, " — put in ", putIn.isEmpty() ? lost : putIn);
            }
            if (!gained.isEmpty()) {
                append(sb, lost.isEmpty() ? " — took out " : ", took out ", gained);
            }
        }
        sb.append('.');

        log.log(Level.INFO, "container", sb.toString());
    }

    /** Entries whose count grew from {@code before} to {@code after}. */
    private static Map<String, Integer> increases(Map<String, Integer> before,
                                                  Map<String, Integer> after) {
        Map<String, Integer> result = new LinkedHashMap<>();
        if (before == null || after == null) return result;
        for (Map.Entry<String, Integer> e : after.entrySet()) {
            int delta = e.getValue() - before.getOrDefault(e.getKey(), 0);
            if (delta > 0) result.put(e.getKey(), delta);
        }
        return result;
    }

    /** Contents of the container's own slots, i.e. every slot that is not the player's. */
    private static Map<String, Integer> snapshotContainer(AbstractContainerMenu handler,
                                                          LocalPlayer player) {
        Map<String, Integer> counts = new HashMap<>();
        if (handler == null) return counts;

        for (Slot slot : handler.slots) {
            if (slot.container == player.getInventory()) continue;
            ItemStack stack = slot.getItem();
            if (stack.isEmpty()) continue;
            counts.merge(Names.readable(stack), stack.getCount(), Integer::sum);
        }
        return counts;
    }

    private static void append(StringBuilder sb, String prefix, Map<String, Integer> items) {
        sb.append(prefix);
        items.forEach((item, count) -> sb.append(count).append("x ").append(item).append(", "));
        sb.setLength(sb.length() - 2);
    }

    private static Map<String, Integer> snapshotInventory(Inventory inventory) {
        Map<String, Integer> counts = new HashMap<>();
        for (int i = 0; i < inventory.getContainerSize(); i++) {
            ItemStack stack = inventory.getItem(i);
            if (stack.isEmpty()) continue;
            counts.merge(Names.readable(stack), stack.getCount(), Integer::sum);
        }
        return counts;
    }

    private static String describe(AbstractContainerMenu handler) {
        MenuType<?> type;
        try {
            type = handler.getType();
        } catch (UnsupportedOperationException e) {
            return "container"; // the player's own inventory has no registered type
        }

        Identifier id = BuiltInRegistries.MENU.getKey(type);
        if (id == null) return "container";

        String path = id.getPath();
        return FRIENDLY_NAMES.getOrDefault(path, path.replace('_', ' '));
    }
}
