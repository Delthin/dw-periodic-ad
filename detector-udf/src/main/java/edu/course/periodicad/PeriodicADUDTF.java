package edu.course.periodicad;

import org.apache.iotdb.udf.api.UDTF;
import org.apache.iotdb.udf.api.access.Row;
import org.apache.iotdb.udf.api.collector.PointCollector;
import org.apache.iotdb.udf.api.customizer.config.UDTFConfigurations;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameterValidator;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameters;
import org.apache.iotdb.udf.api.customizer.strategy.RowByRowAccessStrategy;
import org.apache.iotdb.udf.api.type.Type;

/** Tree model only. Every invocation owns its detector, including score/flag siblings. */
public final class PeriodicADUDTF implements UDTF {
    private PeriodicDetector detector;
    private boolean flagMode;
    private Type inputType;
    private static boolean flagMode(UDFParameters parameters) {
        String mode = parameters.getStringOrDefault("mode", "score");
        if (!mode.equals("score") && !mode.equals("flag"))
            throw new IllegalArgumentException("mode must be score or flag");
        return mode.equals("flag");
    }
    @Override public void validate(UDFParameterValidator validator) throws Exception {
        validator.validateInputSeriesNumber(1).validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE);
        DetectorConfig.fromMap(validator.getParameters().getAttributes());
        flagMode(validator.getParameters());
    }
    @Override public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
        detector = new PeriodicDetector(DetectorConfig.fromMap(parameters.getAttributes()));
        flagMode = flagMode(parameters);
        inputType = parameters.getDataType(0);
        configurations.setAccessStrategy(new RowByRowAccessStrategy()).setOutputDataType(flagMode ? Type.BOOLEAN : Type.DOUBLE);
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
            if (flagMode) collector.putBoolean(point.timestampMs(), point.flag());
            else collector.putDouble(point.timestampMs(), point.score());
        }
    }
    @Override public void beforeDestroy() { detector = null; inputType = null; }
}
