package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.network.ClientPlayerInteractionManager;
import net.minecraft.entity.Entity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.BlockItem;
import net.minecraft.item.ItemStack;
import net.minecraft.util.ActionResult;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * The single entry point for the player's own world interactions. Working from the client
 * interaction manager (rather than server events) is what makes this work on remote servers.
 */
@Mixin(ClientPlayerInteractionManager.class)
public class ClientPlayerInteractionManagerMixin {

    /** State captured at HEAD, since by RETURN the block is already gone. */
    @Unique
    private BlockState pal$brokenState;

    @Inject(method = "breakBlock", at = @At("HEAD"))
    private void pal$captureBlock(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        MinecraftClient client = MinecraftClient.getInstance();
        pal$brokenState = client.world == null ? null : client.world.getBlockState(pos);
    }

    @Inject(method = "breakBlock", at = @At("RETURN"))
    private void pal$onBlockBroken(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        if (cir.getReturnValue() && pal$brokenState != null && !pal$brokenState.isAir()) {
            PlayerActionLogger.onBlockBroken(pal$brokenState, pos);
        }
        pal$brokenState = null;
    }

    @Inject(method = "attackEntity", at = @At("HEAD"))
    private void pal$onAttack(PlayerEntity player, Entity target, CallbackInfo ci) {
        PlayerActionLogger.onAttack(target);
    }

    /**
     * Block placement is inferred from the item in hand rather than from the resulting world
     * state: the server is the one that actually places the block, so at this point the client
     * only knows what it asked for. The block identity is exact, and the position is exact in
     * every case except placement against a replaceable block.
     */
    @Inject(method = "interactBlock", at = @At("RETURN"))
    private void pal$onInteractBlock(ClientPlayerEntity player, Hand hand, BlockHitResult hitResult,
                                     CallbackInfoReturnable<ActionResult> cir) {
        if (!cir.getReturnValue().isAccepted()) return;

        ItemStack stack = player.getStackInHand(hand);
        if (!(stack.getItem() instanceof BlockItem blockItem)) return;

        BlockPos target = hitResult.getBlockPos();
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.world != null && !client.world.getBlockState(target).isReplaceable()) {
            target = target.offset(hitResult.getSide());
        }

        PlayerActionLogger.onBlockPlaced(blockItem.getBlock().getDefaultState(), target);
    }
}
