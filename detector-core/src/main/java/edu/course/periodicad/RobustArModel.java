package edu.course.periodicad;

import java.util.Arrays;
import java.util.Map;
import java.util.stream.Collectors;

/** Immutable offline-fitted model. No fitting, labels, or mutable arrays escape this object. */
public final class RobustArModel {
    public static final int MAX_LAG = 1_000_000;
    public static final int MAX_COEFFICIENTS = 1024;
    private final int[] lags;
    private final double[] coefficients;
    private final double intercept, center, scale, lagClip;

    public RobustArModel(int[] lags, double[] coefficients, double intercept,
                         double center, double scale, double lagClip) {
        if (lags == null || coefficients == null || lags.length == 0
                || lags.length != coefficients.length || lags.length > MAX_COEFFICIENTS)
            throw new IllegalArgumentException("ar_lags/ar_coefficients require equal lengths in [1,1024]");
        this.lags = lags.clone(); this.coefficients = coefficients.clone();
        int previous = 0;
        for (int i=0; i<this.lags.length; i++) {
            if (this.lags[i] <= previous || this.lags[i] > MAX_LAG)
                throw new IllegalArgumentException("ar_lags must be unique, positive, ascending and <=1000000");
            finite(this.coefficients[i], "ar_coefficients"); previous = this.lags[i];
        }
        finite(intercept, "ar_intercept"); finite(center, "ar_center");
        positive(scale, "ar_scale"); positive(lagClip, "ar_lag_clip");
        this.intercept=intercept; this.center=center; this.scale=scale; this.lagClip=lagClip;
    }
    public int[] lags() { return lags.clone(); }
    public double[] coefficients() { return coefficients.clone(); }
    public double intercept() { return intercept; }
    public double center() { return center; }
    public double scale() { return scale; }
    public double lagClip() { return lagClip; }
    public int maxLag() { return lags[lags.length-1]; }
    public String lagsAttribute() { return Arrays.stream(lags).mapToObj(Integer::toString).collect(Collectors.joining(",")); }
    public String coefficientsAttribute() { return Arrays.stream(coefficients).mapToObj(Double::toString).collect(Collectors.joining(",")); }

    /** Explicit lag order, no vectorized/reassociated summation or fused multiply-add. */
    double predict(RingBuffer history) {
        double sum=intercept;
        for (int i=0; i<lags.length; i++) {
            // Like numpy.clip, saturate even an overflowed finite-input normalization before arithmetic checks.
            double z=(history.lag(lags[i])-center)/scale;
            double clipped=Math.max(-lagClip, Math.min(lagClip,z));
            sum=Statistics.finite(sum + Statistics.finite(coefficients[i]*clipped));
        }
        return Statistics.finite(center + Statistics.finite(scale*sum));
    }
    public static RobustArModel fromMap(Map<String,String> p) {
        String[] keys={"ar_lags","ar_coefficients","ar_intercept","ar_center","ar_scale","ar_lag_clip"};
        if (Arrays.stream(keys).noneMatch(p::containsKey)) return null;
        for (String key:keys) if (!p.containsKey(key)) throw new IllegalArgumentException("Missing "+key);
        int[] lags=Arrays.stream(p.get("ar_lags").split(",",-1)).map(String::trim).mapToInt(Integer::parseInt).toArray();
        double[] coefficients=Arrays.stream(p.get("ar_coefficients").split(",",-1)).map(String::trim).mapToDouble(Double::parseDouble).toArray();
        return new RobustArModel(lags,coefficients,Double.parseDouble(p.get("ar_intercept").trim()),
                Double.parseDouble(p.get("ar_center").trim()),Double.parseDouble(p.get("ar_scale").trim()),
                Double.parseDouble(p.get("ar_lag_clip").trim()));
    }
    private static void finite(double value,String key) {
        if (!Double.isFinite(value)) throw new IllegalArgumentException(key+" must be finite");
    }
    private static void positive(double value,String key) {
        finite(value,key);
        if (value<=0) throw new IllegalArgumentException(key+" must be positive");
    }
    @Override public boolean equals(Object other) {
        if (this==other) return true;
        if (!(other instanceof RobustArModel m)) return false;
        return Arrays.equals(lags,m.lags) && Arrays.equals(coefficients,m.coefficients)
                && Double.compare(intercept,m.intercept)==0 && Double.compare(center,m.center)==0
                && Double.compare(scale,m.scale)==0 && Double.compare(lagClip,m.lagClip)==0;
    }
    @Override public int hashCode() {
        return java.util.Objects.hash(Arrays.hashCode(lags),Arrays.hashCode(coefficients),intercept,center,scale,lagClip);
    }
}
