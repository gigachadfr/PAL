package com.gigachad.pal.context;

import com.gigachad.pal.util.Names;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.level.Level;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.core.BlockPos;

/**
 * Immutable snapshot of "where and when" the player is — biome, time, weather, dimension,
 * depth. None of this existed in the old log, which is why the AI could only ever comment on
 * bare actions with no sense of place.
 */
public record WorldContext(
        String dimension,
        String phase,
        String weather,
        String biome,
        String altitude,
        int y,
        boolean skyVisible
) {
    public static WorldContext of(LocalPlayer player) {
        Level world = player.level();
        BlockPos pos = player.blockPosition();
        boolean sky = world.canSeeSky(pos);
        int y = pos.getY();

        return new WorldContext(
                describeDimension(world.dimension()),
                describePhase(world.getDayTime()),
                describeWeather(world),
                describeBiome(world, pos),
                describeAltitude(y, sky),
                y,
                sky);
    }

    private static String describeDimension(ResourceKey<net.minecraft.world.level.Level> key) {
        String path = key.location().getPath();
        return switch (path) {
            case "overworld" -> "the Overworld";
            case "the_nether" -> "the Nether";
            case "the_end" -> "the End";
            default -> Names.prettify(path);
        };
    }

    /** Minecraft day is 24000 ticks: 0 sunrise, 6000 noon, 12000 sunset, 18000 midnight. */
    private static String describePhase(long timeOfDay) {
        long t = Math.floorMod(timeOfDay, 24000L);
        if (t < 1000L || t >= 23000L) return "dawn";
        if (t < 11000L) return "day";
        if (t < 13000L) return "dusk";
        return "night";
    }

    private static String describeWeather(Level world) {
        if (world.isThundering()) return "thunderstorm";
        if (world.isRaining()) return "raining";
        return "clear";
    }

    private static String describeBiome(Level world, BlockPos pos) {
        ResourceLocation id = world.getBiome(pos).unwrapKey().map(ResourceKey::location).orElse(null);
        return id == null ? "unknown" : Names.readable(id);
    }

    private static String describeAltitude(int y, boolean skyVisible) {
        if (skyVisible) {
            if (y > 140) return "high up";
            if (y < 50) return "in a low valley";
            return "on the surface";
        }
        if (y < 0) return "deep underground";
        if (y < 45) return "underground";
        return "under cover";
    }

    /** True when the two snapshots differ enough that the AI should hear about it. */
    public boolean differsMeaningfullyFrom(WorldContext other) {
        if (other == null) return true;
        return !dimension.equals(other.dimension)
                || !phase.equals(other.phase)
                || !weather.equals(other.weather)
                || !biome.equals(other.biome)
                || !altitude.equals(other.altitude);
    }

    /** One natural English sentence, ready to be read by the model. */
    public String describe() {
        StringBuilder sb = new StringBuilder();
        sb.append(capitalise(altitude)).append(" at Y=").append(y);
        sb.append(" in a ").append(biome);
        sb.append(", in ").append(dimension);
        sb.append(", ").append(phase);

        if (!"clear".equals(weather)) {
            sb.append(" and ").append(weather);
        }
        return sb.append('.').toString();
    }

    private static String capitalise(String s) {
        return s.isEmpty() ? s : Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }
}
