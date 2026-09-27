"""Who in this meeting is the client, and who is us.

The whole suggestion gate hangs off this. Get it wrong one way and Photon
answers your own colleague unprompted; get it wrong the other way and it
never fires at all.

Two paths, and they resolve it in opposite directions FOR GOOD REASON:

  Photon's own call   `speaker_user_id` comes from a LiveKit token this API
                      signed, so "is a member" is authoritative. Anyone
                      without it is a guest, i.e. the client. Nothing here
                      is needed.

  Any other platform  We have a display name off a transcript and nothing
                      else. Defaulting everyone to "not the client" — the
                      safe-looking choice — means ZERO suggestions ever
                      fire on a Meet call, which is the whole feature. So
                      the default inverts: a speaker is the client UNLESS
                      they match a workspace member.

That inversion is only safe because of where a suggestion lands. The cost of
a false positive is one unnecessary suggestion in a private thread that
nobody but its owner can see; the cost of a false negative is whisper doing
nothing on the calls it was built for. It would NOT be safe if suggestions
were spoken aloud, which is exactly why speak mode's gate is the strict one.
"""
from __future__ import annotations

import re

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import User, WorkspaceMember

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _normalise(value: str) -> str:
    return _NON_WORD.sub("", (value or "").lower())


def _candidates(email: str) -> set[str]:
    """Forms of an email a transcript might show a person under.

    A meeting platform reports a display name, not an address, so
    "priya.nair@acme.com" has to match "Priya Nair" — hence the local part
    with its punctuation stripped.
    """
    local = email.split("@", 1)[0]
    return {_normalise(email), _normalise(local)} - {""}


async def member_identities(session: AsyncSession, workspace_id: str) -> set[str]:
    rows = (await session.execute(
        select(User.email)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )).all()
    identities: set[str] = set()
    for (email,) in rows:
        identities |= _candidates(email or "")
    return identities


def looks_like_member(speaker_name: str, identities: set[str]) -> bool:
    name = _normalise(speaker_name)
    if not name:
        return False
    # Exact match on the whole normalised name, or on a full email. Not a
    # substring test in either direction: "sam" would otherwise match
    # "samantha@…", and quietly silence a real client's questions.
    return name in identities


async def resolve_is_client(session: AsyncSession, workspace_id: str, speaker_name: str,
                            speaker_email: str | None = None) -> bool:
    """True when this speaker is NOT a known workspace member.

    An email, when the platform reports one (Recall does for signed-in
    participants), is the stronger signal and is checked first — two people
    share a display name far more often than an address."""
    identities = await member_identities(session, workspace_id)
    if speaker_email and looks_like_member(speaker_email, identities):
        return False
    return not looks_like_member(speaker_name, identities)
