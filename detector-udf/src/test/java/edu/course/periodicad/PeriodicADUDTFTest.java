package edu.course.periodicad;

import java.lang.reflect.Proxy;
import java.time.ZoneOffset;
import java.util.*;
import org.apache.iotdb.udf.api.access.Row;
import org.apache.iotdb.udf.api.collector.PointCollector;
import org.apache.iotdb.udf.api.customizer.config.UDTFConfigurations;
import org.apache.iotdb.udf.api.customizer.parameter.*;
import org.apache.iotdb.udf.api.type.Type;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class PeriodicADUDTFTest {
    private UDFParameters parameters(Type type,String mode) {
        return new UDFParameters(List.of("value"),List.of(type),Map.of("step_ms","1","period","2","k","1","window","2","epsilon","0.01","mode",mode));
    }
    private Row row(long t, Double v, Type type) {
        return (Row) Proxy.newProxyInstance(Row.class.getClassLoader(),new Class<?>[]{Row.class},(o,m,a)->switch(m.getName()) {
            case "getTime" -> t;
            case "isNull" -> v==null;
            case "getDataType" -> type;
            case "getInt" -> v.intValue();
            case "getLong" -> v.longValue();
            case "getFloat" -> v.floatValue();
            case "getDouble" -> v;
            case "size" -> 1;
            default -> throw new UnsupportedOperationException(m.getName());
        });
    }
    @Test void numericTypesModesAndRepeatedQueriesHaveIndependentState() throws Exception {
        for(Type type:List.of(Type.INT32,Type.INT64,Type.FLOAT,Type.DOUBLE)) {
            for(String mode:List.of("score","flag")) {
                List<Object> previous=null;
                for(int query=0;query<2;query++) {
                    var udf=new PeriodicADUDTF(); var p=parameters(type,mode);
                    udf.validate(new UDFParameterValidator(p));
                    var configurations=new UDTFConfigurations(ZoneOffset.UTC);
                    udf.beforeStart(p,configurations);
                    List<Object> output=new ArrayList<>();
                    PointCollector collector=(PointCollector)Proxy.newProxyInstance(PointCollector.class.getClassLoader(),new Class<?>[]{PointCollector.class},(o,m,a)->{
                        assertEquals(mode.equals("score") ? "putDouble" : "putBoolean",m.getName());
                        output.add(List.of(a)); return null;
                    });
                    for(int i=0;i<5;i++) udf.transform(row(i,10.0,type),collector);
                    assertEquals(List.of(List.of(4L,mode.equals("score") ? (Object)0.0 : false)),output);
                    if(previous!=null) assertEquals(previous,output);
                    previous=output;
                    udf.beforeDestroy();
                }
            }
        }
    }
    @Test void rejectsTypesModesAndNullRowsStillValidateTime() throws Exception {
        var udf=new PeriodicADUDTF();
        assertThrows(Exception.class,()->udf.validate(new UDFParameterValidator(parameters(Type.BOOLEAN,"score"))));
        assertThrows(IllegalArgumentException.class,()->udf.validate(new UDFParameterValidator(parameters(Type.DOUBLE,"bad"))));
        udf.beforeStart(parameters(Type.DOUBLE,"score"),new UDTFConfigurations(ZoneOffset.UTC));
        udf.transform(row(0,null,Type.DOUBLE),null);
        assertThrows(IllegalArgumentException.class,()->udf.transform(row(0,null,Type.DOUBLE),null));
    }
}
