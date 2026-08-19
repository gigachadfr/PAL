package com.gigachad.pal;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.tracker.*;
import com.gigachad.pal.util.Names;
import net.minecraft.world.damagesource.DamageSource;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.UUID;

/**
 * Client-side action logger for LLM commentary.
 * <p>
 * Everything here runs on the client thread and reads only client-visible state, which is what
 * lets the same code work in singleplayer and on a remote server — and guarantees it only ever
 * logs <em>our</em> player. The previous version routed everything through
 * {@code ServerPlayer} via {@code client.getSingleplayerServer()}, which is null on any real server,
 * so it silently logged nothing at all in multiplayer.
 */
public class PlayerActionLogger implements ClientModInitializer {
    public static final String MOD_ID = "playeractionlogger";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    private static final EventLog LOG = new EventLog();

    private static final SceneTracker SCENE = new SceneTracker();
    private static final VitalsTracker VITALS = new VitalsTracker();
    private static final DangerTracker DANGER = new DangerTracker();
    private static final LookTracker LOOK = new LookTracker();
    private static final MiningTracker MINING = new MiningTracker();
    private static final BuildTracker BUILD = new BuildTracker();
    private static final CombatTracker COMBAT = new CombatTracker();
    private static final ContainerTracker CONTAINER = new ContainerTracker();
    private static final ProgressTracker PROGRESS = new ProgressTracker();

    private static final List<Tracker> TRACKERS =
            List.of(SCENE, VITALS, DANGER, LOOK, MINING, BUILD, COMBAT, CONTAINER, PROGRESS);

    private static boolean sessionActive = false;
    private static long tickCount = 0L;

    /**
     * Host mode: in singleplayer the integrated server runs in this JVM, so the server-side
     * mixins fire and give exact damage sources and kill attribution. On a remote server they
     * never run and the client-side trackers infer instead. Both are volatile because the
     * server mixins read them from the server thread.
     */
    private static volatile boolean hostMode = false;
    private static volatile UUID localPlayerUuid = null;

    @Override
    public void onInitializeClient() {
        LOGGER.info("PlayerActionLogger starting (client-side)");

        ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> startSession(client));
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> endSession());
        ClientTickEvents.END_CLIENT_TICK.register(PlayerActionLogger::onClientTick);

        registerChatEvents();

        // Belt and braces: make sure the file is closed if the game is killed.
        Runtime.getRuntime().addShutdownHook(new Thread(LOG::close, "PAL-shutdown"));
    }

    private void registerChatEvents() {
        // The player talking is usually the player talking *to* the AI — always worth a reply.
        ClientSendMessageEvents.ALLOW_CHAT.register(message -> {
            if (sessionActive) {
                LOG.log(Level.NOTABLE, "chat_sent",
                        String.format("The player said in chat: \"%s\"", message));
            }
            return true;
        });

        ClientSendMessageEvents.ALLOW_COMMAND.register(command -> {
            if (sessionActive) {
                LOG.log(Level.INFO, "command",
                        String.format("The player ran the command /%s", command));
            }
            return true;
        });

        // Other players' messages — worthless in singleplayer, gold on a server.
        ClientReceiveMessageEvents.CHAT.register((message, signed, sender, params, timestamp) -> {
            if (!sessionActive) return;

            // The server echoes our own message straight back, which would log every line the
            // player types twice — once as chat_sent, once as chat_received.
            LocalPlayer self = Minecraft.getInstance().player;
            if (self != null && sender != null && sender.id().equals(self.getUUID())) {
                return;
            }

            String who = sender != null ? sender.name() : "someone";
            LOG.log(Level.NOTABLE, "chat_received",
                    String.format("%s said in chat: \"%s\"", who, message.getString()));
        });
    }

    private static void startSession(Minecraft client) {
        LocalPlayer player = client.player;
        if (player == null) return;

        localPlayerUuid = player.getUUID();
        hostMode = client.hasSingleplayerServer();

        LOG.start(player.getGameProfile().name(), describeWorld(client));
        LOGGER.info("Session started ({} mode)", hostMode ? "host, exact events" : "client, inferred events");
        sessionActive = true;
        tickCount = 0L;

        for (Tracker tracker : TRACKERS) {
            tracker.onSessionStart(player, LOG);
        }
    }

    private static void endSession() {
        if (!sessionActive) return;
        sessionActive = false;

        for (Tracker tracker : TRACKERS) {
            tracker.onSessionEnd(LOG);
        }
        LOG.log(Level.INFO, "session_end", "The player left the world.");
        LOG.close();
    }

    private static void onClientTick(Minecraft client) {
        if (!sessionActive) return;

        LocalPlayer player = client.player;
        if (player == null || client.level == null) return;

        tickCount++;
        for (Tracker tracker : TRACKERS) {
            try {
                tracker.tick(player, LOG, tickCount);
            } catch (Exception e) {
                // A broken tracker must never take the game down with it.
                LOGGER.error("Tracker {} failed", tracker.getClass().getSimpleName(), e);
            }
        }
    }

    private static String describeWorld(Minecraft client) {
        if (client.hasSingleplayerServer()) {
            IntegratedServer server = client.getSingleplayerServer();
            return server != null
                    ? server.getWorldData().getLevelName()
                    : "a singleplayer world";
        }
        ServerData info = client.getCurrentServer();
        return info != null ? info.ip : "a multiplayer server";
    }

    // ---- facade used by the mixins ----------------------------------------
    // Mixins stay one-liners; all the logic lives in the trackers.

    public static void onBlockBroken(BlockState state, BlockPos pos) {
        if (sessionActive) MINING.onBlockBroken(state, pos, LOG);
    }

    public static void onBlockPlaced(BlockState state, BlockPos pos) {
        if (sessionActive) BUILD.onBlockPlaced(state, pos);
    }

    public static void onAttack(Entity target) {
        if (sessionActive) COMBAT.onAttack(target);
    }

    public static void onEntityDied(Entity entity) {
        if (sessionActive) COMBAT.onEntityDied(entity, LOG);
    }

    public static void onCrafted(ItemStack result) {
        if (sessionActive) PROGRESS.onCrafted(result, LOG);
    }

    public static void onAdvancement(String title, String description) {
        if (sessionActive) PROGRESS.onAdvancement(title, description, LOG);
    }

    public static void onContainerOpen(AbstractContainerMenu handler) {
        LocalPlayer player = Minecraft.getInstance().player;
        if (sessionActive && player != null) CONTAINER.onOpen(handler, player);
    }

    public static void onContainerClose() {
        LocalPlayer player = Minecraft.getInstance().player;
        if (sessionActive && player != null) CONTAINER.onClose(player, LOG);
    }

    /** Called with the game's own death message, so the cause is always exact. */
    public static void onDeath(String deathMessage) {
        if (!sessionActive) return;
        LocalPlayer player = Minecraft.getInstance().player;
        String where = player == null ? "" : String.format(" at Y=%d", player.blockPosition().getY());
        LOG.log(Level.CRITICAL, "death", String.format("DIED: %s%s.", deathMessage, where));
    }

    public static EventLog log() {
        return LOG;
    }

    // ---- host mode: exact events from the integrated server -----------------
    // These run on the server thread. EventLog is synchronised, and the two fields they read
    // are volatile, so no further locking is needed.

    /** True when we are hosting and the server-side mixins are authoritative. */
    public static boolean hostMode() {
        return hostMode;
    }

    public static boolean isLocalPlayer(UUID uuid) {
        return sessionActive && hostMode && uuid != null && uuid.equals(localPlayerUuid);
    }

    public static void onExactDamage(DamageSource source, float amount, float health, float maxHealth) {
        if (!sessionActive || amount <= 0f) return;

        String cause = describeDamageSource(source);
        String msg = String.format("Took %.0f damage from %s. Health now %.0f/%.0f.",
                amount, cause, health, maxHealth);

        if (amount >= maxHealth * 0.25f) {
            LOG.log(Level.NOTABLE, "damage", msg);
        } else {
            LOG.logThrottled(Level.INFO, "damage", msg, 3_000L);
        }
    }

    public static void onExactKill(Entity victim) {
        if (sessionActive) COMBAT.onConfirmedKill(victim, LOG);
    }

    /** Turns a DamageSource into something worth reading aloud. */
    private static String describeDamageSource(DamageSource source) {
        Entity attacker = source.getEntity();
        if (attacker != null) {
            String name = Names.readable(attacker);
            Entity projectile = source.getDirectEntity();
            // "a Skeleton" reads better than "an arrow", but mentioning both is clearer still.
            if (projectile != null && projectile != attacker) {
                return String.format("a %s (%s)", name, Names.readable(projectile));
            }
            return "a " + name;
        }

        return switch (source.getMsgId()) {
            case "inWall" -> "suffocation";
            case "cactus" -> "a cactus";
            case "drown" -> "drowning";
            case "onFire", "inFire" -> "fire";
            case "lava" -> "lava";
            case "hotFloor" -> "magma";
            case "fall" -> "the fall";
            case "flyIntoWall" -> "flying into a wall";
            case "starve" -> "starvation";
            case "outOfWorld" -> "the void";
            case "sweetBerryBush" -> "a berry bush";
            case "freeze" -> "the cold";
            case "explosion", "explosion.player" -> "an explosion";
            case "lightningBolt" -> "a lightning bolt";
            case "magic" -> "magic";
            case "wither" -> "wither";
            default -> Names.prettify(source.getMsgId()).toLowerCase();
        };
    }
}
