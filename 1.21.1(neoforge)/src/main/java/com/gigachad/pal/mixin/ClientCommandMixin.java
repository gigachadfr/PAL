package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.client.multiplayer.ClientPacketListener;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Commands the player types, which is the one thing NeoForge has no event for.
 *
 * <p>Fabric offers {@code ClientSendMessageEvents.ALLOW_COMMAND}. NeoForge's
 * {@code ClientChatEvent} fires only for chat, and {@code RegisterClientCommandsEvent} declares
 * commands rather than observing them — so this hooks the method both of those would have come
 * from. {@code sendCommand} is where the client sends a command it has already decided is one,
 * so the string arrives without its leading slash, exactly as Fabric delivered it.
 *
 * <p>{@code @At("HEAD")} without cancellation: the command runs untouched whatever happens here.
 */
@Mixin(ClientPacketListener.class)
public abstract class ClientCommandMixin {

    @Inject(method = "sendCommand", at = @At("HEAD"))
    private void pal$onSendCommand(String command, CallbackInfo ci) {
        PlayerActionLogger.onCommandSent(command);
    }
}
