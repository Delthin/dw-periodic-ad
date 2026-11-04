package edu.course.periodicad;

import java.io.BufferedWriter;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.apache.iotdb.isession.SessionDataSet;
import org.apache.tsfile.enums.TSDataType;
import org.apache.tsfile.read.common.RowRecord;
import static edu.course.periodicad.ExecutionEvidence.*;

/** Streaming correctness and fair local-client timing. Constructors do not start services. */
final class DatabaseExperiments {
    private static Scored udfScorePoint(RowRecord row,double tau) {
        var f=row.getFields();
        if(f.size()!=1 || f.get(0).getDataType()!=TSDataType.DOUBLE)
            throw new IllegalStateException("Expected UDF score DOUBLE at "+row.getTimestamp());
        double score=f.get(0).getDoubleV();
        return new Scored(row.getTimestamp(),score,score>tau);
    }
    static Scored nextLocal(DetectorCsv csv,PeriodicDetector detector) throws Exception {
        DetectorCsv.Point p;
        while((p=csv.next())!=null) {
            Detection d=detector.accept(p.timestamp(),p.value());
            if(d!=null) return new Scored(d.timestampMs(),d.score(),d.flag());
        }
        return null;
    }
    record Comparison(long rows,String localSha256,String databaseSha256,double maxAbsoluteError,double maxRelativeError) {}
    @FunctionalInterface interface Cursor { Scored next() throws Exception; }
    /** Independently supplied cursor makes matching, missing/extra rows and tolerance testable offline. */
    static Comparison compareStreams(Cursor local,Cursor database,BufferedWriter evidence) throws Exception {
        Digest a=new Digest(), b=new Digest(); double maxAbs=0,maxRel=0;
        evidence.write("timestamp_ms,csv_score,db_score,csv_flag,db_flag,absolute_error,relative_error,pass\n");
        while(true) {
            Scored x=local.next(), y=database.next();
            if(x==null && y==null) break;
            if(x==null || y==null) throw new IllegalStateException("Missing/extra database scoring rows at output row "+a.rows);
            double abs=Math.abs(x.score()-y.score());
            double relative=abs/Math.max(1e-300,Math.max(Math.abs(x.score()),Math.abs(y.score())));
            boolean pass=x.timestamp()==y.timestamp() && x.flag()==y.flag() && close(x.score(),y.score());
            evidence.write(x.timestamp()+","+x.score()+","+y.score()+","+x.flag()+","+y.flag()+","+abs+","+relative+","+pass+"\n");
            if(!pass) throw new IllegalStateException("CSV/UDF mismatch at "+x.timestamp()+" (database timestamp "+y.timestamp()+")");
            a.add(x); b.add(y); maxAbs=Math.max(maxAbs,abs); maxRel=Math.max(maxRel,relative);
        }
        if(a.rows==0) throw new IllegalStateException("No formal scores: extend input or reduce warmup parameters");
        return new Comparison(a.rows,a.finish(),b.finish(),maxAbs,maxRel);
    }
    static Comparison compareModes(DetectorCsv csv, DetectorConfig config, AlarmGateConfig gateConfig,
                                   SessionDataSet data, BufferedWriter scores, BufferedWriter alarms) throws Exception {
        PeriodicDetector detector = new PeriodicDetector(config);
        AlarmGate gate = new AlarmGate(gateConfig);
        AlarmGate.Result[] expected = new AlarmGate.Result[1];
        alarms.write("timestamp_ms,local_event,db_event,local_active,db_active,pass\n");
        return compareStreams(() -> {
            Scored point = nextLocal(csv, detector);
            expected[0] = point == null ? null : gate.accept(point.timestamp(), point.score());
            return point;
        }, () -> {
            if (!data.hasNext()) return null;
            RowRecord row = data.next();
            var f = row.getFields();
            if (f.size() != 4 || f.get(0).getDataType() != TSDataType.DOUBLE
                    || f.subList(1,4).stream().anyMatch(v -> v.getDataType() != TSDataType.BOOLEAN))
                throw new IllegalStateException("Expected UDF score/flag/event/active at " + row.getTimestamp());
            boolean event = f.get(2).getBoolV(), active = f.get(3).getBoolV();
            AlarmGate.Result local = expected[0];
            boolean pass = local != null && local.notification() == event && local.active() == active;
            alarms.write(row.getTimestamp()+","+(local == null ? "" : local.notification())+","+event+","+(local == null ? "" : local.active())+","+active+","+pass+"\n");
            if (!pass) throw new IllegalStateException("CSV/UDF event/active mismatch at " + row.getTimestamp());
            return new Scored(row.getTimestamp(), f.get(0).getDoubleV(), f.get(1).getBoolV());
        }, scores);
    }
    static void compare(Path input,Path configPath,Path output) throws Exception {
        Properties properties=DetectorRunner.properties(configPath); DetectorConfig config=DetectorConfig.fromProperties(properties);
        AlarmGateConfig gateConfig = AlarmGateConfig.fromMap(configMap(properties), config);
        DetectorDatabase.Input meta=DetectorDatabase.inspect(input,config);
        String device=DetectorDatabase.device(properties);
        Files.createDirectories(output);
        Path summary=output.resolve("comparison.json");
        try(DetectorDatabase db=new DetectorDatabase()) {
            db.prepare(input,device,config); db.verifyRaw(input,device,meta);
            Comparison[] comparisons=new Comparison[2];
            for(int query=0;query<2;query++) {
                try(DetectorCsv csv=new DetectorCsv(input);
                    SessionDataSet data=db.session.executeQueryStatement(DetectorDatabase.udfQuery(device,meta,config,gateConfig));
                    BufferedWriter writer=Files.newBufferedWriter(output.resolve("comparison_"+(query+1)+".csv"),StandardCharsets.UTF_8);
                    BufferedWriter alarms=Files.newBufferedWriter(output.resolve("alarms_"+(query+1)+".csv"),StandardCharsets.UTF_8)) {
                    comparisons[query]=compareModes(csv,config,gateConfig,data,writer,alarms);
                }
            }
            Comparison a=comparisons[0],b=comparisons[1];
            if(a.rows()!=b.rows() || !a.databaseSha256().equals(b.databaseSha256()))
                throw new IllegalStateException("Repeated UDF query retained/leaked state or changed its output");
            Files.writeString(summary,"{\n  \"passed\": true,\n  \"device\": "+json(device)+",\n  \"detector_config\": "+json(config.toString())+",\n  \"udf_query\": "+json(DetectorDatabase.udfQuery(device,meta,config,gateConfig))+",\n  \"input_first_ms\": "+meta.first()+",\n  \"input_last_ms\": "+meta.last()+",\n  \"raw_returned_rows\": "+meta.rows()+",\n  \"udf_returned_rows\": "+a.rows()+",\n  \"output_rows\": "+a.rows()+",\n  \"queries\": 2,\n  \"validated_modes\": [\"score\",\"flag\",\"event\",\"active\"],\n  \"all_event_active_rows_equal\": true,\n  \"independent_repeat_identical\": true,\n  \"abs_tolerance\": 1e-9,\n  \"rel_tolerance\": 1e-9,\n  \"max_absolute_error\": "+Math.max(a.maxAbsoluteError(),b.maxAbsoluteError())+",\n  \"max_relative_error\": "+Math.max(a.maxRelativeError(),b.maxRelativeError())+",\n  \"csv_sha256\": "+json(a.localSha256())+",\n  \"udf_sha256\": "+json(a.databaseSha256())+"\n}\n",StandardCharsets.UTF_8);
        } catch(Exception e) {
            Files.writeString(summary,"{\"passed\":false,\"device\":"+json(device)+",\"error\":"+json(e.toString())+"}\n",StandardCharsets.UTF_8);
            throw e;
        }
        System.out.println("PASS compare: "+summary);
    }
    private static Map<String,String> configMap(Properties properties) {
        Map<String,String> map = new HashMap<>();
        properties.forEach((k,v) -> map.put(k.toString(), v.toString()));
        return map;
    }
    record Timing(int round,boolean warmup,String order,String path,double elapsedMs,Double algorithmMs,
                  long rawReturnedRows,long udfReturnedRows,long outputRows,String sha256,
                  Long clientCpuNanos,long heapUsedBefore,long heapUsedAfter) {}
    private static long heapUsed() { return ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getUsed(); }
    private static long cpuTime() {
        var os=ManagementFactory.getOperatingSystemMXBean();
        return os instanceof com.sun.management.OperatingSystemMXBean bean ? bean.getProcessCpuTime() : -1;
    }
    static Timing timed(DetectorDatabase db,String device,DetectorDatabase.Input input,DetectorConfig config,
                        int round,boolean warmup,String order,boolean udf) throws Exception {
        PeriodicDetector detector=udf ? null : new PeriodicDetector(config);
        String sql=udf ? DetectorDatabase.udfScoreQuery(device,input,config) : DetectorDatabase.rawQuery(device,input);
        Digest digest=new Digest(); long rawRows=0,udfRows=0,algorithmNanos=0;
        long heapBefore=heapUsed(),cpuBefore=cpuTime();
        // Fair start: directly before issuing SQL. No CSV IO or disk evidence in this region.
        long start=System.nanoTime(),end,cpuAfter;
        try(SessionDataSet data=db.session.executeQueryStatement(sql)) {
            while(data.hasNext()) {
                RowRecord row=data.next();
                if(udf) { udfRows++; digest.add(udfScorePoint(row,config.scoreThreshold())); }
                else {
                    rawRows++;
                    if(row.getFields().size()!=1 || row.getFields().get(0).getDataType()!=TSDataType.DOUBLE)
                        throw new IllegalStateException("Expected raw DOUBLE value");
                    double value=row.getFields().get(0).getDoubleV();
                    long t=System.nanoTime();
                    Detection d=detector.accept(row.getTimestamp(),value);
                    algorithmNanos+=System.nanoTime()-t;
                    if(d!=null) digest.add(new Scored(d.timestampMs(),d.score(),d.flag()));
                }
            }
            end=System.nanoTime();
            cpuAfter=cpuTime();
        }
        long elapsed=end-start;
        if(!udf && rawRows!=input.rows()) throw new IllegalStateException("Raw query count changed during benchmark");
        if(digest.rows==0) throw new IllegalStateException("No scoring points for benchmark");
        return new Timing(round,warmup,order,udf?"A_udf":"B_raw_java",elapsed/1e6,udf?null:algorithmNanos/1e6,
                rawRows,udfRows,digest.rows,digest.finish(),cpuBefore<0||cpuAfter<0?null:cpuAfter-cpuBefore,heapBefore,heapUsed());
    }
    static void requireSame(Timing expected,Timing actual) {
        if(expected.outputRows()!=actual.outputRows() || !expected.sha256().equals(actual.sha256()))
            throw new IllegalStateException("A/B/repeat timestamp-score-flag checksum mismatch: "+expected.path()+" vs "+actual.path());
    }
    static void writeTimings(Path path,List<Timing> rows) throws Exception {
        try(BufferedWriter writer=Files.newBufferedWriter(path,StandardCharsets.UTF_8)) {
            writer.write("round,warmup,order,path,end_to_end_ms,pure_algorithm_ms,raw_returned_rows,udf_returned_rows,output_rows,timestamp_score_flag_sha256,client_process_cpu_ns,client_heap_used_before_bytes,client_heap_used_after_bytes\n");
            for(Timing t:rows) writer.write(t.round()+","+t.warmup()+","+t.order()+","+t.path()+","+t.elapsedMs()+","+(t.algorithmMs()==null?"":t.algorithmMs())+","+t.rawReturnedRows()+","+t.udfReturnedRows()+","+t.outputRows()+","+t.sha256()+","+(t.clientCpuNanos()==null?"":t.clientCpuNanos())+","+t.heapUsedBefore()+","+t.heapUsedAfter()+"\n");
        }
    }
    private static String distribution(double[] values) {
        Arrays.sort(values);
        return "{\"median_ms\":"+Statistics.median(values)+",\"min_ms\":"+values[0]+",\"max_ms\":"+values[values.length-1]+",\"p25_ms\":"+quantile(values,0.25)+",\"p75_ms\":"+quantile(values,0.75)+"}";
    }
    private static double quantile(double[] sorted,double q) {
        double i=(sorted.length-1)*q; int low=(int)i;
        return sorted[low]+(sorted[Math.min(low+1,sorted.length-1)]-sorted[low])*(i-low);
    }
    static void benchmark(Path input,Path configPath,Path output,int repeats) throws Exception {
        if(repeats<5 || repeats>1000) throw new IllegalArgumentException("REPEATS must be in [5,1000]");
        Properties properties=DetectorRunner.properties(configPath); DetectorConfig config=DetectorConfig.fromProperties(properties);
        DetectorDatabase.Input meta=DetectorDatabase.inspect(input,config); String device=DetectorDatabase.device(properties);
        Files.createDirectories(output);
        List<Timing> timings=new ArrayList<>();
        try(DetectorDatabase db=new DetectorDatabase()) {
            db.prepare(input,device,config); db.verifyRaw(input,device,meta);
            Timing reference=null;
            for(int round=0;round<repeats+2;round++) {
                boolean ab=round%2==0; String order=ab?"AB":"BA";
                for(int position=0;position<2;position++) {
                    boolean udf=position==0?ab:!ab;
                    Timing t=timed(db,device,meta,config,round,round<2,order,udf);
                    timings.add(t);
                    if(reference==null) reference=t; else requireSame(reference,t);
                }
            }
            double[] a=timings.stream().filter(t->!t.warmup()&&t.path().equals("A_udf")).mapToDouble(Timing::elapsedMs).toArray();
            double[] b=timings.stream().filter(t->!t.warmup()&&t.path().equals("B_raw_java")).mapToDouble(Timing::elapsedMs).toArray();
            double[] algo=timings.stream().filter(t->!t.warmup()&&t.path().equals("B_raw_java")).mapToDouble(Timing::algorithmMs).toArray();
            Files.writeString(output.resolve("summary.json"),"{\n  \"passed\":true,\n  \"device\":"+json(device)+",\n  \"detector_config\":"+json(config.toString())+",\n  \"udf_query\":"+json(DetectorDatabase.udfScoreQuery(device,meta,config))+",\n  \"raw_query\":"+json(DetectorDatabase.rawQuery(device,meta))+",\n  \"input_first_ms\":"+meta.first()+",\n  \"input_last_ms\":"+meta.last()+",\n  \"warmup_rounds\":2,\n  \"measured_rounds\":"+repeats+",\n  \"raw_returned_rows\":"+meta.rows()+",\n  \"udf_returned_rows\":"+reference.outputRows()+",\n  \"output_rows\":"+reference.outputRows()+",\n  \"all_checksums_equal\":true,\n  \"timestamp_score_flag_sha256\":"+json(reference.sha256())+",\n  \"A_udf\":"+distribution(a)+",\n  \"B_raw_java\":"+distribution(b)+",\n  \"B_pure_algorithm\":"+distribution(algo)+",\n  \"udf_core_ms\":null,\n  \"wire_bytes\":null,\n  \"wire_bytes_note\":\"Actual protocol bytes not measured. Logical lower bounds below omit flag, framing, compression and metadata.\",\n  \"raw_timestamp_double_logical_payload_lower_bound_bytes\":"+Math.multiplyExact(meta.rows(),16L)+",\n  \"udf_timestamp_score_logical_payload_lower_bound_bytes\":"+Math.multiplyExact(reference.outputRows(),16L)+",\n  \"client_rss_bytes\":null,\n  \"server_rss_bytes\":null,\n  \"server_cpu_ns\":null,\n  \"heap_note\":\"Per-query JVM heap-used samples in timings.csv are not RSS or peak memory; CPU is client JVM process CPU, not server CPU.\",\n  \"timing_note\":\"SQL issuance through full consumption (dataset close excluded); no CSV writes. Both target one score stream. A invokes one score UDF and client derives flag as score>selected threshold, avoiding double server evaluation. compare separately validates BOOLEAN UDF. B algorithm is sum of accept() calls (two nanoTime probes per raw row). A core not instrumented.\",\n  \"cache_note\":\"Two warmup AB/BA rounds; server/client caches not flushed; subsequent measurements are warm-cache alternating AB/BA.\",\n  \"java_version\":"+json(System.getProperty("java.version"))+",\n  \"vm_name\":"+json(System.getProperty("java.vm.name"))+",\n  \"os\":"+json(System.getProperty("os.name")+" "+System.getProperty("os.arch"))+",\n  \"available_processors\":"+Runtime.getRuntime().availableProcessors()+",\n  \"client_max_heap_bytes\":"+Runtime.getRuntime().maxMemory()+"\n}\n",StandardCharsets.UTF_8);
        } catch(Exception e) {
            Files.writeString(output.resolve("summary.json"),"{\"passed\":false,\"device\":"+json(device)+",\"error\":"+json(e.toString())+"}\n",StandardCharsets.UTF_8);
            throw e;
        } finally { writeTimings(output.resolve("timings.csv"),timings); }
        System.out.println("PASS benchmark: "+output);
    }
}
