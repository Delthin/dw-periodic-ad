-- Tree model only. Replace example with an imported device, retain ALL warmup history.
-- score_mode and mode are orthogonal: one scoring choice, four output projections.
-- raw tau=4 still controls FREEZE/GUARDED writes; selected threshold here is evidence_tau=3.
-- No WHERE time test-split filter before the UDF: calculate over full history first.
SELECT periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'mode'='score'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'mode'='flag'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'mode'='event',
                   'exit_ratio'='0.5', 'recovery_ms'='1800000'),
       periodic_ad(value, 'step_ms'='1800000', 'origin_ms'='1404172800000', 'period'='336',
                   'k'='6', 'window'='336', 'tau'='4', 'clip'='2', 'epsilon'='auto',
                   'strategy'='bounded', 'method'='periodic',
                   'score_mode'='ewma', 'evidence_span'='12', 'evidence_tau'='3', 'mode'='active',
                   'exit_ratio'='0.5', 'recovery_ms'='1800000')
FROM root.periodic_ad.example;
-- Each column is an independent detector instance; a repeated query starts from zero.
-- Omitting score_mode (or setting point) restores the original point score and tau threshold.
