"""Punto de entrada de AWS Lambda (Function URL, payload v2): handler = kid.api.lambda_handler.handler."""

from mangum import Mangum

from .main import create_app

handler = Mangum(create_app(), lifespan="off")
