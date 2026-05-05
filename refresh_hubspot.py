import subprocess, sys, pathlib, logging, datetime, os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
BASE = pathlib.Path(__file__).parent

HUBSPOT_PROD_ENV = {**os.environ, "HUBSPOT_TOKEN": os.environ.get("HUBSPOT_TOKEN", "")}


def run(script):
    logging.info(f"Rodando {script}...")
    result = subprocess.run([sys.executable, BASE / script], capture_output=True, text=True, env=HUBSPOT_PROD_ENV)
    if result.returncode != 0:
        logging.error(result.stderr)
        raise RuntimeError(f"{script} falhou")
    logging.info(f"{script} concluído")


if __name__ == "__main__":
    run("hubspot_importer.py")
    run("hubspot_growth_importer.py")
    logging.info("Atualização concluída: " + str(datetime.datetime.now()))
