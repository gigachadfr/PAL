package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import com.gigachad.pal.util.Names;
import net.minecraft.advancement.AdvancementEntry;
import net.minecraft.client.toast.AdvancementToast;
import net.minecraft.client.toast.Toast;
import net.minecraft.client.toast.ToastManager;
import net.minecraft.util.Identifier;
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
@Mixin(ToastManager.class)
public class ToastManagerMixin {

    @Inject(method = "add", at = @At("HEAD"))
    private void pal$onToast(Toast toast, CallbackInfo ci) {
        if (!(toast instanceof AdvancementToast advancementToast)) return;

        AdvancementEntry entry = ((AdvancementToastAccessor) advancementToast).pal$getAdvancement();
        if (entry == null) return;

        Identifier id = entry.id();
        String path = id.getPath();
        if (path.startsWith("recipes/")) return;

        // "story/mine_diamond" -> "Mine Diamond"
        int slash = path.lastIndexOf('/');
        String name = Names.prettify(slash >= 0 ? path.substring(slash + 1) : path);

        PlayerActionLogger.onAdvancement(name, null);
    }
}
