package edu.course.periodicad;

import static edu.course.periodicad.Statistics.*;
import static edu.course.periodicad.DetectorConfig.Method.*;
import static edu.course.periodicad.DetectorConfig.Strategy.*;

/** Single ordered series, single query, score-before-update; no static mutable state. */
public final class PeriodicDetector {
    private final DetectorConfig config;
    private final RingBuffer[] phases;
    private final RingBuffer residuals;
    private int fullPhases;
    private boolean seen, frozen;
    private long previous, rows, skipped, scored;
    private Double epsilon;
    private double[] frozenBaselines;
    private double frozenCenter, frozenScale;
    private boolean evidenceSeen;
    private long evidencePrevious;
    private double evidence;

    /** O(1), signed causal evidence; never fed back into baseline/residual updates. */
    private Detection output(long timestamp, double x, double baseline, double residual,
                             double center, double scale, double rawScore, boolean rawFlag,
                             Double update, int phase) {
        if (config.scoreMode() == DetectorConfig.ScoreMode.POINT)
            return new Detection(timestamp, x, baseline, residual, center, scale, rawScore, rawFlag, update, phase);
        boolean contiguous = evidenceSeen && evidencePrevious <= Long.MAX_VALUE - config.stepMs()
                && timestamp == evidencePrevious + config.stepMs();
        if (!contiguous) evidence = 0;
        double z = finite(finite(residual - center) / scale);
        double alpha = 2.0 / (config.evidenceSpan() + 1.0);
        evidence = finite((1 - alpha) * evidence + alpha * z);
        evidenceSeen = true;
        evidencePrevious = timestamp;
        double score = Math.abs(evidence);
        return new Detection(timestamp, x, baseline, residual, center, scale, score,
                score > config.scoreThreshold(), update, phase, rawScore, rawFlag, score);
    }

    public PeriodicDetector(DetectorConfig config) {
        this.config = java.util.Objects.requireNonNull(config);
        phases = new RingBuffer[config.period()];
        residuals = new RingBuffer(config.window());
        epsilon = config.epsilon();
    }
    public long rows() { return rows; }
    public long skipped() { return skipped; }
    public long scored() { return scored; }
    public Double epsilon() { return epsilon; }
    private boolean rolling() { return config.method() == ROLLING_Z || config.method() == ROLLING_MAD; }
    private void initializeEpsilon() {
        if (epsilon == null) {
            double[] values = residuals.values();
            epsilon = Math.max(1e-9, finite(1e-3 * finite(MAD_FACTOR * mad(values, median(values)))));
        }
    }
    private double[] centerScale() {
        double[] values = residuals.values();
        double center = config.method() == ROLLING_Z ? mean(values) : median(values);
        double scale = config.method() == ROLLING_Z ? Math.max(populationStd(values, center), epsilon)
                : robustScale(values, center, epsilon);
        return new double[]{center, scale};
    }
    private void freezeIfDue(long timestamp) {
        if (config.method() != FIXED || frozen || timestamp < config.freezeAtMs()) return;
        if (fullPhases != config.period() || !residuals.full())
            throw new IllegalStateException("fixed training ended before warmup completed");
        initializeEpsilon();
        frozenBaselines = new double[phases.length];
        for (int i=0; i<phases.length; i++) frozenBaselines[i] = phases[i].median();
        double[] stats = centerScale();
        frozenCenter = stats[0]; frozenScale = stats[1]; frozen = true;
    }
    /** Null/nonfinite values are counted/skipped, but their timestamps are always validated. */
    public Detection accept(long timestamp, Double value) {
        int phase = config.phase(timestamp);
        if (seen && timestamp <= previous) throw new IllegalArgumentException("Timestamps must strictly increase: " + timestamp);
        seen = true; previous = timestamp; rows++;
        freezeIfDue(timestamp);
        if (value == null || !Double.isFinite(value)) {
            skipped++; evidenceSeen = false; evidence = 0; return null;
        }
        double x = value;
        if (rolling()) return acceptRolling(timestamp, x, phase);
        RingBuffer history = phases[phase];
        if (history == null) phases[phase] = history = new RingBuffer(config.k());
        if (fullPhases != config.period()) {
            boolean wasFull = history.full();
            history.add(x);
            if (!wasFull && history.full()) fullPhases++;
            return null;
        }
        double baseline = config.method() == NO_PERIOD ? 0 : frozen ? frozenBaselines[phase] : history.median();
        double residual = finite(x - baseline);
        if (!residuals.full()) {
            residuals.add(residual); history.add(x);
            if (residuals.full()) initializeEpsilon();
            return null;
        }
        double[] stats = frozen ? new double[]{frozenCenter, frozenScale} : centerScale();
        double center = stats[0], scale = stats[1];
        double score = finite(Math.abs(finite(residual-center)) / scale);
        boolean flag = score > config.tau();
        Double update = null;
        if (!frozen && (config.strategy() != GUARDED || !flag)) {
            if (config.strategy() != FREEZE || !flag) {
                update = config.strategy() == BOUNDED || config.strategy() == GUARDED
                        ? boundedUpdate(baseline, center, residual, config.clip(), scale) : x;
                history.add(update);
            }
            // FREEZE retains the ORIGINAL residual; GUARDED rejects both updates when flagged.
            residuals.add(residual);
        }
        scored++;
        return output(timestamp, x, baseline, residual, center, scale, score, flag, update, phase);
    }
    private Detection acceptRolling(long timestamp, double x, int phase) {
        if (!residuals.full()) {
            residuals.add(x);
            if (residuals.full()) initializeEpsilon();
            return null;
        }
        double[] stats = centerScale();
        double score = finite(Math.abs(finite(x-stats[0])) / stats[1]);
        boolean flag = score > config.tau();
        boolean update = config.strategy() != GUARDED || !flag;
        if (update) residuals.add(x);
        scored++;
        return output(timestamp, x, 0, x, stats[0], stats[1], score, flag, update ? x : null, phase);
    }
}
