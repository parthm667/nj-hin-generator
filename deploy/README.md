# Deploying the NJ HIN backend (Oracle Cloud Always Free VM)

`deploy/cloud-init.yaml` bootstraps an Ubuntu 24.04 aarch64 VM: Docker,
`docker compose` stack (`docker-compose.prod.yml`: PostGIS, API, worker, Caddy),
and a systemd timer running `autoupdate.sh` every 3 minutes.

* **Deploy** = push to `main`. The timer pulls, rebuilds and restarts.
* **One-off jobs** = add `deploy/run-once/NN-name.sh`; each runs exactly once.
* **Status without SSH**: https://hin-api.mhaske.com/_boot/ (update.log,
  ps.txt, compose.log, per-job logs, ingest-statewide.log).
* **On the VM**: `hin ps|logs [svc]|shell|psql|ingest [args]|sync`.

Data: `scripts/ingest_all_real_data.py` (NJGIN boundaries, NJDOT roads, NJDOT
crashes with route/milepost geolocation), cache on the `hindata` volume.
