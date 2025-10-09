package com.yourmod.playeractionlogger;

import net.minecraft.block.BlockState;
import net.minecraft.entity.Entity;
import net.minecraft.entity.damage.DamageSource;
import net.minecraft.entity.mob.HostileEntity;
import net.minecraft.entity.passive.AnimalEntity;
import net.minecraft.entity.passive.PassiveEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.recipe.Recipe;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.math.BlockPos;

import java.util.*;

public class PlayerTracker {
    private ServerPlayerEntity player;
    private final PlayerStats stats;
    private final VisionTracker visionTracker;
    private final MiningTracker miningTracker;
    private final ConstructionTracker constructionTracker;
    private final InventoryTracker inventoryTracker;
    private final Set<String> discoveredEntities;
    private final Set<String> discoveredOres;
    private final Map<String, Integer> actionCounts;
    private final Map<String, Integer> deathCauses;
    private final Map<String, Integer> craftedItems;
    private final Map<String, Integer> blockTypesMined;
    private final Map<String, Integer> blockTypesPlaced;
    private final Map<String, Integer> itemsUsed;
    private final List<TimedAction> recentActions;
    private long lastUpdateTime;
    private String currentHeldItem = "";
    private Entity currentlyLookingAt = null;
    private Set<Entity> recentlyBredAnimals = new HashSet<>();
    
    private static final Set<String> SKIP_FIRST_ENCOUNTER = Set.of(
        "Item", "Falling Block", "Experience Orb", "Arrow", 
        "Trident", "Snowball", "Egg", "Ender Pearl", "Firework Rocket",
        "Item Frame", "Painting", "Armor Stand", "Boat", "Minecart"
    );
    
    public PlayerTracker(ServerPlayerEntity player) {
        this.player = player;
        this.stats = new PlayerStats();
        this.visionTracker = new VisionTracker();
        this.miningTracker = new MiningTracker();
        this.constructionTracker = new ConstructionTracker();
        this.inventoryTracker = new InventoryTracker();
        this.discoveredEntities = new HashSet<>();
        this.discoveredOres = new HashSet<>();
        this.actionCounts = new HashMap<>();
        this.deathCauses = new HashMap<>();
        this.craftedItems = new HashMap<>();
        this.blockTypesMined = new HashMap<>();
        this.blockTypesPlaced = new HashMap<>();
        this.itemsUsed = new HashMap<>();
        this.recentActions = new ArrayList<>();
        this.lastUpdateTime = System.currentTimeMillis();
    }
    
    public void tick() {
        if (player == null || !player.isAlive()) return;
        
        stats.update(player);
        
        // Track held item changes
        ItemStack heldStack = player.getMainHandStack();
        String newHeldItem = heldStack.isEmpty() ? "empty" : heldStack.getName().getString();
        if (!newHeldItem.equals(currentHeldItem)) {
            currentHeldItem = newHeldItem;
            PlayerActionLogger.getLogManager().logRegularEvent(player, "Switched to holding: " + currentHeldItem);
        }
        
        // Track vision
        visionTracker.update(player);
        
        // Check what entity player is looking at
        Entity lookingAt = visionTracker.getEntityLookingAt(player);
        if (lookingAt != null && lookingAt != currentlyLookingAt) {
            currentlyLookingAt = lookingAt;
            String entityName = lookingAt.getType().getName().getString();
            PlayerActionLogger.getLogManager().logRegularEvent(player, "Looking at: " + entityName);
            
            // Check for first encounter
            if (!SKIP_FIRST_ENCOUNTER.contains(entityName)) {
                String playerName = player.getName().getString();
                LogManager logManager = PlayerActionLogger.getLogManager();
                
                if (!logManager.hasDiscovered(playerName, "entities", entityName)) {
                    logManager.recordDiscovery(playerName, "entities", entityName);
                    logManager.logImportantEvent(player, "First visual contact with " + entityName);
                }
            }
        }
        
        // Check mining updates
        if (miningTracker.shouldSendUpdate()) {
            MiningTracker.MiningUpdate update = miningTracker.getUpdate();
            if (update != null && !update.blocks.isEmpty()) {
                PlayerActionLogger.getLogManager().logMiningUpdate(player, update.blocks, update.duration, update.isActive);
            }
        }
        
        // Check construction updates
        if (constructionTracker.shouldSendUpdate()) {
            ConstructionTracker.ConstructionUpdate update = constructionTracker.getUpdate();
            if (update != null && !update.blocks.isEmpty()) {
                PlayerActionLogger.getLogManager().logConstructionUpdate(player, update.type, update.blocks, 
                    update.width, update.height, update.depth, update.duration, update.isActive);
            }
        }
        
        // Clean old tracked animals
        recentlyBredAnimals.removeIf(animal -> !animal.isAlive());
    }
    
    public void onBlockBreak(BlockPos pos, BlockState state) {
        String blockName = state.getBlock().getName().getString();
        incrementAction("blocks_broken");
        blockTypesMined.put(blockName, blockTypesMined.getOrDefault(blockName, 0) + 1);
        
        // Track mining
        miningTracker.onBlockBroken(state);
        
        // Log the individual block break
        PlayerActionLogger.getLogManager().logRegularEvent(player, "Broke " + blockName + " at " + 
            pos.getX() + "," + pos.getY() + "," + pos.getZ());
        
        // Check if it's an ore for first discovery
        if (isOre(state)) {
            incrementAction("ores_mined");
            String playerName = player.getName().getString();
            LogManager logManager = PlayerActionLogger.getLogManager();
            
            if (!logManager.hasDiscovered(playerName, "ores", blockName)) {
                logManager.recordDiscovery(playerName, "ores", blockName);
                String dimension = getDimensionName();
                logManager.logImportantEvent(player,
                    String.format("Discovered new ore: %s in %s at %d,%d,%d", 
                        blockName, dimension, pos.getX(), pos.getY(), pos.getZ()));
            }
        }
    }
    
    public void onBlockPlace(BlockPos pos, BlockState state) {
        String blockName = state.getBlock().getName().getString();
        incrementAction("blocks_placed");
        blockTypesPlaced.put(blockName, blockTypesPlaced.getOrDefault(blockName, 0) + 1);
        
        // Track construction
        constructionTracker.onBlockPlaced(pos, state);
        
        // Log the individual block place
        PlayerActionLogger.getLogManager().logRegularEvent(player, "Placed " + blockName + " at " + 
            pos.getX() + "," + pos.getY() + "," + pos.getZ());
    }
    
    public void onDamageReceived(DamageSource source, float amount) {
        String damageType = source.getName();
        
        // Get more specific damage source info
        if (source.getAttacker() != null) {
            if (source.getAttacker() instanceof PlayerEntity) {
                damageType = "player " + source.getAttacker().getName().getString();
            } else {
                damageType = source.getAttacker().getType().getName().getString();
            }
        }
        
        PlayerActionLogger.getLogManager().logDamageReceived(player, damageType, amount);
    }
    
    public void onDamageDealt(Entity target, float amount) {
        String targetName = target.getName().getString();
        if (target instanceof PlayerEntity) {
            targetName = "player " + targetName;
        }
        
        PlayerActionLogger.getLogManager().logDamageDealt(player, targetName, amount);
    }
    
    public void onEntityInteraction(Entity entity) {
        String entityType = entity.getType().getName().getString();
        
        if (entity instanceof AnimalEntity animal) {
            ItemStack heldItem = player.getMainHandStack();
            if (!heldItem.isEmpty() && animal.isBreedingItem(heldItem)) {
                String itemName = heldItem.getName().getString();
                PlayerActionLogger.getLogManager().logRegularEvent(player, 
                    String.format("Fed %s with %s", entityType, itemName));
                incrementAction("animals_fed");
                
                // Check for breeding
                if (animal.canEat() && !animal.isBaby()) {
                    for (Entity nearbyEntity : player.getWorld().getOtherEntities(animal, 
                            animal.getBoundingBox().expand(8.0), 
                            e -> e.getType() == animal.getType() && e instanceof AnimalEntity)) {
                        AnimalEntity nearbyAnimal = (AnimalEntity) nearbyEntity;
                        if (nearbyAnimal.isInLove() || recentlyBredAnimals.contains(nearbyAnimal)) {
                            PlayerActionLogger.getLogManager().logImportantEvent(player,
                                String.format("Breeding %s", entityType));
                            incrementAction("animals_bred");
                            recentlyBredAnimals.add(animal);
                            recentlyBredAnimals.add(nearbyAnimal);
                            break;
                        }
                    }
                }
            }
        }
        
        // Check for first interaction
        if (!SKIP_FIRST_ENCOUNTER.contains(entityType)) {
            String playerName = player.getName().getString();
            LogManager logManager = PlayerActionLogger.getLogManager();
            
            if (!logManager.hasDiscovered(playerName, "entities", entityType)) {
                logManager.recordDiscovery(playerName, "entities", entityType);
                logManager.logImportantEvent(player,
                    String.format("First interaction with %s", entityType));
            }
        }
    }
    
    public void onEntityKill(Entity entity) {
        String entityType = entity.getType().getName().getString();
        incrementAction("entities_killed");
        
        // Check if it's a player kill
        if (entity instanceof PlayerEntity killedPlayer) {
            PlayerActionLogger.getLogManager().logPlayerKill(player, (ServerPlayerEntity) killedPlayer);
            incrementAction("players_killed");
        } else if (entity instanceof HostileEntity) {
            incrementAction("hostiles_killed");
            stats.addCombatKill(entityType);
            PlayerActionLogger.getLogManager().logRegularEvent(player, "Killed " + entityType);
        } else if (entity instanceof PassiveEntity) {
            incrementAction("passives_killed");
            PlayerActionLogger.getLogManager().logRegularEvent(player, "Killed " + entityType);
        }
    }
    
    public void onPlayerDeath(DamageSource source) {
        String deathCause = source.getName();
        incrementAction("deaths");
        deathCauses.put(deathCause, deathCauses.getOrDefault(deathCause, 0) + 1);
        stats.recordDeath(deathCause);
        
        // More specific death cause
        if (source.getAttacker() != null) {
            if (source.getAttacker() instanceof PlayerEntity) {
                deathCause = "killed by player " + source.getAttacker().getName().getString();
            } else {
                deathCause = "killed by " + source.getAttacker().getType().getName().getString();
            }
        }
        
        String dimension = getDimensionName();
        PlayerActionLogger.getLogManager().logImportantEvent(player,
            String.format("Death #%d from %s in %s at %.0f,%.0f,%.0f",
                actionCounts.getOrDefault("deaths", 0), deathCause, dimension,
                player.getX(), player.getY(), player.getZ()));
    }
    
    public void onItemUse(ItemStack stack) {
        if (stack.isEmpty()) return;
        
        String itemName = stack.getName().getString();
        itemsUsed.put(itemName, itemsUsed.getOrDefault(itemName, 0) + 1);
        incrementAction("items_used");
        
        // Log important item usage
        if (isImportantItemUse(itemName)) {
            PlayerActionLogger.getLogManager().logRegularEvent(player,
                String.format("Used important item: %s", itemName));
        }
    }
    
    public void onItemCrafted(Recipe<?> recipe, ItemStack result) {
        String itemName = result.getName().getString();
        int count = result.getCount();
        
        craftedItems.put(itemName, craftedItems.getOrDefault(itemName, 0) + count);
        incrementAction("items_crafted");
        
        PlayerActionLogger.getLogManager().logRegularEvent(player,
            String.format("Crafted %dx %s", count, itemName));
        
        // Log important crafts
        if (isImportantItem(itemName)) {
            PlayerActionLogger.getLogManager().logImportantEvent(player,
                String.format("Crafted important item: %dx %s", count, itemName));
        }
    }
    
    public void onInventoryOpen(ScreenHandler handler) {
        inventoryTracker.onInventoryOpened(handler);
    }
    
    public void onInventoryClose() {
        String summary = inventoryTracker.getSummary();
        if (summary != null) {
            PlayerActionLogger.getLogManager().logRegularEvent(player, summary);
        }
        inventoryTracker.reset();
    }
    
    public void onSlotChange(int slotIndex, ItemStack newStack, ItemStack oldStack, boolean isPlayerSlot) {
        inventoryTracker.onSlotChange(slotIndex, newStack, oldStack, isPlayerSlot);
    }
    
    public void onChatMessage(String message) {
        PlayerActionLogger.getLogManager().logChatMessage(player, message);
    }
    
    private String getDimensionName() {
        String fullDimension = player.getWorld().getRegistryKey().getValue().toString();
        String dimName = fullDimension.substring(fullDimension.lastIndexOf(':') + 1);
        return dimName.substring(0, 1).toUpperCase() + dimName.substring(1);
    }
    
    private boolean isOre(BlockState state) {
        String name = state.getBlock().getName().getString().toLowerCase();
        return name.contains("ore") || name.contains("_ore") || 
               name.contains("ancient_debris") || name.contains("nether_gold");
    }
    
    private boolean isImportantItem(String itemName) {
        String lower = itemName.toLowerCase();
        return lower.contains("diamond") || lower.contains("netherite") || 
               lower.contains("enchant") || lower.contains("golden_apple") ||
               lower.contains("totem") || lower.contains("elytra");
    }
    
    private boolean isImportantItemUse(String itemName) {
        String lower = itemName.toLowerCase();
        return lower.contains("potion") || lower.contains("ender_pearl") || 
               lower.contains("eye_of_ender") || lower.contains("totem") ||
               lower.contains("bucket") || lower.contains("flint_and_steel") ||
               lower.contains("golden_apple");
    }
    
    private void incrementAction(String action) {
        actionCounts.put(action, actionCounts.getOrDefault(action, 0) + 1);
    }
    
    private void addRecentAction(TimedAction action) {
        recentActions.add(action);
        if (recentActions.size() > 100) {
            recentActions.remove(0);
        }
    }
    
    public void updatePlayer(ServerPlayerEntity newPlayer) {
        this.player = newPlayer;
    }
    
    // Getters
    public PlayerStats getStats() { return stats; }
    public Map<String, Integer> getActionCounts() { return new HashMap<>(actionCounts); }
    public Map<String, Integer> getDeathCauses() { return new HashMap<>(deathCauses); }
    public Map<String, Integer> getCraftedItems() { return new HashMap<>(craftedItems); }
    public Map<String, Integer> getBlockTypesMined() { return new HashMap<>(blockTypesMined); }
    public Map<String, Integer> getBlockTypesPlaced() { return new HashMap<>(blockTypesPlaced); }
    public Map<String, Integer> getItemsUsed() { return new HashMap<>(itemsUsed); }
    public List<TimedAction> getRecentActions() { return new ArrayList<>(recentActions); }
    public InventoryTracker getInventoryTracker() { return inventoryTracker; }
    
    public static class TimedAction {
        public final String action;
        public final long timestamp;
        
        public TimedAction(String action) {
            this.action = action;
            this.timestamp = System.currentTimeMillis();
        }
    }
    
    public static class StructureInfo {
        public final int width, height, depth;
        public final String type;
        
        public StructureInfo(int width, int height, int depth, String type) {
            this.width = width;
            this.height = height;
            this.depth = depth;
            this.type = type;
        }
        
        public int getVolume() {
            return width * height * depth;
        }
    }
}