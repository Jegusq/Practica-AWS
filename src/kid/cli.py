"""Linea de comandos del pipeline:  python -m kid <comando>

scrape    descubre y descarga los KID de las gestoras -> S3 (o carpeta local) + manifiesto
extract   extrae los campos de cada PDF con Amazon Bedrock (salidas estructuradas)
load      carga lo extraido en PostgreSQL (idempotente)
migrate   crea/actualiza las tablas
all       scrape + extract + migrate + load
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings
from .scraping.sources import ALL_SOURCES
from .storage import open_store


def _common(p: argparse.ArgumentParser, settings: Settings) -> None:
    p.add_argument("--bucket", default=settings.bucket, help="bucket de S3 (o KID_BUCKET)")
    p.add_argument("--local-dir", help="usar esta carpeta en vez de S3 (desarrollo sin AWS)")
    p.add_argument("--region", default=settings.region)


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kid", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="capturar los KID de las webs de las gestoras")
    _common(scrape, settings)
    scrape.add_argument(
        "--sources", nargs="+", default=list(ALL_SOURCES), choices=list(ALL_SOURCES)
    )
    scrape.add_argument("--limit", type=int, help="maximo de KID por fuente (para pruebas)")
    scrape.add_argument(
        "--delay", type=float, default=2.0, help="segundos entre peticiones al mismo sitio"
    )
    scrape.add_argument(
        "--dry-run", action="store_true", help="solo lista lo que encontraria, sin descargar"
    )
    scrape.add_argument("--contact", default=settings.contact, help="contacto para el User-Agent")

    extract = sub.add_parser("extract", help="extraer datos de los PDFs con Bedrock")
    _common(extract, settings)
    extract.add_argument(
        "--model", default=settings.model_id, help="id del modelo / perfil de inferencia"
    )
    extract.add_argument(
        "--force", action="store_true", help="repetir aunque ya exista la extraccion"
    )
    extract.add_argument("--limit", type=int)

    load = sub.add_parser("load", help="cargar en PostgreSQL")
    _common(load, settings)
    load.add_argument("--model", default=settings.model_id, help="que extracciones cargar")

    sub.add_parser("migrate", help="aplicar el esquema de la base de datos")

    everything = sub.add_parser("all", help="scrape + extract + migrate + load")
    _common(everything, settings)
    everything.add_argument(
        "--sources", nargs="+", default=list(ALL_SOURCES), choices=list(ALL_SOURCES)
    )
    everything.add_argument("--limit", type=int)
    everything.add_argument("--delay", type=float, default=2.0)
    everything.add_argument("--model", default=settings.model_id)
    everything.add_argument("--contact", default=settings.contact)
    return parser


def cmd_scrape(args: argparse.Namespace) -> int:
    from .scraping import pipeline

    store = (
        None
        if args.dry_run and not (args.bucket or args.local_dir)
        else open_store(args.bucket, args.local_dir, args.region)
    )
    entries = pipeline.run(
        store,  # type: ignore[arg-type]
        args.sources,
        contact=args.contact,
        delay=args.delay,
        limit_per_source=args.limit,
        dry_run=args.dry_run,
    )
    return 0 if args.dry_run or any(e.estado == "ok" for e in entries) else 1


def cmd_extract(args: argparse.Namespace) -> int:
    from .extraction.bedrock import BedrockExtractor
    from .extraction.runner import extract_all

    store = open_store(args.bucket, args.local_dir, args.region)
    extractor = BedrockExtractor(args.model, args.region)
    report = extract_all(store, extractor, force=args.force, limit=args.limit)
    print(
        f"Extraidos: {report.extraidos} | ya existentes: {report.omitidos} | errores: {len(report.errores)} "
        f"| tokens in/out: {report.tokens_entrada}/{report.tokens_salida}"
    )
    for err in report.errores:
        print(f"  ERROR {err}")
    return 1 if report.errores else 0


def cmd_migrate(_: argparse.Namespace) -> int:
    from .db import connect
    from .db.migrate import migrate

    print(f"Esquema aplicado ({migrate(connect())} sentencias).")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    from .db import connect
    from .db.loader import load_all

    store = open_store(args.bucket, args.local_dir, args.region)
    report = load_all(connect(), store, args.model)
    print(
        f"Cargados: {report.cargados} | sin extraccion: {len(report.sin_extraccion)} | errores: {len(report.errores)}"
    )
    for item in report.sin_extraccion:
        print(f"  sin extraccion: {item}")
    for err in report.errores:
        print(f"  ERROR {err}")
    return 1 if report.errores else 0


def cmd_all(args: argparse.Namespace) -> int:
    args.dry_run = False
    steps = (cmd_scrape, cmd_extract, lambda a: cmd_migrate(a), cmd_load)
    for step in steps:
        rc = step(args)
        if rc:
            return rc
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = Settings.from_env()
    args = build_parser(settings).parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    commands = {
        "scrape": cmd_scrape,
        "extract": cmd_extract,
        "load": cmd_load,
        "migrate": cmd_migrate,
        "all": cmd_all,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
