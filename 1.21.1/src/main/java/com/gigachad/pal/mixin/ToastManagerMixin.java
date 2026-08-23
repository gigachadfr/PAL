package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import com.gigachad.pal.util.Names;
import net.minecraft.advancements.AdvancementHolder;
import net.minecraft.client.gui.components.toasts.AdvancementToast;
import net.minecraft.client.gui.components.toasts.Toast;
import net.minecraft.client.gui.components.toasts.ToastComponent;
import net.minecraft.resources.ResourceLocation;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Advancements, caught as the game pops the toast — works identically in singleplayer and on a
 * server, with no reliance on chat announcements.
 * <p>
 * The advancement's registry id is used rather than its display title, so the log stays English
 * on a non-English client.
 */
@Mixin(ToastComponent.class)
public class ToastManagerMixin {

    @Inject(method = "addToast", at = @At("HEAD"))
    private void pal$onToast(Toast toast, CallbackInfo ci) {
        if (!(toast instanceof AdvancementToast advancementToast)) return;

        AdvancementHolder entry = ((AdvancementToastAccessor) advancementToast).pal$getAdvancement();
        if (entry == null) return;

        ResourceLocation id = entry.id();
        String path = id.getPath();
        if (path.startsWith("recipes/")) return;

        // "story/mine_diamond" -> "Mine Diamond"
        int slash = path.lastIndexOf('/');
        String name = Names.prettify(slash >= 0 ? path.substring(slash + 1) : path);

        PlayerActionLogger.onAdvancement(name, null);
    }
}
