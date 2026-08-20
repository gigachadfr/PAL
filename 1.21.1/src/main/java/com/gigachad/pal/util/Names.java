package com.gigachad.pal.util;

import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.entity.Entity;
import net.minecraft.entity.EntityType;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;

/**
 * Names derived from registry IDs, never from the translated display name.
 * <p>
 * The old version of this mod matched on {@code block.getName().getString()}, which returns the
 * player's localised name ("Minerai de diamant" on a French client). Every underscore-based check
 * it did was therefore dead code. Everything here goes through the registry instead, so the log
 * stays stable and English whatever language the game is set to.
 */
public final class Names {
    private Names() {}

    public static Identifier id(Block block) {
        return Registries.BLOCK.getId(block);
    }

    public static Identifier id(Item item) {
        return Registries.ITEM.getId(item);
    }

    public static Identifier id(EntityType<?> type) {
        return Registries.ENTITY_TYPE.getId(type);
    }

    /** {@code minecraft:diamond_ore} -> {@code diamond_ore}. Use this for matching. */
    public static String path(Identifier identifier) {
        return identifier.getPath();
    }

    public static String blockPath(BlockState state) {
        return path(id(state.getBlock()));
    }

    public static String itemPath(ItemStack stack) {
        return path(id(stack.getItem()));
    }

    public static String entityPath(Entity entity) {
        return path(id(entity.getType()));
    }

    /** {@code diamond_ore} -> {@code Diamond Ore}. Always English, for the log message itself. */
    public static String readable(Identifier identifier) {
        return prettify(identifier.getPath());
    }

    public static String readable(BlockState state) {
        return readable(id(state.getBlock()));
    }

    public static String readable(ItemStack stack) {
        return readable(id(stack.getItem()));
    }

    public static String readable(Entity entity) {
        return readable(id(entity.getType()));
    }

    /** Turns a snake_case registry path into Title Case words. */
    public static String prettify(String snakeCase) {
        String[] words = snakeCase.split("_");
        StringBuilder sb = new StringBuilder(snakeCase.length());
        for (String word : words) {
            if (word.isEmpty()) continue;
            if (sb.length() > 0) sb.append(' ');
            sb.append(Character.toUpperCase(word.charAt(0)));
            sb.append(word, 1, word.length());
        }
        return sb.length() == 0 ? snakeCase : sb.toString();
    }
}
