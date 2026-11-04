package edu.course.periodicad;

import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class AlarmCsvTest {
    @TempDir Path dir;

    @Test void eventsStreamsActualDetectorOutputWithoutChangingScoresOrOriginalColumns() throws Exception {
        Path input = dir.resolve("input.csv"), config = dir.resolve("detector.properties");
        Path scores = dir.resolve("scores.csv"), gateConfig = dir.resolve("gate.properties"), events = dir.resolve("events.csv");
        Files.writeString(input, DetectorCsv.INPUT_HEADER + "\n0,x,10\n10,x,10\n20,x,10\n30,x,10\n40,x,15\n50,x,12\n70,x,12\n80,x,12\n90,x,15\n100,x,13\n110,x,12\n120,x,12\n130,x,14\n");
        Files.writeString(config, "step_ms=10\nperiod=2\nk=1\nwindow=2\nepsilon=1\nmethod=fixed\nfreeze_at_ms=40\ntau=4\n");
        DetectorRunner.main(new String[]{"score", input.toString(), scores.toString(), config.toString()});
        String original = Files.readString(scores);
        assertEquals(DetectorCsv.OUTPUT_HEADER, Files.readAllLines(scores).get(0));
        Files.writeString(gateConfig, "step_ms=10\ntau=4\nexit_ratio=0.5\nrecovery_ms=20\n");
        DetectorRunner.main(new String[]{"events", scores.toString(), events.toString(), gateConfig.toString()});
        assertEquals(AlarmCsv.OUTPUT_HEADER + "\n40,5.0,true,true,true\n50,2.0,false,false,true\n70,2.0,false,false,true\n80,2.0,false,false,false\n90,5.0,true,true,true\n100,3.0,false,false,true\n110,2.0,false,false,true\n120,2.0,false,false,false\n130,4.0,false,false,false\n", Files.readString(events));
        assertEquals(original, Files.readString(scores));
        Path second = dir.resolve("second.csv");
        DetectorRunner.main(new String[]{"events", scores.toString(), second.toString(), gateConfig.toString()});
        assertEquals(Files.readString(events), Files.readString(second));
    }

    @Test void ewmaDiagnosticAndStandardCsvUseSelectedThresholdAcrossRunnerAndSql() throws Exception {
        Path input=dir.resolve("in.csv"),config=dir.resolve("ewma.properties"),scores=dir.resolve("scores.csv"),
                diagnostics=dir.resolve("diagnostics.csv"),events=dir.resolve("events.csv"),other=dir.resolve("other.csv");
        Files.writeString(input,DetectorCsv.INPUT_HEADER+"\n0,x,10\n10,x,10\n20,x,10\n30,x,10\n40,x,17\n50,x,17\n60,x,3\n");
        Files.writeString(config,"step_ms=10\nperiod=2\nk=1\nwindow=2\nepsilon=1\nmethod=fixed\nfreeze_at_ms=40\ntau=4\nscore_mode=ewma\nevidence_span=3\nevidence_tau=3\n");
        DetectorRunner.main(new String[]{"score",input.toString(),scores.toString(),config.toString()});
        DetectorRunner.main(new String[]{"score-diagnostics",input.toString(),diagnostics.toString(),config.toString()});
        DetectorRunner.main(new String[]{"events",scores.toString(),events.toString(),config.toString()});
        DetectorRunner.main(new String[]{"events",diagnostics.toString(),other.toString(),config.toString()});
        assertEquals(Files.readString(events),Files.readString(other));
        assertEquals(AlarmCsv.OUTPUT_HEADER+"\n40,3.5,true,true,true\n50,5.25,true,false,true\n60,0.875,false,false,false\n",Files.readString(events));
        var lines=Files.readAllLines(diagnostics);
        assertEquals(DetectorCsv.DIAGNOSTIC_HEADER,lines.get(0));
        assertTrue(lines.get(1).endsWith(",7.0,true,3.5,ewma,3.0"));
        for(int i=1;i<lines.size();i++) assertEquals(Files.readAllLines(scores).get(i),String.join(",",Arrays.copyOf(lines.get(i).split(",",-1),10)));
        var properties=DetectorRunner.properties(config);
        var c=DetectorConfig.fromProperties(properties);
        assertEquals(3,AlarmGateConfig.fromProperties(properties).tau());
        assertEquals(3,AlarmGateConfig.fromMap(Map.of(),c).tau());
        String attributes=DetectorDatabase.attributes(c,"event");
        assertTrue(attributes.contains("'score_mode'='ewma'"));
        assertTrue(attributes.contains("'evidence_span'='3'"));
        assertTrue(attributes.contains("'evidence_tau'='3.0'"));
        assertTrue(attributes.contains("'tau'='4.0'"));
        // Explicit diagnostics refuse event projection with a mismatched threshold and preserve output.
        Files.writeString(other,"keep");
        assertThrows(IllegalArgumentException.class,()->AlarmCsv.events(diagnostics,other,new AlarmGateConfig(10,0,4,.5,10)));
        assertEquals("keep",Files.readString(other));
    }
    private String row(long timestamp, String score) {
        return timestamp + ",0,0,0,0,1," + score + ",false,,0\n";
    }

    @Test void standaloneDefaultsNeedNoDetectorHistorySettingsAndEmptyScoresAreValid() throws Exception {
        Path input = dir.resolve("scores.csv"), output = dir.resolve("events.csv"), config = dir.resolve("gate.properties");
        Files.writeString(config, "step_ms=10\ntau=4\n");
        Files.writeString(input, DetectorCsv.OUTPUT_HEADER + "\n" + row(0, "5") + row(10, "2") + row(20, "5"));
        DetectorRunner.main(new String[]{"events", input.toString(), output.toString(), config.toString()});
        assertEquals(AlarmCsv.OUTPUT_HEADER + "\n0,5.0,true,true,true\n10,2.0,false,false,false\n20,5.0,true,true,true\n", Files.readString(output));
        Files.writeString(input, DetectorCsv.OUTPUT_HEADER + "\n");
        DetectorRunner.main(new String[]{"events", input.toString(), output.toString(), config.toString()});
        assertEquals(AlarmCsv.OUTPUT_HEADER + "\n", Files.readString(output));
        assertThrows(IllegalArgumentException.class, () -> DetectorRunner.main(new String[]{"events"}));
    }

    @Test void malformedInputPreservesExistingOutputAndRemovesTemporaryFiles() throws Exception {
        Path input = dir.resolve("bad.csv"), output = dir.resolve("out.csv");
        var config = new AlarmGateConfig(10, 0, 4, 0.5, 20);
        String header = DetectorCsv.OUTPUT_HEADER + "\n";
        for (String content : new String[]{"bad-header\n", header + "0,5\n", header + row(0, "NaN"),
                header + row(0, "Infinity"), header + row(0, "-Infinity"), header + row(0, "bad"),
                header + row(0, "5") + row(0, "0"), header + row(10, "5") + row(0, "0"),
                header + row(1, "5"), header + "bad,0,0,0,0,1,0,false,,0\n"}) {
            Files.writeString(input, content); Files.writeString(output, "keep");
            assertThrows(IllegalArgumentException.class, () -> AlarmCsv.events(input, output, config));
            assertEquals("keep", Files.readString(output));
            try (var files = Files.list(dir)) {
                assertFalse(files.anyMatch(p -> p.getFileName().toString().startsWith(".periodic-events-")));
            }
        }
    }

    @Test void rejectsSameInputOutputIncludingFilesystemAliases() throws Exception {
        Path input = dir.resolve("scores.csv");
        String content = DetectorCsv.OUTPUT_HEADER + "\n" + row(0, "5");
        Files.writeString(input, content);
        var config = new AlarmGateConfig(10, 0, 4, 0.5, 10);
        assertThrows(IllegalArgumentException.class, () -> AlarmCsv.events(input, input, config));
        assertThrows(IllegalArgumentException.class, () -> AlarmCsv.events(input, dir.resolve("./scores.csv"), config));
        Path symlink = dir.resolve("symlink.csv"), hardlink = dir.resolve("hardlink.csv");
        Files.createSymbolicLink(symlink, input); Files.createLink(hardlink, input);
        assertThrows(IllegalArgumentException.class, () -> AlarmCsv.events(input, symlink, config));
        assertThrows(IllegalArgumentException.class, () -> AlarmCsv.events(input, hardlink, config));
        assertEquals(content, Files.readString(input));
    }
}
