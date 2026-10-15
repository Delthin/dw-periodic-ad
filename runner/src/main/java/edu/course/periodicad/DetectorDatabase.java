package edu.course.periodicad;

import java.nio.file.Path;
import java.time.ZoneOffset;
import java.util.*;
import org.apache.iotdb.isession.SessionDataSet;
import org.apache.iotdb.session.Session;
import org.apache.tsfile.enums.TSDataType;
import org.apache.tsfile.read.common.RowRecord;
import org.apache.tsfile.write.record.Tablet;
import org.apache.tsfile.write.schema.MeasurementSchema;

/** Explicit local Tree/ms adapter. Never deletes data, and never overwrites an existing series. */
final class DetectorDatabase implements AutoCloseable {
    record Input(long first, long last, long rows) {}
    final Session session;
    DetectorDatabase() throws Exception {
        session = new Session.Builder().host("127.0.0.1").port(6667).username("root")
                .password(System.getenv().getOrDefault("IOTDB_PASSWORD","root"))
                .sqlDialect("tree").zoneId(ZoneOffset.UTC).enableRedirection(false)
                .enableAutoFetch(false).maxRetryCount(0).build();
        try {
            session.open(false);
            if (!"ms".equals(session.getTimestampPrecision())) throw new IllegalStateException("Database precision must be ms");
        } catch (Exception e) { try { session.close(); } catch (Exception close) { e.addSuppressed(close); } throw e; }
    }
    static String device(Properties properties) {
        String name=properties.getProperty("db_device",System.getenv().getOrDefault("PERIODIC_AD_DEVICE",""));
        return name.isBlank() ? "root.periodic_ad.detector_"+UUID.randomUUID().toString().replace("-","") : validateDevice(name);
    }
    static String validateDevice(String name) {
        if (!name.matches("root\\.periodic_ad\\.[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)*"))
            throw new IllegalArgumentException("db_device must be a simple device under root.periodic_ad.*");
        return name;
    }
    static Input inspect(Path path, DetectorConfig config) throws Exception {
        long count=0, first=0, last=0;
        try (DetectorCsv csv=new DetectorCsv(path)) {
            DetectorCsv.Point p;
            while ((p=csv.next())!=null) {
                config.phase(p.timestamp());
                if (count>0 && p.timestamp()<=last) throw new IllegalArgumentException("CSV timestamps must strictly increase");
                if (p.value()==null || !Double.isFinite(p.value())) throw new IllegalArgumentException("Database paths reject null/nonfinite values");
                if(count==0) first=p.timestamp();
                last=p.timestamp(); count++;
            }
        }
        if(count==0) throw new IllegalArgumentException("Empty CSV");
        return new Input(first,last,count);
    }
    boolean listed(String sql,String name) throws Exception {
        try(SessionDataSet data=session.executeQueryStatement(sql)) {
            while(data.hasNext()) {
                RowRecord row=data.next();
                if(!row.getFields().isEmpty() && name.equalsIgnoreCase(row.getFields().get(0).getStringValue())) return true;
            }
        }
        return false;
    }
    void register() throws Exception {
        if(listed("SHOW FUNCTIONS","periodic_ad")) session.executeNonQueryStatement("DROP FUNCTION periodic_ad");
        session.executeNonQueryStatement("CREATE FUNCTION periodic_ad AS 'edu.course.periodicad.PeriodicADUDTF'");
    }
    /** Existing series is read-only here. A new project series is populated once, after full validation. */
    void prepare(Path input, String device, DetectorConfig config) throws Exception {
        validateDevice(device);
        if(session.checkTimeseriesExists(device+".value")) return;
        importNew(input,device,config);
    }
    void importNew(Path input,String device,DetectorConfig config) throws Exception {
        validateDevice(device); inspect(input,config);
        if(session.checkTimeseriesExists(device+".value")) throw new IllegalStateException("Refusing to overwrite existing series: "+device+".value");
        if(!listed("SHOW DATABASES","root.periodic_ad")) session.executeNonQueryStatement("CREATE DATABASE root.periodic_ad");
        session.executeNonQueryStatement("CREATE TIMESERIES "+device+".value WITH DATATYPE=DOUBLE, ENCODING=PLAIN");
        // Failed inserts are not retried or deleted. Re-run with a NEW device after inspecting the failure.
        Tablet tablet=new Tablet(device,List.of(new MeasurementSchema("value",TSDataType.DOUBLE)),1024);
        try(DetectorCsv csv=new DetectorCsv(input)) {
            DetectorCsv.Point p; int row=0;
            while((p=csv.next())!=null) {
                tablet.addTimestamp(row,p.timestamp()); tablet.addValue("value",row,p.value());
                row++; tablet.setRowSize(row);
                if(row==tablet.getMaxRowNumber()) { session.insertTablet(tablet,true); tablet.reset(); row=0; }
            }
            if(row>0) session.insertTablet(tablet,true);
        }
    }
    static String range(Input input) { return " WHERE time >= "+input.first()+" AND time <= "+input.last(); }
    static String rawQuery(String device,Input input) { return "SELECT value FROM "+validateDevice(device)+range(input); }
    static String attributes(DetectorConfig c,String mode) {
        return "'step_ms'='"+c.stepMs()+"','origin_ms'='"+c.originMs()+"','period'='"+c.period()+"','k'='"+c.k()+"','window'='"+c.window()+"','tau'='"+c.tau()+"','clip'='"+c.clip()+"','epsilon'='"+(c.epsilon()==null ? "auto" : c.epsilon())+"','strategy'='"+c.strategy().name().toLowerCase(Locale.ROOT)+"','method'='"+c.method().name().toLowerCase(Locale.ROOT)+"'"+(c.freezeAtMs()==null ? "" : ",'freeze_at_ms'='"+c.freezeAtMs()+"'")+",'mode'='"+mode+"'";
    }
    static String udfScoreQuery(String device,Input input,DetectorConfig config) {
        return "SELECT periodic_ad(value,"+attributes(config,"score")+") FROM "+validateDevice(device)+range(input);
    }
    static String udfQuery(String device,Input input,DetectorConfig config) {
        return "SELECT periodic_ad(value,"+attributes(config,"score")+"), periodic_ad(value,"+attributes(config,"flag")+") FROM "+validateDevice(device)+range(input);
    }
    /** Verifies specified existing series, including every timestamp/value, BEFORE timing or comparison. */
    void verifyRaw(Path input,String device,Input meta) throws Exception {
        long rows=0;
        try(DetectorCsv csv=new DetectorCsv(input); SessionDataSet data=session.executeQueryStatement(rawQuery(device,meta))) {
            while(data.hasNext()) {
                RowRecord row=data.next(); DetectorCsv.Point expected=csv.next();
                if(expected==null || row.getTimestamp()!=expected.timestamp() || row.getFields().size()!=1
                        || row.getFields().get(0).getDataType()!=TSDataType.DOUBLE
                        || Double.compare(row.getFields().get(0).getDoubleV(),expected.value())!=0)
                    throw new IllegalStateException("Raw database/CSV mismatch at returned row "+rows);
                rows++;
            }
            if(csv.next()!=null || rows!=meta.rows()) throw new IllegalStateException("Raw database/CSV row count mismatch");
        }
    }
    @Override public void close() throws Exception { session.close(); }
}
