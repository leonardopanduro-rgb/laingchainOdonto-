# Validación de la recepción LangChain — 13 de octubre de 2026

## Resultado actual

Código corregido y listo para configurar un piloto controlado. No activado en
producción: falta validar credenciales y el recorrido Gemini → Chatwoot → WhatsApp.
La guía GUIA_RECEPCION_CODIGO.md relaciona las tareas con sus funciones concretas.

| Comprobación | Resultado |
|---|---|
| Servicio: pruebas de unidad, escenarios LangChain y probes originales | **52 aprobadas**, 1 aviso externo, 19.21 s |
| Recorrido integrado con API OdontoFlow y PostgreSQL reales | **1 aprobado**, 23.68 s; Gemini y Chatwoot simulados |
| Backend antes del cambio | **494 aprobadas**, 670.62 s |
| Backend: selección de mensajes, validación y aislamiento de organizaciones | **9 aprobadas**, 14.73 s |
| Backend completo después del cambio | **503 aprobadas**, 21 avisos previos, 704.45 s |
| `pip check` | Sin dependencias rotas |
| Construcción Docker | Correcta; imagen `odonto-recepcion-langchain:local` |
| Arranque del contenedor sin red, `/health` y `/status` autenticado | Correctos con credenciales sintéticas; contenedor temporal retirado |
| Conversión local de las 15 herramientas para Gemini | Correcta; no llama a Google |
| Revisión independiente de código y probes adicionales | Hallazgos corregidos; sin nuevos bloqueos concretos en las rutas revisadas |

El escenario integrado registra un perfil, consulta horarios, propone y confirma
una reserva, reprograma esa misma cita, la cancela, entrega las respuestas al
proveedor simulado, deriva y reanuda. Comprueba directamente en PostgreSQL el
estado, hora y unicidad de la cita y que cada mensaje se entregue una sola vez.

## Fallos corregidos y pruebas que los cubren

| Caso | Solución | Evidencia |
|---|---|---|
| Confirmar reserva/reprogramación en el mismo turno | Política de consentimiento previa al acceso a la API | `audit/acceptance_probes.py`, `tests/test_workflows.py` |
| Confirmación condicional, expirada, anterior a entrega o de otra acción | Validación de texto, operación, token, mensaje, vigencia y presentación | `tests/test_workflows.py` |
| Propuesta antigua que reaparece tras completar otra | Solo se confirma la última propuesta presentada | `tests/test_workflows.py` |
| Entrega atrasada cambia cuál propuesta vio el paciente al final | Presentación ordenada por envío real, no por creación | `tests/test_recovery.py` |
| “Sí” antiguo recibido después de una negativa posterior | Orden cronológico del lote y comprobación del último inbound | `tests/test_recovery.py`, `tests/test_workflows.py` |
| `/status` falla por acceso a SQLite desde otro hilo | Ruta asíncrona | Probe original y prueba de arranque |
| `ConnectError` antes de enviar termina en fallo definitivo | Fallo transitorio con otro intento y clave de resultado por intento | Probe original y `tests/test_recovery.py` |
| Mensaje ordinario pendiente se envía durante pausa humana | Comprobación de pausa; excepción limitada al aviso de derivación | Probe original |
| Etiquetas aplicadas, asignación humana fallida y abandonada | Job persistente de asignación independiente de las etiquetas | Probe original |
| Handoff hace commit pero se pierde la respuesta | Intención persistente y replay de la misma mutación antes del contexto | `tests/test_recovery.py` |
| Acuse de entrega hace commit pero se pierde la respuesta | `delivery_results` se recupera sin depender de otro claim ni reenviar | `tests/test_recovery.py` |
| 50 salidas terminales remotas bloquean nuevas respuestas | Selección rotativa de IDs propios | `tests/test_recovery.py` |
| Un fragmento nuevo oculta una operación anterior inconclusa | Recuperar intención/borrador antes de cerrar fragmentos previos | `tests/test_recovery.py` |
| Reanudación coincide con recuperación del handoff | Lock compartido y revalidación del job | `tests/test_handoff_concurrency.py` |
| Dispatcher consume mensajes de otro adaptador | Filtro `outbound_ids` en cliente y backend, limitado por tenant | `tests/test_outbound_claim_selection.py` del backend |
| Falta de checkpoint operativo | `reception_state` persistido con cada salida | Recorrido real con PostgreSQL y `tests/test_workflows.py` |
| Circuito de audio sin prueba positiva | Descarga permitida, transcripción y caché verificadas con proveedor simulado | `tests/test_recovery.py` |

Los seis probes originales se incluyen ahora en la ejecución normal de pytest.
Se ajustaron sus respuestas sintéticas para incluir `expires_at`, como exige el
contrato real, y para registrar los IDs propios del nuevo dispatcher. Se
conservaron las aserciones de comportamiento; no hay xfail que oculte fallos.
La auditoría previa, con sus fallos originales, se conserva en
`audit/INFORME_INICIAL.md` como registro histórico.

## Qué significa y qué no significa esta validación

- Se probó el bucle real de LangChain. Sus respuestas de prueba siguen un guion:
  no demuestran la comprensión libre de Gemini en conversaciones reales.
- API y PostgreSQL se probaron con datos sintéticos en
  `127.0.0.1:5434/odontoflow_test`. No se modificaron fichas de pacientes reales.
- Audio: se verificó el circuito de descarga/transcripción/caché con bytes y
  modelo de prueba. Falta comprobar formatos y precisión en audios reales.
- Las reglas de confirmación están en código. El contenido informativo y la
  clasificación de casos no cubiertos por los patrones siguen dependiendo del
  modelo; requieren evaluación con consultas reales de la clínica.
- `sent` significa aceptación por Chatwoot. No acredita entrega/lectura en
  WhatsApp. No hay recordatorios, cobros, facturación ni Google Calendar.
- Un envío incierto se conserva para revisión manual; no se afirma una garantía
  distribuida de “exactamente una vez”. Se evita repetir a ciegas un envío incierto.
- Un solo proceso/worker. Retención automatizada de SQLite, réplicas, monitoreo
  externo y validación bajo carga quedan fuera del piloto local.

## Configuración que falta en esta entrega

El servicio no tiene `.env` con credenciales. `python -m recepcion.doctor` detecta
que faltan `ODONTO_INBOUND_TOKEN`, `ODONTO_AGENT_TOKEN`, `RECEPTION_API_KEY` y
`GOOGLE_API_KEY`. Para la bandeja real también hacen falta las variables Chatwoot,
el canal y los tokens delivery/operator. No se copiaron ni rotaron claves de
otros entornos.

El backend con el filtro nuevo debe desplegarse antes de usar el dispatcher.
El backend existente en el puerto 8000 no fue sustituido. La imagen nueva se
construyó y arrancó en una prueba temporal sin red; no quedó atendiendo pacientes.
No se cambió ningún webhook ni se enviaron mensajes al exterior.

## Reproducir

Desde `recepcion-langchain`, ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe integration/run.py
```

El tercer comando requiere las dependencias del backend y la base de pruebas
local; su runner rechaza otra dirección. No ejecutarlo a la vez que pytest del
backend porque ambos usan y reinician el mismo esquema de prueba.

Desde `OdontoFlow-Backend-github-clean`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

La suite normal del servicio no usa PostgreSQL. Las advertencias observadas
provienen del SDK Google, de Starlette y de la configuración heredada de Alembic;
no fueron errores de pruebas.
