package com.yourmod.playeractionlogger.mixin;

import com.yourmod.playeractionlogger.PlayerActionLogger;
import com.yourmod.playeractionlogger.PlayerTracker;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.recipe.RecipeEntry;
import net.minecraft.server.network.ServerPlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.List;

@Mixin(PlayerEntity.class)
public abstract class PlayerEntityMixin {
    
    @Inject(method = "onRecipeCrafted", at = @At("HEAD"))
    private void onRecipeCrafted(RecipeEntry<?> recipe, List<ItemStack> ingredients, CallbackInfo ci) {
        if ((Object)this instanceof ServerPlayerEntity serverPlayer) {
            PlayerTracker tracker = PlayerActionLogger.getOrCreateTracker(serverPlayer);
            // Get the result from the recipe
            ItemStack result = recipe.value().getResult(serverPlayer.getWorld().getRegistryManager());
            tracker.onItemCrafted(recipe.value(), result);
        }
    }
}