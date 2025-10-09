package com.yourmod.playeractionlogger.mixin;

import com.yourmod.playeractionlogger.PlayerActionLogger;
import com.yourmod.playeractionlogger.PlayerTracker;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.server.network.ServerPlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.HashMap;
import java.util.Map;

@Mixin(ScreenHandler.class)
public abstract class ScreenHandlerMixin {
    
    @Shadow public abstract Slot getSlot(int index);
    @Shadow public abstract ItemStack getCursorStack();
    
    private boolean inventoryOpened = false;
    private Map<Integer, ItemStack> slotStatesBefore = new HashMap<>();
    
    @Inject(method = "onSlotClick", at = @At("HEAD"))
    private void beforeSlotClick(int slotIndex, int button, SlotActionType actionType, PlayerEntity player, CallbackInfo ci) {
        if (player instanceof ServerPlayerEntity serverPlayer) {
            if (!inventoryOpened) {
                PlayerTracker tracker = PlayerActionLogger.getOrCreateTracker(serverPlayer);
                tracker.onInventoryOpen((ScreenHandler)(Object)this);
                inventoryOpened = true;
            }
            
            // Capturer l'état de tous les slots avant l'action
            slotStatesBefore.clear();
            ScreenHandler handler = (ScreenHandler)(Object)this;
            for (int i = 0; i < handler.slots.size(); i++) {
                Slot slot = handler.getSlot(i);
                // Créer une copie du stack pour éviter les modifications
                slotStatesBefore.put(i, slot.getStack().copy());
            }
        }
    }
    
    @Inject(method = "onSlotClick", at = @At("TAIL"))
    private void onSlotClick(int slotIndex, int button, SlotActionType actionType, PlayerEntity player, CallbackInfo ci) {
        if (player instanceof ServerPlayerEntity serverPlayer) {
            PlayerTracker tracker = PlayerActionLogger.getOrCreateTracker(serverPlayer);
            ScreenHandler handler = (ScreenHandler)(Object)this;
            
            // Comparer l'état avant et après pour tous les slots qui ont changé
            for (int i = 0; i < handler.slots.size(); i++) {
                Slot slot = handler.getSlot(i);
                ItemStack oldStack = slotStatesBefore.getOrDefault(i, ItemStack.EMPTY);
                ItemStack newStack = slot.getStack();
                
                // Vérifier s'il y a eu un changement réel
                if (!ItemStack.areEqual(oldStack, newStack)) {
                    boolean isPlayerSlot = slot.inventory == player.getInventory();
                    tracker.onSlotChange(i, newStack.copy(), oldStack, isPlayerSlot);
                }
            }
            
            slotStatesBefore.clear();
        }
    }
    
    @Inject(method = "onClosed", at = @At("HEAD"))
    private void onClosed(PlayerEntity player, CallbackInfo ci) {
        if (player instanceof ServerPlayerEntity serverPlayer) {
            PlayerTracker tracker = PlayerActionLogger.getOrCreateTracker(serverPlayer);
            tracker.onInventoryClose();
            inventoryOpened = false;
            slotStatesBefore.clear();
        }
    }
}