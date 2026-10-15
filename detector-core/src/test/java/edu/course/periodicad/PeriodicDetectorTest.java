package edu.course.periodicad;

import java.util.*;
import org.junit.jupiter.api.Test;
import static edu.course.periodicad.DetectorConfig.Method.*;
import static edu.course.periodicad.DetectorConfig.Strategy.*;
import static org.junit.jupiter.api.Assertions.*;

class PeriodicDetectorTest {
    private DetectorConfig config(DetectorConfig.Strategy strategy, DetectorConfig.Method method, Long freeze) {
        return new DetectorConfig(1,0,2,4,4,4,3,0.01,strategy,method,freeze);
    }
    private PeriodicDetector warm(DetectorConfig config) {
        PeriodicDetector d = new PeriodicDetector(config);
        for (int i=0;i<8;i++) assertNull(d.accept(i,i%2==0 ? 10.0 : 20.0));
        for (int i=8;i<12;i++) assertNull(d.accept(i,new double[]{9,20,10,21}[i-8]));
        return d;
    }
    @Test void reportHandCalculationAndThreeStages() {
        PeriodicDetector d=warm(config(BOUNDED,PERIODIC,null));
        Detection p=d.accept(12,14.0);
        assertEquals(10,p.baseline()); assertEquals(4,p.residual());
        assertEquals(0,p.residualCenter()); assertEquals(0.7413,p.scale(),1e-12);
        assertEquals(5.395926075812762,p.score(),1e-12);
        assertEquals(12.2239,p.update(),1e-12); assertTrue(p.flag());
        // Next phase sees raw residual 4 (not clipped 2.2239): R=[0,0,1,4], m=0.5.
        assertEquals(0.5,d.accept(13,20.0).residualCenter(),1e-12);
    }
    @Test void directFreezeAndBoundedUseDifferentUpdatesButSameRawResidual() {
        for (var strategy : DetectorConfig.Strategy.values()) {
            PeriodicDetector d=warm(config(strategy,PERIODIC,null));
            Detection p=d.accept(12,14.0);
            if (strategy==DIRECT) assertEquals(14,p.update());
            if (strategy==FREEZE) assertNull(p.update());
            assertEquals(0.5,d.accept(13,20.0).residualCenter());
        }
        PeriodicDetector zero=warm(config(BOUNDED,PERIODIC,null));
        Detection p=zero.accept(12,10.0);
        assertEquals(0,p.score()); assertFalse(p.flag()); assertEquals(10,p.update());
    }
    @Test void prefixUnaffectedByFutureAndInstancesIndependent() {
        PeriodicDetector a=new PeriodicDetector(config(BOUNDED,PERIODIC,null));
        PeriodicDetector b=new PeriodicDetector(config(BOUNDED,PERIODIC,null));
        List<Detection> prefix=new ArrayList<>();
        for(int i=0;i<40;i++) prefix.add(a.accept(i,(double)(i%2*10+i%3)));
        for(int i=40;i<100;i++) a.accept(i,9999.0);
        for(int i=0;i<40;i++) assertEquals(prefix.get(i),b.accept(i,(double)(i%2*10+i%3)));
    }
    @Test void nullNonfiniteGridGapAndNegativePhase() {
        var c=new DetectorConfig(10,100,3,1,2,4,3,null,BOUNDED,PERIODIC,null);
        assertEquals(2,c.phase(90));
        PeriodicDetector d=new PeriodicDetector(c);
        assertNull(d.accept(80,null)); assertNull(d.accept(90,Double.NaN));
        assertNull(d.accept(100,1.0)); assertNull(d.accept(120,3.0));
        assertNull(d.accept(140,2.0)); // skipped phase later filled using timestamp, not row count
        assertNull(d.accept(150,3.0)); assertNull(d.accept(160,1.0));
        assertNotNull(d.accept(170,2.0)); assertEquals(2,d.skipped()); assertEquals(8,d.rows());
        assertThrows(IllegalArgumentException.class,()->d.accept(170,null));
        assertThrows(IllegalArgumentException.class,()->d.accept(160,null));
        assertThrows(IllegalArgumentException.class,()->d.accept(181,Double.POSITIVE_INFINITY));
        assertThrows(IllegalArgumentException.class,()->c.phase(Long.MIN_VALUE));
    }
    @Test void automaticEpsilonConstantAndFixedAfterWarmup() {
        var c=new DetectorConfig(1,0,2,1,2,4,3,null,BOUNDED,PERIODIC,null);
        var d=new PeriodicDetector(c);
        for(int i=0;i<4;i++) assertNull(d.accept(i,10.0));
        assertEquals(1e-9,d.epsilon()); assertEquals(0,d.accept(4,10.0).score());
        for(int i=5;i<15;i++) d.accept(i,1e6+(double)i);
        assertEquals(1e-9,d.epsilon());
    }
    @Test void nonzeroAutomaticEpsilonIsDerivedFromInitResidualsAndNeverRecomputed() {
        var c=new DetectorConfig(1,0,2,4,4,4,3,null,BOUNDED,PERIODIC,null);
        var d=warm(c);
        assertEquals(0.0007413,d.epsilon(),1e-15);
        for(int i=12;i<30;i++) d.accept(i,(double)(i*100));
        assertEquals(0.0007413,d.epsilon(),1e-15);
    }
    @Test void fixedContinuesCausalTrainingUntilBoundary() {
        var d=warm(config(DIRECT,FIXED,14L));
        assertEquals(14,d.accept(12,14.0).update());
        assertEquals(22,d.accept(13,22.0).update());
        Detection p=d.accept(14,1000.0);
        assertEquals(1.5,p.residualCenter()); assertEquals(1.4826,p.scale(),1e-12); assertNull(p.update());
        assertEquals(1.5,d.accept(15,2000.0).residualCenter());
    }
    @Test void fixedBoundaryInclusiveAndNoStateUpdatesAfterTraining() {
        var d=warm(config(DIRECT,FIXED,12L));
        Detection first=d.accept(12,14.0);
        assertEquals(10,first.baseline()); assertEquals(0,first.residualCenter()); assertNull(first.update());
        for(int i=13;i<30;i++) {
            Detection p=d.accept(i,1000.0);
            assertEquals(0,p.residualCenter()); assertEquals(0.7413,p.scale(),1e-12);
            if (i%2==0) assertEquals(10,p.baseline());
        }
        var late=new PeriodicDetector(config(BOUNDED,FIXED,3L));
        late.accept(0,10.0); late.accept(1,20.0); late.accept(2,10.0);
        assertThrows(IllegalStateException.class,()->late.accept(3,20.0));
    }
    @Test void rollingUsesOnlyPastValuesAndPopulationStd() {
        var c=new DetectorConfig(1,0,2,1,2,4,3,0.01,DIRECT,ROLLING_Z,null);
        var d=new PeriodicDetector(c);
        assertNull(d.accept(0,1.0)); assertNull(d.accept(1,3.0));
        Detection p=d.accept(2,6.0);
        assertEquals(2,p.residualCenter()); assertEquals(1,p.scale()); assertEquals(4,p.score()); assertFalse(p.flag());
        var mad=new PeriodicDetector(new DetectorConfig(1,0,2,1,2,4,3,0.01,DIRECT,ROLLING_MAD,null));
        mad.accept(0,1.0); mad.accept(1,3.0); assertEquals(1.4826,mad.accept(2,6.0).scale());
    }
    @Test void constantRollingZIsExactlyZeroAndMedianPreservesSubnormals() {
        var c=new DetectorConfig(1,0,2,1,48,4,3,null,DIRECT,ROLLING_Z,null);
        var d=new PeriodicDetector(c);
        for(int i=0;i<48;i++) assertNull(d.accept(i,10.0));
        assertEquals(0,d.accept(48,10.0).score());
        assertEquals(Double.MIN_VALUE,Statistics.median(new double[]{Double.MIN_VALUE,Double.MIN_VALUE}));
        assertEquals(-Double.MIN_VALUE,Statistics.median(new double[]{-Double.MIN_VALUE,-Double.MIN_VALUE}));
    }
    @Test void noPeriodKeepsSameWarmupButZeroBaseline() {
        var d=warm(config(BOUNDED,NO_PERIOD,null));
        Detection p=d.accept(12,14.0);
        assertEquals(0,p.baseline()); assertEquals(14,p.residual()); assertEquals(15,p.residualCenter());
    }
    @Test void rejectsIllegalParametersAndArithmeticOverflow() {
        assertThrows(IllegalArgumentException.class,()->new DetectorConfig(0,0,2,1,2,4,3,null,BOUNDED,PERIODIC,null));
        assertThrows(IllegalArgumentException.class,()->new DetectorConfig(1,0,100001,1,2,4,3,null,BOUNDED,PERIODIC,null));
        for(double v:new double[]{0,-1,Double.NaN,Double.POSITIVE_INFINITY})
            assertThrows(IllegalArgumentException.class,()->new DetectorConfig(1,0,2,1,2,v,3,null,BOUNDED,PERIODIC,null));
        assertThrows(IllegalArgumentException.class,()->Statistics.mad(new double[]{-Double.MAX_VALUE},Double.MAX_VALUE));
        assertEquals(Double.MAX_VALUE,Statistics.median(new double[]{Double.MAX_VALUE,Double.MAX_VALUE}));
    }
}
