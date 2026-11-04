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
    private final RingBuffer arFeatures, arLevels;
    private Double levelEpsilon;

    /** O(1), signed causal evidence; never fed back into baseline/residual updates. */
    private Detection output(long timestamp, double x, double baseline, double residual,
                             double center, double scale, double rawScore, boolean rawFlag,
                             Double update, int phase) {
        return output(timestamp,x,baseline,residual,center,scale,rawScore,rawFlag,update,phase,null);
    }
    private Detection output(long timestamp, double x, double baseline, double residual,
                             double center, double scale, double rawScore, boolean rawFlag,
                             Double update, int phase, Double jointInput) {
        if (config.scoreMode() == DetectorConfig.ScoreMode.POINT)
            return new Detection(timestamp, x, baseline, residual, center, scale, rawScore, rawFlag, update, phase);
        boolean contiguous = evidenceSeen && evidencePrevious <= Long.MAX_VALUE - config.stepMs()
                && timestamp == evidencePrevious + config.stepMs();
        if (!contiguous) evidence = 0;
        double z = jointInput == null
                ? finite(finite(residual - center) / Math.max(scale, config.evidenceScaleFloor())) : jointInput;
        // Winsorize only the signed evidence input; raw diagnostics and histories stay untouched.
        if (config.evidenceClip() != null)
            z = Math.max(-config.evidenceClip(), Math.min(config.evidenceClip(), z));
        double alpha = 2.0 / (config.evidenceSpan() + 1.0);
        evidence = finite((1 - alpha) * evidence + alpha * z);
        evidenceSeen = true;
        evidencePrevious = timestamp;
        double score = finite(Math.abs(evidence) / config.evidenceNormalizer());
        return new Detection(timestamp, x, baseline, residual, center, scale, score,
                score > config.scoreThreshold(), update, phase, rawScore, rawFlag, score);
    }

    public PeriodicDetector(DetectorConfig config) {
        this.config = java.util.Objects.requireNonNull(config);
        phases = new RingBuffer[config.method() == ROBUST_AR ? 0 : config.period()];
        residuals = new RingBuffer(config.window());
        epsilon = config.epsilon();
        arFeatures = config.method() == ROBUST_AR ? new RingBuffer(config.arModel().maxLag()) : null;
        arLevels = config.method() == ROBUST_AR ? new RingBuffer(config.window()) : null;
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
        if (config.method() == ROBUST_AR && seen
                && (previous > Long.MAX_VALUE-config.stepMs() || timestamp != previous+config.stepMs())) resetAr();
        seen = true; previous = timestamp; rows++;
        freezeIfDue(timestamp);
        if (value == null || !Double.isFinite(value)) {
            if (config.method() == ROBUST_AR) resetAr();
            skipped++; evidenceSeen = false; evidence = 0; return null;
        }
        double x = value;
        if (config.method() == ROBUST_AR) return acceptAr(timestamp,x,phase);
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
    /** AR discontinuities invalidate sample lags, both warmups and both frozen automatic epsilons. */
    private void resetAr() {
        arFeatures.clear(); arLevels.clear(); residuals.clear();
        epsilon=null; levelEpsilon=null; evidenceSeen=false; evidence=0;
    }
    private void appendArValue(double x) {
        arFeatures.add(x); arLevels.add(x);
        if (levelEpsilon == null && arLevels.full()) {
            double[] values=arLevels.values();
            levelEpsilon=Math.max(1e-9,finite(1e-3*finite(MAD_FACTOR*mad(values,median(values)))));
        }
    }
    private Detection acceptAr(long timestamp, double x, int phase) {
        if (!arFeatures.full()) { appendArValue(x); return null; }
        // Prediction and both statistics use strictly previous samples only.
        double baseline=config.arModel().predict(arFeatures);
        double residual=finite(x-baseline);
        if (!residuals.full()) {
            residuals.add(residual); appendArValue(x);
            if (residuals.full()) initializeEpsilon();
            return null;
        }
        double[] stats=centerScale();
        double rawScore=finite(Math.abs(finite(residual-stats[0]))/stats[1]);
        Double jointInput=null;
        if (config.arLevelContext() && config.scoreMode() == DetectorConfig.ScoreMode.EWMA) {
            double[] levels=arLevels.values();
            double levelCenter=median(levels);
            double levelScale=robustScale(levels,levelCenter,levelEpsilon);
            double zAr=finite(finite(residual-stats[0])/Math.max(stats[1],config.evidenceScaleFloor()));
            double zLevel=finite(finite(x-levelCenter)/Math.max(levelScale,config.arLevelScaleFloor()));
            jointInput=finite(Math.signum(zAr)*Math.sqrt(finite(Math.abs(zAr)*Math.abs(zLevel))));
        }
        Detection point=output(timestamp,x,baseline,residual,stats[0],stats[1],rawScore,
                rawScore>config.tau(),x,phase,jointInput);
        // Raw values and raw residuals always append, regardless of legacy strategy/tau.
        appendArValue(x); residuals.add(residual); scored++;
        return point;
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
