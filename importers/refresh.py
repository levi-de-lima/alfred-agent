"""
importers/refresh.py — orquestrador dos importers HubSpot.

Roda em sequência, em subprocessos isolados (preservando HUBSPOT_TOKEN
no env):

    1. importers.hubspot_closer        → data/hubspot/hs_closer_pipeline.parquet
    2. importers.hubspot_growth        → data/hubspot/hs_growth_leads.parquet
    3. importers.merge_growth_legado   → sobrescreve hs_growth_leads.parquet
                                         unindo com data/Base Legado Growth.xlsx

A ordem importa: o merge legado lê o parquet recém-gerado pelo importer
Growth e regrava o mesmo arquivo, acrescentando a coluna `fonte`
(`hubspot` | `pipedrive`).

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
    run("importers.merge_growth_legado")
    logging.info("Atualização concluída: " + str(datetime.datetime.now()))
