package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.contents.TranslatableContents;
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

    // 1.21.1's constructor is (Component, boolean); the LocalPlayer argument arrives in 1.21.5.
    @Inject(method = "<init>(Lnet/minecraft/network/chat/Component;Z)V", at = @At("RETURN"))
    private void pal$onDeathScreen(Component message, boolean isHardcore, CallbackInfo ci) {
        // The translation key ("death.attack.lava") is the language-proof half of this: the
        // rendered string is in the client's language, so the tally would be per-language.
        String key = message != null && message.getContents() instanceof TranslatableContents contents
                ? contents.getKey()
                : null;
        PlayerActionLogger.onDeath(
                message == null ? "The player died" : message.getString(), key);
    }
}
