package edu.course.periodicad;

import org.apache.iotdb.udf.api.UDTF;
import org.apache.iotdb.udf.api.access.Row;
import org.apache.iotdb.udf.api.collector.PointCollector;
import org.apache.iotdb.udf.api.customizer.config.UDTFConfigurations;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameterValidator;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameters;
import org.apache.iotdb.udf.api.customizer.strategy.RowByRowAccessStrategy;
import org.apache.iotdb.udf.api.type.Type;

/** Tree model only. Every invocation owns its detector and optional notification gate.
 * score_mode selects point/tau or normalized ewma/evidence_tau consistently for all four output modes.
 * Validation (also in beforeStart) requires finite evidence_scale_floor >= 0 and evidence_normalizer > 0;
 * capped EWMA requires evidence_tau < evidence_clip/evidence_normalizer. Point output ignores both.
 * robust_ar requires a complete finite offline model, ascending unique lags, window=period and epsilon=auto.
 * AR gaps/null/nonfinite invalidate feature/residual/level histories; context is EWMA-only, never raw diagnostics.
 * beforeStart constructs fresh model histories, evidence and gate state, including when an object is reused. */
public final class PeriodicADUDTF implements UDTF {
    private PeriodicDetector detector;
    private AlarmGate gate;
    private String mode;
    private Type inputType;
    private static String mode(UDFParameters parameters) {
        String mode = parameters.getStringOrDefault("mode", "score");
        if (!mode.equals("score") && !mode.equals("flag") && !mode.equals("event") && !mode.equals("active"))
            throw new IllegalArgumentException("mode must be score, flag, event or active");
        return mode;
    }
    private static boolean gated(String mode) {
        return mode.equals("event") || mode.equals("active");
    }
    @Override public void validate(UDFParameterValidator validator) throws Exception {
        validator.validateInputSeriesNumber(1).validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE);
        UDFParameters parameters = validator.getParameters();
        DetectorConfig config = DetectorConfig.fromMap(parameters.getAttributes());
        if (gated(mode(parameters))) AlarmGateConfig.fromMap(parameters.getAttributes(), config);
    }
    @Override public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
        DetectorConfig config = DetectorConfig.fromMap(parameters.getAttributes());
        mode = mode(parameters);
        gate = gated(mode) ? new AlarmGate(AlarmGateConfig.fromMap(parameters.getAttributes(), config)) : null;
        detector = new PeriodicDetector(config);
        inputType = parameters.getDataType(0);
        configurations.setAccessStrategy(new RowByRowAccessStrategy()).setOutputDataType(mode.equals("score") ? Type.DOUBLE : Type.BOOLEAN);
    }
    @Override public void transform(Row row, PointCollector collector) throws Exception {
        Double value = null;
        if (!row.isNull(0)) value = switch (inputType) {
            case INT32 -> (double) row.getInt(0);
            case INT64 -> (double) row.getLong(0);
            case FLOAT -> (double) row.getFloat(0);
            case DOUBLE -> row.getDouble(0);
            default -> throw new IllegalArgumentException("Unsupported numeric type: " + inputType);
        };
        Detection point = detector.accept(row.getTime(), value);
        if (point != null) {
            if (gate != null) {
                AlarmGate.Result alarm = gate.accept(point.timestampMs(), point.score());
                collector.putBoolean(point.timestampMs(), mode.equals("event") ? alarm.notification() : alarm.active());
            } else if (mode.equals("flag")) collector.putBoolean(point.timestampMs(), point.flag());
            else collector.putDouble(point.timestampMs(), point.score());
        }
    }
    @Override public void beforeDestroy() { detector = null; gate = null; mode = null; inputType = null; }
}
