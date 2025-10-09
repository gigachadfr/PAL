package com.yourmod.playeractionlogger;

import net.minecraft.block.BlockState;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;

import java.util.*;

public class ConstructionTracker {
    private final Map<String, Integer> currentSessionBlocks = new HashMap<>();
    private final Map<String, Integer> last30SecondsBlocks = new HashMap<>();
    private final Set<BlockPos> placedPositions = new HashSet<>();
    private long constructionStartTime = 0;
    private long lastUpdateTime = 0;
    private long lastBlockPlaceTime = 0;
    private boolean isBuilding = false;
    private static final long BUILDING_TIMEOUT = 3000; // 3 seconds to consider building stopped
    private static final long UPDATE_INTERVAL = 30000; // 30 seconds for updates
    
    private int minX, maxX, minY, maxY, minZ, maxZ;
    
    public void onBlockPlaced(BlockPos pos, BlockState state) {
        String blockName = state.getBlock().getName().getString();
        long currentTime = System.currentTimeMillis();
        
        // Start or continue building session
        if (!isBuilding) {
            constructionStartTime = currentTime;
            currentSessionBlocks.clear();
            last30SecondsBlocks.clear();
            placedPositions.clear();
            isBuilding = true;
            lastUpdateTime = currentTime;
            
            // Initialize dimensions
            minX = maxX = pos.getX();
            minY = maxY = pos.getY();
            minZ = maxZ = pos.getZ();
        }
        
        // Update dimensions
        minX = Math.min(minX, pos.getX());
        maxX = Math.max(maxX, pos.getX());
        minY = Math.min(minY, pos.getY());
        maxY = Math.max(maxY, pos.getY());
        minZ = Math.min(minZ, pos.getZ());
        maxZ = Math.max(maxZ, pos.getZ());
        
        // Track blocks
        currentSessionBlocks.put(blockName, currentSessionBlocks.getOrDefault(blockName, 0) + 1);
        last30SecondsBlocks.put(blockName, last30SecondsBlocks.getOrDefault(blockName, 0) + 1);
        placedPositions.add(pos);
        lastBlockPlaceTime = currentTime;
    }
    
    public boolean shouldSendUpdate() {
        if (!isBuilding) return false;
        
        long currentTime = System.currentTimeMillis();
        long duration = currentTime - constructionStartTime;
        
        // Check if building stopped
        if (currentTime - lastBlockPlaceTime > BUILDING_TIMEOUT) {
            return true; // Final update
        }
        
        // Check if 30 seconds passed and still active
        if (duration >= UPDATE_INTERVAL && currentTime - lastUpdateTime >= UPDATE_INTERVAL) {
            return true; // Periodic update
        }
        
        return false;
    }
    
    public ConstructionUpdate getUpdate() {
        long currentTime = System.currentTimeMillis();
        boolean isActive = (currentTime - lastBlockPlaceTime) <= BUILDING_TIMEOUT;
        long duration = currentTime - constructionStartTime;
        
        Map<String, Integer> blocksToReport;
        boolean reportDimensions = false;
        
        if (isActive && duration >= UPDATE_INTERVAL) {
            // Report last 30 seconds (periodic update)
            blocksToReport = new HashMap<>(last30SecondsBlocks);
            last30SecondsBlocks.clear();
            lastUpdateTime = currentTime;
        } else if (!isActive) {
            // Report entire session with dimensions
            blocksToReport = new HashMap<>(currentSessionBlocks);
            reportDimensions = true;
            isBuilding = false;
        } else {
            // Active but not time for update yet
            return null;
        }
        
        int width = reportDimensions ? (maxX - minX + 1) : 0;
        int height = reportDimensions ? (maxY - minY + 1) : 0;
        int depth = reportDimensions ? (maxZ - minZ + 1) : 0;
        
        String type = determineStructureType(width, height, depth);
        
        return new ConstructionUpdate(blocksToReport, width, height, depth, type, duration, isActive, reportDimensions);
    }
    
    private String determineStructureType(int width, int height, int depth) {
        if (height == 1 && width > 2 && depth > 2) {
            return "floor";
        } else if (height > width && height > depth) {
            if (width == 1 || depth == 1) {
                return "pillar";
            } else {
                return "wall";
            }
        } else if (width == 1 && depth > 2) {
            return "line";
        } else if (depth == 1 && width > 2) {
            return "line";
        } else if (Math.abs(width - depth) <= 2 && Math.abs(width - height) <= 2) {
            return "cube";
        } else {
            return "structure";
        }
    }
    
    public boolean isActiveBuilding() {
        return isBuilding && (System.currentTimeMillis() - lastBlockPlaceTime <= BUILDING_TIMEOUT);
    }
    
    public static class ConstructionUpdate {
        public final Map<String, Integer> blocks;
        public final int width, height, depth;
        public final String type;
        public final long duration;
        public final boolean isActive;
        public final boolean hasDimensions;
        
        public ConstructionUpdate(Map<String, Integer> blocks, int width, int height, int depth, 
                                String type, long duration, boolean isActive, boolean hasDimensions) {
            this.blocks = blocks;
            this.width = width;
            this.height = height;
            this.depth = depth;
            this.type = type;
            this.duration = duration;
            this.isActive = isActive;
            this.hasDimensions = hasDimensions;
        }
    }
}