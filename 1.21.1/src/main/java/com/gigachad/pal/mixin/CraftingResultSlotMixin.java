package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.slot.CraftingResultSlot;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Fires when the player pulls an item out of a crafting result slot. */
@Mixin(CraftingResultSlot.class)
public class CraftingResultSlotMixin {

    @Inject(method = "onTakeItem", at = @At("HEAD"))
    private void pal$onCrafted(PlayerEntity player, ItemStack stack, CallbackInfo ci) {
        if (player.getWorld().isClient()) {
            PlayerActionLogger.onCrafted(stack);
        }
    }
}
