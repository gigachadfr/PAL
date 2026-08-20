package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.damage.DamageSource;
import net.minecraft.entity.player.PlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Exact kill attribution while hosting. The client can only guess ("we hit it recently and then
 * it died"); here the game itself tells us who landed the killing blow.
 */
@Mixin(LivingEntity.class)
public abstract class ServerKillMixin {

    @Inject(method = "onDeath", at = @At("HEAD"))
    private void pal$onServerDeath(DamageSource source, CallbackInfo ci) {
        LivingEntity self = (LivingEntity) (Object) this;
        if (self.getWorld().isClient()) return;

        if (!(source.getAttacker() instanceof PlayerEntity killer)) return;
        if (!PlayerActionLogger.isLocalPlayer(killer.getUuid())) return;

        PlayerActionLogger.onExactKill(self);
    }
}
