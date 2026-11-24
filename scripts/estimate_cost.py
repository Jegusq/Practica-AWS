"""Calculadora del coste de la practica en AWS (eu-west-3 Paris, precios de la lista oficial de AWS a 22-11-2026).

    python scripts/estimate_cost.py                      # escenarios por defecto
    python scripts/estimate_cost.py --days-up 10 --haiku-runs 5

Los precios son USD sin impuestos. Fuentes: AWS Price List API (pricing.us-east-1.amazonaws.com), regiones
EU (Paris): AmazonECS, AWSELB, AmazonVPC, AmazonRDS, AWSSecretsManager, AmazonCloudWatch, AmazonECR y
AmazonBedrockFoundationModels. Es una ESTIMACION: el consumo real depende del uso.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

# ---- Precios unitarios (USD) ---------------------------------------------------------------------
FARGATE_VCPU_H = 0.0486
FARGATE_GB_H = 0.0053
ALB_H = 0.02646
ALB_LCU_H = 0.0084
IPV4_H = 0.005
ACU_H = 0.14  # Aurora PostgreSQL Serverless v2
AURORA_GB_MONTH = 0.11
SECRET_MONTH = 0.40
LOGS_GB_INGEST = 0.57
ECR_GB_MONTH = 0.10
HAIKU_IN, HAIKU_OUT = 0.11, 0.55  # USD por millon de tokens, perfil geografico UE
SONNET_IN, SONNET_OUT = 2.20, 11.00


@dataclass
class Inputs:
    days_up: float  # dias con la infraestructura desplegada
    web_days: float  # dias con la web (ALB + Fargate) encendida
    cpu: float = 0.25  # vCPU de la tarea Fargate
    mem_gb: float = 1.0
    alb_ips: int = (
        3  # una IPv4 publica por zona de disponibilidad (la VPC por defecto tiene 3 en eu-west-3)
    )
    aurora_wakeups: int = 40  # veces que la base se reanuda
    aurora_hours_per_wakeup: float = 0.25  # 10 min de espera de auto-pausa + uso
    aurora_acu_when_active: float = 1.0
    db_gb: float = 0.5
    docs: int = 49
    in_tokens_per_doc: int = 12_000
    out_tokens_per_doc: int = 1_500
    sonnet_think_tokens: int = (
        2_000  # razonamiento adaptativo de Sonnet 5.5 (se factura como salida)
    )
    haiku_runs: int = 10
    sonnet_runs: int = 1


def web_cost_per_day(i: Inputs) -> dict[str, float]:
    return {
        "Fargate (tarea 0,25 vCPU / 1 GB)": 24 * (i.cpu * FARGATE_VCPU_H + i.mem_gb * FARGATE_GB_H),
        "ALB (hora + LCU minimas)": 24 * (ALB_H + ALB_LCU_H),
        "IPv4 publicas (ALB + tarea)": 24 * IPV4_H * (i.alb_ips + 1),
    }


def estimate(i: Inputs) -> dict[str, float]:
    web = sum(web_cost_per_day(i).values()) * i.web_days
    aurora = (
        i.aurora_wakeups * i.aurora_hours_per_wakeup * i.aurora_acu_when_active * ACU_H
        + i.db_gb * AURORA_GB_MONTH * i.days_up / 30
    )
    other = (
        SECRET_MONTH * i.days_up / 30  # secreto de la base de datos
        + 0.5  # CloudWatch Logs (centimos) y trafico de datos
        + ECR_GB_MONTH * 0.3 * i.days_up / 30  # imagen de ~300 MB
    )
    docs_in = i.docs * i.in_tokens_per_doc / 1e6
    docs_out = i.docs * i.out_tokens_per_doc / 1e6
    haiku = i.haiku_runs * (docs_in * HAIKU_IN + docs_out * HAIKU_OUT)
    sonnet = i.sonnet_runs * (
        docs_in * SONNET_IN + (docs_out + i.docs * i.sonnet_think_tokens / 1e6) * SONNET_OUT
    )
    return {
        "Web: Streamlit en ECS Express (ALB + Fargate + IPv4)": web,
        "Base de datos: Aurora Serverless v2 con auto-pausa": aurora,
        "Secrets Manager, logs, ECR, S3, Lambda": other,
        "Bedrock: extraccion con Claude Haiku 5.5": haiku,
        "Bedrock: una pasada de comparacion con Sonnet 5.5": sonnet,
    }


def show(title: str, i: Inputs) -> None:
    costs = estimate(i)
    total = sum(costs.values())
    print(f"\n=== {title}  (infra {i.days_up:g} dias, web {i.web_days:g} dias) ===")
    for name, value in costs.items():
        print(f"  {name:<56} {value:>7.2f} USD")
    print(
        f"  {'TOTAL (sin impuestos)':<56} {total:>7.2f} USD   | con IVA 21 %: {total * 1.21:.2f} USD"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--days-up", type=float, help="dias con la infraestructura desplegada")
    parser.add_argument(
        "--web-days", type=float, help="dias con la web encendida (por defecto = days-up)"
    )
    parser.add_argument("--haiku-runs", type=int, default=10)
    parser.add_argument("--sonnet-runs", type=int, default=1)
    args = parser.parse_args()

    if args.days_up:
        i = Inputs(
            args.days_up,
            args.web_days or args.days_up,
            haiku_runs=args.haiku_runs,
            sonnet_runs=args.sonnet_runs,
        )
        show("Personalizado", i)
        return

    print("Coste por DIA con la web encendida:")
    per_day = web_cost_per_day(Inputs(1, 1))
    for name, value in per_day.items():
        print(f"  {name:<40} {value:>6.2f} USD/dia")
    print(
        f"  {'TOTAL web':<40} {sum(per_day.values()):>6.2f} USD/dia  (~{sum(per_day.values()) * 30:.0f} USD/mes)"
    )

    show("A) Todo encendido hasta el final de la correccion", Inputs(days_up=21, web_days=21))
    show("B) Web solo en entrega, exposicion y correccion", Inputs(days_up=21, web_days=7))
    show(
        "C) Minimo: web solo 3 dias (demo + correccion corta)",
        Inputs(days_up=14, web_days=3, aurora_wakeups=25),
    )
    print("\nSi la base NO se pausara (0,5 ACU 24/7): +%.0f USD/mes." % (0.5 * ACU_H * 730))


if __name__ == "__main__":
    main()
