package edu.course.periodicad;

import java.nio.file.*;
import java.util.*;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class AdaptiveEvidenceIntegrationTest {
    @TempDir Path dir;

    @Test void propertiesReachCliDiagnosticAndEventOutputsWithUnchangedSchemas() throws Exception {
        Path input=dir.resolve("input.csv"), config=dir.resolve("adaptive.properties"), scores=dir.resolve("scores.csv"),
                diagnostics=dir.resolve("diagnostics.csv"), events=dir.resolve("events.csv"), other=dir.resolve("other.csv");
        Files.writeString(input,DetectorCsv.INPUT_HEADER+"\n0,x,10\n10,x,10\n20,x,10\n30,x,10\n40,x,11.5\n50,x,12\n60,x,12\n70,x,12\n80,x,10\n90,x,10\n");
        String parameters="step_ms=10\nperiod=2\nk=1\nwindow=2\nepsilon=0.25\nmethod=fixed\nfreeze_at_ms=40\ntau=4\nscore_mode=ewma\nevidence_span=3\nevidence_tau=1.5\nevidence_clip=4\nevidence_scale_floor=0.5\nevidence_normalizer=2\n";
        Files.writeString(config,parameters);
        DetectorRunner.main(new String[]{"score",input.toString(),scores.toString(),config.toString()});
        DetectorRunner.main(new String[]{"score-diagnostics",input.toString(),diagnostics.toString(),config.toString()});
        DetectorRunner.main(new String[]{"events",scores.toString(),events.toString(),config.toString()});
        DetectorRunner.main(new String[]{"events",diagnostics.toString(),other.toString(),config.toString()});
        assertEquals(AlarmCsv.OUTPUT_HEADER+"\n40,0.75,false,false,false\n50,1.375,false,false,false\n60,1.6875,true,true,true\n70,1.84375,true,false,true\n80,0.921875,false,false,true\n90,0.4609375,false,false,false\n",Files.readString(events));
        assertEquals(Files.readString(events),Files.readString(other));
        var standard=Files.readAllLines(scores); var diagnostic=Files.readAllLines(diagnostics);
        assertEquals(DetectorCsv.OUTPUT_HEADER,standard.get(0));
        assertEquals(DetectorCsv.DIAGNOSTIC_HEADER,diagnostic.get(0));
        assertTrue(diagnostic.get(1).endsWith(",6.0,true,0.75,ewma,1.5"));
        for (int i=1;i<standard.size();i++) {
            assertEquals(10,standard.get(i).split(",",-1).length);
            String[] columns=diagnostic.get(i).split(",",-1);
            assertEquals(15,columns.length);
            assertEquals(standard.get(i),String.join(",",Arrays.copyOf(columns,10)));
        }
        // Standalone event projection must reject an unreachable normalized threshold as scoring does.
        Files.writeString(config,parameters.replace("evidence_tau=1.5","evidence_tau=2"));
        assertThrows(IllegalArgumentException.class,()->DetectorRunner.main(new String[]{"events",scores.toString(),dir.resolve("invalid.csv").toString(),config.toString()}));
    }

    @Test void sqlRoundTripsDefaultsAndAdaptiveParametersForEveryModeWithoutDatabase() {
        for (String scoring:List.of("point","ewma")) for (boolean adaptive:List.of(false,true)) for (String cap:List.of("none","4")) {
            var p=new HashMap<>(Map.of("step_ms","10","period","2","score_mode",scoring,"evidence_clip",cap));
            if (adaptive) p.putAll(Map.of("evidence_scale_floor","0.5","evidence_normalizer","2","evidence_tau","1.5"));
            var config=DetectorConfig.fromMap(p);
            var input=new DetectorDatabase.Input(0,200,21);
            var gate=AlarmGateConfig.fromMap(Map.of(),config);
            for (var query:List.of(
                    Map.entry(DetectorDatabase.udfQuery("root.periodic_ad.test",input,config,gate),4),
                    Map.entry(DetectorDatabase.udfScoreQuery("root.periodic_ad.test",input,config),1),
                    Map.entry(DetectorDatabase.udfQuery("root.periodic_ad.test",input,config),2))) {
                var columns=Pattern.compile("periodic_ad\\(value,([^)]*)\\)").matcher(query.getKey());
                var modes=new HashSet<String>(); int count=0;
                while (columns.find()) {
                    var attributes=new HashMap<String,String>();
                    var pair=Pattern.compile("'([^']+)'='([^']*)'").matcher(columns.group(1));
                    while (pair.find()) assertNull(attributes.put(pair.group(1),pair.group(2)));
                    assertEquals(adaptive ? "0.5" : "0.0",attributes.get("evidence_scale_floor"));
                    assertEquals(adaptive ? "2.0" : "1.0",attributes.get("evidence_normalizer"));
                    assertEquals(config,DetectorConfig.fromMap(attributes));
                    assertEquals(config.scoreThreshold(),AlarmGateConfig.fromMap(attributes,config).tau());
                    modes.add(attributes.get("mode")); count++;
                }
                assertEquals(query.getValue(),count);
                if (count==4) assertEquals(Set.of("score","flag","event","active"),modes);
            }
        }
    }

    @Test void checkedInAdaptiveExamplesRoundTripToTheSameConfigForAllFourSqlColumns() throws Exception {
        // Maven module cwd is runner; fixtures remain in the worktree, with no DB connection.
        Path root=Path.of("..");
        var config=DetectorConfig.fromProperties(DetectorRunner.properties(root.resolve("configs/adaptive-evidence.properties")));
        assertEquals(.5,config.evidenceScaleFloor()); assertEquals(2,config.evidenceNormalizer());
        assertEquals(4,config.evidenceClip()); assertEquals(1.5,config.scoreThreshold());
        String sql=Files.readString(root.resolve("sql/adaptive-evidence.sql"));
        var columns=Pattern.compile("periodic_ad\\(value,([^)]*)\\)").matcher(sql);
        var modes=new HashSet<String>();
        while (columns.find()) {
            var attributes=new HashMap<String,String>();
            var pair=Pattern.compile("'([^']+)'='([^']*)'").matcher(columns.group(1));
            while (pair.find()) assertNull(attributes.put(pair.group(1),pair.group(2)));
            assertEquals(config,DetectorConfig.fromMap(attributes));
            modes.add(attributes.get("mode"));
        }
        assertEquals(Set.of("score","flag","event","active"),modes);
    }
}
