package edu.course.periodicad;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import org.apache.iotdb.isession.SessionDataSet;
import org.apache.iotdb.session.Session;
import org.apache.tsfile.enums.TSDataType;
import org.apache.tsfile.read.common.RowRecord;
import org.apache.tsfile.write.record.Tablet;
import org.apache.tsfile.write.schema.MeasurementSchema;

/** Local preparation tools only; the anomaly CSV runner is not yet implemented. */
public final class SmokeRunner {
    private static final String DATABASE = "root.periodic_ad";
    record Point(long timestamp, double value) {}

    static List<Point> readCsv(Path path) throws Exception {
        List<String> lines = Files.readAllLines(path);
        if (lines.isEmpty() || !lines.get(0).equals("timestamp_ms,source_timestamp,value"))
            throw new IllegalArgumentException("Expected processed CSV header");
        List<Point> points = new ArrayList<>();
        long previous = 0;
        long step = 0;
        for (int i = 1; i < lines.size(); i++) {
            String[] fields = lines.get(i).split(",", -1);
            if (fields.length != 3) throw new IllegalArgumentException("Invalid CSV row " + (i + 1));
            long timestamp = Long.parseLong(fields[0]);
            double value = Double.parseDouble(fields[2]);
            if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite row " + (i + 1));
            if (!points.isEmpty()) {
                long gap = Math.subtractExact(timestamp, previous);
                if (gap <= 0) throw new IllegalArgumentException("Timestamp order at row " + (i + 1));
                if (points.size() == 1) step = gap;
                if (gap != step) throw new IllegalArgumentException("Non-fixed grid at row " + (i + 1));
            }
            points.add(new Point(timestamp, value));
            previous = timestamp;
        }
        if (points.isEmpty()) throw new IllegalArgumentException("No input data");
        return points;
    }

    private static void show(Session session, String sql) throws Exception {
        System.out.println("SQL: " + sql);
        try (SessionDataSet result = session.executeQueryStatement(sql)) {
            System.out.println(String.join("\t", result.getColumnNames()));
            while (result.hasNext()) System.out.println(result.next());
        }
    }

    private static boolean listed(Session session, String sql, String name) throws Exception {
        boolean found = false;
        try (SessionDataSet result = session.executeQueryStatement(sql)) {
            while (result.hasNext()) {
                RowRecord row = result.next();
                if (!row.getFields().isEmpty()
                        && name.equalsIgnoreCase(row.getFields().get(0).getStringValue())) found = true;
            }
        }
        return found;
    }

    private static void ensureSchema(Session session, String device) throws Exception {
        if (!listed(session, "SHOW DATABASES", DATABASE))
            session.executeNonQueryStatement("CREATE DATABASE " + DATABASE);
        if (!session.checkTimeseriesExists(device + ".value"))
            session.executeNonQueryStatement("CREATE TIMESERIES " + device
                    + ".value WITH DATATYPE=DOUBLE, ENCODING=PLAIN");
    }

    private static void ensureEcho(Session session) throws Exception {
        if (!listed(session, "SHOW FUNCTIONS", "periodic_echo"))
            session.executeNonQueryStatement("CREATE FUNCTION periodic_echo AS 'edu.course.periodicad.EchoUDTF'");
    }

    private static void insert(Session session, String device, List<Point> points) throws Exception {
        ensureSchema(session, device);
        Tablet tablet = new Tablet(device, List.of(new MeasurementSchema("value", TSDataType.DOUBLE)), 512);
        int row = 0;
        for (Point point : points) {
            tablet.addTimestamp(row, point.timestamp());
            tablet.addValue("value", row, point.value());
            row++;
            tablet.setRowSize(row);
            if (row == tablet.getMaxRowNumber()) {
                session.insertTablet(tablet, true);
                tablet.reset();
                row = 0;
            }
        }
        if (row > 0) session.insertTablet(tablet, true);
        System.out.println("Imported " + points.size() + " points to " + device + ".value");
    }

    private static void verify(Session session, String device, List<Point> expected, boolean echo) throws Exception {
        String expression = echo ? "periodic_echo(value)" : "value";
        int index = 0;
        try (SessionDataSet result = session.executeQueryStatement("SELECT " + expression + " FROM " + device)) {
            while (result.hasNext()) {
                RowRecord row = result.next();
                if (index >= expected.size()) throw new IllegalStateException("Extra database rows");
                Point point = expected.get(index);
                if (row.getTimestamp() != point.timestamp() || row.getFields().size() != 1
                        || row.getFields().get(0).getDataType() != TSDataType.DOUBLE)
                    throw new IllegalStateException("Timestamp/type mismatch at " + index);
                double actual = row.getFields().get(0).getDoubleV();
                if (!Double.isFinite(actual) || Double.compare(actual, point.value()) != 0)
                    throw new IllegalStateException("Value mismatch at " + point.timestamp());
                index++;
            }
        }
        if (index != expected.size()) throw new IllegalStateException("Missing database rows: " + index);
        System.out.println("PASS " + expression + ": " + index + " timestamps and values match CSV exactly");
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 0) throw new IllegalArgumentException(
                "Commands: smoke <fixture.csv> | import-csv <processed.csv> | verify-csv <processed.csv> | query <SQL> | unregister-echo");
        String command = args[0];
        if (!List.of("smoke", "import-csv", "verify-csv", "query", "unregister-echo").contains(command))
            throw new IllegalArgumentException("Unknown command: " + command);
        int required = command.equals("unregister-echo") ? 1 : 2;
        if (args.length != required) throw new IllegalArgumentException("Wrong argument count");
        List<Point> points = List.of("smoke", "import-csv", "verify-csv").contains(command)
                ? readCsv(Path.of(args[1])) : List.of();
        if (command.equals("query") && !args[1].stripLeading().toUpperCase(java.util.Locale.ROOT).matches("(?s)(SELECT|SHOW)\\b.*"))
            throw new IllegalArgumentException("query accepts SELECT or SHOW only");
        Session session = new Session.Builder().host("127.0.0.1").port(6667).username("root")
                .password(System.getenv().getOrDefault("IOTDB_PASSWORD", "root"))
                .sqlDialect("tree").zoneId(ZoneOffset.UTC).enableRedirection(false)
                .enableAutoFetch(false).maxRetryCount(0).build();
        try (session) {
            session.open(false);
            if (!"ms".equals(session.getTimestampPrecision()))
                throw new IllegalStateException("Database timestamp precision must be ms");
            switch (command) {
                case "smoke" -> {
                    show(session, "SHOW VERSION");
                    show(session, "SHOW CLUSTER");
                    insert(session, DATABASE + ".smoke", points);
                    ensureEcho(session);
                    show(session, "SELECT value, periodic_echo(value) FROM " + DATABASE + ".smoke");
                    verify(session, DATABASE + ".smoke", points, false);
                    verify(session, DATABASE + ".smoke", points, true);
                    verify(session, DATABASE + ".smoke", points, true);
                }
                case "import-csv" -> insert(session, DATABASE + ".nyc", points);
                case "verify-csv" -> {
                    verify(session, DATABASE + ".nyc", points, false);
                    verify(session, DATABASE + ".nyc", points, true);
                    show(session, "SELECT COUNT(value) FROM " + DATABASE + ".nyc");
                }
                case "query" -> show(session, args[1]);
                case "unregister-echo" -> {
                    if (listed(session, "SHOW FUNCTIONS", "periodic_echo")) {
                        session.executeNonQueryStatement("DROP FUNCTION periodic_echo");
                        System.out.println("Dropped only periodic_echo");
                    }
                }
                default -> throw new AssertionError(command);
            }
        }
    }
}
