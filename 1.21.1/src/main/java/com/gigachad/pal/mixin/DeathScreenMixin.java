package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.client.gui.screen.DeathScreen;
import net.minecraft.text.Text;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Death, taken straight from the game's own death message ("Steve was blown up by a Creeper").
 * This is exact in singleplayer and multiplayer alike, which no client-side inference could be —
 * and death was not logged at all before, despite being the single best thing to comment on.
 */
@Mixin(DeathScreen.class)
public class DeathScreenMixin {

    @Inject(method = "<init>(Lnet/minecraft/text/Text;Z)V", at = @At("RETURN"))
    private void pal$onDeathScreen(Text message, boolean isHardcore, CallbackInfo ci) {
        PlayerActionLogger.onDeath(message == null ? "The player died" : message.getString());
    }
}
