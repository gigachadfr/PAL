package com.gigachad.pal.mixin;

import net.minecraft.advancement.AdvancementEntry;
import net.minecraft.client.toast.AdvancementToast;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Reads the advancement behind a toast, so its registry id can be used instead of a translated title. */
@Mixin(AdvancementToast.class)
public interface AdvancementToastAccessor {

    @Accessor("advancement")
    AdvancementEntry pal$getAdvancement();
}
