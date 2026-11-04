package edu.course.periodicad;

import java.nio.file.*;
import java.util.*;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class RobustEvidenceIntegrationTest {
    @TempDir Path dir;

    @Test void propertiesReachCliScoresDiagnosticsAndEventsWithoutChangingColumnContracts() throws Exception {
        Path input=dir.resolve("input.csv"), config=dir.resolve("robust.properties"), scores=dir.resolve("scores.csv"),
                diagnostics=dir.resolve("diagnostics.csv"), events=dir.resolve("events.csv"), other=dir.resolve("other.csv");
        Files.writeString(input,DetectorCsv.INPUT_HEADER+"\n0,x,10\n10,x,10\n20,x,10\n30,x,10\n40,x,1000010\n50,x,1000010\n60,x,1000010\n70,x,1000010\n80,x,10\n");
        Files.writeString(config,"step_ms=10\nperiod=2\nk=1\nwindow=2\nepsilon=1\nmethod=fixed\nfreeze_at_ms=40\ntau=4\nscore_mode=ewma\nevidence_span=3\nevidence_tau=3\nevidence_clip=4\n");
        DetectorRunner.main(new String[]{"score",input.toString(),scores.toString(),config.toString()});
        DetectorRunner.main(new String[]{"score-diagnostics",input.toString(),diagnostics.toString(),config.toString()});
        DetectorRunner.main(new String[]{"events",scores.toString(),events.toString(),config.toString()});
        DetectorRunner.main(new String[]{"events",diagnostics.toString(),other.toString(),config.toString()});
        assertEquals(AlarmCsv.OUTPUT_HEADER+"\n40,2.0,false,false,false\n50,3.0,false,false,false\n60,3.5,true,true,true\n70,3.75,true,false,true\n80,1.875,false,false,true\n",Files.readString(events));
        assertEquals(Files.readString(events),Files.readString(other));
        var standard=Files.readAllLines(scores); var diagnostic=Files.readAllLines(diagnostics);
        assertEquals(DetectorCsv.OUTPUT_HEADER,standard.get(0));
        assertEquals(DetectorCsv.DIAGNOSTIC_HEADER,diagnostic.get(0));
        assertTrue(diagnostic.get(1).endsWith(",1000000.0,true,2.0,ewma,3.0"));
        for (int i=1;i<standard.size();i++) {
            assertEquals(10,standard.get(i).split(",",-1).length);
            String[] columns=diagnostic.get(i).split(",",-1);
            assertEquals(15,columns.length);
            assertEquals(standard.get(i),String.join(",",Arrays.copyOf(columns,10)));
        }
    }

    @Test void comparisonAndBenchmarkSqlRoundTripEveryModeAndDisabledCapWithoutDatabase() {
        for (String cap:List.of("4","none")) {
            var config=DetectorConfig.fromMap(Map.of("step_ms","10","period","2","score_mode","ewma","evidence_clip",cap));
            var input=new DetectorDatabase.Input(0,200,21);
            var gate=AlarmGateConfig.fromMap(Map.of(),config);
            // DatabaseExperiments.compare and .benchmark use these same SQL constructors.
            String comparison=DetectorDatabase.udfQuery("root.periodic_ad.test",input,config,gate);
            String benchmark=DetectorDatabase.udfScoreQuery("root.periodic_ad.test",input,config);
            String legacy=DetectorDatabase.udfQuery("root.periodic_ad.test",input,config);
            for (var query:List.of(Map.entry(comparison,4),Map.entry(benchmark,1),Map.entry(legacy,2))) {
                String sql=query.getKey(); int expected=query.getValue();
                var columns=Pattern.compile("periodic_ad\\(value,([^)]*)\\)").matcher(sql);
                Set<String> modes=new HashSet<>(); int count=0;
                while (columns.find()) {
                    Map<String,String> attributes=new HashMap<>();
                    var pair=Pattern.compile("'([^']+)'='([^']*)'").matcher(columns.group(1));
                    while (pair.find()) assertNull(attributes.put(pair.group(1),pair.group(2)));
                    assertEquals(cap.equals("none") ? "none" : "4.0",attributes.get("evidence_clip"));
                    assertEquals(config,DetectorConfig.fromMap(attributes));
                    modes.add(attributes.get("mode")); count++;
                }
                assertEquals(expected,count);
                assertTrue(modes.contains("score"));
                if (expected==4) assertEquals(Set.of("score","flag","event","active"),modes);
                assertTrue(sql.endsWith("WHERE time >= 0 AND time <= 200"));
            }
        }
    }
}
