package edu.course.periodicad;

import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.util.HexFormat;

/** Constant-memory timestamp/score/flag digest, shared by both execution paths. */
final class ExecutionEvidence {
    record Scored(long timestamp,double score,boolean flag) {}
    static final class Digest {
        private final MessageDigest digest;
        private final ByteBuffer buffer=ByteBuffer.allocate(17);
        long rows;
        Digest() {
            try { digest=MessageDigest.getInstance("SHA-256"); }
            catch(java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
        }
        void add(Scored p) {
            if(!Double.isFinite(p.score()) || p.score()<0) throw new IllegalStateException("Nonfinite/negative score");
            buffer.clear(); buffer.putLong(p.timestamp()).putDouble(p.score()).put((byte)(p.flag()?1:0));
            digest.update(buffer.array()); rows++;
        }
        String finish() { return HexFormat.of().formatHex(digest.digest()); }
    }
    static boolean close(double expected,double actual) {
        return Double.isFinite(expected) && Double.isFinite(actual)
                && Math.abs(expected-actual)<=1e-9+1e-9*Math.max(Math.abs(expected),Math.abs(actual));
    }
    static String json(String text) {
        StringBuilder b=new StringBuilder("\"");
        for(char c:text.toCharArray()) {
            switch(c) {
                case '\\' -> b.append("\\\\");
                case '"' -> b.append("\\\"");
                case '\n' -> b.append("\\n");
                case '\r' -> b.append("\\r");
                case '\t' -> b.append("\\t");
                default -> { if(c<32) b.append(String.format("\\u%04x",(int)c)); else b.append(c); }
            }
        }
        return b.append('"').toString();
    }
}
