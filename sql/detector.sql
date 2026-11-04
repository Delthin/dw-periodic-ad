-- Tree model, UTC epoch milliseconds. Deployment helper only re-registers periodic_ad.
CREATE FUNCTION periodic_ad AS 'edu.course.periodicad.PeriodicADUDTF';
-- Replace the example device with the device printed by import-detector/compare/benchmark.
-- Keep the complete historical range for warmup; do not value-filter input history.
SELECT periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2',
                   'epsilon'='auto', 'strategy'='bounded', 'method'='periodic', 'mode'='score'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2',
                   'epsilon'='auto', 'strategy'='bounded', 'method'='periodic', 'mode'='flag')
FROM root.periodic_ad.example;

-- Optional shared notification policy; score/flag and detector updates remain unchanged.
-- event emits true only on entry; active reports the continuing event state.
-- Here recovery requires six consecutive safe 30-minute cells (gaps reset recovery).
SELECT periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2',
                   'epsilon'='auto', 'strategy'='bounded', 'method'='periodic', 'mode'='event',
                   'exit_ratio'='0.75', 'recovery_ms'='10800000'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2',
                   'epsilon'='auto', 'strategy'='bounded', 'method'='periodic', 'mode'='active',
                   'exit_ratio'='0.75', 'recovery_ms'='10800000')
FROM root.periodic_ad.example;
