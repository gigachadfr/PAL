package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.core.BlockPos;
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
@Mixin(MultiPlayerGameMode.class)
public class MultiPlayerGameModeMixin {

    /** State captured at HEAD, since by RETURN the block is already gone. */
    @Unique
    private BlockState pal$brokenState;

    @Inject(method = "destroyBlock", at = @At("HEAD"))
    private void pal$captureBlock(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        Minecraft client = Minecraft.getInstance();
        pal$brokenState = client.level == null ? null : client.level.getBlockState(pos);
    }

    @Inject(method = "destroyBlock", at = @At("RETURN"))
    private void pal$onBlockBroken(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
        if (cir.getReturnValue() && pal$brokenState != null && !pal$brokenState.isAir()) {
            PlayerActionLogger.onBlockBroken(pal$brokenState, pos);
        }
        pal$brokenState = null;
    }

    @Inject(method = "attack", at = @At("HEAD"))
    private void pal$onAttack(Player player, Entity target, CallbackInfo ci) {
        PlayerActionLogger.onAttack(target);
    }

    /**
     * Block placement is inferred from the item in hand rather than from the resulting world
     * state: the server is the one that actually places the block, so at this point the client
     * only knows what it asked for. The block identity is exact, and the position is exact in
     * every case except placement against a replaceable block.
     */
    @Inject(method = "useItemOn", at = @At("RETURN"))
    private void pal$onInteractBlock(LocalPlayer player, InteractionHand hand, BlockHitResult hitResult,
                                     CallbackInfoReturnable<InteractionResult> cir) {
        if (!cir.getReturnValue().consumesAction()) return;

        ItemStack stack = player.getItemInHand(hand);
        if (!(stack.getItem() instanceof BlockItem blockItem)) return;

        BlockPos target = hitResult.getBlockPos();
        Minecraft client = Minecraft.getInstance();
        if (client.level != null && !client.level.getBlockState(target).canBeReplaced()) {
            target = target.relative(hitResult.getDirection());
        }

        PlayerActionLogger.onBlockPlaced(blockItem.getBlock().defaultBlockState(), target);
    }
}
