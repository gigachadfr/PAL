package com.yourmod.playeractionlogger.mixin;

import com.yourmod.playeractionlogger.PlayerActionLogger;
import com.yourmod.playeractionlogger.PlayerTracker;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.damage.DamageSource;
import net.minecraft.server.network.ServerPlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(LivingEntity.class)
public abstract class LivingEntityMixin {
    
    @Inject(method = "damage", at = @At("HEAD"))
    private void onDamage(DamageSource source, float amount, CallbackInfoReturnable<Boolean> cir) {
        LivingEntity entity = (LivingEntity)(Object)this;
        
        // Log damage received by players
        if (entity instanceof ServerPlayerEntity serverPlayer) {
            PlayerTracker tracker = PlayerActionLogger.getOrCreateTracker(serverPlayer);
            tracker.onDamageReceived(source, amount);
        }
        
        // Log damage dealt by players
        if (source.getAttacker() instanceof ServerPlayerEntity attacker) {
            PlayerTracker tracker = PlayerActionLogger.getOrCreateTracker(attacker);
            tracker.onDamageDealt(entity, amount);
        }
    }
}