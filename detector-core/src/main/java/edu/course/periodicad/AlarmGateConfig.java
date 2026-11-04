package edu.course.periodicad;

import java.util.HashMap;
import java.util.Map;
import java.util.Properties;

/** Optional notification policy, independent of detector scoring and updates. */
public record AlarmGateConfig(long stepMs, long originMs, double tau, double exitRatio, long recoveryMs) {
    public AlarmGateConfig {
        if (stepMs <= 0) throw new IllegalArgumentException("step_ms must be positive");
        if (!Double.isFinite(tau) || tau <= 0)
            throw new IllegalArgumentException("tau must be finite and positive");
        if (!Double.isFinite(exitRatio) || exitRatio <= 0 || exitRatio > 1)
            throw new IllegalArgumentException("exit_ratio must be in (0,1]");
        if (recoveryMs < stepMs || recoveryMs % stepMs != 0)
            throw new IllegalArgumentException("recovery_ms must be >= step_ms and a multiple of step_ms");
    }

    /** Uses the detector's exact threshold and grid; only gate options come from attributes. */
    public static AlarmGateConfig fromMap(Map<String,String> p, DetectorConfig detector) {
        return fromMap(p, detector.stepMs(), detector.originMs(), detector.scoreThreshold());
    }

    /** Standalone score-file configuration; no period/history parameters are required. */
    public static AlarmGateConfig fromProperties(Properties properties) {
        Map<String,String> p = new HashMap<>();
        properties.forEach((k,v) -> p.put(k.toString(), v.toString()));
        return fromMap(p, Long.parseLong(required(p, "step_ms")),
                Long.parseLong(p.getOrDefault("origin_ms", "0").trim()),
                DetectorConfig.scoreThreshold(p, Double.parseDouble(required(p, "tau"))));
    }

    private static AlarmGateConfig fromMap(Map<String,String> p, long step, long origin, double tau) {
        return new AlarmGateConfig(step, origin, tau,
                Double.parseDouble(p.getOrDefault("exit_ratio", "0.5").trim()),
                Long.parseLong(p.getOrDefault("recovery_ms", Long.toString(step)).trim()));
    }

    private static String required(Map<String,String> p, String key) {
        if (!p.containsKey(key)) throw new IllegalArgumentException("Missing " + key);
        return p.get(key).trim();
    }
}
