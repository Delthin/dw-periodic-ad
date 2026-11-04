package edu.course.periodicad;

import java.nio.file.*;
import java.util.*;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class RobustArIntegrationTest {
    @TempDir Path dir;
    private final Path root=Path.of("..");
    private DetectorConfig example() throws Exception {
        return DetectorConfig.fromProperties(DetectorRunner.properties(root.resolve("configs/robust-ar.properties")));
    }
    private Set<String> roundTrip(String sql,DetectorConfig config) {
        var columns=Pattern.compile("periodic_ad\\(value,([^)]*)\\)").matcher(sql); var modes=new HashSet<String>();
        while(columns.find()) {
            var attributes=new HashMap<String,String>();
            var pair=Pattern.compile("'([^']+)'='([^']*)'").matcher(columns.group(1));
            while(pair.find()) assertNull(attributes.put(pair.group(1),pair.group(2)));
            assertEquals("1,2,3,6,12",attributes.get("ar_lags"));
            assertEquals(config,DetectorConfig.fromMap(attributes));
            assertEquals(config.scoreThreshold(),AlarmGateConfig.fromMap(attributes,config).tau());
            assertTrue(modes.add(attributes.get("mode")));
        }
        return modes;
    }
    @Test void completeModelAndContextRoundTripAllDatabaseSqlModesWithoutConnection() throws Exception {
        var config=example(); var input=new DetectorDatabase.Input(0,100,101);
        var gate=AlarmGateConfig.fromMap(Map.of(),config);
        assertEquals(Set.of("score","flag","event","active"),roundTrip(DetectorDatabase.udfQuery("root.periodic_ad.test",input,config,gate),config));
        assertEquals(Set.of("score","flag"),roundTrip(DetectorDatabase.udfQuery("root.periodic_ad.test",input,config),config));
        assertEquals(Set.of("score"),roundTrip(DetectorDatabase.udfScoreQuery("root.periodic_ad.test",input,config),config));
        assertEquals(Set.of("score","flag","event","active"),roundTrip(Files.readString(root.resolve("sql/robust-ar.sql")),config));
    }
    @Test void cliPropertiesDiagnosticsAndEventsMatchPythonFixtureWithoutSchemaChanges() throws Exception {
        Path fixtures=root.resolve("detector-core/src/test/resources/robust-ar");
        Path input=fixtures.resolve("input.csv"), config=root.resolve("configs/robust-ar.properties"),
                scores=dir.resolve("scores.csv"), diagnostics=dir.resolve("diagnostics.csv"), events=dir.resolve("events.csv");
        DetectorRunner.main(new String[]{"score",input.toString(),scores.toString(),config.toString()});
        DetectorRunner.main(new String[]{"score-diagnostics",input.toString(),diagnostics.toString(),config.toString()});
        DetectorRunner.main(new String[]{"events",scores.toString(),events.toString(),config.toString()});
        var standard=Files.readAllLines(scores); var diagnostic=Files.readAllLines(diagnostics);
        var reference=Files.readAllLines(fixtures.resolve("context-ewma6.csv")); var alarms=Files.readAllLines(events);
        assertEquals(reference.size(),standard.size()); assertEquals(reference.size(),alarms.size());
        assertEquals(DetectorCsv.OUTPUT_HEADER,standard.get(0)); assertEquals(DetectorCsv.DIAGNOSTIC_HEADER,diagnostic.get(0));
        assertEquals(AlarmCsv.OUTPUT_HEADER,alarms.get(0));
        for(int i=1;i<standard.size();i++) {
            var a=standard.get(i).split(",",-1); var d=diagnostic.get(i).split(",",-1); var e=reference.get(i).split(",",-1);
            assertEquals(10,a.length); assertEquals(15,d.length);
            assertEquals(standard.get(i),String.join(",",Arrays.copyOf(d,10)));
            assertEquals(Long.parseLong(e[0]),Long.parseLong(a[0]));
            assertEquals(Double.parseDouble(e[6]),Double.parseDouble(a[6]),2e-9);
            assertEquals(Boolean.parseBoolean(e[7]),Boolean.parseBoolean(a[7]));
            assertEquals(Double.parseDouble(e[8]),Double.parseDouble(d[10]),2e-8);
            assertEquals(Boolean.parseBoolean(e[9]),Boolean.parseBoolean(d[11]));
            assertEquals(a[6],d[12]); assertEquals("ewma",d[13]); assertEquals("0.4",d[14]);
            assertEquals(a[0],alarms.get(i).split(",")[0]); assertEquals(a[7],alarms.get(i).split(",")[2]);
        }
    }
}
