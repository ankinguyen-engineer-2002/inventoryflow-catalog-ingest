"""Track B CLI entry points.

Mirrors the surface of track-a-jd-native/src/cli/:
  ingest          — full bronze + silver + gold pipeline
  ingest_dryrun   — bronze materialisation only
  enrich          — LLM cross-validation (audit mode)
  populate_models — derive vehicle_models from gold fitment
  bench           — fitment-query latency benchmark on Iceberg
"""
