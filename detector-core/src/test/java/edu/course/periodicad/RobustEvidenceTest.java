package edu.course.periodicad;

import java.util.*;
import org.junit.jupiter.api.Test;
import static edu.course.periodicad.DetectorConfig.*;
import static org.junit.jupiter.api.Assertions.*;

class RobustEvidenceTest {
    private DetectorConfig config(Map<String,String> overrides) {
        Map<String,String> p = new HashMap<>(Map.of("step_ms","1","period","2","k","1","window","2",
                "epsilon","1","method","fixed","freeze_at_ms","4","score_mode","ewma","evidence_span","12"));
        p.putAll(overrides);
        return DetectorConfig.fromMap(p);
    }
    private PeriodicDetector warm(DetectorConfig c) {
        var d = new PeriodicDetector(c);
        for (int t=0;t<4;t++) assertNull(d.accept(t,10.0));
        return d;
    }
    @Test void oneHugeInputCannotCrossThresholdAndItsTailDecaysWithoutAlarming() {
        var d = warm(config(Map.of("evidence_clip","4")));
        Detection spike = d.accept(4,1_000_010.0);
        assertEquals(1_000_000,spike.rawScore()); assertTrue(spike.rawFlag());
        assertEquals(8.0/13,spike.score()); assertFalse(spike.flag());
        assertEquals(spike.score(),spike.evidenceScore());
        double previous = spike.score();
        for (int t=5;t<100;t++) {
            Detection tail = d.accept(t,10.0);
            assertTrue(tail.score()<previous); assertFalse(tail.flag());
            assertEquals(0,tail.rawScore());
            previous=tail.score();
        }
        // Disabled input cap preserves the old huge first jump exactly.
        Detection old = warm(config(Map.of())).accept(4,1_000_010.0);
        assertEquals((2.0/13)*1_000_000,old.score()); assertTrue(old.flag());
    }
    @Test void sustainedSignedDeviationAlarmsAtNinthPointAndOppositeSignsCancel() {
        var positive = warm(config(Map.of("evidence_clip","4")));
        var negative = warm(config(Map.of("evidence_clip","4")));
        for (int n=1;n<=12;n++) {
            Detection a=positive.accept(n+3,1_000_010.0), b=negative.accept(n+3,-999_990.0);
            assertEquals(4*(1-Math.pow(11.0/13,n)),a.score(),1e-14);
            assertEquals(a.score(),b.score()); assertEquals(a.flag(),b.flag());
            assertEquals(n>=9,a.flag()); assertTrue(a.score()<=4);
        }
        var cancel=warm(config(Map.of("evidence_clip","4")));
        cancel.accept(4,1_000_010.0);
        assertEquals(16.0/169,cancel.accept(5,-999_990.0).score(),1e-15);
        // Already-in-range z is not changed by enabling a cap.
        assertEquals(warm(config(Map.of())).accept(4,12.0),
                warm(config(Map.of("evidence_clip","4"))).accept(4,12.0));
    }
    @Test void gapNullAndNonfiniteResetOnlyEvidenceAndKeepFrozenHistory() {
        var d=warm(config(Map.of("evidence_clip","4")));
        d.accept(4,1_000_010.0);
        assertTrue(d.accept(5,1_000_010.0).score()>8.0/13);
        assertEquals(8.0/13,d.accept(8,1_000_010.0).score());
        long t=9;
        for (Double missing:Arrays.asList(null,Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY)) {
            assertNull(d.accept(t++,missing));
            Detection next=d.accept(t++,1_000_010.0);
            assertEquals(8.0/13,next.score()); assertFalse(next.flag());
            assertEquals(10,next.baseline()); assertEquals(1_000_000,next.rawScore()); assertNull(next.update());
            assertTrue(d.accept(t++,1_000_010.0).score()>next.score());
        }
        assertEquals(4,d.skipped());
        assertEquals(8.0/13,d.accept(Long.MAX_VALUE,1_000_010.0).score());
    }
    @Test void allMethodsAndStrategiesKeepRawScoresFlagsUpdatesAndFutureHistoriesExactly() {
        for (Method method:List.of(Method.PERIODIC,Method.ROLLING_Z,Method.ROLLING_MAD,Method.FIXED,Method.NO_PERIOD)) for (Strategy strategy:Strategy.values()) {
            Map<String,String> options=Map.of("method",method.name(),"strategy",strategy.name());
            var rawOptions=new HashMap<>(options); rawOptions.put("score_mode","point");
            var cappedOptions=new HashMap<>(options); cappedOptions.put("evidence_clip","4");
            var raw=new PeriodicDetector(config(rawOptions));
            var unbounded=new PeriodicDetector(config(options));
            var capped=new PeriodicDetector(config(cappedOptions));
            for (int t=0;t<150;t++) {
                Double x;
                if (t<4) x=10.0;
                else if (t%23==0) x=null;
                else x=t%17==0 ? 1e100 : t%19==0 ? -1e100 : t%7==0 ? 1_000_010.0 : t%3==0 ? -20.0 : 12.0;
                Detection a=raw.accept(t,x), b=unbounded.accept(t,x), c=capped.accept(t,x);
                if (a==null) { assertNull(b); assertNull(c); continue; }
                for (Detection selected:List.of(b,c)) {
                    assertEquals(a.baseline(),selected.baseline());
                    assertEquals(a.residual(),selected.residual());
                    assertEquals(a.residualCenter(),selected.residualCenter());
                    assertEquals(a.scale(),selected.scale());
                    assertEquals(a.update(),selected.update(),method+"/"+strategy+" at "+t);
                    assertEquals(a.score(),selected.rawScore()); assertEquals(a.flag(),selected.rawFlag());
                }
                assertEquals(c.score()>3,c.flag()); assertTrue(c.score()<=4);
            }
            assertEquals(raw.rows(),capped.rows()); assertEquals(raw.skipped(),capped.skipped());
            assertEquals(raw.scored(),capped.scored()); assertEquals(raw.epsilon(),capped.epsilon());
        }
    }
    @Test void omittedAndExplicitNoneAreIdenticalAndPointModeIgnoresValidCap() {
        for (Method method:List.of(Method.PERIODIC,Method.ROLLING_Z,Method.ROLLING_MAD,Method.FIXED,Method.NO_PERIOD)) for (Strategy strategy:Strategy.values()) for (String mode:List.of("point","ewma")) {
            Map<String,String> options=new HashMap<>(Map.of("method",method.name(),"strategy",strategy.name(),"score_mode",mode));
            var implicit=new PeriodicDetector(config(options));
            options.put("evidence_clip"," NoNe ");
            var explicit=new PeriodicDetector(config(options));
            options.put("evidence_clip","4");
            var capped=new PeriodicDetector(config(options));
            for (int t=0;t<70;t++) {
                Double x;
                if (t<4) x=10.0;
                else if (t%11==0) x=null;
                else x=t%5==0?1_000_010.0:-12.0;
                Detection old=implicit.accept(t,x);
                assertEquals(old,explicit.accept(t,x));
                Detection bounded=capped.accept(t,x);
                if (mode.equals("point")) assertEquals(old,bounded);
            }
        }
        var oldPoint=new DetectorConfig(1,0,2,1,2,4,3,1.0,Strategy.BOUNDED,Method.FIXED,4L);
        var oldEwma=new DetectorConfig(1,0,2,1,2,4,3,1.0,Strategy.BOUNDED,Method.FIXED,4L,ScoreMode.EWMA,12,3);
        assertEquals(config(Map.of("score_mode","point")),oldPoint);
        assertEquals(config(Map.of()),oldEwma);
        assertNull(oldPoint.evidenceClip()); assertNull(oldEwma.evidenceClip());
    }
    @Test void capDoesNotHideOriginalRawArithmeticOverflow() {
        for (String mode:List.of("point","ewma")) for (String cap:List.of("none","4")) {
            var d=warm(config(Map.of("score_mode",mode,"evidence_clip",cap,"epsilon","1e-9")));
            var error=assertThrows(IllegalArgumentException.class,()->d.accept(4,Double.MAX_VALUE));
            assertEquals("Numeric overflow in detector arithmetic",error.getMessage());
        }
    }
    @Test void mapPropertiesStandaloneGateAndRecordRejectInvalidCapsAndUnreachableThresholds() {
        for (String mode:List.of("point","ewma")) for (String invalid:List.of("0","-1","NaN","Infinity","-Infinity","","auto","null","bad")) {
            var options=Map.of("score_mode",mode,"evidence_clip",invalid);
            assertThrows(IllegalArgumentException.class,()->config(options));
            Properties p=new Properties(); p.putAll(options); p.setProperty("step_ms","1"); p.setProperty("tau","4");
            assertThrows(IllegalArgumentException.class,()->AlarmGateConfig.fromProperties(p));
        }
        for (double invalid:new double[]{0,-1,Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,3,2})
            assertThrows(IllegalArgumentException.class,()->new DetectorConfig(1,0,2,1,2,4,3,1.0,
                    Strategy.BOUNDED,Method.FIXED,4L,ScoreMode.EWMA,12,3,invalid));
        for (String cap:List.of("2","3")) {
            IllegalArgumentException e=assertThrows(IllegalArgumentException.class,()->config(Map.of("evidence_clip",cap)));
            assertEquals("evidence_clip must be > evidence_tau in ewma mode",e.getMessage());
            assertDoesNotThrow(()->config(Map.of("score_mode","point","evidence_clip",cap)));
            Map<String,String> options=Map.of("step_ms","1","tau","4","score_mode","ewma","evidence_clip",cap);
            assertThrows(IllegalArgumentException.class,()->DetectorConfig.scoreThreshold(options,4));
        }
        Properties p=new Properties();
        p.putAll(Map.of("step_ms","1","period","2","score_mode","ewma","evidence_clip"," 4 "));
        assertEquals(4,DetectorConfig.fromProperties(p).evidenceClip());
        p.setProperty("tau","4");
        assertEquals(3,AlarmGateConfig.fromProperties(p).tau());
        p.setProperty("evidence_clip","none"); assertNull(DetectorConfig.fromProperties(p).evidenceClip());
    }
}
