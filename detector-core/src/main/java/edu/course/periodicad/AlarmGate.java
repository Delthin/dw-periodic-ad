package edu.course.periodicad;

import java.util.Objects;

/** Causal O(1) notification state for one strictly ordered score series. Not a detector. */
public final class AlarmGate {
    public record Result(boolean rawFlag, boolean notification, boolean active) {}

    private final AlarmGateConfig config;
    private boolean seen, active, recovering;
    private long previous, safeSince;

    public AlarmGate(AlarmGateConfig config) {
        this.config = Objects.requireNonNull(config);
    }

    /** Feed scored points only; omitted timestamps break recovery, never clear an active event. */
    public Result accept(long timestamp, double score) {
        if (!Double.isFinite(score) || score < 0) throw new IllegalArgumentException("score must be finite and non-negative");
        long delta;
        try { delta = Math.subtractExact(timestamp, config.originMs()); }
        catch (ArithmeticException e) { throw new IllegalArgumentException("timestamp-origin_ms overflow", e); }
        if (Math.floorMod(delta, config.stepMs()) != 0)
            throw new IllegalArgumentException("Timestamp is off sampling grid: " + timestamp);
        if (seen && timestamp <= previous)
            throw new IllegalArgumentException("Timestamps must strictly increase: " + timestamp);

        // Avoid overflowing either the gap subtraction or previous+step near long limits.
        boolean contiguous = seen && previous <= Long.MAX_VALUE - config.stepMs()
                && timestamp == previous + config.stepMs();
        if (!contiguous) recovering = false;
        seen = true;
        previous = timestamp;

        boolean rawFlag = score > config.tau();
        boolean notification = false;
        if (!active) {
            if (rawFlag) { active = true; notification = true; }
        } else if (score <= config.exitRatio() * config.tau()) {
            if (!recovering) { safeSince = timestamp; recovering = true; }
            // Inclusive sampling cells: timestamp-safeSince+step >= recovery, without overflow.
            long span = config.recoveryMs() - config.stepMs();
            if (safeSince <= Long.MAX_VALUE - span && timestamp >= safeSince + span) {
                active = false;
                recovering = false;
            }
        } else {
            recovering = false;
        }
        return new Result(rawFlag, notification, active);
    }
}
