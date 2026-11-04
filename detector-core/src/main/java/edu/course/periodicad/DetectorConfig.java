package edu.course.periodicad;

import java.util.Locale;
import java.util.Map;
import java.util.Properties;

/** Immutable, label-free configuration. Null epsilon means automatic, fixed after warmup. */
public record DetectorConfig(long stepMs, long originMs, int period, int k, int window,
                             double tau, double clip, Double epsilon, Strategy strategy,
                             Method method, Long freezeAtMs, ScoreMode scoreMode,
                             int evidenceSpan, double evidenceTau, Double evidenceClip,
                             double evidenceScaleFloor, double evidenceNormalizer,
                             RobustArModel arModel, boolean arLevelContext, double arLevelScaleFloor) {
    public enum ScoreMode { POINT, EWMA }
    /** Original constructor: exact point scoring and update behavior. */
    public DetectorConfig(long stepMs, long originMs, int period, int k, int window,
                          double tau, double clip, Double epsilon, Strategy strategy,
                          Method method, Long freezeAtMs) {
        this(stepMs, originMs, period, k, window, tau, clip, epsilon, strategy, method,
                freezeAtMs, ScoreMode.POINT, 12, 3);
    }
    /** Original EWMA constructor: unbounded evidence input. */
    public DetectorConfig(long stepMs, long originMs, int period, int k, int window,
                          double tau, double clip, Double epsilon, Strategy strategy,
                          Method method, Long freezeAtMs, ScoreMode scoreMode,
                          int evidenceSpan, double evidenceTau) {
        this(stepMs, originMs, period, k, window, tau, clip, epsilon, strategy, method,
                freezeAtMs, scoreMode, evidenceSpan, evidenceTau, null);
    }
    /** Original capped EWMA constructor: no evidence scale floor or output normalization. */
    public DetectorConfig(long stepMs, long originMs, int period, int k, int window,
                          double tau, double clip, Double epsilon, Strategy strategy,
                          Method method, Long freezeAtMs, ScoreMode scoreMode,
                          int evidenceSpan, double evidenceTau, Double evidenceClip) {
        this(stepMs, originMs, period, k, window, tau, clip, epsilon, strategy, method,
                freezeAtMs, scoreMode, evidenceSpan, evidenceTau, evidenceClip, 0, 1);
    }
    /** Original floor/normalizer constructor, before optional offline AR models. */
    public DetectorConfig(long stepMs, long originMs, int period, int k, int window,
                          double tau, double clip, Double epsilon, Strategy strategy,
                          Method method, Long freezeAtMs, ScoreMode scoreMode,
                          int evidenceSpan, double evidenceTau, Double evidenceClip,
                          double evidenceScaleFloor, double evidenceNormalizer) {
        this(stepMs, originMs, period, k, window, tau, clip, epsilon, strategy, method,
                freezeAtMs, scoreMode, evidenceSpan, evidenceTau, evidenceClip,
                evidenceScaleFloor, evidenceNormalizer, null, false, 0);
    }
    /** Output threshold only (after evidence normalization); raw state-update decisions always use tau. */
    public double scoreThreshold() { return scoreMode == ScoreMode.EWMA ? evidenceTau : tau; }
    public static ScoreMode scoreMode(Map<String,String> p) {
        return ScoreMode.valueOf(p.getOrDefault("score_mode", "point").trim().toUpperCase(Locale.ROOT));
    }
    /** Shared with standalone event projection, which does not require detector history options. */
    public static double scoreThreshold(Map<String,String> p, double tau) {
        positive(tau, "tau");
        ScoreMode mode = scoreMode(p);
        int span = Integer.parseInt(p.getOrDefault("evidence_span", "12").trim());
        double threshold = Double.parseDouble(p.getOrDefault("evidence_tau", "3").trim());
        validateScoring(mode, span, threshold, evidenceClip(p), evidenceScaleFloor(p), evidenceNormalizer(p));
        arLevelContext(p); validateLevelFloor(arLevelScaleFloor(p));
        return mode == ScoreMode.EWMA ? threshold : tau;
    }
    private static Double evidenceClip(Map<String,String> p) {
        String value = p.getOrDefault("evidence_clip", "none").trim();
        return value.equalsIgnoreCase("none") ? null : Double.valueOf(value);
    }
    private static double evidenceScaleFloor(Map<String,String> p) {
        return Double.parseDouble(p.getOrDefault("evidence_scale_floor", "0").trim());
    }
    private static double evidenceNormalizer(Map<String,String> p) {
        return Double.parseDouble(p.getOrDefault("evidence_normalizer", "1").trim());
    }
    private static boolean arLevelContext(Map<String,String> p) {
        String value=p.getOrDefault("ar_level_context","false").trim();
        if (!value.equalsIgnoreCase("true") && !value.equalsIgnoreCase("false"))
            throw new IllegalArgumentException("ar_level_context must be true or false");
        return Boolean.parseBoolean(value);
    }
    private static double arLevelScaleFloor(Map<String,String> p) {
        return Double.parseDouble(p.getOrDefault("ar_level_scale_floor","0").trim());
    }
    private static void validateLevelFloor(double floor) {
        if (!Double.isFinite(floor) || floor<0)
            throw new IllegalArgumentException("ar_level_scale_floor must be finite and nonnegative");
    }
    private static void validateScoring(ScoreMode mode, int span, double threshold, Double cap,
                                        double floor, double normalizer) {
        if (mode == null) throw new IllegalArgumentException("Missing score_mode");
        if (span < 1) throw new IllegalArgumentException("evidence_span must be >=1");
        positive(threshold, "evidence_tau");
        if (!Double.isFinite(floor) || floor < 0)
            throw new IllegalArgumentException("evidence_scale_floor must be finite and nonnegative");
        positive(normalizer, "evidence_normalizer");
        if (cap != null) {
            positive(cap, "evidence_clip");
            if (mode == ScoreMode.EWMA && cap / normalizer <= threshold)
                throw new IllegalArgumentException(normalizer == 1
                        ? "evidence_clip must be > evidence_tau in ewma mode"
                        : "evidence_clip/evidence_normalizer must be > evidence_tau in ewma mode");
        }
    }
    public enum Strategy { BOUNDED, DIRECT, FREEZE, GUARDED }
    public enum Method { PERIODIC, ROLLING_Z, ROLLING_MAD, FIXED, NO_PERIOD, ROBUST_AR }
    public DetectorConfig {
        if (stepMs <= 0 || period < 2 || k < 1 || window < 2)
            throw new IllegalArgumentException("Require step_ms>0, period>=2, k>=1, window>=2");
        if (period > 100_000 || (long) period * k + window > 2_000_000L)
            throw new IllegalArgumentException("State limit: period<=100000 and period*k+window<=2000000");
        positive(tau, "tau"); positive(clip, "clip");
        validateScoring(scoreMode, evidenceSpan, evidenceTau, evidenceClip, evidenceScaleFloor, evidenceNormalizer);
        if (epsilon != null) positive(epsilon, "epsilon");
        if (strategy == null || method == null) throw new IllegalArgumentException("Missing strategy/method");
        if (method == Method.FIXED && freezeAtMs == null)
            throw new IllegalArgumentException("fixed requires freeze_at_ms (first frozen timestamp, inclusive)");
        validateLevelFloor(arLevelScaleFloor);
        if (method == Method.ROBUST_AR) {
            if (arModel == null) throw new IllegalArgumentException("robust_ar requires a frozen AR model");
            if (window != period || epsilon != null)
                throw new IllegalArgumentException("robust_ar requires window=period and epsilon=auto");
            if ((long)arModel.maxLag()+2L*window>2_000_000L)
                throw new IllegalArgumentException("AR state limit: maxLag+2*window<=2000000");
        }
    }
    private static void positive(double x, String key) {
        if (!Double.isFinite(x) || x <= 0) throw new IllegalArgumentException(key + " must be finite and positive");
    }
    public static DetectorConfig fromProperties(Properties properties) {
        java.util.HashMap<String,String> map = new java.util.HashMap<>();
        properties.forEach((k,v) -> map.put(k.toString(), v.toString()));
        return fromMap(map);
    }
    public static DetectorConfig fromMap(Map<String,String> p) {
        String eps = p.getOrDefault("epsilon", "auto").trim();
        return new DetectorConfig(Long.parseLong(required(p,"step_ms")), Long.parseLong(p.getOrDefault("origin_ms","0").trim()),
                Integer.parseInt(required(p,"period")), Integer.parseInt(p.getOrDefault("k","4").trim()),
                Integer.parseInt(p.getOrDefault("window",required(p,"period")).trim()),
                Double.parseDouble(p.getOrDefault("tau","4").trim()), Double.parseDouble(p.getOrDefault("clip","3").trim()),
                eps.equalsIgnoreCase("auto") ? null : Double.valueOf(eps),
                Strategy.valueOf(p.getOrDefault("strategy","bounded").trim().toUpperCase(Locale.ROOT)),
                Method.valueOf(p.getOrDefault("method","periodic").trim().toUpperCase(Locale.ROOT)),
                p.containsKey("freeze_at_ms") ? Long.valueOf(p.get("freeze_at_ms").trim()) : null,
                scoreMode(p), Integer.parseInt(p.getOrDefault("evidence_span", "12").trim()),
                Double.parseDouble(p.getOrDefault("evidence_tau", "3").trim()), evidenceClip(p),
                evidenceScaleFloor(p), evidenceNormalizer(p), RobustArModel.fromMap(p),
                arLevelContext(p), arLevelScaleFloor(p));
    }
    private static String required(Map<String,String> p, String key) {
        if (!p.containsKey(key)) throw new IllegalArgumentException("Missing " + key);
        return p.get(key).trim();
    }
    /** Checks subtraction overflow as well as ordering and grid membership. Gaps are permitted. */
    public int phase(long timestamp) {
        long delta;
        try { delta = Math.subtractExact(timestamp, originMs); }
        catch (ArithmeticException e) { throw new IllegalArgumentException("timestamp-origin_ms overflow", e); }
        if (Math.floorMod(delta, stepMs) != 0) throw new IllegalArgumentException("Timestamp is off sampling grid: " + timestamp);
        return (int) Math.floorMod(Math.floorDiv(delta, stepMs), (long) period);
    }
}
