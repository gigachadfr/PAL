package com.yourmod.playeractionlogger;

import net.minecraft.block.BlockState;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.math.BlockPos;

import java.util.*;

public class MiningTracker {
    private final Map<String, Integer> currentSessionBlocks = new HashMap<>();
    private final Map<String, Integer> last5SecondsBlocks = new HashMap<>();
    private long miningStartTime = 0;
    private long lastUpdateTime = 0;
    private long lastBlockBreakTime = 0;
    private boolean isMining = false;
    private static final long MINING_TIMEOUT = 2000; // 2 seconds to consider mining stopped
    private static final long UPDATE_INTERVAL = 5000; // 5 seconds for updates
    
    public void onBlockBroken(BlockState state) {
        String blockName = state.getBlock().getName().getString();
        long currentTime = System.currentTimeMillis();
        
        // Start or continue mining session
        if (!isMining) {
            miningStartTime = currentTime;
            currentSessionBlocks.clear();
            last5SecondsBlocks.clear();
            isMining = true;
            lastUpdateTime = currentTime;
        }
        
        // Track blocks
        currentSessionBlocks.put(blockName, currentSessionBlocks.getOrDefault(blockName, 0) + 1);
        last5SecondsBlocks.put(blockName, last5SecondsBlocks.getOrDefault(blockName, 0) + 1);
        lastBlockBreakTime = currentTime;
    }
    
    public boolean shouldSendUpdate() {
        if (!isMining) return false;
        
        long currentTime = System.currentTimeMillis();
        
        // Check if mining stopped
        if (currentTime - lastBlockBreakTime > MINING_TIMEOUT) {
            return true; // Final update
        }
        
        // Check if 5 seconds passed
        if (currentTime - lastUpdateTime >= UPDATE_INTERVAL) {
            return true; // Periodic update
        }
        
        return false;
    }
    
    public MiningUpdate getUpdate() {
        long currentTime = System.currentTimeMillis();
        boolean isActive = (currentTime - lastBlockBreakTime) <= MINING_TIMEOUT;
        long duration = currentTime - miningStartTime;
        
        Map<String, Integer> blocksToReport;
        if (isActive) {
            // Report last 5 seconds
            blocksToReport = new HashMap<>(last5SecondsBlocks);
            last5SecondsBlocks.clear();
            lastUpdateTime = currentTime;
        } else {
            // Report entire session
            blocksToReport = new HashMap<>(currentSessionBlocks);
            isMining = false;
        }
        
        return new MiningUpdate(blocksToReport, duration, isActive);
    }
    
    public boolean isActiveMining() {
        return isMining && (System.currentTimeMillis() - lastBlockBreakTime <= MINING_TIMEOUT);
    }
    
    public static class MiningUpdate {
        public final Map<String, Integer> blocks;
        public final long duration;
        public final boolean isActive;
        
        public MiningUpdate(Map<String, Integer> blocks, long duration, boolean isActive) {
            this.blocks = blocks;
            this.duration = duration;
            this.isActive = isActive;
        }
    }
}