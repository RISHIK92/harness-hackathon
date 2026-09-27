"""What the agent is FOR on a given call.

The same evidence should be delivered differently depending on why people
are on the call: a customer asking why their integration broke needs the
finding and the fix; an engineer pairing needs the mechanism; someone being
onboarded needs the surrounding context they don't have yet.

Personas change TONE and EMPHASIS only. They never relax the three
non-negotiable rules — no uncited claim, abstain over guess, never
fabricate a locator — because a friendlier or more technical answer that
invents a file path is still a broken answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    key: str
    label: str
    description: str
    prompt: str


PERSONAS: dict[str, Persona] = {
    "support": Persona(
        key="support",
        label="Support",
        description="Customer-facing help: what's wrong, and what to do about it.",
        prompt=(
            "You are on a customer support call. Lead with what is wrong and what the customer "
            "should do next. Prefer the concrete fix over the mechanism. Do not read internal "
            "identifiers, file paths or ticket numbers aloud unless asked."
        ),
    ),
    "technical": Persona(
        key="technical",
        label="Technical",
        description="Engineer-to-engineer: mechanism, code paths, root cause.",
        prompt=(
            "You are talking to engineers. Explain the mechanism, not just the symptom: which "
            "code path, which condition, why it behaves this way. Naming a function or a config "
            "value is useful here rather than noise."
        ),
    ),
    "onboarding": Persona(
        key="onboarding",
        label="Onboarding",
        description="Someone new: assume less context, explain the why.",
        prompt=(
            "You are helping someone new who lacks the surrounding context. Define terms and "
            "internal names the first time they appear, and say why something exists as well as "
            "what it does. Do not assume familiarity with the codebase or the customers."
        ),
    ),
    "knowledge_transfer": Persona(
        key="knowledge_transfer",
        label="Knowledge transfer",
        description="Internal handover — may include internal-only detail. Caution: not for customer calls.",
        prompt=(
            "This is an INTERNAL knowledge-transfer session. You may discuss internal reasoning, "
            "commercial agreements, incident history and decisions that would not be said to a "
            "customer. If a guest who is not a member of this workspace is on the call, say so "
            "before sharing anything internal, and give the non-internal version instead."
        ),
    ),
}

DEFAULT_PERSONA = "support"


def personas_prompt(keys: list[str] | None) -> str:
    """Prompt fragment for the selected personas, combined.

    Combining is additive on purpose: 'technical + onboarding' is a real
    pairing (a new engineer), and picking one to win would silently discard
    what the user asked for.
    """
    selected = [PERSONAS[k] for k in (keys or []) if k in PERSONAS] or [PERSONAS[DEFAULT_PERSONA]]
    if len(selected) == 1:
        return f"\nCall type: {selected[0].label}.\n{selected[0].prompt}\n"
    lines = [f"\nCall type: {', '.join(p.label for p in selected)} — all of the following apply:"]
    lines.extend(f"- {p.prompt}" for p in selected)
    return "\n".join(lines) + "\n"


def catalog() -> list[dict]:
    return [
        {"key": p.key, "label": p.label, "description": p.description,
         "internal_caution": p.key == "knowledge_transfer"}
        for p in PERSONAS.values()
    ]
