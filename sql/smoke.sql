-- Deployment probe; not the final anomaly function.
SHOW VERSION;
SHOW CLUSTER;
SHOW FUNCTIONS;
SELECT value, periodic_echo(value) FROM root.periodic_ad.smoke;
SELECT COUNT(value) FROM root.periodic_ad.nyc;
