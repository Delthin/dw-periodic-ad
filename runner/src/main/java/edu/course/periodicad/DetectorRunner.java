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
        if (args.length == 0) throw new IllegalArgumentException("Commands: score/score-diagnostics INPUT OUTPUT CONFIG | events SCORES.csv EVENTS.csv CONFIG.properties | batch JOBS.tsv | compare INPUT CONFIG OUTPUT_DIR | benchmark INPUT CONFIG OUTPUT_DIR [REPEATS] | smoke/import-csv/verify-csv/query/unregister-echo");
        switch (args[0]) {
            case "score", "score-diagnostics" -> {
                if (args.length != 4) throw new IllegalArgumentException(args[0]+" INPUT.csv OUTPUT.csv CONFIG.properties");
                DetectorCsv.score(Path.of(args[1]),Path.of(args[2]),DetectorConfig.fromProperties(properties(Path.of(args[3]))), args[0].equals("score-diagnostics"));
            }
            case "events" -> {
                if (args.length != 4) throw new IllegalArgumentException("events SCORES.csv EVENTS.csv CONFIG.properties");
                AlarmCsv.events(Path.of(args[1]), Path.of(args[2]), AlarmGateConfig.fromProperties(properties(Path.of(args[3]))));
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
            case "compare", "benchmark" -> {
                boolean benchmark=args[0].equals("benchmark");
                if (args.length!=4 && !(benchmark && args.length==5))
                    throw new IllegalArgumentException(args[0]+" INPUT.csv CONFIG.properties OUTPUT_DIR"+(benchmark?" [REPEATS=5]":""));
                if(benchmark) DatabaseExperiments.benchmark(Path.of(args[1]),Path.of(args[2]),Path.of(args[3]),args.length==5?Integer.parseInt(args[4]):5);
                else DatabaseExperiments.compare(Path.of(args[1]),Path.of(args[2]),Path.of(args[3]));
            }
            case "register-detector" -> {
                if(args.length!=1) throw new IllegalArgumentException("register-detector");
                try(DetectorDatabase db=new DetectorDatabase()) { db.register(); }
            }
            case "import-detector" -> {
                if(args.length<3 || args.length>4) throw new IllegalArgumentException("import-detector INPUT.csv CONFIG.properties [root.periodic_ad.DEVICE]");
                Properties p=properties(Path.of(args[2]));
                String device=args.length==4?DetectorDatabase.validateDevice(args[3]):DetectorDatabase.device(p);
                DetectorConfig c=DetectorConfig.fromProperties(p);
                DetectorDatabase.inspect(Path.of(args[1]),c);
                try(DetectorDatabase db=new DetectorDatabase()) { db.importNew(Path.of(args[1]),device,c); }
                System.out.println("Imported device="+device+" (set db_device in properties to reuse read-only)");
            }
            default -> SmokeRunner.main(args);
        }
    }
}
