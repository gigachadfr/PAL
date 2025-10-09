package com.yourmod.playeractionlogger;

import net.minecraft.entity.Entity;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;

import java.util.HashSet;
import java.util.Optional;
import java.util.Set;

public class VisionTracker {
    private static final double MAX_VIEW_DISTANCE = 64.0;
    private static final double FIELD_OF_VIEW = 70.0; // degrees
    private static final double PRECISE_LOOK_DISTANCE = 20.0; // for precise looking at entity
    
    private Set<String> currentlyVisible;
    private Set<String> previouslyVisible;
    private Entity currentlyLookingAt;
    
    public VisionTracker() {
        this.currentlyVisible = new HashSet<>();
        this.previouslyVisible = new HashSet<>();
        this.currentlyLookingAt = null;
    }
    
    public void update(ServerPlayerEntity player) {
        previouslyVisible = new HashSet<>(currentlyVisible);
        currentlyVisible.clear();
        
        // Get player's view direction
        Vec3d eyePos = player.getEyePos();
        Vec3d lookVec = player.getRotationVector();
        
        // Scan for entities in view
        Box searchBox = new Box(eyePos.subtract(MAX_VIEW_DISTANCE, MAX_VIEW_DISTANCE, MAX_VIEW_DISTANCE),
                                eyePos.add(MAX_VIEW_DISTANCE, MAX_VIEW_DISTANCE, MAX_VIEW_DISTANCE));
        
        player.getWorld().getOtherEntities(player, searchBox, entity -> true).forEach(entity -> {
            if (canSee(player, entity)) {
                String entityInfo = String.format("%s at %.1f blocks", 
                    entity.getType().getName().getString(), 
                    entity.distanceTo(player));
                currentlyVisible.add(entityInfo);
            }
        });
        
        // Update what entity player is directly looking at
        updateLookingAtEntity(player);
    }
    
    public Entity getEntityLookingAt(ServerPlayerEntity player) {
        return currentlyLookingAt;
    }
    
    private void updateLookingAtEntity(ServerPlayerEntity player) {
        Vec3d eyePos = player.getEyePos();
        Vec3d lookVec = player.getRotationVector();
        Vec3d endPos = eyePos.add(lookVec.multiply(PRECISE_LOOK_DISTANCE));
        
        // Raycast for entities
        Box searchBox = new Box(eyePos, endPos).expand(1.0);
        
        Entity closestEntity = null;
        double closestDistance = PRECISE_LOOK_DISTANCE;
        
        for (Entity entity : player.getWorld().getOtherEntities(player, searchBox, e -> true)) {
            Box entityBox = entity.getBoundingBox();
            Optional<Vec3d> hitPos = entityBox.raycast(eyePos, endPos);
            
            if (hitPos.isPresent()) {
                double distance = eyePos.distanceTo(hitPos.get());
                if (distance < closestDistance) {
                    // Check line of sight
                    HitResult blockHit = player.getWorld().raycast(new RaycastContext(
                        eyePos,
                        hitPos.get(),
                        RaycastContext.ShapeType.COLLIDER,
                        RaycastContext.FluidHandling.NONE,
                        player
                    ));
                    
                    if (blockHit.getType() == HitResult.Type.MISS || 
                        blockHit.getPos().distanceTo(hitPos.get()) > blockHit.getPos().distanceTo(eyePos)) {
                        closestEntity = entity;
                        closestDistance = distance;
                    }
                }
            }
        }
        
        currentlyLookingAt = closestEntity;
    }
    
    public boolean canSee(ServerPlayerEntity player, Entity target) {
        if (target == null) return false;
        
        Vec3d eyePos = player.getEyePos();
        Vec3d targetPos = target.getPos().add(0, target.getHeight() / 2, 0);
        
        // Check distance
        double distance = eyePos.distanceTo(targetPos);
        if (distance > MAX_VIEW_DISTANCE) return false;
        
        // Check field of view
        Vec3d lookVec = player.getRotationVector();
        Vec3d toTarget = targetPos.subtract(eyePos).normalize();
        double dot = lookVec.dotProduct(toTarget);
        double angle = Math.toDegrees(Math.acos(dot));
        
        if (angle > FIELD_OF_VIEW / 2) return false;
        
        // Check line of sight (raycast)
        HitResult hitResult = player.getWorld().raycast(new RaycastContext(
            eyePos,
            targetPos,
            RaycastContext.ShapeType.OUTLINE,
            RaycastContext.FluidHandling.NONE,
            player
        ));
        
        // If we hit nothing or hit the target position, we can see it
        return hitResult.getType() == HitResult.Type.MISS || 
               hitResult.getPos().distanceTo(targetPos) < 1.0;
    }
    
    public boolean canSeeBlock(ServerPlayerEntity player, BlockPos pos) {
        Vec3d eyePos = player.getEyePos();
        Vec3d blockCenter = Vec3d.ofCenter(pos);
        
        // Check distance
        double distance = eyePos.distanceTo(blockCenter);
        if (distance > MAX_VIEW_DISTANCE) return false;
        
        // Check field of view
        Vec3d lookVec = player.getRotationVector();
        Vec3d toBlock = blockCenter.subtract(eyePos).normalize();
        double dot = lookVec.dotProduct(toBlock);
        double angle = Math.toDegrees(Math.acos(dot));
        
        if (angle > FIELD_OF_VIEW / 2) return false;
        
        // Simple line of sight check
        HitResult hitResult = player.getWorld().raycast(new RaycastContext(
            eyePos,
            blockCenter,
            RaycastContext.ShapeType.OUTLINE,
            RaycastContext.FluidHandling.NONE,
            player
        ));
        
        // Cast to BlockHitResult to access getBlockPos()
        if (hitResult.getType() == HitResult.Type.BLOCK && hitResult instanceof BlockHitResult blockHitResult) {
            return blockHitResult.getBlockPos().equals(pos);
        }
        
        return false;
    }
    
    public Set<String> getNewlyVisible() {
        Set<String> newly = new HashSet<>(currentlyVisible);
        newly.removeAll(previouslyVisible);
        return newly;
    }
    
    public Set<String> getNoLongerVisible() {
        Set<String> lost = new HashSet<>(previouslyVisible);
        lost.removeAll(currentlyVisible);
        return lost;
    }
    
    public Set<String> getCurrentlyVisible() {
        return new HashSet<>(currentlyVisible);
    }
}