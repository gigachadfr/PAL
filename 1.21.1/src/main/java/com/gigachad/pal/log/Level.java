package com.gigachad.pal.log;

/**
 * Priority of a logged event. Drives how the companion Python bot reacts:
 * <ul>
 *   <li>{@link #INFO} — ambient context, accumulated and sent on the timer.</li>
 *   <li>{@link #NOTABLE} — worth a comment, triggers a send.</li>
 *   <li>{@link #CRITICAL} — react now, jumps the queue and interrupts playback.</li>
 * </ul>
 */
public enum Level {
    INFO,
    NOTABLE,
    CRITICAL
}
