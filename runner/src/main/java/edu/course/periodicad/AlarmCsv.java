package edu.course.periodicad;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;

/** Streaming, method-independent notification projection of DetectorCsv scores. */
public final class AlarmCsv {
    public static final String OUTPUT_HEADER = "timestamp_ms,score,raw_flag,notification,active";
    private AlarmCsv() {}

    public static void events(Path input, Path output, AlarmGateConfig config) throws IOException {
        input = input.toAbsolutePath().normalize();
        output = output.toAbsolutePath().normalize();
        if (input.equals(output) || (Files.exists(output) && Files.isSameFile(input, output)))
            throw new IllegalArgumentException("Input and output must differ");
        Files.createDirectories(output.getParent());
        Path temp = Files.createTempFile(output.getParent(), ".periodic-events-", ".csv");
        try {
            AlarmGate gate = new AlarmGate(config);
            try (BufferedReader reader = Files.newBufferedReader(input, StandardCharsets.UTF_8);
                 BufferedWriter writer = Files.newBufferedWriter(temp, StandardCharsets.UTF_8)) {
                String header = reader.readLine();
                boolean diagnostics = DetectorCsv.DIAGNOSTIC_HEADER.equals(header);
                if (!diagnostics && !DetectorCsv.OUTPUT_HEADER.equals(header))
                    throw new IllegalArgumentException("Expected detector score or diagnostic CSV header");
                writer.write(OUTPUT_HEADER);
                writer.newLine();
                String line;
                long number = 1;
                while ((line = reader.readLine()) != null) {
                    number++;
                    String[] fields = line.split(",", -1);
                    if (fields.length != (diagnostics ? 15 : 10)) throw new IllegalArgumentException("Invalid score CSV at line " + number);
                    long timestamp;
                    double score;
                    try {
                        timestamp = Long.parseLong(fields[0].trim());
                        score = Double.parseDouble(fields[6].trim());
                        if (diagnostics && Double.compare(Double.parseDouble(fields[14]), config.tau()) != 0)
                            throw new IllegalArgumentException("Score threshold/config mismatch at line " + number);
                    } catch (NumberFormatException e) {
                        throw new IllegalArgumentException("Invalid number at score CSV line " + number, e);
                    }
                    AlarmGate.Result alarm = gate.accept(timestamp, score);
                    writer.write(timestamp + "," + score + "," + alarm.rawFlag() + "," + alarm.notification() + "," + alarm.active());
                    writer.newLine();
                }
            }
            Files.move(temp, output, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(temp);
        }
    }
}
