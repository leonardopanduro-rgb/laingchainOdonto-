# Validación — 13 de octubre de 2026

## Reauditoría de cumplimiento

**Veredicto: cumplimiento parcial; no listo para operar con pacientes.**
El trabajo pendiente incluye defectos de implementación, no solo credenciales.
Se reejecutó la suite original: **18 passed**. Se añadieron seis comprobaciones
de aceptación independientes: **6 failed**, correspondientes a cinco fallos.
No se modificó la lógica del servicio durante esta auditoría.

Reproducción sin servicios externos:

```powershell
.\.venv\Scripts\python.exe -m pytest audit/acceptance_probes.py -q --tb=short
```

Este archivo se ejecuta explícitamente: las aserciones describen el comportamiento
requerido y actualmente fallan. No se marcaron como xfail ni se modificaron las
pruebas previas para ocultar resultados.

| Prioridad | Hallazgo reproducido | Evidencia y consecuencia |
|---|---|---|
| P1 | Reserva y reprogramación se pueden confirmar en el mismo turno. | Un modelo de prueba llama a propose y después a confirm con una única entrada; LangChain envía ambas al backend. Las funciones actuales confirm_contact_booking_proposal y confirm_reschedule_proposal tampoco exigen un segundo mensaje entrante. La espera está escrita en el prompt, no impuesta por el código. Son dos pruebas fallidas. |
| P1 | Una respuesta automática pendiente se envía después de pausar para atención humana. | dispatch no verifica etiquetas o estado de handoff antes del POST a Chatwoot; la prueba reproduce el envío a una conversación pausada. Hay que distinguir el aviso de derivación de respuestas antiguas que deben suprimirse. |
| P1 | Una derivación parcial se da por terminada sin asignar al equipo. | Si el POST de etiquetas funciona y assignments devuelve 503, el siguiente process ve apagado, marca la entrada done y nunca reintenta la asignación. |
| P2 | Un fallo de conexión previo al envío se vuelve definitivo. | httpx.ConnectError termina como permanent_failure/delivery_uncertain; el backend lo pasa a dead_letter. Debe distinguirse un fallo seguro de reintentar de una entrega de resultado incierto. |
| P2 | GET /status autenticado falla. | FastAPI ejecuta la ruta síncrona en un hilo distinto; Store usa sqlite3 con check_same_thread activo. Se reproduce sqlite3.ProgrammingError. |

### Comparación con las capacidades descritas

- **Información, precios, sedes, horarios y promociones:** el contexto del
  backend contiene esos datos y las herramientas están conectadas. Falta validar
  respuestas reales con Gemini y el catálogo. No hay pruebas de conversación
  para cada uno de esos casos en la suite nueva.
- **Disponibilidad y perfil:** contratos y llamadas existen. No hay pruebas
  integradas del nuevo servicio con PostgreSQL que demuestren los recorridos.
- **Reserva/reprogramación con confirmación posterior:** no satisface la
  garantía descrita, por el primer hallazgo.
- **Cancelación:** el backend sí comprueba un mensaje posterior; interpretar
  si ese mensaje afirma o rechaza la cancelación sigue delegado al modelo.
- **Memoria:** recupera los últimos ocho mensajes visibles y la propuesta
  pendiente. El nuevo servicio no escribe reception_state en el outbound;
  no conserva un checkpoint nuevo con servicio/sede/slot en conversaciones largas.
- **Audio:** existe descarga y transcripción mediante LangChain. La prueba
  previa solo cubre rechazo de un origen no permitido; no acredita una
  transcripción exitosa ni comprensión de notas de voz reales.
- **Derivación/pausa:** herramientas existentes, con los dos fallos P1 de
  integración descritos arriba. La detección de solicitudes clínicas o de
  atención humana se encomienda al prompt y no cuenta con evaluaciones reales.
- **Límites clínicos y comerciales:** no se exponen herramientas para cobrar
  ni cambiar precios. Evitar diagnósticos o prescripciones en texto depende
  del prompt; no se comprobó con una evaluación de respuestas del modelo.

También se verificó que el adaptador real ChatGoogleGenerativeAI puede preparar
los esquemas de las 15 herramientas sin error local. Esto no llama a Google ni
demuestra disponibilidad del modelo o validez de credenciales.

La restricción ya documentada del dispatcher por organización continúa:
reclama toda la cola y marca como route_missing las rutas desconocidas. No es
apto para una organización con otros canales/consumidores sin ajustar ese contrato.

## Registro de la entrega inicial

Estado: implementación local de recepción y adaptador WhatsApp/Chatwoot.
Sin despliegue ni cambio de webhooks. Los workflows anteriores se conservan.

Pruebas: `python -m pytest -q` — 18 passed. Un aviso de deprecación proviene
del SDK google-genai en Python 3.14. `pip check`: sin dependencias rotas.

Verificado con transportes de prueba: bucle real LangChain y llamadas de tools,
IDs de contacto/conversación impuestos por servidor, trace headers coherentes,
UUIDv4 de reintento persistente, recuperación del borrador saliente, duplicados,
bloqueo por handoff/denegación del backend, autenticación HTTP/webhook,
filtros de Chatwoot, debounce tras reiniciar y no repetición de entrega incierta.

No verificado: Gemini real, transcripción real, PostgreSQL real, Chatwoot real,
entrega de WhatsApp, nota de reanudación real, construcción del contenedor,
comportamiento bajo carga y concurrencia distribuida. Requiere un solo worker.
El dispatcher usa la cola completa de la organización: el piloto debe tener
una organización dedicada al inbox conectado.

Faltan credenciales Gemini/Chatwoot/dispatcher en el servicio y confirmar el
canal del backend. Los tokens locales existentes del laboratorio no se rotaron.
No se ejecutaron mensajes reales ni se activó el servicio contra pacientes.
