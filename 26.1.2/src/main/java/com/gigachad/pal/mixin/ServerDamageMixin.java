package com.gigachad.pal.mixin;

import com.gigachad.pal.PlayerActionLogger;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Exact damage attribution, available only when we are the host.
 * <p>
 * In singleplayer the integrated server runs in this JVM, so this mixin fires and gives the real
 * {@link DamageSource} — no guessing from proximity. On a remote server it never fires (the
 * server is another machine), and {@code VitalsTracker} falls back to inference.
 * <p>
 * Injected at RETURN so cancelled or fully-absorbed hits are not reported as damage.
 */
@Mixin(LivingEntity.class)
public abstract class ServerDamageMixin {

    @Inject(method = "hurtServer", at = @At("RETURN"))
    private void pal$onServerDamage(ServerLevel level, DamageSource source, float amount,
                                    CallbackInfoReturnable<Boolean> cir) {
        if (!Boolean.TRUE.equals(cir.getReturnValue())) return;

        LivingEntity self = (LivingEntity) (Object) this;
        if (self.level().isClientSide()) return;  // client copy: not authoritative
        if (!(self instanceof ServerPlayer serverPlayer)) return;
        if (!PlayerActionLogger.isLocalPlayer(serverPlayer.getUUID())) return;

        PlayerActionLogger.onExactDamage(
                source, amount, serverPlayer.getHealth(), serverPlayer.getMaxHealth());
    }
}
