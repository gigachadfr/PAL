package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Container open/close, detected from screen transitions. Far cheaper and more reliable than
 * the old per-slot diffing, which copied up to 90 item stacks on every single click.
 */
@Mixin(Minecraft.class)
public class MinecraftClientMixin {

    @Shadow public Screen currentScreen;

    @Inject(method = "setScreen", at = @At("HEAD"))
    private void pal$onSetScreen(Screen screen, CallbackInfo ci) {
        if (currentScreen instanceof AbstractContainerScreen<?> && !(screen instanceof AbstractContainerScreen<?>)) {
            PlayerActionLogger.onContainerClose();
        }
        if (screen instanceof AbstractContainerScreen<?> handled) {
            PlayerActionLogger.onContainerOpen(handled.getMenu());
        }
    }
}
