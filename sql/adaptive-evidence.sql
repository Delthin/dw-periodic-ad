-- Tree model only; illustrative calibration, NOT a selected NAB variant.
-- Replace example with an existing imported device. Keep full warmup history.
-- floor=.5 affects only EWMA input, norm=2 affects only evidence_score/score.
-- All four modes use normalized evidence_tau=1.5; cap/norm=4/2=2 > 1.5.
-- tau=4 still controls raw flags and all history-update decisions.
SELECT periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='1.5', 'evidence_clip'='4',
                   'evidence_scale_floor'='0.5', 'evidence_normalizer'='2', 'mode'='score'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='1.5', 'evidence_clip'='4',
                   'evidence_scale_floor'='0.5', 'evidence_normalizer'='2', 'mode'='flag'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='1.5', 'evidence_clip'='4',
                   'evidence_scale_floor'='0.5', 'evidence_normalizer'='2', 'mode'='event',
                   'exit_ratio'='0.5', 'recovery_ms'='1800000'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='1.5', 'evidence_clip'='4',
                   'evidence_scale_floor'='0.5', 'evidence_normalizer'='2', 'mode'='active',
                   'exit_ratio'='0.5', 'recovery_ms'='1800000')
FROM root.periodic_ad.example;
-- Omit evidence_scale_floor/evidence_normalizer (or use 0/1) for old evidence behavior.
-- Each invocation/query owns fresh detector and gate state; no calibration is learned here.
