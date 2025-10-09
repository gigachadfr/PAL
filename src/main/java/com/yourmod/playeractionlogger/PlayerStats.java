package com.yourmod.playeractionlogger;

import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.entity.attribute.EntityAttributes;
import com.google.gson.JsonObject;

import java.util.*;

public class PlayerStats {
    private float health;
    private float maxHealth;
    private int foodLevel;
    private float saturation;
    private int experienceLevel;
    private float experienceProgress;
    private double posX, posY, posZ;
    private String dimension;
    private int armor;
    private long playTime;
    private long sessionStartTime;
    
    // Combat stats
    private int totalKills;
    private Map<String, Integer> killsByType;
    private int deaths;
    private List<String> deathCauses;
    
    // Resource stats
    private long totalBlocksBroken;
    private long totalBlocksPlaced;
    private Map<String, Integer> minedOres;
    
    // Movement stats
    private double totalDistance;
    private double lastX, lastY, lastZ;
    private boolean firstUpdate = true;
    
    public PlayerStats() {
        this.killsByType = new HashMap<>();
        this.deathCauses = new ArrayList<>();
        this.minedOres = new HashMap<>();
        this.sessionStartTime = System.currentTimeMillis();
    }
    
    public void update(ServerPlayerEntity player) {
        // Basic stats
        this.health = player.getHealth();
        this.maxHealth = player.getMaxHealth();
        this.foodLevel = player.getHungerManager().getFoodLevel();
        this.saturation = player.getHungerManager().getSaturationLevel();
        this.experienceLevel = player.experienceLevel;
        this.experienceProgress = player.experienceProgress;
        
        // Position and dimension
        this.posX = player.getX();
        this.posY = player.getY();
        this.posZ = player.getZ();
        this.dimension = player.getWorld().getRegistryKey().getValue().toString();
        
        // Armor value
        this.armor = player.getArmor();
        
        // Update play time
        this.playTime = (System.currentTimeMillis() - sessionStartTime) / 1000; // in seconds
        
        // Calculate distance traveled
        if (!firstUpdate) {
            double dx = posX - lastX;
            double dy = posY - lastY;
            double dz = posZ - lastZ;
            double distance = Math.sqrt(dx*dx + dy*dy + dz*dz);
            
            // Only count if movement is reasonable (not teleportation)
            if (distance < 100) {
                totalDistance += distance;
            }
        }
        
        lastX = posX;
        lastY = posY;
        lastZ = posZ;
        firstUpdate = false;
    }
    
    public void addCombatKill(String entityType) {
        totalKills++;
        killsByType.put(entityType, killsByType.getOrDefault(entityType, 0) + 1);
    }
    
    public void recordDeath(String cause) {
        deaths++;
        deathCauses.add(cause);
        if (deathCauses.size() > 10) {
            deathCauses.remove(0);
        }
    }
    
    public void recordMinedOre(String oreType) {
        minedOres.put(oreType, minedOres.getOrDefault(oreType, 0) + 1);
    }
    
    public JsonObject toJson() {
        JsonObject json = new JsonObject();
        
        // Basic stats
        JsonObject basic = new JsonObject();
        basic.addProperty("health", String.format("%.1f/%.1f", health, maxHealth));
        basic.addProperty("hunger", foodLevel + "/20");
        basic.addProperty("saturation", String.format("%.1f", saturation));
        basic.addProperty("armor", armor);
        basic.addProperty("experience_level", experienceLevel);
        basic.addProperty("experience_progress", String.format("%.1f%%", experienceProgress * 100));
        json.add("basic", basic);
        
        // Position
        JsonObject position = new JsonObject();
        position.addProperty("x", Math.round(posX));
        position.addProperty("y", Math.round(posY));
        position.addProperty("z", Math.round(posZ));
        position.addProperty("dimension", dimension);
        json.add("position", position);
        
        // Combat stats
        JsonObject combat = new JsonObject();
        combat.addProperty("total_kills", totalKills);
        combat.addProperty("deaths", deaths);
        if (!killsByType.isEmpty()) {
            JsonObject kills = new JsonObject();
            killsByType.forEach((type, count) -> kills.addProperty(type, count));
            combat.add("kills_by_type", kills);
        }
        json.add("combat", combat);
        
        // Session stats
        JsonObject session = new JsonObject();
        session.addProperty("play_time_seconds", playTime);
        session.addProperty("play_time_formatted", formatTime(playTime));
        session.addProperty("distance_traveled", String.format("%.1f blocks", totalDistance));
        json.add("session", session);
        
        return json;
    }
    
    public String getSummary() {
        StringBuilder sb = new StringBuilder();
        sb.append(String.format("Health: %.1f/%.1f | ", health, maxHealth));
        sb.append(String.format("Hunger: %d/20 | ", foodLevel));
        sb.append(String.format("Level: %d | ", experienceLevel));
        sb.append(String.format("Pos: %.0f,%.0f,%.0f | ", posX, posY, posZ));
        sb.append(String.format("Dim: %s | ", dimension.substring(dimension.lastIndexOf(':') + 1)));
        sb.append(String.format("Kills: %d | Deaths: %d", totalKills, deaths));
        return sb.toString();
    }
    
    private String formatTime(long seconds) {
        long hours = seconds / 3600;
        long minutes = (seconds % 3600) / 60;
        long secs = seconds % 60;
        
        if (hours > 0) {
            return String.format("%dh %dm %ds", hours, minutes, secs);
        } else if (minutes > 0) {
            return String.format("%dm %ds", minutes, secs);
        } else {
            return String.format("%ds", secs);
        }
    }
    
    // Getters
    public float getHealth() { return health; }
    public float getMaxHealth() { return maxHealth; }
    public int getFoodLevel() { return foodLevel; }
    public float getSaturation() { return saturation; }
    public int getExperienceLevel() { return experienceLevel; }
    public String getDimension() { return dimension; }
    public int getArmor() { return armor; }
    public double getTotalDistance() { return totalDistance; }
    public int getTotalKills() { return totalKills; }
    public int getDeaths() { return deaths; }
}