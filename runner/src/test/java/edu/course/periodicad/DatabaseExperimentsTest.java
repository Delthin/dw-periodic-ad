package edu.course.periodicad;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;
import static edu.course.periodicad.ExecutionEvidence.*;

class DatabaseExperimentsTest {
    @TempDir Path dir;
    private DatabaseExperiments.Cursor cursor(Scored... points) {
        var iterator=List.of(points).iterator(); return ()->iterator.hasNext()?iterator.next():null;
    }
    private DatabaseExperiments.Comparison compare(Scored[] a,Scored[] b) throws Exception {
        try(var writer=new BufferedWriter(new StringWriter())) { return DatabaseExperiments.compareStreams(cursor(a),cursor(b),writer); }
    }
    @Test void comparesTimestampsScoresFlagsAndToleranceWithoutRetainingResults() throws Exception {
        Scored[] a={new Scored(10,5.395926075812762,true),new Scored(20,0,false)};
        var same=compare(a,a);
        assertEquals(2,same.rows()); assertEquals(same.localSha256(),same.databaseSha256());
        var tolerance=compare(a,new Scored[]{new Scored(10,a[0].score()+1e-10,true),a[1]});
        assertTrue(tolerance.maxAbsoluteError()>0);
        assertThrows(IllegalStateException.class,()->compare(a,new Scored[]{new Scored(11,a[0].score(),true),a[1]}));
        assertThrows(IllegalStateException.class,()->compare(a,new Scored[]{new Scored(10,a[0].score(),false),a[1]}));
        assertThrows(IllegalStateException.class,()->compare(a,new Scored[]{new Scored(10,5.5,true),a[1]}));
        assertThrows(IllegalStateException.class,()->compare(a,new Scored[]{a[0]}));
        assertThrows(IllegalStateException.class,()->compare(new Scored[]{a[0]},a));
        assertThrows(IllegalStateException.class,()->compare(new Scored[0],new Scored[0]));
    }
    @Test void databasePreflightAllowsGridGapsButRejectsMissingNonfiniteAndBadTimes() throws Exception {
        var c=DetectorConfig.fromMap(Map.of("step_ms","10","period","2"));
        Path input=dir.resolve("input.csv");
        Files.writeString(input,DetectorCsv.INPUT_HEADER+"\n0,x,1\n10,x,2\n40,x,3\n");
        assertEquals(new DetectorDatabase.Input(0,40,3),DetectorDatabase.inspect(input,c));
        for(String rows:List.of("0,x,1\n10,x,null\n","0,x,NaN\n","0,x,1\n0,x,2\n","0,x,1\n11,x,2\n")) {
            Files.writeString(input,DetectorCsv.INPUT_HEADER+"\n"+rows);
            assertThrows(IllegalArgumentException.class,()->DetectorDatabase.inspect(input,c));
        }
    }
    @Test void devicesAreProjectScopedAndSqlPreservesHistoricalRangeAndParameters() {
        assertEquals("root.periodic_ad.test_1",DetectorDatabase.validateDevice("root.periodic_ad.test_1"));
        for(String name:List.of("root.other.x","root.periodic_ad","root.periodic_ad.x;drop function x","root.periodic_ad.x.*"))
            assertThrows(IllegalArgumentException.class,()->DetectorDatabase.validateDevice(name));
        var c=DetectorConfig.fromMap(Map.of("step_ms","10","origin_ms","20","period","4","method","fixed","freeze_at_ms","100"));
        String sql=DetectorDatabase.udfQuery("root.periodic_ad.test",new DetectorDatabase.Input(20,200,19),c);
        assertTrue(sql.endsWith("FROM root.periodic_ad.test WHERE time >= 20 AND time <= 200"));
        assertTrue(sql.contains("'freeze_at_ms'='100'")); assertTrue(sql.contains("'origin_ms'='20'"));
        assertTrue(sql.contains("'mode'='score'")); assertTrue(sql.contains("'mode'='flag'"));
        assertFalse(sql.contains("value >"));
        String benchmarkSql=DetectorDatabase.udfScoreQuery("root.periodic_ad.test",new DetectorDatabase.Input(20,200,19),c);
        assertEquals(1,benchmarkSql.split("periodic_ad\\(",-1).length-1);
        assertFalse(benchmarkSql.contains("'mode'='flag'"));
    }
    @Test void digestDetectsDifferencesAndJsonEscapes() {
        var a=new Digest(); var b=new Digest();
        a.add(new Scored(1,2,false)); b.add(new Scored(1,2,true));
        assertNotEquals(a.finish(),b.finish());
        assertEquals("\"a\\n\\\"\\\\\"",json("a\n\"\\"));
        assertFalse(close(Double.NaN,0)); assertTrue(close(1e6,1e6+1e-4));
    }
    @Test void timingEvidenceContainsCountsAndUnavailableMeasurementsAreBlank() throws Exception {
        var timing=new DatabaseExperiments.Timing(0,false,"AB","A_udf",1.25,null,0,2,2,"abc",null,100,200);
        Path output=dir.resolve("timings.csv");
        DatabaseExperiments.writeTimings(output,List.of(timing));
        var lines=Files.readAllLines(output);
        assertEquals(2,lines.size());
        assertEquals("0,false,AB,A_udf,1.25,,0,2,2,abc,,100,200",lines.get(1));
    }
    @Test void checksumMismatchAndTooFewRepeatsFailBeforeDatabaseAccess() throws Exception {
        var a=new DatabaseExperiments.Timing(0,false,"AB","A_udf",1,null,0,2,2,"abc",null,0,0);
        var b=new DatabaseExperiments.Timing(0,false,"AB","B_raw_java",1,0.1,3,0,2,"def",null,0,0);
        assertThrows(IllegalStateException.class,()->DatabaseExperiments.requireSame(a,b));
        assertThrows(IllegalArgumentException.class,()->DatabaseExperiments.benchmark(dir.resolve("missing"),dir.resolve("missing"),dir,4));
    }
}
