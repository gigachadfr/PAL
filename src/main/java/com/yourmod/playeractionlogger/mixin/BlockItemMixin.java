package com.yourmod.playeractionlogger.mixin;

import com.yourmod.playeractionlogger.PlayerActionLogger;
import com.yourmod.playeractionlogger.PlayerTracker;
import net.minecraft.block.BlockState;
import net.minecraft.item.BlockItem;
import net.minecraft.item.ItemPlacementContext;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.math.BlockPos;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(BlockItem.class)
public class BlockItemMixin {
    
    @Inject(
        method = "place(Lnet/minecraft/item/ItemPlacementContext;)Lnet/minecraft/util/ActionResult;",
        at = @At("RETURN")
    )
    private void onBlockPlaced(ItemPlacementContext context, CallbackInfoReturnable<net.minecraft.util.ActionResult> cir) {
        // Vérifier que le placement a réussi
        if (cir.getReturnValue().isAccepted() && context.getPlayer() instanceof ServerPlayerEntity) {
            ServerPlayerEntity serverPlayer = (ServerPlayerEntity) context.getPlayer();
            BlockPos pos = context.getBlockPos();
            BlockState state = context.getWorld().getBlockState(pos);
            
            // Vérifier que le bloc n'est pas de l'air
            if (!state.isAir()) {
                PlayerTracker tracker = PlayerActionLogger.getOrCreateTracker(serverPlayer);
                tracker.onBlockPlace(pos, state);
                PlayerActionLogger.getActionAnalyzer().analyzeBlockPlace(serverPlayer, pos, tracker);
            }
        }
    }
}