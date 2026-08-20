package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.inventory.ResultSlot;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Fires when the player pulls an item out of a crafting result slot. */
@Mixin(ResultSlot.class)
public class CraftingResultSlotMixin {

    @Inject(method = "onTake", at = @At("HEAD"))
    private void pal$onCrafted(Player player, ItemStack stack, CallbackInfo ci) {
        if (player.level().isClientSide()) {
            PlayerActionLogger.onCrafted(stack);
        }
    }
}
