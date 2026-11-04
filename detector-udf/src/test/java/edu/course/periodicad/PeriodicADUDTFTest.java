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
    private UDFParameters fixedParameters(String mode, Map<String,String> overrides) {
        Map<String,String> attributes = new HashMap<>(Map.of(
                "step_ms", "1800000", "period", "2", "k", "1", "window", "2", "epsilon", "1",
                "method", "fixed", "freeze_at_ms", "7200000", "recovery_ms", "3600000"));
        if (mode != null) attributes.put("mode", mode);
        attributes.putAll(overrides);
        return new UDFParameters(List.of("value"), List.of(Type.DOUBLE), attributes);
    }
    private PeriodicADUDTF start(UDFParameters p) throws Exception {
        PeriodicADUDTF udf = new PeriodicADUDTF();
        udf.validate(new UDFParameterValidator(p));
        udf.beforeStart(p, new UDTFConfigurations(ZoneOffset.UTC));
        return udf;
    }
    private PointCollector collector(List<Object> points, boolean score) {
        return (PointCollector) Proxy.newProxyInstance(PointCollector.class.getClassLoader(), new Class<?>[]{PointCollector.class}, (o,m,a) -> {
            assertEquals(score ? "putDouble" : "putBoolean", m.getName());
            points.add(List.of(a)); return null;
        });
    }
    @Test void defaultScoreAndRawFlagRemainUnchangedWhileEventAndActiveHandleMissingScores() throws Exception {
        Double[] values = {10.0,10.0,10.0,10.0,15.0,15.0,12.0,null,12.0,12.0,15.0,Double.NaN,12.0,12.0,14.0,15.0};
        long[] cells = {4,5,6,8,9,10,12,13,14,15};
        double[] scores = {5,5,2,2,2,5,2,2,4,5};
        boolean[] flags = {true,true,false,false,false,true,false,false,false,true};
        boolean[] events = {true,false,false,false,false,true,false,false,false,true};
        boolean[] active = {true,true,true,true,false,true,true,false,false,true};
        for (String mode : new String[]{null,"score","flag","event","active"}) {
            var udf = start(fixedParameters(mode, Map.of()));
            List<Object> points = new ArrayList<>();
            boolean scoreMode = mode == null || mode.equals("score");
            var collector = collector(points, scoreMode);
            for (int i = 0; i < values.length; i++) udf.transform(row(i * 1_800_000L, values[i], Type.DOUBLE), collector);
            assertEquals(cells.length, points.size()); // neither warmup nor skipped rows are emitted
            for (int i = 0; i < cells.length; i++) {
                Object value = scoreMode ? (Object) scores[i] : mode.equals("flag") ? flags[i] : mode.equals("event") ? events[i] : active[i];
                assertEquals(List.of(cells[i] * 1_800_000L, value), points.get(i));
            }
            udf.beforeDestroy();
        }
    }
    @Test void eventInstancesAndRestartsAreIndependentAndDefaultsRecoverInOneCell() throws Exception {
        UDFParameters p = fixedParameters("event", Map.of("recovery_ms", "1800000"));
        var a = start(p); var b = start(p);
        List<Object> left = new ArrayList<>(), right = new ArrayList<>();
        var ca = collector(left, false); var cb = collector(right, false);
        for (int i = 0; i < 4; i++) {
            a.transform(row(i * 1_800_000L, 10.0, Type.DOUBLE), ca);
            b.transform(row(i * 1_800_000L, 10.0, Type.DOUBLE), cb);
        }
        a.transform(row(7_200_000, 15.0, Type.DOUBLE), ca);
        b.transform(row(7_200_000, 10.0, Type.DOUBLE), cb);
        a.transform(row(9_000_000, 15.0, Type.DOUBLE), ca);
        b.transform(row(9_000_000, 15.0, Type.DOUBLE), cb);
        a.transform(row(10_800_000, 12.0, Type.DOUBLE), ca);
        a.transform(row(12_600_000, 15.0, Type.DOUBLE), ca);
        assertEquals(List.of(List.of(7_200_000L,true), List.of(9_000_000L,false), List.of(10_800_000L,false), List.of(12_600_000L,true)), left);
        assertEquals(List.of(List.of(7_200_000L,false), List.of(9_000_000L,true)), right);
        a.beforeDestroy();
        a.beforeStart(p, new UDTFConfigurations(ZoneOffset.UTC));
        left.clear();
        for (int i = 0; i < 5; i++) a.transform(row(i * 1_800_000L, i == 4 ? 15.0 : 10.0, Type.DOUBLE), ca);
        assertEquals(List.of(List.of(7_200_000L,true)), left);
    }
    @Test void validatesGateOptionsOnlyForOptInModesAndUsesStepDefault() throws Exception {
        for (String mode : List.of("event", "active")) {
            for (var invalid : List.of(Map.of("exit_ratio", "0"), Map.of("exit_ratio", "NaN"),
                    Map.of("recovery_ms", "0"), Map.of("recovery_ms", "1800001"))) {
                var p = fixedParameters(mode, invalid);
                assertThrows(IllegalArgumentException.class, () -> new PeriodicADUDTF().validate(new UDFParameterValidator(p)));
                assertThrows(IllegalArgumentException.class, () -> new PeriodicADUDTF().beforeStart(p, new UDTFConfigurations(ZoneOffset.UTC)));
            }
            Map<String,String> attributes = new HashMap<>(fixedParameters(mode, Map.of()).getAttributes());
            attributes.remove("recovery_ms");
            var udf = start(new UDFParameters(List.of("value"), List.of(Type.DOUBLE), attributes));
            List<Object> points = new ArrayList<>(); var collector = collector(points, false);
            for (int i = 0; i < 4; i++) udf.transform(row(i * 1_800_000L, 10.0, Type.DOUBLE), collector);
            udf.transform(row(7_200_000, 15.0, Type.DOUBLE), collector);
            udf.transform(row(9_000_000, 12.0, Type.DOUBLE), collector);
            assertEquals(List.of(List.of(7_200_000L,true), List.of(9_000_000L,false)), points);
        }
        assertDoesNotThrow(() -> start(fixedParameters(null, Map.of("exit_ratio", "invalid", "recovery_ms", "invalid"))));
    }
    @Test void ewmaScoreFlagEventActiveUseEvidenceThresholdWithIndependentModesAndRestart() throws Exception {
        // alpha=.5: z=[6,6,-6,missing,6,6] -> |u|=[3,4.5,.75,missing,3,4.5].
        // tau=4 but evidence_tau=3.5: event at 4.5 must recover at .75, then re-enter.
        Double[] values={10.0,10.0,10.0,10.0,16.0,16.0,4.0,null,16.0,16.0};
        List<PeriodicADUDTF> instances=new ArrayList<>();
        List<List<Object>> outputs=new ArrayList<>();
        List<PointCollector> collectors=new ArrayList<>();
        List<String> modes=List.of("score","flag","event","active");
        for(String mode:modes) {
            var p=fixedParameters(mode,Map.of("score_mode","ewma","evidence_span","3","evidence_tau","3.5","recovery_ms","1800000"));
            instances.add(start(p)); outputs.add(new ArrayList<>());
            collectors.add(collector(outputs.get(outputs.size()-1),mode.equals("score")));
        }
        var raw=start(fixedParameters("score",Map.of()));
        List<Object> rawPoints=new ArrayList<>();var rawCollector=collector(rawPoints,true);
        for(int t=0;t<values.length;t++) {
            for(int j=0;j<instances.size();j++) instances.get(j).transform(row(t*1_800_000L,values[t],Type.DOUBLE),collectors.get(j));
            raw.transform(row(t*1_800_000L,values[t],Type.DOUBLE),rawCollector);
        }
        double[] expectedScores={3,4.5,.75,3,4.5};
        boolean[] expected={false,true,false,false,true};
        long[] cells={4,5,6,8,9};
        for(int i=0;i<cells.length;i++) {
            assertEquals(List.of(cells[i]*1_800_000L,expectedScores[i]),outputs.get(0).get(i));
            assertEquals(List.of(cells[i]*1_800_000L,6.0),rawPoints.get(i));
            for(int j=1;j<4;j++) assertEquals(List.of(cells[i]*1_800_000L,expected[i]),outputs.get(j).get(i));
        }
        // Reuse the same UDF object after beforeDestroy, including a different score mode.
        var udf=instances.get(0);udf.beforeDestroy();
        var p=fixedParameters("score",Map.of("score_mode","ewma","evidence_span","3"));
        udf.beforeStart(p,new UDTFConfigurations(ZoneOffset.UTC));outputs.get(0).clear();
        for(int t=0;t<6;t++) udf.transform(row(t*1_800_000L,values[t],Type.DOUBLE),collectors.get(0));
        assertEquals(List.of(List.of(7_200_000L,3.0),List.of(9_000_000L,4.5)),outputs.get(0));
        udf.beforeDestroy();
        udf.beforeStart(fixedParameters("score",Map.of()),new UDTFConfigurations(ZoneOffset.UTC));outputs.get(0).clear();
        for(int t=0;t<6;t++) udf.transform(row(t*1_800_000L,values[t],Type.DOUBLE),collectors.get(0));
        assertEquals(List.of(List.of(7_200_000L,6.0),List.of(9_000_000L,6.0)),outputs.get(0));
        for(var instance:instances) instance.beforeDestroy();raw.beforeDestroy();
    }
    @Test void cappedEvidenceAttributesReachAllFourModesAndRestartCanDisableCap() throws Exception {
        Double[] values={10.0,10.0,10.0,10.0,1_000_010.0,1_000_010.0,1_000_010.0,1_000_010.0,10.0,10.0};
        double[] scores={2,3,3.5,3.75,1.875,.9375};
        boolean[] flags={false,false,true,true,false,false}, events={false,false,true,false,false,false};
        boolean[] active={false,false,true,true,true,false}; // recovery requires score <= 1.5, not just <= 3
        for (String mode:List.of("score","flag","event","active")) {
            var p=fixedParameters(mode,Map.of("score_mode","ewma","evidence_span","3","evidence_clip","4","recovery_ms","1800000"));
            var udf=start(p); List<Object> points=new ArrayList<>(); var out=collector(points,mode.equals("score"));
            for (int t=0;t<values.length;t++) udf.transform(row(t*1_800_000L,values[t],Type.DOUBLE),out);
            assertEquals(scores.length,points.size());
            for (int i=0;i<scores.length;i++) {
                Object expected=mode.equals("score") ? (Object)scores[i] : mode.equals("event") ? events[i]
                        : mode.equals("active") ? active[i] : flags[i];
                assertEquals(List.of((i+4)*1_800_000L,expected),points.get(i));
            }
            udf.beforeDestroy();
            if (mode.equals("score")) {
                var disabled=fixedParameters(mode,Map.of("score_mode","ewma","evidence_span","3","evidence_clip"," NoNe "));
                udf.beforeStart(disabled,new UDTFConfigurations(ZoneOffset.UTC)); points.clear();
                for (int t=0;t<5;t++) udf.transform(row(t*1_800_000L,values[t],Type.DOUBLE),out);
                assertEquals(List.of(List.of(7_200_000L,500_000.0)),points);
                udf.beforeDestroy();
            }
        }
    }
    @Test void validatesEvidenceInValidateAndBeforeStartForEveryOutputMode() {
        for(String mode:List.of("score","flag","event","active")) for(var invalid:List.of(
                Map.of("score_mode","bad"),Map.of("evidence_span","0"),Map.of("evidence_tau","NaN"),
                Map.of("evidence_clip","0"),Map.of("evidence_clip","-1"),Map.of("evidence_clip","NaN"),
                Map.of("evidence_clip","Infinity"),Map.of("evidence_clip","bad"),
                Map.of("score_mode","ewma","evidence_clip","3"),
                Map.of("score_mode","ewma","evidence_clip","2"))) {
            var p=fixedParameters(mode,invalid);
            assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().validate(new UDFParameterValidator(p)));
            assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().beforeStart(p,new UDTFConfigurations(ZoneOffset.UTC)));
        }
    }
    @Test void adaptiveEvidenceSharesNormalizedThresholdAcrossFourModesAndQueryInstances() throws Exception {
        Double[] values={10.0,10.0,10.0,10.0,16.0,18.0,18.0,18.0,10.0,10.0,null,18.0,18.0,18.0,18.0};
        double[] scores={.75,1.375,1.6875,1.84375,.921875,.4609375,1,1.5,1.75,1.875};
        boolean[] flags={false,false,true,true,false,false,false,false,true,true};
        boolean[] events={false,false,true,false,false,false,false,false,true,false};
        boolean[] active={false,false,true,true,true,false,false,false,true,true};
        long[] cells={4,5,6,7,8,9,11,12,13,14};
        for (String mode:List.of("score","flag","event","active")) {
            var p=fixedParameters(mode,Map.of("score_mode","ewma","evidence_span","3","evidence_clip","4",
                    "evidence_scale_floor","2","evidence_normalizer","2","evidence_tau","1.5","recovery_ms","1800000"));
            var a=start(p); var b=start(p);
            List<Object> points=new ArrayList<>(), other=new ArrayList<>();
            var out=collector(points,mode.equals("score")); var outB=collector(other,mode.equals("score"));
            for (int t=0;t<values.length;t++) {
                a.transform(row(t*1_800_000L,values[t],Type.DOUBLE),out);
                b.transform(row(t*1_800_000L,10.0,Type.DOUBLE),outB);
            }
            assertEquals(scores.length,points.size());
            for (int i=0;i<cells.length;i++) {
                Object expected=mode.equals("score") ? (Object)scores[i] : mode.equals("flag") ? flags[i]
                        : mode.equals("event") ? events[i] : active[i];
                assertEquals(List.of(cells[i]*1_800_000L,expected),points.get(i));
            }
            for (Object point:other) assertEquals(mode.equals("score") ? (Object)0.0 : false,((List<?>)point).get(1));
            var previous=new ArrayList<>(points);
            // beforeStart must replace state even if the harness reuses an object without beforeDestroy.
            a.beforeStart(p,new UDTFConfigurations(ZoneOffset.UTC)); points.clear();
            for (int t=0;t<values.length;t++) a.transform(row(t*1_800_000L,values[t],Type.DOUBLE),out);
            assertEquals(previous,points);
            a.beforeDestroy();
            a.beforeStart(fixedParameters(mode,Map.of("evidence_scale_floor","100","evidence_normalizer","2","recovery_ms","1800000")),
                    new UDTFConfigurations(ZoneOffset.UTC)); points.clear();
            for (int t=0;t<5;t++) a.transform(row(t*1_800_000L,t==4 ? 18.0 : 10.0,Type.DOUBLE),out);
            assertEquals(List.of(List.of(7_200_000L,mode.equals("score") ? (Object)8.0 : true)),points);
            a.beforeDestroy(); b.beforeDestroy();
        }
    }
    @Test void adaptiveValidationRejectsInvalidValuesAndUnreachableNormalizedCapForEveryMode() {
        for (String mode:List.of("score","flag","event","active")) for (String scoring:List.of("point","ewma")) {
            for (String key:List.of("evidence_scale_floor","evidence_normalizer")) {
                var invalid=new ArrayList<>(List.of("-1","NaN","Infinity","-Infinity","bad",""));
                if (key.equals("evidence_normalizer")) invalid.add("0");
                for (String value:invalid) {
                    var p=fixedParameters(mode,Map.of("score_mode",scoring,key,value));
                    assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().validate(new UDFParameterValidator(p)));
                    assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().beforeStart(p,new UDTFConfigurations(ZoneOffset.UTC)));
                }
            }
            for (String tau:List.of("2","3")) {
                var p=fixedParameters(mode,Map.of("score_mode",scoring,"evidence_clip","4","evidence_normalizer","2","evidence_tau",tau));
                if (scoring.equals("ewma")) {
                    assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().validate(new UDFParameterValidator(p)));
                    assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().beforeStart(p,new UDTFConfigurations(ZoneOffset.UTC)));
                } else assertDoesNotThrow(()->start(p).beforeDestroy());
            }
        }
    }
    private UDFParameters arParameters(String mode,Map<String,String> overrides) {
        var a=new HashMap<String,String>();
        a.putAll(Map.of("step_ms","1","period","2","window","2","method","robust_ar","mode",mode));
        a.putAll(Map.of("ar_lags","1","ar_coefficients","0","ar_intercept","0","ar_center","10","ar_scale","1","ar_lag_clip","6"));
        a.putAll(Map.of("score_mode","ewma","evidence_span","3","evidence_tau","0.6","evidence_clip","4",
                "evidence_scale_floor","2","evidence_normalizer","2","ar_level_context","true","ar_level_scale_floor","2"));
        a.putAll(overrides); return new UDFParameters(List.of("value"),List.of(Type.DOUBLE),a);
    }
    @Test void robustArFourModesMatchCoreOnWarmupDiscontinuitiesInterleavedQueriesAndRestart() throws Exception {
        Double[] x={10.,10.,10.,16.,16.,10.,10.,null,10.,10.,10.,16.,Double.NaN,10.,10.,10.,16.};
        for(String mode:List.of("score","flag","event","active")) {
            var p=arParameters(mode,Map.of()); var udf=start(p); var other=start(p);
            var config=DetectorConfig.fromMap(p.getAttributes()); var core=new PeriodicDetector(config);
            var gate=new AlarmGate(AlarmGateConfig.fromMap(p.getAttributes(),config));
            List<Object> points=new ArrayList<>(), quiet=new ArrayList<>(), expected=new ArrayList<>();
            var out=collector(points,mode.equals("score")); var quietOut=collector(quiet,mode.equals("score"));
            for(int t=0;t<x.length;t++) {
                long time=t<7 ? t : t+5; // both a gap and explicit invalid rows
                udf.transform(row(time,x[t],Type.DOUBLE),out);
                other.transform(row(time,10.,Type.DOUBLE),quietOut);
                var point=core.accept(time,x[t]);
                if(point!=null) {
                    var alarm=gate.accept(time,point.score());
                    Object value=mode.equals("score") ? (Object)point.score() : mode.equals("flag") ? point.flag()
                            : mode.equals("event") ? alarm.notification() : alarm.active();
                    expected.add(List.of(time,value));
                }
            }
            assertEquals(List.of(3L,mode.equals("score") ? (Object).75 : true),points.get(0));
            assertEquals(expected,points);
            for(var point:quiet) assertEquals(mode.equals("score") ? (Object)0. : false,((List<?>)point).get(1));
            // Same object, fresh histories/model and evidence, without requiring beforeDestroy.
            udf.beforeStart(p,new UDTFConfigurations(ZoneOffset.UTC)); points.clear();
            for(int t=0;t<4;t++) udf.transform(row(t,t==3 ? 16. : 10.,Type.DOUBLE),out);
            assertEquals(List.of(List.of(3L,mode.equals("score") ? (Object).75 : true)),points);
            udf.beforeDestroy(); other.beforeDestroy();
        }
    }
    @Test void robustArValidationAndBeforeStartRejectPartialOrInvalidModelsForEveryMode() {
        for(String mode:List.of("score","flag","event","active")) {
            for(var invalid:List.of(Map.of("ar_lags","2,1"),Map.of("ar_coefficients","NaN"),Map.of("ar_scale","0"),
                    Map.of("ar_lag_clip","Infinity"),Map.of("ar_level_context","yes"),Map.of("ar_level_scale_floor","-1"),
                    Map.of("window","3"),Map.of("epsilon","1"))) {
                var p=arParameters(mode,invalid);
                assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().validate(new UDFParameterValidator(p)));
                assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().beforeStart(p,new UDTFConfigurations(ZoneOffset.UTC)));
            }
            var attributes=new HashMap<>(arParameters(mode,Map.of()).getAttributes()); attributes.remove("ar_intercept");
            var partial=new UDFParameters(List.of("value"),List.of(Type.DOUBLE),attributes);
            assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().validate(new UDFParameterValidator(partial)));
            assertThrows(IllegalArgumentException.class,()->new PeriodicADUDTF().beforeStart(partial,new UDTFConfigurations(ZoneOffset.UTC)));
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
