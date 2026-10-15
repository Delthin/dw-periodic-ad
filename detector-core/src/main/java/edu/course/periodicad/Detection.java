package edu.course.periodicad;

/** Only formal scoring points. update is null when no phase value was appended. */
public record Detection(long timestampMs, double value, double baseline, double residual,
                        double residualCenter, double scale, double score, boolean flag,
                        Double update, int phase) {}
