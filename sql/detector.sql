-- Tree model, UTC epoch milliseconds. Deployment helper only re-registers periodic_ad.
CREATE FUNCTION periodic_ad AS 'edu.course.periodicad.PeriodicADUDTF';
-- Replace the example device with the device printed by import-detector/compare/benchmark.
-- Keep the complete historical range for warmup; do not value-filter input history.
SELECT periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='0', 'period'='336',
                   'k'='4', 'window'='336', 'tau'='4', 'clip'='3',
                   'epsilon'='auto', 'strategy'='bounded', 'method'='periodic', 'mode'='score'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='0', 'period'='336',
                   'k'='4', 'window'='336', 'tau'='4', 'clip'='3',
                   'epsilon'='auto', 'strategy'='bounded', 'method'='periodic', 'mode'='flag')
FROM root.periodic_ad.example;
