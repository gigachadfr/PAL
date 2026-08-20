package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.damage.DamageSource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Catches entity deaths on the client. {@code onDeath} runs client-side too, when the death
 * status packet arrives, which is how kills get noticed without any server-side hook.
 * Attribution to the player happens in {@code CombatTracker}.
 */
@Mixin(LivingEntity.class)
public abstract class LivingEntityMixin {

    @Inject(method = "onDeath", at = @At("HEAD"))
    private void pal$onDeath(DamageSource source, CallbackInfo ci) {
        LivingEntity self = (LivingEntity) (Object) this;
        if (self.getWorld().isClient()) {
            PlayerActionLogger.onEntityDied(self);
        }
    }
}
