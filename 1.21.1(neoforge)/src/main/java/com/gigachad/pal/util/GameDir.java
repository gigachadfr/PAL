package com.gigachad.pal.util;

import net.neoforged.fml.loading.FMLPaths;

import java.nio.file.Path;

/**
 * Where the game is installed.
 *
 * <p>The one thing every file this mod writes needs, and the one thing the two loaders spell
 * differently: Fabric says {@code FabricLoader.getInstance().getGameDir()}, NeoForge says
 * {@code FMLPaths.GAMEDIR.get()}. Isolating it here keeps the loader out of the three classes
 * that write files, so this build and the Fabric one differ in a single method rather than in
 * three unrelated places — which matters, because both are maintained from the same source.
 */
public final class GameDir {
    private GameDir() {}

    public static Path get() {
        return FMLPaths.GAMEDIR.get();
    }
}
