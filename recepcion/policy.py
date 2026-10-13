"""Server-enforced reception rules, independent of the model's instructions."""
import re
import unicodedata
from datetime import datetime, timezone, timedelta

ACTION_BY_TOOL = {
    "propose_appointment": "BOOK", "confirm_appointment": "BOOK",
    "propose_cancellation": "CANCEL", "confirm_cancellation": "CANCEL",
    "propose_reschedule": "RESCHEDULE", "confirm_reschedule": "RESCHEDULE",
}
CONFIRM_TOOL = {"BOOK": "confirm_appointment", "CANCEL": "confirm_cancellation", "RESCHEDULE": "confirm_reschedule"}
LIMA = timezone(timedelta(hours=-5))


def normalized(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


def explicit_confirmation(text, action=None):
    # Deliberately exact: "sí, pero cambia la hora" must never confirm.
    generic = {
        "si", "si confirmo", "confirmo", "confirmar", "si por favor",
        "si confirmar",
    }
    specific = {"BOOK": {"confirmo la cita", "confirmo la reserva"},
                "CANCEL": {"confirmo la cancelacion"}, "RESCHEDULE": {"confirmo la reprogramacion"}}
    allowed = specific.get(action, set()) if action else set().union(*specific.values())
    return normalized(text) in generic | allowed


def handoff_reason(text):
    value = normalized(text)
    if re.search(r"\b(persona|humano|humana|recepcionista|asesor|asesora)\b", value):
        return "requested_by_contact"
    if re.search(r"\b(queja|reclamo|reclamar)\b", value):
        return "complaint"
    if re.search(r"\b(urgencia|sangrado|sangrando|no puedo respirar)\b", value):
        return "urgent_symptoms"
    if re.search(r"\b(dolor|duele|hinchad\w*|fiebre|diagnostico|medicamento|antibiotico|recet\w*)\b", value):
        return "clinical_case"
    return None


def confirmation_allowed(store, turn, name, arguments):
    pending = turn.context.get("pending_action") or {}
    action = ACTION_BY_TOOL[name]
    latest = turn.context.get("conversation", {}).get("latest_inbound_message_id")
    if latest is not None and latest != turn.message_id:
        return False
    for message in turn.context.get("recent_messages", []):
        if (message.get("direction") == "inbound" and message.get("occurred_at") and turn.occurred_at and
            datetime.fromisoformat(message["occurred_at"]) > turn.occurred_at):
            return False
    if pending.get("action_type") != action or pending.get("proposal_id") != arguments["proposal_id"]:
        return False
    if str(pending.get("confirmation_token")) != str(arguments["confirmation_token"]):
        return False
    if not explicit_confirmation(turn.text, action) or turn.proposal is not None:
        return False
    presented = store.proposal(turn.conversation_id, action, arguments["proposal_id"])
    if store.latest_presented_proposal(turn.conversation_id) != (action, arguments["proposal_id"]):
        return False
    if not presented or presented["presented_at"] is None or turn.message_id <= presented["source_message_id"]:
        return False
    now = datetime.now(timezone.utc)
    if datetime.fromisoformat(presented["expires_at"]) <= now:
        return False
    # Provider timestamps precede webhook handling: reject a pre-delivery "sí".
    return turn.occurred_at is not None and turn.occurred_at > datetime.fromisoformat(presented["presented_at"])


def local_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(LIMA).strftime("%d/%m/%Y a las %H:%M")


def proposal_text(action, proposal, context):
    services = {s["id"]: s["name"] for s in context.get("services", [])}
    locations = {s["id"]: s["name"] for s in context.get("locations", [])}
    if action == "BOOK":
        description = f"{services.get(proposal.get('service_id'), 'Cita dental')} en {locations.get(proposal.get('location_id'), 'la sede seleccionada')}, {local_time(proposal['start'])} (hora de Lima)"
        return f"Propuesta de reserva: {description}. Aún no está reservada. Responde «Sí, confirmo» para confirmarla."
    appointment = next((a for a in context.get("appointments", []) if a["id"] == proposal.get("appointment_id")), None)
    original = (" del " + local_time(appointment["start"])) if appointment else " seleccionada"
    if action == "CANCEL":
        return f"Propuesta: cancelar tu cita{original}. Aún no está cancelada. Responde «Sí, confirmo» para cancelarla."
    return f"Propuesta: mover tu cita{original} al {local_time(proposal['new_start'])} (hora de Lima). Aún no está cambiada. Responde «Sí, confirmo» para reprogramarla."


def confirmed_text(action, data):
    appointment = data.get("appointment", {})
    when = (" para el " + local_time(appointment["start"]) + " (hora de Lima)") if appointment.get("start") else ""
    return {"BOOK": f"Tu cita quedó reservada{when}.", "CANCEL": "Tu cita quedó cancelada.",
            "RESCHEDULE": f"Tu cita quedó reprogramada{when}."}[action]
