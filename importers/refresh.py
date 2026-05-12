"""
importers/refresh.py — orquestrador dos importers HubSpot.

Roda hubspot_closer e hubspot_growth em subprocessos isolados, preservando
HUBSPOT_TOKEN no env. Cada subprocesso lê o token e grava o parquet
correspondente em data/hubspot/.

Uso:
    python -m importers.refresh
"""

import datetime
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

HUBSPOT_PROD_ENV = {**os.environ, "HUBSPOT_TOKEN": os.environ.get("HUBSPOT_TOKEN", "")}


def run(module: str) -> None:
    logging.info(f"Rodando {module}...")
    result = subprocess.run(
        [sys.executable, "-m", module],
        capture_output=True,
        text=True,
        env=HUBSPOT_PROD_ENV,
    )
    if result.returncode != 0:
        logging.error(result.stderr)
        raise RuntimeError(f"{module} falhou")
    logging.info(f"{module} concluído")


if __name__ == "__main__":
    run("importers.hubspot_closer")
    run("importers.hubspot_growth")
    logging.info("Atualização concluída: " + str(datetime.datetime.now()))
