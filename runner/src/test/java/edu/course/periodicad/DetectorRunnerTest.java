package edu.course.periodicad;

import java.nio.file.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class DetectorRunnerTest {
    @TempDir Path dir;
    @Test void scoreAndBatchStreamSameValuesAndSkipInvalidValues() throws Exception {
        Path input=dir.resolve("in.csv"), config=dir.resolve("config.properties"), a=dir.resolve("a.csv"), b=dir.resolve("b.csv");
        Files.writeString(config,"step_ms=10\norigin_ms=0\nperiod=2\nk=1\nwindow=2\nepsilon=auto\n");
        Files.writeString(input,DetectorCsv.INPUT_HEADER+"\n0,x,10\n10,x,20\n20,x,10\n30,x,20\n40,x,10\n50,x,null\n70,x,nan\n80,x,11\n");
        DetectorRunner.main(new String[]{"score",input.toString(),a.toString(),config.toString()});
        Path jobs=dir.resolve("jobs.tsv");
        Files.writeString(jobs,input+"\t"+b+"\t"+config+"\n");
        DetectorRunner.main(new String[]{"batch",jobs.toString()});
        assertEquals(Files.readString(a),Files.readString(b));
        var lines=Files.readAllLines(a);
        assertEquals(DetectorCsv.OUTPUT_HEADER,lines.get(0)); assertEquals(3,lines.size());
        assertTrue(lines.get(1).startsWith("40,10.0,10.0,0.0,0.0,1.0E-9,0.0,false,10.0,0"));
    }
    @Test void failedScorePreservesExistingOutput() throws Exception {
        Path input=dir.resolve("bad.csv"), output=dir.resolve("out.csv");
        Files.writeString(input,DetectorCsv.INPUT_HEADER+"\n0,x,1\n0,x,null\n"); Files.writeString(output,"keep");
        var config=DetectorConfig.fromMap(java.util.Map.of("step_ms","1","period","2"));
        assertThrows(IllegalArgumentException.class,()->DetectorCsv.score(input,output,config));
        assertEquals("keep",Files.readString(output));
    }
}
