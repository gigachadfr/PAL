package com.yourmod.playeractionlogger;

import net.minecraft.inventory.Inventory;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.*;

import java.util.*;

public class InventoryTracker {
    private Map<String, Integer> itemsMovedIn = new HashMap<>();
    private Map<String, Integer> itemsMovedOut = new HashMap<>();
    private String containerType = "";
    private long openTime = 0;
    private boolean hasInteracted = false;
    private boolean isPlayerInventoryOnly = false;
    
    public void onInventoryOpened(ScreenHandler handler) {
        reset();
        openTime = System.currentTimeMillis();
        
        // Get actual container name from the handler
        containerType = getContainerTypeName(handler);
        
        // Check if this is just the player's inventory (no external container)
        isPlayerInventoryOnly = containerType.equals("Player Inventory") || 
                                containerType.equals("Crafting");
    }
    
    private String getContainerTypeName(ScreenHandler handler) {
        // Utilisation des classes exactes pour Minecraft 1.20.4
        if (handler instanceof GenericContainerScreenHandler) {
            int rows = ((GenericContainerScreenHandler) handler).getRows();
            if (rows <= 3) {
                return "Chest";
            } else {
                return "Large Chest";
            }
        } else if (handler instanceof Generic3x3ContainerScreenHandler) {
            return "Dispenser/Dropper";
        } else if (handler instanceof FurnaceScreenHandler) {
            return "Furnace";
        } else if (handler instanceof BlastFurnaceScreenHandler) {
            return "Blast Furnace";
        } else if (handler instanceof SmokerScreenHandler) {
            return "Smoker";
        } else if (handler instanceof CraftingScreenHandler) {
            return "Crafting Table";
        } else if (handler instanceof EnchantmentScreenHandler) {
            return "Enchanting Table";
        } else if (handler instanceof BrewingStandScreenHandler) {
            return "Brewing Stand";
        } else if (handler instanceof AnvilScreenHandler) {
            return "Anvil";
        } else if (handler instanceof BeaconScreenHandler) {
            return "Beacon";
        } else if (handler instanceof HopperScreenHandler) {
            return "Hopper";
        } else if (handler instanceof ShulkerBoxScreenHandler) {
            return "Shulker Box";
        } else if (handler instanceof CartographyTableScreenHandler) {
            return "Cartography Table";
        } else if (handler instanceof GrindstoneScreenHandler) {
            return "Grindstone";
        } else if (handler instanceof LecternScreenHandler) {
            return "Lectern";
        } else if (handler instanceof LoomScreenHandler) {
            return "Loom";
        } else if (handler instanceof StonecutterScreenHandler) {
            return "Stonecutter";
        } else if (handler instanceof SmithingScreenHandler) {
            return "Smithing Table";
        } else if (handler instanceof MerchantScreenHandler) {
            return "Villager Trade";
        } else if (handler instanceof PlayerScreenHandler) {
            return "Player Inventory";
        } else {
            // Fallback - utilise le nom de la classe
            String className = handler.getClass().getSimpleName();
            return className.replace("ScreenHandler", "").replaceAll("([A-Z])", " $1").trim();
        }
    }
    
    public void onSlotChange(int slotIndex, ItemStack newStack, ItemStack oldStack, boolean isPlayerSlot) {
        // Skip tracking for player-only inventory movements
        if (isPlayerInventoryOnly) {
            return;
        }
        
        if (newStack.isEmpty() && oldStack.isEmpty()) return;
        
        hasInteracted = true;
        
        if (!oldStack.isEmpty() && newStack.isEmpty()) {
            // Item removed
            String itemName = oldStack.getName().getString();
            int count = oldStack.getCount();
            
            if (isPlayerSlot) {
                // Item moved from player to container
                itemsMovedIn.put(itemName, itemsMovedIn.getOrDefault(itemName, 0) + count);
            } else {
                // Item taken from container
                itemsMovedOut.put(itemName, itemsMovedOut.getOrDefault(itemName, 0) + count);
            }
        } else if (oldStack.isEmpty() && !newStack.isEmpty()) {
            // Item added
            String itemName = newStack.getName().getString();
            int count = newStack.getCount();
            
            if (isPlayerSlot) {
                // Item moved from container to player
                itemsMovedOut.put(itemName, itemsMovedOut.getOrDefault(itemName, 0) + count);
            } else {
                // Item placed in container
                itemsMovedIn.put(itemName, itemsMovedIn.getOrDefault(itemName, 0) + count);
            }
        } else if (!oldStack.isEmpty() && !newStack.isEmpty()) {
            // Stack size changed
            String oldName = oldStack.getName().getString();
            String newName = newStack.getName().getString();
            
            if (oldName.equals(newName)) {
                int diff = newStack.getCount() - oldStack.getCount();
                if (diff != 0) {
                    Map<String, Integer> targetMap = diff > 0 ? 
                        (isPlayerSlot ? itemsMovedOut : itemsMovedIn) : 
                        (isPlayerSlot ? itemsMovedIn : itemsMovedOut);
                    targetMap.put(newName, targetMap.getOrDefault(newName, 0) + Math.abs(diff));
                }
            } else {
                // Different items - treat as remove and add
                if (isPlayerSlot) {
                    itemsMovedIn.put(oldName, itemsMovedIn.getOrDefault(oldName, 0) + oldStack.getCount());
                    itemsMovedOut.put(newName, itemsMovedOut.getOrDefault(newName, 0) + newStack.getCount());
                } else {
                    itemsMovedOut.put(oldName, itemsMovedOut.getOrDefault(oldName, 0) + oldStack.getCount());
                    itemsMovedIn.put(newName, itemsMovedIn.getOrDefault(newName, 0) + newStack.getCount());
                }
            }
        }
    }
    
    public String getSummary() {
        if (!hasInteracted || isPlayerInventoryOnly || containerType.isEmpty()) return null;
        
        long duration = (System.currentTimeMillis() - openTime) / 1000;
        StringBuilder sb = new StringBuilder();
        sb.append("Interacted with ").append(containerType);
        sb.append(" for ").append(duration).append("s");
        
        if (!itemsMovedIn.isEmpty()) {
            sb.append(" | Deposited: ");
            itemsMovedIn.forEach((item, count) -> 
                sb.append(item).append(" x").append(count).append(", "));
            sb.setLength(sb.length() - 2); // Remove last comma
        }
        
        if (!itemsMovedOut.isEmpty()) {
            sb.append(" | Took: ");
            itemsMovedOut.forEach((item, count) -> 
                sb.append(item).append(" x").append(count).append(", "));
            sb.setLength(sb.length() - 2); // Remove last comma
        }
        
        return sb.toString();
    }
    
    public void reset() {
        itemsMovedIn.clear();
        itemsMovedOut.clear();
        containerType = "";
        openTime = System.currentTimeMillis();
        hasInteracted = false;
        isPlayerInventoryOnly = false;
    }
    
    public boolean hasInteracted() {
        return hasInteracted;
    }
    
    public String getContainerType() {
        return containerType;
    }
}