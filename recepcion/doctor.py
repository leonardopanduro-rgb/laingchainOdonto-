"""Configuration preflight. Prints names/status only, never credentials."""
import argparse
import asyncio
from pydantic import ValidationError
import httpx
from .config import Settings


async def check(online=False):
    try:
        config = Settings()
    except ValidationError as error:
        print('Configuración incompleta o inválida:')
        for item in error.errors(include_input=False):
            print(' - ' + str(item['loc'][0]).upper())
        return 1
    missing = []
    names = ['odonto_inbound_token', 'odonto_agent_token', 'reception_api_key', 'google_api_key']
    if config.chatwoot_url:
        names += ['chatwoot_token', 'chatwoot_webhook_secret', 'odonto_delivery_token', 'odonto_operator_token']
        if not config.odonto_channel_external_id: missing.append('ODONTO_CHANNEL_EXTERNAL_ID')
    missing += [name.upper() for name in names if not getattr(config, name).get_secret_value()]
    if missing:
        print('Falta completar: ' + ', '.join(missing))
        return 1
    print('Campos obligatorios: completos. Credenciales aún no validadas contra sus proveedores.')
    print('Modo: ' + ('Chatwoot' if config.chatwoot_url else 'HTTP de laboratorio'))
    if online:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                result = await client.get(config.odonto_backend_url.rstrip('/') + '/openapi.json')
                result.raise_for_status()
                properties = result.json()['components']['schemas']['OutboundClaimRequest']['properties']
                if 'outbound_ids' not in properties:
                    print('Backend incompatible: falta actualizar el filtro outbound_ids.')
                    return 1
            print('Contrato del backend: compatible. Esta prueba no llama al modelo ni envía mensajes.')
        except (httpx.HTTPError, KeyError, ValueError) as error:
            print('No se pudo verificar el backend: ' + type(error).__name__)
            return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='Consultar el contrato OpenAPI del backend')
    raise SystemExit(asyncio.run(check(parser.parse_args().online)))


if __name__ == '__main__':
    main()
