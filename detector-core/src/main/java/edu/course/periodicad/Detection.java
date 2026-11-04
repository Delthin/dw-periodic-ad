package edu.course.periodicad;

/** Formal scoring points. score/flag use the selected mode; raw fields describe update decisions.
 * evidenceScore is null in point mode. update is null when no phase value was appended. */
public record Detection(long timestampMs, double value, double baseline, double residual,
                        double residualCenter, double scale, double score, boolean flag,
                        Double update, int phase, double rawScore, boolean rawFlag, Double evidenceScore) {
    /** Backward-compatible point constructor. */
    public Detection(long timestampMs, double value, double baseline, double residual,
                     double residualCenter, double scale, double score, boolean flag,
                     Double update, int phase) {
        this(timestampMs, value, baseline, residual, residualCenter, scale, score, flag,
                update, phase, score, flag, null);
    }
}
