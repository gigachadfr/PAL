package com.gigachad.pal.util;

/**
 * The one place that turns Minecraft's internal damage identifiers into English.
 * <p>
 * The same table is needed twice and from two different angles: {@code PlayerActionLogger}
 * has a {@code DamageSource} and reads {@code getMsgId()}, while {@code DeathHistory} only has
 * the death message's translation key. Both end up at the same identifier, so they share the
 * table rather than drifting apart.
 */
public final class Causes {
    private Causes() {}

    /** Damage identifiers whose real culprit is a creature, not the environment. */
    private static final String CREATURE = "a creature";

    /**
     * {@code fall} -> {@code the fall}. Never localised: the identifiers come from the game's
     * internals, not from the player's language, which is what makes the log stable on a
     * non-English client.
     */
    public static String phrase(String msgId) {
        if (msgId == null) return "an unknown cause";
        return switch (msgId) {
            case "inWall" -> "suffocation";
            case "cramming" -> "being crushed by the crowd";
            case "cactus" -> "a cactus";
            case "drown" -> "drowning";
            case "onFire", "inFire" -> "fire";
            case "lava" -> "lava";
            case "hotFloor" -> "magma";
            case "fall", "fallingBlock", "fallingStalactite", "stalagmite" -> "the fall";
            case "flyIntoWall" -> "flying into a wall";
            case "starve" -> "starvation";
            case "outOfWorld" -> "the void";
            case "genericKill" -> "being killed outright";
            case "sweetBerryBush" -> "a berry bush";
            case "freeze" -> "the cold";
            // "explosion.player" keeps its suffix when the creeper died in its own blast and
            // there is no attacker left to name.
            case "explosion", "explosion.player", "badRespawnPoint" -> "an explosion";
            case "lightningBolt" -> "a lightning bolt";
            case "magic", "indirectMagic" -> "magic";
            case "wither" -> "wither";
            case "dryout" -> "drying out";
            case "generic" -> "an unknown cause";
            // Every identifier below means "something alive did it". Which creature it was is
            // answered exactly by the vanilla killed-by statistic, so it is not guessed here.
            case "mob", "player", "arrow", "trident", "fireball", "unattributed_fireball",
                 "witherSkull", "thrown", "sting", "thorns", "mace_smash", "wind_charge",
                 "fireworks" -> CREATURE;
            default -> Names.prettify(msgId).toLowerCase();
        };
    }

    /**
     * Extracts the cause from a death message's translation key, e.g.
     * {@code death.attack.fall} -> {@code the fall}.
     * <p>
     * The key is used rather than the rendered message because the message is translated into
     * whatever language the client runs in — bucketing on it would give one tally per language.
     */
    public static String fromDeathKey(String translationKey) {
        if (translationKey == null || !translationKey.startsWith("death.attack.")) {
            return "an unknown cause";
        }
        String id = translationKey.substring("death.attack.".length());
        // ".player" and ".item" are the "…while fighting X" / "…using X" variants of the same
        // cause. "death.attack.player" itself is not one of them, and does not end with a dot.
        if (id.endsWith(".player")) id = id.substring(0, id.length() - ".player".length());
        if (id.endsWith(".item")) id = id.substring(0, id.length() - ".item".length());
        return phrase(id);
    }

    public static boolean isCreature(String cause) {
        return CREATURE.equals(cause);
    }
}
