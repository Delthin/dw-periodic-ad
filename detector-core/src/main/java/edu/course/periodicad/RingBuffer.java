package edu.course.periodicad;

final class RingBuffer {
    private final double[] values;
    private int size, next;
    RingBuffer(int capacity) { values = new double[capacity]; }
    int size() { return size; }
    boolean full() { return size == values.length; }
    void clear() { size = 0; next = 0; }
    /** Value exactly lag accepted samples before the next insertion; independent of wraparound. */
    double lag(int lag) {
        if (lag<1 || lag>size) throw new IllegalArgumentException("Lag exceeds available history");
        return values[Math.floorMod(next-lag,values.length)];
    }
    void add(double x) {
        values[next] = Statistics.finite(x);
        next = (next+1) % values.length;
        if (size < values.length) size++;
    }
    double[] values() { return java.util.Arrays.copyOf(values, size); }
    double median() { return Statistics.median(values()); }
}
