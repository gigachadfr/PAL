package com.yourmod.playeractionlogger;

import net.minecraft.util.ActionResult;
import net.minecraft.util.TypedActionResult;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.fabricmc.fabric.api.event.player.*;
import net.minecraft.client.MinecraftClient;
import net.minecraft.server.network.ServerPlayerEntity;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class PlayerActionLogger implements ClientModInitializer {
    public static final String MOD_ID = "playeractionlogger";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);
    
    private static final int VITAL_STATS_INTERVAL = 100; // 5 secondes (20 ticks/sec * 5)
    
    private static PlayerTracker clientTracker;
    private static ActionAnalyzer actionAnalyzer;
    private static LogManager logManager;
    private static MinecraftClient client;
    private static int vitalStatsTickCounter = 0;
    
    @Override
    public void onInitializeClient() {
        LOGGER.info("Initializing Player Action Logger (Client Side)");
        
        client = MinecraftClient.getInstance();
        actionAnalyzer = new ActionAnalyzer();
        logManager = new LogManager();
        
        registerClientEvents();
        
        // Client tick handler optimisé
        ClientTickEvents.END_CLIENT_TICK.register(minecraft -> {
            if (minecraft.player != null && minecraft.world != null) {
                // Créer le tracker si nécessaire
                if (clientTracker == null) {
                    ServerPlayerEntity serverPlayer = getServerPlayer();
                    if (serverPlayer != null) {
                        clientTracker = new PlayerTracker(serverPlayer);
                    }
                }
                
                if (clientTracker != null) {
                    clientTracker.tick();
                    
                    // Envoyer les stats vitales toutes les 5 secondes
                    vitalStatsTickCounter++;
                    if (vitalStatsTickCounter >= VITAL_STATS_INTERVAL) {
                        sendVitalStats();
                        vitalStatsTickCounter = 0;
                    }
                }
            } else if (clientTracker != null) {
                // Cleanup quand le joueur se déconnecte
                clientTracker = null;
                vitalStatsTickCounter = 0;
            }
        });
    }
    
    private void sendVitalStats() {
        ServerPlayerEntity serverPlayer = getServerPlayer();
        if (serverPlayer != null && clientTracker != null) {
            float health = serverPlayer.getHealth();
            float maxHealth = serverPlayer.getMaxHealth();
            int hunger = serverPlayer.getHungerManager().getFoodLevel();
            int air = serverPlayer.getAir();
            int maxAir = serverPlayer.getMaxAir();
            
            String vitalStats = String.format(
                "Vital Stats - Health: %.1f/%.1f | Hunger: %d/20 | Air: %d/%d | Pos: %.1f, %.1f, %.1f",
                health, maxHealth, hunger, air, maxAir,
                serverPlayer.getX(), serverPlayer.getY(), serverPlayer.getZ()
            );
            
            logManager.logRegularEvent(serverPlayer, vitalStats);
        }
    }
    
    private void registerClientEvents() {
        // Chat messages
        ClientSendMessageEvents.ALLOW_CHAT.register((message) -> {
            ServerPlayerEntity serverPlayer = getServerPlayer();
            if (serverPlayer != null) {
                logManager.logChatMessage(serverPlayer, message);
            }
            return true;
        });
        
        // Commands
        ClientSendMessageEvents.ALLOW_COMMAND.register((command) -> {
            ServerPlayerEntity serverPlayer = getServerPlayer();
            if (serverPlayer != null) {
                logManager.logRegularEvent(serverPlayer, "Command: /" + command);
            }
            return true;
        });
        
        // Block breaking
        PlayerBlockBreakEvents.AFTER.register((world, player, pos, state, entity) -> {
            if (isClientPlayer(player) && clientTracker != null) {
                ServerPlayerEntity serverPlayer = getServerPlayer();
                if (serverPlayer != null) {
                    clientTracker.onBlockBreak(pos, state);
                    actionAnalyzer.analyzeBlockBreak(serverPlayer, pos, state, clientTracker);
                }
            }
        });
        
        // Entity interactions
        UseEntityCallback.EVENT.register((player, world, hand, entity, hitResult) -> {
            if (isClientPlayer(player) && clientTracker != null) {
                ServerPlayerEntity serverPlayer = getServerPlayer();
                if (serverPlayer != null) {
                    clientTracker.onEntityInteraction(entity);
                    actionAnalyzer.analyzeEntityInteraction(serverPlayer, entity, hand, clientTracker);
                }
            }
            return ActionResult.PASS;
        });
        
        // Item usage
        UseItemCallback.EVENT.register((player, world, hand) -> {
            if (isClientPlayer(player) && clientTracker != null) {
                clientTracker.onItemUse(player.getStackInHand(hand));
            }
            return TypedActionResult.pass(player.getStackInHand(hand));
        });
    }
    
    private static ServerPlayerEntity getServerPlayer() {
        if (client.player != null && client.getServer() != null) {
            return client.getServer().getPlayerManager().getPlayer(client.player.getUuid());
        }
        return null;
    }
    
    private static boolean isClientPlayer(net.minecraft.entity.player.PlayerEntity player) {
        return client.player != null && player.getUuid().equals(client.player.getUuid());
    }
    
    public static PlayerTracker getOrCreateTracker(ServerPlayerEntity player) {
        if (client.player != null && player != null && 
            player.getUuid().equals(client.player.getUuid())) {
            if (clientTracker == null) {
                clientTracker = new PlayerTracker(player);
            }
            return clientTracker;
        }
        return new PlayerTracker(player);
    }
    
    public static LogManager getLogManager() {
        return logManager;
    }
    
    public static ActionAnalyzer getActionAnalyzer() {
        return actionAnalyzer;
    }
}