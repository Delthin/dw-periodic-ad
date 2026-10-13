package edu.course.periodicad;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class SmokeRunnerTest {
    @TempDir Path directory;
    private Path csv(String rows) throws Exception {
        Path file = directory.resolve("input.csv");
        Files.writeString(file, "timestamp_ms,source_timestamp,value\n" + rows);
        return file;
    }
    @Test void acceptsFixedGrid() throws Exception {
        var points = SmokeRunner.readCsv(csv("1000,fixture,10\n2000,fixture,-0.5\n3000,fixture,0\n"));
        assertEquals(3, points.size());
        assertEquals(2000, points.get(1).timestamp());
        assertEquals(-0.5, points.get(1).value());
    }
    @Test void rejectsDuplicateAndReversedTimes() throws Exception {
        for (String rows : new String[]{"1000,x,1\n1000,x,2\n", "2000,x,1\n1000,x,2\n"}) {
            Path path = csv(rows);
            assertThrows(IllegalArgumentException.class, () -> SmokeRunner.readCsv(path));
        }
    }
    @Test void rejectsNonFiniteAndNonFixedGrid() throws Exception {
        for (String rows : new String[]{"1000,x,NaN\n", "1000,x,Infinity\n", "1000,x,1\n2000,x,2\n4000,x,3\n"}) {
            Path path = csv(rows);
            assertThrows(IllegalArgumentException.class, () -> SmokeRunner.readCsv(path));
        }
    }
    @Test void rejectsEmptyOrWrongHeader() throws Exception {
        Path empty = csv("");
        assertThrows(IllegalArgumentException.class, () -> SmokeRunner.readCsv(empty));
        Path wrong = directory.resolve("wrong.csv");
        Files.writeString(wrong, "timestamp,value\n1000,10\n");
        assertThrows(IllegalArgumentException.class, () -> SmokeRunner.readCsv(wrong));
    }
}
