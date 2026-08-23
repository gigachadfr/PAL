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
 * per-slot diffing, which copied up to 90 item stacks on every single click.
 */
@Mixin(Minecraft.class)
public class MinecraftClientMixin {

    /** The screen currently open — this is the one being replaced. */
    @Shadow public Screen screen;

    @Inject(method = "setScreen", at = @At("HEAD"))
    private void pal$onSetScreen(Screen newScreen, CallbackInfo ci) {
        // `screen` is the outgoing screen, `newScreen` the incoming one. Keeping the two
        // distinct is what makes the close case detectable at all.
        if (screen instanceof AbstractContainerScreen<?> && !(newScreen instanceof AbstractContainerScreen<?>)) {
            PlayerActionLogger.onContainerClose();
        }
        if (newScreen instanceof AbstractContainerScreen<?> opened) {
            PlayerActionLogger.onContainerOpen(opened.getMenu());
        }
    }
}
