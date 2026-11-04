package edu.course.periodicad;

import java.util.*;
import org.junit.jupiter.api.Test;
import static edu.course.periodicad.DetectorConfig.*;
import static org.junit.jupiter.api.Assertions.*;

class AdaptiveEvidenceTest {
    private Map<String,String> options(Map<String,String> overrides) {
        var p = new HashMap<>(Map.of("step_ms","1","period","2","k","1","window","2",
                "epsilon","1","method","fixed","freeze_at_ms","4","score_mode","ewma","evidence_span","6"));
        p.put("tau","4"); // Standalone event configuration requires the raw threshold explicitly.
        p.putAll(overrides);
        return p;
    }
    private DetectorConfig config(Map<String,String> overrides) {
        return DetectorConfig.fromMap(options(overrides));
    }
    private PeriodicDetector warm(DetectorConfig c) {
        var d = new PeriodicDetector(c);
        for (int t=0;t<4;t++) assertNull(d.accept(t,10.0));
        return d;
    }
    private void sameRaw(Detection expected, Detection actual) {
        assertEquals(expected.timestampMs(),actual.timestampMs());
        assertEquals(expected.value(),actual.value());
        assertEquals(expected.baseline(),actual.baseline());
        assertEquals(expected.residual(),actual.residual());
        assertEquals(expected.residualCenter(),actual.residualCenter());
        assertEquals(expected.scale(),actual.scale());
        assertEquals(expected.rawScore(),actual.rawScore());
        assertEquals(expected.rawFlag(),actual.rawFlag());
        assertEquals(expected.update(),actual.update());
        assertEquals(expected.phase(),actual.phase());
    }
    @Test void defaultsExplicitZeroAndOneAndAllOriginalConstructorsAreCompatible() {
        var point = new DetectorConfig(1,0,2,1,2,4,3,1.0,Strategy.BOUNDED,Method.FIXED,4L);
        var ewma = new DetectorConfig(1,0,2,1,2,4,3,1.0,Strategy.BOUNDED,Method.FIXED,4L,ScoreMode.EWMA,12,3);
        var capped = new DetectorConfig(1,0,2,1,2,4,3,1.0,Strategy.BOUNDED,Method.FIXED,4L,ScoreMode.EWMA,12,3,4.0);
        assertEquals(config(Map.of("score_mode","point","evidence_span","12")),point);
        assertEquals(config(Map.of("evidence_span","12")),ewma);
        assertEquals(config(Map.of("evidence_span","12","evidence_clip","4")),capped);
        assertEquals(0,capped.evidenceScaleFloor()); assertEquals(1,capped.evidenceNormalizer());
        for (Method method:List.of(Method.PERIODIC,Method.ROLLING_Z,Method.ROLLING_MAD,Method.FIXED,Method.NO_PERIOD)) for (Strategy strategy:Strategy.values())
            for (String mode:List.of("point","ewma")) for (String cap:List.of("none","4")) {
                var p=options(Map.of("method",method.name(),"strategy",strategy.name(),"score_mode",mode,"evidence_clip",cap));
                var a=new PeriodicDetector(DetectorConfig.fromMap(p));
                p.putAll(Map.of("evidence_scale_floor"," 0 ","evidence_normalizer"," 1 "));
                var properties=new Properties(); properties.putAll(p);
                var b=new PeriodicDetector(DetectorConfig.fromProperties(properties));
                for (int t=0;t<90;t++) {
                    if (t==61) continue;
                    Double x;
                    if (t<4) x=10.0;
                    else if (t%23==0) x=null;
                    else if (t%31==0) x=Double.NaN;
                    else x=t%7==0 ? 1e100 : t%5==0 ? -20.0 : 12.0;
                    assertEquals(a.accept(t,x),b.accept(t,x),method+"/"+strategy+"/"+mode+"/"+cap+" at "+t);
                }
            }
    }
    @Test void floorAppliesToCenteredEvidenceBeforeCapAndNormalizationNotToRawScale() {
        var d=new PeriodicDetector(config(Map.of("epsilon","0.25","evidence_span","3","evidence_clip","4",
                "evidence_scale_floor","0.5","evidence_normalizer","2","evidence_tau","1.5")));
        assertNull(d.accept(0,10.0)); assertNull(d.accept(1,10.0));
        assertNull(d.accept(2,10.5)); assertNull(d.accept(3,10.5));
        var p=d.accept(4,12.5);
        assertEquals(10.5,p.baseline()); assertEquals(2,p.residual());
        assertEquals(.5,p.residualCenter()); assertEquals(.25,p.scale());
        assertEquals(6,p.rawScore()); assertTrue(p.rawFlag());
        // Centered z=1.5/.5=3 (below cap 4), alpha=.5, then normalize by 2.
        assertEquals(.75,p.score()); assertEquals(.75,p.evidenceScore()); assertFalse(p.flag());
        assertNull(p.update());
        for (String span:List.of("6","12")) {
            var c=config(Map.of("evidence_span",span,"evidence_scale_floor","2","evidence_normalizer","2"));
            var q=warm(c).accept(4,16.0);
            assertEquals(3.0/(Integer.parseInt(span)+1),q.score());
            // Floor smaller than the real scale does not change evidence.
            assertEquals(warm(config(Map.of("evidence_span",span))).accept(4,16.0),
                    warm(config(Map.of("evidence_span",span,"evidence_scale_floor","0.5"))).accept(4,16.0));
        }
    }
    @Test void everyMethodAndStrategyPreservesRawStateAndPointModeIgnoresBothParameters() {
        for (Method method:List.of(Method.PERIODIC,Method.ROLLING_Z,Method.ROLLING_MAD,Method.FIXED,Method.NO_PERIOD)) for (Strategy strategy:Strategy.values()) {
            var p=options(Map.of("method",method.name(),"strategy",strategy.name(),"score_mode","point"));
            var raw=new PeriodicDetector(DetectorConfig.fromMap(p));
            p.putAll(Map.of("evidence_scale_floor","100","evidence_normalizer","2","evidence_clip","4","evidence_tau","1.5"));
            var point=new PeriodicDetector(DetectorConfig.fromMap(p));
            p.put("score_mode","ewma");
            var adaptive=new PeriodicDetector(DetectorConfig.fromMap(p));
            for (int t=0;t<180;t++) {
                if (t==81) continue;
                Double x;
                if (t<4) x=10.0;
                else if (t%23==0) x=null;
                else if (t%31==0) x=Double.POSITIVE_INFINITY;
                else x=t%17==0 ? 1e100 : t%19==0 ? -1e100 : t%7==0 ? 1_000_010.0 : t%3==0 ? -20.0 : 12.0;
                Detection a=raw.accept(t,x), b=point.accept(t,x), c=adaptive.accept(t,x);
                assertEquals(a,b);
                if (a==null) { assertNull(c); continue; }
                sameRaw(a,c);
                assertEquals(c.score()>1.5,c.flag()); assertTrue(c.score()<=2);
            }
            assertEquals(raw.rows(),adaptive.rows()); assertEquals(raw.scored(),adaptive.scored());
            assertEquals(raw.skipped(),adaptive.skipped()); assertEquals(raw.epsilon(),adaptive.epsilon());
        }
    }
    @Test void normalizerScalesOutputNotRecurrenceWithAndWithoutCap() {
        for (String cap:List.of("none","4")) for (String normalizer:List.of("0.5","2","10")) {
            var p=options(Map.of("evidence_clip",cap,"evidence_scale_floor","2","evidence_tau","0.25"));
            var a=warm(DetectorConfig.fromMap(p));
            p.put("evidence_normalizer",normalizer);
            var b=warm(DetectorConfig.fromMap(p));
            for (int t=4;t<50;t++) {
                double x=t%3==0 ? -20 : 16;
                var unscaled=a.accept(t,x); var scaled=b.accept(t,x);
                sameRaw(unscaled,scaled);
                assertEquals(unscaled.score()/Double.parseDouble(normalizer),scaled.score());
                assertEquals(scaled.score(),scaled.evidenceScore()); assertEquals(scaled.score()>.25,scaled.flag());
            }
        }
    }
    @Test void invalidFloorAndNormalizerAreRejectedThroughEveryConfigurationEntry() {
        for (String mode:List.of("point","ewma")) for (String key:List.of("evidence_scale_floor","evidence_normalizer")) {
            var invalid=new ArrayList<>(List.of("-1","NaN","Infinity","-Infinity","","none","bad"));
            if (key.equals("evidence_normalizer")) invalid.add("0");
            for (String value:invalid) {
                var p=options(Map.of("score_mode",mode,key,value));
                var properties=new Properties(); properties.putAll(p);
                assertThrows(IllegalArgumentException.class,()->DetectorConfig.fromMap(p));
                assertThrows(IllegalArgumentException.class,()->DetectorConfig.fromProperties(properties));
                assertThrows(IllegalArgumentException.class,()->DetectorConfig.scoreThreshold(p,4));
                assertThrows(IllegalArgumentException.class,()->AlarmGateConfig.fromProperties(properties));
            }
            for (double value:new double[]{-1,Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY})
                assertThrows(IllegalArgumentException.class,()->new DetectorConfig(1,0,2,1,2,4,3,1.0,Strategy.BOUNDED,
                        Method.FIXED,4L,ScoreMode.valueOf(mode.toUpperCase(Locale.ROOT)),6,3,null,
                        key.equals("evidence_scale_floor") ? value : 0,key.equals("evidence_normalizer") ? value : 1));
        }
        assertThrows(IllegalArgumentException.class,()->new DetectorConfig(1,0,2,1,2,4,3,1.0,
                Strategy.BOUNDED,Method.FIXED,4L,ScoreMode.EWMA,6,3,null,0,0));
    }
    @Test void normalizedCapBoundsReachableThresholdAndStandaloneGateUsesSameThreshold() {
        for (String threshold:List.of("2","2.1","3")) {
            var p=options(Map.of("evidence_clip","4","evidence_normalizer","2","evidence_tau",threshold));
            var properties=new Properties(); properties.putAll(p);
            assertThrows(IllegalArgumentException.class,()->DetectorConfig.fromMap(p));
            assertThrows(IllegalArgumentException.class,()->DetectorConfig.fromProperties(properties));
            assertThrows(IllegalArgumentException.class,()->AlarmGateConfig.fromProperties(properties));
            assertThrows(IllegalArgumentException.class,()->new DetectorConfig(1,0,2,1,2,4,3,1.0,
                    Strategy.BOUNDED,Method.FIXED,4L,ScoreMode.EWMA,6,Double.parseDouble(threshold),4.0,0,2));
            p.put("score_mode","point");
            assertEquals(4,DetectorConfig.fromMap(p).scoreThreshold());
        }
        // Normalizing by <1 can also make a previously unreachable threshold valid.
        var p=options(Map.of("evidence_clip","4","evidence_normalizer","0.5","evidence_tau","6"));
        var properties=new Properties(); properties.putAll(p);
        var c=DetectorConfig.fromProperties(properties);
        assertEquals(6,c.scoreThreshold()); assertEquals(6,AlarmGateConfig.fromProperties(properties).tau());
        assertEquals(6,AlarmGateConfig.fromMap(Map.of(),c).tau());
        assertDoesNotThrow(()->config(Map.of("evidence_clip","4","evidence_normalizer","2","evidence_tau","1.5")));
    }
    @Test void gapsAndNonfiniteResetEvidenceAndSeparateInstancesKeepIndependentPrefixes() {
        var c=config(Map.of("evidence_scale_floor","2","evidence_normalizer","2","evidence_clip","4","evidence_tau","1.5"));
        var d=warm(c);
        assertEquals(3.0/7,d.accept(4,16.0).score());
        assertTrue(d.accept(5,16.0).score()>3.0/7);
        assertEquals(3.0/7,d.accept(8,16.0).score());
        long t=9;
        for (Double missing:Arrays.asList(null,Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY)) {
            assertNull(d.accept(t++,missing));
            var next=d.accept(t++,16.0);
            assertEquals(3.0/7,next.score()); assertEquals(6,next.rawScore());
            assertEquals(10,next.baseline()); assertNull(next.update());
            assertTrue(d.accept(t++,16.0).score()>next.score());
        }
        assertEquals(4,d.skipped());
        assertEquals(3.0/7,d.accept(Long.MAX_VALUE,16.0).score());
        var a=warm(c); var b=warm(c); var expected=new ArrayList<Detection>();
        for (int i=4;i<20;i++) expected.add(a.accept(i,(double)i));
        a.accept(20,1e100);
        for (int i=4;i<20;i++) assertEquals(expected.get(i-4),b.accept(i,(double)i));
    }
}
