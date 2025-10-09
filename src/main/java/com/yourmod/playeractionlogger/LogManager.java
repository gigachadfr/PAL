package com.yourmod.playeractionlogger;

import net.minecraft.server.network.ServerPlayerEntity;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonArray;

import java.io.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.text.SimpleDateFormat;
import java.util.*;

public class LogManager {
    private static final String LOG_DIR = "logs/player_actions";
    private static final String DISCOVERIES_FILE = "discoveries.json";
    private static final SimpleDateFormat TIME_FORMAT = new SimpleDateFormat("HH:mm:ss");
    private final Gson gson;
    private final Map<String, PrintWriter> playerLogWriters;
    private final Map<String, List<String>> recentLogs;
    private JsonObject discoveries;
    
    public LogManager() {
        this.gson = new GsonBuilder().setPrettyPrinting().create();
        this.playerLogWriters = new HashMap<>();
        this.recentLogs = new HashMap<>();
        this.discoveries = new JsonObject();
        
        try {
            Files.createDirectories(Paths.get(LOG_DIR));
            loadDiscoveries();
        } catch (IOException e) {
            PlayerActionLogger.LOGGER.error("Failed to create log directory", e);
        }
    }
    
    private void loadDiscoveries() {
        try {
            String filePath = LOG_DIR + "/" + DISCOVERIES_FILE;
            File file = new File(filePath);
            if (file.exists()) {
                try (FileReader reader = new FileReader(file)) {
                    discoveries = gson.fromJson(reader, JsonObject.class);
                    if (discoveries == null) {
                        discoveries = new JsonObject();
                    }
                }
            }
        } catch (IOException e) {
            PlayerActionLogger.LOGGER.error("Failed to load discoveries", e);
        }
    }
    
    private void saveDiscoveries() {
        try {
            String filePath = LOG_DIR + "/" + DISCOVERIES_FILE;
            try (FileWriter writer = new FileWriter(filePath)) {
                gson.toJson(discoveries, writer);
            }
        } catch (IOException e) {
            PlayerActionLogger.LOGGER.error("Failed to save discoveries", e);
        }
    }
    
    public void logImportantEvent(ServerPlayerEntity player, String event) {
        String playerName = player.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        String logEntry = String.format("[%s] IMPORTANT: %s", timestamp, event);
        
        writeToLog(playerName, logEntry);
        storeRecentLog(playerName, logEntry);
        PlayerActionLogger.LOGGER.info("[{}] {}", playerName, event);
    }
    
    public void logRegularEvent(ServerPlayerEntity player, String event) {
        String playerName = player.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        String logEntry = String.format("[%s] %s", timestamp, event);
        
        writeToLog(playerName, logEntry);
        storeRecentLog(playerName, logEntry);
    }
    
    public void logChatMessage(ServerPlayerEntity player, String message) {
        String playerName = player.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        String logEntry = String.format("[%s] CHAT: %s", timestamp, message);
        
        writeToLog(playerName, logEntry);
        storeRecentLog(playerName, logEntry);
        PlayerActionLogger.LOGGER.info("[{}] Chat: {}", playerName, message);
    }
    
    public void logDamageReceived(ServerPlayerEntity player, String damageSource, float amount) {
        String playerName = player.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        String logEntry = String.format("[%s] Took %.1f damage from %s", timestamp, amount, damageSource);
        
        writeToLog(playerName, logEntry);
        storeRecentLog(playerName, logEntry);
    }
    
    public void logDamageDealt(ServerPlayerEntity player, String target, float amount) {
        String playerName = player.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        String logEntry = String.format("[%s] Dealt %.1f damage to %s", timestamp, amount, target);
        
        writeToLog(playerName, logEntry);
        storeRecentLog(playerName, logEntry);
    }
    
    public void logPlayerKill(ServerPlayerEntity killer, ServerPlayerEntity victim) {
        String killerName = killer.getName().getString();
        String victimName = victim.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        String logEntry = String.format("[%s] IMPORTANT: Killed player %s", timestamp, victimName);
        
        writeToLog(killerName, logEntry);
        storeRecentLog(killerName, logEntry);
        PlayerActionLogger.LOGGER.info("[{}] Killed player {}", killerName, victimName);
    }
    
    public void logMiningUpdate(ServerPlayerEntity player, Map<String, Integer> minedBlocks, long duration, boolean isActive) {
        String playerName = player.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        
        StringBuilder sb = new StringBuilder();
        if (isActive) {
            sb.append("Mining update (").append(duration / 1000).append("s): ");
        } else {
            sb.append("Mining session ended (").append(duration / 1000).append("s): ");
        }
        
        minedBlocks.forEach((block, count) -> 
            sb.append(block).append(" x").append(count).append(", "));
        if (sb.length() > 2) sb.setLength(sb.length() - 2);
        
        String logEntry = String.format("[%s] %s", timestamp, sb.toString());
        writeToLog(playerName, logEntry);
        storeRecentLog(playerName, logEntry);
        
        if (!isActive) {
            PlayerActionLogger.LOGGER.info("[{}] {}", playerName, sb.toString());
        }
    }
    
    public void logConstructionUpdate(ServerPlayerEntity player, String type, Map<String, Integer> blocks, 
                                     int width, int height, int depth, long duration, boolean isActive) {
        String playerName = player.getName().getString();
        String timestamp = TIME_FORMAT.format(new Date());
        
        StringBuilder sb = new StringBuilder();
        
        if (isActive) {
            sb.append("Building in progress (").append(duration / 1000).append("s): ");
            blocks.forEach((block, count) -> 
                sb.append(block).append(" x").append(count).append(", "));
            if (sb.length() > 2) sb.setLength(sb.length() - 2);
        } else {
            sb.append("Construction completed: ").append(type);
            sb.append(" ").append(width).append("x").append(height).append("x").append(depth);
            sb.append(" (").append(duration / 1000).append("s) Blocks: ");
            blocks.forEach((block, count) -> 
                sb.append(block).append(" x").append(count).append(", "));
            if (sb.length() > 2) sb.setLength(sb.length() - 2);
        }
        
        String logEntry = String.format("[%s] %s", timestamp, sb.toString());
        writeToLog(playerName, logEntry);
        storeRecentLog(playerName, logEntry);
        
        if (!isActive) {
            PlayerActionLogger.LOGGER.info("[{}] {}", playerName, sb.toString());
        }
    }
    
    public void logSessionEnd(ServerPlayerEntity player, PlayerTracker tracker) {
        // Just close the writer, no summary needed
        String playerName = player.getName().getString();
        PrintWriter writer = playerLogWriters.remove(playerName);
        if (writer != null) {
            writer.close();
        }
    }
    
    public void recordDiscovery(String playerName, String type, String item) {
        JsonObject playerDiscoveries;
        if (discoveries.has(playerName)) {
            playerDiscoveries = discoveries.getAsJsonObject(playerName);
        } else {
            playerDiscoveries = new JsonObject();
            discoveries.add(playerName, playerDiscoveries);
        }
        
        JsonArray typeArray;
        if (playerDiscoveries.has(type)) {
            typeArray = playerDiscoveries.getAsJsonArray(type);
        } else {
            typeArray = new JsonArray();
            playerDiscoveries.add(type, typeArray);
        }
        
        boolean found = false;
        for (int i = 0; i < typeArray.size(); i++) {
            if (typeArray.get(i).getAsString().equals(item)) {
                found = true;
                break;
            }
        }
        
        if (!found) {
            typeArray.add(item);
            saveDiscoveries();
        }
    }
    
    public boolean hasDiscovered(String playerName, String type, String item) {
        if (!discoveries.has(playerName)) return false;
        
        JsonObject playerDiscoveries = discoveries.getAsJsonObject(playerName);
        if (!playerDiscoveries.has(type)) return false;
        
        JsonArray typeArray = playerDiscoveries.getAsJsonArray(type);
        for (int i = 0; i < typeArray.size(); i++) {
            if (typeArray.get(i).getAsString().equals(item)) {
                return true;
            }
        }
        return false;
    }
    
    private void storeRecentLog(String playerName, String logEntry) {
        List<String> recent = recentLogs.computeIfAbsent(playerName, k -> new ArrayList<>());
        recent.add(logEntry);
        if (recent.size() > 50) {
            recent.remove(0);
        }
    }
    
    private void writeToLog(String playerName, String content) {
        try {
            PrintWriter writer = getOrCreateWriter(playerName);
            writer.println(content);
            writer.flush();
        } catch (IOException e) {
            PlayerActionLogger.LOGGER.error("Failed to write log for " + playerName, e);
        }
    }
    
    private PrintWriter getOrCreateWriter(String playerName) throws IOException {
        if (!playerLogWriters.containsKey(playerName)) {
            String filename = String.format("%s/%s_latest.log", LOG_DIR, playerName);
            
            // Clear the file on new session (false = no append)
            FileWriter fileWriter = new FileWriter(filename, false);
            PrintWriter writer = new PrintWriter(new BufferedWriter(fileWriter));
            
            playerLogWriters.put(playerName, writer);
        }
        return playerLogWriters.get(playerName);
    }
    
    public List<String> getRecentLogs(String playerName) {
        return recentLogs.getOrDefault(playerName, new ArrayList<>());
    }
    
    public void cleanup() {
        playerLogWriters.values().forEach(PrintWriter::close);
        playerLogWriters.clear();
        saveDiscoveries();
    }
}