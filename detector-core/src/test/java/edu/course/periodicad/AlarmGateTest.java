package edu.course.periodicad;

import java.util.Map;
import java.util.Properties;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class AlarmGateTest {
    private static final long STEP = 1_800_000L;
    private AlarmGate gate() { return new AlarmGate(new AlarmGateConfig(STEP, 0, 4, 0.5, 2 * STEP)); }
    private void result(AlarmGate gate, long cell, double score, boolean raw, boolean notification, boolean active) {
        assertEquals(new AlarmGate.Result(raw, notification, active), gate.accept(cell * STEP, score));
    }

    @Test void immediateEntryInclusiveRecoveryAndTwoIndependentEvents() {
        AlarmGate gate = gate();
        result(gate, 0, 4, false, false, false); // entry equality does not trigger
        result(gate, 1, 5, true, true, true);
        result(gate, 2, 6, true, false, true); // suppress repeats
        result(gate, 3, 2, false, false, true); // exit equality is safe, first inclusive cell
        result(gate, 4, 2, false, false, false); // 1h = two 30min cells, not three
        result(gate, 5, 5, true, true, true);
        result(gate, 6, 0, false, false, true);
        result(gate, 7, 0, false, false, false);
        result(gate, 8, 4, false, false, false);
    }

    @Test void middleBandAndGapsResetRecoveryWithoutClearingActive() {
        AlarmGate gate = gate();
        result(gate, 0, 5, true, true, true);
        result(gate, 1, 0, false, false, true);
        result(gate, 2, 3, false, false, true); // below tau, but not safe
        result(gate, 3, 0, false, false, true);
        result(gate, 5, 0, false, false, true); // missing cell 4 is not recovery
        result(gate, 6, 0, false, false, false);
        result(gate, 10, 5, true, true, true);
        result(gate, 20, 5, true, false, true); // an active event survives a long gap
    }

    @Test void singleCellDefaultAndInstancesAreIndependent() {
        Properties p = new Properties(); p.setProperty("step_ms", "10"); p.setProperty("tau", "4");
        AlarmGateConfig config = AlarmGateConfig.fromProperties(p);
        assertEquals(new AlarmGateConfig(10, 0, 4, 0.5, 10), config);
        AlarmGate a = new AlarmGate(config), b = new AlarmGate(config);
        assertTrue(a.accept(0, 5).notification());
        assertFalse(a.accept(10, 6).notification());
        assertTrue(b.accept(10, 6).notification());
        assertEquals(new AlarmGate.Result(false, false, false), a.accept(20, 2));
        assertTrue(b.accept(20, 6).active());
    }

    @Test void rejectsParametersAndInvalidPointsWithoutAdvancingState() {
        for (double tau : new double[]{0, -1, Double.NaN, Double.POSITIVE_INFINITY})
            assertThrows(IllegalArgumentException.class, () -> new AlarmGateConfig(10, 0, tau, 0.5, 10));
        for (double ratio : new double[]{0, -1, 1.1, Double.NaN, Double.POSITIVE_INFINITY})
            assertThrows(IllegalArgumentException.class, () -> new AlarmGateConfig(10, 0, 4, ratio, 10));
        for (long recovery : new long[]{-10, 0, 5, 15})
            assertThrows(IllegalArgumentException.class, () -> new AlarmGateConfig(10, 0, 4, 0.5, recovery));
        for (long step : new long[]{0, -1})
            assertThrows(IllegalArgumentException.class, () -> new AlarmGateConfig(step, 0, 4, 0.5, 10));
        assertDoesNotThrow(() -> new AlarmGateConfig(10, 0, 4, 1, 10));
        assertThrows(IllegalArgumentException.class, () -> AlarmGateConfig.fromProperties(new Properties()));

        AlarmGate gate = gate();
        result(gate, 0, 5, true, true, true);
        result(gate, 1, 0, false, false, true);
        for (double score : new double[]{-1, Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY})
            assertThrows(IllegalArgumentException.class, () -> gate.accept(2 * STEP, score));
        assertThrows(IllegalArgumentException.class, () -> gate.accept(2 * STEP + 1, 0));
        assertThrows(IllegalArgumentException.class, () -> gate.accept(STEP, 0));
        assertThrows(IllegalArgumentException.class, () -> gate.accept(0, 0));
        result(gate, 2, 0, false, false, false);
    }

    @Test void nonzeroOriginAndLongBoundariesDoNotOverflowRecoveryOrGaps() {
        AlarmGate gate = new AlarmGate(new AlarmGateConfig(10, 3, 4, 1, 20));
        assertTrue(gate.accept(-7, 5).notification());
        assertTrue(gate.accept(3, 4).active());
        assertFalse(gate.accept(13, 4).active());
        assertThrows(IllegalArgumentException.class, () -> gate.accept(Long.MIN_VALUE, 0));
        AlarmGate extreme = new AlarmGate(new AlarmGateConfig(1, 0, 4, 0.5, 2));
        assertTrue(extreme.accept(Long.MIN_VALUE, 5).notification());
        assertTrue(extreme.accept(Long.MIN_VALUE + 1, 0).active());
        assertTrue(extreme.accept(Long.MAX_VALUE - 1, 0).active()); // huge gap
        assertFalse(extreme.accept(Long.MAX_VALUE, 0).active());
        AlarmGate noWrap = new AlarmGate(new AlarmGateConfig(1, 0, 4, 0.5, 3));
        noWrap.accept(Long.MAX_VALUE - 1, 5);
        assertTrue(noWrap.accept(Long.MAX_VALUE, 0).active());
    }

    @Test void detectorThresholdAndGridAreReused() {
        DetectorConfig detector = DetectorConfig.fromMap(Map.of("step_ms", "10", "origin_ms", "3", "tau", "7", "period", "2"));
        assertEquals(new AlarmGateConfig(10, 3, 7, 0.75, 30),
                AlarmGateConfig.fromMap(Map.of("exit_ratio", "0.75", "recovery_ms", "30"), detector));
    }
}
