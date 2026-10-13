package edu.course.periodicad;

/** Deployment fixture only. This is NOT an anomaly detector. */
public final class EchoValue {
    private EchoValue() {}
    public static double copy(double value) {
        if (!Double.isFinite(value)) throw new IllegalArgumentException("value must be finite");
        return value;
    }
}
