package com.yourmod.playeractionlogger;

import net.minecraft.block.BlockState;
import net.minecraft.entity.Entity;
import net.minecraft.entity.passive.AnimalEntity;
import net.minecraft.entity.passive.SheepEntity;
import net.minecraft.entity.passive.CowEntity;
import net.minecraft.entity.passive.PigEntity;
import net.minecraft.entity.passive.ChickenEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.Hand;
import net.minecraft.util.math.BlockPos;

import java.util.*;

public class ActionAnalyzer {
    private final Map<String, ActionPattern> playerPatterns;
    
    public ActionAnalyzer() {
        this.playerPatterns = new HashMap<>();
    }
    
    public void analyzeBlockBreak(ServerPlayerEntity player, BlockPos pos, BlockState state, PlayerTracker tracker) {
        String playerName = player.getName().getString();
        ActionPattern pattern = getOrCreatePattern(playerName);
        
        // Check if mining
        if (pos.getY() < 40) {
            pattern.incrementMiningActivity();
            
            // Check for strip mining pattern
            if (pattern.isStripMining(pos)) {
                PlayerActionLogger.getLogManager().logRegularEvent(player,
                    "Strip mining detected at Y=" + pos.getY());
            }
        }
        
        // Check for ore discovery
        String blockName = state.getBlock().getName().getString();
        if (isValuableOre(blockName)) {
            PlayerActionLogger.getLogManager().logImportantEvent(player,
                String.format("Found valuable ore: %s at %d,%d,%d", 
                    blockName, pos.getX(), pos.getY(), pos.getZ()));
        }
    }
    
    public void analyzeBlockPlace(ServerPlayerEntity player, BlockPos pos, PlayerTracker tracker) {
        String playerName = player.getName().getString();
        ActionPattern pattern = getOrCreatePattern(playerName);
        
        pattern.addPlacedBlock(pos);
        
        // Check for building patterns
        if (pattern.isBuildingPattern()) {
            if (!pattern.wasRecentlyBuilding()) {
                PlayerActionLogger.getLogManager().logRegularEvent(player,
                    "Started building activity");
            }
            pattern.setRecentlyBuilding(true);
        } else {
            pattern.setRecentlyBuilding(false);
        }
    }
    
    public void analyzeEntityInteraction(ServerPlayerEntity player, Entity entity, Hand hand, PlayerTracker tracker) {
        ItemStack heldItem = player.getStackInHand(hand);
        
        // Check for feeding animals
        if (entity instanceof AnimalEntity animal) {
            if (isAnimalFood(animal, heldItem)) {
                String animalType = entity.getType().getName().getString();
                String food = heldItem.getName().getString();
                
                PlayerActionLogger.getLogManager().logImportantEvent(player,
                    String.format("Feeding %s with %s", animalType, food));
                
                // Check if breeding
                if (animal.isBreedingItem(heldItem)) {
                    tracker.getActionCounts().put("animals_bred", 
                        tracker.getActionCounts().getOrDefault("animals_bred", 0) + 1);
                }
            }
        }
    }
    
    private boolean isAnimalFood(AnimalEntity animal, ItemStack item) {
        if (item.isEmpty()) return false;
        
        if (animal instanceof CowEntity || animal instanceof SheepEntity) {
            return item.isOf(Items.WHEAT);
        } else if (animal instanceof PigEntity) {
            return item.isOf(Items.CARROT) || item.isOf(Items.POTATO) || item.isOf(Items.BEETROOT);
        } else if (animal instanceof ChickenEntity) {
            return item.isOf(Items.WHEAT_SEEDS) || item.isOf(Items.PUMPKIN_SEEDS) || 
                   item.isOf(Items.MELON_SEEDS) || item.isOf(Items.BEETROOT_SEEDS);
        }
        
        return animal.isBreedingItem(item);
    }
    
    private boolean isValuableOre(String blockName) {
        String lower = blockName.toLowerCase();
        return lower.contains("diamond") || lower.contains("emerald") || 
               lower.contains("ancient_debris") || lower.contains("netherite");
    }
    
    private ActionPattern getOrCreatePattern(String playerName) {
        return playerPatterns.computeIfAbsent(playerName, k -> new ActionPattern());
    }
    
    // Inner class for tracking action patterns
    private static class ActionPattern {
        private final List<BlockPos> recentMining = new ArrayList<>();
        private final List<BlockPos> recentBuilding = new ArrayList<>();
        private int miningActivityLevel = 0;
        private boolean recentlyBuilding = false;
        private long lastMiningTime = 0;
        private long lastBuildingTime = 0;
        
        public void incrementMiningActivity() {
            miningActivityLevel++;
            lastMiningTime = System.currentTimeMillis();
        }
        
        public void addPlacedBlock(BlockPos pos) {
            recentBuilding.add(pos);
            if (recentBuilding.size() > 100) {
                recentBuilding.remove(0);
            }
            lastBuildingTime = System.currentTimeMillis();
        }
        
        public boolean isStripMining(BlockPos pos) {
            recentMining.add(pos);
            if (recentMining.size() > 20) {
                recentMining.remove(0);
            }
            
            // Check if mining in a straight line
            if (recentMining.size() >= 10) {
                return checkLinearPattern(recentMining);
            }
            return false;
        }
        
        public boolean isBuildingPattern() {
            // Check if placing blocks frequently
            if (recentBuilding.size() < 5) return false;
            
            long currentTime = System.currentTimeMillis();
            return (currentTime - lastBuildingTime) < 5000; // Within 5 seconds
        }
        
        private boolean checkLinearPattern(List<BlockPos> positions) {
            if (positions.size() < 3) return false;
            
            // Check if positions form a roughly linear pattern
            BlockPos first = positions.get(0);
            BlockPos last = positions.get(positions.size() - 1);
            
            int dx = Math.abs(last.getX() - first.getX());
            int dz = Math.abs(last.getZ() - first.getZ());
            int dy = Math.abs(last.getY() - first.getY());
            
            // Horizontal line check (strip mining is usually horizontal)
            return dy <= 2 && (dx > 5 || dz > 5);
        }
        
        public boolean wasRecentlyBuilding() {
            return recentlyBuilding;
        }
        
        public void setRecentlyBuilding(boolean building) {
            this.recentlyBuilding = building;
        }
    }
}