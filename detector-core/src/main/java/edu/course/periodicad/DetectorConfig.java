package edu.course.periodicad;

import java.util.Locale;
import java.util.Map;
import java.util.Properties;

/** Immutable, label-free configuration. Null epsilon means automatic, fixed after warmup. */
public record DetectorConfig(long stepMs, long originMs, int period, int k, int window,
                             double tau, double clip, Double epsilon, Strategy strategy,
                             Method method, Long freezeAtMs) {
    public enum Strategy { BOUNDED, DIRECT, FREEZE }
    public enum Method { PERIODIC, ROLLING_Z, ROLLING_MAD, FIXED, NO_PERIOD }
    public DetectorConfig {
        if (stepMs <= 0 || period < 2 || k < 1 || window < 2)
            throw new IllegalArgumentException("Require step_ms>0, period>=2, k>=1, window>=2");
        if (period > 100_000 || (long) period * k + window > 2_000_000L)
            throw new IllegalArgumentException("State limit: period<=100000 and period*k+window<=2000000");
        positive(tau, "tau"); positive(clip, "clip");
        if (epsilon != null) positive(epsilon, "epsilon");
        if (strategy == null || method == null) throw new IllegalArgumentException("Missing strategy/method");
        if (method == Method.FIXED && freezeAtMs == null)
            throw new IllegalArgumentException("fixed requires freeze_at_ms (first frozen timestamp, inclusive)");
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
                p.containsKey("freeze_at_ms") ? Long.valueOf(p.get("freeze_at_ms").trim()) : null);
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
