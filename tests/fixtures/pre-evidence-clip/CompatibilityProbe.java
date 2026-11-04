import edu.course.periodicad.*;
import java.util.*;

/** Compares the inherited dirty source snapshot, not Git HEAD, with the changed core. */
public class CompatibilityProbe {
    public static void main(String[] args) {
        // Exactly the five legacy methods; ROBUST_AR requires a model and has separate tests.
        for (var method:List.of(DetectorConfig.Method.PERIODIC,DetectorConfig.Method.ROLLING_Z,
                DetectorConfig.Method.ROLLING_MAD,DetectorConfig.Method.FIXED,DetectorConfig.Method.NO_PERIOD))
            for (var strategy:DetectorConfig.Strategy.values())
                for (var mode:DetectorConfig.ScoreMode.values()) for (String constructor:List.of("legacy","map","properties")) {
            Map<String,String> p=new HashMap<>(Map.of("step_ms","10","origin_ms","100","period","2","k","2","window","4",
                    "tau","4","clip","2","score_mode",mode.name(),"method",method.name()));
            p.put("strategy",strategy.name()); p.put("freeze_at_ms","180"); p.put("evidence_span","12");
            Properties properties=new Properties(); properties.putAll(p);
            DetectorConfig c=switch (constructor) {
                case "legacy" -> mode==DetectorConfig.ScoreMode.POINT
                        ? new DetectorConfig(10,100,2,2,4,4,2,null,strategy,method,180L)
                        : new DetectorConfig(10,100,2,2,4,4,2,null,strategy,method,180L,mode,12,3);
                case "map" -> DetectorConfig.fromMap(p);
                default -> DetectorConfig.fromProperties(properties);
            };
            PeriodicDetector detector=new PeriodicDetector(c);
            for (int i=0;i<200;i++) {
                Double value;
                if (i<8) value=10.0;
                else if (i%29==0) value=null;
                else if (i%31==0) value=Double.NaN;
                else value=i%17==0 ? 1e100 : i%19==0 ? -1e100 : i%7==0 ? 1_000_010.0 : i%3==0 ? -20.0 : 12.0;
                long timestamp=100+10L*(i+i/41); // grid-aligned gaps
                System.out.println(method+"/"+strategy+"/"+mode+"/"+constructor+"/"+i+":"+detector.accept(timestamp,value));
            }
            System.out.println("counts:"+detector.rows()+","+detector.scored()+","+detector.skipped()+","+detector.epsilon());
        }
    }
}
