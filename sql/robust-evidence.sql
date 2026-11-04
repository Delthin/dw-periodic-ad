-- Tree model only. Replace example with an existing imported device.
-- Keep full warmup history; filter the returned scores for evaluation, not the UDF input.
-- clip=2 bounds baseline writes, evidence_clip=4 bounds only signed EWMA input.
-- tau=4 still controls raw flags/updates; all four output modes use evidence_tau=3.
SELECT periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'evidence_clip'='4', 'mode'='score'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'evidence_clip'='4', 'mode'='flag'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'evidence_clip'='4', 'mode'='event',
                   'exit_ratio'='0.5', 'recovery_ms'='1800000'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'evidence_clip'='4', 'mode'='active',
                   'exit_ratio'='0.5', 'recovery_ms'='1800000')
FROM root.periodic_ad.example;
-- Omit evidence_clip or use 'evidence_clip'='none' for original EWMA behavior.
-- Each column owns independent detector/gate state; repeated queries start fresh.
