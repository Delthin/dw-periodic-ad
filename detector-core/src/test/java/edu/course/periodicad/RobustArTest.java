package edu.course.periodicad;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static edu.course.periodicad.DetectorConfig.*;

class RobustArTest {
    private Map<String,String> parameters() {
        var p=new HashMap<String,String>();
        p.putAll(Map.of("step_ms","1","period","2","k","1","window","2","method","robust_ar","epsilon","auto"));
        p.putAll(Map.of("ar_lags","1","ar_coefficients","0.5","ar_intercept","1","ar_center","10","ar_scale","2","ar_lag_clip","6"));
        p.putAll(Map.of("score_mode","ewma","evidence_span","3","evidence_tau","1.5","evidence_clip","4",
                "evidence_scale_floor","2","evidence_normalizer","2","ar_level_context","true","ar_level_scale_floor","4"));
        return p;
    }
    private PeriodicDetector detector(Map<String,String> overrides) {
        var p=parameters(); p.putAll(overrides); return new PeriodicDetector(DetectorConfig.fromMap(p));
    }
    private List<Detection> run(PeriodicDetector d,double[] x) {
        var out=new ArrayList<Detection>();
        for(int i=0;i<x.length;i++) { var point=d.accept(i,x[i]); if(point!=null) out.add(point); }
        return out;
    }
    @Test void handComputedPredictionWarmupIndependentEpsilonsAndGeometricContext() {
        var d=detector(Map.of());
        assertNull(d.accept(0,10.)); assertNull(d.accept(1,12.)); assertNull(d.accept(2,14.));
        assertEquals(.0007413,d.epsilon(),1e-15); // first two residuals [0,1], not first raw values
        var a=d.accept(3,16.);
        assertEquals(14,a.baseline()); assertEquals(2,a.residual()); assertEquals(.5,a.residualCenter());
        assertEquals(.7413,a.scale(),1e-15); assertEquals(1.5/.7413,a.rawScore(),1e-12);
        assertFalse(a.rawFlag()); assertEquals(16,a.update());
        // zAR=1.5/2=.75; zLevel=(16-13)/4=.75; joint=.75; alpha=.5; norm=2.
        assertEquals(.1875,a.score()); assertEquals(a.score(),a.evidenceScore());
        var b=d.accept(4,20.);
        assertEquals(15,b.baseline()); assertEquals(5,b.residual()); assertEquals(1.5,b.residualCenter());
        assertTrue(b.rawFlag()); assertEquals(20,b.update());
        assertEquals((.5*.375+.5*Math.sqrt(1.75*1.25))/2,b.score(),1e-15);
        assertEquals(5,d.rows()); assertEquals(2,d.scored());
    }
    @Test void ringWrapLagClippingAndRawHistoriesIgnoreAllLegacyUpdateStrategies() {
        double[] values={10,12,14,16,20,1000000,18,20,22,16,14,12};
        var expected=run(detector(Map.of("strategy","direct")),values);
        for(Strategy strategy:Strategy.values()) assertEquals(expected,run(detector(Map.of("strategy",strategy.name())),values));
        var spike=expected.get(2); assertEquals(17,spike.baseline()); assertEquals(1000000,spike.update());
        // The stored million is raw; prediction input alone clamps at +6: 10+2*(1+.5*6)=18.
        assertEquals(18,expected.get(3).baseline());
        // No bounded residual append: previous raw residuals [5,999983] have center 499994.
        assertEquals(499994,expected.get(3).residualCenter());
        var m=new RobustArModel(new int[]{1,3},new double[]{1,10},0,0,1,1e6);
        var ring=new RingBuffer(3); for(double x:new double[]{1,2,3,4}) ring.add(x);
        assertEquals(24,m.predict(ring)); ring.clear();
        assertThrows(IllegalArgumentException.class,()->m.predict(ring));
        var clipped=new RobustArModel(new int[]{1},new double[]{1},0,0,1e-300,6);
        ring.add(Double.MAX_VALUE); assertEquals(6e-300,clipped.predict(ring),1e-314);
    }
    @Test void contextFloorsNormalizerAndPointModeNeverChangeRawDiagnosticsOrHistory() {
        double[] x={10,12,14,16,20,18,10,12,10,40,10,10};
        var raw=run(detector(Map.of("score_mode","point")),x);
        var changedPoint=run(detector(Map.of("score_mode","point","ar_level_context","false","ar_level_scale_floor","1000",
                "evidence_scale_floor","500","evidence_normalizer","7")),x);
        assertEquals(raw,changedPoint);
        for(var overrides:List.<Map<String,String>>of(Map.of(),Map.of("ar_level_context","false"),Map.of("ar_level_scale_floor","100"),
                Map.of("evidence_scale_floor","100","evidence_normalizer","3","evidence_tau","1"))) {
            var ewma=run(detector(overrides),x);
            for(int i=0;i<raw.size();i++) {
                var a=raw.get(i); var b=ewma.get(i);
                assertEquals(a.baseline(),b.baseline()); assertEquals(a.residual(),b.residual());
                assertEquals(a.residualCenter(),b.residualCenter()); assertEquals(a.scale(),b.scale());
                assertEquals(a.rawScore(),b.rawScore()); assertEquals(a.rawFlag(),b.rawFlag()); assertEquals(a.update(),b.update());
            }
        }
        assertNotEquals(run(detector(Map.of()),x).get(1).score(),run(detector(Map.of("ar_level_context","false")),x).get(1).score());
    }
    @Test void prefixesNeverDependOnFutureAndRepeatedInstancesDoNotShareState() {
        double[] x=new double[80]; for(int i=0;i<x.length;i++) x[i]=10+(i*7%13)*.3;
        var full=run(detector(Map.of()),x);
        for(int prefix:List.of(1,2,3,4,6,17,49,80)) {
            var expected=full.stream().filter(d->d.timestampMs()<prefix).toList();
            assertEquals(expected,run(detector(Map.of()),Arrays.copyOf(x,prefix)));
        }
        var left=detector(Map.of()); var right=detector(Map.of()); var untouched=detector(Map.of());
        for(int i=0;i<x.length;i++) {
            left.accept(i,i%2==0 ? 1e5 : -1e5);
            assertEquals(untouched.accept(i,x[i]),right.accept(i,x[i]));
        }
    }
    @Test void gapsNullAndEveryNonfiniteRewarmLagResidualLevelAndBothEpsilons() {
        for(Double missing:Arrays.asList(null,Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY)) {
            var d=detector(Map.of()); run(d,new double[]{10,12,14,16,20});
            assertNull(d.accept(5,missing)); assertNull(d.epsilon());
            var fresh=detector(Map.of());
            for(int i=0;i<7;i++) {
                double x=100+i*10; var a=d.accept(6+i,x); var b=fresh.accept(6+i,x);
                assertEquals(b,a); if(i<3) assertNull(a); else assertNotNull(a);
            }
            assertEquals(1,d.skipped()); assertEquals(13,d.rows());
        }
        var d=detector(Map.of()); run(d,new double[]{10,12,14,16,20});
        var fresh=detector(Map.of());
        for(int i=0;i<8;i++) assertEquals(fresh.accept(20+i,100.+i),d.accept(20+i,100.+i));
        assertThrows(IllegalArgumentException.class,()->d.accept(27,10.));
        assertThrows(IllegalArgumentException.class,()->d.accept(26,10.));
        var grid=detector(Map.of("step_ms","10")); grid.accept(0,10.);
        assertThrows(IllegalArgumentException.class,()->grid.accept(1,null));
    }
    @Test void independentLevelAutomaticEpsilonMatchesRollingMadWhenLevelMadCollapses() {
        var ar=detector(Map.of("ar_level_scale_floor","0","evidence_scale_floor","0","evidence_normalizer","1",
                "evidence_clip","none","evidence_span","1"));
        var level=new PeriodicDetector(DetectorConfig.fromMap(Map.of("step_ms","1","period","2","k","1",
                "window","2","method","rolling_mad","epsilon","auto","strategy","direct")));
        for(int i=0;i<9;i++) {
            double x=new double[]{10,12,14,14,14,16,16,16,18}[i];
            var a=ar.accept(i,x); var b=level.accept(i,x);
            if(a!=null) {
                double za=(a.residual()-a.residualCenter())/a.scale();
                assertEquals(Math.sqrt(Math.abs(za)*b.score()),a.score(),1e-10);
            }
        }
    }
    @Test void immutableModelCopiesInputAndOutputArraysAndConfigConstructorsRemainCompatible() {
        int[] lags={1,3}; double[] coefficients={.5,.25};
        var m=new RobustArModel(lags,coefficients,1,10,2,6);
        lags[0]=100; coefficients[0]=100; m.lags()[0]=100; m.coefficients()[0]=100;
        assertArrayEquals(new int[]{1,3},m.lags()); assertArrayEquals(new double[]{.5,.25},m.coefficients());
        assertEquals(m,new RobustArModel(new int[]{1,3},new double[]{.5,.25},1,10,2,6));
        var old11=new DetectorConfig(1,0,2,1,2,4,2,null,Strategy.BOUNDED,Method.PERIODIC,null);
        var old14=new DetectorConfig(1,0,2,1,2,4,2,null,Strategy.BOUNDED,Method.PERIODIC,null,ScoreMode.POINT,12,3);
        var old15=new DetectorConfig(1,0,2,1,2,4,2,null,Strategy.BOUNDED,Method.PERIODIC,null,ScoreMode.POINT,12,3,null);
        var old17=new DetectorConfig(1,0,2,1,2,4,2,null,Strategy.BOUNDED,Method.PERIODIC,null,ScoreMode.POINT,12,3,null,0,1);
        assertEquals(old11,old14); assertEquals(old11,old15); assertEquals(old11,old17);
        assertNull(old11.arModel()); assertFalse(old11.arLevelContext()); assertEquals(0,old11.arLevelScaleFloor());
        Properties p=new Properties(); p.putAll(parameters());
        assertEquals(DetectorConfig.fromMap(parameters()),DetectorConfig.fromProperties(p));
    }
    @Test void rejectsMalformedModelsContextAndIncompatibleWarmupsIncludingPointMode() {
        var invalid=new ArrayList<Map<String,String>>();
        for(String value:List.of("","0","-1","2,1","1,1","1000001","1,","bad")) invalid.add(Map.of("ar_lags",value));
        for(String key:List.of("ar_coefficients","ar_intercept","ar_center","ar_scale","ar_lag_clip","ar_level_scale_floor"))
            for(String value:List.of("NaN","Infinity","-Infinity","bad","")) invalid.add(Map.of(key,value));
        for(String key:List.of("ar_scale","ar_lag_clip")) for(String value:List.of("0","-1")) invalid.add(Map.of(key,value));
        invalid.add(Map.of("ar_level_scale_floor","-1")); invalid.add(Map.of("ar_coefficients","1,2"));
        invalid.add(Map.of("ar_level_context","yes")); invalid.add(Map.of("window","3")); invalid.add(Map.of("epsilon","1"));
        for(String mode:List.of("point","ewma")) for(var change:invalid) {
            var p=parameters(); p.putAll(change); p.put("score_mode",mode);
            assertThrows(IllegalArgumentException.class,()->DetectorConfig.fromMap(p),p.toString());
        }
        for(String key:List.of("ar_lags","ar_coefficients","ar_intercept","ar_center","ar_scale","ar_lag_clip")) {
            var p=parameters(); p.remove(key); assertThrows(IllegalArgumentException.class,()->DetectorConfig.fromMap(p));
        }
        var empty=parameters(); empty.keySet().removeIf(k->k.startsWith("ar_"));
        assertThrows(IllegalArgumentException.class,()->DetectorConfig.fromMap(empty));
        assertThrows(IllegalArgumentException.class,()->new RobustArModel(new int[1025],new double[1025],0,0,1,6));
        assertThrows(IllegalArgumentException.class,()->new RobustArModel(null,null,0,0,1,6));
    }
    private InputStream resource(String file) {
        return Objects.requireNonNull(getClass().getResourceAsStream("/robust-ar/"+file),file);
    }
    private List<String> lines(String file) throws IOException {
        try(var reader=new BufferedReader(new InputStreamReader(resource(file),StandardCharsets.UTF_8))) { return reader.lines().toList(); }
    }
    @Test void sixReproducibleFrozenPythonFixturesMatchEveryPredictionDiagnosticScoreAndFlag() throws Exception {
        for(String context:List.of("ar","context")) for(String mode:List.of("point","ewma6","ewma12")) {
            String name=context+"-"+mode; var p=new Properties();
            try(var input=resource(name+".properties")) { p.load(input); }
            var d=new PeriodicDetector(DetectorConfig.fromProperties(p));
            var expected=lines(name+".csv"); int row=1;
            for(String line:lines("input.csv").subList(1,101)) {
                String[] input=line.split(","); long t=Long.parseLong(input[0]); double x=Double.parseDouble(input[2]);
                var a=d.accept(t,x); if(t<18) { assertNull(a); continue; }
                assertNotNull(a); String[] e=expected.get(row++).split(",");
                assertEquals(Long.parseLong(e[0]),a.timestampMs());
                double[] actual={a.value(),a.baseline(),a.residual(),a.residualCenter(),a.scale(),a.score()};
                for(int i=0;i<actual.length;i++) {
                    double value=Double.parseDouble(e[i+1]);
                    assertEquals(value,actual[i],Math.max(2e-10,Math.abs(value)*2e-10),name+" t="+t+" column="+i);
                }
                assertEquals(Boolean.parseBoolean(e[7]),a.flag());
                assertEquals(Double.parseDouble(e[8]),a.rawScore(),Math.max(2e-9,a.rawScore()*2e-10));
                assertEquals(Boolean.parseBoolean(e[9]),a.rawFlag()); assertEquals(a.score(),a.evidenceScore());
            }
            assertEquals(expected.size(),row);
        }
    }
}
