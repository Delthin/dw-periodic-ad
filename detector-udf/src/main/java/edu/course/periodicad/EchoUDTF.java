package edu.course.periodicad;

import org.apache.iotdb.udf.api.UDTF;
import org.apache.iotdb.udf.api.access.Row;
import org.apache.iotdb.udf.api.collector.PointCollector;
import org.apache.iotdb.udf.api.customizer.config.UDTFConfigurations;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameterValidator;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameters;
import org.apache.iotdb.udf.api.customizer.strategy.RowByRowAccessStrategy;
import org.apache.iotdb.udf.api.type.Type;

/** Minimal tree-model SQL deployment probe; no anomaly detection. */
public final class EchoUDTF implements UDTF {
    @Override public void validate(UDFParameterValidator validator) throws Exception {
        validator.validateInputSeriesNumber(1).validateInputSeriesDataType(0, Type.DOUBLE);
    }
    @Override public void beforeStart(UDFParameters parameters, UDTFConfigurations config) {
        config.setAccessStrategy(new RowByRowAccessStrategy()).setOutputDataType(Type.DOUBLE);
    }
    @Override public void transform(Row row, PointCollector collector) throws Exception {
        if (!row.isNull(0)) collector.putDouble(row.getTime(), EchoValue.copy(row.getDouble(0)));
    }
}
