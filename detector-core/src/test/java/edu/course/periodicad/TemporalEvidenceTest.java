package edu.course.periodicad;

import java.util.*;
import org.junit.jupiter.api.Test;
import static edu.course.periodicad.DetectorConfig.*;
import static org.junit.jupiter.api.Assertions.*;

class TemporalEvidenceTest {
    private DetectorConfig config(Map<String,String> overrides) {
        Map<String,String> p = new HashMap<>(Map.of("step_ms","1","period","2","k","1","window","2",
                "epsilon","1","method","fixed","freeze_at_ms","4","score_mode","ewma","evidence_span","3"));
        p.putAll(overrides);
        return DetectorConfig.fromMap(p);
    }
    private PeriodicDetector warm(DetectorConfig c) {
        PeriodicDetector d = new PeriodicDetector(c);
        for (int t=0;t<4;t++) assertNull(d.accept(t,10.0));
        return d;
    }
    @Test void signedEvidenceStartsAtZeroUsesStrictSelectedThresholdAndCancelsOppositeSigns() {
        var d = warm(config(Map.of()));
        Detection a = d.accept(4,16.0);
        assertEquals(6,a.rawScore()); assertTrue(a.rawFlag());
        assertEquals(3,a.evidenceScore()); assertEquals(3,a.score()); assertFalse(a.flag());
        Detection b = d.accept(5,16.0);
        assertEquals(4.5,b.score()); assertTrue(b.flag());
        Detection c = d.accept(6,4.0);
        assertEquals(6,c.rawScore()); assertEquals(0.75,c.score()); assertFalse(c.flag());
        assertNull(c.update()); // fixed training remains frozen
        assertEquals(3,config(Map.of()).scoreThreshold());
    }
    @Test void gapsNullAndEveryNonfiniteValueResetEvidenceButNotRawHistoriesOrWarmup() {
        var d = warm(config(Map.of()));
        assertEquals(3,d.accept(4,16.0).score());
        assertEquals(4.5,d.accept(5,16.0).score());
        assertEquals(3,d.accept(8,16.0).score()); // absent scored timestamp
        long t=9;
        for (Double value : Arrays.asList(null,Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY)) {
            assertNull(d.accept(t++,value));
            Detection next = d.accept(t++,16.0);
            assertEquals(3,next.score()); assertEquals(6,next.rawScore());
            assertEquals(4.5,d.accept(t++,16.0).score());
        }
        assertEquals(4,d.skipped());
        assertThrows(IllegalArgumentException.class,()->d.accept(8,null));
    }
    @Test void allStrategiesAndMethodsKeepOriginalRawUpdateDecisionsEvenWhenEvidenceDisagrees() {
        for (Method method : List.of(Method.PERIODIC,Method.ROLLING_Z,Method.ROLLING_MAD,Method.FIXED,Method.NO_PERIOD)) for (Strategy strategy : Strategy.values()) {
            DetectorConfig point = config(Map.of("method",method.name(),"strategy",strategy.name(),"score_mode","point"));
            DetectorConfig ewma = config(Map.of("method",method.name(),"strategy",strategy.name()));
            var a = new PeriodicDetector(point); var b = new PeriodicDetector(ewma);
            for (int t=0;t<100;t++) {
                double x=t<4?10:t%7==0?100:t%3==0?-20:12;
                Detection raw=a.accept(t,x), selected=b.accept(t,x);
                if (raw==null) { assertNull(selected); continue; }
                assertEquals(raw.baseline(),selected.baseline());
                assertEquals(raw.residual(),selected.residual());
                assertEquals(raw.residualCenter(),selected.residualCenter());
                assertEquals(raw.scale(),selected.scale());
                assertEquals(raw.update(),selected.update(),method+"/"+strategy+" at "+t);
                assertEquals(raw.score(),selected.rawScore()); assertEquals(raw.flag(),selected.rawFlag());
                assertEquals(selected.score()>3,selected.flag());
                assertNull(raw.evidenceScore());
            }
        }
    }
    @Test void spanOneIsRawMagnitudeAndPrefixInstancesAndLongGapsAreIndependent() {
        var c=config(Map.of("evidence_span","1","evidence_tau","4"));
        var a=warm(c); var b=warm(c);
        List<Detection> prefix=new ArrayList<>();
        for(int t=4;t<20;t++) {
            Detection p=a.accept(t,(double)t);
            assertEquals(p.rawScore(),p.score()); assertEquals(p.rawFlag(),p.flag()); prefix.add(p);
        }
        a.accept(Long.MAX_VALUE,100.0);
        for(int t=4;t<20;t++) assertEquals(prefix.get(t-4),b.accept(t,(double)t));
        var large=warm(config(Map.of("evidence_span",Integer.toString(Integer.MAX_VALUE))));
        assertTrue(large.accept(4,16.0).score()>0); // span+1 must not overflow int
    }
    @Test void standaloneAlarmConfigurationValidatesRawAndEvidenceParametersEvenWhenUnused() {
        for(String mode:List.of("point","ewma")) for(var invalid:List.of(
                Map.of("tau","NaN"),Map.of("tau","0"),Map.of("evidence_tau","Infinity"),
                Map.of("evidence_span","2147483648"),Map.of("score_mode","bad"))) {
            Properties p=new Properties();p.setProperty("step_ms","1");p.setProperty("tau","4");p.setProperty("score_mode",mode);
            p.putAll(invalid);
            assertThrows(IllegalArgumentException.class,()->AlarmGateConfig.fromProperties(p));
        }
        Properties p=new Properties();p.setProperty("step_ms","1");p.setProperty("tau","4");p.setProperty("score_mode","ewma");
        assertEquals(3,AlarmGateConfig.fromProperties(p).tau());
    }
    @Test void rejectsInvalidOptionsInBothModesAndRetainsOriginalConstructor() {
        for (String mode:List.of("point","ewma")) for (var invalid:List.of(
                Map.of("evidence_span","0"),Map.of("evidence_span","-1"),Map.of("evidence_span","2147483648"),
                Map.of("evidence_tau","0"),Map.of("evidence_tau","NaN"),Map.of("evidence_tau","Infinity"))) {
            Map<String,String> p=new HashMap<>(invalid);p.put("score_mode",mode);
            assertThrows(IllegalArgumentException.class,()->config(p));
        }
        assertThrows(IllegalArgumentException.class,()->config(Map.of("score_mode","unknown")));
        var old=new DetectorConfig(1,0,2,1,2,4,2,1.0,Strategy.BOUNDED,Method.PERIODIC,null);
        assertEquals(ScoreMode.POINT,old.scoreMode());assertEquals(4,old.scoreThreshold());
        assertNull(warm(config(Map.of("score_mode","point"))).accept(4,16.0).evidenceScore());
    }
}
