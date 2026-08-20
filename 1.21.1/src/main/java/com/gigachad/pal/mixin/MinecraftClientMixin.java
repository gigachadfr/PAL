package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.gui.screen.ingame.HandledScreen;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Container open/close, detected from screen transitions. Far cheaper and more reliable than
 * the old per-slot diffing, which copied up to 90 item stacks on every single click.
 */
@Mixin(MinecraftClient.class)
public class MinecraftClientMixin {

    @Shadow public Screen currentScreen;

    @Inject(method = "setScreen", at = @At("HEAD"))
    private void pal$onSetScreen(Screen screen, CallbackInfo ci) {
        if (currentScreen instanceof HandledScreen<?> && !(screen instanceof HandledScreen<?>)) {
            PlayerActionLogger.onContainerClose();
        }
        if (screen instanceof HandledScreen<?> handled) {
            PlayerActionLogger.onContainerOpen(handled.getScreenHandler());
        }
    }
}
