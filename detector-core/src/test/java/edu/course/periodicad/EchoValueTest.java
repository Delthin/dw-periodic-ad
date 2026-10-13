package edu.course.periodicad;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class EchoValueTest {
    @Test void preservesValues() {
        assertEquals(14.0, EchoValue.copy(14.0));
        assertEquals(-0.25, EchoValue.copy(-0.25));
        assertEquals(0.0, EchoValue.copy(0.0));
    }
    @Test void rejectsNonFinite() {
        for (double value : new double[]{Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY})
            assertThrows(IllegalArgumentException.class, () -> EchoValue.copy(value));
    }
}
