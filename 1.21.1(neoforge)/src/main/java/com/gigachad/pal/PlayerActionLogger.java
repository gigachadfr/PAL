package com.gigachad.pal;

import com.gigachad.pal.log.EventLog;
import com.gigachad.pal.log.Level;
import com.gigachad.pal.state.DeathHistory;
import com.gigachad.pal.state.StateExporter;
import com.gigachad.pal.tracker.*;
import com.gigachad.pal.util.Causes;
import com.gigachad.pal.util.Names;
import com.gigachad.pal.context.WorldContext;
import net.minecraft.world.damagesource.DamageSource;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.ClientChatEvent;
import net.neoforged.neoforge.client.event.ClientChatReceivedEvent;
import net.neoforged.neoforge.client.event.ClientPlayerNetworkEvent;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.minecraft.client.multiplayer.PlayerInfo;
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
@Mod(value = PlayerActionLogger.MOD_ID, dist = Dist.CLIENT)
@EventBusSubscriber(modid = PlayerActionLogger.MOD_ID, value = Dist.CLIENT)
public class PlayerActionLogger {
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

    /** Deaths kept across sessions, so "how many times have I died to lava" has an answer. */
    private static final DeathHistory DEATHS = new DeathHistory();
    /** Publishes the live snapshot the bot reads when it needs a fact rather than an event. */
    private static final StateExporter STATE = new StateExporter(DEATHS);

    private static final List<Tracker> TRACKERS =
            List.of(SCENE, VITALS, DANGER, LOOK, MINING, BUILD, COMBAT, CONTAINER, PROGRESS, STATE);

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

    /**
     * Constructed by FML. Registration is done by annotation, so all this has to do is exist —
     * and open the log so a crash still leaves a readable file.
     */
    public PlayerActionLogger() {
        LOGGER.info("PlayerActionLogger starting (client-side)");
        Runtime.getRuntime().addShutdownHook(new Thread(LOG::close, "PAL-shutdown"));
    }

    /*
     * The loader glue, and the only part of this mod that differs from the Fabric build.
     *
     * Fabric registers callbacks by hand in `onInitializeClient`; NeoForge dispatches to
     * annotated static methods on the game bus. The events line up one for one, with one
     * exception noted at `onCommand` below — which is why this build needs no Forgified Fabric
     * API: it would have added a dependency for four callbacks that NeoForge already has.
     */

    @SubscribeEvent
    private static void onLoggingIn(ClientPlayerNetworkEvent.LoggingIn event) {
        startSession(Minecraft.getInstance());
    }

    @SubscribeEvent
    private static void onLoggingOut(ClientPlayerNetworkEvent.LoggingOut event) {
        endSession();
    }

    @SubscribeEvent
    private static void onClientTick(ClientTickEvent.Post event) {
        onClientTick(Minecraft.getInstance());
    }

    /** The player talking is usually the player talking *to* the AI — always worth a reply. */
    @SubscribeEvent
    private static void onChatSent(ClientChatEvent event) {
        if (!sessionActive) return;
        LOG.log(Level.NOTABLE, "chat_sent",
                String.format("The player said in chat: \"%s\"", event.getMessage()));
    }

    /**
     * Other players' messages — worthless in singleplayer, gold on a server.
     *
     * <p>Fabric hands the sender's profile straight to the callback; NeoForge gives a UUID, so
     * the name is looked up on the connection's player list. A player who has since left is not
     * in it, hence the fallback.
     */
    @SubscribeEvent
    private static void onChatReceived(ClientChatReceivedEvent event) {
        if (!sessionActive || event.isSystem()) return;

        Minecraft client = Minecraft.getInstance();
        LocalPlayer self = client.player;
        UUID sender = event.getSender();

        // The server echoes our own message straight back, which would log every line the
        // player types twice — once as chat_sent, once as chat_received.
        if (self != null && sender != null && sender.equals(self.getUUID())) return;

        String who = "someone";
        if (sender != null && client.getConnection() != null) {
            PlayerInfo info = client.getConnection().getPlayerInfo(sender);
            if (info != null) who = info.getProfile().getName();
        }
        LOG.log(Level.NOTABLE, "chat_received",
                String.format("%s said in chat: \"%s\"", who, event.getMessage().getString()));
    }

    /**
     * Called from {@code ClientCommandMixin}.
     *
     * <p>The one event NeoForge does not have: {@code ClientChatEvent} covers chat only, and
     * {@code RegisterClientCommandsEvent} is for declaring commands, not watching them. Fabric's
     * {@code ALLOW_COMMAND} has no counterpart, so a mixin on {@code ClientPacketListener
     * .sendCommand} stands in — the same technique the mod already uses for its other hooks.
     */
    public static void onCommandSent(String command) {
        if (!sessionActive) return;
        LOG.log(Level.INFO, "command", String.format("The player ran the command /%s", command));
    }

    private static void startSession(Minecraft client) {
        LocalPlayer player = client.player;
        if (player == null) return;

        localPlayerUuid = player.getUUID();
        hostMode = client.hasSingleplayerServer();
        DEATHS.load();

        LOG.start(player.getGameProfile().getName(), describeWorld(client));
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

    /** Public because {@code StateExporter} labels its snapshot with the same world name. */
    public static String describeWorld(Minecraft client) {
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

    /**
     * Called with the game's own death message, so the cause is always exact.
     *
     * @param translationKey the message's translation key ({@code death.attack.fall}), which is
     *                       what the history buckets on — the rendered message is in whatever
     *                       language the client runs in.
     */
    public static void onDeath(String deathMessage, String translationKey) {
        if (!sessionActive) return;
        LocalPlayer player = Minecraft.getInstance().player;
        String where = player == null ? "" : String.format(" at Y=%d", player.blockPosition().getY());
        LOG.log(Level.CRITICAL, "death", String.format("DIED: %s%s.", deathMessage, where));

        if (player != null) {
            DEATHS.record(deathMessage, translationKey, describeWorld(Minecraft.getInstance()),
                    WorldContext.of(player).dimension(), player.blockPosition().getY());
        }
        // The server only resends statistics when asked, and the death counter has just moved.
        STATE.invalidateStats();
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
            // Blowing up an End Crystal credits the damage to the player themselves; "a Player"
            // reads as if someone else did it.
            if (isLocalPlayer(attacker.getUUID())) {
                Entity own = source.getDirectEntity();
                return own != null && own != attacker
                        ? "their own " + Names.readable(own)
                        : "themselves";
            }
            String name = Names.readable(attacker);
            Entity projectile = source.getDirectEntity();
            // "a Skeleton" reads better than "an arrow", but mentioning both is clearer still.
            if (projectile != null && projectile != attacker) {
                return String.format("a %s (%s)", name, Names.readable(projectile));
            }
            return "a " + name;
        }

        return Causes.phrase(source.getMsgId());
    }
}
