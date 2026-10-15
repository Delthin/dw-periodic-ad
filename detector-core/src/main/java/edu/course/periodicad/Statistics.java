package edu.course.periodicad;

import java.util.Arrays;

public final class Statistics {
    public static final double MAD_FACTOR = 1.4826;
    private Statistics() {}
    public static double finite(double x) {
        if (!Double.isFinite(x)) throw new IllegalArgumentException("Numeric overflow in detector arithmetic");
        return x;
    }
    /** Sorts a private copy; even median avoids overflowing a+b. */
    public static double median(double[] values) {
        if (values.length == 0) throw new IllegalArgumentException("Empty median");
        double[] copy = values.clone();
        for (double v : copy) finite(v);
        Arrays.sort(copy);
        int mid = copy.length / 2;
        return (copy.length & 1) == 1 ? copy[mid] : finite(copy[mid-1] / 2 + copy[mid] / 2);
    }
    public static double mad(double[] values, double center) {
        double[] deviations = new double[values.length];
        for (int i=0; i<values.length; i++) deviations[i] = Math.abs(finite(values[i]-center));
        return median(deviations);
    }
    public static double robustScale(double[] values, double center, double epsilon) {
        return Math.max(finite(MAD_FACTOR * mad(values, center)), epsilon);
    }
    public static double mean(double[] values) {
        double mean = 0;
        for (double value : values) mean = finite(mean + value / values.length);
        return mean;
    }
    /** Population standard deviation, not sample standard deviation. */
    public static double populationStd(double[] values, double mean) {
        double largest = 0;
        for (double value : values) largest = Math.max(largest, Math.abs(finite(value - mean)));
        if (largest == 0) return 0;
        double variance = 0;
        for (double value : values) {
            double normalized = (value - mean) / largest;
            variance += normalized * normalized / values.length;
        }
        return finite(largest * Math.sqrt(variance));
    }
    public static double boundedUpdate(double baseline, double center, double residual, double clip, double scale) {
        double bound = finite(clip * scale);
        double deviation = finite(residual - center);
        return finite(finite(baseline + center) + Math.max(-bound, Math.min(bound, deviation)));
    }
}
