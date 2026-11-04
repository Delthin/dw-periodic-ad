package edu.course.periodicad;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.Locale;

/** Streaming processed CSV; deliberately no labels or source-time interpretation. */
public final class DetectorCsv implements AutoCloseable {
    public static final String INPUT_HEADER = "timestamp_ms,source_timestamp,value";
    public static final String OUTPUT_HEADER = "timestamp_ms,value,baseline,residual,residual_center,scale,score,flag,update,phase";
    public static final String DIAGNOSTIC_HEADER = OUTPUT_HEADER + ",raw_score,raw_flag,evidence_score,score_mode,score_threshold";
    public record Point(long timestamp, Double value) {}
    private final BufferedReader reader;
    private long line = 1;
    public DetectorCsv(Path input) throws IOException {
        reader = Files.newBufferedReader(input, StandardCharsets.UTF_8);
        if (!INPUT_HEADER.equals(reader.readLine())) {
            reader.close(); throw new IllegalArgumentException("Expected CSV header: " + INPUT_HEADER);
        }
    }
    public Point next() throws IOException {
        String text = reader.readLine();
        if (text == null) return null;
        line++;
        String[] fields = text.split(",", -1);
        if (fields.length != 3) throw new IllegalArgumentException("Invalid CSV at line " + line);
        try {
            String value = fields[2].trim();
            Double parsed = switch (value.toLowerCase(Locale.ROOT)) {
                case "", "null" -> null;
                case "nan" -> Double.NaN;
                case "inf", "+inf", "infinity", "+infinity" -> Double.POSITIVE_INFINITY;
                case "-inf", "-infinity" -> Double.NEGATIVE_INFINITY;
                default -> Double.valueOf(value);
            };
            return new Point(Long.parseLong(fields[0].trim()), parsed);
        } catch (NumberFormatException e) { throw new IllegalArgumentException("Invalid number at CSV line " + line, e); }
    }
    @Override public void close() throws IOException { reader.close(); }
    private static String row(Detection d) {
        return d.timestampMs()+","+d.value()+","+d.baseline()+","+d.residual()+","+d.residualCenter()+","+d.scale()+","+d.score()+","+d.flag()+","+(d.update()==null ? "" : d.update())+","+d.phase();
    }
    public static void write(BufferedWriter writer, Detection d) throws IOException {
        writer.write(row(d)); writer.newLine();
    }
    public static void score(Path input, Path output, DetectorConfig config) throws IOException {
        score(input, output, config, false);
    }
    /** Diagnostics are explicit; the original ten-column score CSV is unchanged. */
    public static void score(Path input, Path output, DetectorConfig config, boolean diagnostics) throws IOException {
        input = input.toAbsolutePath().normalize(); output = output.toAbsolutePath().normalize();
        if (input.equals(output) || (Files.exists(output) && Files.isSameFile(input,output)))
            throw new IllegalArgumentException("Input and output must differ");
        Files.createDirectories(output.getParent());
        Path temp = Files.createTempFile(output.getParent(), ".periodic-score-", ".csv");
        try {
            PeriodicDetector detector = new PeriodicDetector(config);
            try (DetectorCsv csv = new DetectorCsv(input); BufferedWriter writer = Files.newBufferedWriter(temp, StandardCharsets.UTF_8)) {
                writer.write(diagnostics ? DIAGNOSTIC_HEADER : OUTPUT_HEADER); writer.newLine();
                Point p;
                while ((p=csv.next()) != null) {
                    Detection d = detector.accept(p.timestamp(),p.value());
                    if (d != null) {
                        if (!diagnostics) write(writer,d);
                        else {
                            writer.write(row(d)+","+d.rawScore()+","+d.rawFlag()+","+(d.evidenceScore()==null ? "" : d.evidenceScore())+","+config.scoreMode().name().toLowerCase(Locale.ROOT)+","+config.scoreThreshold());
                            writer.newLine();
                        }
                    }
                }
            }
            Files.move(temp, output, StandardCopyOption.REPLACE_EXISTING);
            System.out.println("Scored " + detector.rows()+" rows; skipped="+detector.skipped()+"; output="+detector.scored()+"; epsilon="+detector.epsilon());
        } finally { Files.deleteIfExists(temp); }
    }
}
