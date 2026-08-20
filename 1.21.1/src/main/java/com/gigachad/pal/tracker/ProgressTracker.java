package com.gigachad.pal.tracker;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.util.Names;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.item.ItemStack;

import java.util.HashSet;
import java.util.Set;

/**
 * Milestones: advancements, and crafting something that actually matters. These were either
 * missing entirely or silently broken before — the old {@code isImportantItem} matched against
 * translated names with underscores in them, so it never fired once.
 */
public class ProgressTracker implements Tracker {
    private static final Set<String> MILESTONE_ITEMS = Set.of(
            "diamond_pickaxe", "diamond_sword", "diamond_chestplate", "diamond_helmet",
            "diamond_leggings", "diamond_boots",
            "netherite_pickaxe", "netherite_sword", "netherite_chestplate", "netherite_helmet",
            "netherite_leggings", "netherite_boots", "netherite_ingot",
            "enchanting_table", "anvil", "beacon", "elytra", "shield",
            "golden_apple", "enchanted_golden_apple", "totem_of_undying",
            "ender_eye", "flint_and_steel", "bucket", "brewing_stand", "conduit");

    private final Set<String> craftedMilestones = new HashSet<>();

    @Override
    public void onSessionStart(ClientPlayerEntity player, EventLog log) {
        craftedMilestones.clear();
    }

    /** Called from {@code CraftingResultSlotMixin}. */
    public void onCrafted(ItemStack result, EventLog log) {
        if (result.isEmpty()) return;

        String path = Names.itemPath(result);
        if (!MILESTONE_ITEMS.contains(path)) return;

        // Only the first craft of a given milestone is interesting.
        if (!craftedMilestones.add(path)) return;

        log.log(Level.NOTABLE, "craft",
                String.format("Crafted their first %s.", Names.readable(result)));
    }

    /** Called from {@code ToastManagerMixin} when the game shows an advancement toast. */
    public void onAdvancement(String title, String description, EventLog log) {
        String msg = (description == null || description.isBlank())
                ? String.format("Earned the advancement \"%s\".", title)
                : String.format("Earned the advancement \"%s\" (%s).", title, description);
        log.log(Level.NOTABLE, "advancement", msg);
    }
}
