package edu.course.periodicad;

import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

/** Detector CLI plus backward-compatible routing of the preparation/smoke commands. */
public final class DetectorRunner {
    private DetectorRunner() {}
    public static Properties properties(Path path) throws Exception {
        Properties p = new Properties();
        try (var reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) { p.load(reader); }
        return p;
    }
    public static void main(String[] args) throws Exception {
        if (args.length == 0) throw new IllegalArgumentException("Commands: score INPUT OUTPUT CONFIG | batch JOBS.tsv | compare INPUT CONFIG OUTPUT_DIR | benchmark INPUT CONFIG OUTPUT_DIR [REPEATS] | smoke/import-csv/verify-csv/query/unregister-echo");
        switch (args[0]) {
            case "score" -> {
                if (args.length != 4) throw new IllegalArgumentException("score INPUT.csv OUTPUT.csv CONFIG.properties");
                DetectorCsv.score(Path.of(args[1]),Path.of(args[2]),DetectorConfig.fromProperties(properties(Path.of(args[3]))));
            }
            case "batch" -> {
                if (args.length != 2) throw new IllegalArgumentException("batch JOBS.tsv");
                try (BufferedReader reader = Files.newBufferedReader(Path.of(args[1]), StandardCharsets.UTF_8)) {
                    String line; int number=0;
                    while ((line=reader.readLine()) != null) {
                        number++;
                        String[] paths=line.split("\t",-1);
                        if (paths.length!=3) throw new IllegalArgumentException("Expected input<TAB>output<TAB>config at job " + number);
                        for (String path : paths) if (!Path.of(path).isAbsolute())
                            throw new IllegalArgumentException("Batch paths must be absolute at job " + number);
                        DetectorCsv.score(Path.of(paths[0]),Path.of(paths[1]),DetectorConfig.fromProperties(properties(Path.of(paths[2]))));
                    }
                }
            }
            default -> SmokeRunner.main(args);
        }
    }
}
