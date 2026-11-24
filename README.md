# Del KID al dashboard en AWS

Práctica B7 · Módulo Cloud (MIAX). Pipeline de datos de fondos de inversión: captura automática de los
*Documentos de Datos Fundamentales* (KID) de tres gestoras españolas, extracción de datos con IA (Amazon
Bedrock, salidas estructuradas), carga en PostgreSQL, API HTTP y aplicación web, todo definido con Terraform
y desplegado desde GitHub Actions.

| | |
|---|---|
| **Grupo** | _COMPLETAR: nombres de las personas del grupo_ |
| **URL de la aplicación** | _COMPLETAR tras el despliegue (`terraform output web_url`)_ |
| **URL de la API** | _COMPLETAR tras el despliegue (`terraform output api_url`; documentación interactiva en `<url>/docs`)_ |
| **Repositorio** | https://github.com/Jegusq/Practica-AWS (compartido con el profesor) |

---

## 1. Arquitectura

```mermaid
flowchart LR
    subgraph Web["Webs públicas de las gestoras"]
        B[Bestinver]
        A[Azvalor]
        C[Cobas AM]
    end

    subgraph Batch["Pipeline por lotes (python -m kid / GitHub Actions)"]
        S[1 Scraper<br/>requests + BeautifulSoup]
        E[2 Extracción<br/>Bedrock Converse<br/>salidas estructuradas]
        L[3 Carga idempotente]
    end

    S3[(S3<br/>PDFs + manifiestos<br/>+ extracciones)]
    BR[[Amazon Bedrock<br/>Claude Haiku 5.5]]
    DB[(Aurora PostgreSQL<br/>Serverless v2)]
    API[Lambda + FastAPI<br/>Function URL]
    APP[Streamlit<br/>ECS Express Mode]
    U((Usuario))

    B & A & C --> S
    S -->|PDF + metadatos| S3
    S3 --> E
    E <--> BR
    E -->|JSON validado| S3
    S3 --> L
    L -->|RDS Data API| DB
    API -->|RDS Data API| DB
    API -.->|URL firmada 5 min| S3
    APP -->|HTTPS| API
    U --> APP
    U --> API
```

Infraestructura (Terraform, `infra/`): bucket S3, Aurora Serverless v2, Lambda + Function URL, ECR y servicio
ECS Express Mode. Despliegue (GitHub Actions, `.github/workflows/`): autenticación OIDC, estado en S3.

### Decisiones y su justificación

| Capa | Elección | Por qué |
|---|---|---|
| Captura | Python `requests` + BeautifulSoup, cliente propio "educado" | Las tres webs son HTML estático; no hace falta un navegador. Ver §2. |
| Almacenamiento | S3, clave `kids/<fuente>/<fondo>/<clase>/<sha256>.pdf` | El hash hace la clave inmutable e idempotente; metadatos de origen en el objeto y en el manifiesto. |
| Extracción | Bedrock **Converse** + `outputConfig.textFormat` (JSON Schema), **Claude Haiku 5.5** (perfil UE) | Salidas estructuradas reales (gramática restringida), no «devuelve JSON y parsea». Haiku: ~20× más barato que Sonnet y suficiente para extraer campos. |
| Base de datos | **Aurora PostgreSQL Serverless v2 con auto-pausa (0 ACU)** + **RDS Data API** | Sin capacidad que pagar cuando no se usa; Data API = HTTPS + IAM, sin VPC/NAT/drivers para la Lambda. |
| API | FastAPI en Lambda (arm64) con Function URL | Pago por uso y sin API Gateway. Solo lectura. |
| Web | Streamlit en **ECS Express Mode** | Igual que la referencia: ALB + HTTPS + autoescalado sin configurarlos a mano. |
| IaC | Terraform ≥ 1.10, estado en S3 con `use_lockfile` | Sin DynamoDB; bloqueo nativo de S3. |
| CI/CD | GitHub Actions + **OIDC** | Sin claves de AWS en GitHub. |

Se ha seguido la arquitectura de referencia del enunciado; las decisiones propias son el modelo, la
auto-pausa de Aurora (§7), el empaquetado determinista de la Lambda y las validaciones posteriores a la IA.

---

## 2. Captura (web scraping responsable)

`python -m kid scrape` — código en [`src/kid/scraping`](src/kid/scraping).

| Fuente | Cómo se obtiene el KID | KID |
|---|---|---|
| **Bestinver** | Índice de fondos → ficha → enlace «Datos fundamentales» | 22 |
| **Azvalor** | Índice (`/fondos/` redirige) → ficha → «Folleto simplificado (DFP)»; la URL lleva fecha, se lee del HTML | 5 |
| **Cobas AM** | Sitemap `product-sitemap.xml` → ficha → un bloque `article.download-docs` por clase → enlace «PRIIP» (PDF en un bucket S3 público de Cobas) | 22 |

Total descubierto el 22‑11‑2026: **49 KID de 3 fuentes** (el enunciado pide ≥ 15 de ≥ 2). Las webs cambian: cada
parser tiene tests con fragmentos HTML y el scraper **no se detiene** ante un enlace roto (lo anota en el
manifiesto con `estado: error`).

**Buenas prácticas implementadas** (`scraping/http.py`, `scraping/robots.py`):

- **Se identifica**: `User-Agent: KIDPipelineBot/<ver> (proyecto academico MIAX; contacto: <KID_CONTACT>)`.
- **Respeta `robots.txt`** con la semántica de RFC 9309 (comodines `*` y `$`, la regla más larga gana). Se
  implementa a mano porque `urllib.robotparser` **no entiende `*` ni `$`**.
- **Ritmo limitado**: 2 s entre peticiones al mismo sitio; reintentos con espera exponencial y `Retry-After`.
- **Idempotente**: el PDF se guarda con su SHA‑256; descargar dos veces no duplica nada.

> **Nota sobre Bestinver y `robots.txt`.** Su `robots.txt` declara `Disallow: /*.pdf$`. Los enlaces que la
> propia ficha publica llevan un parámetro `?h=…` (cache‑buster), así que **no terminan en `.pdf`** y la regla
> literal no los alcanza; el scraper cumple exactamente lo que dice el fichero. Es una lectura literal que
> conviene conocer: si se prefiere ser más conservador, basta con excluir la fuente
> (`python -m kid scrape --sources cobas azvalor`; con solo esas dos hay 27 KID).
> Azvalor responde `418` a `/robots.txt`; por RFC 9309 (§2.3.1.3) un 4xx se trata como «sin restricciones».

**Manifiesto** (`manifests/<fecha>.jsonl` en S3): por cada PDF, `fuente`, `fondo_slug`, `clase_etiqueta`,
`url_pagina` (ficha), `url_pdf`, `descargado_en`, `http_status`, `content_type`, `tamano_bytes`, `sha256`,
`s3_key`, `scraper_version`, `estado`/`error`. Los metadatos de origen también van como *user metadata* del
objeto S3.

---

## 3. Extracción con IA

`python -m kid extract` — código en [`src/kid/extraction`](src/kid/extraction).

- API **Converse** de Bedrock con el PDF como bloque `document` y el esquema en
  `outputConfig.textFormat` (`type: json_schema`). El esquema se define **una vez en Pydantic**
  ([`extraction/schema.py`](src/kid/extraction/schema.py)) y de él salen el esquema de Bedrock, la validación y la forma de la BD.
- **Restricciones de Bedrock aplicadas** (documentación oficial): todos los objetos con
  `additionalProperties: false`; máx. 12 propiedades opcionales y 16 de tipo unión; sin `minimum`/`maximum`.
  Por eso **todos los campos son `required` y los que pueden faltar son nulables**: el modelo devuelve `null`
  en vez de inventar (requisito del Anexo A). Hay un test que comprueba estas restricciones
  (`check_bedrock_compat`).
- **Campos**: los 10 del Anexo A (`isin`, `nombre_producto`, `nombre_clase`, `gestora`, `fecha_documento`,
  `sri`, `periodo_mantenimiento_anios`, `gastos_corrientes_pct`, `costes_entrada_pct`, `costes_salida_pct`) más
  `divisa`, `comision_exito_pct`, `objetivo_inversion`, `escenarios` (tabla de rentabilidad) y
  `distribucion_activos` (solo si el KID la indica con porcentajes).
- **Validación posterior** ([`extraction/validate.py`](src/kid/extraction/validate.py)): las salidas estructuradas
  garantizan la *forma*, no que el dato sea correcto. Se comprueba el dígito de control del ISIN (ISO 6166), el
  SRI 1‑7, rangos de porcentajes, coherencia de la distribución de activos y que el ISIN coincida con el que da
  la web. Un dato inválido pasa a `null` y queda un **aviso** (se guarda también la respuesta original sin sanear).
- **Idempotente y barato**: el resultado se guarda en `extractions/<modelo>/<sha256>.json`; reejecutar no vuelve a
  llamar al modelo (`--force` para repetir). Cambiar de modelo (`--model eu.anthropic.claude-sonnet-5-5`) crea
  otra carpeta, lo que permite **comparar la calidad de varios modelos** (ampliación).
- Si el PDF supera 4,5 MB (límite de Converse) se rechaza explícitamente antes de llamar al modelo.

---

## 4. Base de datos (PostgreSQL)

Esquema en [`src/kid/db/schema.sql`](src/kid/db/schema.sql) (idempotente):

```
documentos (id, sha256 UNIQUE, fuente, fondo_slug, clase_etiqueta, url_pagina, url_pdf, s3_key, descargado_en …)
   └─ kids (id, documento_id UNIQUE, isin, nombre_producto, gestora, gestora_clave, sri, gastos_corrientes_pct …,
            avisos jsonb, extraccion jsonb)
        ├─ escenarios (kid_id, escenario, periodo, valor_final_eur, rentabilidad_anual_pct)
        └─ distribucion_activos (kid_id, categoria, porcentaje)
vista kids_vigentes: la versión más reciente de cada ISIN
```

- **Sin duplicados**: `documentos.sha256` y `kids.documento_id` son `UNIQUE`; la carga es `INSERT … ON CONFLICT DO
  UPDATE` y los hijos se reemplazan en la misma transacción. Procesar el mismo documento N veces deja la base
  idéntica (hay tests).
- `gestora_clave` normaliza variantes del nombre («BESTINVER GESTION SGIIC, S.A.» = «Bestinver Gestión, S.A.,
  S.G.I.I.C.») para agrupar correctamente.
- Acceso por **RDS Data API**; si Aurora está en pausa se reanuda sola con la primera petición y el cliente
  espera/reintenta (`DatabaseResumingException`).

---

## 5. API

FastAPI ([`src/kid/api`](src/kid/api)) en Lambda con Function URL. Solo lectura. Documentación interactiva en `/docs`.

| Endpoint | Descripción |
|---|---|
| `GET /kids` | Búsqueda con filtros (`q`, `gestora`, `fuente`, `isin`, `sri_min/max`, `gastos_max`, `solo_vigentes`), orden y paginación |
| `GET /kids/{id}` | Ficha completa: datos, escenarios, distribución de activos, avisos y trazabilidad |
| `GET /kids/{id}/pdf` | URL firmada de S3 (caduca en 5 min) del PDF original |
| `GET /isin/{isin}` | Todas las versiones de un ISIN |
| `GET /gestoras`, `GET /stats` | Agregados |
| `GET /documentos` | Trazabilidad: de dónde y cuándo se descargó cada PDF |
| `GET /health`, `GET /health/db` | Salud (la segunda despierta la base de datos) |

## 6. Aplicación web

Streamlit ([`app/`](app)) que **solo habla con la API** (nunca con la base de datos): *Resumen* (KPIs y gráficos),
*Explorador* (filtros, tabla y ficha con escenarios y distribución de activos), *Comparador* y *Trazabilidad*.

---

## 7. Coste estimado

Cálculo reproducible con `python scripts/estimate_cost.py` (precios de la lista oficial de AWS, región eu-west-3 (París), 22-11-2026; USD sin impuestos; con IVA 21 % ≈ +21 %).

| Componente | Coste |
|---|---|
| **Web (ALB + Fargate 0,25 vCPU/1 GB + IPv4)** | **≈ 1,74 USD por día encendida (~52 USD/mes)**: es el 80 % del gasto |
| Aurora Serverless v2 con auto‑pausa | ≈ 1–2 USD en total (sin auto‑pausa serían +51 USD/mes) |
| Bedrock, Claude Haiku 5.5 (49 KID) | ≈ 0,10 USD por pasada completa (Sonnet 5.5: ≈ 3,2 USD) |
| Secrets Manager, logs, ECR, S3, Lambda | < 1 USD |

| Escenario | Total estimado |
|---|---|
| A) Todo encendido 21 días | ≈ 43 USD (≈ 52 con IVA) |
| B) Web encendida solo 7 días (entrega, exposición y corrección) | ≈ 19 USD (≈ 23 con IVA) |
| C) Mínimo: web solo 3 días | ≈ 11 USD (≈ 13 con IVA) |

**Cómo controlarlo**: `deploy_web_app=false` (variable `DEPLOY_WEB_APP` / input del workflow) elimina el ALB y
Fargate; `terraform destroy` (workflow *Deploy → destroy*) borra todo; la auto‑pausa de Aurora evita pagar la
base cuando no se usa (`aurora_auto_pause_seconds` sube el tiempo antes de pausar el día de la demo para evitar
la espera de ~15 s); `BUDGET_ALERT_EMAIL` crea una alerta de AWS Budgets.

---

## 8. Cómo desplegar

**Requisitos**: cuenta de AWS, AWS CLI v2, Terraform ≥ 1.10, Python ≥ 3.12, repositorio en GitHub, y acceso a
Claude Haiku 5.5 en Bedrock (la primera vez AWS puede pedir aceptar los términos del modelo en la consola).

```powershell
# 0. Credenciales (nada de claves en el repo)
aws login                       # o: aws sso login --profile <perfil>

# 0b. Comprobaciones previas en la región elegida (París)
aws sts get-caller-identity
aws ec2 describe-vpcs --filters Name=isDefault,Values=true --region eu-west-3 --query "Vpcs[].VpcId"      # debe haber VPC por defecto
aws rds describe-db-engine-versions --engine aurora-postgresql --region eu-west-3 --query "DBEngineVersions[?starts_with(EngineVersion,'16.')].EngineVersion"
aws bedrock list-inference-profiles --region eu-west-3 --query "inferenceProfileSummaries[?contains(inferenceProfileId,'haiku-5-5')].inferenceProfileId"

# 1. Bootstrap, UNA vez: bucket del estado de Terraform + rol OIDC para GitHub Actions
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply
terraform -chdir=infra/bootstrap output
```

2. En GitHub → *Settings → Secrets and variables → Actions → **Variables*** crea:
   `AWS_REGION` (`eu-west-3`), `AWS_ROLE_ARN` (`deploy_role_arn`), `TF_STATE_BUCKET` (`tf_state_bucket`) y,
   opcionalmente, `KID_CONTACT`, `DEPLOY_WEB_APP` y `BUDGET_ALERT_EMAIL`.
3. *Actions → Deploy → Run workflow* (o *push* a `main`). Crea S3, Aurora, Lambda, ECR, construye y publica la
   imagen, crea el servicio ECS Express y aplica el esquema SQL. El resumen del job muestra las URLs.
4. Cargar los datos: *Actions → Pipeline de datos → Run workflow*, o en local:

```powershell
pip install -e .
$env:AWS_REGION = "eu-west-3"
$env:KID_BUCKET = "<bucket>"; $env:DB_CLUSTER_ARN = "<arn>"; $env:DB_SECRET_ARN = "<arn>"
python -m kid all                    # scrape + extract + migrate + load
# o por pasos: python -m kid scrape | extract | migrate | load   (--help en cada uno)
```

5. Abrir la `web_url` y la `api_url` (`/docs`). Para borrarlo todo: *Deploy → Run workflow → destroy*.

Orden interno del despliegue de la web: ECR → `docker push` → servicio ECS (la imagen debe existir antes).

---

## 9. Seguridad y privacidad

- **Sin credenciales en el repositorio**: GitHub Actions asume un rol por OIDC (restringido a este repositorio);
  la contraseña de Aurora la genera y custodia Secrets Manager; `.gitignore` excluye `.env`, `*.tfstate`, etc.
- **Los datos no son públicos salvo vía API/app**: el bucket tiene el acceso público bloqueado, cifrado y solo
  TLS; los PDFs se sirven con URLs firmadas de 5 minutos; Aurora no es accesible desde Internet (solo Data API).
- **Mínimo privilegio**: la Lambda solo puede consultar *este* cluster, leer *este* secreto y firmar lecturas de
  `kids/*`; la API solo ejecuta `SELECT`.
- El rol de despliegue usa `PowerUserAccess` + IAM acotado a roles `kid-*` (se puede afinar más).

## 10. Desarrollo local y tests

```powershell
python -m venv .venv ; .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
ruff check . ; ruff format --check .
pytest                                  # los tests de BD se omiten si no hay KID_TEST_DSN
```

Los tests de SQL, carga idempotente y API necesitan un PostgreSQL (en CI es un servicio `postgres:16`). Sin
Docker se puede usar PGlite: `npm i @electric-sql/pglite @electric-sql/pglite-socket` y
`npx pglite-server -p 54329 -m 4`, luego
`$env:KID_TEST_DSN="postgresql://postgres:postgres@127.0.0.1:54329/postgres?sslmode=disable"`.
Para ver la app sin AWS: `scripts/seed_demo.py` (datos **sintéticos**, solo contra una base local), API con
`KID_DB_DSN` y `streamlit run app/streamlit_app.py` con `API_URL=http://127.0.0.1:8000`.

Terraform: `terraform fmt`, `validate` y **9 tests con un provider de AWS simulado** (`infra/main/tests/plan.tftest.hcl`:
web desactivada/sin imagen/con imagen, auto-pausa de Aurora, bucket privado, permisos de la Function URL, alerta de
presupuesto) que se ejecutan en el CI sin tocar AWS: `terraform -chdir=infra/main test`.

Cobertura: parsers de las tres webs, `robots.txt`, cliente HTTP, descarga e idempotencia, esquema de Bedrock,
validaciones, petición Converse (sin llamar a AWS), cliente Data API (con boto3 simulado, incl. reanudación de
Aurora), carga idempotente, consultas y endpoints de la API contra PostgreSQL real.

## 11. Limitaciones y mejoras

- Si una gestora cambia su web, el scraper lo registra pero hay que adaptar el parser.
- La extracción no se ha medido contra un conjunto de KID revisados a mano (mejora propuesta).
- La API es pública y de solo lectura; para uso real convendría un límite de peticiones (WAF/API Gateway).
- Alternativa de menor coste para la web: servirla desde la propia Lambda o desde S3 + CloudFront (≈ 0 USD/día
  frente a 1,74 USD/día de ALB + Fargate), a costa de dejar de usar Streamlit/ECS Express.

## 12. Uso de asistentes de programación

Este proyecto se ha desarrollado con ayuda de **Claude Code**. Las decisiones de arquitectura, los límites de
Bedrock, el modelo de datos y las pruebas están documentados arriba para poder explicarlos y defenderlos.
